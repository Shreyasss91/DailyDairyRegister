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
from typing import Iterable, Mapping, Sequence

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

    The target is the smallest of the requested target, the DTC capacity the
    calendar can actually hold once area and feeder visits have taken their
    share, and the number of active DTCs that can still take a visit this month
    under their own monthly maximum.

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
            feeders, which compete for the same daily capacity.
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
    combined_capacity = len(eligible) * config.combined_daily_cap
    dtc_capacity = max(0, combined_capacity - max(0, area_and_feeder_demand))

    # A DTC with no room left under its monthly maximum cannot be a candidate.
    eligible_dtcs = [
        a for a in dtcs if config.limits_for(a.asset_id).monthly_max >= 1
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
    #: Monthly obligations used, for the reproducibility snapshot.
    obligations: Mapping[str, int] = field(default_factory=dict)

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

    # Committed visits anywhere in the cycle count toward that asset's cycle
    # obligation, whichever month they fall in. Without this, a DTC whose
    # commitment landed in November while the resolver assigned it December would
    # be owed a second visit in December, so the cycle would over-cover it.
    committed_in_cycle: dict[str, int] = {}
    for month, visits in locked.items():
        for visit in visits:
            if cycle.contains(month):
                committed_in_cycle[visit.asset_id] = (
                    committed_in_cycle.get(visit.asset_id, 0) + 1
                )

    over_covered = tuple(
        Evidence(
            constraint=C_CYCLE_OVER_COVERED,
            detail=(
                f"{asset_id} has {committed_in_cycle[asset_id]} committed visits in "
                f"the cycle but its cycle obligation is {cycle.visits_per_asset}; "
                "no further visits were added for it"
            ),
            asset_id=asset_id,
            limit=cycle.visits_per_asset,
            actual=count,
        )
        for asset_id, count in sorted(committed_in_cycle.items())
        if count > cycle.visits_per_asset
    )

    carried: list[Visit] = list(prior_visits)
    plans: list[MonthPlan] = []
    discharged: dict[str, int] = {}

    for plan in resolution.plans:
        month_fixed = tuple(locked.get(plan.month, ()))
        if plan.month not in place_set:
            # Not scheduled, but a commitment here still happened and must
            # constrain later months. Skipping placement is not permission to
            # forget it.
            carried.extend(month_fixed)
            for visit in month_fixed:
                discharged[visit.asset_id] = discharged.get(visit.asset_id, 0) + 1
            continue

        # Reduce this month's obligations by what commitments already discharged
        # for that asset anywhere in the cycle.
        obligations: dict[str, int] = {}
        for asset_id, count in plan.obligations.items():
            already = discharged.get(asset_id, 0)
            remaining = max(0, count - already)
            if remaining > 0:
                obligations[asset_id] = remaining

        period = _period_for(plan.month)
        result = allocate_month(
            AllocationRequest(
                period=period,
                calendar=calendar,
                roster=roster,
                config=config,
                cycle_obligations=obligations,
                history=tuple(carried),
                fixed=month_fixed,
                seed=seed + plan.month_index,
                attempt_budget=attempt_budget,
                from_cycle=True,
                weights=resolution.weights,
            )
        )
        plans.append(
            MonthPlan(
                month=plan.month,
                period=period,
                result=result,
                mode=TargetMode.COVERAGE_CYCLE,
                obligations=obligations,
            )
        )
        for visit in month_fixed:
            discharged[visit.asset_id] = discharged.get(visit.asset_id, 0) + 1

        # Committed visits constrain later months whether or not this month's new
        # allocations succeeded; the commitment exists either way. Only visits
        # that were actually placed are carried as generated allocations.
        carried.extend(month_fixed)
        if result.status is FeasibilityStatus.FEASIBLE:
            carried.extend(v for v in result.visits if v not in month_fixed)

    return MultiMonthResult(
        mode=TargetMode.COVERAGE_CYCLE,
        months=tuple(plans),
        cycle_resolution=resolution,
        seed=seed,
        over_covered=over_covered,
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
) -> MultiMonthResult:
    """Run the no-cycle fallback across one or more months.

    Each month gets a capacity-bounded DTC target rather than a per-DTC
    obligation, integrated with that month's area and feeder obligations in one
    joint allocation. The result is labelled a capacity-based suggestion, because
    without a declared cycle there is no coverage commitment to satisfy.

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
    )


def _period_for(month: MonthKey) -> DatePeriod:
    """The full calendar month as a valid ``DatePeriod``."""

    start = date(month.year, month.month, 1)
    if month.month == 12:
        end = date(month.year + 1, 1, 1)
    else:
        end = date(month.year, month.month + 1, 1)
    from datetime import timedelta

    return DatePeriod(start, end - timedelta(days=1))
