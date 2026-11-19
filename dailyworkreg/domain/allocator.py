"""Joint date-level allocation for area, DTC and feeder field visits.

This module places a month's obligations onto eligible dates. It decides *when*
each visit happens; it never decides *what* is owed, and it never decides whether
a visit is legal. Obligations come from the validator's own resolution, and every
candidate schedule is accepted or rejected by the validator. There is no second
implementation of daily capacities, monthly maxima, working-date eligibility,
asset activity or revisit gaps anywhere in this module.

The three-outcome contract is preserved:

*   ``FEASIBLE`` - a complete concrete schedule was built and its full witness,
    committed visits included, passes ``validate_schedule(OBLIGATIONS)``.
*   ``INFEASIBLE`` - established by proof: the validator's own assessment
    returned ``INFEASIBLE``, or the aggregate arithmetic is contradicted.
*   ``INDETERMINATE`` - the constructive search ran out of budget without a
    witness and without a proof. A heuristic that merely failed to find a place
    is never reported as impossible.

The construction is constrained-first: assets with the fewest viable dates are
placed earliest, so a flexible visit cannot consume the only date a constrained
asset could use. Ordering is fully determined by the inputs and the seed.
"""

from __future__ import annotations

import random
from bisect import bisect_left, insort
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable, Iterable, Mapping, Sequence

from .prorating import resolve_period_config
from .validator import (
    C_AGG_COMBINED_CAPACITY,
    KIND_AGGREGATE_CONSTRAINT,
    KIND_LABEL,
    C_ASSET_MONTHLY_MAX,
    Asset,
    AssetLimits,
    AssetType,
    Calendar,
    DatePeriod,
    Evidence,
    FeasibilityConfig,
    FeasibilityStatus,
    ObligationContext,
    ObligationSpec,
    ProposalContext,
    ValidationMode,
    Visit,
    assess_feasibility,
    calendar_day_gap,
    resolve_obligations,
    validate_schedule,
)

#: Emitted when no schedule was found within the attempt budget and no proof of
#: infeasibility exists. Deliberately distinct from an infeasibility proof.
C_ALLOCATION_BUDGET_EXHAUSTED = "allocation_search_budget_exhausted"

#: Bumped whenever the placement strategy changes, so a stored plan can state
#: which algorithm produced it.
ALLOCATOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class AllocationResult:
    """The outcome of a joint allocation attempt.

    ``visits`` is the complete month - committed visits and newly placed visits
    together - so it can be handed straight to the validator or rendered. The
    per-asset and per-date views are derived from it.

    A ``FEASIBLE`` result is only ever returned after its own ``visits`` have
    passed obligation-aware validation, so a caller may rely on that.
    """

    status: FeasibilityStatus
    period: DatePeriod
    visits: tuple[Visit, ...] = ()
    #: Visits supplied as immutable commitments, echoed back for the snapshot.
    fixed: tuple[Visit, ...] = ()
    #: The obligations this month was built against.
    obligations: ObligationSpec | None = None
    evidence: tuple[Evidence, ...] = ()
    #: Seed and configuration, for reproducibility.
    seed: int = 0
    allocator_version: str = ALLOCATOR_VERSION
    #: True when this month's targets came from a coverage cycle.
    from_cycle: bool = False
    #: Free-form notes a caller may display; never the source of truth.
    notes: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return self.status is FeasibilityStatus.FEASIBLE

    def per_asset(self) -> Mapping[str, int]:
        """Visit counts per asset id, for the monthly category totals."""

        counts: dict[str, int] = {}
        for visit in self.visits:
            counts[visit.asset_id] = counts.get(visit.asset_id, 0) + 1
        return counts

    def per_date(self) -> Mapping[date, int]:
        counts: dict[date, int] = {}
        for visit in self.visits:
            counts[visit.visit_date] = counts.get(visit.visit_date, 0) + 1
        return counts

    def category_totals(self, roster: Sequence[Asset]) -> Mapping[AssetType, int]:
        """Visits per category. Categories share the combined cap but not limits."""

        kinds = {asset.asset_id: asset.asset_type for asset in roster}
        totals: dict[AssetType, int] = {kind: 0 for kind in AssetType}
        for visit in self.visits:
            kind = kinds.get(visit.asset_id)
            if kind is not None:
                totals[kind] = totals.get(kind, 0) + 1
        return totals


