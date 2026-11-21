"""Deterministic DTC coverage-cycle resolution and monthly period limits.

A DTC coverage cycle is a repeating window in which every active DTC must be
inspected once. The validator assesses exactly one calendar month at a time, so
cycle obligations must be resolved into monthly portions *before* assessment.

Two ideas are kept strictly apart here, because conflating them is how a cycle
silently becomes a guarantee that every DTC is visited every month:

*   **Obligation** - how many visits each asset owes in a given month. In cycle
    mode this is an equal per-DTC share; weights never change it.
*   **Priority** - the order in which space is granted when a month cannot take
    the whole cycle. This is weight-informed and rotation-aware.

Nothing in this module performs date-level placement or duplicate validation. It
only decides *how many* visits each asset owes in each month, and is the single
source of truth for that decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Iterable, Mapping, Sequence

from .validator import Asset, AssetType, Calendar, InputError, Visit

#: Stable identifiers for cycle-configuration errors. Prefixed ``CYCLE_`` so a
#: caller can tell a malformed cycle from a malformed obligation set.
CYCLE_UNKNOWN_START = "cycle_unknown_start_month"
CYCLE_INVALID_DURATION = "cycle_invalid_duration"
CYCLE_INVALID_VISITS = "cycle_invalid_visits_per_asset"
CYCLE_UNKNOWN_WEIGHT_ASSET = "cycle_unknown_weight_asset"
CYCLE_NEGATIVE_WEIGHT = "cycle_negative_weight"
CYCLE_HEAVIER_CLASSED = "cycle_weight_inconsistent_with_class"
#: No month of the declared cycle can hold a visit to an asset, which the resolver
#: excludes before it deals anything.
CYCLE_MONTH_UNAVAILABLE = "cycle_month_unavailable"
CYCLE_UNRESOLVED_PARTIAL_MONTH = "cycle_unresolved_partial_month_limits"

DEFAULT_DEPT_MULTIPLIER = 1.00
DEFAULT_SELF_DTC_MULTIPLIER = 0.20


class DtcClass(str, Enum):
    """The recorded DTC scheduling class.

    This is a *classification within* :attr:`AssetType.DTC`, not a fourth visit
    category. DEPT DTCs outrank the MSB/OWN group, which share ``SELF_DTC``.

    It is deliberately a separate type rather than extra members on
    :class:`~dailyworkreg.domain.validator.AssetType`: the validator's category
    enum decides which visits share the combined and area daily capacities, and
    adding scheduling classes to it would change capacity accounting. Original
    classification is carried here so it is never inferred from a display name.
    """

    DEPARTMENT = "DEPT"
    SELF_DTC = "SELF_DTC"

    @property
    def multiplier_default(self) -> float:
        return (
            DEFAULT_DEPT_MULTIPLIER
            if self is DtcClass.DEPARTMENT
            else DEFAULT_SELF_DTC_MULTIPLIER
        )


class ProratingBasis(str, Enum):
    """How a partial month's limits are derived from eligible working days.

    Only ``NOT_REQUESTED`` and caller-supplied limits are usable today. The owner has
    since fixed the integer rule (requirements §23, ``round_half_up``, implemented in
    :mod:`dailyworkreg.domain.prorating`), but no partial-month scheduling path exists
    yet, so this enum still describes how a caller's limits were obtained rather than
    promising that they were derived here.

    A coverage-cycle *obligation* is never subject to this basis. It is declared over a
    cycle, not per month, so no rounding of a part-month can reduce it.
    """

    #: The period is a full calendar month, so configured limits apply unchanged.
    NOT_REQUESTED = "not_requested"
    #: Effective prorated limits were supplied by the caller.
    SUPPLIED = "supplied"


@dataclass(frozen=True)
class MonthKey:
    """A calendar month, used as a cycle key."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")

    @classmethod
    def of(cls, day: date) -> "MonthKey":
        return cls(day.year, day.month)

    def next(self) -> "MonthKey":
        return MonthKey(self.year + 1, 1) if self.month == 12 else MonthKey(
            self.year, self.month + 1
        )

    def ordinal(self) -> int:
        """Months since year 0, for deterministic ordering and arithmetic."""

        return self.year * 12 + (self.month - 1)

    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def __lt__(self, other: "MonthKey") -> bool:  # ordering helper
        return self.ordinal() < other.ordinal()


