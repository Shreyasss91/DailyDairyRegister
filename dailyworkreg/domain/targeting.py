"""Weighted-fair DTC targeting, the no-cycle fallback and multi-month execution.

Three things live here, all of them about *which* DTCs a month's capacity is
spent on and about carrying a plan across months:

*   **Cycle mode** (the primary policy) - a coverage cycle gives every active DTC
    an equal obligation. Weight and rotation decide only which month inside the
    cycle an asset falls into.
*   **Fallback mode** - with no cycle declared, a month gets a
    feasibility-bounded target rather than a per-DTC obligation. The result is a
    capacity-based monthly suggestion and is labelled as such, never presented as
    a coverage commitment.
*   **Multi-month execution** - each month is allocated against its own period,
    with earlier months' visits carried forward as history so the seven-day gap
    and prior-visit fairness survive the boundary.

Weighted fairness is an objective, never a licence. No weighting decision here may
exceed a per-asset maximum, break the seven-day gap, or exceed daily capacity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Iterable, Mapping, Sequence

from .allocator import AllocationRequest, AllocationResult, allocate_month
from .cycle import (
    CoverageCycle,
    CycleResolution,
    DtcClass,
    DtcWeights,
    MonthKey,
    resolve_cycle,
    verify_cycle,
)
from .validator import (
    Asset,
    AssetType,
    Calendar,
    DatePeriod,
    Evidence,
    FeasibilityConfig,
    FeasibilityStatus,
    InputError,
    ObligationContext,
    ProposalContext,
    ValidationMode,
    Visit,
    assess_feasibility,
    resolve_obligations,
    validate_committed_visits,
    validate_schedule,
)

#: How a month's DTC targets were decided.
CYCLE_MODE = "coverage_cycle"
FALLBACK_MODE = "capacity_based_suggestion"

#: A cycle resolution failed its structural verification.
CYCLE_RESOLUTION_INVALID = "cycle_resolution_invalid"
#: An explicit month selection was empty.
C_MONTH_SELECTION_EMPTY = "month_selection_empty"
#: An explicit month selection repeated a month.
C_MONTH_SELECTION_DUPLICATE = "month_selection_duplicate"
#: A selected month falls outside the declared cycle.
C_MONTH_SELECTION_OUTSIDE_CYCLE = "month_selection_outside_cycle"
#: Committed visits exceed an asset's cycle obligation.
C_CYCLE_OVER_COVERED = "cycle_obligation_over_covered"
#: A multi-month helper received a partial (non full-calendar-month) request.
C_PARTIAL_PERIOD_UNSUPPORTED = "partial_period_unsupported"
#: A fixed-visit mapping named a month outside the declared cycle.
C_FIXED_MONTH_OUTSIDE_CYCLE = "fixed_month_outside_cycle"
#: A fixed visit's date is not in the month it was supplied under.
C_FIXED_VISIT_WRONG_MONTH = "fixed_visit_in_wrong_month"
#: A committed visit failed the shared hard-constraint validation.
C_CYCLE_FIXED_VISIT_INVALID = "cycle_fixed_visit_invalid"


def _validated_month_selection(
    selection: Sequence[MonthKey] | None,
    cycle_months: Sequence[MonthKey],
    cycle: CoverageCycle,
) -> tuple[MonthKey, ...]:
    """Validate an explicit month selection and return it in chronological order.

    An empty selection is rejected rather than accepted: a run that places no
    month would otherwise report the worst of zero outcomes, which is success,
    and a caller would present an empty result as a complete plan.

    Raises:
        InputError: On an empty selection, a duplicate month, or a month outside
            the declared cycle.
    """

    if selection is None:
        return tuple(cycle_months)

    chosen = tuple(selection)
    if not chosen:
        raise InputError(
            C_MONTH_SELECTION_EMPTY,
            "months_to_place was explicitly empty; at least one month must be "
            "selected, because a run that places nothing is not a successful "
            "allocation",
        )
    seen: set[MonthKey] = set()
    for month in chosen:
        if month in seen:
            raise InputError(
                C_MONTH_SELECTION_DUPLICATE,
                f"month {month.label()} was selected more than once",
            )
        seen.add(month)
        if not cycle.contains(month):
            raise InputError(
                C_MONTH_SELECTION_OUTSIDE_CYCLE,
                f"month {month.label()} is outside the declared cycle",
            )
    # Chronological output regardless of input order.
    return tuple(sorted(chosen, key=lambda m: m.ordinal()))


def _validated_month_sequence(months: Sequence[MonthKey]) -> tuple[MonthKey, ...]:
    """Validate a fallback month sequence: non-empty, unique, chronological.

    Raises:
        InputError: On an empty sequence or a duplicate month.
    """

    chosen = tuple(months)
    if not chosen:
        raise InputError(
            C_MONTH_SELECTION_EMPTY,
            "at least one month is required; a run that places nothing is not a "
            "successful allocation",
        )
    seen: set[MonthKey] = set()
    for month in chosen:
        if month in seen:
            raise InputError(
                C_MONTH_SELECTION_DUPLICATE,
                f"month {month.label()} appears more than once",
            )
        seen.add(month)
    # Sorted, so history can never be carried backwards from a future month into
    # an earlier one.
    return tuple(sorted(chosen, key=lambda m: m.ordinal()))


def _require_full_month(period: DatePeriod, caller: str) -> None:
    """Reject a partial period in a convenience API.

    Multi-month cycle and fallback helpers are full-calendar-month only. They say
    so explicitly rather than quietly applying full-month limits to a shorter
    period, which is the failure mode the partial-month contract exists to
    prevent.
    """

    if not period.is_full_month:
        raise InputError(
            C_PARTIAL_PERIOD_UNSUPPORTED,
            (
                f"{caller} supports full calendar months only; "
                f"{period.start.isoformat()} to {period.end.isoformat()} is a "
                "partial month and needs explicit prorated limits through "
                "allocate_month"
            ),
        )


class TargetMode(str, Enum):
    """Which DTC targeting policy produced a month's targets."""

    #: A declared coverage cycle. Every active DTC owes an equal cycle obligation.
    COVERAGE_CYCLE = CYCLE_MODE
    #: No cycle declared. A feasibility-bounded monthly target, weight-informed.
    CAPACITY_SUGGESTION = FALLBACK_MODE