@dataclass(frozen=True)
class AllocationRequest:
    """Everything the allocator needs for one calendar month.

    ``required_visits`` and ``cycle_obligations`` are the resolved per-month
    obligations. They are obligations, never merely targets: a monthly maximum
    is not accepted here, because a maximum is an upper bound and putting one in
    this field would create coverage nobody approved.
    """

    period: DatePeriod
    calendar: Calendar
    roster: Sequence[Asset]
    config: FeasibilityConfig
    required_visits: Mapping[str, int] = field(default_factory=dict)
    cycle_obligations: Mapping[str, int] = field(default_factory=dict)
    history: Sequence[Visit] = ()
    fixed: Sequence[Visit] = ()
    #: Deterministic seed. The same seed and inputs always give the same plan.
    seed: int = 0
    #: Bound on constructive attempts before INDETERMINATE is reported.
    attempt_budget: int = 400
    #: Whether the targets came from a coverage cycle, for labelling only.
    from_cycle: bool = False
    #: Scheduling weights by asset id, used only to break placement ties.
    #: Passed through so the weight model is computed once by ``DtcWeights``
    #: rather than re-derived here. Weight never outranks constraint pressure and
    #: never changes an obligation count.
    weights: Mapping[str, float] = field(default_factory=dict)
    #: Committed visits outside this period that must constrain the revisit gap
    #: in both directions. Passed through to the validator unchanged, so a
    #: commitment later in a cycle blocks a date here before it is placed rather
    #: than being discovered after this month is already accepted.
    gap_blockers: tuple[Visit, ...] = ()
    #: Effective prorated limits, required for a partial month. A partial period
    #: must not silently use full-month limits, so the caller supplies these and
    #: ``allocate_month`` refuses the request without them.
    effective_limits: Mapping[str, "AssetLimits"] | None = None
    #: Told the attempt number and the budget before each constructive attempt, so a
    #: caller can show that a hard month is still working rather than stuck. It is a
    #: report only: nothing is read from it, and a caller that raises from it breaks its
    #: own run rather than the schedule.
    on_attempt: Callable[[int, int], None] | None = None

    def __post_init__(self) -> None:
        if self.attempt_budget < 1:
            raise ValueError("attempt_budget must be at least 1")


def _eligible_dates(period: DatePeriod, calendar: Calendar) -> tuple[date, ...]:
    return tuple(sorted(d for d in calendar.working_days if period.contains(d)))


def _violates_gap(
    asset_id: str, day: date, placed: Sequence[Visit], gap: int
) -> bool:
    """True when ``day`` is too close to a visit already recorded for the asset.

    The reference reading of the rule, kept as the definition rather than as the fast
    path: a visit dated after ``day`` produces a negative elapsed gap, which is below
    every positive interval, so any later visit of the same asset already blocks the
    date. :func:`_gap_violated` answers the same question from a sorted index and is
    what construction uses.
    """

    for visit in placed:
        if visit.asset_id != asset_id:
            continue
        if calendar_day_gap(visit.visit_date, day) < gap:
            return True
    return False


def _gap_violated(dates: Sequence[date], day: date, gap: int) -> bool:
    """The same rule as :func:`_violates_gap`, from dates already sorted.

    Construction asks this question once per (asset, candidate date) pair, and on a
    ten-month cycle that is tens of millions of asks: scanning every placement each
    time made generation quadratic in the number of visits and dominated the whole
    run. Because a visit after ``day`` blocks the date outright and the remaining
    visits are earlier, only two dates can decide it - the first on or after ``day``,
    and the last before it - so a binary search answers it exactly.

    ``revisit_gap_days`` is at least one (``AssetLimits`` refuses anything lower), and
    the general reading is kept as the fallback so a caller outside that contract is
    still answered correctly rather than optimistically.
    """

    if not dates:
        return False
    if gap < 1:
        return any(calendar_day_gap(visit_date, day) < gap for visit_date in dates)
    position = bisect_left(dates, day)
    if position < len(dates):
        return True
    return calendar_day_gap(dates[-1], day) < gap


