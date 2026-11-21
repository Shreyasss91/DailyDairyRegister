"""Starting a new office: the database is emptied, what must survive survives, and nothing
half-cleared is left behind.

The reset exists because one installation holds one office. Every test here is written from
the question the operator actually faces — "may I hand this computer to the next office?" —
rather than from the mechanism, so the assertions are on what a later screen would read: the
transformer list, the calendar, the issued months, the settings, and the file the operator is
told to keep.

Three properties are asserted repeatedly because they are the ones a reset can get wrong:

*   **Nothing is removed without the confirmation word,** and a refused reset leaves the
    database byte-for-byte as it was, including the recovery copies on disk.
*   **The kept calendar is the state's, not the previous office's.** Two General Holidays,
    two leave types and the weekly-off settings survive; the office's own leave records,
    dated events and overrides do not.
*   **The next start does not put the old office back.** The approved areas, feeders and
    leave types are seeded on a fresh install, so the seed marker is written by the reset to
    say the masters were cleared on purpose.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application import finalisation as finalise
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application import progress, revisions
from dailyworkreg.application import reset as reset_application
from dailyworkreg.application.draft_generation import DraftMode, DraftStatus, generate_draft
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import imports as staged_imports
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite import schema as schema_module
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import (
    SEED_SETTING_KEY,
    seed_approved_identities,
)
from dailyworkreg.interfaces.api import create_app
from dailyworkreg.interfaces.maintenance import MaintenanceBusy
from fastapi.testclient import TestClient

AREA_CSV = b"area id,kannada name,english name\nA-01,Mangaluru,Mangaluru\n"

#: Every table the reset is supposed to leave empty, whatever the scope. `app_setting` is not
#: here because two scopes keep three of its rows, and it is asserted by name instead.
EMPTIED_TABLES = (
    "activity",
    "area",
    "area_alias",
    "date_lock",
    "date_override",
    "date_review",
    "dated_event",
    "draft_revision",
    "dtc",
    "dtc_source_record",
    "feeder",
    "generation_entry",
    "generation_exception",
    "generation_month",
    "generation_row",
    "generation_run",
    "generation_visit",
    "import_batch",
    "import_staging_row",
    "leave_record",
    "month_finalisation",
    "month_state",
    "register",
    "revision_change",
    "revision_decision",
)


def _spec(start: date, end: date) -> GenerationSpec:
    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=13,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=2,
        cycle_visits_per_asset=1,
    )


def _review(connection: sqlite3.Connection, run_id: str, month: str) -> None:
    for item in revisions.review_state(connection, run_id)["dates"]:
        if item["date"].startswith(month):
            revisions.mark_reviewed(
                connection,
                run_id,
                date.fromisoformat(item["date"]),
                expected_fingerprint=item["fingerprint"],
            )


@pytest.fixture()
def office(tmp_path: Path) -> dict:
    """One office's database: masters, calendar, leave, a staged import, and a closed month."""

    path = tmp_path / "office" / "dailyworkreg.db"
    path.parent.mkdir(parents=True)
    connection = open_database(path)
    try:
        seed_approved_identities(connection)
        for index in range(1, 5):
            masters.upsert_dtc(
                connection,
                dtc_id=f"SCD-{index}",
                name=f"Office DTC {index}",
                dtc_class="DEPT",
                kva=100.0 + index,
                canonical_feeder_id=f"F-0{(index % 2) + 1}",
            )
        for index in range(1, 5):
            masters.upsert_activity(
                connection,
                activity_id=f"A-{index:03d}",
                category="Inspection",
                description_en=f"Activity {index}",
                description_kn=f"ಚಟುವಟಿಕೆ {index}",
            )
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-11-10",
            holiday_date=date(2026, 11, 10),
            name="Office holiday",
            source="karnataka",
        )
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-11-20",
            holiday_date=date(2026, 11, 20),
            name="Second office holiday",
            source="karnataka",
        )
        cal.upsert_leave_record(
            connection,
            leave_id="L-1",
            leave_date=date(2026, 11, 3),
            leave_type_id="CL",
            note="The previous office's clerk",
        )
        batch, _ = staged_imports.stage_bytes(connection, "area", "areas.csv", AREA_CSV)

        prepared = prepare_generation(connection, _spec(date(2026, 11, 1), date(2026, 12, 31)))
        draft = generate_draft(prepared.request)
        assert draft.status == DraftStatus.ALLOCATION_COMPLETE
        run_id = generations.save_run(connection, prepared, draft)
        revisions.lock_date(connection, run_id, date(2026, 11, 6))
        _review(connection, run_id, "2026-11")
        closed = finalise.finalise_month(connection, run_id, "2026-11", note="November closed")
    finally:
        connection.close()

    return {
        "path": path,
        "run_id": run_id,
        "batch_id": batch.batch_id,
        "finalisation_id": closed["finalisation_id"],
    }


