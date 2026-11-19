"""Building a ``DraftRequest`` from canonical master data.

Every value here is read from SQLite and passed through unchanged. The only thing
this module decides is which stored field answers which request field, and it refuses
rather than guessing when a field has no source.

Weight is the one calculation, and it is not new: the DTC formula
``kVA * class_multiplier * adjustment`` comes from the domain's own ``DtcWeights``,
which is what keeps the application from growing a second copy of it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from ..domain import wording
from ..domain.activities import C_ACTIVITY_DAILY_RANGE_INVALID, ActivitySpec
from ..domain.cycle import CoverageCycle, DtcClass, DtcWeights, MonthKey
from ..domain.validator import (
    DEFAULT_AREA_DAILY_CAP,
    DEFAULT_COMBINED_DAILY_CAP,
    Asset,
    AssetLimits,
    AssetType,
    FeasibilityConfig,
    Visit,
)
from ..domain.wording import DEFAULT_ENTRY_LANGUAGE
from ..infrastructure.sqlite import masters
from .calendar_projection import CalendarProjection, project_calendar
from .draft_generation import (
    C_ENTRY_LANGUAGE_INVALID,
    C_RANGE_REVERSED,
    AssetDisplay,
    DraftMode,
    DraftRequest,
)
from .readiness import Readiness, assess_readiness

C_MONTHS_NOT_SELECTED = "generation_months_not_selected"
C_ROSTER_EMPTY = "generation_roster_empty"
C_LIMITS_INCONSISTENT = "generation_limits_inconsistent"
C_ACTIVITIES_PER_DAY_INVALID = "activities_per_day_invalid"
C_DAILY_VISIT_CAP_INVALID = "daily_visit_cap_invalid"

#: What the operator's own form starts with: the office asked for at least three
#: activities a working day. It is a form default rather than a silent platform rule,
#: because a register is a record of work, not a template applied behind a request.
DEFAULT_ACTIVITIES_PER_DAY = 3

#: The form's own starting ceiling. Five is not a policy - the office chose "at least
#: three, sometimes more", and a range needs an upper end to draw against - and it is
#: editable, so a heavier day is the operator's decision rather than this constant's.
DEFAULT_ACTIVITIES_PER_DAY_MAX = 5


class GenerationInputError(ValueError):
    """The office's stored data cannot supply a generation request as asked."""

    def __init__(self, constraint: str, detail: str, asset_id: str | None = None):
        self.constraint = constraint
        self.detail = detail
        self.asset_id = asset_id
        super().__init__(f"{constraint}: {detail}")


@dataclass(frozen=True)
class GenerationSpec:
    """What the operator asked for, before any master data is read."""

    start: date
    end: date
    mode: DraftMode
    seed: int
    attempt_budget: int = 400
    rotation_offset: int = 0
    #: Coverage-cycle mode only.
    cycle_start_month: MonthKey | None = None
    cycle_duration_months: int = 1
    cycle_visits_per_asset: int = 1
    #: Regular activities recorded on every eligible working day. ``None`` means the
    #: caller did not ask for activities, and it stays that: a request that says nothing
    #: about activities generates a visits-only draft. The daily count is a decision, so
    #: the operator's own form supplies it and its box starts at
    #: :data:`DEFAULT_ACTIVITIES_PER_DAY`.
    #:
    #: ``activities_per_day`` is the exact count an earlier caller could ask for, and it
    #: says the same thing as a range whose two ends are equal. ``activities_per_day_min``
    #: and ``_max`` state the range directly: the floor a day never falls below and the
    #: ceiling it never exceeds. A range wins when both are present, because one request
    #: cannot mean two counts.
    activities_per_day: int | None = None
    activities_per_day_min: int | None = None
    activities_per_day_max: int | None = None
    #: Which language the register is written in. Left unset it is the platform default,
    #: because a register has always been filed in both languages and a caller that predates
    #: the choice must keep the document it used to get.
    entry_language: str = DEFAULT_ENTRY_LANGUAGE
    #: Capacity-fallback mode only.
    dtc_target: int | None = None
    area_required: dict[MonthKey, dict[str, int]] = field(default_factory=dict)
    feeder_required: dict[MonthKey, dict[str, int]] = field(default_factory=dict)
    #: Carried from an earlier run or entered by the operator. Never invented here.
    fixed_by_month: dict[MonthKey, tuple[Visit, ...]] = field(default_factory=dict)
    prior_history: tuple[Visit, ...] = ()
    #: Field-visit caps per working day. ``None`` keeps the platform default rather than
    #: a number this layer invented, and the cap is stated in the request because it is
    #: the one number that decides whether a month's obligations fit at all: the office's
    #: own monthly maximums are what they are, and a cap too low refuses the month.
    combined_daily_cap: int | None = None
    area_daily_cap: int | None = None

    def months(self) -> tuple[MonthKey, ...]:
        keys: list[MonthKey] = []
        cursor = MonthKey(self.start.year, self.start.month)
        last = MonthKey(self.end.year, self.end.month)
        while cursor.ordinal() <= last.ordinal():
            keys.append(cursor)
            cursor = cursor.next()
        return tuple(keys)


