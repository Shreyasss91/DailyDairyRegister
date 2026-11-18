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

from ..domain.activities import ActivitySpec
from ..domain.cycle import CoverageCycle, DtcClass, DtcWeights, MonthKey
from ..domain.validator import (
    Asset,
    AssetLimits,
    AssetType,
    FeasibilityConfig,
    Visit,
)
from ..infrastructure.sqlite import masters
from .calendar_projection import CalendarProjection, project_calendar
from .draft_generation import (
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

#: What the operator's own form starts with: the office asked for at least three
#: activities a working day. It is a form default rather than a silent platform rule,
#: because a register is a record of work, not a template applied behind a request.
DEFAULT_ACTIVITIES_PER_DAY = 3


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
    #: Regular activities recorded on every eligible working day. ``None`` means "the
    #: platform default", which is :data:`DEFAULT_ACTIVITIES_PER_DAY` when a catalogue
    #: is stored and ``0`` when it is not - a range must still generate field visits
    #: on an office that has not imported activities yet.
    activities_per_day: int | None = None
    #: Capacity-fallback mode only.
    dtc_target: int | None = None
    area_required: dict[MonthKey, dict[str, int]] = field(default_factory=dict)
    feeder_required: dict[MonthKey, dict[str, int]] = field(default_factory=dict)
    #: Carried from an earlier run or entered by the operator. Never invented here.
    fixed_by_month: dict[MonthKey, tuple[Visit, ...]] = field(default_factory=dict)
    prior_history: tuple[Visit, ...] = ()

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


def _config(connection: sqlite3.Connection, roster: tuple[Asset, ...]) -> FeasibilityConfig:
    """Configured caps and per-asset limits, read from the masters that hold them.

    The limits map is restricted to the assets actually in the roster. A stored limit
    for an asset that is no longer active would otherwise name an asset the validator
    has never heard of, and the every-other generation would be refused for a reason
    that has nothing to do with the request.
    """

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
    return FeasibilityConfig(asset_limits=limits)


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
    """The count every eligible day carries, or a refusal the operator can act on.

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
    per_day = _activities_per_day(spec.activities_per_day)

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
        config=_config(connection, roster),
        seed=spec.seed,
        prior_history=spec.prior_history,
        fixed_by_month=dict(spec.fixed_by_month),
        entries=entries,
        activities=catalogue,
        activities_per_day=per_day,
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