@pytest.fixture()
def client(office: dict) -> TestClient:
    return TestClient(create_app(office["path"]))


# --- helpers ---------------------------------------------------------------


def rows(path: Path, table: str) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]  # noqa: S608
    finally:
        connection.close()


def count(path: Path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
    finally:
        connection.close()


def setting(path: Path, key: str) -> str | None:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT value FROM app_setting WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]
    finally:
        connection.close()


def sidecars(path: Path) -> list[Path]:
    """Every file beside the database, so a reset that leaves debris is visible."""

    return sorted(item.name for item in path.parent.iterdir() if item != path and item.is_file())


def reset_body(scope: str, *, word: str = "RESET") -> dict:
    return {"scope": scope, "confirmation": word}


# --- reading what is there -------------------------------------------------


def test_the_preview_counts_what_the_operator_is_about_to_lose(client: TestClient) -> None:
    preview = client.get("/api/reset/preview").json()
    counts = preview["counts"]

    assert counts["dtc"] == 4
    assert counts["activity"] == 4
    assert counts["general_holiday"] == 2
    assert counts["leave_record"] == 1
    assert counts["import_batch"] == 1
    assert counts["month_finalisation"] == 1
    assert counts["register"] >= 1
    assert preview["empty"] is False
    assert preview["confirmation_word"] == "RESET"
    assert set(preview["scopes"]) == set(reset_application.SCOPES)
    # The labels are the words the page prints, so they are part of the contract.
    assert preview["labels"]["dtc"] == "transformers (DTCs)"


def test_the_preview_reads_without_changing_anything(client: TestClient, office: dict) -> None:
    before = {table: rows(office["path"], table) for table in ("dtc", "general_holiday")}

    client.get("/api/reset/preview")

    assert {table: rows(office["path"], table) for table in before} == before
    assert sidecars(office["path"]) == []


def test_a_fresh_installation_already_holds_the_identities_a_reset_would_clear(
    tmp_path: Path,
) -> None:
    # A first start seeds the approved areas, feeders and leave types, so "nothing has been
    # entered yet" is not the same state as "the database is empty", and the page must not
    # claim a reset has nothing to do.
    path = tmp_path / "fresh.db"

    preview = TestClient(create_app(path)).get("/api/reset/preview").json()

    assert preview["counts"]["area"] == 16
    assert preview["counts"]["feeder"] == 4
    assert preview["counts"]["leave_type"] == 2
    assert preview["counts"]["dtc"] == 0
    assert preview["empty"] is False


def test_after_a_full_reset_the_preview_says_there_is_nothing_left(client: TestClient) -> None:
    client.post("/api/reset", json=reset_body("everything"))

    preview = client.get("/api/reset/preview").json()

    assert all(value == 0 for value in preview["counts"].values())
    assert preview["empty"] is True


# --- the confirmation ------------------------------------------------------


@pytest.mark.parametrize("word", ["", "REPLACED", "yes", "RESETT", "RESET PLEASE"])
def test_a_reset_is_refused_without_the_word(client: TestClient, office: dict, word: str) -> None:
    before = rows(office["path"], "dtc")

    answer = client.post("/api/reset", json=reset_body("everything", word=word))

    assert answer.status_code == 400
    detail = answer.json()["error"]
    assert detail["constraint"] == reset_application.C_RESET_REFUSED
    assert "RESET" in detail["detail"]
    assert rows(office["path"], "dtc") == before
    assert sidecars(office["path"]) == []


def test_the_word_is_accepted_regardless_of_its_case(client: TestClient, office: dict) -> None:
    # The word is not a password; it is a deliberate act. Failing on the shift key would
    # refuse a person who has already decided.
    answer = client.post("/api/reset", json=reset_body("everything", word="  reset  "))

    assert answer.status_code == 200, answer.text
    assert count(office["path"], "dtc") == 0


