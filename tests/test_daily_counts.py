"""The two numbers a working day is built from: a range, and a visit ceiling.

The office asked for *at least* three activities a working day. A minimum is a floor,
so the daily count became a range whose two ends are both hard: no working day falls
below the minimum, none rises above the maximum, and the day's own count is drawn
between them. The number that refused a month - "71 visits are required ... exceeding
the total limit of 3 per working day" - was a visit ceiling the operator could not see,
let alone change, so both halves of that number are now stated in the request. The
first of the two is the whole of a date's work, not the part of it that is not an area
visit: it is labelled Total because reading it as "field visits, plus area visits" is
how an office came to believe the form allowed twice what it did.

These tests hold the contract at the layer that owns each part: the allocator for the
range, the request builder for the ceiling, the snapshot for what a stored draft
reproduces, and the HTTP surface and the client for what an operator can set.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
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
    GenerationInputError,
    GenerationSpec,
    prepare_generation,
)
from dailyworkreg.domain.activities import (
    C_ACTIVITY_DAILY_RANGE_INVALID,
    C_ACTIVITY_MONTH_MIN_UNSATISFIABLE,
    C_ACTIVITY_NONE_ALLOWED,
    ActivityInfeasible,
    ActivitySpec,
    allocate_activities,
)
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.domain.validator import (
    C_AGG_AREA_CAPACITY,
    C_AGG_COMBINED_CAPACITY,
    DEFAULT_AREA_DAILY_CAP,
    DEFAULT_COMBINED_DAILY_CAP,
    InputError,
)
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities

# --- helpers --------------------------------------------------------------


def catalogue(count: int = 12, **overrides) -> tuple[ActivitySpec, ...]:
    """A catalogue wide enough that a day of five is never short of candidates."""

    items = []
    for index in range(1, count + 1):
        values = {
            "activity_id": f"ACT-{index:03d}",
            "category": "System operation",
            "title_en": f"Activity {index}",
            "title_kn": f"ಚಟುವಟಿಕೆ {index}",
            "weight": 1.0,
            "monthly_min": 0,
            "monthly_max": 22,
            "repeat_gap_days": 1,
            "weekday_mask": None,
        }
        values.update(overrides)
        items.append(ActivitySpec(**values))
    return tuple(items)


def working_days(start: date, end: date) -> list[date]:
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


NOVEMBER = working_days(date(2026, 11, 1), date(2026, 11, 30))


# --- the range the allocator honours --------------------------------------


def test_no_working_day_falls_below_the_minimum_or_rises_above_the_maximum():
    plan = allocate_activities(
        days=NOVEMBER, activities=catalogue(), per_day_min=3, per_day_max=6, seed=7
    )
    counts = per_day(plan)
    assert set(counts) == set(NOVEMBER), "every eligible day carries activities"
    assert min(counts.values()) >= 3
    assert max(counts.values()) <= 6


def test_the_days_are_not_all_the_same_length():
    """A range that never varied would be a maximum pretending to be a range."""

    plan = allocate_activities(
        days=NOVEMBER, activities=catalogue(), per_day_min=3, per_day_max=6, seed=7
    )
    assert len(set(per_day(plan).values())) > 1


def test_the_minimum_is_a_floor_and_not_a_target():
    """A month of threes is what the floor would look like on its own; it is not this."""

    plan = allocate_activities(
        days=NOVEMBER, activities=catalogue(), per_day_min=3, per_day_max=6, seed=7
    )
    assert sum(per_day(plan).values()) > 3 * len(NOVEMBER)


def test_an_equal_ended_range_is_the_flat_count_an_earlier_request_asked_for():
    plan = allocate_activities(
        days=NOVEMBER, activities=catalogue(), per_day_min=4, per_day_max=4, seed=7
    )
    assert set(per_day(plan).values()) == {4}


def test_the_same_seed_places_the_same_days_and_a_different_seed_does_not():
    first = allocate_activities(
        days=NOVEMBER, activities=catalogue(), per_day_min=3, per_day_max=6, seed=7
    )
    same = allocate_activities(
        days=NOVEMBER, activities=catalogue(), per_day_min=3, per_day_max=6, seed=7
    )
    other = allocate_activities(
        days=NOVEMBER, activities=catalogue(), per_day_min=3, per_day_max=6, seed=8
    )
    assert first == same
    assert per_day(first) != per_day(other)


def test_a_ceiling_above_the_catalogue_is_a_ceiling_rather_than_a_demand():
    """A maximum may be drawn under. Only the floor has to be satisfiable."""

    plan = allocate_activities(
        days=NOVEMBER, activities=catalogue(4), per_day_min=2, per_day_max=9, seed=7
    )
    counts = per_day(plan)
    assert set(counts) == set(NOVEMBER)
    assert min(counts.values()) >= 2
    assert max(counts.values()) <= 4


def test_a_reversed_range_is_refused_by_the_allocator():
    with pytest.raises(ActivityInfeasible) as caught:
        allocate_activities(days=NOVEMBER, activities=catalogue(), per_day_min=5, per_day_max=3)
    assert caught.value.constraint == C_ACTIVITY_DAILY_RANGE_INVALID


def test_a_negative_minimum_is_refused_by_the_allocator():
    with pytest.raises(ActivityInfeasible) as caught:
        allocate_activities(days=NOVEMBER, activities=catalogue(), per_day_min=-1, per_day_max=3)
    assert caught.value.constraint == C_ACTIVITY_DAILY_RANGE_INVALID


def test_a_floor_wider_than_the_catalogue_is_refused():
    with pytest.raises(ActivityInfeasible) as caught:
        allocate_activities(days=NOVEMBER, activities=catalogue(3), per_day_min=4, per_day_max=6)
    assert caught.value.constraint == C_ACTIVITY_NONE_ALLOWED


def test_a_monthly_minimum_above_the_ceiling_capacity_is_refused():
    items = tuple(
        ActivitySpec(
            activity_id=f"ACT-{index:03d}",
            category="System operation",
            title_en=f"Activity {index}",
            monthly_min=3,
            monthly_max=22,
            repeat_gap_days=1,
        )
        for index in range(1, 13)
    )
    # 12 activities owing three each is 36 placements; two days at most two each is 4.
    with pytest.raises(ActivityInfeasible) as caught:
        allocate_activities(
            days=[date(2026, 11, 2), date(2026, 11, 3)],
            activities=items,
            per_day_min=1,
            per_day_max=2,
        )
    assert caught.value.constraint == C_ACTIVITY_MONTH_MIN_UNSATISFIABLE


def test_an_outstanding_minimum_may_borrow_the_headroom_but_never_exceed_it():
    """A day drawn at the floor still takes work the month owes, up to the ceiling."""

    items = tuple(
        ActivitySpec(
            activity_id=f"ACT-{index:03d}",
            category="System operation",
            title_en=f"Activity {index}",
            monthly_min=1,
            monthly_max=2,
            repeat_gap_days=1,
        )
        for index in range(1, 13)
    )
    days = working_days(date(2026, 11, 2), date(2026, 11, 6))
    plan = allocate_activities(days=days, activities=items, per_day_min=0, per_day_max=4, seed=3)
    counts = per_day(plan)
    assert sum(counts.values()) >= 12, "every monthly minimum was placed"
    assert max(counts.values()) <= 4, "and no day went over its ceiling"


# --- the ceiling on field visits -----------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """An office whose November obligations need more visits than three a day."""

    connection = open_database(tmp_path / "counts.db")
    seed_approved_identities(connection)
    for index in range(1, 31):
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
    for index in range(1, 11):
        masters.upsert_feeder(
            connection,
            feeder_id=f"F-{index:02d}",
            name=f"Feeder {index}",
            monthly_min=3,
            monthly_max=4,
            revisit_gap_days=4,
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
            weight=1.0,
            monthly_min=0,
            monthly_max=20,
            repeat_gap_days=1,
        )
    yield connection
    connection.close()


def spec(**overrides) -> GenerationSpec:
    values = {
        "start": date(2026, 11, 1),
        "end": date(2026, 11, 30),
        "mode": DraftMode.COVERAGE_CYCLE,
        "seed": 7,
        "cycle_start_month": MonthKey(2026, 11),
        "cycle_duration_months": 1,
        "cycle_visits_per_asset": 1,
    }
    values.update(overrides)
    return GenerationSpec(**values)


def test_an_unstated_visit_ceiling_keeps_the_platform_default(db):
    prepared = prepare_generation(db, spec())
    assert prepared.request.config.combined_daily_cap == DEFAULT_COMBINED_DAILY_CAP
    assert prepared.request.config.area_daily_cap == DEFAULT_AREA_DAILY_CAP


def test_the_visit_ceilings_reach_the_request_unchanged(db):
    prepared = prepare_generation(db, spec(combined_daily_cap=6, area_daily_cap=3))
    assert prepared.request.config.combined_daily_cap == 6
    assert prepared.request.config.area_daily_cap == 3


def test_a_ceiling_of_zero_visits_a_day_is_refused(db):
    with pytest.raises(GenerationInputError) as caught:
        prepare_generation(db, spec(combined_daily_cap=0))
    assert caught.value.constraint == C_DAILY_VISIT_CAP_INVALID


def test_an_area_share_above_the_whole_ceiling_is_refused(db):
    with pytest.raises(GenerationInputError) as caught:
        prepare_generation(db, spec(combined_daily_cap=3, area_daily_cap=4))
    assert caught.value.constraint == C_DAILY_VISIT_CAP_INVALID


def test_the_month_that_was_refused_generates_once_the_ceiling_is_raised(db):
    """The shape of the office's own refusal: more visits owed than a day allows."""

    refused = generate_draft(
        prepare_generation(db, spec(combined_daily_cap=1, area_daily_cap=1)).request
    )
    assert refused.status == DraftStatus.INFEASIBLE
    evidence = refused.months[0].evidence[0]
    assert evidence.constraint == C_AGG_COMBINED_CAPACITY
    assert "exceeding the total limit of 1 per working day" in evidence.detail
    # The refusal names the ceiling that would work, so the operator is not left to
    # divide the shortfall by the working days themselves.
    assert "at least 3 visits a working day would fit them" in evidence.detail

    raised = generate_draft(
        prepare_generation(db, spec(combined_daily_cap=3, area_daily_cap=2)).request
    )
    assert raised.status == DraftStatus.ALLOCATION_COMPLETE