@dataclass(frozen=True)
class PreparedGeneration:
    """Everything a generation needs, plus what is still missing from the masters."""

    spec: GenerationSpec
    request: DraftRequest
    readiness: Readiness
    projection: CalendarProjection
    master_version: int


def _cycle_weight_model(connection: sqlite3.Connection) -> DtcWeights:
    """The domain's weight model, fed from the stored masters.

    A DTC with no recorded capacity contributes no capacity rather than an invented
    one, and its class still applies, so an unbilled record is ranked on class alone.

    Areas and feeders are entered as capacity with the department multiplier, which is
    the model's neutral value, because the formula is shaped for DTCs and the
    application is not entitled to a second one. A feeder's capacity is the aggregate
    of its active department DTCs per §9, scaled by its stored manual multiplier; an
    area carries its stored weight. A feeder with no department DTC keeps a
    capacity of 1.0, so it is neither dropped nor promoted.
    """

    dtcs = masters.list_dtcs(connection)
    kva: dict[str, float] = {dtc.dtc_id: dtc.kva or 0.0 for dtc in dtcs}
    adjustment: dict[str, float] = {
        dtc.dtc_id: dtc.individual_adjustment for dtc in dtcs
    }
    classification: dict[str, DtcClass] = {
        dtc.dtc_id: (
            DtcClass.DEPARTMENT if dtc.dtc_class == "DEPT" else DtcClass.SELF_DTC
        )
        for dtc in dtcs
    }
    department_capacity: dict[str, float] = {}
    for dtc in dtcs:
        if dtc.canonical_feeder_id and dtc.dtc_class == "DEPT" and dtc.active:
            department_capacity[dtc.canonical_feeder_id] = department_capacity.get(
                dtc.canonical_feeder_id, 0.0
            ) + (dtc.kva or 0.0)

    for feeder in masters.list_feeders(connection):
        kva[feeder.feeder_id] = department_capacity.get(feeder.feeder_id, 1.0) or 1.0
        adjustment[feeder.feeder_id] = feeder.manual_multiplier
        classification[feeder.feeder_id] = DtcClass.DEPARTMENT

    for area in masters.list_areas(connection):
        kva[area.area_id] = area.weight
        classification[area.area_id] = DtcClass.DEPARTMENT

    return DtcWeights(
        kva=kva, individual_adjustment=adjustment, classification=classification
    )


def _asset_type_for(dtc_class: str) -> AssetType:
    """DEPT and SELF_DTC share one capacity category; the class affects weight only."""

    return AssetType.DTC


def _roster(
    connection: sqlite3.Connection,
) -> tuple[tuple[Asset, ...], dict[str, AssetDisplay], DtcWeights]:
    """Active assets, their display names and the domain weight model."""

    assets: list[Asset] = []
    display: dict[str, AssetDisplay] = {}
    model = _cycle_weight_model(connection)

    for area in masters.list_areas(connection):
        if not area.active:
            continue
        asset = Asset(area.area_id, AssetType.AREA)
        assets.append(asset)
        display[area.area_id] = AssetDisplay(
            asset_id=area.area_id,
            name_en=area.name_en or area.name_kn,
            name_kn=area.name_kn,
        )

    for feeder in masters.list_feeders(connection):
        if not feeder.active:
            continue
        asset = Asset(feeder.feeder_id, AssetType.FEEDER)
        assets.append(asset)
        display[feeder.feeder_id] = AssetDisplay(
            asset_id=feeder.feeder_id, name_en=feeder.name
        )

    for dtc in masters.list_dtcs(connection):
        if not dtc.active:
            continue
        assets.append(Asset(dtc.dtc_id, _asset_type_for(dtc.dtc_class)))
        display[dtc.dtc_id] = AssetDisplay(asset_id=dtc.dtc_id, name_en=dtc.name)

    return tuple(assets), display, model