def test_an_unknown_scope_is_refused_by_name(client: TestClient, office: dict) -> None:
    before = rows(office["path"], "dtc")

    answer = client.post("/api/reset", json=reset_body("just_the_masters"))

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == reset_application.C_RESET_REFUSED
    assert rows(office["path"], "dtc") == before


# --- the full reset --------------------------------------------------------


def test_clearing_everything_empties_every_table(client: TestClient, office: dict) -> None:
    answer = client.post("/api/reset", json=reset_body("everything"))

    assert answer.status_code == 200, answer.text
    for table in EMPTIED_TABLES:
        assert count(office["path"], table) == 0, table
    # The calendar goes too: only the seed marker a restart reads is left.
    assert count(office["path"], "general_holiday") == 0
    assert count(office["path"], "leave_type") == 0
    # `master_version` and `schema_migration` are the build's own records, not the
    # office's: the replacement database arrives already knowing which schema it is.
    assert count(office["path"], "master_version") == 1


def test_clearing_everything_also_clears_the_weekly_off_rules(
    client: TestClient, office: dict
) -> None:
    assert setting(office["path"], "calendar.sundays_off") in (None, "1")

    client.post("/api/reset", json=reset_body("everything"))

    assert setting(office["path"], "calendar.sundays_off") is None
    assert setting(office["path"], "calendar.second_saturday_off") is None
    assert setting(office["path"], "calendar.fourth_saturday_off") is None


def test_the_tool_is_immediately_usable_after_a_full_reset(
    client: TestClient, office: dict
) -> None:
    client.post("/api/reset", json=reset_body("everything"))
    fresh = TestClient(create_app(office["path"]))

    # No masters, so the overview must answer rather than fail: this is the state a first
    # install is in, and every screen has to survive it.
    assert fresh.get("/api/readiness").status_code == 200
    assert fresh.get("/api/masters/dtc").json() == []
    assert fresh.get("/api/calendar/holidays").json() == []
    assert all(runs == [] for runs in fresh.get("/api/generations").json().values())


# --- the reset that keeps the calendar -------------------------------------


def test_keeping_the_calendar_keeps_the_state_wide_facts_only(
    client: TestClient, office: dict
) -> None:
    before_holidays = rows(office["path"], "general_holiday")
    before_types = rows(office["path"], "leave_type")

    answer = client.post("/api/reset", json=reset_body("keep_calendar"))

    assert answer.status_code == 200, answer.text
    assert rows(office["path"], "general_holiday") == before_holidays
    assert rows(office["path"], "leave_type") == before_types
    for table in EMPTIED_TABLES:
        assert count(office["path"], table) == 0, table


def test_keeping_the_calendar_keeps_the_office_s_own_leave_off_the_calendar(
    client: TestClient, office: dict
) -> None:
    # A leave record is about a person, not about the state. The holiday list is reusable;
    # the clerk's leave is not.
    assert count(office["path"], "leave_record") == 1

    client.post("/api/reset", json=reset_body("keep_calendar"))

    assert count(office["path"], "leave_record") == 0


def test_keeping_the_calendar_keeps_the_weekly_off_rules_that_were_set(
    client: TestClient, office: dict
) -> None:
    connection = sqlite3.connect(office["path"])
    try:
        connection.execute(
            "INSERT OR REPLACE INTO app_setting (key, value, updated_at) VALUES (?, ?, ?)",
            ("calendar.fourth_saturday_off", "0", "2026-11-01T00:00:00+00:00"),
        )
        connection.commit()
    finally:
        connection.close()

    client.post("/api/reset", json=reset_body("keep_calendar"))

    assert setting(office["path"], "calendar.fourth_saturday_off") == "0"


def test_a_reset_with_no_calendar_to_keep_still_succeeds(client: TestClient, office: dict) -> None:
    connection = sqlite3.connect(office["path"])
    try:
        connection.execute("DELETE FROM general_holiday")
        connection.commit()
    finally:
        connection.close()

    answer = client.post("/api/reset", json=reset_body("keep_calendar"))

    assert answer.status_code == 200, answer.text
    assert answer.json()["kept"]["general_holiday"] == 0
    assert count(office["path"], "general_holiday") == 0


# --- the seed does not restore the previous office -------------------------