def months_between(start: MonthKey, count: int) -> tuple[MonthKey, ...]:
    """Return ``count`` consecutive month keys starting at ``start``."""

    if count < 1:
        raise InputError(CYCLE_INVALID_DURATION, "a cycle must span at least one month")
    out: list[MonthKey] = [start]
    for _ in range(count - 1):
        out.append(out[-1].next())
    return tuple(out)


@dataclass(frozen=True)
class DtcWeights:
    """Approved DTC weight model: ``kVA * class_multiplier * adjustment``.

    Classification is taken from the supplied :class:`DtcClass` per asset, never
    inferred from a display string, and the class multiplier is applied by
    :meth:`weight_for` rather than baked into the stored kVA.
    """

    kva: Mapping[str, float] = field(default_factory=dict)
    individual_adjustment: Mapping[str, float] = field(default_factory=dict)
    classification: Mapping[str, DtcClass] = field(default_factory=dict)
    department_multiplier: float = DEFAULT_DEPT_MULTIPLIER
    self_dtc_multiplier: float = DEFAULT_SELF_DTC_MULTIPLIER

    def class_of(self, asset: Asset) -> DtcClass:
        """The recorded class of ``asset``, defaulting to the lower class.

        Defaulting to ``SELF_DTC`` rather than ``DEPARTMENT`` means a missing
        classification under-weights an asset instead of over-weighting it, so an
        omission can never promote a DTC above a genuinely departmental one.
        """

        return self.classification.get(asset.asset_id, DtcClass.SELF_DTC)

    def weight_for(self, asset: Asset) -> float:
        """The scheduling weight of ``asset``.

        A DEPT DTC outweighs a SELF_DTC of equal capacity, which is the recorded
        policy. Weight is a tie-breaker and rotation input, never an obligation
        count.
        """

        capacity = float(self.kva.get(asset.asset_id, 1.0))
        adjustment = float(self.individual_adjustment.get(asset.asset_id, 1.0))
        if capacity < 0:
            raise InputError(
                CYCLE_NEGATIVE_WEIGHT,
                f"kVA for {asset.asset_id!r} must not be negative",
                asset.asset_id,
            )
        if adjustment < 0:
            raise InputError(
                CYCLE_NEGATIVE_WEIGHT,
                f"individual adjustment for {asset.asset_id!r} must not be negative",
                asset.asset_id,
            )
        multiplier = (
            self.department_multiplier
            if self.class_of(asset) is DtcClass.DEPARTMENT
            else self.self_dtc_multiplier
        )
        return capacity * multiplier * adjustment


@dataclass(frozen=True)
class CoverageCycle:
    """A declared DTC coverage cycle.

    ``visits_per_asset`` is the number of inspections each active DTC owes across
    the whole cycle. It is uniform by design: a heavier DTC is scheduled earlier,
    not more often.
    """

    start_month: MonthKey
    duration_months: int
    visits_per_asset: int = 1

    def __post_init__(self) -> None:
        if self.duration_months < 1:
            raise InputError(
                CYCLE_INVALID_DURATION, "a cycle must span at least one month"
            )
        if self.visits_per_asset < 1:
            raise InputError(
                CYCLE_INVALID_VISITS,
                "visits_per_asset must be at least 1; a cycle that owes nothing "
                "is not a coverage cycle",
            )

    def months(self) -> tuple[MonthKey, ...]:
        return months_between(self.start_month, self.duration_months)

    def ordinal_index(self, month: MonthKey) -> int:
        """Which month of this cycle ``month`` is, or ``-1`` when outside it."""

        offset = month.ordinal() - self.start_month.ordinal()
        if 0 <= offset < self.duration_months:
            return offset
        return -1

    def contains(self, month: MonthKey) -> bool:
        return self.ordinal_index(month) >= 0


