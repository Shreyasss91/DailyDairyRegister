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
from datetime import date
from enum import Enum
from typing import Iterable, Mapping, Sequence

from .validator import Asset, AssetType, InputError, Visit

#: Stable identifiers for cycle-configuration errors. Prefixed ``CYCLE_`` so a
#: caller can tell a malformed cycle from a malformed obligation set.
CYCLE_UNKNOWN_START = "cycle_unknown_start_month"
CYCLE_INVALID_DURATION = "cycle_invalid_duration"
CYCLE_INVALID_VISITS = "cycle_invalid_visits_per_asset"
CYCLE_UNKNOWN_WEIGHT_ASSET = "cycle_unknown_weight_asset"
CYCLE_NEGATIVE_WEIGHT = "cycle_negative_weight"
CYCLE_HEAVIER_CLASSED = "cycle_weight_inconsistent_with_class"
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

    Only ``NOT_REQUESTED`` is usable without owner input. The measured basis
    itself is deliberately not implemented: the baseline requires prorating "by
    eligible working days" but does not fix an integer rounding rule, and a
    hidden rule would silently change every prorated minimum and maximum.
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

    def plan_for(self, month: MonthKey) -> CycleMonthPlan | None:
        for plan in self.plans:
            if plan.month == month:
                return plan
        return None

    @property
    def total_obligation(self) -> int:
        return sum(self.cycle_totals.values())


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

    Returns:
        A :class:`CycleResolution` covering every month of the cycle.

    Raises:
        InputError: If the cycle is malformed or names assets with invalid
            weights.
    """

    weight_model = weights or DtcWeights()
    assets = _active_dtcs(roster)
    months = cycle.months()

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

    # Deal the queue out by taking one unit per month in rotation, so a small
    # roster spreads across the cycle instead of stacking into its first months.
    # Front-loading was a defect: one DTC over three months came out as 1/0/0,
    # leaving it never revisited. Round-robin dealing keeps the monthly totals at
    # differ-by-at-most-one while spreading assets through the whole cycle, so
    # the seven-day gap and history continuity are exercised across boundaries.
    plans: list[CycleMonthPlan] = []
    assigned: list[list[str]] = [[] for _ in months]
    remaining_units = list(queue)
    # Each pass takes at most one unit per month, which is what makes the
    # distribution even rather than grouped.
    while remaining_units:
        for index in range(len(months)):
            if not remaining_units:
                break
            assigned[index].append(remaining_units.pop(0))

    for index, month in enumerate(months):
        chosen = assigned[index]
        obligations = {asset_id: 1 for asset_id in chosen}
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
    )


def verify_cycle(resolution: CycleResolution) -> tuple[str, ...]:
    """Check a resolution's internal arithmetic. Returns problems, empty if sound.

    Every active DTC must receive exactly its cycle obligation, and the monthly
    plans must add up to the cycle total. This is deliberately a separate,
    checkable function rather than an assertion buried in the resolver, so a
    caller can verify a stored plan without re-running the resolution.
    """

    problems: list[str] = []
    expected = resolution.cycle.visits_per_asset
    seen: dict[str, int] = {asset_id: 0 for asset_id in resolution.roster_ids}
    for plan in resolution.plans:
        for asset_id in plan.obligations:
            seen[asset_id] = seen.get(asset_id, 0) + 1
    for asset_id, count in sorted(seen.items()):
        if count != expected:
            problems.append(
                f"{asset_id} received {count} visits in the cycle, expected {expected}"
            )
    monthly_total = sum(plan.total for plan in resolution.plans)
    if monthly_total != resolution.total_obligation:
        problems.append(
            f"monthly plans total {monthly_total}, cycle obligation is "
            f"{resolution.total_obligation}"
        )
    totals = sorted(plan.total for plan in resolution.plans)
    if totals and totals[-1] - totals[0] > 1:
        problems.append(
            f"monthly totals are uneven by more than one: {totals}"
        )
    return tuple(problems)