def test_the_accounts_own_area_limit_names_the_limit_that_would_fit_the_month(db):
    """The area share is a second ceiling, and it is refused in its own words."""

    areas = {MonthKey(2026, 11): {f"A-{index:02d}": 6 for index in range(1, 6)}}
    refused = generate_draft(
        prepare_generation(
            db,
            spec(
                mode=DraftMode.CAPACITY_FALLBACK,
                cycle_start_month=None,
                area_required=areas,
                combined_daily_cap=2,
                area_daily_cap=1,
            ),
        ).request
    )
    assert refused.status == DraftStatus.INFEASIBLE
    evidence = refused.months[0].evidence[0]
    assert evidence.constraint == C_AGG_AREA_CAPACITY
    assert "at least 2 area visits a working day would fit them" in evidence.detail


def test_the_daily_activity_range_reaches_the_request(db):
    prepared = prepare_generation(db, spec(activities_per_day_min=3, activities_per_day_max=6))
    assert prepared.request.activities_per_day_min == 3
    assert prepared.request.activities_per_day_max == 6


def test_an_exact_count_is_the_range_whose_ends_are_equal(db):
    prepared = prepare_generation(db, spec(activities_per_day=4))
    assert prepared.request.activities_per_day_min == 4
    assert prepared.request.activities_per_day_max == 4


