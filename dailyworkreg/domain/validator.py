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

# Input-contract errors. A malformed obligation set is not a mathematical
# infeasibility; it is a caller error. These identifiers appear in
# :class:`InputError` and, where an obligation is representable but impossible,
# in :class:`Violation` as well.
C_DUPLICATE_ASSET_ID = "duplicate_asset_id"
C_UNKNOWN_OBLIGATION_ASSET = "unknown_obligation_asset"
C_UNKNOWN_LIMIT_OVERRIDE = "unknown_limit_override_asset"
C_NEGATIVE_OBLIGATION = "negative_obligation"
C_OBLIGATION_ON_INACTIVE_ASSET = "obligation_on_inactive_asset"
C_MINIMUM_ON_INACTIVE_ASSET = "minimum_on_inactive_asset"
C_PERIOD_NOT_SINGLE_MONTH = "period_not_single_calendar_month"
C_FIXED_VISIT_OUTSIDE_PERIOD = "fixed_visit_outside_period"
C_FIXED_VISIT_NON_WORKING = "fixed_visit_not_on_working_date"
C_FIXED_VISIT_INACTIVE_ASSET = "fixed_visit_on_inactive_asset"
C_CYCLE_EXCEEDS_MONTHLY = "cycle_obligation_exceeds_monthly_portion"


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
    """Inclusive date range under validation.

    Assessment is defined for one calendar month. Field-visit limits in the
    approved policy are per-calendar-month limits (with proration for partial
    months), and a coverage-cycle obligation is resolved by the caller into the
    portion owed in the month being assessed. A period that spans two calendar
    months would silently treat two months as one, which would apply a single
    month's per-asset maximum to both and hide genuine capacity overruns, so it
    is rejected rather than accepted with a different meaning.

    A partial month is permitted: the period may start after the first day or
    end before the last day of its month, provided both endpoints fall in the
    same calendar month.
    """

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("period end must not precede period start")
        if (self.start.year, self.start.month) != (self.end.year, self.end.month):
            raise ValueError(
                "period must fall within a single calendar month; a cycle or "
                "multi-month obligation must be resolved into its monthly portion "
                "before assessment (got %s to %s)"
                % (self.start.isoformat(), self.end.isoformat())
            )

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def month_key(self) -> tuple[int, int]:
        """The ``(year, month)`` this period belongs to."""

        return (self.start.year, self.start.month)

    @property
    def is_full_month(self) -> bool:
        """Whether the period covers its entire calendar month."""

        import calendar as _calendar

        last_day = _calendar.monthrange(self.start.year, self.start.month)[1]
        return self.start.day == 1 and self.end.day == last_day


@dataclass(frozen=True)
class ObligationSpec:
    """The authoritative obligation set for one calendar month of assessment.

    Both assessment paths consume this one specification, so proposed-schedule
    validation and feasibility assessment can never disagree about what is owed.
    It is built once by :func:`resolve_obligations`, which validates the caller's
    raw inputs and raises :class:`InputError` on anything malformed.

    ``per_asset`` holds the number of visits this month owes each asset. It is
    derived from the larger of the configured monthly minimum, an explicit
    required count and a resolved cycle portion. A monthly *maximum* never
    contributes to it, because a maximum is an upper bound and not a demand.
    """

    #: Asset id to the number of visits owed in this month.
    per_asset: Mapping[str, int]
    #: Assets whose cycle obligation exceeds this month's available portion.
    cycle_exceeds_monthly: Mapping[str, int] = field(default_factory=dict)

    def owed_for(self, asset_id: str) -> int:
        """Visits owed for ``asset_id`` this month, zero when none are owed."""

        return self.per_asset.get(asset_id, 0)

    @property
    def total_owed(self) -> int:
        return sum(self.per_asset.values())

    def assets_with_obligations(self) -> tuple[str, ...]:
        """Asset ids with a positive obligation, in deterministic order."""

        return tuple(
            sorted(a for a, count in self.per_asset.items() if count > 0)
        )