@dataclass(frozen=True)
class FallbackTarget:
    """A capacity-based monthly DTC target computed without a coverage cycle.

    ``selected`` is the set of DTCs this month should visit, chosen by weighted
    fairness and least-recently-visited history. It is a suggestion bounded by
    capacity, and carries no claim that every DTC is covered within any period.
    """

    selected: tuple[str, ...]
    target_count: int
    capacity_bound: int
    weights: Mapping[str, float] = field(default_factory=dict)
    reason: str = "capacity_based_suggestion"


def _eligible_dates(period: DatePeriod, calendar: Calendar) -> tuple[date, ...]:
    return tuple(sorted(d for d in calendar.working_days if period.contains(d)))


def _prior_state(history: Iterable[Visit]) -> tuple[dict[str, int], dict[str, date]]:
    counts: dict[str, int] = {}
    last: dict[str, date] = {}
    for visit in history:
        counts[visit.asset_id] = counts.get(visit.asset_id, 0) + 1
        known = last.get(visit.asset_id)
        if known is None or visit.visit_date > known:
            last[visit.asset_id] = visit.visit_date
    return counts, last


def fallback_dtc_target(
    roster: Sequence[Asset],
    period: DatePeriod,
    calendar: Calendar,
    config: FeasibilityConfig,
    history: Iterable[Visit] = (),
    weights: DtcWeights | None = None,
    requested_target: int | None = None,
    area_and_feeder_demand: int = 0,
    rotation_offset: int = 0,
) -> FallbackTarget:
    """Choose a capacity-bounded DTC target for a month with no declared cycle.

    The target is the smallest of the requested target, the transformer capacity
    the calendar can hold under the DTC ceiling, and the number of active DTCs
    that can still take a visit this month under their own monthly maximum.

    Selection is least-recently-visited first, then heaviest weight, then asset
    id, rotated by ``rotation_offset`` so consecutive months do not always favour
    the same DTCs. Fairness across months is what keeps repeated generation from
    systematically skipping the same assets.

    Args:
        roster: Full roster; only active DTCs are candidates.
        period: The month being planned.
        calendar: Eligible working dates.
        config: Capacities and per-asset limits.
        history: Visits already made, used for fairness.
        weights: DTC weight model.
        requested_target: The owner's desired DTC visits this month, if any.
        area_and_feeder_demand: Visits the month already owes to areas and
            feeders. Retained for callers that still report it: each kind now has
            its own daily ceiling, so an area visit no longer takes a transformer's
            slot and this number does not reduce the transformer target.
        rotation_offset: Deterministic rotation state between months.

    Returns:
        A :class:`FallbackTarget` labelled as a capacity-based suggestion.
    """

    weight_model = weights or DtcWeights()
    dtcs = sorted(
        (a for a in roster if a.asset_type is AssetType.DTC and a.active),
        key=lambda a: a.asset_id,
    )

    eligible = _eligible_dates(period, calendar)
    # Transformers have their own ceiling, so the area and feeder demand of the same
    # month does not come out of this number: the kinds no longer compete for a slot.
    dtc_capacity = len(eligible) * config.dtc_daily_cap

    # A DTC with no room left under its monthly maximum cannot be a candidate, and
    # neither can one commissioned after the whole period: it has no usable date in
    # it, so selecting it could only produce a month that cannot be allocated.
    eligible_dtcs = [
        a
        for a in dtcs
        if config.limits_for(a.asset_id).monthly_max >= 1
        and any(a.is_available_on(day) for day in eligible)
    ]

    target = len(eligible_dtcs)
    if requested_target is not None:
        target = min(target, max(0, requested_target))
    target = min(target, dtc_capacity)

    counts, last = _prior_state(history)

    def sort_key(asset: Asset):
        previous = last.get(asset.asset_id)
        return (
            int(counts.get(asset.asset_id, 0)),
            0 if previous is None else 1,
            0 if previous is None else previous.toordinal(),
            -weight_model.weight_for(asset),
            asset.asset_id,
        )

    ordered = sorted(eligible_dtcs, key=sort_key)
    if ordered and rotation_offset:
        head_rank = sort_key(ordered[0])[:-2]
        group = [a for a in ordered if sort_key(a)[:-2] == head_rank]
        tail = [a for a in ordered if sort_key(a)[:-2] != head_rank]
        shift = rotation_offset % len(group)
        ordered = group[shift:] + group[:shift] + tail

    selected = tuple(a.asset_id for a in ordered[:target])
    return FallbackTarget(
        selected=selected,
        target_count=len(selected),
        capacity_bound=dtc_capacity,
        weights={asset_id: weight_model.weight_for(
            next(a for a in dtcs if a.asset_id == asset_id)
        ) for asset_id in selected},
    )