@dataclass(frozen=True)
class CycleMonthPlan:
    """The obligations one month carries, with everything needed to explain it."""

    month: MonthKey
    #: Asset id to the visits owed in this month. Equal per asset in cycle mode.
    obligations: Mapping[str, int]
    #: Total visits this month owes across all assets.
    total: int
    #: Which month of the cycle this is, zero-based.
    month_index: int
    #: Assets scheduled this month rather than a later one, in priority order.
    scheduled: tuple[str, ...] = ()


@dataclass(frozen=True)
class CycleResolution:
    """A complete resolved cycle, month by month.

    The whole cycle is resolved at once so its totals can be checked against the
    cycle obligation. Only some months may later be placed on dates.
    """

    cycle: CoverageCycle
    months: tuple[MonthKey, ...]
    plans: tuple[CycleMonthPlan, ...]
    #: Every active DTC the cycle covers, in deterministic order.
    roster_ids: tuple[str, ...]
    #: Asset id to the total visits it owes across the whole cycle.
    cycle_totals: Mapping[str, int]
    #: Weights used for priority ordering, for the reproducibility snapshot.
    weights: Mapping[str, float] = field(default_factory=dict)
    #: Prior-visit counts used to break rotation ties fairly.
    prior_counts: Mapping[str, int] = field(default_factory=dict)
    #: Prior last-visit dates, used to prefer the least recently seen.
    prior_last: Mapping[str, date] = field(default_factory=dict)
    #: Active DTCs this cycle cannot cover, each with the reason in words. A DTC
    #: commissioned after the cycle's last usable date is not silently dropped: it is
    #: recorded here, kept out of the plan, and reported to the operator, because a
    #: cycle that quietly forgot an asset would look like complete coverage.
    excluded: Mapping[str, str] = field(default_factory=dict)
    #: How many units had to be placed outside their month's even share because the
    #: asset does not exist in the months that still had room. Zero for a cycle whose
    #: assets carry no commissioning dates, where the distribution is exactly even.
    availability_forced: int = 0
    #: Months holding no working date at all, which no visit can exist in. They are
    #: recorded on the resolution rather than looked up again, so a stored plan is
    #: verified against the same facts it was built from and owes nothing in them.
    closed_months: tuple[MonthKey, ...] = ()

    def plan_for(self, month: MonthKey) -> CycleMonthPlan | None:
        for plan in self.plans:
            if plan.month == month:
                return plan
        return None

    @property
    def total_obligation(self) -> int:
        return sum(self.cycle_totals.values())


def _last_day_of(month: MonthKey) -> date:
    """The last calendar date of ``month``."""

    following = month.next()
    return date(following.year, following.month, 1) - timedelta(days=1)


def _month_usable(asset: Asset, month: MonthKey) -> bool:
    """Whether ``month`` holds at least one date the asset may be visited on.

    Reserve depends on master data rather than the calendar, so a cycle can be
    resolved without one: a month is usable when its last calendar date is later
    than the commissioning date. An asset with no recorded date is usable
    everywhere.
    """

    if asset.commissioned_on is None:
        return True
    return asset.commissioned_on < _last_day_of(month)


def _active_dtcs(roster: Sequence[Asset]) -> tuple[Asset, ...]:
    """Active DTCs in a stable order. Areas and feeders are not cycle members."""

    return tuple(
        sorted(
            (a for a in roster if a.asset_type is AssetType.DTC and a.active),
            key=lambda a: a.asset_id,
        )
    )


