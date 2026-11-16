"""Exporting an issued month: the workbook and the print view read one stored record.

Both exports are proved against the snapshot the finalisation wrote, not against a fresh
generation: the tests build a register, close a month, then compare every cell and every
day section with ``month_finalisation.content_json``. They then change the live master
data, export again, and require the document to be unchanged, because a register issued in
November must still read as it was approved after the office has imported more masters.

Nothing here asserts incidental formatting. A test that pinned a column width would fail
on a change that made the document better, so widths, fonts and page setup are checked only
for being present.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from io import BytesIO
from pathlib import Path

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
from dailyworkreg.interfaces.api import create_app
from dailyworkreg.interfaces.documents import XLSX_MEDIA_TYPE
from fastapi.testclient import TestClient
from openpyxl import load_workbook

XLSX_HEADER_ROW = 5
XLSX_REGISTER_COLUMNS = {
    "date": 1,
    "weekday": 2,
    "kind": 3,
    "working": 4,
    "reasons_en": 5,
    "reasons_kn": 6,
    "visits": 7,
    "entries": 8,
    "warnings": 9,
    "locked": 10,
}


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
) -> GenerationSpec:
    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=seed,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=cycle_months,
        cycle_visits_per_asset=1,
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


def register_rows(workbook) -> list[dict[str, object]]:
    sheet = workbook["Register"]
    rows = []
    for values in sheet.iter_rows(min_row=XLSX_HEADER_ROW + 1, values_only=True):
        if not any(value not in (None, "") for value in values):
            continue
        rows.append(
            {name: values[column - 1] for name, column in XLSX_REGISTER_COLUMNS.items()}
        )
    return rows


def stamped(row: dict[str, object]) -> str:
    """The date a row carries, back as the stored ISO day it came from."""

    value = row["date"]
    return value if isinstance(value, str) else value.strftime("%Y-%m-%d")


def day_sections(markup: str) -> list[str]:
    """Each date's block of the print page, so a day can be examined on its own."""

    parts = markup.split('<section class="day">')
    return parts[1:]


# --- the workbook ----------------------------------------------------------


def test_the_workbook_is_a_real_xlsx_file_for_the_requested_version(client, db):
    _, record = issued(db, client)

    answer, book = workbook(client, record["finalisation_id"])

    assert answer.headers["content-type"].startswith(XLSX_MEDIA_TYPE)
    assert "attachment" in answer.headers["content-disposition"]
    assert answer.content[:2] == b"PK"  # a real zip container, not a placeholder
    assert book.sheetnames == ["Register", "Issued version"]


def test_every_stored_date_reaches_the_workbook_in_the_stored_order(client, db):
    _, record = issued(db, client)

    _, book = workbook(client, record["finalisation_id"])

    stored_days = list(record["content"]["rows"])
    assert [stamped(row) for row in register_rows(book)] == stored_days
    assert len(stored_days) == 30  # a whole calendar month, not only the working days


def test_a_date_with_nothing_on_it_keeps_its_place_in_the_workbook(client, db):
    _, record = issued(db, client)
    stored_rows = record["content"]["rows"]
    empty_day = next(day for day, row in stored_rows.items() if not row["visits"])
    _, book = workbook(client, record["finalisation_id"])

    row = next(item for item in register_rows(book) if stamped(item) == empty_day)

    assert not row["visits"]  # blank, because the record holds no visits
    assert row["kind"] == finalise_kind_label(stored_rows[empty_day]["kind"])
    assert row["working"] == ("Yes" if stored_rows[empty_day]["working"] else "No")


def finalise_kind_label(kind: str) -> str:
    from dailyworkreg.application.export import DAY_KIND_LABELS

    return DAY_KIND_LABELS.get(kind, kind)


def test_every_stored_value_is_what_the_workbook_shows(client, db):
    _, record = issued(db, client)
    stored_rows = record["content"]["rows"]

    _, book = workbook(client, record["finalisation_id"])
    rows = {stamped(row): row for row in register_rows(book)}

    for day, stored_row in stored_rows.items():
        row = rows[day]
        assert row["weekday"] == date.fromisoformat(day).strftime("%A")
        assert row["kind"] == finalise_kind_label(stored_row["kind"])
        assert (row["reasons_en"] or "") == "\n".join(stored_row["reasons_en"])
        assert (row["reasons_kn"] or "") == "\n".join(stored_row["reasons_kn"])
        assert (row["warnings"] or "") == "\n".join(
            f"{item[0]}: {item[1]}" for item in stored_row["warnings"]
        )
        visits = row["visits"].split("\n") if row["visits"] else []
        assert len(visits) == len(stored_row["visits"])
        for line, visit in zip(visits, stored_row["visits"], strict=True):
            # The name, the id and the provenance all come from the record.
            assert visit[2] in line
            assert visit[0] in line
            assert visit[1] in line.lower() or "Committed" in line or "Allocated" in line
        entries = row["entries"].split("\n") if row["entries"] else []
        assert len(entries) == len(stored_row["entries"])
        for line, entry in zip(entries, stored_row["entries"], strict=True):
            assert entry[2] in line