@dataclass(frozen=True)
class MonthPlan:
    """One month of a multi-month run, with its own validation evidence."""

    month: MonthKey
    period: DatePeriod
    result: AllocationResult
    mode: TargetMode
    #: The obligations this month was actually allocated against, after committed
    #: visits in the cycle were reconciled against the declared plan. This is what
    #: the allocator received and what the month's validation used.
    obligations: Mapping[str, int] = field(default_factory=dict)
    #: The obligations the coverage cycle declared for this month, before
    #: commitments were reconciled. Kept separately so the declared plan is never
    #: confused with the adjusted one.
    declared_obligations: Mapping[str, int] = field(default_factory=dict)

    @property
    def visits(self) -> tuple[Visit, ...]:
        return self.result.visits


@dataclass(frozen=True)
class MultiMonthResult:
    """A whole range or cycle, month by month.

    Month-level evidence is retained rather than merged, so a caller can show why
    one month succeeded and another did not, and so a month never silently
    inherits another month's per-asset maximum.
    """

    mode: TargetMode
    months: tuple[MonthPlan, ...]
    cycle_resolution: CycleResolution | None = None
    seed: int = 0
    allocator_version: str = "1.0.0"
    #: Assets whose committed visits exceed their cycle obligation. Surfaced
    #: rather than silently adding further visits.
    over_covered: tuple[Evidence, ...] = ()
    #: Every committed visit inventoried from the cycle, in date order. These are
    #: reported so a caller can reconcile the whole cycle without re-deriving the
    #: inventory, and they are what the cycle-level reconciliation was computed
    #: from.
    committed_visits: tuple[Visit, ...] = ()
    #: Active DTCs the range could not visit at all, each with the reason in words. A
    #: DTC commissioned after the last date of the range is reported here instead of
    #: being counted as a transformer the register covered.
    excluded: Mapping[str, str] = field(default_factory=dict)

    @property
    def status(self) -> FeasibilityStatus:
        """Worst month outcome, so a caller cannot mistake a partial run for success."""

        statuses = [m.result.status for m in self.months]
        if any(s is FeasibilityStatus.INFEASIBLE for s in statuses):
            return FeasibilityStatus.INFEASIBLE
        if any(s is FeasibilityStatus.INDETERMINATE for s in statuses):
            return FeasibilityStatus.INDETERMINATE
        return FeasibilityStatus.FEASIBLE

    @property
    def all_visits(self) -> tuple[Visit, ...]:
        return tuple(v for month in self.months for v in month.visits)

    def month_for(self, month: MonthKey) -> MonthPlan | None:
        for plan in self.months:
            if plan.month == month:
                return plan
        return None