def test_a_restart_does_not_put_the_previous_office_masters_back(
    client: TestClient, office: dict
) -> None:
    # The approved areas, feeders and leave types are seeded on a fresh install. Without the
    # marker the reset writes, the next start would put sixteen areas belonging to the
    # previous office into the new one, and nobody would be told.
    client.post("/api/reset", json=reset_body("everything"))
    assert setting(office["path"], SEED_SETTING_KEY) == reset_application.CLEARED_SEED_VALUE

    connection = open_database(office["path"])
    try:
        seeded = seed_approved_identities(connection)
    finally:
        connection.close()

    assert seeded == {"areas": 0, "feeders": 0, "leave_types": 0}
    assert count(office["path"], "area") == 0
    assert count(office["path"], "feeder") == 0


def test_the_seed_marker_says_a_person_cleared_it(client: TestClient, office: dict) -> None:
    client.post("/api/reset", json=reset_body("keep_calendar"))

    marker = setting(office["path"], SEED_SETTING_KEY)
    assert marker == reset_application.CLEARED_SEED_VALUE
    assert "operator" in marker


# --- what the operator is told to keep -------------------------------------


def test_a_backup_of_what_was_removed_is_taken_and_can_be_downloaded(
    client: TestClient, office: dict
) -> None:
    answer = client.post("/api/reset", json=reset_body("everything")).json()

    assert answer["backup"]["size_bytes"] > 0
    assert answer["backup"]["filename"].endswith(".zip")
    download = client.get(answer["backup"]["download"])

    assert download.status_code == 200
    assert download.content[:2] == b"PK"


def test_the_downloaded_backup_still_holds_the_previous_office(
    client: TestClient, office: dict
) -> None:
    answer = client.post("/api/reset", json=reset_body("everything")).json()
    package_bytes = client.get(answer["backup"]["download"]).content

    assert count(office["path"], "dtc") == 0
    # The package is the recovery path, so what it holds has to be the office that was
    # removed rather than the empty database that replaced it.
    staged = client.post(
        "/api/backup-validation",
        files={"file": ("previous-office.zip", package_bytes, "application/zip")},
    )
    assert staged.status_code == 200, staged.text
    contents = staged.json()["contents"]
    assert contents["dtcs"] == 4
    assert contents["finalisations"] == 1
    assert contents["holidays"] == 2


def test_a_copy_of_the_database_as_it_stood_is_kept_beside_it(
    client: TestClient, office: dict
) -> None:
    answer = client.post("/api/reset", json=reset_body("everything")).json()

    recovery = office["path"].parent / answer["recovery_copy"]
    assert recovery.exists()
    assert count(recovery, "dtc") == 4


def test_neither_copy_of_the_old_data_is_deleted_by_the_reset_itself(
    client: TestClient, office: dict
) -> None:
    client.post("/api/reset", json=reset_body("everything"))

    survivors = sidecars(office["path"])
    assert any("pre-restore" in name for name in survivors)


def test_the_exports_already_written_are_left_alone(client: TestClient, office: dict) -> None:
    # Export files live outside the database and are the previous office's issued documents.
    # Removing them would be a third decision the operator never made.
    exports = office["path"].parent / "exports"
    exports.mkdir()
    issued = exports / "2026-11.pdf"
    issued.write_bytes(b"%PDF-1.4 issued")

    client.post("/api/reset", json=reset_body("everything"))

    assert issued.read_bytes() == b"%PDF-1.4 issued"


# --- the replacement itself ------------------------------------------------


def test_the_database_is_replaced_rather_than_half_emptied(
    client: TestClient, office: dict
) -> None:
    # Every table is emptied by one replacement. A database that had been deleted from
    # table by table could be interrupted, and the interruption is exactly what leaves one
    # office's transformers beside another office's areas.
    answer = client.post("/api/reset", json=reset_body("everything")).json()

    assert answer["verified"]["integrity"] == "ok"
    assert answer["verified"]["foreign_keys"] == "ok"
    assert answer["schema_version"] == schema_module.SCHEMA_VERSION
    assert answer["verified"]["schema_version"] == schema_module.SCHEMA_VERSION


def test_no_staging_file_is_left_behind(client: TestClient, office: dict) -> None:
    client.post("/api/reset", json=reset_body("keep_calendar"))

    leftovers = [name for name in sidecars(office["path"]) if "staged" in name]
    assert leftovers == []