def _rotation_order(
    assets: Sequence[Asset],
    weights: DtcWeights,
    prior_counts: Mapping[str, int],
    prior_last: Mapping[str, date],
    rotation_offset: int,
) -> tuple[str, ...]:
    """Order assets so the remainder falls to those most deserving of it.

    The ordering is a fairness objective, not an obligation change. It sorts by,
    in order:

    1. fewest visits already received (prior history), so a previously
       over-served asset is not served first again;
    2. earliest last visit, so the least recently visited is preferred;
    3. heaviest weight, so a heavier DTC is inspected sooner;
    4. asset id, so the result is fully determined.

    ``rotation_offset`` shifts the starting point of the equally-ranked group so
    consecutive cycles do not always favour the same ids.
    """

    def sort_key(asset: Asset):
        last = prior_last.get(asset.asset_id)
        # A never-visited asset sorts before any visited one.
        last_rank = 0 if last is None else 1
        last_ordinal = 0 if last is None else last.toordinal()
        return (
            int(prior_counts.get(asset.asset_id, 0)),
            last_rank,
            last_ordinal,
            -weights.weight_for(asset),
            asset.asset_id,
        )

    ordered = sorted(assets, key=sort_key)
    if not ordered:
        return ()
    if rotation_offset <= 0:
        return tuple(a.asset_id for a in ordered)

    # Rotate within the group sharing the same fairness rank, so rotation cannot
    # override the fairness ordering itself.
    head_rank = sort_key(ordered[0])[:-2]
    group = [a for a in ordered if sort_key(a)[:-2] == head_rank]
    tail = [a for a in ordered if sort_key(a)[:-2] != head_rank]
    shift = rotation_offset % len(group) if group else 0
    rotated = group[shift:] + group[:shift]
    return tuple(a.asset_id for a in rotated + tail)