@dataclass(frozen=True)
class _CycleCommitments:
    """The complete fixed-visit inventory for one declared cycle.

    Commitments are reconciled against the cycle *before* any month is allocated,
    because a commitment in a month the loop has not reached yet must still
    discharge its obligation and must still constrain the gap. Reconciling only as
    the loop arrives cannot see it, so an earlier month would allocate a visit it
    did not owe and the later commitment would arrive too late to prevent it.

    Attributes:
        by_month: Committed visits, keyed by the cycle month they fall in.
        effective: Monthly obligations after commitments. A commitment consumes
            one obligation unit exactly once, so for every asset
            ``generated + committed == declared``.
        over_covered: Structured evidence for DTC cycle members whose commitments
            exceed their cycle obligation.
        all_fixed: Every committed visit in the cycle, in date order.
    """

    by_month: Mapping[MonthKey, tuple[Visit, ...]]
    effective: Mapping[MonthKey, Mapping[str, int]]
    over_covered: tuple[Evidence, ...]
    all_fixed: tuple[Visit, ...]

    def blockers_outside(self, month: MonthKey) -> tuple[Visit, ...]:
        """Committed visits in every *other* cycle month.

        These reach the allocator as gap blockers: they consume no capacity and no
        obligation here, they only stop this month selecting a date too close to
        them. Passing exactly the other months keeps each commitment from being
        counted twice, and never supplies a visit this month also holds as its own
        ``fixed`` input.
        """

        return tuple(
            v
            for v in self.all_fixed
            if (v.visit_date.year, v.visit_date.month)
            != (month.year, month.month)
        )