def test_a_request_that_says_nothing_records_no_activities(db):
    prepared = prepare_generation(db, spec())
    assert prepared.request.activities_per_day_min == 0
    assert prepared.request.activities_per_day_max == 0


def test_a_reversed_daily_range_is_refused_before_generation(db):
    with pytest.raises(GenerationInputError) as caught:
        prepare_generation(db, spec(activities_per_day_min=6, activities_per_day_max=3))
    assert caught.value.constraint == C_ACTIVITY_DAILY_RANGE_INVALID


def test_the_form_defaults_are_a_minimum_of_three_and_a_ceiling_above_it():
    assert DEFAULT_ACTIVITIES_PER_DAY == 3
    assert DEFAULT_ACTIVITIES_PER_DAY_MAX > DEFAULT_ACTIVITIES_PER_DAY


def test_a_generated_month_records_the_range_it_was_built_from(db):
    prepared = prepare_generation(db, spec(activities_per_day_min=3, activities_per_day_max=6))
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    counts: dict[date, int] = {}
    for row in draft.rows:
        if row.is_working_day:
            counts[row.day] = len(
                [entry for entry in row.entries if entry.kind.value == "activity"]
            )
    assert counts, "the month has working days"
    assert min(counts.values()) >= 3
    assert max(counts.values()) <= 6


