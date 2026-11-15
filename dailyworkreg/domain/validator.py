"""Deterministic date-level field-visit feasibility validation.

This module implements the hard field-visit constraints recorded in
``documentation/PRODUCT_REQUIREMENTS_BASELINE.md`` sections 10.1 to 10.6.

Two distinct problems are solved here and must not be conflated:

*   Proposed-schedule validation (:func:`validate_schedule`) checks an actual
    list of proposed visits against every hard constraint. It is exact.
*   Target-obligation feasibility assessment (:func:`assess_feasibility`)
    determines whether at least one valid assignment exists for a set of
    required visit obligations. It never claims infeasibility from a failed
    heuristic; only from a completed exhaustive search or an arithmetic proof.

The module is pure domain code. It imports no web, database or presentation
layer, keeps dates as :class:`datetime.date` values, and is deterministic: no
randomness and no reliance on dictionary or set ordering for correctness.

Constraint identifiers used in violations and evidence are stable strings so
that callers and the future UI can map them to messages without parsing prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterable, Mapping, Sequence

#: Default combined field-visit limit per working day (areas + DTCs + feeders).
DEFAULT_COMBINED_DAILY_CAP = 3

#: Default area-only visits permitted on a single working day.
DEFAULT_AREA_DAILY_CAP = 2

#: Default maximum visits to one individual asset in one calendar month.
DEFAULT_PER_ASSET_MONTHLY_MAX = 3

#: Default minimum calendar days between consecutive visits to one asset.
DEFAULT_REVISIT_GAP_DAYS = 7

#: Upper bound on search-tree nodes explored by the feasibility solver. Exceeding
#: it yields INDETERMINATE, never INFEASIBLE.
DEFAULT_SEARCH_NODE_LIMIT = 2_000_000

# --- constraint identifiers -------------------------------------------------

C_WORKING_DATE = "not_a_working_date"
C_UNKNOWN_ASSET = "unknown_asset"
C_INACTIVE_ASSET = "inactive_asset"
C_AREA_DAILY_CAP = "area_daily_cap_exceeded"
C_COMBINED_DAILY_CAP = "combined_daily_cap_exceeded"
C_ASSET_MONTHLY_MAX = "asset_monthly_max_exceeded"
C_REVISIT_GAP = "revisit_gap_violated"
C_ASSET_MINIMUM = "asset_minimum_not_met"
C_CYCLE_OBLIGATION = "cycle_obligation_not_met"
C_OUTSIDE_PERIOD = "visit_outside_period"
C_PERIOD_NOT_WORKING = "period_date_not_working"
C_MIN_EXCEEDS_MAX = "configured_minimum_exceeds_maximum"
C_AGG_AREA_CAPACITY = "aggregate_area_capacity_exceeded"
C_AGG_COMBINED_CAPACITY = "aggregate_combined_capacity_exceeded"
C_SEARCH_LIMIT = "search_node_limit_reached"


class AssetType(str, Enum):
    """Visit categories that share the combined daily capacity."""

    AREA = "AREA"
    DTC = "DTC"
    FEEDER = "FEEDER"


class ValidationMode(str, Enum):
    """How much of the obligation set a proposed schedule must satisfy.

    ``SCHEDULE_ONLY`` checks the hard calendar, capacity and per-asset limits of
    whatever visits were proposed. It does not require that every configured
    minimum or cycle obligation be met, because a partially filled draft is a
    legitimate intermediate state.

    ``OBLIGATIONS`` additionally requires that every configured per-asset
    minimum and every declared cycle obligation be satisfied.
    """

    SCHEDULE_ONLY = "schedule_only"
    OBLIGATIONS = "obligations"


class FeasibilityStatus(str, Enum):
    """Outcome of a target-obligation feasibility assessment.

    ``FEASIBLE`` requires a witness assignment or a proven constructible
    assignment. ``INFEASIBLE`` requires a complete proof: either an arithmetic
    argument or an exhaustive search that found no assignment. Anything the
    implemented method could not settle is ``INDETERMINATE``.
    """

    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    INDETERMINATE = "indeterminate"


# --- input contract ---------------------------------------------------------


@dataclass(frozen=True, order=True)
class Asset:
    """A visitable asset.

    ``active`` is part of the roster contract rather than a silent filter, so an
    inactive asset appearing in a proposed schedule is reported as a violation
    instead of being dropped.
    """

    asset_id: str
    asset_type: AssetType
    active: bool = True

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ValueError("asset_id must be a non-empty string")


@dataclass(frozen=True)
class AssetLimits:
    """Effective per-asset limits for the selected period.

    ``monthly_max`` is an upper bound and never an obligation. ``monthly_min``
    is a genuine obligation and is therefore only enforced in
    :attr:`ValidationMode.OBLIGATIONS`.
    """

    monthly_max: int = DEFAULT_PER_ASSET_MONTHLY_MAX
    monthly_min: int = 0
    revisit_gap_days: int = DEFAULT_REVISIT_GAP_DAYS

    def __post_init__(self) -> None:
        if self.monthly_max < 0:
            raise ValueError("monthly_max must be non-negative")
        if self.monthly_min < 0:
            raise ValueError("monthly_min must be non-negative")
        if self.revisit_gap_days < 1:
            raise ValueError("revisit_gap_days must be at least 1")


@dataclass(frozen=True)
class FeasibilityConfig:
    """Effective scheduling configuration for one validation run.

    The defaults mirror the recorded initial policy: 3 combined visits per
    working day, at most 2 of them area visits, at most 3 visits to any single
    asset per month, and 7 calendar days between consecutive visits to the same
    asset. Every value remains configurable.
    """

    combined_daily_cap: int = DEFAULT_COMBINED_DAILY_CAP
    area_daily_cap: int = DEFAULT_AREA_DAILY_CAP
    default_asset_limits: AssetLimits = field(default_factory=AssetLimits)
    asset_limits: Mapping[str, AssetLimits] = field(default_factory=dict)
    search_node_limit: int = DEFAULT_SEARCH_NODE_LIMIT

    def __post_init__(self) -> None:
        if self.combined_daily_cap < 1:
            raise ValueError("combined_daily_cap must be at least 1")
        if self.area_daily_cap < 0:
            raise ValueError("area_daily_cap must be non-negative")
        if self.area_daily_cap > self.combined_daily_cap:
            raise ValueError("area_daily_cap cannot exceed combined_daily_cap")
        if self.search_node_limit < 1:
            raise ValueError("search_node_limit must be at least 1")

    def limits_for(self, asset_id: str) -> AssetLimits:
        """Return the effective limits for ``asset_id``.

        An asset-specific entry overrides the default entry wholesale, so a
        partially specified override never silently inherits half of the default.
        """

        return self.asset_limits.get(asset_id, self.default_asset_limits)


@dataclass(frozen=True)
class Calendar:
    """Eligibility of every date the validator may consider.

    Holiday, leave and date-override effects are represented here by the caller.
    The validator never imports official holiday data and never infers that a
    weekday is eligible. Dates absent from ``working_days`` are treated as
    non-working, so an incomplete calendar fails closed.
    """

    working_days: frozenset[date]

    @classmethod
    def from_dates(cls, dates: Iterable[date]) -> "Calendar":
        return cls(working_days=frozenset(dates))

    def is_working(self, day: date) -> bool:
        return day in self.working_days


@dataclass(frozen=True, order=True)
class Visit:
    """A proposed or historical visit of one asset on one calendar date."""

    asset_id: str
    visit_date: date


@dataclass(frozen=True)
class DatePeriod:
    """Inclusive date range under validation."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("period end must not precede period start")

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True)
class ProposalContext:
    """Everything needed to validate a proposed schedule."""

    period: DatePeriod
    calendar: Calendar
    roster: Sequence[Asset]
    config: FeasibilityConfig
    proposed: Sequence[Visit]
    history: Sequence[Visit] = ()

    def asset_index(self) -> dict[str, Asset]:
        return {asset.asset_id: asset for asset in self.roster}