class InputError(ValueError):
    """Raised when an obligation input set is malformed.

    This distinguishes a caller error from a mathematically infeasible but
    otherwise valid obligation set. A malformed set is never reported as
    ``FEASIBLE`` and is never reported as ``INFEASIBLE``, because neither verdict
    would be meaningful: the input does not describe a schedule problem at all.

    Attributes:
        constraint: A stable identifier from the ``C_*`` constants.
        detail: A readable explanation naming what is wrong.
        asset_id: The offending asset, when the error is asset-specific.
    """

    def __init__(self, constraint: str, detail: str, asset_id: str | None = None):
        self.constraint = constraint
        self.detail = detail
        self.asset_id = asset_id
        super().__init__(f"{constraint}: {detail}")


def _index_roster(roster: Sequence[Asset]) -> dict[str, Asset]:
    """Build the asset index, rejecting duplicate asset ids.

    A duplicate id makes every later lookup ambiguous, so it is an input error
    rather than something resolved by taking the last entry.
    """

    index: dict[str, Asset] = {}
    for asset in roster:
        if asset.asset_id in index:
            raise InputError(
                C_DUPLICATE_ASSET_ID,
                f"asset id {asset.asset_id!r} appears more than once in the roster",
                asset.asset_id,
            )
        index[asset.asset_id] = asset
    return index


def resolve_obligations(
    roster: Sequence[Asset],
    config: FeasibilityConfig,
    required_visits: Mapping[str, int] | None = None,
    cycle_obligations: Mapping[str, int] | None = None,
) -> ObligationSpec:
    """Validate caller obligations and resolve them into one specification.

    Every malformed input is rejected explicitly. Nothing is silently dropped or
    silently clamped, because a dropped obligation turns an unsatisfiable month
    into an apparently feasible one.

    Args:
        roster: The full roster, including inactive assets.
        config: Effective configuration, including per-asset limit overrides.
        required_visits: Explicit required visit counts for this month.
        cycle_obligations: Cycle obligations already resolved by the caller into
            the portion owed in this month.

    Returns:
        An :class:`ObligationSpec` describing what this month owes.

    Raises:
        InputError: If the roster is ambiguous, a mapping names an unknown asset,
            a count is negative, or a positive obligation or minimum targets an
            inactive asset.
    """

    index = _index_roster(roster)
    required_visits = dict(required_visits or {})
    cycle_obligations = dict(cycle_obligations or {})

    # Asset-specific limit overrides must name known assets, otherwise the
    # caller believes a limit applies that the engine never sees.
    for asset_id in sorted(config.asset_limits):
        if asset_id not in index:
            raise InputError(
                C_UNKNOWN_LIMIT_OVERRIDE,
                f"limit override names unknown asset {asset_id!r}",
                asset_id,
            )

    for label, mapping in (
        ("required_visits", required_visits),
        ("cycle_obligations", cycle_obligations),
    ):
        for asset_id in sorted(mapping):
            count = mapping[asset_id]
            if asset_id not in index:
                raise InputError(
                    C_UNKNOWN_OBLIGATION_ASSET,
                    f"{label} names unknown asset {asset_id!r}",
                    asset_id,
                )
            # ``bool`` subclasses ``int`` in Python, so an explicit exclusion is
            # required or ``True`` would be accepted as a count of one. A
            # boolean is a mistaken input, never a visit count.
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise InputError(
                    C_NEGATIVE_OBLIGATION,
                    f"{label} for {asset_id!r} must be a non-negative integer, "
                    f"got {count!r}",
                    asset_id,
                )

    per_asset: dict[str, int] = {}
    cycle_exceeds: dict[str, int] = {}
    for asset_id in sorted(index):
        asset = index[asset_id]
        limits = config.limits_for(asset_id)
        required = required_visits.get(asset_id, 0)
        cycle = cycle_obligations.get(asset_id, 0)

        # A positive obligation against an inactive asset cannot be discharged.
        # It is rejected explicitly rather than skipped, because skipping it
        # would report a month as feasible while omitting owed work.
        if not asset.active:
            if required > 0 or cycle > 0:
                raise InputError(
                    C_OBLIGATION_ON_INACTIVE_ASSET,
                    f"asset {asset_id!r} is inactive but carries a positive "
                    f"obligation (required={required}, cycle={cycle})",
                    asset_id,
                )
            if limits.monthly_min > 0:
                raise InputError(
                    C_MINIMUM_ON_INACTIVE_ASSET,
                    f"asset {asset_id!r} is inactive but has a configured "
                    f"monthly minimum of {limits.monthly_min}",
                    asset_id,
                )
            continue

        owed = max(limits.monthly_min, required, cycle)
        if owed > 0:
            per_asset[asset_id] = owed

        # A cycle obligation larger than this month's monthly maximum cannot be
        # delivered in this month alone. Record it so the caller can see that the
        # cycle portion must be spread, rather than having it silently capped.
        if cycle > limits.monthly_max:
            cycle_exceeds[asset_id] = cycle

    return ObligationSpec(
        per_asset=per_asset, cycle_exceeds_monthly=cycle_exceeds
    )


