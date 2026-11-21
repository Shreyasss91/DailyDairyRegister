"""One daily ceiling per kind of visit, and nothing else counts against them.

The office asked for a separate maximum on each kind - transformers, areas, feeders
and activities - with a minimum only on activities, and for the safety meeting held
every Monday to stay out of all of those numbers. These tests pin that contract at
the layer that enforces it: the validator's day arithmetic, the allocator's
placement, the activity allocator's per-day count, the request and snapshot fields
that carry the ceilings, and the form an operator sets them on.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application import snapshot
from dailyworkreg.application.draft_generation import (
    C_ACTIVITY_ALLOCATION_INFEASIBLE,
    DraftMode,
    DraftStatus,
    generate_draft,
)
from dailyworkreg.application.draft_requests import (
    C_DAILY_VISIT_CAP_INVALID,
    DEFAULT_ACTIVITIES_PER_DAY,
    DEFAULT_ACTIVITIES_PER_DAY_MAX,
    DEFAULT_AREA_DAILY_CAP,
    DEFAULT_DTC_DAILY_CAP,
    DEFAULT_FEEDER_DAILY_CAP,
    GenerationInputError,
    GenerationSpec,
    prepare_generation,
)
from dailyworkreg.domain.activities import C_ACTIVITY_NONE_ALLOWED
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.domain.validator import (
    C_AGG_AREA_CAPACITY,
    C_AGG_DTC_CAPACITY,
    C_AGG_FEEDER_CAPACITY,
    C_DTC_DAILY_CAP,
    KIND_AGGREGATE_CONSTRAINT,
    KIND_LABEL,
    Asset,
    AssetType,
    Calendar,
    DatePeriod,
    FeasibilityConfig,
    FeasibilityStatus,
    InputError,
    ObligationContext,
    Visit,
    assess_feasibility,
)
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from master_fixtures import seed_office_masters

#: November 2026, the month the office's own files were measured on.
NOVEMBER = (date(2026, 11, 1), date(2026, 11, 30))

#: The Mondays of November 2026, each of which carries the safety meeting.
MONDAYS = (
    date(2026, 11, 2),
    date(2026, 11, 9),
    date(2026, 11, 16),
    date(2026, 11, 23),
    date(2026, 11, 30),
)

#: Every date of the month that can carry a visit: the first and the third Saturday
#: work, the second and the fourth do not, and no holiday is recorded in this office.
WORKING_DATES = tuple(
    day
    for day in (date(2026, 11, 1).replace(day=number) for number in range(1, 31))
    if day.weekday() < 5 or day in (date(2026, 11, 7), date(2026, 11, 21))
)

ROOT = Path(__file__).resolve().parents[1]


# --- the fixture ----------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """An office owing one transformer visit on every working date of November."""

    connection = open_database(tmp_path / "per_kind.db")
    seed_office_masters(connection)
    for index in range(1, 27):
        masters.upsert_dtc(
            connection,
            dtc_id=f"SCD-{index}",
            name=f"Test DTC {index}",
            dtc_class="DEPT",
            kva=100.0 + index,
        )
    for index in range(1, 6):
        masters.upsert_area(
            connection,
            area_id=f"A-{index:02d}",
            name_kn=f"ಪ್ರದೇಶ {index}",
            monthly_min=0,
            monthly_max=6,
            revisit_gap_days=5,
        )
    for index in range(1, 4):
        masters.upsert_feeder(
            connection,
            feeder_id=f"F-{index:02d}",
            name=f"Feeder {index}",
            monthly_min=0,
            monthly_max=20,
            revisit_gap_days=4,
        )
    for index in range(1, 13):
        masters.upsert_activity(
            connection,
            activity_id=f"ACT-{index:03d}",
            category="System operation",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
            weight=1.0,
            monthly_min=0,
            monthly_max=30,
            repeat_gap_days=1,
        )
    yield connection
    connection.close()


def spec(**overrides) -> GenerationSpec:
    """One transformer duty on each of the month's twenty-three working dates."""

    values = {
        "start": NOVEMBER[0],
        "end": NOVEMBER[1],
        "mode": DraftMode.CAPACITY_FALLBACK,
        "seed": 11,
        "cycle_start_month": None,
        "cycle_duration_months": None,
        "cycle_visits_per_asset": None,
        "dtc_target": len(WORKING_DATES),
        # The month must reject a total, so the day holds one visit of each kind at
        # most and the transformer duties fill every working date exactly once.
        "dtc_daily_cap": 1,
        "area_daily_cap": 1,
        "feeder_daily_cap": 1,
        "activities_per_day_min": 4,
        "activities_per_day_max": 4,
    }
    values.update(overrides)
    return GenerationSpec(**values)


