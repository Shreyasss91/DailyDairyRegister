"""Calendar projection: stored facts become per-date classifications.

These tests exist because the projection is where a date's meaning is decided, and a
wrong decision there is invisible later: the allocator will schedule into a Sunday and
the register will print it without complaint.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application.calendar_projection import (
    SAFETY_MEETING_WORDING,
    WeeklyOffRules,
    holiday_wording,
    load_weekly_off_rules,
    project_calendar,
    save_weekly_off_rules,
)
from dailyworkreg.application.draft_generation import CalendarDayKind, DayEntryKind
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "test.db")
    seed_approved_identities(connection)
    yield connection
    connection.close()


def holiday(db, day: date, name: str, description: str | None = None) -> None:
    cal.upsert_holiday(
        db,
        holiday_id=f"H-{day.isoformat()}",
        holiday_date=day,
        name=name,
        description=description,
        source="karnataka",
    )


def day_of(db, wanted: date, *, rules: WeeklyOffRules | None = None):
    projection = project_calendar(db, wanted, wanted, rules=rules)
    return projection.days[0]


# --- weekly offs ---------------------------------------------------------


def test_a_sunday_is_a_weekly_off(db):
    day = day_of(db, date(2026, 11, 1))  # a Sunday
    assert day.kind is CalendarDayKind.SUNDAY
    assert day.is_working_day is False
    assert "Sunday" in day.reasons_en


def test_the_second_saturday_is_a_weekly_off(db):
    day = day_of(db, date(2026, 11, 14))
    assert day.kind is CalendarDayKind.SECOND_SATURDAY
    assert day.is_working_day is False


def test_the_fourth_saturday_is_a_weekly_off(db):
    day = day_of(db, date(2026, 11, 28))
    assert day.kind is CalendarDayKind.FOURTH_SATURDAY
    assert day.is_working_day is False


def test_an_ordinary_saturday_is_a_working_day(db):
    day = day_of(db, date(2026, 11, 21))
    assert day.kind is CalendarDayKind.WORKING
    assert day.is_working_day is True


def test_every_other_saturday_in_the_month_is_working(db):
    projection = project_calendar(db, date(2026, 11, 1), date(2026, 11, 30))
    working = [d.day.day for d in projection.days if d.day.weekday() == 5 and d.is_working_day]
    assert working == [7, 21]


def test_a_weekly_off_rule_can_be_switched_off_and_is_persisted(db):
    save_weekly_off_rules(db, WeeklyOffRules(second_saturday=False))
    assert load_weekly_off_rules(db).second_saturday is False
    day = day_of(db, date(2026, 11, 14))
    assert day.is_working_day is True


def test_the_stored_rules_are_used_when_none_are_passed(db):
    save_weekly_off_rules(db, WeeklyOffRules(sundays=False))
    assert day_of(db, date(2026, 11, 1)).is_working_day is True


# --- holidays ------------------------------------------------------------


def test_a_general_holiday_is_not_a_working_day(db):
    holiday(db, date(2026, 11, 10), "Deepavali")
    day = day_of(db, date(2026, 11, 10))
    assert day.kind is CalendarDayKind.GENERAL_HOLIDAY
    assert day.is_working_day is False


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Republic Day", "Celebrated Republic Day in office"),
        ("Independence Day", "Celebrated Independence Day in office"),
        ("Gandhi Jayanti", "Celebrated Gandhi Jayanti in office"),
        ("Sir M Vishveshwaraiah Jayanthi", "Celebrated Sir M Vishveshwaraiah Jayanthi in office"),
        ("Deepavali", "Holiday - Deepavali"),
    ],
)
def test_holiday_wording_follows_the_requirement(name, expected):
    assert holiday_wording(name, None) == expected


def test_a_supplied_description_wins_over_the_generated_wording(db):
    holiday(db, date(2026, 11, 10), "Deepavali", "Deepavali celebration in office")
    day = day_of(db, date(2026, 11, 10))
    assert day.reasons_en == ("Deepavali celebration in office",)


def test_a_holiday_on_a_sunday_keeps_both_reasons(db):
    holiday(db, date(2026, 11, 1), "Kannada Rajyotsava")
    projection = project_calendar(db, date(2026, 11, 1), date(2026, 11, 1))
    day = projection.days[0]
    assert day.kind is CalendarDayKind.GENERAL_HOLIDAY
    assert day.reasons_en == ("Holiday - Kannada Rajyotsava", "Sunday")
    assert len(projection.conflicts) == 1
    assert "falls on a Sunday" in projection.conflicts[0]


def test_a_holiday_description_that_does_not_overlap_is_the_only_reason(db):
    holiday(db, date(2026, 11, 10), "Deepavali")
    day = day_of(db, date(2026, 11, 10))
    assert day.reasons_en == ("Holiday - Deepavali",)


# --- restricted holidays -------------------------------------------------


def test_a_date_that_is_not_recorded_is_a_working_day(db):
    # §4.1: a restricted holiday is an ordinary working day. Nothing is stored for it,
    # so this also pins that the projection invents nothing.
    day = day_of(db, date(2026, 11, 9))
    assert day.kind is CalendarDayKind.WORKING
    assert day.is_working_day is True
    assert day.reasons_en == ()


def test_no_restricted_holiday_classification_can_be_produced(db):
    projection = project_calendar(db, date(2025, 1, 1), date(2026, 12, 31))
    assert "RESTRICTED_HOLIDAY" not in {d.kind.name for d in projection.days}
    assert not any("restricted" in reason.lower() for d in projection.days for reason in d.reasons_en)


# --- leave ---------------------------------------------------------------


def test_leave_on_a_working_day_makes_it_non_working(db):
    cal.upsert_leave_record(
        db, leave_id="L-1", leave_date=date(2026, 11, 9), leave_type_id="CL"
    )
    day = day_of(db, date(2026, 11, 9))
    assert day.kind is CalendarDayKind.LEAVE
    assert day.is_working_day is False
    assert day.reasons_en == ("Casual Leave",)


def test_leave_on_a_sunday_keeps_both_reasons_and_reports_the_overlap(db):
    cal.upsert_leave_record(
        db, leave_id="L-1", leave_date=date(2026, 11, 1), leave_type_id="CL"
    )
    projection = project_calendar(db, date(2026, 11, 1), date(2026, 11, 1))
    day = projection.days[0]
    # A recorded leave is more specific than the standing weekly off, so it names the
    # day; the weekly off is still shown, because the office needs to know the Sunday
    # was not the reason the person was away.
    assert day.kind is CalendarDayKind.LEAVE
    assert day.reasons_en == ("Sunday", "Casual Leave")
    assert any("leave is recorded" in text for text in projection.conflicts)


def test_two_leave_records_on_one_day_are_both_kept(db):
    cal.upsert_leave_record(db, leave_id="L-1", leave_date=date(2026, 11, 9), leave_type_id="CL")
    cal.upsert_leave_record(db, leave_id="L-2", leave_date=date(2026, 11, 9), leave_type_id="FPL")
    day = day_of(db, date(2026, 11, 9))
    assert day.reasons_en == ("Casual Leave", "Full Pay Leave")


# --- overrides -----------------------------------------------------------


def test_an_override_can_make_a_holiday_a_working_day_and_keeps_the_fact(db):
    holiday(db, date(2026, 11, 10), "Deepavali")
    cal.upsert_override(
        db,
        override_id="O-1",
        override_date=date(2026, 11, 10),
        is_working_day=True,
        reason_en="Office function",
    )
    projection = project_calendar(db, date(2026, 11, 10), date(2026, 11, 10))
    day = projection.days[0]
    assert day.is_working_day is True
    assert day.kind is CalendarDayKind.WORKING
    assert "Holiday - Deepavali" in day.reasons_en
    assert "Office function" in day.reasons_en
    assert any("override" in text for text in projection.conflicts)


def test_an_override_can_make_a_working_day_non_working(db):
    cal.upsert_override(
        db,
        override_id="O-1",
        override_date=date(2026, 11, 9),
        is_working_day=False,
        reason_en="Local bandh",
    )
    day = day_of(db, date(2026, 11, 9))
    assert day.is_working_day is False
    assert day.kind is CalendarDayKind.OTHER_NON_WORKING
    assert day.reasons_en == ("Local bandh",)


# --- safety meetings -----------------------------------------------------


def test_a_safety_meeting_is_entered_on_every_monday(db):
    projection = project_calendar(db, date(2026, 11, 1), date(2026, 11, 30))
    mondays = sorted(
        entry.day for entry in projection.entries if entry.kind is DayEntryKind.SAFETY_MEETING
    )
    assert mondays == [date(2026, 11, d) for d in (2, 9, 16, 23, 30)]


def test_a_safety_meeting_survives_a_monday_holiday(db):
    holiday(db, date(2026, 11, 9), "Deepavali")
    projection = project_calendar(db, date(2026, 11, 9), date(2026, 11, 9))
    assert projection.days[0].is_working_day is False
    assert len(projection.entries) == 1
    assert projection.entries[0].kind is DayEntryKind.SAFETY_MEETING


def test_an_operator_safety_meeting_replaces_the_default_rather_than_doubling(db):
    cal.upsert_event(
        db,
        event_id="E-1",
        event_date=date(2026, 11, 9),
        title_en="Safety meeting on depot safety",
        event_kind=DayEntryKind.SAFETY_MEETING.value,
    )
    projection = project_calendar(db, date(2026, 11, 9), date(2026, 11, 9))
    assert len(projection.entries) == 1
    assert projection.entries[0].title_en == "Safety meeting on depot safety"


def test_the_safety_meeting_consumes_no_working_day_capacity(db):
    projection = project_calendar(db, date(2026, 11, 2), date(2026, 11, 2))
    assert projection.days[0].is_working_day is True
    assert projection.calendar().working_days == frozenset({date(2026, 11, 2)})


# --- stored events -------------------------------------------------------


def test_a_stored_event_is_projected_on_its_date(db):
    cal.upsert_event(
        db,
        event_id="E-2",
        event_date=date(2026, 11, 17),
        title_en="Meter reading drive",
        title_kn="ಮೀಟರ್ ಓದುವಿಕೆ",
    )
    projection = project_calendar(db, date(2026, 11, 17), date(2026, 11, 17))
    assert [e.title_en for e in projection.entries] == ["Meter reading drive"]
    assert projection.days[0].is_working_day is True


def test_the_working_day_set_matches_the_classification(db):
    holiday(db, date(2026, 11, 10), "Deepavali")
    projection = project_calendar(db, date(2026, 11, 1), date(2026, 11, 30))
    from_classification = {
        day.day for day in projection.days if day.is_working_day
    }
    assert projection.calendar().working_days == from_classification
    assert date(2026, 11, 10) not in from_classification


def test_the_projection_covers_every_date_in_the_range(db):
    projection = project_calendar(db, date(2026, 11, 15), date(2026, 12, 2))
    assert len(projection.days) == 18
    assert projection.days[0].day == date(2026, 11, 15)
    assert projection.days[-1].day == date(2026, 12, 2)


def test_the_safety_wording_is_the_approved_sentence():
    assert SAFETY_MEETING_WORDING == "Conducted safety meeting for employees"