@dataclass(frozen=True)
class ProposalContext:
    """Everything needed to validate a proposed schedule.

    ``history`` holds visits before the period, used only for revisit-gap
    continuity. ``fixed`` holds visits already committed *inside* the period, for
    example locked dates during regeneration. Fixed visits consume daily
    capacity, count toward per-asset monthly limits and discharge part of the
    declared obligations, and they constrain new placements through the revisit
    gap. They are not history and must not be supplied as such.
    """

    period: DatePeriod
    calendar: Calendar
    roster: Sequence[Asset]
    config: FeasibilityConfig
    proposed: Sequence[Visit]
    history: Sequence[Visit] = ()
    fixed: Sequence[Visit] = ()
    required_visits: Mapping[str, int] = field(default_factory=dict)
    cycle_obligations: Mapping[str, int] = field(default_factory=dict)

    def asset_index(self) -> dict[str, Asset]:
        return _index_roster(self.roster)

    def obligations(self) -> ObligationSpec:
        """Resolve the obligation specification, validating the inputs."""

        return resolve_obligations(
            self.roster,
            self.config,
            self.required_visits,
            self.cycle_obligations,
        )


@dataclass(frozen=True)
class ObligationContext:
    """Everything needed to assess whether obligations can be scheduled.

    ``fixed`` carries visits already committed inside the period. They are
    honoured by the assessment: they consume capacity, discharge obligations, and
    constrain new placements. They are returned alongside newly allocated visits
    so the witness covers the whole month.
    """

    period: DatePeriod
    calendar: Calendar
    roster: Sequence[Asset]
    config: FeasibilityConfig
    history: Sequence[Visit] = ()
    fixed: Sequence[Visit] = ()
    #: Declared per-asset visit obligation for this month. Only minima and
    #: cycle obligations belong here; a monthly maximum is never an obligation.
    required_visits: Mapping[str, int] = field(default_factory=dict)
    #: Coverage-cycle obligations, already resolved by the caller into the
    #: portion owed in the month being assessed. A cycle total spanning several
    #: months is never supplied here whole.
    cycle_obligations: Mapping[str, int] = field(default_factory=dict)

    def asset_index(self) -> dict[str, Asset]:
        return _index_roster(self.roster)

    def obligations(self) -> ObligationSpec:
        """Resolve the obligation specification, validating the inputs."""

        return resolve_obligations(
            self.roster,
            self.config,
            self.required_visits,
            self.cycle_obligations,
        )

    def obligation_for(self, asset_id: str) -> int:
        """Return the required visit count for ``asset_id``.

        The larger of the configured minimum, an explicit required count and a
        resolved cycle portion applies; a monthly maximum is never treated as a
        requirement.
        """

        return self.obligations().owed_for(asset_id)