def _inventory_commitments(
    locked: Mapping[MonthKey, Sequence[Visit]],
    cycle: CoverageCycle,
    calendar: Calendar,
    roster: Sequence[Asset],
    config: FeasibilityConfig,
    prior_visits: Iterable[Visit],
) -> tuple[dict[MonthKey, tuple[Visit, ...]], tuple[Visit, ...]]:
    """Validate every commitment in the cycle, selected month or not.

    A commitment is held to the same hard constraints a schedule is, and it is
    held to them whether or not its month will ever be placed, because a month
    omitted from placement is not permission to ignore what is already committed
    there. An invalid commitment raises rather than being treated as a discharge:
    a visit that could not have happened must not silently satisfy an obligation.

    Raises:
        InputError: A month key outside the cycle, a visit filed under a month
            that does not contain its date, or a commitment that fails the shared
            hard-constraint validation.
    """

    by_month: dict[MonthKey, list[Visit]] = {}
    for month, visits in locked.items():
        if not cycle.contains(month):
            raise InputError(
                C_FIXED_MONTH_OUTSIDE_CYCLE,
                (
                    f"fixed visits were supplied for {month.label()}, which is "
                    "outside the declared cycle"
                ),
            )
        for visit in visits:
            if (visit.visit_date.year, visit.visit_date.month) != (
                month.year,
                month.month,
            ):
                raise InputError(
                    C_FIXED_VISIT_WRONG_MONTH,
                    (
                        f"the committed visit for {visit.asset_id} on "
                        f"{visit.visit_date.isoformat()} was supplied under "
                        f"{month.label()}, which does not contain that date"
                    ),
                    asset_id=visit.asset_id,
                )
            by_month.setdefault(month, []).append(visit)

    all_fixed = tuple(
        sorted(
            (v for group in by_month.values() for v in group),
            key=lambda v: (v.visit_date, v.asset_id),
        )
    )

    if all_fixed:
        violations = validate_committed_visits(
            all_fixed, calendar, roster, config, history=tuple(prior_visits)
        )
        if violations:
            first = violations[0]
            raise InputError(
                C_CYCLE_FIXED_VISIT_INVALID,
                (
                    "a committed visit in the cycle is not a valid visit: "
                    f"{first.message}"
                ),
                asset_id=first.asset_id,
                violations=violations,
            )

    return {month: tuple(group) for month, group in by_month.items()}, all_fixed


def _dtc_commitment_counts(
    all_fixed: Sequence[Visit],
    roster: Sequence[Asset],
) -> dict[str, int]:
    """Count committed visits per DTC asset.

    Only DTCs take part in cycle accounting. Area and feeder commitments are
    preserved, consume capacity and constrain gaps, but are never compared with
    ``cycles.visits_per_asset``: a month can hold several area visits legitimately
    and none of that is DTC over-coverage.
    """

    types = {asset.asset_id: asset.asset_type for asset in roster}
    counts: dict[str, int] = {}
    for visit in all_fixed:
        if types.get(visit.asset_id) is AssetType.DTC:
            counts[visit.asset_id] = counts.get(visit.asset_id, 0) + 1
    return counts