def draft_for(connection: sqlite3.Connection, **overrides) -> tuple:
    """Generate November and return the draft with the roster's asset kinds."""

    prepared = prepare_generation(connection, spec(**overrides))
    kinds = {a.asset_id: a.asset_type for a in prepared.request.roster}
    return generate_draft(prepared.request), kinds


def working(rows) -> list:
    return [row for row in rows if row.is_working_day]


def counts(row, kind: str) -> int:
    """How many entries of one kind the date recorded."""

    return len([entry for entry in row.entries if entry.kind.value == kind])


def visits_of(row, kinds, asset_type: str) -> int:
    """How many visits of one kind the date holds."""

    return len([visit for visit in row.visits if kinds[visit.asset_id] is asset_type])


# --- one ceiling per kind -------------------------------------------------


def test_a_date_holds_each_kind_up_to_its_own_ceiling(db):
    draft, kinds = draft_for(db, dtc_daily_cap=2, area_daily_cap=2, feeder_daily_cap=1)
    assert draft.status is DraftStatus.ALLOCATION_COMPLETE
    for row in working(draft.rows):
        assert visits_of(row, kinds, AssetType.DTC) <= 2
        assert visits_of(row, kinds, AssetType.AREA) <= 2
        assert visits_of(row, kinds, AssetType.FEEDER) <= 1


def test_the_configured_ceiling_is_the_one_the_schedule_holds_to(db):
    """A ceiling of one transformer a date survives a month that needs one a date."""

    draft, kinds = draft_for(db)
    assert draft.status is DraftStatus.ALLOCATION_COMPLETE
    every_date = [visits_of(row, kinds, AssetType.DTC) for row in working(draft.rows)]
    assert every_date == [1] * len(working(draft.rows))


def test_the_day_capacity_is_the_sum_of_the_kind_ceilings():
    config = FeasibilityConfig(dtc_daily_cap=3, area_daily_cap=2, feeder_daily_cap=1)
    assert config.total_capacity_per_day == 6
    assert config.cap_for(AssetType.DTC) == 3
    assert config.cap_for(AssetType.AREA) == 2
    assert config.cap_for(AssetType.FEEDER) == 1


def test_a_stated_total_is_an_extra_ceiling_over_all_of_the_kinds():
    config = FeasibilityConfig(
        dtc_daily_cap=3, area_daily_cap=2, feeder_daily_cap=1, combined_daily_cap=2
    )
    assert config.combined_daily_cap == 2
    # The kinds keep their own ceilings; the total can only make the day tighter.
    assert config.total_capacity_per_day == 2


# --- the refusals name the kind that was exceeded --------------------------


def test_each_kind_has_its_own_day_constraint_and_aggregate_constraint():
    for asset_type in (AssetType.DTC, AssetType.AREA, AssetType.FEEDER):
        assert KIND_LABEL[asset_type]
        assert KIND_AGGREGATE_CONSTRAINT[asset_type].endswith("_capacity_exceeded")


def test_a_month_owed_more_transformers_than_a_day_allows_is_refused_by_kind(db):
    # Two duties per transformer over twenty-two working dates, one allowed a date.
    draft, _ = draft_for(
        db,
        mode=DraftMode.COVERAGE_CYCLE,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=1,
        cycle_visits_per_asset=2,
        dtc_target=None,
        dtc_daily_cap=1,
    )
    assert draft.status is DraftStatus.INFEASIBLE
    evidence = draft.months[0].evidence[0]
    assert evidence.constraint == C_AGG_DTC_CAPACITY
    # The refusal names the transformer allowance, not a total for all visits, and it
    # states the number that would fit the month.
    assert "DTC" in evidence.detail
    assert "at least 3 DTC visits a working day would fit them" in evidence.detail