def _drop_date(days: list[date], day: date) -> None:
    """Remove ``day`` from an ascending list of dates, if it is there.

    Used to keep a cached set of viable dates exact when a date fills up. A list of
    at most a month's working days is cheaper to filter in place than to rebuild, and
    removing rather than rebuilding cannot add a date back that a constraint closed.
    """

    position = bisect_left(days, day)
    if position < len(days) and days[position] == day:
        del days[position]


def _weight_of(request: AllocationRequest, asset_id: str) -> float:
    """Scheduling weight for ``asset_id``, or 0.0 when none was supplied.

    A missing weight sorts last among equally-constrained assets, so an omission
    never promotes an asset ahead of one with a real weight.
    """

    return float(request.weights.get(asset_id, 0.0))


def _attempt_placement(
    request: AllocationRequest,
    required: Mapping[str, int],
    eligible: Sequence[date],
    rng: random.Random,
) -> tuple[Visit, ...] | None:
    """One constructive attempt. Returns the placed visits, or None on failure.

    Constrained-first: at every step the asset with the fewest still-viable dates
    is placed next. That is what stops a flexible asset from taking the last date
    a tightly constrained one could have used, which is the failure mode a plain
    first-fit suffers.

    Constrained-first ranking is: fewest viable dates, then greatest remaining
    count, then scheduling weight (heavier first), then a seed-derived tie key.
    The asset id is used only to keep the ordering total, and the seeded key sits
    ahead of it so a different seed can genuinely reach a different arrangement.
    Weight never outranks constraint pressure: it is the third key, not the first.

    Date choice prefers the least-loaded date, then the fewest remaining feasible
    dates after the placement, then a seed-derived tie key. The last two are what
    make alternative seeds produce different valid schedules instead of repeating
    one greedy path.

    Each asset's viable dates are computed once and then maintained rather than
    recomputed: a placement can only ever *remove* a date from an asset's options -
    its own visit closes the dates around it, and a date that fills up closes for
    everybody - so the sets are filtered as they change. Recomputing all of them at
    every step made generation quadratic in the number of visits.
    """

    roster = {asset.asset_id: asset for asset in request.roster}

    # A per-asset seed-derived tie key. This is what a different seed varies: it
    # only ever breaks a tie that constraint pressure and weight left open, so it
    # can never override them. Built from the seeded RNG rather than hash(), whose
    # value is process-randomised and would make runs irreproducible.
    seed_key: dict[str, int] = {}
    for asset_id in sorted(required):
        seed_key[asset_id] = rng.randrange(1 << 30)
    # Dates get the same treatment for their own ties.
    date_key: dict[date, int] = {}
    for day in eligible:
        date_key[day] = rng.randrange(1 << 30)

    remaining: dict[str, int] = {
        asset_id: required[asset_id] for asset_id in sorted(required)
    }

    placed: list[Visit] = list(request.fixed)
    # Every visit that constrains a gap: prior history, commitments, placements.
    gap_source: list[Visit] = (
        list(request.history) + list(request.fixed) + list(request.gap_blockers)
    )
    # The same visits, by asset, each list sorted, so the gap question is a binary
    # search rather than a scan of everything placed so far.
    gap_dates: dict[str, list[date]] = {}
    for visit in gap_source:
        insort(gap_dates.setdefault(visit.asset_id, []), visit.visit_date)

    per_date: dict[date, int] = {}
    per_date_kind: dict[AssetType, dict[date, int]] = {
        kind: {} for kind in AssetType
    }
    per_asset: dict[str, int] = {}
    for visit in request.fixed:
        per_date[visit.visit_date] = per_date.get(visit.visit_date, 0) + 1
        per_asset[visit.asset_id] = per_asset.get(visit.asset_id, 0) + 1
        asset = roster.get(visit.asset_id)
        if asset is not None:
            kind_dates = per_date_kind[asset.asset_type]
            kind_dates[visit.visit_date] = kind_dates.get(visit.visit_date, 0) + 1

    config = request.config
    kind_caps = config.kind_caps()
    combined_cap = config.combined_daily_cap
    kind_of: dict[str, AssetType] = {
        asset.asset_id: asset.asset_type for asset in request.roster
    }

    # Viable dates by asset, computed on first use and then kept exact. Only the
    # three things that can change membership are acted on below: the placed asset's
    # own gap set, a date that has just filled up for its kind, and an asset reaching
    # its monthly capacity.
    viable_cache: dict[str, list[date]] = {}

    while any(count > 0 for count in remaining.values()):
        best_asset: str | None = None
        best_dates: list[date] = []
        best_rank: tuple[int, int, float, int, str] | None = None

        for asset_id, count in remaining.items():
            if count <= 0:
                continue
            limits = config.limits_for(asset_id)
            if per_asset.get(asset_id, 0) >= limits.monthly_max:
                # No capacity left this month for this asset.
                continue
            viable = viable_cache.get(asset_id)
            if viable is None:
                gap = limits.revisit_gap_days
                blocked = gap_dates.get(asset_id, ())
                kind = kind_of[asset_id]
                kind_cap = kind_caps[kind]
                kind_dates = per_date_kind[kind]
                viable = [
                    day
                    for day in eligible
                    if kind_dates.get(day, 0) < kind_cap
                    and (combined_cap is None or per_date.get(day, 0) < combined_cap)
                    and not _gap_violated(blocked, day, gap)
                ]
                viable_cache[asset_id] = viable
            if not viable:
                continue
            rank = (
                len(viable),
                -count,
                -_weight_of(request, asset_id),
                seed_key[asset_id],
                asset_id,
            )
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_asset = asset_id
                best_dates = viable

        if best_asset is None:
            # Some obligation can no longer be placed in this construction.
            return None

        # Spread the visit rather than clustering it. Least-loaded first, then the
        # date whose choice leaves the most room for later placements, then a
        # seeded key, then the calendar date so the order is total.
        best_dates.sort(
            key=lambda day: (
                per_date.get(day, 0),
                date_key[day],
                day,
            )
        )
        chosen = best_dates[0]

        visit = Visit(best_asset, chosen)
        kind = kind_of[best_asset]
        placed.append(visit)
        gap_source.append(visit)
        insort(gap_dates.setdefault(best_asset, []), chosen)
        per_date[chosen] = per_date.get(chosen, 0) + 1
        per_asset[best_asset] = per_asset.get(best_asset, 0) + 1
        kind_dates = per_date_kind[kind]
        kind_dates[chosen] = kind_dates.get(chosen, 0) + 1
        remaining[best_asset] -= 1

        # Keep the cached option sets exact. The placed asset's own options are
        # rebuilt on its next use, because its visit just closed the dates around it.
        viable_cache.pop(best_asset, None)
        if combined_cap is not None and per_date[chosen] >= combined_cap:
            for days in viable_cache.values():
                _drop_date(days, chosen)
        elif kind_dates[chosen] >= kind_caps[kind]:
            # Only assets of that kind lose the date; the ceiling belongs to them
            # alone, and another kind may still have room on the same day.
            for asset_id, days in viable_cache.items():
                if kind_of[asset_id] is kind:
                    _drop_date(days, chosen)

    return tuple(sorted(placed, key=lambda v: (v.visit_date, v.asset_id)))