def _reconcile_cycle_obligations(
    resolution: CycleResolution,
    cycle: CoverageCycle,
    committed_dtc: Mapping[str, int],
    all_fixed: Sequence[Visit],
) -> tuple[dict[MonthKey, dict[str, int]], tuple[Evidence, ...]]:
    """Reconcile commitments against the declared cycle obligations.

    Each committed DTC visit consumes exactly one obligation unit, so the units
    left for generation plus the commitments equal the declared obligation. A
    commitment is charged to the month it falls in when that month still owes a
    unit, and otherwise to the earliest month that does. Charging own-month first
    stops a December commitment being taken out of November's unit when December
    owes one of its own, while still letting a December commitment satisfy an
    obligation the resolver placed in November when December owes nothing.

    Subtracting a whole cycle's commitment count from every month's obligation is
    the obvious shortcut and is wrong: an asset owing units in two months would be
    reduced twice for one commitment.

    Returns:
        The effective obligations per month, and over-coverage evidence for DTC
        cycle members whose commitments exceed their cycle obligation.
    """

    month_order = [plan.month for plan in resolution.plans]
    remaining: dict[str, dict[MonthKey, int]] = {}
    for plan in resolution.plans:
        for asset_id, count in plan.obligations.items():
            remaining.setdefault(asset_id, {})[plan.month] = count

    for visit in all_fixed:
        per_month = remaining.get(visit.asset_id)
        if per_month is None:
            # Not a DTC cycle member: preserved, but carries no cycle obligation.
            continue
        own = MonthKey(visit.visit_date.year, visit.visit_date.month)
        if per_month.get(own, 0) > 0:
            per_month[own] -= 1
            continue
        for month in month_order:
            if per_month.get(month, 0) > 0:
                per_month[month] -= 1
                break
        # No unit left: the commitment exceeds the obligation. It is preserved
        # and reported as over-coverage rather than silently dropped.

    effective: dict[MonthKey, dict[str, int]] = {month: {} for month in month_order}
    for asset_id, per_month in remaining.items():
        for month, count in per_month.items():
            if count > 0:
                effective[month][asset_id] = count

    over_covered = tuple(
        Evidence(
            constraint=C_CYCLE_OVER_COVERED,
            detail=(
                f"{asset_id} has {count} committed visits in the cycle but its "
                f"cycle obligation is {cycle.visits_per_asset}; every commitment "
                "was preserved and no further visit was added for it"
            ),
            asset_id=asset_id,
            limit=cycle.visits_per_asset,
            actual=count,
        )
        for asset_id, count in sorted(committed_dtc.items())
        if count > cycle.visits_per_asset
    )
    return effective, over_covered