def resolve_cycle(
    roster: Sequence[Asset],
    cycle: CoverageCycle,
    weights: DtcWeights | None = None,
    prior_visits: Iterable[Visit] = (),
    rotation_offset: int = 0,
    calendar: Calendar | None = None,
) -> CycleResolution:
    """Resolve ``cycle`` into a stable per-month obligation plan.

    Every active DTC owes the same number of visits across the cycle. The cycle's
    total obligation is distributed over its months so that the monthly totals
    differ by at most one, and the asset set assigned to each month is chosen by
    the weighted-fair rotation above.

    For the recorded reference case of 113 active DTCs owing one visit each over
    three months this yields monthly totals of 38, 38 and 37, and every DTC
    appears exactly once across the cycle. That is a consequence of the general
    rule, not a special case.

    Args:
        roster: The asset roster, including areas and feeders, which are not
            cycle members.
        cycle: The declared coverage cycle.
        weights: Weight model used only to order priority.
        prior_visits: Visits before the cycle, used for fairness rotation.
        rotation_offset: Deterministic rotation state carried between cycles.
        calendar: The working dates of the whole cycle, when the caller has them. A
            month holding no working date at all cannot carry a visit for anybody -
            an office that was on leave from end to end did no field work in it - so
            that month's share is dealt to the months that can hold work instead of
            being left as an obligation no schedule could ever discharge. Left out,
            every month of the cycle is assumed able to hold work, which is what a
            caller that has not projected a calendar is entitled to assume.

    Returns:
        A :class:`CycleResolution` covering every month of the cycle.

    Raises:
        InputError: If the cycle is malformed or names assets with invalid
            weights.
    """

    weight_model = weights or DtcWeights()
    months = cycle.months()
    can_hold_work = _months_that_can_hold_work(months, calendar)
    serving = tuple(index for index, ok in enumerate(can_hold_work) if ok)
    # A cycle whose every month is closed cannot discharge its obligation anywhere, and
    # quietly redistributing it to nothing would report a cycle it never made. Such a cycle
    # keeps the plain distribution, so its month comes back as an empty month with the reason.
    placeable_capacity = can_hold_work if serving else tuple(True for _ in months)

    # An asset commissioned after a month has no usable date in it, so the cycle
    # cannot serve it there. An asset commissioned after the whole cycle cannot be
    # served by this cycle at all: it is excluded with a stated reason rather than
    # silently dropped, and the plan's totals are computed from the assets that
    # remain so its obligations still add up.
    usable_months: dict[str, tuple[bool, ...]] = {}
    excluded: dict[str, str] = {}
    served: list[Asset] = []
    for asset in _active_dtcs(roster):
        flags = tuple(_month_usable(asset, month) for month in months)
        if not any(flags):
            excluded[asset.asset_id] = (
                f"commissioned on {asset.commissioned_on.isoformat()}, after the "
                f"last usable date of {months[-1].label()}"
                if asset.commissioned_on is not None
                else f"has no usable date in {months[-1].label()}"
            )
            continue
        usable_months[asset.asset_id] = flags
        served.append(asset)
    assets = tuple(served)

    prior_counts: dict[str, int] = {}
    prior_last: dict[str, date] = {}
    for visit in prior_visits:
        prior_counts[visit.asset_id] = prior_counts.get(visit.asset_id, 0) + 1
        known = prior_last.get(visit.asset_id)
        if known is None or visit.visit_date > known:
            prior_last[visit.asset_id] = visit.visit_date

    # Total cycle obligation and its base monthly share. The remainder is spread
    # one visit at a time over the earliest months, which is what makes 113 over
    # three months come out as 38, 38, 37 rather than 37, 38, 38.
    total_obligation = len(assets) * cycle.visits_per_asset
    base, remainder = divmod(total_obligation, cycle.duration_months)

    # Build the cycle-wide list of (asset, which_repeat) units in priority order,
    # then deal them out month by month. Dealing from one priority-ordered queue
    # is what guarantees each asset appears exactly `visits_per_asset` times
    # across the cycle and no single asset is double-served in one month.
    queue: list[str] = []
    for repeat in range(cycle.visits_per_asset):
        queue.extend(
            _rotation_order(
                assets,
                weight_model,
                prior_counts,
                prior_last,
                rotation_offset + repeat,
            )
        )

    # Deal the queue out round-robin so a small roster spreads across the cycle
    # instead of stacking into its first months. Front-loading was a defect: one
    # DTC over three months came out as 1/0/0, leaving it never revisited.
    #
    # Units are counted per asset, not per key. Storing ``{asset_id: 1}`` from a
    # list of repeats collapsed an asset owing several visits in one month down to
    # a single visit: the plan reported ``total == 2`` while its obligations held
    # ``{asset: 1}``, so a caller consumed one visit and the cycle still looked
    # feasible. Obligations therefore carry the actual multiplicity.
    plans: list[CycleMonthPlan] = []
    assigned: list[list[str]] = [[] for _ in months]
    remaining_units = list(queue)
    # Each month takes at most its declared share, and a unit is only dealt to a
    # month in which its asset is usable. The pointer moves on from wherever the last
    # unit landed, so the deal stays round-robin and a month still holds one visit per
    # turn: this is what makes a small roster spread across the cycle instead of
    # stacking into its first months. With no commissioning dates the shares and the
    # order are exactly what a plain round-robin produces.
    # A month nobody can be visited in takes no share: dealing to it would create an
    # obligation no schedule could discharge, which is how a fully-leave month used to
    # come back infeasible instead of coming back with the leave sentence on every date.
    # A cycle whose every month is closed is left on the plain division, so the month is
    # reported as an empty month rather than the cycle quietly owing nothing anywhere.
    shares = [0] * len(months)
    if serving:
        share_base, share_remainder = divmod(total_obligation, len(serving))
        for position, index in enumerate(serving):
            shares[index] = share_base + (1 if position < share_remainder else 0)
    else:
        shares = [base + (1 if index < remainder else 0) for index in range(len(months))]
    pointer = 0
    forced = 0
    for unit in remaining_units:
        placed_at: int | None = None
        for step in range(len(months)):
            candidate = (pointer + step) % len(months)
            if shares[candidate] <= 0:
                continue
            if not usable_months[unit][candidate]:
                continue
            placed_at = candidate
            break
        if placed_at is None:
            # No month with a share left can hold it, because the asset does not exist
            # in those months yet. Availability is master data and the distribution is
            # only a fairness objective, so the cycle yields and places the visit
            # where it can exist.
            for step in range(len(months)):
                candidate = (pointer + step) % len(months)
                if usable_months[unit][candidate] and placeable_capacity[candidate]:
                    placed_at = candidate
                    break
            if placed_at is None:
                # Unreachable: an asset with no usable month was excluded above.
                raise InputError(
                    CYCLE_MONTH_UNAVAILABLE,
                    f"no month of the cycle can hold a visit to {unit}",
                )
            forced += 1
        assigned[placed_at].append(unit)
        shares[placed_at] -= 1
        pointer = (placed_at + 1) % len(months)

    for index, month in enumerate(months):
        chosen = assigned[index]
        # Count repeated occurrences rather than collapsing them to one.
        obligations: dict[str, int] = {}
        for asset_id in chosen:
            obligations[asset_id] = obligations.get(asset_id, 0) + 1
        plans.append(
            CycleMonthPlan(
                month=month,
                obligations=obligations,
                total=len(chosen),
                month_index=index,
                scheduled=tuple(chosen),
            )
        )

    cycle_totals: dict[str, int] = {a.asset_id: cycle.visits_per_asset for a in assets}

    return CycleResolution(
        cycle=cycle,
        months=months,
        plans=tuple(plans),
        roster_ids=tuple(a.asset_id for a in assets),
        cycle_totals=cycle_totals,
        weights={a.asset_id: weight_model.weight_for(a) for a in assets},
        prior_counts=prior_counts,
        prior_last=prior_last,
        excluded=excluded,
        availability_forced=forced,
        closed_months=tuple(
            month
            for index, month in enumerate(months)
            if not placeable_capacity[index]
        ),
    )