def allocate_month(request: AllocationRequest) -> AllocationResult:
    """Build a complete, validated schedule for one calendar month.

    Args:
        request: Period, calendar, roster, configuration, resolved obligations,
            history, commitments and seed.

    Returns:
        An :class:`AllocationResult`. ``FEASIBLE`` carries a witness that has
        already passed obligation-aware validation; ``INFEASIBLE`` carries
        proof; ``INDETERMINATE`` means the attempt budget ran out first.

    Raises:
        InputError: If the obligation inputs are malformed. A caller error is
            never reported as a feasibility outcome.
    """

    # A partial month must never fall back to full-month limits. Resolve the
    # effective configuration first so this holds for every caller, including one
    # that reaches the allocator directly rather than through a convenience API.
    config = resolve_period_config(
        request.period,
        request.config,
        request.calendar.working_days,
        request.effective_limits,
    )
    # Everything downstream uses the effective limits from here on.
    request = replace(request, config=config)

    # Fail fast on malformed obligations, so a caller error and a scheduling
    # verdict can never be confused for one another.
    obligations = resolve_obligations(
        request.roster,
        request.config,
        request.required_visits,
        request.cycle_obligations,
    )
    # Remaining work is what the commitments do not already discharge. Committed
    # visits count toward the obligation and consume their capacity, so placing
    # the full obligation again would double-count them.
    committed_by_asset: dict[str, int] = {}
    for visit in request.fixed:
        if request.period.contains(visit.visit_date):
            committed_by_asset[visit.asset_id] = (
                committed_by_asset.get(visit.asset_id, 0) + 1
            )
    required = {
        asset_id: count - committed_by_asset.get(asset_id, 0)
        for asset_id, count in obligations.per_asset.items()
        if count - committed_by_asset.get(asset_id, 0) > 0
    }
    eligible = _eligible_dates(request.period, request.calendar)

    # 1. An invalid commitment is a proof of infeasibility, not something to
    #    build around. Asking the validator first keeps this module from
    #    duplicating what makes a commitment invalid.
    probe = validate_schedule(
        ProposalContext(
            period=request.period,
            calendar=request.calendar,
            roster=request.roster,
            config=request.config,
            proposed=(),
            history=request.history,
            gap_blockers=request.gap_blockers,
            fixed=request.fixed,
            required_visits=request.required_visits,
            cycle_obligations=request.cycle_obligations,
        )
    )
    commitment_faults = tuple(
        violation
        for violation in probe.violations
        if violation.constraint
        not in {C_ASSET_MONTHLY_MAX}
        and violation.asset_id in {v.asset_id for v in request.fixed}
        and violation.visit_date in {v.visit_date for v in request.fixed}
    )
    if commitment_faults:
        return AllocationResult(
            status=FeasibilityStatus.INFEASIBLE,
            period=request.period,
            fixed=tuple(request.fixed),
            obligations=obligations,
            evidence=tuple(
                Evidence(
                    constraint=v.constraint,
                    detail=v.message,
                    asset_id=v.asset_id,
                    asset_type=v.asset_type,
                    visit_date=v.visit_date,
                    offending_dates=v.offending_dates,
                    limit=v.limit,
                    actual=v.actual,
                )
                for v in commitment_faults
            ),
            seed=request.seed,
            from_cycle=request.from_cycle,
        )

    # 2. Constructive placement, validated on every candidate. A candidate that
    #    fails validation is discarded rather than repaired, because the
    #    validator is the only authority on legality.
    #
    #    Construction runs before the feasibility assessment deliberately. The
    #    assessment is exact and can spend its whole node budget on a large
    #    roster (measured: ~40s for a 54-asset month), while construction found a
    #    valid month in ~0.09s. The assessment is therefore used as an oracle of
    #    impossibility *after* construction fails, and as a source of a witness,
    #    never as a cost every allocation must pay.
    for attempt in range(request.attempt_budget):
        if request.on_attempt is not None:
            # Reported before the attempt runs, so the count an operator watches is the
            # number of attempts already spent rather than one behind.
            request.on_attempt(attempt + 1, request.attempt_budget)
        # A string seed keeps this deterministic across runs and platforms, and
        # folds the seed and attempt together without depending on hash randomisation.
        rng = random.Random(f"{request.seed}:{attempt}")
        candidate = _attempt_placement(request, required, eligible, rng)
        if candidate is None:
            continue
        validated = validate_schedule(
            ProposalContext(
                period=request.period,
                calendar=request.calendar,
                roster=request.roster,
                config=request.config,
                proposed=candidate,
                history=request.history,
                gap_blockers=request.gap_blockers,
                required_visits=request.required_visits,
                cycle_obligations=request.cycle_obligations,
            ),
            ValidationMode.OBLIGATIONS,
        )
        if validated.valid:
            return AllocationResult(
                status=FeasibilityStatus.FEASIBLE,
                period=request.period,
                visits=candidate,
                fixed=tuple(request.fixed),
                obligations=obligations,
                seed=request.seed,
                from_cycle=request.from_cycle,
            )

    # 3. Construction produced nothing. Only now ask the validator whether the
    #    month is impossible, so a real proof is honoured but never paid for.
    assessment = assess_feasibility(
        ObligationContext(
            period=request.period,
            calendar=request.calendar,
            roster=request.roster,
            config=request.config,
            history=request.history,
            gap_blockers=request.gap_blockers,
            fixed=request.fixed,
            required_visits=request.required_visits,
            cycle_obligations=request.cycle_obligations,
        )
    )
    if assessment.status is FeasibilityStatus.INFEASIBLE:
        return AllocationResult(
            status=FeasibilityStatus.INFEASIBLE,
            period=request.period,
            fixed=tuple(request.fixed),
            obligations=obligations,
            evidence=assessment.evidence,
            seed=request.seed,
            from_cycle=request.from_cycle,
        )

    # 4. The assessment may hold a witness that construction failed to reach. A
    #    valid witness is a valid answer; refusing it because a heuristic found
    #    nothing better would reject a proven schedule.
    if assessment.status is FeasibilityStatus.FEASIBLE and assessment.witness:
        validated = validate_schedule(
            ProposalContext(
                period=request.period,
                calendar=request.calendar,
                roster=request.roster,
                config=request.config,
                proposed=assessment.witness,
                history=request.history,
                gap_blockers=request.gap_blockers,
                required_visits=request.required_visits,
                cycle_obligations=request.cycle_obligations,
            ),
            ValidationMode.OBLIGATIONS,
        )
        if validated.valid:
            return AllocationResult(
                status=FeasibilityStatus.FEASIBLE,
                period=request.period,
                visits=tuple(assessment.witness),
                fixed=tuple(request.fixed),
                obligations=obligations,
                seed=request.seed,
                from_cycle=request.from_cycle,
                notes=("placed from the feasibility witness",),
            )

    # 5. No witness and no proof. Report that plainly; never claim impossibility.
    return AllocationResult(
        status=FeasibilityStatus.INDETERMINATE,
        period=request.period,
        fixed=tuple(request.fixed),
        obligations=obligations,
        evidence=(
            Evidence(
                constraint=C_ALLOCATION_BUDGET_EXHAUSTED,
                detail=(
                    f"no valid schedule was constructed in {request.attempt_budget} "
                    "attempts and infeasibility was not proven; the month is "
                    "unresolved rather than impossible"
                ),
            ),
        ),
        seed=request.seed,
        from_cycle=request.from_cycle,
    )