def test_a_catalogue_that_cannot_meet_the_floor_is_refused_in_the_operators_words(db):
    connection = db
    for index in range(1, 13):
        connection.execute("DELETE FROM activity WHERE activity_id = ?", (f"ACT-{index:03d}",))
    connection.commit()
    masters.upsert_activity(
        connection,
        activity_id="ACT-001",
        category="System operation",
        description_en="Only one",
        description_kn=None,
        weight=1.0,
        monthly_min=0,
        monthly_max=20,
        repeat_gap_days=1,
    )
    prepared = prepare_generation(db, spec(activities_per_day_min=3, activities_per_day_max=6))
    with pytest.raises(InputError) as caught:
        generate_draft(prepared.request)
    assert caught.value.constraint == C_ACTIVITY_ALLOCATION_INFEASIBLE
    assert "between 3 and 6 activities a day" in caught.value.detail


# --- what a stored draft reproduces --------------------------------------


def test_the_range_survives_a_snapshot_round_trip(db):
    prepared = prepare_generation(db, spec(activities_per_day_min=3, activities_per_day_max=6))
    encoded = snapshot.encode_request(prepared.request)
    assert encoded["schema"] == snapshot.REQUEST_SNAPSHOT_SCHEMA
    assert encoded["activities_per_day_min"] == 3
    assert encoded["activities_per_day_max"] == 6

    restored = snapshot.decode_request(encoded)
    assert restored.activities_per_day_min == 3
    assert restored.activities_per_day_max == 6


def test_a_schema_three_snapshot_reads_as_an_equal_ended_range(db):
    """The count those drafts recorded is the range whose two ends are equal."""

    prepared = prepare_generation(db, spec(activities_per_day=3))
    encoded = snapshot.encode_request(prepared.request)
    old = dict(encoded)
    old["schema"] = 3
    old.pop("activities_per_day_min")
    old.pop("activities_per_day_max")
    old["activities_per_day"] = 3

    restored = snapshot.decode_request(old)
    assert restored.activities_per_day_min == 3
    assert restored.activities_per_day_max == 3


def test_the_visit_ceiling_is_inside_the_snapshot_that_already_held_the_config(db):
    prepared = prepare_generation(db, spec(combined_daily_cap=5, area_daily_cap=2))
    restored = snapshot.decode_request(snapshot.encode_request(prepared.request))
    assert restored.config.combined_daily_cap == 5
    assert restored.config.area_daily_cap == 2


# --- through the HTTP surface --------------------------------------------