def run_cycle(
    roster: Sequence[Asset],
    calendar: Calendar,
    config: FeasibilityConfig,
    cycle: CoverageCycle,
    seed: int = 0,
    weights: DtcWeights | None = None,
    prior_visits: Iterable[Visit] = (),
    fixed_by_month: Mapping[MonthKey, Sequence[Visit]] | None = None,
    months_to_place: Sequence[MonthKey] | None = None,
    rotation_offset: int = 0,
    attempt_budget: int = 400,
    on_month: Callable[[MonthKey, int, int], None] | None = None,
    on_attempt: Callable[[int, int], None] | None = None,
) -> MultiMonthResult:
    """Place a coverage cycle month by month, keeping gaps across boundaries.

    Every month of the cycle is resolved, so the cycle's obligations are stable
    even when only some months are placed. Only ``months_to_place`` is actually
    scheduled; the rest still appear in ``cycle_resolution``.

    Earlier months' visits are carried into later months as history, so the
    seven-day interval and the fairness rotation survive the month and cycle
    boundary rather than resetting at it.

    Args:
        roster: Full asset roster.
        calendar: Eligible working dates for the whole range.
        config: Capacities and limits.
        cycle: The declared coverage cycle.
        seed: Deterministic seed.
        weights: DTC weight model.
        prior_visits: Visits before the cycle.
        fixed_by_month: Locked visits for specific months.
        months_to_place: Months to schedule; defaults to the whole cycle.
        rotation_offset: Rotation state carried between cycles.
        attempt_budget: Constructive attempts per month.
        on_month: Told which month is being placed and which one of how many, so a
            caller can report progress. A report only: nothing here is read from it.
        on_attempt: Passed to the month's allocator for the same purpose.

    Returns:
        A :class:`MultiMonthResult` whose per-month evidence is retained.
    """

    resolution = resolve_cycle(
        roster,
        cycle,
        weights=weights,
        prior_visits=prior_visits,
        rotation_offset=rotation_offset,
    )

    # A stored or freshly built plan is only trusted once its structure is
    # verified. A corrupted resolution must never reach a successful allocation,
    # so this fails closed rather than allocating from an inconsistent plan.
    problems = verify_cycle(resolution)
    if problems:
        raise InputError(
            CYCLE_RESOLUTION_INVALID,
            "the cycle resolution failed verification: " + "; ".join(problems),
        )

    place = _validated_month_selection(
        months_to_place, resolution.months, cycle
    )
    place_set = set(place)
    locked = dict(fixed_by_month or {})

    # Commitments are inventoried and reconciled against the whole cycle before a
    # single month is allocated. Reconciling inside the loop instead meant a
    # commitment in a later month was invisible while an earlier month was placed,
    # so the earlier month still allocated a visit it no longer owed.
    by_month, all_fixed = _inventory_commitments(
        locked, cycle, calendar, roster, config, prior_visits
    )
    committed_dtc = _dtc_commitment_counts(all_fixed, roster)
    effective, over_covered = _reconcile_cycle_obligations(
        resolution, cycle, committed_dtc, all_fixed
    )
    commitments = _CycleCommitments(
        by_month=by_month,
        effective=effective,
        over_covered=over_covered,
        all_fixed=all_fixed,
    )

    # Only generated visits are carried as history. A commitment in another month
    # reaches the allocator as a gap blocker instead, so it constrains placement in
    # both directions and is never counted twice or supplied as history.
    carried: list[Visit] = list(prior_visits)
    plans: list[MonthPlan] = []

    placed = sum(1 for plan in resolution.plans if plan.month in place_set)
    placed_so_far = 0

    for plan in resolution.plans:
        month_fixed = commitments.by_month.get(plan.month, ())
        if plan.month not in place_set:
            # Not scheduled. Its commitments were reconciled above and remain gap
            # blockers for every month that is placed.
            continue

        obligations = dict(commitments.effective.get(plan.month, {}))
        period = _period_for(plan.month)
        _require_full_month(period, "run_cycle")
        placed_so_far += 1
        if on_month is not None:
            on_month(plan.month, placed_so_far, placed)
        result = allocate_month(
            AllocationRequest(
                period=period,
                calendar=calendar,
                roster=roster,
                config=config,
                cycle_obligations=obligations,
                history=tuple(carried),
                fixed=month_fixed,
                gap_blockers=commitments.blockers_outside(plan.month),
                seed=seed + plan.month_index,
                attempt_budget=attempt_budget,
                from_cycle=True,
                weights=resolution.weights,
                on_attempt=on_attempt,
            )
        )
        plans.append(
            MonthPlan(
                month=plan.month,
                period=period,
                result=result,
                mode=TargetMode.COVERAGE_CYCLE,
                obligations=obligations,
                declared_obligations=dict(plan.obligations),
            )
        )
        if result.status is FeasibilityStatus.FEASIBLE:
            carried.extend(v for v in result.visits if v not in month_fixed)

    return MultiMonthResult(
        mode=TargetMode.COVERAGE_CYCLE,
        months=tuple(plans),
        cycle_resolution=resolution,
        seed=seed,
        over_covered=over_covered,
        committed_visits=all_fixed,
        excluded=resolution.excluded,
    )