def test_the_whole_reset_runs_in_one_exclusive_barrier(
    client: TestClient, office: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The swap must not overlap a request that is holding the database open, so it runs
    # behind the same barrier a restore does. The barrier is observed rather than assumed.
    import dailyworkreg.interfaces.api as api_module

    seen: list[str] = []
    original = api_module.MaintenanceGate.exclusive

    def spy(self, *args, **kwargs):
        seen.append("exclusive")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(api_module.MaintenanceGate, "exclusive", spy)

    client.post("/api/reset", json=reset_body("everything"))

    assert seen == ["exclusive"]


def test_a_reset_while_a_restore_holds_the_barrier_is_refused(
    client: TestClient, office: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(self, *args, **kwargs):
        raise MaintenanceBusy("a restore is in progress")

    monkeypatch.setattr("dailyworkreg.interfaces.maintenance.MaintenanceGate.exclusive", busy)
    before = rows(office["path"], "dtc")

    answer = client.post("/api/reset", json=reset_body("everything"))

    assert answer.status_code == 503, answer.text
    assert rows(office["path"], "dtc") == before


def test_a_reset_waits_for_a_generation_rather_than_swapping_under_it(
    office: dict,
) -> None:
    # A generation holds a request slot for its whole run, so the barrier a reset takes is
    # the same one that keeps it out: without that, a register being built from the old
    # office's masters could be written into the new office's empty database.
    app = create_app(office["path"])
    gate = app.state.maintenance_gate
    # The drain timeout is shortened on the gate the routes actually hold, so the refusal
    # is reached in a moment rather than after the production wait.
    gate._timeout = 0.05
    client = TestClient(app)
    before = rows(office["path"], "dtc")

    with gate.request():
        answer = client.post("/api/reset", json=reset_body("everything"))

    assert answer.status_code == 503, answer.text
    assert answer.json()["error"]["constraint"] == "restore_in_progress"
    assert rows(office["path"], "dtc") == before


def test_the_generation_history_is_empty_because_every_draft_is_gone(
    client: TestClient, office: dict
) -> None:
    assert any(client.get("/api/generations").json().values())

    answer = client.post("/api/reset", json=reset_body("everything"))

    assert answer.status_code == 200
    assert all(runs == [] for runs in client.get("/api/generations").json().values())


def test_a_poll_that_arrives_after_the_reset_is_not_answered_with_a_dead_draft() -> None:
    # The record of a finished run lives in memory, not in the database, so emptying the
    # database would otherwise leave a poll answered with a draft id that no table holds.
    jobs = progress.GenerationJobs()
    running = threading.Event()

    def work(reporter) -> dict:
        running.wait(5)
        return {"run_id": "R-1"}

    job_id = jobs.begin(work)
    try:
        jobs.forget_finished()
        assert jobs.report(job_id)["finished"] is False
    finally:
        running.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not jobs.report(job_id)["finished"]:
            time.sleep(0.01)

    jobs.forget_finished()

    with pytest.raises(progress.UnknownJob):
        jobs.report(job_id)


# --- the page --------------------------------------------------------------


def test_the_page_offers_two_separate_resets_with_their_own_confirmations(
    client: TestClient,
) -> None:
    page = client.get("/").text

    assert 'id="reset-everything"' in page
    assert 'id="reset-keep-calendar"' in page
    assert 'id="reset-word-everything"' in page
    assert 'id="reset-word-calendar"' in page
    # Each button belongs to its own confirmation, so the operator cannot pick a scope and
    # then type the word beside the other one.
    assert page.index('id="reset-word-everything"') < page.index('id="reset-everything"')
    assert page.index('id="reset-word-calendar"') < page.index('id="reset-keep-calendar"')
    assert page.count("button-danger") >= 2


def test_the_page_says_what_each_scope_removes(client: TestClient) -> None:
    page = client.get("/").text

    assert "Start a new office" in page
    assert "Clear everything, but keep the calendar" in page
    assert "Clear everything, including the calendar" in page
    assert "General Holiday list" in page
    assert "Type <strong>RESET</strong> to confirm" in page


def test_the_page_loads_a_client_that_knows_about_the_reset(client: TestClient) -> None:
    page = client.get("/").text
    script = client.get("/static/app.js").text

    assert "app.js?v=reset-1" in page
    assert "'/api/reset/preview'" in script
    assert "'/api/reset'" in script
    assert "loadReset()" in script