def surface(tmp_path: Path):
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
    for index in range(1, 6):
        client.put(
            "/api/masters/area",
            json={
                "area_id": f"A-{index:02d}",
                "name_kn": f"ಪ್ರದೇಶ {index}",
                "monthly_min": 0,
                "monthly_max": 6,
                "revisit_gap_days": 7,
            },
        )
    for index in range(1, 13):
        client.put(
            "/api/masters/activity",
            json={
                "activity_id": f"ACT-{index:03d}",
                "category": "System operation",
                "description_en": f"Activity {index}",
                "description_kn": f"ಚಟುವಟಿಕೆ {index}",
                "weight": 1.0,
                "monthly_min": 0,
                "monthly_max": 20,
                "repeat_gap_days": 1,
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


def test_the_route_accepts_a_range_and_reports_the_one_it_used(tmp_path: Path):
    client = surface(tmp_path)
    answer = generate(client, activities_per_day_min=3, activities_per_day_max=6)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["activities_per_day_min"] == 3
    assert body["activities_per_day_max"] == 6

    reported = client.get(f"/api/generations/{body['run_id']}").json()
    counts: dict[str, int] = {}
    for row in reported["rows"]:
        counts[row["day"]] = len([entry for entry in row["entries"] if entry["kind"] == "activity"])
    working = [row["day"] for row in reported["rows"] if row["is_working_day"]]
    assert min(counts[day] for day in working) >= 3
    assert max(counts[day] for day in working) <= 6


def test_the_route_accepts_the_visit_ceilings_and_echoes_them(tmp_path: Path):
    client = surface(tmp_path)
    answer = generate(client, field_visits_per_day=5, area_visits_per_day=2)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["field_visits_per_day"] == 5
    assert body["area_visits_per_day"] == 2


def test_the_route_refuses_a_reversed_daily_range(tmp_path: Path):
    client = surface(tmp_path)
    answer = generate(client, activities_per_day_min=6, activities_per_day_max=3)
    assert answer.status_code == 400
    assert C_ACTIVITY_DAILY_RANGE_INVALID in answer.text


def test_the_route_refuses_an_area_share_above_the_field_ceiling(tmp_path: Path):
    client = surface(tmp_path)
    answer = generate(client, field_visits_per_day=2, area_visits_per_day=3)
    assert answer.status_code == 400
    assert C_DAILY_VISIT_CAP_INVALID in answer.text


# --- the form an operator actually sees ----------------------------------


def page() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "dailyworkreg"
        / "interfaces"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")


def client_script() -> str:
    return (
        Path(__file__).resolve().parents[1] / "dailyworkreg" / "interfaces" / "static" / "app.js"
    ).read_text(encoding="utf-8")


def test_the_generate_form_offers_a_minimum_and_a_maximum():
    html = page()
    assert 'id="gen-activities-min"' in html
    assert 'id="gen-activities-max"' in html
    assert 'value="3"' in html
    # The ceiling starts above the floor, or the range would be a flat count again.
    minimum = html.split('id="gen-activities-min"')[1].split(">")[0]
    maximum = html.split('id="gen-activities-max"')[1].split(">")[0]
    assert int(minimum.split('value="')[1].split('"')[0]) == 3
    assert int(maximum.split('value="')[1].split('"')[0]) > 3


def test_the_generate_form_offers_the_two_visit_ceilings():
    html = page()
    assert 'id="gen-visit-cap"' in html
    assert 'id="gen-area-cap"' in html
    assert "Total visits per working day" in html
    assert "Field visits per working day" not in html
    # The form states in one sentence that the area ceiling lives inside the total,
    # because a label reading "field visits" was taken to exclude area visits.
    assert "counted together" in html
    assert "Area visits per working day" in html


def test_the_client_sends_the_range_and_the_ceilings_it_was_given():
    script = client_script()
    for key in (
        "activities_per_day_min",
        "activities_per_day_max",
        "field_visits_per_day",
        "area_visits_per_day",
    ):
        assert key in script, key
    assert "activities_per_day" not in script.replace("activities_per_day_min", "").replace(
        "activities_per_day_max", ""
    )


def test_the_client_names_the_range_in_the_outcome_it_reports():
    script = client_script()
    assert "a working day" in script