@dataclass(frozen=True)
class ObligationContext:
    """Everything needed to assess whether obligations can be scheduled."""

    period: DatePeriod
    calendar: Calendar
    roster: Sequence[Asset]
    config: FeasibilityConfig
    history: Sequence[Visit] = ()
    #: Declared per-asset visit obligation for this period. Only minima and
    #: cycle obligations belong here; a monthly maximum is never an obligation.
    required_visits: Mapping[str, int] = field(default_factory=dict)
    #: Optional declared coverage-cycle obligations, already resolved to a
    #: required visit count for the current cycle window by the caller.
    cycle_obligations: Mapping[str, int] = field(default_factory=dict)

    def asset_index(self) -> dict[str, Asset]:
        return {asset.asset_id: asset for asset in self.roster}

    def obligation_for(self, asset_id: str) -> int:
        """Return the required visit count for ``asset_id``.

        The larger of the configured minimum and an explicit required count
        applies; a monthly maximum is never treated as a requirement.
        """

        limits = self.config.limits_for(asset_id)
        return max(
            limits.monthly_min,
            self.required_visits.get(asset_id, 0),
            self.cycle_obligations.get(asset_id, 0),
        )


# --- result model -----------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """One detected constraint violation, suitable for display without parsing."""

    constraint: str
    message: str
    asset_id: str | None = None
    asset_type: AssetType | None = None
    visit_date: date | None = None
    conflicting_dates: tuple[date, ...] = ()
    limit: int | None = None
    actual: int | None = None

    def sort_key(self) -> tuple:
        return (
            self.visit_date or date.min,
            self.asset_id or "",
            self.constraint,
        )


