"""Exporting an issued month: the workbook and the printed page read one stored record.

Both exports are proved against the snapshot the finalisation wrote, not against a fresh
generation: the tests build a register, close a month, then compare every row of the
register with ``month_finalisation.content_json``. They then change the live master data,
export again, and require the document to be unchanged, because a register issued in
November must still read as it was approved after the office has imported more masters.

The document itself is deliberately small: three columns, one row per calendar date, and
the identification moved to a page of its own. So the tests are about exactly that — that
a date is never dropped, that a date with several things on it stays one row, that the
items of one date are joined in the one order the record states, and that nothing which
identifies the issued version has been lost with the columns that used to hold it.

Nothing here asserts incidental formatting. A test that pinned a column width would fail on
a change that made the document better, so widths, fonts and page setup are checked only
for being present.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from dailyworkreg.application import finalisation as finalise
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application import revisions
from dailyworkreg.application.draft_generation import DraftMode, DraftStatus, generate_draft
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.application.register_edits import add_entry
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters, revision_state
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities
from dailyworkreg.interfaces import documents
from dailyworkreg.interfaces.api import create_app
from dailyworkreg.interfaces.documents import XLSX_MEDIA_TYPE
from fastapi.testclient import TestClient
from openpyxl import load_workbook

XLSX_HEADER_ROW = documents.REGISTER_HEADER_ROW
XLSX_REGISTER_HEADERS = ("Sl No.", "Date", "Activity")

#: What an empty date says. Spelled out in the tests rather than imported, so a change to
#: the register's own choice of mark has to be a deliberate one.
EMPTY = "\u2014"


@pytest.fixture()
def database(tmp_path: Path) -> Path:
    path = tmp_path / "export.db"
    connection = open_database(path)
    try:
        seed_approved_identities(connection)  # the approved feeders the DTCs point at
        for index in range(1, 7):
            masters.upsert_dtc(
                connection,
                dtc_id=f"SCD-{index}",
                name=f"Test DTC {index}",
                dtc_class="DEPT",
                kva=100.0 + index,
                canonical_feeder_id=f"F-0{(index % 2) + 1}",
            )
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-11-10",
            holiday_date=date(2026, 11, 10),
            name="Recorded holiday",
            source="karnataka",
        )
        for index in range(1, 3):
            masters.upsert_activity(
                connection,
                activity_id=f"A-{index:03d}",
                category="Inspection",
                description_en=f"Activity {index}",
                description_kn=f"ಚಟುವಟಿಕೆ {index}",
            )
    finally:
        connection.close()
    return path


@pytest.fixture()
def db(database: Path) -> sqlite3.Connection:
    connection = open_database(database)
    yield connection
    connection.close()


@pytest.fixture()
def client(database: Path) -> TestClient:
    return TestClient(create_app(database))


def spec(
    *,
    start: date = date(2026, 11, 1),
    end: date = date(2026, 11, 30),
    seed: int = 7,
    cycle_months: int = 1,
    entry_language: str = "both",
) -> GenerationSpec:
    """A one-month cycle. Bilingual, because this module pins both texts at once.

    The platform default is now English; this fixture asks for the bilingual register it
    asserts about, so a test that is about keeping Kannada beside English keeps testing
    that rather than the default.
    """

    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=seed,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=cycle_months,
        cycle_visits_per_asset=1,
        entry_language=entry_language,
    )


def stored(db: sqlite3.Connection, **kwargs) -> str:
    prepared = prepare_generation(db, spec(**kwargs))
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    return generations.save_run(db, prepared, draft)


def review_every_date(db: sqlite3.Connection, run_id: str, *, month: str = "2026-11") -> None:
    for item in revisions.review_state(db, run_id)["dates"]:
        if not item["date"].startswith(month):
            continue
        revisions.mark_reviewed(
            db,
            run_id,
            date.fromisoformat(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )


def issued(
    db: sqlite3.Connection,
    client: TestClient,
    *,
    run_id: str | None = None,
    month: str = "2026-11",
    **kwargs,
) -> tuple[str, dict]:
    """A reviewed month closed, and its record read back through the surface."""

    closing = kwargs.pop("finalise", {})
    run_id = run_id or stored(db, **kwargs)
    review_every_date(db, run_id, month=month)
    record = finalise.finalise_month(db, run_id, month, **closing)
    answer = client.get(f"/api/finalisations/{record['finalisation_id']}")
    assert answer.status_code == 200
    return run_id, answer.json()


def workbook(client: TestClient, finalisation_id: str):
    answer = client.get(f"/api/finalisations/{finalisation_id}/export.xlsx")
    assert answer.status_code == 200, answer.text
    return answer, load_workbook(BytesIO(answer.content))


def register_rows(book) -> list[dict[str, object]]:
    """The register sheet as rows, keyed by the headings it carries."""

    sheet = book[documents.REGISTER_SHEET]
    headings = [
        sheet.cell(row=XLSX_HEADER_ROW, column=index).value
        for index in range(1, 4)
    ]
    assert tuple(headings) == XLSX_REGISTER_HEADERS
    rows = []
    for values in sheet.iter_rows(
        min_row=XLSX_HEADER_ROW + 1, max_col=3, values_only=True
    ):
        if not any(value not in (None, "") for value in values):
            continue
        rows.append(
            {"serial": values[0], "date": values[1], "activity": values[2]}
        )
    return rows


def stamped(row: dict[str, object]) -> str:
    """The date a row carries, back as the stored ISO day it came from."""

    value = row["date"]
    return value if isinstance(value, str) else value.strftime("%Y-%m-%d")


def printed_rows(markup: str) -> list[tuple[str, str, str]]:
    """Every row of the printed register, as (serial, date, activity)."""

    body = markup.split('<table class="register">')[1].split("</table>")[0]
    body = body.split("<tbody>")[1]
    rows = []
    for block in body.split("<tr>")[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", block, flags=re.S)
        if len(cells) != 3:
            continue
        rows.append(tuple(cell.strip() for cell in cells))
    return rows


def printed_table(markup: str) -> str:
    """The register table alone, so its columns can be examined without the rest."""

    return markup.split('<table class="register">')[1].split("</table>")[0]


#: What a calendar reason is called beside the date, mirrored here so the test states the
#: rule instead of asking the code what it does.
REASON_LABELS = {
    "sunday": "Weekly off",
    "second_saturday": "Holiday",
    "fourth_saturday": "Holiday",
    "other_weekly_off": "Weekly off",
    "general_holiday": "Holiday",
    "leave": "Leave",
    "other_non_working": "Non-working day",
}

#: The kinds of closed day the office staffs for maintenance work, and the line they carry.
STAFFED_KINDS = {
    "sunday",
    "second_saturday",
    "fourth_saturday",
    "other_weekly_off",
    "general_holiday",
}
STAFFED_NOTE = "Allocated operational and maintenance works to be done on that day to staff"

#: What a visit says, by the kind of asset it named. Only the DTC entry carries the
#: roster identifier: transformer work is filed and defended by that number.
VISIT_PHRASES = {
    "DTC": (
        "Inspected {name} [{asset_id}] for transformer loading, oil level and earthing, "
        "and checked the consumer supply"
    ),
    "FEEDER": (
        "Patrolled {name} feeder for loading, tripping history and tree clearance along the route"
    ),
    "AREA": (
        "Inspected {name} area for DTC loading, street-light complaints and service connections"
    ),
}

GENERIC_VISIT_PHRASE = "Visited {name} and checked the supply and complaints there"


def in_language(english: str, kannada: str | None, language: str) -> str:
    english = (english or "").strip()
    kannada = (kannada or "").strip()
    if language == "kn":
        return kannada or english
    if language == "en":
        return english or kannada
    return f"{english} ({kannada})" if english and kannada else (english or kannada)


def expected_activity(content: dict, row: dict) -> str:
    """What one stored date must read as, worked out from the record alone.

    This mirrors the documented rule — visits as a sentence naming the work, then activities
    and other entries, then calendar reasons classified by the kind of day, then warnings,
    joined with a comma and a space, in the language the record was issued in, and an em
    dash when the date holds nothing — rather than calling the code under test, so a change
    of wording, order or separator has to be made deliberately in two places.
    """

    language = content.get("entry_language") or "both"
    asset_types = content.get("asset_types") or {}
    label = REASON_LABELS.get(row["kind"], "")

    items: list[str] = []
    for visit in row["visits"]:
        name = in_language(visit[2], visit[3] if len(visit) > 3 else "", language)
        template = VISIT_PHRASES.get(asset_types.get(visit[0], ""), GENERIC_VISIT_PHRASE)
        items.append(template.format(name=name, asset_id=visit[0]))
    for entry in row["entries"]:
        items.append(in_language(entry[2], entry[3] if len(entry) > 3 else "", language))
    if language == "en":
        reasons = row["reasons_en"] or row["reasons_kn"]
    elif language == "kn":
        reasons = row["reasons_kn"] or row["reasons_en"]
    else:
        reasons = row["reasons_en"] + [
            reason for reason in row["reasons_kn"] if reason not in row["reasons_en"]
        ]
    seen: set[str] = set()
    kept: list[str] = []
    for reason in reasons:
        if reason and reason not in seen:
            seen.add(reason)
            kept.append(reason)
    # The classification belongs to the date, so it is written once beside the first
    # reason rather than on every one of them.
    for position, reason in enumerate(kept):
        items.append(f"{reason} ({label})" if label and position == 0 else reason)
    # A closed day the office staffed carries the line saying so, after its reasons.
    if row["kind"] in STAFFED_KINDS:
        items.append(STAFFED_NOTE)
    for constraint, detail in row["warnings"]:
        if constraint and detail:
            items.append(f"Warning: {detail} [{constraint}]")
        elif constraint:
            items.append(f"Warning [{constraint}]")
        elif detail:
            items.append(f"Warning: {detail}")
    items = [item for item in items if item]
    return ", ".join(items) if items else EMPTY


def head(book) -> list[tuple]:
    """The heading rows, as values, for comparing two exports as documents."""

    return list(
        book[documents.REGISTER_SHEET].iter_rows(
            min_row=1, max_row=XLSX_HEADER_ROW, max_col=3, values_only=True
        )
    )


# --- the mixed register export, kept as it was -----------------------------


def test_bulk_export_contains_each_month_workbook_and_one_combined_print_document(client, db):
    run_id = stored(
        db,
        start=date(2026, 11, 1),
        end=date(2026, 12, 31),
        cycle_months=2,
    )
    register_id = revisions.register_of(db, run_id)
    review_every_date(db, run_id, month="2026-11")
    review_every_date(db, run_id, month="2026-12")
    november = finalise.finalise_month(db, run_id, "2026-11")
    december = finalise.finalise_month(db, run_id, "2026-12")

    answer = client.get(f"/api/registers/{register_id}/exports.zip")

    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("application/zip")
    assert "attachment" in answer.headers["content-disposition"]
    with ZipFile(BytesIO(answer.content)) as package:
        names = set(package.namelist())
        november_xlsx = "excel/daily-dairy-register-2026-11-v1.xlsx"
        december_xlsx = "excel/daily-dairy-register-2026-12-v1.xlsx"
        combined_html = (
            "print-ready/daily-dairy-register-2026-11-to-2026-12-combined.html"
        )
        assert {november_xlsx, december_xlsx, combined_html, "README.txt"} <= names

        for path in (november_xlsx, december_xlsx):
            book = load_workbook(BytesIO(package.read(path)), read_only=True)
            assert book.sheetnames == ["Register", "Metadata"]
            book.close()

        markup = package.read(combined_html).decode("utf-8")
        assert markup.count('<article class="sheet">') == 2
        assert "Issued version 1 of November 2026" in markup
        assert "Issued version 1 of December 2026" in markup
        assert "Back to the tool" not in markup
        assert november["finalisation_id"] in markup
        assert december["finalisation_id"] in markup
        assert ".sheet + .sheet" in markup
        instructions = package.read("README.txt").decode("utf-8")
        assert "one combined HTML document" in instructions
        assert "browser's own print dialog" in instructions


def test_bulk_export_uses_only_issued_months(client, db):
    run_id = stored(
        db,
        start=date(2026, 11, 1),
        end=date(2026, 12, 31),
        cycle_months=2,
    )
    register_id = revisions.register_of(db, run_id)
    review_every_date(db, run_id, month="2026-11")
    finalise.finalise_month(db, run_id, "2026-11")

    answer = client.get(f"/api/registers/{register_id}/exports.zip")
    assert answer.status_code == 200, answer.text
    with ZipFile(BytesIO(answer.content)) as package:
        names = package.namelist()
        assert len([name for name in names if name.startswith("excel/")]) == 1
        assert "excel/daily-dairy-register-2026-11-v1.xlsx" in names
        assert not any("2026-12" in name for name in names)
        markup_name = next(name for name in names if name.endswith("-combined.html"))
        markup = package.read(markup_name).decode("utf-8")
        assert markup.count('<article class="sheet">') == 1
        assert "NOVEMBER 2026" in markup
        assert "DECEMBER 2026" not in markup
        instructions = package.read("README.txt").decode("utf-8")
        assert "2026-12" in instructions
        assert "omitted because they have no issued finalisation" in instructions


def test_bulk_export_refuses_a_register_without_an_issued_month(client, db):
    unissued_run = stored(db)
    unissued_register = revisions.register_of(db, unissued_run)
    refused = client.get(f"/api/registers/{unissued_register}/exports.zip")
    assert refused.status_code == 400
    assert refused.json()["error"]["constraint"] == "register_has_no_issued_months"


# --- the workbook ----------------------------------------------------------


def test_the_workbook_is_a_real_xlsx_file_for_the_requested_version(client, db):
    _, record = issued(db, client)

    answer, book = workbook(client, record["finalisation_id"])

    assert answer.headers["content-type"].startswith(XLSX_MEDIA_TYPE)
    assert "attachment" in answer.headers["content-disposition"]
    assert answer.content[:2] == b"PK"  # a real zip container, not a placeholder
    assert book.sheetnames == ["Register", "Metadata"]


def test_the_register_sheet_carries_exactly_three_columns(client, db):
    _, record = issued(db, client)

    _, book = workbook(client, record["finalisation_id"])
    sheet = book[documents.REGISTER_SHEET]

    headings = [sheet.cell(row=XLSX_HEADER_ROW, column=index).value for index in (1, 2, 3)]
    assert headings == ["Sl No.", "Date", "Activity"]
    # Nothing else was left beside them: the columns that used to hold weekday, kind,
    # reasons, visits and warnings are gone, and their content moved into the one cell.
    assert sheet.cell(row=XLSX_HEADER_ROW, column=4).value in (None, "")


def test_every_stored_date_reaches_the_workbook_in_the_stored_order(client, db):
    _, record = issued(db, client)

    _, book = workbook(client, record["finalisation_id"])

    stored_days = list(record["content"]["rows"])
    assert [stamped(row) for row in register_rows(book)] == stored_days
    assert len(stored_days) == 30  # a whole calendar month, not only the working days


def test_the_serial_numbers_run_from_one_without_a_gap(client, db):
    _, record = issued(db, client)

    _, book = workbook(client, record["finalisation_id"])
    rows = register_rows(book)

    assert [row["serial"] for row in rows] == list(
        range(1, len(record["content"]["rows"]) + 1)
    )


def test_a_date_with_nothing_on_it_keeps_its_place_and_says_so(client, db):
    _, record = issued(db, client)
    stored_rows = record["content"]["rows"]
    empty_day = next(
        day
        for day, row in stored_rows.items()
        if not row["visits"] and not row["entries"] and not row["reasons_en"]
    )
    _, book = workbook(client, record["finalisation_id"])

    row = next(item for item in register_rows(book) if stamped(item) == empty_day)

    # Present, in its place, and marked with the register's own em dash rather than left
    # blank so that a reader cannot mistake the row for a printing fault.
    assert row["activity"] == EMPTY


def test_every_stored_value_is_what_the_register_row_shows(client, db):
    _, record = issued(db, client)
    stored_rows = record["content"]["rows"]

    _, book = workbook(client, record["finalisation_id"])
    rows = {stamped(row): row for row in register_rows(book)}

    for day, stored_row in stored_rows.items():
        assert rows[day]["activity"] == expected_activity(record["content"], stored_row)


def test_every_stored_visit_reaches_the_cell_by_name_and_dtcs_by_number(client, db):
    _, record = issued(db, client)
    stored_rows = record["content"]["rows"]
    visited = {day: row for day, row in stored_rows.items() if row["visits"]}
    assert visited, "the fixture is meant to visit at least one DTC"

    _, book = workbook(client, record["finalisation_id"])
    rows = {stamped(row): row for row in register_rows(book)}
    asset_types = record["content"].get("asset_types") or {}

    for day, stored_row in visited.items():
        cell = rows[day]["activity"]
        for visit in stored_row["visits"]:
            # The name always reaches the cell. A DTC's roster number reaches it too,
            # because transformer work is filed and defended by that number; an area's or
            # a feeder's identifier does not, because those are named by name.
            assert visit[2] in cell
            printed = f"[{visit[0]}]" in cell
            assert printed == (asset_types.get(visit[0]) == "DTC"), visit[0]


def test_several_activities_on_one_date_share_one_row_and_one_cell(client, db):
    run_id = stored(db)
    # Two entries on one date, so "one row per date" cannot silently drop the second.
    for title in ("Pole inspection", "Meter reading"):
        edit = add_entry(
            db, run_id, day=date(2026, 11, 5), kind="activity", title_en=title
        )
        run_id = edit.run_id
    _, record = issued(db, client, run_id=run_id)
    fifth = record["content"]["rows"]["2026-11-05"]
    assert len(fifth["entries"]) >= 2

    _, book = workbook(client, record["finalisation_id"])
    rows = [item for item in register_rows(book) if stamped(item) == "2026-11-05"]

    assert len(rows) == 1
    assert "Pole inspection" in rows[0]["activity"]
    assert "Meter reading" in rows[0]["activity"]
    assert rows[0]["activity"] == expected_activity(record["content"], fifth)


def test_a_kannada_description_is_kept_beside_its_english_one(client, db):
    run_id = stored(db)
    edit = add_entry(
        db,
        run_id,
        day=date(2026, 11, 6),
        kind="activity",
        title_en="Line inspection",
        title_kn="ಲೈನ್ ಪರಿಶೀಲನೆ",
    )
    _, record = issued(db, client, run_id=edit.run_id)

    _, book = workbook(client, record["finalisation_id"])
    row = next(item for item in register_rows(book) if stamped(item) == "2026-11-06")

    # Printed as stored and beside the English text, never translated or transliterated.
    assert "Line inspection" in row["activity"]
    assert "ಲೈನ್ ಪರಿಶೀಲನೆ" in row["activity"]
    assert row["activity"] == expected_activity(record["content"], record["content"]["rows"]["2026-11-06"])


def test_a_calendar_reason_and_a_warning_stay_visible_in_the_cell(client, db):
    _, record = issued(db, client)
    stored_rows = record["content"]["rows"]
    with_reasons = {
        day: row for day, row in stored_rows.items() if row["reasons_en"]
    }
    assert with_reasons, "November 2026 is meant to carry at least one holiday"

    _, book = workbook(client, record["finalisation_id"])
    rows = {stamped(row): row for row in register_rows(book)}

    for day, stored_row in with_reasons.items():
        cell = rows[day]["activity"]
        for reason in stored_row["reasons_en"]:
            # Classified, so a bare "Sunday" cannot be read as the date rather than as
            # the reason it carries no work and cannot be mistaken for an activity.
            assert f"{reason} (" in cell
            assert "Calendar:" not in cell
        assert any(
            word in cell for word in ("Weekly off", "Holiday", "Leave", "Non-working day")
        )

    warned = {
        day: row for day, row in stored_rows.items() if row["warnings"]
    }
    for day, stored_row in warned.items():
        cell = rows[day]["activity"]
        for constraint, detail in stored_row["warnings"]:
            assert detail in cell
            assert f"[{constraint}]" in cell


def test_the_metadata_sheet_identifies_this_version(client, db):
    _, record = issued(db, client, finalise={"note": "November closed"})

    _, book = workbook(client, record["finalisation_id"])
    sheet = book[documents.METADATA_SHEET]
    values = {
        sheet.cell(row=row, column=1).value: sheet.cell(row=row, column=2).value
        for row in range(3, 22)
    }

    assert values["Issued version"] == record["sequence"] == 1
    assert values["Revision closed"] == record["revision_number"]
    assert values["Finalisation id"] == record["finalisation_id"]
    assert values["Register"] == record["register_id"]
    assert values["Content fingerprint"] == record["fingerprint"]
    assert values["Master version at finalisation"] == record["master_version"]
    assert values["Note"] == "November closed"
    assert values["Month"] == "November 2026"
    assert values["Standing"] == "In force"


def test_the_metadata_sheet_keeps_the_findings_and_readiness_of_the_issue(client, db):
    _, record = issued(db, client)

    _, book = workbook(client, record["finalisation_id"])
    text = " ".join(
        str(value)
        for row in book[documents.METADATA_SHEET].iter_rows(values_only=True)
        for value in row
        if value is not None
    )

    assert "Master readiness measured at finalisation" in text
    assert "Recorded obligations" in text
    # Every readiness item the record holds is named on the sheet.
    for item in record["readiness"]:
        assert item["label"] in text


def test_the_metadata_sheet_counts_how_the_visits_were_placed(client, db):
    _, record = issued(db, client)

    _, book = workbook(client, record["finalisation_id"])
    values = {
        (sheet_row[0], sheet_row[1])
        for sheet_row in book[documents.METADATA_SHEET].iter_rows(
            min_row=1, max_col=2, values_only=True
        )
    }
    visits = sum(len(row["visits"]) for row in record["content"]["rows"].values())
    allocated = sum(
        1
        for row in record["content"]["rows"].values()
        for visit in row["visits"]
        if visit[1] == "allocated"
    )

    assert ("Allocated", allocated) in values
    assert allocated <= visits


def test_the_workbook_is_set_up_to_print(client, db):
    _, record = issued(db, client)

    _, book = workbook(client, record["finalisation_id"])
    sheet = book[documents.REGISTER_SHEET]

    assert sheet.print_title_rows.replace("$", "") == f"{XLSX_HEADER_ROW}:{XLSX_HEADER_ROW}"
    assert sheet.freeze_panes  # the headings stay on screen while scrolling
    assert sheet.page_setup.orientation == "portrait"
    assert int(sheet.page_setup.paperSize) == 9  # A4


def test_the_filename_is_safe_and_says_which_version_it_is(client, db):
    run_id, first = issued(db, client)
    correction = finalise.create_correction_revision(db, run_id, reason="a fix")
    review_every_date(db, correction["correction_run_id"])
    second = finalise.finalise_month(db, correction["correction_run_id"], "2026-11")

    first_name = workbook(client, first["finalisation_id"])[0].headers[
        "content-disposition"
    ]
    second_name = workbook(client, second["finalisation_id"])[0].headers[
        "content-disposition"
    ]

    for name in (first_name, second_name):
        filename = name.split('filename="')[1].rstrip('"')
        assert set(filename) <= set(
            "abcdefghijklmnopqrstuvwxyz0123456789-_."
        ), filename
        assert filename.startswith("daily-dairy-register-2026-11")
    assert "-v1.xlsx" in first_name
    assert "-v2.xlsx" in second_name


# --- the printed register --------------------------------------------------


def test_the_printed_register_carries_exactly_three_columns(client, db):
    _, record = issued(db, client)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text
    table = printed_table(markup)

    head_block = table.split("<thead>")[1].split("</thead>")[0]
    headings = re.findall("<th[^>]*>([^<]*)</th>", head_block)
    assert [item.strip() for item in headings] == ["Sl No.", "Date", "Activity"]
    # Exactly one column definition per heading, so nothing else can be printed beside
    # them: no weekday, no status, no provenance and no asset type.
    assert table.count("<col ") == 3


def test_the_month_is_headed_centrally_and_named_in_capitals(client, db):
    _, record = issued(db, client)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text
    head = markup.split('<header class="register-head">')[1].split("</header>")[0]

    assert "DAILY DAIRY REGISTER" in head
    assert "NOVEMBER 2026" in head
    # The heading is the whole of the masthead: the register identifies itself on its own
    # page rather than in the corner of the first one.
    assert "Issued version" not in head
    assert record["finalisation_id"] not in head
    assert "text-align: center" in markup.split("header.register-head {")[1].split("}")[0]


def test_every_stored_date_is_printed_exactly_once_in_order(client, db):
    _, record = issued(db, client)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text
    rows = printed_rows(markup)

    stored_days = list(record["content"]["rows"])
    assert [row[1] for row in rows] == stored_days
    assert len(rows) == len(set(stored_days)) == 30
    assert [row[0] for row in rows] == [str(index) for index in range(1, 31)]


def test_each_printed_row_shows_what_its_date_recorded(client, db):
    _, record = issued(db, client)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text
    rows = {row[1]: row[2] for row in printed_rows(markup)}

    for day, stored_row in record["content"]["rows"].items():
        assert rows[day] == expected_activity(record["content"], stored_row)


def test_a_date_with_several_things_on_it_is_still_one_printed_row(client, db):
    run_id = stored(db)
    for title in ("Pole inspection", "Meter reading"):
        edit = add_entry(
            db, run_id, day=date(2026, 11, 5), kind="activity", title_en=title
        )
        run_id = edit.run_id
    _, record = issued(db, client, run_id=run_id)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text
    fifth = [row for row in printed_rows(markup) if row[1] == "2026-11-05"]

    assert len(fifth) == 1
    cell = fifth[0][2]
    assert "Pole inspection" in cell
    assert "Meter reading" in cell
    assert ", " in cell


def test_the_identification_page_starts_on_a_page_of_its_own(client, db):
    _, record = issued(db, client, finalise={"note": "November closed"})

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text
    after_table = markup.split('<table class="register">')[1].split("</table>")[1]
    metadata = markup.split('<section class="metadata">')[1]

    # The identification is its own sheet, which is what puts it on a fresh page: the
    # break that starts a new sheet is the rule every engine honours, whereas a break on a
    # section that follows a table is not. So the sheet closes after the last date and a
    # new one opens for the identification, and no separate section break is stacked on
    # top of it - two breaks in a row would leave a blank page between them.
    assert markup.split('<table class="register">')[1].split("</article>")[0].count(
        '<article class="sheet identification">'
    ) == 0
    assert after_table.strip().startswith("</article>")
    assert '<article class="sheet identification">' in after_table
    assert after_table.index("</article>") < after_table.index(
        '<article class="sheet identification">'
    )
    assert metadata.index("Issued version identification") < metadata.index(
        record["finalisation_id"]
    )
    assert ".sheet + .sheet { break-before: page; page-break-before: always; }" in markup
    assert "section.metadata { break-before: page" not in markup
    # Metadata is on that page once, not repeated as a footer under the last dates.
    assert markup.count(record["fingerprint"]) == 1
    assert markup.count(record["finalisation_id"]) == 1


def test_the_identification_page_keeps_every_recorded_identity(client, db):
    _, record = issued(db, client, finalise={"note": "November closed"})

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text

    assert "Issued version" in markup
    assert record["fingerprint"] in markup
    assert str(record["master_version"]) in markup
    assert "Revision closed" in markup
    assert "Note recorded when this version was issued" in markup
    assert "November closed" in markup
    assert "In force" in markup


def test_a_long_description_is_wrapped_rather_than_cut_off(client, db):
    run_id = stored(db)
    long_title = "Inspection of the 11 kV feeder along the industrial estate road " * 3
    edit = add_entry(
        db, run_id, day=date(2026, 11, 7), kind="activity", title_en=long_title.strip()
    )
    _, record = issued(db, client, run_id=edit.run_id)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text
    cell = next(row[2] for row in printed_rows(markup) if row[1] == "2026-11-07")

    # Nothing was truncated, shortened or replaced with an ellipsis on the way out.
    assert long_title.strip() in cell
    assert "\u2026" not in cell
    assert "overflow-wrap: anywhere" in markup.split("td.activity {")[1].split("}")[0]


def test_stored_text_is_escaped_in_the_print_page(client, db):
    run_id = stored(db)
    edit = add_entry(
        db,
        run_id,
        day=date(2026, 11, 5),
        kind="activity",
        title_en='<script>alert("x")</script> A & B',
        title_kn="<b>bold</b>",
    )
    _, record = issued(db, client, run_id=edit.run_id, finalise={"note": "5 & 6 <i>"})

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text

    assert "<script>alert" not in markup
    assert "&lt;script&gt;" in markup
    assert "A &amp; B" in markup
    assert "&lt;b&gt;bold&lt;/b&gt;" in markup
    assert "5 &amp; 6 &lt;i&gt;" in markup
    # The page's own script is the only one, so nothing stored became markup.
    assert markup.count("<script") == 1


def test_the_print_page_hides_navigation_and_the_print_control_when_printed(client, db):
    _, record = issued(db, client)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text

    assert "@media print" in markup
    assert ".no-print { display: none !important; }" in markup
    assert 'class="toolbar no-print"' in markup
    # The heading repeats on every content page and a row is kept whole where it can be.
    assert "display: table-header-group" in markup
    assert "break-inside: avoid" in markup
    assert "@page { size: A4 portrait;" in markup
    # None of the application's own chrome is on this page at all.
    for marker in ('class="app-bar"', 'class="tabs"', 'id="panel"'):
        assert marker not in markup


def test_the_print_page_does_not_open_the_dialog_on_arrival(client, db):
    _, record = issued(db, client)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text

    assert markup.count("window.print") == 1
    script = markup.split("<script>")[-1]
    assert "addEventListener('click'" in script
    assert "window.print" in script
    # Nothing runs the dialog at load time: it is inside the listener body only.
    assert script.index("addEventListener('click'") < script.index("window.print")


# --- version identity and correction history -------------------------------


def test_an_earlier_version_still_exports_itself_after_a_correction(client, db):
    run_id, first = issued(db, client)
    correction = finalise.create_correction_revision(db, run_id, reason="a missed visit")
    edit = add_entry(
        db,
        correction["correction_run_id"],
        day=date(2026, 11, 12),
        kind="activity",
        title_en="Added during the correction",
    )
    review_every_date(db, edit.run_id)
    second = finalise.finalise_month(db, edit.run_id, "2026-11")
    assert second["sequence"] == 2
    first = client.get(f"/api/finalisations/{first['finalisation_id']}").json()
    second = client.get(f"/api/finalisations/{second['finalisation_id']}").json()

    first_book = workbook(client, first["finalisation_id"])[1]
    first_markup = client.get(f"/finalisations/{first['finalisation_id']}/print").text
    second_markup = client.get(f"/finalisations/{second['finalisation_id']}/print").text

    # Each export is the snapshot its own id names, not the month's newest version.
    assert "Added during the correction" in second_markup
    assert "Added during the correction" not in first_markup
    assert [stamped(row) for row in register_rows(first_book)][0] == "2026-11-01"
    assert first["fingerprint"] != second["fingerprint"]
    assert first["fingerprint"] in first_markup
    assert second["fingerprint"] in second_markup
    assert "Issued version 1 of November 2026" in first_markup
    assert "Issued version 2 of November 2026" in second_markup
    assert "Superseded by a later version" in first_markup
    assert first["is_latest"] is False and second["is_latest"] is True


def test_an_unknown_identifier_is_refused_by_both_formats(client):
    for path in (
        "/api/finalisations/MF-nothing/export.xlsx",
        "/finalisations/MF-nothing/print",
    ):
        answer = client.get(path)
        assert answer.status_code == 400, path
        assert answer.json()["error"]["constraint"] == finalise.C_FINALISATION_UNKNOWN


def test_a_revision_is_not_a_finalised_version_and_cannot_be_exported(client, db):
    run_id = stored(db)  # generated and stored, never finalised

    for path in (
        f"/api/finalisations/{run_id}/export.xlsx",
        f"/finalisations/{run_id}/print",
    ):
        answer = client.get(path)
        assert answer.status_code == 400, path
        assert answer.json()["error"]["constraint"] == finalise.C_FINALISATION_UNKNOWN

    # And the month is genuinely not finalised, so there is nothing to export.
    assert revision_state.finalisations_of_run(db, run_id) == ()


# --- independence from anything that can change later ----------------------


def test_a_master_change_cannot_reach_an_issued_document(client, db):
    _, record = issued(db, client)
    before_answer, before_book = workbook(client, record["finalisation_id"])
    before_markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text

    # Rename every DTC and add a holiday: on a register rebuilt from masters, both would
    # show. On an issued document, neither can.
    for index in range(1, 7):
        masters.upsert_dtc(
            db,
            dtc_id=f"SCD-{index}",
            name=f"Renamed after issue {index}",
            dtc_class="DEPT",
            kva=140.0 + index,
            canonical_feeder_id=f"F-0{(index % 2) + 1}",
        )
    cal.upsert_holiday(
        db,
        holiday_id="H-late",
        holiday_date=date(2026, 11, 18),
        name="Recorded after the month was closed",
        source="karnataka",
    )

    after_answer, after_book = workbook(client, record["finalisation_id"])
    after_markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text

    # Compared as documents rather than as bytes. Two exports made in different two-second
    # windows carry different archive timestamps, which is packaging noise rather than a
    # difference in the register, so the comparison is of every cell and every row.
    assert register_rows(after_book) == register_rows(before_book)
    assert head(after_book) == head(before_book)
    assert len(after_answer.content) > 0 and len(before_answer.content) > 0
    assert after_markup == before_markup
    assert "Renamed after issue" not in after_markup


def test_exporting_writes_nothing_and_leaves_the_record_untouched(client, db):
    _, record = issued(db, client)
    tables = (
        "month_finalisation",
        "month_state",
        "generation_run",
        "generation_row",
        "generation_visit",
        "generation_entry",
        "date_review",
        "date_lock",
        "master_version",
        "schema_migration",
    )
    before = {
        table: [
            tuple(row)
            for row in db.execute(f"SELECT * FROM {table} ORDER BY 1, 2")
        ]
        for table in tables
    }
    record_before = revision_state.finalisation(db, record["finalisation_id"])

    workbook(client, record["finalisation_id"])
    client.get(f"/finalisations/{record['finalisation_id']}/print")

    after = {
        table: [
            tuple(row)
            for row in db.execute(f"SELECT * FROM {table} ORDER BY 1, 2")
        ]
        for table in tables
    }
    record_after = revision_state.finalisation(db, record["finalisation_id"])
    assert after == before
    assert record_after == record_before
    assert record_after.fingerprint == record["fingerprint"]
    assert record_after.content_json == record_before.content_json


def test_the_same_version_exports_the_same_document_every_time(client, db):
    """The document is the record, not the moment it was downloaded.

    The bytes are compared as content rather than as a file: a workbook is a zip, and
    the zip container stamps each member with the time it was written, so two saves of
    an identical document differ in their container offsets and timestamps alone. What
    must not differ is a single cell.
    """

    _, record = issued(db, client)

    first = load_workbook(BytesIO(workbook(client, record["finalisation_id"])[0].content))
    second = load_workbook(
        BytesIO(workbook(client, record["finalisation_id"])[0].content)
    )

    assert register_rows(first) == register_rows(second)
    assert [[cell.value for cell in row] for row in first[documents.METADATA_SHEET].iter_rows()] == [
        [cell.value for cell in row] for row in second[documents.METADATA_SHEET].iter_rows()
    ]
    assert first.properties.title == second.properties.title
    assert first.properties.created == second.properties.created


# --- safety ---------------------------------------------------------------


def test_stored_text_that_looks_like_a_formula_stays_text(client, db):
    run_id = stored(db)
    edit = add_entry(
        db,
        run_id,
        day=date(2026, 11, 5),
        kind="activity",
        title_en='=HYPERLINK("http://example.invalid","click")',
    )
    _, record = issued(
        db,
        client,
        run_id=edit.run_id,
        finalise={"note": '=1+1+cmd|"/c calc"!A0'},
    )

    _, book = workbook(client, record["finalisation_id"])
    metadata = book[documents.METADATA_SHEET]
    note_cell = next(
        metadata.cell(row=row, column=2)
        for row in range(3, 22)
        if metadata.cell(row=row, column=1).value == "Note"
    )
    sheet = book[documents.REGISTER_SHEET]
    activity_cell = next(
        sheet.cell(row=row, column=3)
        for row in range(XLSX_HEADER_ROW + 1, XLSX_HEADER_ROW + 40)
        if isinstance(sheet.cell(row=row, column=3).value, str)
        and "HYPERLINK" in sheet.cell(row=row, column=3).value
    )

    # A leading "=" is stored as text, so a spreadsheet shows the characters and runs
    # nothing: an imported activity title is data, not code.
    assert note_cell.data_type == "s"
    assert note_cell.value == '=1+1+cmd|"/c calc"!A0'
    assert activity_cell.data_type == "s"
    assert '=HYPERLINK("http://example.invalid","click")' in activity_cell.value


# --- the projection is the only interpretation -----------------------------


def test_the_projection_reads_the_record_and_sorts_nothing(db, client):
    from dailyworkreg.application import export

    _, record = issued(db, client)

    document = export.document(db, record["finalisation_id"])

    assert [day.day for day in document.days] == list(record["content"]["rows"])
    assert document.finalisation_id == record["finalisation_id"]
    assert document.sequence == record["sequence"]
    assert document.version_label == f"Issued version {record['sequence']} of November 2026"


def test_one_shared_cell_is_what_both_documents_render(db, client):
    """The workbook and the page read the same value, so they cannot disagree."""

    _, record = issued(db, client)

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text
    _, book = workbook(client, record["finalisation_id"])

    printed = {row[1]: row[2] for row in printed_rows(markup)}
    exported = {stamped(row): row["activity"] for row in register_rows(book)}
    assert printed == exported


def test_the_export_path_holds_no_scheduling_or_generation():
    """A static guard: exporting must never reach the generator or the allocator."""

    from pathlib import Path as _Path

    source = _Path(__file__).resolve().parents[1] / "dailyworkreg"
    for relative in (
        "application/export.py",
        "interfaces/documents.py",
    ):
        text = (source / relative).read_text(encoding="utf-8")
        for forbidden in (
            "generate_draft(",
            "prepare_generation(",
            "assess_feasibility(",
            "domain.allocator",
            "import masters",
            "generation_store.",
        ):
            assert forbidden not in text, f"{relative} reaches {forbidden}"