def _months_that_can_hold_work(
    months: Sequence[MonthKey], calendar: Calendar | None
) -> tuple[bool, ...]:
    """Which of the cycle's months hold at least one working date.

    A month with no working date is a month the office did not work: every date is a weekly
    off, a General Holiday, a leave date or an override. No visit can exist in it, so the
    cycle cannot owe one there. Without a calendar every month is assumed to hold work, which
    keeps the plain distribution for a caller that has not projected one.
    """

    if calendar is None:
        return tuple(True for _ in months)
    working = calendar.working_days
    return tuple(_month_bounds_hold_work(month, working) for month in months)


def _month_bounds_hold_work(month: MonthKey, working: frozenset[date]) -> bool:
    for day in working:
        if (day.year, day.month) == (month.year, month.month):
            return True
    return False


def verify_cycle(resolution: CycleResolution) -> tuple[str, ...]:
    """Independently verify a stored cycle resolution. Returns problems, empty if sound.

    This checks the *structure* of a resolution rather than re-running it, so a
    plan read back from storage can be trusted before it drives an allocation.
    It verifies values and multiplicities, not merely the presence of asset ids:
    counting ``obligations.keys()`` treats every key as one visit, which is
    exactly how a month owing two visits to one asset silently became one.

    The invariants checked are:

    *   the resolution's months equal the declared cycle's months, in order;
    *   month indices are ``0..n-1`` and consistent with the month key;
    *   every planned asset belongs to the cycle roster;
    *   every obligation value is a positive integer;
    *   each month's obligations sum to its declared ``total``;
    *   each asset's total across months equals its ``cycle_totals`` entry;
    *   cycle totals equal ``visits_per_asset`` for every roster asset;
    *   ``scheduled`` and ``obligations`` describe the same assignments and
       multiplicities;
    *   monthly totals differ by at most one, the resolver's distribution
       contract, widened only by the units availability forced out of their month;
    *   an asset the cycle could not cover is excluded and appears in no month, and
       an asset that does appear is not excluded.
    """

    problems: list[str] = []
    expected = resolution.cycle.visits_per_asset
    roster = set(resolution.roster_ids)

    # 0. The excluded set and the covered set must not overlap, or a month could
    #    carry obligations for an asset the resolution says it cannot serve.
    for asset_id in sorted(resolution.excluded):
        if asset_id in roster:
            problems.append(
                f"{asset_id} is both excluded from the cycle and in its roster"
            )
        if asset_id in resolution.cycle_totals:
            problems.append(
                f"{asset_id} is excluded from the cycle but carries a cycle total"
            )

    # 1. Months must match the declared cycle exactly, in order.
    declared = resolution.cycle.months()
    if resolution.months != declared:
        problems.append(
            f"resolution months {[m.label() for m in resolution.months]} do not "
            f"match the declared cycle {[m.label() for m in declared]}"
        )
    if len(resolution.plans) != len(declared):
        problems.append(
            f"resolution has {len(resolution.plans)} month plans for a "
            f"{len(declared)}-month cycle"
        )

    per_asset: dict[str, int] = {asset_id: 0 for asset_id in roster}

    for position, plan in enumerate(resolution.plans):
        # 2. Month index and key consistency.
        if plan.month_index != position:
            problems.append(
                f"plan {position} reports month_index {plan.month_index}"
            )
        if position < len(declared) and plan.month != declared[position]:
            problems.append(
                f"plan {position} is {plan.month.label()}, expected "
                f"{declared[position].label()}"
            )
        if resolution.cycle.ordinal_index(plan.month) < 0:
            problems.append(
                f"plan month {plan.month.label()} is outside the declared cycle"
            )

        # 3. Obligation values must be positive integers, and every asset must
        #    belong to the cycle roster.
        for asset_id, count in plan.obligations.items():
            if asset_id not in roster:
                problems.append(
                    f"{plan.month.label()} obligates {asset_id!r}, which is not in "
                    "the cycle roster"
                )
                continue
            if not isinstance(count, int) or isinstance(count, bool):
                problems.append(
                    f"{plan.month.label()} obligation for {asset_id} is "
                    f"{count!r}, not an integer count"
                )
                continue
            if count <= 0:
                problems.append(
                    f"{plan.month.label()} obligation for {asset_id} is {count}, "
                    "expected a positive count"
                )
                continue
            per_asset[asset_id] = per_asset.get(asset_id, 0) + count

        # 4. The month's declared total must equal its obligation values.
        declared_total = sum(
            c for c in plan.obligations.values() if isinstance(c, int)
        )
        if declared_total != plan.total:
            problems.append(
                f"{plan.month.label()} obligations sum to {declared_total} but "
                f"total is {plan.total}"
            )

        # 5. `scheduled` must match `obligations` exactly, multiplicities included.
        scheduled_counts: dict[str, int] = {}
        for asset_id in plan.scheduled:
            scheduled_counts[asset_id] = scheduled_counts.get(asset_id, 0) + 1
        if scheduled_counts != dict(plan.obligations):
            problems.append(
                f"{plan.month.label()} scheduled assignments "
                f"{sorted(scheduled_counts.items())} disagree with obligations "
                f"{sorted(plan.obligations.items())}"
            )

    # 6. Per-asset cycle totals must match, and match visits_per_asset.
    for asset_id in sorted(roster):
        count = per_asset.get(asset_id, 0)
        declared_cycle = resolution.cycle_totals.get(asset_id)
        if declared_cycle is None:
            problems.append(f"{asset_id} has no cycle_totals entry")
        elif count != declared_cycle:
            problems.append(
                f"{asset_id} receives {count} visits across the cycle but "
                f"cycle_totals says {declared_cycle}"
            )
        if count != expected:
            problems.append(
                f"{asset_id} receives {count} visits across the cycle, expected "
                f"{expected}"
            )

    for asset_id in sorted(set(resolution.cycle_totals) - roster):
        problems.append(
            f"cycle_totals names {asset_id!r}, which is not in the cycle roster"
        )

    # 7. The distribution contract: monthly totals differ by at most one. An asset
    #    commissioned part-way through the cycle cannot use the months before it
    #    existed, so a unit forced out of its month widens the spread by exactly one;
    #    the count travels with the resolution rather than being assumed away.
    closed = set(resolution.closed_months)
    for plan in resolution.plans:
        if plan.month in closed and plan.total:
            problems.append(
                f"{plan.month.label()} holds no working date, yet owes "
                f"{plan.total} visit(s)"
            )
    totals = sorted(plan.total for plan in resolution.plans if plan.month not in closed)
    allowed_spread = 1 + resolution.availability_forced
    if totals and totals[-1] - totals[0] > allowed_spread:
        problems.append(f"monthly totals are uneven by more than one: {totals}")

    # 8. The whole resolution must carry exactly the cycle obligation.
    if sum(per_asset.values()) != resolution.total_obligation:
        problems.append(
            f"plans carry {sum(per_asset.values())} visits but the cycle "
            f"obligation is {resolution.total_obligation}"
        )

    return tuple(problems)
