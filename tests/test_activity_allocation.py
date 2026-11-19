"""Regular activities on working days: the count, the caps and the refusals.

The office asked for at least three activities on every working day, from the approved
activity catalogue it imports itself. These tests hold the placement rule to the
catalogue's own columns: the count per day is exact, a monthly maximum and a repeat gap
are hard, a weekday restriction is hard, a monthly minimum is a floor, and a catalogue
that cannot meet them is refused rather than quietly under-filled. They also pin how a
placed activity reaches the register - as a day entry that survives storage, the request
snapshot, and the issued page and workbook.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from dailyworkreg.application import export, revisions, snapshot
from dailyworkreg.application import finalisation as finalise
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application.draft_generation import (
    C_ACTIVITY_ALLOCATION_INFEASIBLE,
    DayEntryKind,
    DayEntryProvenance,
    DraftMode,
    DraftStatus,
    generate_draft,
)
from dailyworkreg.application.draft_requests import (
    C_ACTIVITIES_PER_DAY_INVALID,
    GenerationInputError,
    GenerationSpec,
    prepare_generation,
)
from dailyworkreg.domain.activities import (
    C_ACTIVITY_DAY_SHORT,
    C_ACTIVITY_LIMITS_INCONSISTENT,
    C_ACTIVITY_MASK_UNREADABLE,
    C_ACTIVITY_MONTH_MIN_UNSATISFIABLE,
    C_ACTIVITY_NONE_ALLOWED,
    ActivityInfeasible,
    ActivitySpec,
    allocate_activities,
    parse_weekday_mask,
)
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.domain.validator import InputError
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities

# --- the catalogue's own shape --------------------------------------------


def catalogue(count: int = 8, **overrides) -> tuple[ActivitySpec, ...]:
    """A catalogue wide enough to fill three activities a day, month after month."""

    items = []
    for index in range(1, count + 1):
        values = {
            "activity_id": f"ACT-{index:03d}",
            "category": "System operation",
            "title_en": f"Activity {index}",
            "title_kn": f"ಚಟುವಟಿಕೆ {index}",
            "weight": 1.5,
            "monthly_min": 1,
            "monthly_max": 22,
            "repeat_gap_days": 2,
            "weekday_mask": None,
        }
        values.update(overrides)
        items.append(ActivitySpec(**values))
    return tuple(items)


def working_days(start: date, end: date) -> list[date]:
    """Monday to Saturday, which is the working week the office recorded."""

    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() != 6:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def per_day(placements) -> dict[date, int]:
    counts: dict[date, int] = {}
    for placement in placements:
        counts[placement.day] = counts.get(placement.day, 0) + 1
    return counts


# --- the count per day ----------------------------------------------------


def test_every_eligible_day_carries_the_configured_count():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    plan = allocate_activities(
        days=days, activities=catalogue(), per_day_min=3, per_day_max=3, seed=7
    )
    assert set(per_day(plan).values()) == {3}
    assert len(plan) == 3 * len(days)


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_the_count_is_configurable(count):
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    # A day needs `count` different activities, and a repeat gap of two days needs one
    # further candidate per day of that gap, so the catalogue has to be wide enough for
    # the count being asked for.
    items = catalogue(max(8, 2 * count))
    plan = allocate_activities(days=days, activities=items, per_day_min=count, per_day_max=count, seed=7)
    assert set(per_day(plan).values()) == {count}


def test_a_count_of_zero_records_nothing():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    plan = allocate_activities(days=days, activities=catalogue(), per_day_min=0, per_day_max=0, seed=7)
    assert plan == ()


def test_a_day_never_carries_one_activity_twice():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    plan = allocate_activities(days=days, activities=catalogue(), per_day_min=3, per_day_max=3, seed=7)
    for day in days:
        ids = [p.activity.activity_id for p in plan if p.day == day]
        assert len(ids) == len(set(ids))


def test_a_day_outside_the_eligible_list_carries_nothing():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    plan = allocate_activities(days=days, activities=catalogue(), per_day_min=3, per_day_max=3, seed=7)
    sundays = {
        date(2026, 11, 1) + timedelta(days=offset)
        for offset in range(30)
        if (date(2026, 11, 1) + timedelta(days=offset)).weekday() == 6
    }
    assert not [p for p in plan if p.day in sundays]


# --- hard constraints -----------------------------------------------------


def test_the_monthly_maximum_is_respected():
    # Four eligible days of two activities is eight placements, while six activities at
    # a maximum of two can carry twelve - so the cap binds without starving the days.
    days = working_days(date(2026, 11, 2), date(2026, 11, 7))
    items = catalogue(6, monthly_max=2)
    plan = allocate_activities(days=days, activities=items, per_day_min=2, per_day_max=2, seed=7)
    assert len(plan) == 2 * len(days)
    counts: dict[str, int] = {}
    for placement in plan:
        counts[placement.activity.activity_id] = (
            counts.get(placement.activity.activity_id, 0) + 1
        )
    assert max(counts.values()) <= 2


def test_the_monthly_minimum_is_met():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    items = tuple(
        item.__class__(**{**item.__dict__, "monthly_min": 4 if index % 2 else 0})
        for index, item in enumerate(catalogue())
    )
    plan = allocate_activities(days=days, activities=items, per_day_min=3, per_day_max=3, seed=7)
    counts: dict[str, int] = {}
    for placement in plan:
        counts[placement.activity.activity_id] = (
            counts.get(placement.activity.activity_id, 0) + 1
        )
    for item in items:
        assert counts.get(item.activity_id, 0) >= item.monthly_min


def test_the_repeat_gap_is_respected_across_a_month_boundary():
    days = working_days(date(2026, 11, 1), date(2026, 12, 31))
    items = tuple(
        item.__class__(**{**item.__dict__, "repeat_gap_days": 10})
        for item in catalogue(45)
    )
    plan = allocate_activities(days=days, activities=items, per_day_min=3, per_day_max=3, seed=7)
    by_activity: dict[str, list[date]] = {}
    for placement in plan:
        by_activity.setdefault(placement.activity.activity_id, []).append(placement.day)
    for activity_id, placed in by_activity.items():
        placed.sort()
        for earlier, later in zip(placed, placed[1:], strict=False):
            assert (later - earlier).days >= 10, activity_id


def test_a_weekday_restriction_is_respected():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    items = tuple(
        item.__class__(**{**item.__dict__, "weekday_mask": "Wed"})
        if index == 0
        else item
        for index, item in enumerate(catalogue())
    )
    plan = allocate_activities(days=days, activities=items, per_day_min=3, per_day_max=3, seed=7)
    restricted = [
        placement
        for placement in plan
        if placement.activity.activity_id == "ACT-001"
    ]
    assert restricted
    assert {placement.day.weekday() for placement in restricted} == {2}


def test_two_records_for_one_activity_are_refused():
    days = working_days(date(2026, 11, 2), date(2026, 11, 6))
    items = catalogue(2) + catalogue(2)
    with pytest.raises(ActivityInfeasible) as error:
        allocate_activities(days=days, activities=items, per_day_min=2, per_day_max=2, seed=7)
    assert error.value.constraint == C_ACTIVITY_LIMITS_INCONSISTENT


def test_an_unreadable_weekday_mask_is_refused():
    with pytest.raises(ActivityInfeasible) as error:
        parse_weekday_mask("Funday", "ACT-001")
    assert error.value.constraint == C_ACTIVITY_MASK_UNREADABLE


def test_the_weekday_mask_also_reads_numbers_and_separators():
    assert parse_weekday_mask("Mon, Thu") == frozenset({0, 3})
    assert parse_weekday_mask("0;6") == frozenset({0, 6})
    assert parse_weekday_mask("") is None
    assert parse_weekday_mask(None) is None


def test_a_month_whose_minimums_exceed_its_slots_is_refused():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    items = tuple(
        item.__class__(**{**item.__dict__, "monthly_min": 22}) for item in catalogue()
    )
    with pytest.raises(ActivityInfeasible) as error:
        allocate_activities(days=days, activities=items, per_day_min=3, per_day_max=3, seed=7)
    assert error.value.constraint == C_ACTIVITY_MONTH_MIN_UNSATISFIABLE
    assert error.value.month == MonthKey(2026, 11)


def test_a_day_that_cannot_be_filled_is_refused():
    days = working_days(date(2026, 11, 2), date(2026, 11, 9))
    items = tuple(
        item.__class__(**{**item.__dict__, "monthly_max": 1}) for item in catalogue(3)
    )
    with pytest.raises(ActivityInfeasible) as error:
        allocate_activities(days=days, activities=items, per_day_min=3, per_day_max=3, seed=7)
    assert error.value.constraint in {
        C_ACTIVITY_DAY_SHORT,
    C_ACTIVITY_LIMITS_INCONSISTENT,
        C_ACTIVITY_MONTH_MIN_UNSATISFIABLE,
    }


def test_a_count_above_the_catalogue_size_is_refused():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    with pytest.raises(ActivityInfeasible) as error:
        allocate_activities(days=days, activities=catalogue(2), per_day_min=3, per_day_max=3, seed=7)
    assert error.value.constraint == C_ACTIVITY_NONE_ALLOWED
    assert "only 2 active activities" in error.value.detail


def test_an_empty_catalogue_is_refused_rather_than_silently_ignored():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    with pytest.raises(ActivityInfeasible) as error:
        allocate_activities(days=days, activities=(), per_day_min=3, per_day_max=3, seed=7)
    assert error.value.constraint == C_ACTIVITY_NONE_ALLOWED


# --- determinism ----------------------------------------------------------


def test_the_same_seed_produces_the_same_plan():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    first = allocate_activities(days=days, activities=catalogue(), per_day_min=3, per_day_max=3, seed=7)
    second = allocate_activities(days=days, activities=catalogue(), per_day_min=3, per_day_max=3, seed=7)
    assert first == second


def test_a_different_seed_produces_a_different_plan():
    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    first = allocate_activities(days=days, activities=catalogue(), per_day_min=3, per_day_max=3, seed=7)
    second = allocate_activities(days=days, activities=catalogue(), per_day_min=3, per_day_max=3, seed=8)
    assert first != second


def test_the_weight_does_not_override_a_maximum():
    """A heavily weighted activity still stops at its recorded monthly maximum."""

    days = working_days(date(2026, 11, 1), date(2026, 11, 30))
    items = (
        ActivitySpec(
            activity_id="ACT-001",
            category="System operation",
            title_en="Heavily weighted",
            weight=50.0,
            monthly_min=1,
            monthly_max=2,
            repeat_gap_days=2,
        ),
    ) + catalogue(7)[1:]
    plan = allocate_activities(days=days, activities=items, per_day_min=3, per_day_max=3, seed=7)
    assert len([p for p in plan if p.activity.activity_id == "ACT-001"]) == 2


# --- through a stored office ----------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """An office with a roster, a holiday, and a catalogue of twelve activities."""

    connection = open_database(tmp_path / "activities.db")
    seed_approved_identities(connection)
    for index in range(1, 7):
        masters.upsert_dtc(
            connection,
            dtc_id=f"SCD-{index}",
            name=f"Test DTC {index}",
            dtc_class="DEPT",
            kva=100.0 + index,
        )
    cal.upsert_holiday(
        connection,
        holiday_id="H-2026-11-10",
        holiday_date=date(2026, 11, 10),
        name="Recorded holiday",
        source="karnataka",
    )
    for index in range(1, 13):
        masters.upsert_activity(
            connection,
            activity_id=f"ACT-{index:03d}",
            category="System operation",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
            weight=1.5,
            monthly_min=1,
            monthly_max=15,
            repeat_gap_days=2,
        )
    yield connection
    connection.close()


def spec(*, months: int = 1, activities_per_day: int | None = 3):
    start = date(2026, 11, 1)
    end = date(2026, 11, 30) + timedelta(days=0)
    if months == 2:
        end = date(2026, 12, 31)
    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=7,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=months,
        cycle_visits_per_asset=1,
        activities_per_day=activities_per_day,
    )


def stored(db: sqlite3.Connection, **kwargs) -> str:
    prepared = prepare_generation(db, spec(**kwargs))
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    return generations.save_run(db, prepared, draft)


def activity_entries(db: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT row_date, title_en, provenance FROM generation_entry "
        "WHERE run_id = ? AND kind = 'activity' ORDER BY row_date, title_en",
        (run_id,),
    ).fetchall()


def test_a_generated_register_records_the_activities_on_working_days(db):
    run_id = stored(db)
    entries = activity_entries(db, run_id)

    rows = db.execute(
        "SELECT row_date FROM generation_row WHERE run_id = ? AND is_working_day = 1",
        (run_id,),
    ).fetchall()
    working = {row["row_date"] for row in rows}
    assert {entry["row_date"] for entry in entries} == working

    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["row_date"]] = counts.get(entry["row_date"], 0) + 1
    assert set(counts.values()) == {3}


def test_a_holiday_records_no_activity(db):
    run_id = stored(db)
    assert not [e for e in activity_entries(db, run_id) if e["row_date"] == "2026-11-10"]


def test_a_request_without_a_daily_count_records_no_activities(db):
    run_id = stored(db, activities_per_day=None)
    assert activity_entries(db, run_id) == []


def test_a_zero_daily_count_records_no_activities(db):
    run_id = stored(db, activities_per_day=0)
    assert activity_entries(db, run_id) == []


def test_a_generated_activity_is_marked_as_generated(db):
    run_id = stored(db)
    assert {entry["provenance"] for entry in activity_entries(db, run_id)} == {
        DayEntryProvenance.GENERATED.value
    }


def test_the_count_reaches_every_month_of_a_multi_month_range(db):
    run_id = stored(db, months=2)
    months = {
        entry["row_date"][:7] for entry in activity_entries(db, run_id)
    }
    assert months == {"2026-11", "2026-12"}


def test_the_daily_count_stays_on_every_working_day_of_a_long_range(db):
    prepared = prepare_generation(
        db,
        GenerationSpec(
            start=date(2026, 11, 1),
            end=date(2026, 11, 30),
            mode=DraftMode.COVERAGE_CYCLE,
            seed=7,
            cycle_start_month=MonthKey(2026, 11),
            activities_per_day=5,
        ),
    )
    draft = generate_draft(prepared.request)
    counts: dict[date, int] = {}
    for row in draft.rows:
        if not row.is_working_day:
            continue
        counts[row.day] = len(
            [entry for entry in row.entries if entry.kind is DayEntryKind.ACTIVITY]
        )
    assert set(counts.values()) == {5}


def test_a_negative_daily_count_is_refused(db):
    with pytest.raises(GenerationInputError) as error:
        prepare_generation(db, spec(activities_per_day=-2))
    assert error.value.constraint == C_ACTIVITIES_PER_DAY_INVALID


def test_an_office_without_a_catalogue_is_refused_when_activities_are_asked_for(
    tmp_path: Path,
):
    connection = open_database(tmp_path / "no-catalogue.db")
    seed_approved_identities(connection)
    masters.upsert_dtc(
        connection,
        dtc_id="SCD-1",
        name="Test DTC",
        dtc_class="DEPT",
        kva=100.0,
    )
    cal.upsert_holiday(
        connection,
        holiday_id="H-2026-11-10",
        holiday_date=date(2026, 11, 10),
        name="Recorded holiday",
        source="karnataka",
    )
    prepared = prepare_generation(connection, spec(activities_per_day=3))
    with pytest.raises(InputError) as error:
        generate_draft(prepared.request)
    assert error.value.constraint == C_ACTIVITY_ALLOCATION_INFEASIBLE
    assert "activity" in error.value.detail.lower()
    connection.close()


# --- the request snapshot -------------------------------------------------


def test_the_daily_range_and_the_catalogue_survive_a_snapshot_round_trip(db):
    prepared = prepare_generation(db, spec())
    encoded = snapshot.encode_request(prepared.request)
    assert encoded["schema"] == snapshot.REQUEST_SNAPSHOT_SCHEMA
    assert encoded["activities_per_day_min"] == 3
    assert encoded["activities_per_day_max"] == 3
    assert len(encoded["activities"]) == 12

    restored = snapshot.decode_request(encoded)
    assert restored.activities_per_day_min == 3
    assert restored.activities_per_day_max == 3
    assert restored.activities == prepared.request.activities
    assert {entry["provenance"] for entry in encoded["entries"]} <= {
        "calendar",
        "manual",
        "locked",
        "generated",
    }


def test_a_schema_one_snapshot_still_reads_as_a_visits_only_request(db):
    prepared = prepare_generation(db, spec(activities_per_day=0))
    encoded = snapshot.encode_request(prepared.request)
    old = dict(encoded)
    old["schema"] = 1
    old.pop("activities")
    old.pop("activities_per_day_min")
    old.pop("activities_per_day_max")
    old["entries"] = [
        {key: value for key, value in item.items() if key != "provenance"}
        for item in encoded["entries"]
    ]

    restored = snapshot.decode_request(old)
    assert restored.activities == ()
    assert restored.activities_per_day_min == 0
    assert restored.activities_per_day_max == 0
    assert {entry.provenance for entry in restored.entries} == {
        DayEntryProvenance.CALENDAR
    }


# --- the issued page and workbook ----------------------------------------


def review_every_date(db: sqlite3.Connection, run_id: str, *, month: str = "2026-11"):
    for item in revisions.review_state(db, run_id)["dates"]:
        if not item["date"].startswith(month):
            continue
        revisions.mark_reviewed(
            db,
            run_id,
            date.fromisoformat(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )


def issued(db: sqlite3.Connection, **kwargs) -> str:
    run_id = stored(db, **kwargs)
    review_every_date(db, run_id)
    record = finalise.finalise_month(db, run_id, "2026-11")
    return record["finalisation_id"]


def test_the_register_lists_the_activities_of_a_day(db):
    document = export.document(db, issued(db))
    days = [day for day in document.days if day.day == "2026-11-03"]
    assert days, "the issued record should carry the day"
    text = days[0].activity_text
    assert "Activity " in text
    # Three activities on one date, joined into the one Activity cell.
    assert text.count("Activity ") == 3


def test_the_register_keeps_the_kannada_beside_the_english(db):
    document = export.document(db, issued(db))
    text = next(day.activity_text for day in document.days if day.day == "2026-11-03")
    assert "ಚಟುವಟಿಕೆ" in text


def test_the_issued_workbook_carries_the_activities_too(db):
    from io import BytesIO

    from dailyworkreg.interfaces import documents
    from openpyxl import load_workbook

    document = export.document(db, issued(db))
    book = load_workbook(BytesIO(documents.workbook_bytes(document)))
    sheet = book[documents.REGISTER_SHEET]
    values = [row[2] for row in sheet.iter_rows(min_row=1, max_col=3, values_only=True)]
    assert any(value and "Activity " in str(value) for value in values)


# --- through the HTTP surface --------------------------------------------


def surface(tmp_path: Path):
    """A client whose masters can generate November with six activities."""

    from dailyworkreg.interfaces.api import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app(tmp_path / "surface.db"))
    for index in range(1, 7):
        client.put(
            "/api/masters/dtc",
            json={
                "dtc_id": f"SCD-{index}",
                "name": f"Test DTC {index}",
                "dtc_class": "DEPT",
                "kva": 100.0 + index,
            },
        )
    for index in range(1, 9):
        client.put(
            "/api/masters/activity",
            json={
                "activity_id": f"ACT-{index:03d}",
                "category": "System operation",
                "description_en": f"Activity {index}",
                "description_kn": f"ಚಟುವಟಿಕೆ {index}",
                "weight": 1.5,
                "monthly_min": 1,
                "monthly_max": 20,
                "repeat_gap_days": 2,
            },
        )
    client.put(
        "/api/calendar/holidays",
        json={
            "holiday_date": "2026-11-10",
            "name": "Recorded holiday",
            "source": "karnataka",
        },
    )
    return client


def generate(client, **extra):
    body = {
        "start": "2026-11-01",
        "end": "2026-11-30",
        "mode": "coverage_cycle",
        "seed": 7,
        "cycle_start_month": "2026-11",
    }
    body.update(extra)
    return client.post("/api/generations", json=body)


def test_the_generation_route_places_the_activities_it_was_asked_for(tmp_path: Path):
    client = surface(tmp_path)
    answer = generate(client, activities_per_day=3)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["activities"] > 0

    reported = client.get(f"/api/generations/{body['run_id']}").json()
    counts: dict[str, int] = {}
    for row in reported["rows"]:
        counts[row["day"]] = len(
            [entry for entry in row["entries"] if entry["kind"] == "activity"]
        )
    working = [row["day"] for row in reported["rows"] if row["is_working_day"]]
    assert {counts[day] for day in working} == {3}
    assert counts["2026-11-10"] == 0


def test_the_generation_route_records_none_when_no_count_is_asked_for(tmp_path: Path):
    client = surface(tmp_path)
    answer = generate(client)
    assert answer.status_code == 200, answer.text
    reported = client.get(f"/api/generations/{answer.json()['run_id']}").json()
    assert not [
        entry
        for row in reported["rows"]
        for entry in row["entries"]
        if entry["kind"] == "activity"
    ]


def test_the_generation_route_refuses_a_negative_count(tmp_path: Path):
    client = surface(tmp_path)
    answer = generate(client, activities_per_day=-1)
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == C_ACTIVITIES_PER_DAY_INVALID