@dataclass(frozen=True)
class Evidence:
    """A machine-readable fact supporting a feasibility verdict."""

    constraint: str
    detail: str
    limit: int | None = None
    actual: int | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Result of proposed-schedule validation."""

    valid: bool
    violations: tuple[Violation, ...]
    days_used: Mapping[date, int]

    @property
    def violation_constraints(self) -> tuple[str, ...]:
        return tuple(v.constraint for v in self.violations)


@dataclass(frozen=True)
class FeasibilityResult:
    """Result of target-obligation feasibility assessment.

    ``witness`` is populated only for ``FEASIBLE`` results. ``evidence``
    explains which constraints limited the verdict, and for ``INDETERMINATE``
    states what the method could not settle.
    """

    status: FeasibilityStatus
    evidence: tuple[Evidence, ...]
    witness: tuple[Visit, ...] = ()
    limits_reached: tuple[str, ...] = ()

    @property
    def is_proven(self) -> bool:
        return self.status is not FeasibilityStatus.INDETERMINATE


# --- shared constraint primitives -------------------------------------------


def calendar_day_gap(earlier: date, later: date) -> int:
    """Elapsed calendar days between two dates.

    This is plain date arithmetic, so weekends and holidays count as elapsed
    days. The settled minimum revisit interval is seven calendar days, not seven
    working days.
    """

    return (later - earlier).days


def visits_by_asset(visits: Iterable[Visit]) -> dict[str, list[Visit]]:
    """Group visits by asset id, each list sorted chronologically."""

    grouped: dict[str, list[Visit]] = {}
    for visit in visits:
        grouped.setdefault(visit.asset_id, []).append(visit)
    for bucket in grouped.values():
        bucket.sort(key=lambda v: v.visit_date)
    return grouped


def visits_by_date(visits: Iterable[Visit]) -> dict[date, list[Visit]]:
    """Group visits by date, each list sorted by asset id for determinism."""

    grouped: dict[date, list[Visit]] = {}
    for visit in visits:
        grouped.setdefault(visit.visit_date, []).append(visit)
    for bucket in grouped.values():
        bucket.sort(key=lambda v: v.asset_id)
    return grouped


def _daily_cap_violations(
    visits: Sequence[Visit],
    roster: Mapping[str, Asset],
    config: FeasibilityConfig,
) -> list[Violation]:
    """Check the area-only cap and the combined cap for every date.

    Both caps are independent. The combined cap counts every category together,
    which is why category day counts must never be summed to reason about
    capacity: different categories may occupy the same date.
    """

    violations: list[Violation] = []
    for day, bucket in sorted(visits_by_date(visits).items()):
        area_ids = [v.asset_id for v in bucket
                    if roster.get(v.asset_id) is not None
                    and roster[v.asset_id].asset_type is AssetType.AREA]
        combined = len(bucket)
        if len(area_ids) > config.area_daily_cap:
            violations.append(
                Violation(
                    constraint=C_AREA_DAILY_CAP,
                    message=(
                        f"{len(area_ids)} area visits on {day.isoformat()}; "
                        f"the area limit is {config.area_daily_cap} per working day"
                    ),
                    visit_date=day,
                    limit=config.area_daily_cap,
                    actual=len(area_ids),
                )
            )
        if combined > config.combined_daily_cap:
            violations.append(
                Violation(
                    constraint=C_COMBINED_DAILY_CAP,
                    message=(
                        f"{combined} field visits on {day.isoformat()}; "
                        f"the combined limit is {config.combined_daily_cap} per working day"
                    ),
                    visit_date=day,
                    limit=config.combined_daily_cap,
                    actual=combined,
                )
            )
    return violations


def _revisit_violations(
    grouped: Mapping[str, Sequence[Visit]],
    roster: Mapping[str, Asset],
    config: FeasibilityConfig,
) -> list[Violation]:
    """Check consecutive-visit gaps per asset, including history.

    History is passed in the same grouping as proposed visits, so a proposed
    visit that falls inside the gap following a prior-period or prior-cycle
    visit is caught without any boundary reset.
    """

    violations: list[Violation] = []
    for asset_id, bucket in sorted(grouped.items()):
        limits = config.limits_for(asset_id)
        asset = roster.get(asset_id)
        for earlier, later in zip(bucket, bucket[1:]):
            gap = calendar_day_gap(earlier.visit_date, later.visit_date)
            if gap < limits.revisit_gap_days:
                violations.append(
                    Violation(
                        constraint=C_REVISIT_GAP,
                        message=(
                            f"visits to {asset_id} on {earlier.visit_date.isoformat()} and "
                            f"{later.visit_date.isoformat()} are {gap} calendar days apart; "
                            f"the minimum is {limits.revisit_gap_days}"
                        ),
                        asset_id=asset_id,
                        asset_type=asset.asset_type if asset else None,
                        visit_date=later.visit_date,
                        conflicting_dates=(earlier.visit_date, later.visit_date),
                        limit=limits.revisit_gap_days,
                        actual=gap,
                    )
                )
    return violations


def _monthly_count_violations(
    grouped: Mapping[str, Sequence[Visit]],
    period: DatePeriod,
    roster: Mapping[str, Asset],
    config: FeasibilityConfig,
) -> list[Violation]:
    """Check the per-asset monthly maximum inside the selected period.

    Only visits whose date falls inside the period count toward the monthly
    total, so a gap that spans a month boundary does not reset or inflate the
    count. The maximum caps visits; it never creates an obligation.
    """

    violations: list[Violation] = []
    for asset_id, bucket in sorted(grouped.items()):
        limits = config.limits_for(asset_id)
        in_period = [v for v in bucket if period.contains(v.visit_date)]
        if len(in_period) > limits.monthly_max:
            asset = roster.get(asset_id)
            violations.append(
                Violation(
                    constraint=C_ASSET_MONTHLY_MAX,
                    message=(
                        f"{asset_id} has {len(in_period)} visits in the period; "
                        f"the monthly maximum is {limits.monthly_max}"
                    ),
                    asset_id=asset_id,
                    asset_type=asset.asset_type if asset else None,
                    visit_date=in_period[-1].visit_date,
                    limit=limits.monthly_max,
                    actual=len(in_period),
                )
            )
    return violations


def _asset_minimum_violations(
    grouped: Mapping[str, Sequence[Visit]],
    period: DatePeriod,
    roster: Mapping[str, Asset],
    config: FeasibilityConfig,
) -> list[Violation]:
    """Check configured per-asset monthly minimums.

    A minimum of zero is the default and is never reported. A configured
    minimum above the effective maximum is reported as an impossible
    configuration rather than as a missing visit.
    """

    violations: list[Violation] = []
    for asset_id, asset in sorted(roster.items()):
        limits = config.limits_for(asset_id)
        if limits.monthly_min <= 0:
            continue
        if limits.monthly_min > limits.monthly_max:
            violations.append(
                Violation(
                    constraint=C_MIN_EXCEEDS_MAX,
                    message=(
                        f"{asset_id} has a configured minimum of {limits.monthly_min} "
                        f"and a maximum of {limits.monthly_max}; the minimum cannot be reached"
                    ),
                    asset_id=asset_id,
                    asset_type=asset.asset_type,
                    limit=limits.monthly_max,
                    actual=limits.monthly_min,
                )
            )
            continue
        count = sum(1 for v in grouped.get(asset_id, ()) if period.contains(v.visit_date))
        if count < limits.monthly_min:
            violations.append(
                Violation(
                    constraint=C_ASSET_MINIMUM,
                    message=(
                        f"{asset_id} has {count} visits in the period; "
                        f"the configured minimum is {limits.monthly_min}"
                    ),
                    asset_id=asset_id,
                    asset_type=asset.asset_type,
                    limit=limits.monthly_min,
                    actual=count,
                )
            )
    return violations


# --- problem A: proposed-schedule validation --------------------------------


def validate_schedule(
    context: ProposalContext,
    mode: ValidationMode = ValidationMode.SCHEDULE_ONLY,
) -> ValidationResult:
    """Validate an actual proposed schedule against every hard constraint.

    This operation is exact: it inspects the visits it is given and reports
    every violation it finds. It does not attempt to repair or optimise the
    schedule, and it does not infer missing obligations from maximums.

    Args:
        context: Period, calendar, roster, configuration, proposed visits and
            prior history.
        mode: ``SCHEDULE_ONLY`` checks the hard limits of the proposal.
            ``OBLIGATIONS`` additionally requires every configured minimum and
            cycle obligation to be met.

    Returns:
        A :class:`ValidationResult` whose violations are ordered
        deterministically by date, asset id and constraint identifier.
    """

    roster = context.asset_index()
    violations: list[Violation] = []

    # 1. Period membership, asset identity, asset activity and working-date
    #    eligibility. Every proposed visit is examined individually.
    for visit in context.proposed:
        if not context.period.contains(visit.visit_date):
            violations.append(
                Violation(
                    constraint=C_OUTSIDE_PERIOD,
                    message=(
                        f"visit to {visit.asset_id} on {visit.visit_date.isoformat()} "
                        f"falls outside {context.period.start.isoformat()} to "
                        f"{context.period.end.isoformat()}"
                    ),
                    asset_id=visit.asset_id,
                    visit_date=visit.visit_date,
                )
            )
            continue
        asset = roster.get(visit.asset_id)
        if asset is None:
            violations.append(
                Violation(
                    constraint=C_UNKNOWN_ASSET,
                    message=f"visit refers to unknown asset {visit.asset_id}",
                    asset_id=visit.asset_id,
                    visit_date=visit.visit_date,
                )
            )
            continue
        if not asset.active:
            violations.append(
                Violation(
                    constraint=C_INACTIVE_ASSET,
                    message=f"visit refers to inactive asset {visit.asset_id}",
                    asset_id=visit.asset_id,
                    asset_type=asset.asset_type,
                    visit_date=visit.visit_date,
                )
            )
        if not context.calendar.is_working(visit.visit_date):
            violations.append(
                Violation(
                    constraint=C_WORKING_DATE,
                    message=(
                        f"{visit.visit_date.isoformat()} is not an eligible working date, "
                        f"but {visit.asset_id} is proposed on it"
                    ),
                    asset_id=visit.asset_id,
                    asset_type=asset.asset_type,
                    visit_date=visit.visit_date,
                )
            )

    # 2. Daily capacity. Different categories share one date within the
    #    combined cap, so both caps are evaluated over the actual date buckets.
    violations.extend(_daily_cap_violations(context.proposed, roster, context.config))

    # 3. Per-asset checks. History is merged so that the revisit interval is
    #    enforced across month and cycle boundaries without a reset.
    merged = visits_by_asset(tuple(context.history) + tuple(context.proposed))
    violations.extend(_revisit_violations(merged, roster, context.config))
    violations.extend(
        _monthly_count_violations(merged, context.period, roster, context.config)
    )

    # 4. Obligations, when the caller requires them.
    if mode is ValidationMode.OBLIGATIONS:
        violations.extend(
            _asset_minimum_violations(merged, context.period, roster, context.config)
        )

    days_used: dict[date, int] = {}
    for day, bucket in visits_by_date(context.proposed).items():
        days_used[day] = len(bucket)

    ordered = tuple(sorted(violations, key=lambda v: v.sort_key()))
    return ValidationResult(valid=not ordered, violations=ordered, days_used=days_used)


# --- problem B: target-obligation feasibility assessment --------------------
# --- problem B: target-obligation feasibility assessment --------------------


def _eligible_dates(period: DatePeriod, calendar: Calendar) -> list[date]:
    """Return the eligible working dates in the period, ascending."""

    return sorted(d for d in calendar.working_days if period.contains(d))


def _placements_for_asset(
    candidates: Sequence[date],
    count: int,
    gap_days: int,
    node_budget: list[int],
) -> list[tuple[date, ...]] | None:
    """Enumerate every valid placement of ``count`` visits for one asset.

    Returns ``None`` when the node budget is exhausted, which the caller must
    translate into ``INDETERMINATE`` rather than ``INFEASIBLE``. An empty list
    is a genuine proof that this single asset cannot meet its obligation,
    because the enumeration is exhaustive over the candidate dates.

    Args:
        candidates: Eligible working dates available to this asset, ascending.
        count: Required number of visits.
        gap_days: Minimum calendar days between consecutive visits.
        node_budget: Single-element list holding the remaining node allowance.
    """

    if count == 0:
        return [()]
    if count > len(candidates):
        return []

    results: list[tuple[date, ...]] = []
    stack: list[tuple[int, tuple[date, ...]]] = [(-1, ())]
    while stack:
        node_budget[0] -= 1
        if node_budget[0] <= 0:
            return None
        last_index, chosen = stack.pop()
        if len(chosen) == count:
            results.append(chosen)
            continue
        for index in range(last_index + 1, len(candidates)):
            candidate = candidates[index]
            if chosen and calendar_day_gap(chosen[-1], candidate) < gap_days:
                continue
            stack.append((index, chosen + (candidate,)))
    results.sort()
    return results


class _SearchBudgetExhausted(Exception):
    """Raised internally when the joint packing search runs out of nodes."""


def _pack_into_days(
    placements: Mapping[str, Sequence[tuple[date, ...]]],
    eligible: Sequence[date],
    config: FeasibilityConfig,
    roster: Mapping[str, Asset],
    node_budget: list[int],
) -> dict[str, tuple[date, ...]] | None:
    """Search for a joint assignment that satisfies both daily caps.

    The search is a complete depth-first backtrack over each asset's enumerated
    placements. Because it is complete, an exhausted search is a genuine
    infeasibility proof for the bounded input, and any returned mapping is a
    witness that can be validated independently. The node budget makes a
    pathological input stop with ``None`` instead of running unbounded; the
    caller distinguishes that case from a proven infeasibility.

    Assets are visited most-constrained-first (fewest available placements) so
    that contradictions surface early and the witness is found quickly when one
    exists.

    Args:
        placements: Per-asset candidate placements, already gap-filtered.
        eligible: Eligible working dates, used to size the usage counters.
        config: Effective caps.
        roster: Asset index used to identify area visits.
        node_budget: Single-element list holding the remaining node allowance.
    """

    asset_ids = sorted(
        placements, key=lambda asset_id: (len(placements[asset_id]), asset_id)
    )
    usage_total: dict[date, int] = {d: 0 for d in eligible}
    usage_area: dict[date, int] = {d: 0 for d in eligible}
    chosen: dict[str, tuple[date, ...]] = {}

    def recurse(index: int) -> dict[str, tuple[date, ...]] | None:
        node_budget[0] -= 1
        if node_budget[0] <= 0:
            raise _SearchBudgetExhausted
        if index == len(asset_ids):
            return dict(chosen)
        asset_id = asset_ids[index]
        is_area = roster[asset_id].asset_type is AssetType.AREA
        for placement in placements[asset_id]:
            fits = True
            for day in placement:
                if usage_total[day] + 1 > config.combined_daily_cap:
                    fits = False
                    break
                if usage_area[day] + (1 if is_area else 0) > config.area_daily_cap:
                    fits = False
                    break
            if not fits:
                continue
            for day in placement:
                usage_total[day] += 1
                if is_area:
                    usage_area[day] += 1
            chosen[asset_id] = placement
            found = recurse(index + 1)
            if found is not None:
                return found
            del chosen[asset_id]
            for day in placement:
                usage_total[day] -= 1
                if is_area:
                    usage_area[day] -= 1
        return None

    try:
        return recurse(0)
    except _SearchBudgetExhausted:
        return None


def _candidate_dates(
    eligible: Sequence[date],
    history_dates: Sequence[date],
    gap_days: int,
) -> list[date]:
    """Filter eligible dates to those compatible with an asset's prior visits.

    A candidate must be at least ``gap_days`` calendar days from every prior
    visit, whether the prior visit fell before or after the candidate. This is
    what carries the revisit interval across a month or cycle boundary without
    a reset.
    """

    candidates: list[date] = []
    for day in eligible:
        ok = True
        for prior in history_dates:
            if abs(calendar_day_gap(prior, day)) < gap_days:
                ok = False
                break
        if ok:
            candidates.append(day)
    return candidates


def assess_feasibility(context: ObligationContext) -> FeasibilityResult:
    """Assess whether the declared obligations admit at least one valid schedule.

    The assessment is exact within a declared node budget:

    *   ``FEASIBLE`` when a joint assignment exists and is returned as a
        witness that can be re-validated independently.
    *   ``INFEASIBLE`` only on a proof: an unsatisfiable configuration, an
        obligation above the effective per-asset maximum, a failed aggregate
        necessary condition, an asset with no valid placement at all, or a
        completed exhaustive search that found nothing.
    *   ``INDETERMINATE`` when the node budget is exhausted first. A failed
        heuristic is never reported as infeasible.

    Args:
        context: Period, calendar, roster, configuration, history and the
            declared obligations.

    Returns:
        A :class:`FeasibilityResult`.
    """

    roster = context.asset_index()
    eligible = _eligible_dates(context.period, context.calendar)
    working_days = len(eligible)

    required: dict[str, int] = {}
    for asset_id in sorted(roster):
        if not roster[asset_id].active:
            continue
        count = context.obligation_for(asset_id)
        if count > 0:
            required[asset_id] = count

    total_required = sum(required.values())
    area_required = sum(
        count
        for asset_id, count in required.items()
        if roster[asset_id].asset_type is AssetType.AREA
    )

    if not required:
        return FeasibilityResult(status=FeasibilityStatus.FEASIBLE, evidence=())

    evidence: list[Evidence] = []

    # An obligation above the effective per-asset maximum cannot be met. The
    # maximum is an upper bound, so this is a configuration contradiction, not
    # a demand that the maximum be reached.
    for asset_id in sorted(required):
        limits = context.config.limits_for(asset_id)
        if limits.monthly_min > limits.monthly_max:
            evidence.append(
                Evidence(
                    constraint=C_MIN_EXCEEDS_MAX,
                    detail=(
                        f"{asset_id} requires at least {limits.monthly_min} visits "
                        f"but its monthly maximum is {limits.monthly_max}"
                    ),
                    limit=limits.monthly_max,
                    actual=limits.monthly_min,
                )
            )
            return FeasibilityResult(
                status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
            )
        if required[asset_id] > limits.monthly_max:
            evidence.append(
                Evidence(
                    constraint=C_ASSET_MONTHLY_MAX,
                    detail=(
                        f"{asset_id} requires {required[asset_id]} visits but the "
                        f"monthly maximum is {limits.monthly_max}"
                    ),
                    limit=limits.monthly_max,
                    actual=required[asset_id],
                )
            )
            return FeasibilityResult(
                status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
            )

    # Aggregate necessary conditions. These are necessary only; a failure is a
    # proof, while passing proves nothing about date-level feasibility.
    if area_required > context.config.area_daily_cap * working_days:
        evidence.append(
            Evidence(
                constraint=C_AGG_AREA_CAPACITY,
                detail=(
                    f"{area_required} required area visits exceed the area limit of "
                    f"{context.config.area_daily_cap} per working day across "
                    f"{working_days} eligible dates"
                ),
                limit=context.config.area_daily_cap * working_days,
                actual=area_required,
            )
        )
        return FeasibilityResult(
            status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
        )
    if total_required > context.config.combined_daily_cap * working_days:
        evidence.append(
            Evidence(
                constraint=C_AGG_COMBINED_CAPACITY,
                detail=(
                    f"{total_required} required visits exceed the combined limit of "
                    f"{context.config.combined_daily_cap} per working day across "
                    f"{working_days} eligible dates"
                ),
                limit=context.config.combined_daily_cap * working_days,
                actual=total_required,
            )
        )
        return FeasibilityResult(
            status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
        )

    node_budget = [context.config.search_node_limit]
    history_by_asset = visits_by_asset(context.history)

    # Per-asset placements, honouring history and the calendar-day gap.
    placements: dict[str, Sequence[tuple[date, ...]]] = {}
    for asset_id in sorted(required):
        limits = context.config.limits_for(asset_id)
        history_dates = [v.visit_date for v in history_by_asset.get(asset_id, ())]
        candidates = _candidate_dates(
            eligible, history_dates, limits.revisit_gap_days
        )
        asset_placements = _placements_for_asset(
            candidates, required[asset_id], limits.revisit_gap_days, node_budget
        )
        if asset_placements is None:
            evidence.append(
                Evidence(
                    constraint=C_SEARCH_LIMIT,
                    detail=(
                        "the search node limit was reached while enumerating "
                        f"placements for {asset_id}; feasibility is undetermined"
                    ),
                    limit=context.config.search_node_limit,
                )
            )
            return FeasibilityResult(
                status=FeasibilityStatus.INDETERMINATE,
                evidence=tuple(evidence),
                limits_reached=(C_SEARCH_LIMIT,),
            )
        if not asset_placements:
            evidence.append(
                Evidence(
                    constraint=C_REVISIT_GAP,
                    detail=(
                        f"no placement of {required[asset_id]} visits for {asset_id} "
                        f"respects a {limits.revisit_gap_days}-calendar-day gap across "
                        f"{len(candidates)} usable eligible dates"
                    ),
                    limit=limits.revisit_gap_days,
                    actual=required[asset_id],
                )
            )
            return FeasibilityResult(
                status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
            )
        placements[asset_id] = asset_placements

    packed = _pack_into_days(
        placements, eligible, context.config, roster, node_budget
    )
    if packed is None:
        if node_budget[0] <= 0:
            evidence.append(
                Evidence(
                    constraint=C_SEARCH_LIMIT,
                    detail=(
                        "the search node limit was reached while packing visits into "
                        "working dates; feasibility is undetermined"
                    ),
                    limit=context.config.search_node_limit,
                )
            )
            return FeasibilityResult(
                status=FeasibilityStatus.INDETERMINATE,
                evidence=tuple(evidence),
                limits_reached=(C_SEARCH_LIMIT,),
            )
        evidence.append(
            Evidence(
                constraint=C_COMBINED_DAILY_CAP,
                detail=(
                    f"no joint assignment of {total_required} required visits "
                    f"satisfies both daily capacity limits across "
                    f"{working_days} eligible dates"
                ),
                limit=context.config.combined_daily_cap,
                actual=total_required,
            )
        )
        return FeasibilityResult(
            status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
        )

    witness = tuple(
        sorted(
            (
                Visit(asset_id=asset_id, visit_date=day)
                for asset_id, days in packed.items()
                for day in days
            ),
            key=lambda visit: (visit.visit_date, visit.asset_id),
        )
    )
    return FeasibilityResult(
        status=FeasibilityStatus.FEASIBLE, evidence=tuple(evidence), witness=witness
    )