def _config(
    connection: sqlite3.Connection,
    roster: tuple[Asset, ...],
    spec: GenerationSpec,
) -> FeasibilityConfig:
    """Configured caps and per-asset limits, read from the masters that hold them.

    The limits map is restricted to the assets actually in the roster. A stored limit
    for an asset that is no longer active would otherwise name an asset the validator
    has never heard of, and the every-other generation would be refused for a reason
    that has nothing to do with the request.

    The two daily visit caps come from the request, not from the masters: they are the
    operator's decision about how much field work one working day holds. Until this was
    stated in the request they were platform constants a register could be refused by
    without the operator being able to see, let alone change, them.
    """

    combined_cap, area_cap = _daily_visit_caps(spec)

    wanted = {asset.asset_id for asset in roster}
    limits: dict[str, AssetLimits] = {}

    def record(asset_id: str, monthly_max: int, monthly_min: int, gap: int) -> None:
        if asset_id in wanted:
            limits[asset_id] = AssetLimits(
                monthly_max=monthly_max,
                monthly_min=monthly_min,
                revisit_gap_days=gap,
            )

    for area in masters.list_areas(connection):
        record(area.area_id, area.monthly_max, area.monthly_min, area.revisit_gap_days)
    for feeder in masters.list_feeders(connection):
        record(
            feeder.feeder_id,
            feeder.monthly_max,
            feeder.monthly_min,
            feeder.revisit_gap_days,
        )
    for dtc in masters.list_dtcs(connection):
        record(dtc.dtc_id, dtc.monthly_max, dtc.monthly_min, dtc.revisit_gap_days)
    return FeasibilityConfig(
        asset_limits=limits,
        combined_daily_cap=combined_cap,
        area_daily_cap=area_cap,
    )


def _activity_catalogue(connection: sqlite3.Connection) -> tuple[ActivitySpec, ...]:
    """The active activities, in the catalogue's own id order.

    A deactivated activity is left out rather than placed: removing it from the
    catalogue is how the office stops recording it, and a generator that ignored the
    flag would keep putting retired work in the register.
    """

    return tuple(
        ActivitySpec(
            activity_id=row.activity_id,
            category=row.category,
            title_en=row.description_en,
            title_kn=row.description_kn or None,
            weight=row.weight,
            monthly_min=row.monthly_min,
            monthly_max=row.monthly_max,
            repeat_gap_days=row.repeat_gap_days,
            weekday_mask=row.weekday_mask,
        )
        for row in masters.list_activities(connection)
        if row.active
    )


def _activities_per_day(requested: int | None) -> int:
    """One daily activity count, or a refusal the operator can act on.

    ``None`` means the caller did not ask for activities, and it stays that: a request
    that says nothing about activities generates a visits-only draft, exactly as it did
    before the catalogue existed. The daily count is a decision, so the operator's own
    form supplies it - and its box starts at :data:`DEFAULT_ACTIVITIES_PER_DAY`.
    """

    if requested is None:
        return 0
    if requested < 0:
        raise GenerationInputError(
            C_ACTIVITIES_PER_DAY_INVALID,
            (
                "the activities per day cannot be negative; use 0 to leave the days "
                "to field visits only"
            ),
        )
    return requested


def _daily_activity_range(spec: GenerationSpec) -> tuple[int, int]:
    """The floor and the ceiling of the regular-activity count, in that order.

    A request may state a range or the exact count it used to state; the exact count is
    the range whose ends are equal, so there is one rule for both and no second meaning
    to drift. An absent range is no activities at all, and a reversed one is refused
    here rather than inside the allocator, so the operator's form hears about its own
    numbers in the platform's own words.
    """

    if spec.activities_per_day_min is not None or spec.activities_per_day_max is not None:
        low = _activities_per_day(spec.activities_per_day_min)
        high = (
            low
            if spec.activities_per_day_max is None
            else _activities_per_day(spec.activities_per_day_max)
        )
    else:
        low = high = _activities_per_day(spec.activities_per_day)

    if high < low:
        raise GenerationInputError(
            C_ACTIVITY_DAILY_RANGE_INVALID,
            (
                f"the daily activity minimum of {low} is above the maximum of {high}; "
                "the minimum is the floor no working day falls below, so it cannot "
                "be the larger of the two"
            ),
        )
    return low, high