def describe_pressure(request: AllocationRequest) -> tuple[Evidence, ...]:
    """Aggregate arithmetic that limits a month, for diagnostics only.

    These are the recorded necessary conditions, not proofs of feasibility and
    not proofs of infeasibility on their own; they are surfaced so a caller can
    explain why a target looks tight.
    """

    roster = {asset.asset_id: asset for asset in request.roster}
    eligible = _eligible_dates(request.period, request.calendar)

    obligations = resolve_obligations(
        request.roster,
        request.config,
        request.required_visits,
        request.cycle_obligations,
    )
    total = obligations.total_owed
    owed_by_kind = {kind: 0 for kind in AssetType}
    for asset_id, count in obligations.per_asset.items():
        owed_by_kind[roster[asset_id].asset_type] += count
    combined_capacity = len(eligible) * request.config.total_capacity_per_day

    notes: list[Evidence] = []
    for kind in AssetType:
        cap = request.config.cap_for(kind)
        capacity = len(eligible) * cap
        if owed_by_kind[kind] <= capacity:
            continue
        label = KIND_LABEL[kind]
        notes.append(
            Evidence(
                constraint=KIND_AGGREGATE_CONSTRAINT[kind],
                detail=(
                    f"{owed_by_kind[kind]} {label} visits are owed but "
                    f"{len(eligible)} eligible dates at {cap} per date allow "
                    f"{capacity}"
                ),
                limit=capacity,
                actual=owed_by_kind[kind],
            )
        )
    combined_cap = request.config.combined_daily_cap
    if combined_cap is not None and total > len(eligible) * combined_cap:
        notes.append(
            Evidence(
                constraint=C_AGG_COMBINED_CAPACITY,
                detail=(
                    f"{total} visits are owed but {len(eligible)} eligible dates at "
                    f"{combined_cap} per date allow {len(eligible) * combined_cap}"
                ),
                limit=len(eligible) * combined_cap,
                actual=total,
            )
        )
    return tuple(notes)