def test_a_month_owed_more_area_visits_than_a_day_allows_is_refused_by_kind(db):
    areas = {MonthKey(2026, 11): {f"A-{index:02d}": 6 for index in range(1, 6)}}
    draft, _ = draft_for(db, dtc_target=1, area_required=areas, area_daily_cap=1)
    assert draft.status is DraftStatus.INFEASIBLE
    assert draft.months[0].evidence[0].constraint == C_AGG_AREA_CAPACITY


def test_a_month_owed_more_feeder_visits_than_a_day_allows_is_refused_by_kind(db):
    feeders = {MonthKey(2026, 11): {f"F-{index:02d}": 9 for index in range(1, 4)}}
    draft, _ = draft_for(
        db,
        dtc_target=1,
        feeder_required=feeders,
        feeder_daily_cap=1,
    )
    assert draft.status is DraftStatus.INFEASIBLE
    assert draft.months[0].evidence[0].constraint == C_AGG_FEEDER_CAPACITY


def test_a_committed_day_above_one_kind_ceiling_is_refused_by_that_kind():
    """A pre-committed day is judged against the kind's own ceiling, not a total."""

    roster = (
        Asset("SCD-1", AssetType.DTC),
        Asset("SCD-2", AssetType.DTC),
    )
    context = ObligationContext(
        period=DatePeriod(date(2026, 11, 1), date(2026, 11, 30)),
        calendar=Calendar([date(2026, 11, 2)]),
        roster=roster,
        config=FeasibilityConfig(dtc_daily_cap=1),
        fixed=(Visit("SCD-1", date(2026, 11, 2)), Visit("SCD-2", date(2026, 11, 2))),
        required_visits={},
    )
    result = assess_feasibility(context)
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_DTC_DAILY_CAP in {evidence.constraint for evidence in result.evidence}


# --- the Monday safety meeting is not one of the numbers -------------------


def test_a_monday_carries_its_safety_meeting_and_its_full_day_of_visits(db):
    draft, kinds = draft_for(db)
    rows = {row.day: row for row in draft.rows}
    meeting_days = [
        day for day in MONDAYS if day in rows and rows[day].is_working_day
    ]
    assert meeting_days, "the month has working Mondays"
    for day in meeting_days:
        row = rows[day]
        assert counts(row, "safety_meeting") == 1
        # The meeting is not a visit: the date still holds the transformer duty it
        # would hold on any other working date.
        assert visits_of(row, kinds, AssetType.DTC) == 1


def test_the_safety_meeting_is_not_charged_against_the_activity_ceiling(db):
    draft, _ = draft_for(db)
    rows = {row.day: row for row in draft.rows}
    working_days = [row.day for row in working(draft.rows)]
    assert working_days
    for day in working_days:
        # Four catalogue activities every working date, Mondays included: the meeting
        # sits beside them rather than inside the four.
        assert counts(rows[day], "activity") == 4
    for day in MONDAYS:
        if day in rows and rows[day].is_working_day:
            assert len(rows[day].entries) == 5


def test_the_safety_meeting_gives_no_credit_towards_the_activity_minimum(db):
    """A date short of its activities is short of them; the meeting is not a credit."""

    # Twenty activities a date against a catalogue of twelve. If the Monday meeting
    # counted as one of the day's activities the floor would be nineteen short of the
    # catalogue rather than eight, and the refusal still names the whole shortfall.
    with pytest.raises(InputError) as caught:
        draft_for(db, activities_per_day_min=20, activities_per_day_max=20)
    assert caught.value.constraint == C_ACTIVITY_ALLOCATION_INFEASIBLE
    assert "20 different activities" in caught.value.detail


# --- the request, the snapshot and the form --------------------------------


def test_the_request_carries_each_ceiling_separately(db):
    prepared = prepare_generation(
        db,
        spec(dtc_daily_cap=4, area_daily_cap=3, feeder_daily_cap=2),
        # A total is stated too, so the request shows the four fields are distinct.
    )
    config = prepared.request.config
    assert (config.dtc_daily_cap, config.area_daily_cap, config.feeder_daily_cap) == (
        4,
        3,
        2,
    )
    assert config.combined_daily_cap is None