def test_several_activities_on_one_date_are_all_kept(client, db):
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
    row = next(item for item in register_rows(book) if stamped(item) == "2026-11-05")
    lines = row["entries"].split("\n")

    assert len(lines) == len(fifth["entries"])
    assert "Pole inspection" in row["entries"]
    assert "Meter reading" in row["entries"]


def test_the_issued_version_sheet_identifies_this_version(client, db):
    _, record = issued(db, client, finalise={"note": "November closed"})

    _, book = workbook(client, record["finalisation_id"])
    sheet = book["Issued version"]
    values = {
        sheet.cell(row=row, column=1).value: sheet.cell(row=row, column=2).value
        for row in range(3, 20)
    }

    assert values["Issued version"] == record["sequence"] == 1
    assert values["Revision closed"] == record["revision_number"]
    assert values["Finalisation id"] == record["finalisation_id"]
    assert values["Register"] == record["register_id"]
    assert values["Content fingerprint"] == record["fingerprint"]
    assert values["Master version at finalisation"] == record["master_version"]
    assert values["Note"] == "November closed"
    assert values["Month"] == "November 2026"


def test_the_workbook_is_set_up_to_print(client, db):
    _, record = issued(db, client)

    _, book = workbook(client, record["finalisation_id"])
    sheet = book["Register"]

    assert sheet.print_title_rows.replace("$", "") == f"{XLSX_HEADER_ROW}:{XLSX_HEADER_ROW}"
    assert sheet.freeze_panes  # the headings stay on screen while scrolling
    assert sheet.page_setup.orientation == "landscape"
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


# --- the print view --------------------------------------------------------


def test_the_print_page_carries_every_stored_date_and_its_content(client, db):
    _, record = issued(db, client)

    answer = client.get(f"/finalisations/{record['finalisation_id']}/print")

    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/html")
    sections = day_sections(answer.text)
    assert len(sections) == len(record["content"]["rows"]) == 30
    for day, stored_row in record["content"]["rows"].items():
        block = next(item for item in sections if f'class="date">{day}<' in item)
        for visit in stored_row["visits"]:
            assert visit[2] in block  # the stored English name
            assert visit[0] in block  # the stored asset id
        for entry in stored_row["entries"]:
            assert entry[2] in block or entry[2] == ""
        if not stored_row["visits"]:
            assert "No field visits recorded." in block
        if not stored_row["entries"]:
            assert "No activities or entries recorded." in block


def test_the_print_page_identifies_the_issued_version_it_shows(client, db):
    _, record = issued(db, client, finalise={"note": "November closed"})

    markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text

    assert "Issued version 1 of November 2026" in markup
    assert record["finalisation_id"] in markup
    assert record["fingerprint"] in markup
    assert str(record["master_version"]) in markup
    assert "Note recorded when this version was issued: November closed" in markup
    assert "In force" in markup


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
    # The day blocks survive printing and are kept off a page break where they can be.
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
    assert "2026-11-12" in second_markup and "Added during the correction" in second_markup
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
    before_book = workbook(client, record["finalisation_id"])[0].content
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

    after_book = workbook(client, record["finalisation_id"])[0].content
    after_markup = client.get(f"/finalisations/{record['finalisation_id']}/print").text

    assert after_book == before_book
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
    _, record = issued(db, client)

    first = workbook(client, record["finalisation_id"])[0].content
    second = workbook(client, record["finalisation_id"])[0].content

    assert first == second
    assert register_rows(load_workbook(BytesIO(first))) == register_rows(
        load_workbook(BytesIO(second))
    )


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
    note_cell = next(
        book["Issued version"].cell(row=row, column=2)
        for row in range(3, 20)
        if book["Issued version"].cell(row=row, column=1).value == "Note"
    )
    entries_cell = next(
        book["Register"].cell(
            row=row, column=XLSX_REGISTER_COLUMNS["entries"]
        )
        for row in range(XLSX_HEADER_ROW + 1, 60)
        if book["Register"].cell(row=row, column=1).value is not None
        and getattr(book["Register"].cell(row=row, column=1).value, "day", None) == 5
    )

    # A leading "=" is stored as text, so a spreadsheet shows the characters and runs
    # nothing: an imported activity title is data, not code.
    assert note_cell.data_type == "s"
    assert note_cell.value == '=1+1+cmd|"/c calc"!A0'
    assert entries_cell.data_type == "s"
    assert '=HYPERLINK("http://example.invalid","click")' in entries_cell.value


# --- the projection is the only interpretation -----------------------------


def test_the_projection_reads_the_record_and_sorts_nothing(db, client):
    from dailyworkreg.application import export

    _, record = issued(db, client)

    document = export.document(db, record["finalisation_id"])

    assert [day.day for day in document.days] == list(record["content"]["rows"])
    assert document.finalisation_id == record["finalisation_id"]
    assert document.sequence == record["sequence"]
    assert document.version_label == f"Issued version {record['sequence']} of November 2026"


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