# --- result model -----------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """One detected constraint violation.

    The structured fields are the source of truth; ``message`` is a readable
    rendering for display. A caller must never need to parse ``message`` to learn
    which assets or dates are involved.

    Attributes:
        constraint: Stable identifier from the ``C_*`` constants.
        message: Readable explanation.
        asset_id: The asset the violation is about, when it is about one asset.
        asset_type: That asset's type, when known.
        visit_date: The date the violation is anchored to, when applicable.
        offending_dates: Every date participating in the violation, ordered.
            For a revisit-gap violation this is both conflicting visits; for a
            monthly-maximum violation it is all visits counted in the month.
            For a daily-cap violation it is the single affected date.
        contributing_assets: For a daily-cap violation, the assets that share the
            date, ordered by asset id, each paired with its type. Empty when not
            applicable.
        limit: The limit that was exceeded, when numeric.
        actual: The observed value, when numeric.
    """

    constraint: str
    message: str
    asset_id: str | None = None
    asset_type: AssetType | None = None
    visit_date: date | None = None
    offending_dates: tuple[date, ...] = ()
    contributing_assets: tuple[tuple[str, AssetType], ...] = ()
    limit: int | None = None
    actual: int | None = None

    @property
    def conflicting_dates(self) -> tuple[date, ...]:
        """Alias for :attr:`offending_dates`, retained for readability."""

        return self.offending_dates

    def sort_key(self) -> tuple:
        return (
            self.visit_date or date.min,
            self.asset_id or "",
            self.constraint,
        )


@dataclass(frozen=True)
class Evidence:
    """A machine-readable fact supporting a feasibility verdict.

    The structured fields describe the fact without requiring a caller to parse
    ``detail``. A violation of the committed visits is reported with the same
    fields :class:`Violation` carries, so an ``INFEASIBLE`` verdict caused by
    invalid fixed commitments is as diagnosable as a rejected schedule.
    """

    constraint: str
    detail: str
    asset_id: str | None = None
    asset_type: "AssetType | None" = None
    visit_date: date | None = None
    offending_dates: tuple[date, ...] = ()
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
        contributing = tuple(
            sorted(
                (
                    (visit.asset_id, roster[visit.asset_id].asset_type)
                    for visit in bucket
                    if visit.asset_id in roster
                ),
                key=lambda pair: pair[0],
            )
        )
        area_ids = [asset_id for asset_id, kind in contributing
                    if kind is AssetType.AREA]
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
                    offending_dates=(day,),
                    contributing_assets=tuple(
                        pair for pair in contributing if pair[1] is AssetType.AREA
                    ),
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
                    offending_dates=(day,),
                    contributing_assets=contributing,
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
                        offending_dates=(earlier.visit_date, later.visit_date),
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
    """Check the per-asset monthly maximum for the single month in ``period``.

    Only visits whose date falls inside the period count toward the monthly
    total, so a gap that spans the month boundary neither resets nor inflates the
    count. The maximum caps visits; it never creates an obligation.

    The period is constrained to one calendar month by :class:`DatePeriod`, so
    the count below is genuinely a monthly count and not a sum over several
    months.
    """

    violations: list[Violation] = []
    for asset_id, bucket in sorted(grouped.items()):
        limits = config.limits_for(asset_id)
        in_period = [v for v in bucket if period.contains(v.visit_date)]
        if len(in_period) > limits.monthly_max:
            asset = roster.get(asset_id)
            dates = tuple(v.visit_date for v in in_period)
            violations.append(
                Violation(
                    constraint=C_ASSET_MONTHLY_MAX,
                    message=(
                        f"{asset_id} has {len(in_period)} visits in "
                        f"{period.start.year}-{period.start.month:02d}; "
                        f"the monthly maximum is {limits.monthly_max}"
                    ),
                    asset_id=asset_id,
                    asset_type=asset.asset_type if asset else None,
                    visit_date=dates[-1],
                    offending_dates=dates,
                    limit=limits.monthly_max,
                    actual=len(in_period),
                )
            )
    return violations


def _obligation_violations(
    grouped: Mapping[str, Sequence[Visit]],
    period: DatePeriod,
    roster: Mapping[str, Asset],
    config: FeasibilityConfig,
    obligations: ObligationSpec,
) -> list[Violation]:
    """Check every declared obligation against the visits actually recorded.

    One authoritative specification drives this check, so proposed-schedule
    validation in obligation mode and feasibility assessment can never disagree
    about what is owed. Visits counted here are those inside the period, which
    includes fixed and newly proposed visits alike.

    A minimum above the effective maximum, and a cycle portion above the
    effective maximum, are configuration contradictions and are reported as such
    rather than as missing visits.
    """

    violations: list[Violation] = []
    for asset_id in sorted(roster):
        asset = roster[asset_id]
        limits = config.limits_for(asset_id)
        owed = obligations.owed_for(asset_id)

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

        cycle = obligations.cycle_exceeds_monthly.get(asset_id)
        if cycle is not None:
            violations.append(
                Violation(
                    constraint=C_CYCLE_EXCEEDS_MONTHLY,
                    message=(
                        f"{asset_id} carries a cycle obligation of {cycle} but the "
                        f"monthly maximum is {limits.monthly_max}; the cycle portion "
                        f"must be spread across months"
                    ),
                    asset_id=asset_id,
                    asset_type=asset.asset_type,
                    limit=limits.monthly_max,
                    actual=cycle,
                )
            )

        if owed <= 0:
            continue

        in_period = [
            v.visit_date
            for v in grouped.get(asset_id, ())
            if period.contains(v.visit_date)
        ]
        if len(in_period) < owed:
            violations.append(
                Violation(
                    constraint=C_ASSET_MINIMUM,
                    message=(
                        f"{asset_id} has {len(in_period)} visits in the month; "
                        f"{owed} are owed"
                    ),
                    asset_id=asset_id,
                    asset_type=asset.asset_type,
                    offending_dates=tuple(sorted(in_period)),
                    limit=owed,
                    actual=len(in_period),
                )
            )
    return violations


def _fixed_visit_violations(
    fixed: Sequence[Visit],
    period: DatePeriod,
    calendar: Calendar,
    roster: Mapping[str, Asset],
) -> list[Violation]:
    """Check that committed in-period visits are themselves well formed.

    A fixed visit is already committed, so it is never dropped. If it falls
    outside the period or off an eligible working date it is reported, because a
    schedule built on top of an invalid commitment cannot be correct.
    """

    violations: list[Violation] = []
    for visit in fixed:
        asset = roster.get(visit.asset_id)
        if not period.contains(visit.visit_date):
            violations.append(
                Violation(
                    constraint=C_FIXED_VISIT_OUTSIDE_PERIOD,
                    message=(
                        f"committed visit to {visit.asset_id} on "
                        f"{visit.visit_date.isoformat()} falls outside the period"
                    ),
                    asset_id=visit.asset_id,
                    asset_type=asset.asset_type if asset else None,
                    visit_date=visit.visit_date,
                    offending_dates=(visit.visit_date,),
                )
            )
        if asset is None:
            violations.append(
                Violation(
                    constraint=C_UNKNOWN_ASSET,
                    message=f"committed visit refers to unknown asset {visit.asset_id}",
                    asset_id=visit.asset_id,
                    visit_date=visit.visit_date,
                    offending_dates=(visit.visit_date,),
                )
            )
            continue
        if not calendar.is_working(visit.visit_date):
            violations.append(
                Violation(
                    constraint=C_FIXED_VISIT_NON_WORKING,
                    message=(
                        f"committed visit to {visit.asset_id} on "
                        f"{visit.visit_date.isoformat()} is not on an eligible working date"
                    ),
                    asset_id=visit.asset_id,
                    asset_type=asset.asset_type,
                    visit_date=visit.visit_date,
                    offending_dates=(visit.visit_date,),
                )
            )
        # An inactive asset cannot carry a committed visit merely because it is
        # still present in the roster. The commitment must not be honoured, and
        # it must not be silently accepted as satisfied work either.
        if not asset.active:
            violations.append(
                Violation(
                    constraint=C_FIXED_VISIT_INACTIVE_ASSET,
                    message=(
                        f"committed visit to {visit.asset_id} on "
                        f"{visit.visit_date.isoformat()} refers to an inactive asset"
                    ),
                    asset_id=visit.asset_id,
                    asset_type=asset.asset_type,
                    visit_date=visit.visit_date,
                    offending_dates=(visit.visit_date,),
                )
            )
    return violations


def _hard_constraint_violations(
    fixed: Sequence[Visit],
    proposed: Sequence[Visit],
    period: DatePeriod,
    calendar: Calendar,
    roster: Mapping[str, Asset],
    config: FeasibilityConfig,
    history: Sequence[Visit] = (),
) -> list[Violation]:
    """Check the hard constraints that any complete schedule must satisfy.

    This is the one place the hard constraints are evaluated. Both public paths
    use it: :func:`validate_schedule` to explain why a concrete schedule is
    rejected, and :func:`assess_feasibility` as preflight, so an assessment can
    never call a month feasible on a schedule this function rejects.

    Committed visits are checked exactly as strictly as proposed ones. A fixed
    visit is immutable input, so a violation here means no valid complete
    schedule can exist around it; it is reported, never dropped.

    Args:
        fixed: Committed in-period visits.
        proposed: Visits proposed in the same period.
        period: The single calendar month under assessment.
        calendar: Eligible working dates.
        roster: Assets by id.
        config: Capacities and per-asset limits.
        history: Prior-period visits, contributing visit-gap continuity only.

    Returns:
        Violations ordered deterministically. Empty when the schedule is sound.
    """

    violations: list[Violation] = []

    # 1. Committed in-period visits must themselves be well formed.
    violations.extend(_fixed_visit_violations(fixed, period, calendar, roster))

    # 2. Individual membership, identity, activity and working-date eligibility.
    for visit in proposed:
        if not period.contains(visit.visit_date):
            violations.append(
                Violation(
                    constraint=C_OUTSIDE_PERIOD,
                    message=(
                        f"visit to {visit.asset_id} on {visit.visit_date.isoformat()} "
                        f"falls outside {period.start.isoformat()} to "
                        f"{period.end.isoformat()}"
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
        if not calendar.is_working(visit.visit_date):
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

    # 3. Daily capacity. Different categories share one date within the
    #    combined cap, so both caps are evaluated over the actual date buckets.
    #    Committed visits consume the same capacity as proposed ones.
    all_in_period = tuple(fixed) + tuple(proposed)
    violations.extend(_daily_cap_violations(all_in_period, roster, config))

    # 4. Per-asset checks. History is merged so that the revisit interval is
    #    enforced across month and cycle boundaries without a reset, and fixed
    #    visits constrain proposed ones through the same gap.
    merged = visits_by_asset(tuple(history) + tuple(fixed) + tuple(proposed))
    violations.extend(_revisit_violations(merged, roster, config))
    violations.extend(_monthly_count_violations(merged, period, roster, config))

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
        context: Period, calendar, roster, configuration, proposed visits,
            prior history and committed in-period visits.
        mode: ``SCHEDULE_ONLY`` checks the hard limits of the proposal.
            ``OBLIGATIONS`` additionally requires every resolved obligation to
            be met. Obligations are resolved from the same specification that
            feasibility assessment consumes.

    Returns:
        A :class:`ValidationResult` whose violations are ordered
        deterministically by date, asset id and constraint identifier.

    Raises:
        InputError: If the roster or obligation inputs are malformed. A malformed
            input is neither a valid nor an invalid schedule; it is a caller
            error and is reported as such.
    """

    roster = context.asset_index()
    obligations = context.obligations()

    # Every hard constraint is evaluated by the shared path, which the
    # feasibility assessment also uses as preflight. The two cannot disagree
    # about what a valid schedule is.
    violations: list[Violation] = list(
        _hard_constraint_violations(
            fixed=context.fixed,
            proposed=context.proposed,
            period=context.period,
            calendar=context.calendar,
            roster=roster,
            config=context.config,
            history=context.history,
        )
    )

    # Obligations, when the caller requires them. The same resolved
    # specification drives this check as drives feasibility assessment.
    if mode is ValidationMode.OBLIGATIONS:
        merged = visits_by_asset(
            tuple(context.history) + tuple(context.fixed) + tuple(context.proposed)
        )
        violations.extend(
            _obligation_violations(
                merged, context.period, roster, context.config, obligations
            )
        )

    all_in_period = tuple(context.fixed) + tuple(context.proposed)
    days_used: dict[date, int] = {}
    for day, bucket in visits_by_date(all_in_period).items():
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
    fixed: Sequence[Visit] = (),
) -> dict[str, tuple[date, ...]] | None:
    """Search for a joint assignment that satisfies both daily caps.

    The search is a complete depth-first backtrack over each asset's enumerated
    placements. Because it is complete, an exhausted search is a genuine
    infeasibility proof for the bounded input, and any returned mapping is a
    witness that can be validated independently. The node budget makes a
    pathological input stop with ``None`` instead of running unbounded; the
    caller distinguishes that case from a proven infeasibility.

    Committed in-period visits seed the per-date usage counters, so they consume
    capacity before any new placement is attempted. Without that seeding a newly
    allocated visit could be placed on a date already full.

    Assets are visited most-constrained-first (fewest available placements) so
    that contradictions surface early and the witness is found quickly when one
    exists.

    Args:
        placements: Per-asset candidate placements, already gap-filtered.
        eligible: Eligible working dates, used to size the usage counters.
        config: Effective caps.
        roster: Asset index used to identify area visits.
        node_budget: Single-element list holding the remaining node allowance.
        fixed: Committed in-period visits that consume capacity first.
    """

    asset_ids = sorted(
        placements, key=lambda asset_id: (len(placements[asset_id]), asset_id)
    )
    usage_total: dict[date, int] = {d: 0 for d in eligible}
    usage_area: dict[date, int] = {d: 0 for d in eligible}
    for visit in fixed:
        day = visit.visit_date
        if day not in usage_total:
            continue
        usage_total[day] += 1
        asset = roster.get(visit.asset_id)
        if asset is not None and asset.asset_type is AssetType.AREA:
            usage_area[day] += 1
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
        context: Period, calendar, roster, configuration, history, committed
            in-period visits and the declared obligations.

    Returns:
        A :class:`FeasibilityResult`. A ``FEASIBLE`` result carries a witness
        covering both committed and newly allocated visits.

    Raises:
        InputError: If the roster or obligation inputs are malformed. A malformed
            input is never reported as ``FEASIBLE`` or ``INFEASIBLE``, because
            neither verdict would describe the actual situation.
    """

    roster = context.asset_index()
    obligations = context.obligations()
    eligible = _eligible_dates(context.period, context.calendar)
    working_days = len(eligible)

    # Preflight. Committed visits are immutable input, so if they already break a
    # hard constraint then no valid complete schedule can exist around them and
    # the month is infeasible whatever else remains to be placed. This check runs
    # before every path that can return FEASIBLE, including the path where the
    # obligations are already discharged and nothing remains to allocate.
    #
    # It is the same evaluation validate_schedule performs, so an assessment can
    # never call a month feasible on a schedule that path rejects.
    preflight = _hard_constraint_violations(
        fixed=context.fixed,
        proposed=(),
        period=context.period,
        calendar=context.calendar,
        roster=roster,
        config=context.config,
        history=context.history,
    )
    if preflight:
        return FeasibilityResult(
            status=FeasibilityStatus.INFEASIBLE,
            evidence=tuple(
                Evidence(
                    constraint=violation.constraint,
                    detail=violation.message,
                    asset_id=violation.asset_id,
                    asset_type=violation.asset_type,
                    visit_date=violation.visit_date,
                    offending_dates=violation.offending_dates,
                    limit=violation.limit,
                    actual=violation.actual,
                )
                for violation in preflight
            ),
        )

    # Committed in-period visits discharge part of each obligation and consume
    # capacity. They are subtracted from what remains to be placed rather than
    # being treated as ordinary prior history.
    fixed_by_asset = visits_by_asset(context.fixed)

    required: dict[str, int] = {}
    for asset_id in obligations.assets_with_obligations():
        owed = obligations.owed_for(asset_id)
        already = sum(
            1
            for visit in fixed_by_asset.get(asset_id, ())
            if context.period.contains(visit.visit_date)
        )
        remaining = max(0, owed - already)
        if remaining > 0:
            required[asset_id] = remaining

    total_required = sum(required.values())
    area_required = sum(
        count
        for asset_id, count in required.items()
        if roster[asset_id].asset_type is AssetType.AREA
    )

    if not required:
        # Nothing remains to place. The preflight above has already established
        # that the committed visits form a valid schedule, so the witness is the
        # complete month and passes independent validation.
        return FeasibilityResult(
            status=FeasibilityStatus.FEASIBLE,
            evidence=(),
            witness=tuple(
                sorted(
                    (v for v in context.fixed if context.period.contains(v.visit_date)),
                    key=lambda v: (v.visit_date, v.asset_id),
                )
            ),
        )


    evidence: list[Evidence] = []

    # An obligation above the effective per-asset maximum cannot be met. The
    # maximum is an upper bound, so this is a configuration contradiction, not
    # a demand that the maximum be reached. The comparison uses the total the
    # month would carry, which includes committed visits, because the maximum
    # applies to the month as a whole and not only to what remains to be placed.
    for asset_id in sorted(required):
        limits = context.config.limits_for(asset_id)
        total_for_asset = obligations.owed_for(asset_id)
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
        if total_for_asset > limits.monthly_max:
            evidence.append(
                Evidence(
                    constraint=C_ASSET_MONTHLY_MAX,
                    detail=(
                        f"{asset_id} owes {total_for_asset} visits but the "
                        f"monthly maximum is {limits.monthly_max}"
                    ),
                    limit=limits.monthly_max,
                    actual=total_for_asset,
                )
            )
            return FeasibilityResult(
                status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
            )

    # Aggregate necessary conditions. These are necessary only; a failure is a
    # proof, while passing proves nothing about date-level feasibility. Committed
    # visits already occupy slots, so both checks use the month total.
    fixed_area = sum(
        1
        for visit in context.fixed
        if context.period.contains(visit.visit_date)
        and visit.asset_id in roster
        and roster[visit.asset_id].asset_type is AssetType.AREA
    )
    fixed_total = sum(
        1 for visit in context.fixed if context.period.contains(visit.visit_date)
    )
    area_demand = area_required + fixed_area
    total_demand = total_required + fixed_total

    if area_demand > context.config.area_daily_cap * working_days:
        evidence.append(
            Evidence(
                constraint=C_AGG_AREA_CAPACITY,
                detail=(
                    f"{area_demand} area visits are required in the month "
                    f"({area_required} to place plus {fixed_area} committed), "
                    f"exceeding the area limit of "
                    f"{context.config.area_daily_cap} per working day across "
                    f"{working_days} eligible dates"
                ),
                limit=context.config.area_daily_cap * working_days,
                actual=area_demand,
            )
        )
        return FeasibilityResult(
            status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
        )
    if total_demand > context.config.combined_daily_cap * working_days:
        evidence.append(
            Evidence(
                constraint=C_AGG_COMBINED_CAPACITY,
                detail=(
                    f"{total_demand} visits are required in the month "
                    f"({total_required} to place plus {fixed_total} committed), "
                    f"exceeding the combined limit of "
                    f"{context.config.combined_daily_cap} per working day across "
                    f"{working_days} eligible dates"
                ),
                limit=context.config.combined_daily_cap * working_days,
                actual=total_demand,
            )
        )
        return FeasibilityResult(
            status=FeasibilityStatus.INFEASIBLE, evidence=tuple(evidence)
        )

    node_budget = [context.config.search_node_limit]
    history_by_asset = visits_by_asset(context.history)

    # Per-asset placements, honouring history, committed in-period visits and the
    # calendar-day gap. Committed visits already occupy their dates, so a
    # candidate date must also respect the gap to them.
    placements: dict[str, Sequence[tuple[date, ...]]] = {}
    for asset_id in sorted(required):
        limits = context.config.limits_for(asset_id)
        blocker_dates = [
            v.visit_date
            for v in history_by_asset.get(asset_id, ())
        ] + [
            v.visit_date
            for v in fixed_by_asset.get(asset_id, ())
            if context.period.contains(v.visit_date)
        ]
        candidates = _candidate_dates(
            eligible, blocker_dates, limits.revisit_gap_days
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
        placements, eligible, context.config, roster, node_budget, context.fixed
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
                [Visit(asset_id=asset_id, visit_date=day)
                 for asset_id, days in packed.items()
                 for day in days]
                + [v for v in context.fixed if context.period.contains(v.visit_date)]
            ),
            key=lambda visit: (visit.visit_date, visit.asset_id),
        )
    )
    return FeasibilityResult(
        status=FeasibilityStatus.FEASIBLE, evidence=tuple(evidence), witness=witness
    )
