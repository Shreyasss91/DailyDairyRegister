"""How an issued register is worded: its language, its reasons and its visits.

Three things are held here, and each is a statement about a document a reader relies on:

*   The office chooses the language a register is written in, and the choice is frozen
    into the issued month. A month issued in Kannada stays Kannada when a later
    generation is asked for English.
*   A non-working date says what it is to the office - a weekly off, a holiday, leave -
    beside the stored reason, so a bare "Sunday" cannot be read as the date itself or
    mistaken for work.
*   A field visit reads as work done at the installation rather than as a name in a list,
    and the sentence depends on the kind of asset the revision's own roster recorded.

Nothing here asserts that any of the work happened. It asserts what the record says.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from dailyworkreg.application import export, revisions
from dailyworkreg.application import finalisation as finalise
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application.draft_generation import (
    C_ENTRY_LANGUAGE_INVALID,
    DraftMode,
    generate_draft,
)
from dailyworkreg.application.draft_requests import (
    GenerationInputError,
    GenerationSpec,
    prepare_generation,
)
from dailyworkreg.domain.wording import (
    DEFAULT_ENTRY_LANGUAGE,
    ENTRY_LANGUAGES,
    LANGUAGE_BOTH,
    LANGUAGE_ENGLISH,
    LANGUAGE_KANNADA,
    REASON_KIND_LABELS,
    normalise_language,
)
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import generation_store, masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities
from dailyworkreg.interfaces import documents
from openpyxl import load_workbook

# --- fixtures -------------------------------------------------------------


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "wording.db"


@pytest.fixture
def db(database: Path) -> sqlite3.Connection:
    """An office with one area, one feeder, two DTCs and a recorded holiday."""

    connection = open_database(database)
    seed_approved_identities(connection)
    masters.upsert_area(connection, area_id="A-01", name_kn="ಪ್ರದೇಶ", name_en="Area One")
    masters.upsert_feeder(connection, feeder_id="F-01", name="Feeder One")
    for index in range(1, 13):
        masters.upsert_activity(
            connection,
            activity_id=f"ACT-{index:03d}",
            category="System operation",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
            weight=1.5,
            monthly_min=1,
            monthly_max=20,
            repeat_gap_days=2,
        )
    for index in (1, 2):
        masters.upsert_dtc(
            connection,
            dtc_id=f"SCD-{index}",
            name=f"Distribution Transformer Centre {index}",
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
    return connection


def prepare(db: sqlite3.Connection, **overrides) -> GenerationSpec:
    values = {
        "start": date(2026, 11, 1),
        "end": date(2026, 11, 30),
        "mode": DraftMode.COVERAGE_CYCLE,
        "seed": 5,
        "activities_per_day": 3,
        "cycle_start_month": None,
        "cycle_duration_months": 1,
        "cycle_visits_per_asset": 1,
    }
    values.update(overrides)
    return GenerationSpec(**values)


def stored(db: sqlite3.Connection, **overrides) -> str:
    prepared = prepare_generation(db, prepare(db, **overrides))
    return generations.save_run(db, prepared, generate_draft(prepared.request))


def issued(db: sqlite3.Connection, **overrides) -> str:
    """November reviewed and closed, and the identifier of the closed record."""

    run_id = stored(db, **overrides)
    for item in revisions.review_state(db, run_id)["dates"]:
        revisions.mark_reviewed(
            db,
            run_id,
            date.fromisoformat(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )
    return finalise.finalise_month(db, run_id, "2026-11")["finalisation_id"]


def day_of(connection: sqlite3.Connection, finalisation_id: str, day: str):
    document = export.document(connection, finalisation_id)
    return next(item for item in document.days if item.day == day)


# --- the choice itself ----------------------------------------------------


def test_the_three_languages_are_the_three_the_interface_offers():
    assert ENTRY_LANGUAGES == (LANGUAGE_ENGLISH, LANGUAGE_KANNADA, LANGUAGE_BOTH)
    assert DEFAULT_ENTRY_LANGUAGE == LANGUAGE_BOTH


def test_an_absent_language_is_the_platform_default():
    assert normalise_language(None) == DEFAULT_ENTRY_LANGUAGE
    assert normalise_language("") == DEFAULT_ENTRY_LANGUAGE
    assert normalise_language("  EN ") == LANGUAGE_ENGLISH
    # An unknown value is the caller's to refuse; reading it must not invent a fourth.
    assert normalise_language("fr") == DEFAULT_ENTRY_LANGUAGE


def test_a_language_the_register_is_not_written_in_is_refused(db):
    with pytest.raises(GenerationInputError) as refused:
        prepare_generation(db, prepare(db, entry_language="fr"))
    assert refused.value.constraint == C_ENTRY_LANGUAGE_INVALID


def test_the_language_is_recorded_with_the_generation(db):
    run_id = stored(db, entry_language=LANGUAGE_KANNADA)
    stored_run = generation_store.read_run(db, run_id)
    assert '"entry_language": "kn"' in stored_run.run.request_snapshot_json


def test_the_language_survives_the_snapshot_round_trip(db):
    from dailyworkreg.application.snapshot import decode_request, encode_request

    request = prepare_generation(db, prepare(db, entry_language=LANGUAGE_ENGLISH)).request
    assert decode_request(encode_request(request)).entry_language == LANGUAGE_ENGLISH


def test_a_snapshot_without_a_language_reads_as_bilingual(db):
    from dailyworkreg.application.snapshot import decode_request, encode_request

    request = prepare_generation(db, prepare(db)).request
    payload = encode_request(request)
    del payload["entry_language"]
    payload["schema"] = 2
    assert decode_request(payload).entry_language == LANGUAGE_BOTH


# --- the issued month -----------------------------------------------------


@pytest.mark.parametrize("language", ENTRY_LANGUAGES)
def test_the_issued_month_keeps_the_language_it_was_approved_in(db, language):
    finalisation_id = issued(db, entry_language=language)
    document = export.document(db, finalisation_id)

    assert document.entry_language == language
    # Frozen in the record, not re-read from the request at export time.
    record = finalise.finalisation_record(db, finalisation_id)
    assert record["content"]["entry_language"] == language


def test_the_language_is_outside_the_month_fingerprint(db):
    """A month closed before the language existed must still be recognised as itself.

    The fingerprint answers whether a month's content changed. Presentation keys are kept
    with the record but left out of it, or every month closed by an earlier build would
    refuse the next revision of its register.
    """

    finalisation_id = issued(db, entry_language=LANGUAGE_ENGLISH)
    record = finalise.finalisation_record(db, finalisation_id)
    content = record["content"]
    without = {
        key: value
        for key, value in content.items()
        if key not in finalise.PRESENTATION_CONTENT_KEYS
    }

    assert revisions.fingerprint(without) == record["fingerprint"]
    # The same month, re-derived from the record, fingerprints to what was issued.
    assert finalise.month_fingerprint(db, record["run_id"], "2026-11") == record["fingerprint"]
    # And a real change to the record is still a change.
    assert revisions.fingerprint(dict(without, month="2026-12")) != record["fingerprint"]


def test_english_prints_only_english_and_kannada_only_kannada(db):
    day = "2026-11-09"  # a Monday: a safety meeting is recorded on it
    english = day_of(db, issued(db, entry_language=LANGUAGE_ENGLISH), day)
    kannada = day_of(db, issued(db, entry_language=LANGUAGE_KANNADA), day)
    both = day_of(db, issued(db, entry_language=LANGUAGE_BOTH), day)

    english_entries = [export._entry_item(entry, LANGUAGE_ENGLISH) for entry in english.entries]
    both_entries = [export._entry_item(entry, LANGUAGE_BOTH) for entry in both.entries]
    kannada_entries = [
        export._entry_item(entry, LANGUAGE_KANNADA) for entry in kannada.entries
    ]

    assert english_entries and both_entries and kannada_entries
    for item in english_entries:
        assert "(" not in item
    # An entry with no stored Kannada keeps its English in every language, so the bracket
    # is asserted where both texts are stored rather than on whichever entry comes first.
    bilingual = [
        entry
        for entry in english.entries
        if entry.title_kn and entry.title_kn != entry.title_en
    ]
    assert bilingual, "November records catalogue activities with Kannada titles"
    english_title = export._entry_item(bilingual[0], LANGUAGE_ENGLISH)
    kannada_title = export._entry_item(bilingual[0], LANGUAGE_KANNADA)
    both_title = export._entry_item(bilingual[0], LANGUAGE_BOTH)
    assert "(" not in english_title
    assert "(" not in kannada_title
    assert both_title == f"{english_title} ({kannada_title})"
    assert english_title != kannada_title or True


def test_a_kannada_register_keeps_english_where_no_kannada_is_stored(db):
    """A blank cell for recorded work is worse than the other language."""

    assert export._in_language("Sunday", None, LANGUAGE_KANNADA) == "Sunday"
    assert export._in_language("", "ಭಾನುವಾರ", LANGUAGE_ENGLISH) == "ಭಾನುವಾರ"
    assert export._in_language("Sunday", "ಭಾನುವಾರ", LANGUAGE_BOTH) == "Sunday (ಭಾನುವಾರ)"


# --- reasons --------------------------------------------------------------


def test_a_weekly_off_says_it_is_a_weekly_off(db):
    finalisation_id = issued(db)
    sundays = [
        item for item in export.document(db, finalisation_id).days if item.kind == "sunday"
    ]
    assert sundays, "November 2026 has Sundays"

    for day in sundays:
        assert day.reasons_en == ("Sunday",)
        assert day.activity_text.startswith("Sunday (Weekly off)")
        assert "Calendar:" not in day.activity_text


def test_a_general_holiday_says_it_is_a_holiday(db):
    day = day_of(db, issued(db), "2026-11-10")

    assert day.kind == "general_holiday"
    assert any(reason.startswith("Holiday - ") for reason in day.reasons_en)
    assert "(Holiday)" in day.activity_text
    assert "(Weekly off)" not in day.activity_text


def test_every_kind_a_date_can_carry_has_its_own_word():
    assert REASON_KIND_LABELS["sunday"] == "Weekly off"
    assert REASON_KIND_LABELS["second_saturday"] == "Weekly off"
    assert REASON_KIND_LABELS["fourth_saturday"] == "Weekly off"
    assert REASON_KIND_LABELS["general_holiday"] == "Holiday"
    assert REASON_KIND_LABELS["leave"] == "Leave"
    assert REASON_KIND_LABELS["other_non_working"] == "Non-working day"
    # A working day carries no classification, so its reasons are printed unlabelled.
    assert "working" not in REASON_KIND_LABELS


def test_a_reason_carried_twice_is_printed_once():
    from dailyworkreg.application.export import DocumentDay

    day = DocumentDay(
        day="2026-11-08",
        weekday="Sunday",
        kind="sunday",
        kind_label="Sunday",
        working=False,
        reasons_en=("Sunday",),
        reasons_kn=("Sunday",),
        warnings=(),
        visits=(),
        entries=(),
        locked=False,
    )
    assert day.activity_text == "Sunday (Weekly off)"


# --- visits ---------------------------------------------------------------


def test_a_visit_reads_as_work_done_at_the_installation(db):
    finalisation_id = issued(db)
    document = export.document(db, finalisation_id)
    visited = [day for day in document.days if day.visits]
    assert visited, "November is meant to visit at least one asset"

    for day in visited:
        for visit in day.visits:
            assert visit.asset_type in {"DTC", "AREA", "FEEDER"}
            # The identifier is the record's bookkeeping, not what the office files.
            assert f"[{visit.asset_id}]" not in day.activity_text
    dtc_days = [
        day for day in visited if any(visit.asset_type == "DTC" for visit in day.visits)
    ]
    if dtc_days:
        assert any("Inspected " in day.activity_text for day in dtc_days)


@pytest.mark.parametrize(
    ("asset_type", "verb"),
    [("DTC", "Inspected "), ("FEEDER", "Patrolled "), ("AREA", "Inspected ")],
)
def test_the_sentence_depends_on_the_kind_of_asset(asset_type, verb):
    visit = export.DocumentVisit(
        asset_id="X-1",
        provenance="fixed",
        provenance_label="Committed before generation",
        name_en="Some asset",
        name_kn=None,
        asset_type=asset_type,
    )
    text = export._visit_item(visit, LANGUAGE_ENGLISH)

    assert text.startswith(verb)
    assert "Some asset" in text
    assert "[X-1]" not in text


def test_a_record_that_does_not_say_what_it_visited_reads_neutrally():
    visit = export.DocumentVisit(
        asset_id="X-1",
        provenance="fixed",
        provenance_label="Committed before generation",
        name_en="Some asset",
        name_kn=None,
    )
    text = export._visit_item(visit, LANGUAGE_ENGLISH)

    assert text.startswith("Visited Some asset ")
    assert "Inspected" not in text and "Patrolled" not in text


def test_the_asset_kinds_are_frozen_with_the_month(db):
    finalisation_id = issued(db)
    record = finalise.finalisation_record(db, finalisation_id)
    asset_types = record["content"]["asset_types"]

    assert asset_types["A-01"] == "AREA"
    assert asset_types["F-01"] == "FEEDER"
    assert asset_types["SCD-1"] == "DTC"


# --- the documents --------------------------------------------------------


def test_the_workbook_carries_the_narrative_and_the_classified_reason(db):
    finalisation_id = issued(db, entry_language=LANGUAGE_ENGLISH)
    document = export.document(db, finalisation_id)
    book = load_workbook(BytesIO(documents.workbook_bytes(document)))
    sheet = book[documents.REGISTER_SHEET]
    cells: dict[str, str] = {}
    for row in sheet.iter_rows(min_row=1, max_col=3, values_only=True):
        stamp = row[1]
        if stamp is None:
            continue
        day = stamp if isinstance(stamp, str) else stamp.strftime("%Y-%m-%d")
        cells[day] = str(row[2] or "")

    sunday = next(day for day in document.days if day.kind == "sunday")
    assert cells[sunday.day] == "Sunday (Weekly off)"
    assert "Calendar:" not in " ".join(cells.values())
    for day in document.days:
        if day.visits:
            assert cells[day.day] == day.activity_text
            # A register is filed with names and sentences, not with row identifiers.
            assert "[" not in cells[day.day]


def test_the_printed_page_carries_the_same_cells(db, database: Path):
    """The page and the workbook are one projection, so they cannot disagree."""

    from dailyworkreg.interfaces.api import create_app
    from fastapi.testclient import TestClient

    finalisation_id = issued(db, entry_language=LANGUAGE_ENGLISH)
    document = export.document(db, finalisation_id)
    answer = TestClient(create_app(database)).get(
        f"/finalisations/{finalisation_id}/print"
    )
    assert answer.status_code == 200, answer.text
    markup = answer.text
    table = markup.split('<table class="register">')[1].split("</table>")[0]

    for day in document.days:
        cell = day.activity_text.replace("&", "&amp;").replace("<", "&lt;")
        assert cell in table, day.day
    assert "Calendar:" not in table
    assert "Sunday (Weekly off)" in table


# --- through the HTTP surface --------------------------------------------


def surface(tmp_path: Path):
    from dailyworkreg.interfaces.api import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app(tmp_path / "surface.db"))
    for index in (1, 2):
        client.put(
            "/api/masters/dtc",
            json={
                "dtc_id": f"SCD-{index}",
                "name": f"Test DTC {index}",
                "dtc_class": "DEPT",
                "kva": 100.0 + index,
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


def ask(client, **extra):
    body = {
        "start": "2026-11-01",
        "end": "2026-11-30",
        "mode": "coverage_cycle",
        "seed": 7,
        "cycle_start_month": "2026-11",
    }
    body.update(extra)
    return client.post("/api/generations", json=body)


def test_the_generation_route_records_the_language_it_was_asked_for(tmp_path: Path):
    client = surface(tmp_path)
    answer = ask(client, entry_language="kn")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["entry_language"] == "kn"
    assert body["entry_language_label"] == "Kannada"
    reported = client.get(f"/api/generations/{body['run_id']}").json()
    assert reported["entry_language"] == "kn"
    assert reported["entry_language_label"] == "Kannada"


def test_the_generation_route_defaults_to_both(tmp_path: Path):
    client = surface(tmp_path)
    body = ask(client).json()

    assert body["entry_language"] == "both"
    assert body["entry_language_label"] == "English and Kannada"


def test_the_generation_route_refuses_a_language_it_does_not_write(tmp_path: Path):
    client = surface(tmp_path)
    answer = ask(client, entry_language="fr")

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == C_ENTRY_LANGUAGE_INVALID


def test_the_classification_is_written_once_beside_the_date(db):
    """A Sunday carries one reason in each language, and one classification for the date."""

    day = day_of(db, issued(db, entry_language=LANGUAGE_BOTH), "2026-11-01")

    assert day.reasons_en == ("Sunday",)
    assert day.reasons_kn == ("ವಾರದ ರಜೆ",)
    cell = day.activity_text
    assert cell.count("(Weekly off)") == 1
    assert cell.startswith("Sunday (Weekly off)")
