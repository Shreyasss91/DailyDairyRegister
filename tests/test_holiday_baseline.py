"""The Karnataka General Holiday baseline for 2025 and 2026, as verified.

The two expected tables below are transcriptions of the Government of Karnataka's
own General Holiday tables, not of this repository's CSV:

* 2025 — No.DPAR 16 HHL 2024, dated 21.11.2024, Notification-I: 19 dates.
* 2026 — No.DPAR 16 HHL 2025, dated 17.11.2025, Notification-I: 20 dates.

Each date is written with the weekday the notification prints beside it, so a
transcription error in either the date or the weekday is caught by the calendar
rather than by agreement with itself. The tests then hold the repository's CSV to
that table: the dates, the names, the year split, the rows the notification
deliberately leaves out, and the rows that belong to other lists and must never
appear here.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application.calendar_projection import project_calendar
from dailyworkreg.infrastructure.imports.tabular import read_source_file
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import open_database
from dailyworkreg.infrastructure.sqlite.imports import (
    accept_batch,
    get_batch,
    reject_batch,
    stage_source,
    staging_rows,
)
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "data" / "templates" / "02_karnataka_general_holidays_2025_2026.csv"

#: 2025 — No.DPAR 16 HHL 2024, dated 21.11.2024. (date, weekday, name)
NOTIFIED_2025: tuple[tuple[str, str, str], ...] = (
    ("2025-01-14", "Tuesday", "Uttarayana Punyakala, Makara Sankranti Festival"),
    ("2025-02-26", "Wednesday", "Maha Shivaratri"),
    ("2025-03-31", "Monday", "Khutub-E-Ramzan"),
    ("2025-04-10", "Thursday", "Mahaveera Jayanthi"),
    ("2025-04-14", "Monday", "Dr. B.R. Ambedkar Jayanthi"),
    ("2025-04-18", "Friday", "Good Friday"),
    ("2025-04-30", "Wednesday", "Basava Jayanthi/Akshaya Tritiya"),
    ("2025-05-01", "Thursday", "May Day"),
    ("2025-06-07", "Saturday", "Bakrid"),
    ("2025-08-15", "Friday", "Independence Day"),
    ("2025-08-27", "Wednesday", "Varasiddhi Vinayaka Vrata"),
    ("2025-09-05", "Friday", "Eid-Milad"),
    ("2025-10-01", "Wednesday", "Mahanavami/Ayudhapooja, Vijayadasami"),
    ("2025-10-02", "Thursday", "Gandhi Jayanthi"),
    ("2025-10-07", "Tuesday", "Maharshi Valmiki Jayanthi"),
    ("2025-10-20", "Monday", "Naraka Chaturdashi"),
    ("2025-10-22", "Wednesday", "Balipadyami, Deepavali"),
    ("2025-11-01", "Saturday", "Kannada Rajyothsava"),
    ("2025-12-25", "Thursday", "Christmas"),
)

#: 2026 — No.DPAR 16 HHL 2025, dated 17.11.2025. (date, weekday, name)
NOTIFIED_2026: tuple[tuple[str, str, str], ...] = (
    ("2026-01-15", "Thursday", "Uttarayana Punyakala, Makara Sankranti Festival"),
    ("2026-01-26", "Monday", "Republic Day"),
    ("2026-03-19", "Thursday", "Ugadi Festival"),
    ("2026-03-21", "Saturday", "Khutub-E-Ramzan"),
    ("2026-03-31", "Tuesday", "Mahaveera Jayanthi"),
    ("2026-04-03", "Friday", "Good Friday"),
    ("2026-04-14", "Tuesday", "Dr. B.R. Ambedkar Jayanthi"),
    ("2026-04-20", "Monday", "Basava Jayanthi, Akshaya Tritiya"),
    ("2026-05-01", "Friday", "May Day"),
    ("2026-05-28", "Thursday", "Bakrid"),
    ("2026-06-26", "Friday", "Last Day of Moharam"),
    ("2026-08-15", "Saturday", "Independence Day"),
    ("2026-08-26", "Wednesday", "Eid-Milad"),
    ("2026-09-14", "Monday", "Varasiddhi Vinayaka Vrata"),
    ("2026-10-02", "Friday", "Gandhi Jayanthi"),
    ("2026-10-20", "Tuesday", "Mahanavami, Ayudhapooja"),
    ("2026-10-21", "Wednesday", "Vijayadasami"),
    ("2026-11-10", "Tuesday", "Balipadyami, Deepavali"),
    ("2026-11-27", "Friday", "Kanakadasa Jayanthi"),
    ("2026-12-25", "Friday", "Christmas"),
)

#: Festivals both notifications say are **not** in the table, with the reason.
#: They are absent because the date already falls on a weekly off, so they must
#: never be added as rows: a weekly off is a calendar rule, not a holiday.
OMITTED_BECAUSE_NOTIFIED = {
    "2025-01-26": "Republic Day falls on a Sunday",
    "2025-03-30": "Ugadi Festival falls on a Sunday",
    "2025-07-06": "Last Day of Moharam falls on a Sunday",
    "2025-09-21": "Mahalaya Amavasye falls on a Sunday",
    "2025-11-08": "Kanakadasa Jayanthi falls on the second Saturday",
    "2026-02-15": "Maha Shivaratri falls on a Sunday",
    "2026-10-25": "Maharshi Valmiki Jayanthi falls on a Sunday",
    "2026-11-01": "Kannada Rajyotsava falls on a Sunday",
    "2026-11-08": "Naraka Chaturdashi falls on a Sunday",
    "2026-10-10": "Mahalaya Amavasya falls on the second Saturday",
}

#: Dates from lists that are not the General Holiday table. Each must stay out.
NOT_GENERAL_HOLIDAYS = {
    # Kodagu-district local holidays named in the 2025 notification.
    "2025-09-03": "Kail Muhuruth, Kodagu district only",
    "2025-10-18": "Tula Sankramana, Kodagu district only",
    "2025-12-05": "Huthri Festival, Kodagu district only",
    # Kodagu-district local holidays named in the 2026 notification.
    "2026-09-03": "Kail Muhuruth, Kodagu district only",
    "2026-10-18": "Tula Sankramana, Kodagu district only",
    "2026-11-26": "Huthari Festival, Kodagu district only",
    # Restricted Holidays: optional days an employee may take, never a closure.
    "2025-01-01": "New Year, restricted list",
    "2025-02-06": "Madhwanavami, restricted list",
    "2025-04-02": "Devara Dasimayya Jayanthi, restricted list",
    "2026-01-01": "New Year, restricted list",
    "2026-02-04": "Shab-e-Barath, restricted list",
    "2026-03-02": "Holi Festival, restricted list",
    "2026-11-24": "Guru Nanak Jayanthi, restricted list",
}


def _rows(path: Path = BASELINE) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _waited_for(year: tuple[tuple[str, str, str], ...], wanted: str) -> dict[str, str]:
    return {row[0]: row[2] for row in year if row[2] == wanted}


# --- the transcription itself --------------------------------------------


def test_the_baseline_holds_the_notified_dates_and_names() -> None:
    rows = _rows()
    assert len(rows) == 39

    for year, notified in (("2025", NOTIFIED_2025), ("2026", NOTIFIED_2026)):
        in_file = [row for row in rows if row["Calendar Year"] == year]
        assert len(in_file) == len(notified), year
        assert [(row["Date"], row["Name"]) for row in in_file] == [
            (notified_date, name) for notified_date, _, name in notified
        ], year


def test_every_notified_weekday_is_the_weekday_of_its_date() -> None:
    weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    for notified in (NOTIFIED_2025, NOTIFIED_2026):
        for written, weekday, name in notified:
            assert weekdays[date.fromisoformat(written).weekday()] == weekday, (
                f"{written} ({name}) is printed as {weekday}"
            )


def test_the_year_split_is_nineteen_and_twenty_with_no_repeated_date() -> None:
    rows = _rows()
    by_year: dict[str, list[str]] = {}
    for row in rows:
        by_year.setdefault(row["Calendar Year"], []).append(row["Date"])

    assert {year: len(dates) for year, dates in by_year.items()} == {"2025": 19, "2026": 20}
    for year, dates in by_year.items():
        assert len(set(dates)) == len(dates), year
        assert sorted(dates) == dates, f"{year} is stored in date order"
        for written in dates:
            assert date.fromisoformat(written).year == int(year), written


def test_the_notification_identifiers_travel_with_every_row() -> None:
    expectations = {
        "2025": ("No.DPAR 16 HHL 2024", "2024-11-21"),
        "2026": ("No.DPAR 16 HHL 2025", "2025-11-17"),
    }
    for row in _rows():
        number, issued = expectations[row["Calendar Year"]]
        assert row["Notification Number"] == number
        assert row["Notification Date"] == issued
        assert row["Source Document"].startswith(number)
        assert row["Description"] == ""


def test_rows_the_notification_leaves_out_are_absent() -> None:
    stored = {row["Date"] for row in _rows()}

    absent = sorted(stored & set(OMITTED_BECAUSE_NOTIFIED))
    assert absent == [], f"these fall on a weekly off and must not be rows: {absent}"


def test_no_row_comes_from_another_holiday_list() -> None:
    stored = {row["Date"] for row in _rows()}

    intruders = sorted(stored & set(NOT_GENERAL_HOLIDAYS))
    assert intruders == [], (
        "these belong to a district, restricted or bank list: "
        + "; ".join(f"{day} ({NOT_GENERAL_HOLIDAYS[day]})" for day in intruders)
    )


def test_the_muslim_observance_note_is_not_turned_into_a_second_row() -> None:
    """The note gives a Muslim employee the date of observance, not a second closure."""

    rows = _rows()
    by_date = {row["Date"]: row for row in rows}
    assert len(by_date) == len(rows)

    for notified in (NOTIFIED_2025, NOTIFIED_2026):
        for written, _, name in notified:
            if name in {"Bakrid", "Khutub-E-Ramzan", "Eid-Milad", "Last Day of Moharam"}:
                in_year = [
                    row
                    for row in rows
                    if row["Name"] == name
                    and row["Date"] == written
                ]
                same_name = [
                    row
                    for row in rows
                    if row["Name"] == name and row["Calendar Year"] == written[:4]
                ]
                assert len(in_year) == 1, f"{name} is not stored at its notified date"
                assert len(same_name) == 1, f"{name} appears {len(same_name)} times"


# --- the import path ------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "holidays.db")
    seed_approved_identities(connection)
    yield connection
    connection.close()


def test_the_baseline_stages_without_error_and_changes_nothing(db: sqlite3.Connection) -> None:
    batch, verdict = stage_source(db, "general_holiday", read_source_file(BASELINE))

    assert batch.row_count == 39
    assert batch.error_count == 0
    assert batch.warning_count == 0
    assert verdict.error_count == 0
    assert cal.list_holidays(db) == ()


def test_the_staged_rows_keep_the_notification_provenance(db: sqlite3.Connection) -> None:
    batch, _ = stage_source(db, "general_holiday", read_source_file(BASELINE))

    rows = staging_rows(db, batch.batch_id)
    assert len(rows) == 39
    for row in rows:
        assert row.severity == "ok"
        assert row.decision == "include"
        # The extra columns are not part of the judged payload; the raw source row
        # is what keeps them, which is the provenance boundary.
        assert set(row.raw) >= {
            "Date",
            "Name",
            "Calendar Year",
            "Notification Number",
            "Notification Date",
            "Source Document",
        }
        assert row.raw["Notification Number"].startswith("No.DPAR 16 HHL")
        assert row.payload["holiday_date"] == row.raw["Date"]


def test_acceptance_stores_both_years_as_karnataka_general_holidays(
    db: sqlite3.Connection,
) -> None:
    batch, _ = stage_source(db, "general_holiday", read_source_file(BASELINE))
    result = accept_batch(db, batch.batch_id)

    assert (result.inserted, result.updated, result.excluded) == (39, 0, 0)
    stored = cal.list_holidays(db)
    assert len(stored) == 39
    assert {holiday.source for holiday in stored} == {"karnataka"}
    assert {holiday.holiday_date.year for holiday in stored} == {2025, 2026}

    expected = {
        (date.fromisoformat(written), name)
        for notified in (NOTIFIED_2025, NOTIFIED_2026)
        for written, _, name in notified
    }
    assert {(holiday.holiday_date, holiday.name) for holiday in stored} == expected
    assert all(holiday.description is None for holiday in stored)


def test_a_second_upload_replaces_the_year_rather_than_doubling_it(
    db: sqlite3.Connection,
) -> None:
    first, _ = stage_source(db, "general_holiday", read_source_file(BASELINE))
    accept_batch(db, first.batch_id)

    second, _ = stage_source(db, "general_holiday", read_source_file(BASELINE))
    accept_batch(db, second.batch_id)

    assert len(cal.list_holidays(db)) == 39
    assert len(cal.list_holidays(db, year=2026)) == 20
    assert len(cal.list_holidays(db, year=2025)) == 19


def test_rejecting_the_baseline_leaves_the_calendar_empty(db: sqlite3.Connection) -> None:
    batch, _ = stage_source(db, "general_holiday", read_source_file(BASELINE))
    reject_batch(db, batch.batch_id, note="wrong year")

    assert get_batch(db, batch.batch_id).status == "rejected"
    assert cal.list_holidays(db) == ()


def test_an_accepted_holiday_become_a_non_working_day_without_duplicating_a_sunday(
    db: sqlite3.Connection,
) -> None:
    batch, _ = stage_source(db, "general_holiday", read_source_file(BASELINE))
    accept_batch(db, batch.batch_id)

    projection = project_calendar(db, date(2026, 1, 11), date(2026, 1, 26))
    by_date = {day.day: day for day in projection.days}

    republic_day = by_date[date(2026, 1, 26)]
    assert republic_day.is_working_day is False
    assert republic_day.kind.value == "general_holiday"
    assert any("Republic Day" in reason for reason in republic_day.reasons_en)

    # 2026-01-11 is a Sunday: a weekly off, and it is not also a holiday row.
    sunday = by_date[date(2026, 1, 11)]
    assert sunday.is_working_day is False
    assert sunday.kind.value == "sunday"


def test_the_baseline_file_itself_is_the_one_that_was_verified() -> None:
    """The template and the office copy must hold the same dates and names.

    The office copy is local and is not always present; when it is, it may only be
    the same table in a different date format.
    """

    office = REPO / "data" / "record_A" / "02_karnataka_general_holidays_2025_2026.csv"
    if not office.exists():
        pytest.skip("this checkout has no office copy")

    def normalised(path: Path) -> list[tuple[str, str, str]]:
        out = []
        for row in _rows(path):
            written = row["Date"]
            day = (
                date.fromisoformat(written)
                if len(written) == 10 and written[4] == "-"
                else date.fromisoformat(written[6:10] + "-" + written[3:5] + "-" + written[:2])
            )
            out.append((day.isoformat(), row["Name"], row["Calendar Year"]))
        return out

    assert normalised(office) == normalised(BASELINE)