def _daily_visit_caps(spec: GenerationSpec) -> tuple[int, int]:
    """The field-visit ceiling per working day and the area share of it.

    Left unset they are the platform defaults, because a request that predates the
    choice must keep the register it used to get. They are read here rather than inside
    the validator because they come from the request: the caps are the operator's
    decision about how much field work one day can hold, not a property of the office's
    master data.
    """

    combined = (
        DEFAULT_COMBINED_DAILY_CAP
        if spec.combined_daily_cap is None
        else spec.combined_daily_cap
    )
    area = DEFAULT_AREA_DAILY_CAP if spec.area_daily_cap is None else spec.area_daily_cap

    if combined < 1:
        raise GenerationInputError(
            C_DAILY_VISIT_CAP_INVALID,
            (
                "the field visits per working day must be at least 1; a register "
                "that may hold no visit at all cannot record the month's obligations"
            ),
        )
    if area < 0:
        raise GenerationInputError(
            C_DAILY_VISIT_CAP_INVALID,
            "the area visits per working day cannot be negative",
        )
    if area > combined:
        raise GenerationInputError(
            C_DAILY_VISIT_CAP_INVALID,
            (
                f"the area visits per working day ({area}) cannot exceed the field "
                f"visits per working day ({combined}); the area share is part of that "
                "limit, not a second allowance"
            ),
        )
    return combined, area


def _entry_language(requested: str | None) -> str:
    """The language the register is written in, or a refusal the operator can act on.

    Absent means the platform default rather than a remembered choice: a generation that
    does not state its language must not silently inherit the last one.
    """

    if requested is None or str(requested).strip() == "":
        return DEFAULT_ENTRY_LANGUAGE
    if not wording.is_known_language(requested):
        raise GenerationInputError(
            C_ENTRY_LANGUAGE_INVALID,
            (
                f"{requested!r} is not a language the register is written in; choose "
                "English, Kannada, or both"
            ),
        )
    return str(requested).strip().lower()


def prepare_generation(
    connection: sqlite3.Connection, spec: GenerationSpec
) -> PreparedGeneration:
    """Assemble the request, the readiness report and the calendar projection."""

    if spec.end < spec.start:
        raise GenerationInputError(
            C_RANGE_REVERSED,
            "the last date is before the first date, so there is no period to generate",
        )

    months = spec.months()
    if not months:
        raise GenerationInputError(
            C_MONTHS_NOT_SELECTED, "the selected range covers no calendar month"
        )

    roster, display, weights = _roster(connection)
    if not roster:
        raise GenerationInputError(
            C_ROSTER_EMPTY,
            (
                "no active area, feeder or DTC is stored, so there is nothing to "
                "schedule; import the master data first"
            ),
        )

    projection = project_calendar(connection, spec.start, spec.end)
    entries = projection.entries

    catalogue = _activity_catalogue(connection)
    activities_per_day_min, activities_per_day_max = _daily_activity_range(spec)

    cycle: CoverageCycle | None = None
    if spec.mode is DraftMode.COVERAGE_CYCLE:
        cycle = CoverageCycle(
            start_month=spec.cycle_start_month or months[0],
            duration_months=max(1, spec.cycle_duration_months),
            visits_per_asset=max(1, spec.cycle_visits_per_asset),
        )

    request = DraftRequest(
        start=spec.start,
        end=spec.end,
        mode=spec.mode,
        roster=roster,
        display=tuple(display.values()),
        calendar_days=projection.days,
        config=_config(connection, roster, spec),
        seed=spec.seed,
        prior_history=spec.prior_history,
        fixed_by_month=dict(spec.fixed_by_month),
        entries=entries,
        activities=catalogue,
        activities_per_day_min=activities_per_day_min,
        activities_per_day_max=activities_per_day_max,
        entry_language=_entry_language(spec.entry_language),
        cycle=cycle,
        weights=weights,
        rotation_offset=spec.rotation_offset,
        area_required=dict(spec.area_required),
        feeder_required=dict(spec.feeder_required),
        dtc_target=spec.dtc_target,
        attempt_budget=spec.attempt_budget,
    )

    return PreparedGeneration(
        spec=spec,
        request=request,
        readiness=assess_readiness(connection, spec.start, spec.end),
        projection=projection,
        master_version=masters.current_version(connection),
    )