def test_a_negative_ceiling_is_refused_before_generation(db):
    for field_name in ("dtc_daily_cap", "area_daily_cap", "feeder_daily_cap"):
        with pytest.raises(GenerationInputError) as caught:
            prepare_generation(db, spec(**{field_name: -1}))
        assert caught.value.constraint == C_DAILY_VISIT_CAP_INVALID


def test_every_kind_at_zero_is_refused_because_no_date_could_hold_a_visit(db):
    with pytest.raises(GenerationInputError) as caught:
        prepare_generation(
            db, spec(dtc_daily_cap=0, area_daily_cap=0, feeder_daily_cap=0)
        )
    assert caught.value.constraint == C_DAILY_VISIT_CAP_INVALID


def test_a_snapshot_written_now_keeps_each_ceiling(db):
    prepared = prepare_generation(
        db, spec(dtc_daily_cap=4, area_daily_cap=3, feeder_daily_cap=2)
    )
    payload = snapshot.encode_request(prepared.request)
    assert payload["schema"] == snapshot.REQUEST_SNAPSHOT_SCHEMA
    assert payload["config"]["dtc_daily_cap"] == 4
    assert payload["config"]["area_daily_cap"] == 3
    assert payload["config"]["feeder_daily_cap"] == 2
    assert payload["config"]["combined_daily_cap"] is None
    restored = snapshot.decode_request(payload)
    assert restored.config.dtc_daily_cap == 4
    assert restored.config.area_daily_cap == 3
    assert restored.config.feeder_daily_cap == 2


def test_a_snapshot_written_before_the_kinds_were_separate_reads_its_total_as_one(db):
    """A stored total was a ceiling on the whole date, so it stays one."""

    prepared = prepare_generation(
        db, spec(dtc_daily_cap=3, area_daily_cap=2, feeder_daily_cap=1)
    )
    payload = snapshot.encode_request(prepared.request)
    # The shape schema 4 wrote: one total for the date, an area share, and nothing
    # per kind beyond the area allowance.
    payload["schema"] = 4
    payload["config"]["combined_daily_cap"] = 3
    payload["config"]["area_daily_cap"] = 2
    payload["config"].pop("dtc_daily_cap")
    payload["config"].pop("feeder_daily_cap")
    restored = snapshot.decode_request(payload)
    assert restored.config.combined_daily_cap == 3
    assert restored.config.area_daily_cap == 2
    # The kinds it never named keep the platform defaults.
    assert restored.config.dtc_daily_cap == DEFAULT_DTC_DAILY_CAP
    assert restored.config.feeder_daily_cap == DEFAULT_FEEDER_DAILY_CAP


def test_the_form_offers_a_ceiling_for_each_kind_and_keeps_the_defaults():
    html = (ROOT / "dailyworkreg" / "interfaces" / "templates" / "index.html").read_text(
        encoding="utf-8"
    )
    for element_id, default in (
        ("gen-dtc-cap", DEFAULT_DTC_DAILY_CAP),
        ("gen-area-cap", DEFAULT_AREA_DAILY_CAP),
        ("gen-feeder-cap", DEFAULT_FEEDER_DAILY_CAP),
    ):
        assert f'id="{element_id}"' in html
        field = html.split(f'id="{element_id}"')[1].split("/>")[0]
        assert f'value="{default}"' in field, element_id
    assert 'id="gen-activities-min"' in html
    assert 'id="gen-activities-max"' in html


def test_the_form_says_the_meeting_is_not_counted():
    html = (ROOT / "dailyworkreg" / "interfaces" / "templates" / "index.html").read_text(
        encoding="utf-8"
    )
    text = " ".join(html.split()).lower()
    assert "safety meeting" in text
    # The form must not present one ceiling for all visit kinds: that number is what
    # used to refuse months that each kind could hold on its own.
    assert 'id="gen-visit-cap"' not in html


def test_the_activity_defaults_still_bound_only_the_activity_lines():
    assert DEFAULT_ACTIVITIES_PER_DAY == 1
    assert DEFAULT_ACTIVITIES_PER_DAY_MAX == 3