def run_fallback(
    roster: Sequence[Asset],
    calendar: Calendar,
    config: FeasibilityConfig,
    months: Sequence[MonthKey],
    area_required: Mapping[MonthKey, Mapping[str, int]] | None = None,
    feeder_required: Mapping[MonthKey, Mapping[str, int]] | None = None,
    dtc_target: int | None = None,
    seed: int = 0,
    weights: DtcWeights | None = None,
    prior_visits: Iterable[Visit] = (),
    fixed_by_month: Mapping[MonthKey, Sequence[Visit]] | None = None,
    rotation_offset: int = 0,
    attempt_budget: int = 400,
    on_month: Callable[[MonthKey, int, int], None] | None = None,
    on_attempt: Callable[[int, int], None] | None = None,
) -> MultiMonthResult:
    """Run the no-cycle fallback across one or more months.

    Each month gets a capacity-bounded DTC target rather than a per-DTC
    obligation, integrated with that month's area and feeder obligations in one
    joint allocation. The result is labelled a capacity-based suggestion, because
    without a declared cycle there is no coverage commitment to satisfy.

    ``on_month`` and ``on_attempt`` are progress reports, passed through unchanged and
    never read: the schedule does not depend on whether anyone is watching.

    Raises:
        InputError: If a month's effective limits or obligations are malformed.
    """

    locked = dict(fixed_by_month or {})
    areas_for = dict(area_required or {})
    feeders_for = dict(feeder_required or {})

    ordered_months = _validated_month_sequence(months)

    carried: list[Visit] = list(prior_visits)
    plans: list[MonthPlan] = []

    for index, month in enumerate(ordered_months):
        period = _period_for(month)
        _require_full_month(period, "run_fallback")
        area_map = dict(areas_for.get(month, {}))
        feeder_map = dict(feeders_for.get(month, {}))
        area_and_feeder_demand = sum(area_map.values()) + sum(feeder_map.values())

        target = fallback_dtc_target(
            roster,
            period,
            calendar,
            config,
            history=tuple(carried),
            weights=weights,
            requested_target=dtc_target,
            area_and_feeder_demand=area_and_feeder_demand,
            rotation_offset=rotation_offset + index,
        )

        required: dict[str, int] = {}
        required.update(area_map)
        required.update(feeder_map)
        for asset_id in target.selected:
            required[asset_id] = 1

        if on_month is not None:
            on_month(month, index + 1, len(ordered_months))
        result = allocate_month(
            AllocationRequest(
                period=period,
                calendar=calendar,
                roster=roster,
                config=config,
                required_visits=required,
                history=tuple(carried),
                fixed=tuple(locked.get(month, ())),
                seed=seed + index,
                attempt_budget=attempt_budget,
                from_cycle=False,
                on_attempt=on_attempt,
            )
        )
        plans.append(
            MonthPlan(
                month=month,
                period=period,
                result=result,
                mode=TargetMode.CAPACITY_SUGGESTION,
                obligations=required,
            )
        )
        if result.status is FeasibilityStatus.FEASIBLE:
            carried.extend(result.visits)

    return MultiMonthResult(
        mode=TargetMode.CAPACITY_SUGGESTION,
        months=tuple(plans),
        seed=seed,
        excluded=_unavailable_dtcs(roster, calendar, ordered_months),
    )


def _unavailable_dtcs(
    roster: Sequence[Asset],
    calendar: Calendar,
    months: Sequence[MonthKey],
) -> dict[str, str]:
    """Active DTCs with no usable date anywhere in the range, and why.

    Reported rather than dropped: a suggestion that silently left a transformer out
    would read as a register that covered it.
    """

    usable: list[date] = []
    for month in months:
        period = _period_for(month)
        usable.extend(_eligible_dates(period, calendar))
    unavailable: dict[str, str] = {}
    for asset in roster:
        if asset.asset_type is not AssetType.DTC or not asset.active:
            continue
        if any(asset.is_available_on(day) for day in usable):
            continue
        unavailable[asset.asset_id] = (
            f"commissioned on {asset.commissioned_on.isoformat()}, after the last "
            f"usable date of {months[-1].label()}"
            if asset.commissioned_on is not None
            else f"has no usable date in {months[-1].label()}"
        )
    return unavailable


def _period_for(month: MonthKey) -> DatePeriod:
    """The full calendar month as a valid ``DatePeriod``."""

    start = date(month.year, month.month, 1)
    if month.month == 12:
        end = date(month.year + 1, 1, 1)
    else:
        end = date(month.year, month.month + 1, 1)
    from datetime import timedelta

    return DatePeriod(start, end - timedelta(days=1))
