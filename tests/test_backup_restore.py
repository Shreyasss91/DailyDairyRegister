"""Backup and restore: the office's records survive, and every unsafe step is refused.

The tests build the operational state an office actually has — accepted masters, a staged
import, one revision covering two months, reviews, a lock, a manual entry, two months closed
from that same revision, and a correction that supersedes November — then back it up,
inspect the package, and restore it. Comparisons are made on whole rows and on the stored
snapshot itself, not on row counts, because a count is satisfied by the wrong rows.

The safety tests deliberately break things: a checksum, a page, a foreign key, the schema
version, the confirmation, the staged file, the replacement itself and the post-restore
check. Each one must leave the live database exactly as it was, or put it back.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import warnings
import zipfile
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from dailyworkreg.application import backup_restore, revisions
from dailyworkreg.application import finalisation as finalise
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application.draft_generation import DraftMode, DraftStatus, generate_draft
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.application.register_edits import add_entry
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.backup import Limits
from dailyworkreg.infrastructure.backup import package as package_module
from dailyworkreg.infrastructure.backup import replace as replace_module
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import imports as staged_imports
from dailyworkreg.infrastructure.sqlite import masters, revision_state
from dailyworkreg.infrastructure.sqlite import schema as schema_module
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities
from dailyworkreg.infrastructure.sqlite.snapshot import inspect
from dailyworkreg.interfaces.api import create_app
from dailyworkreg.interfaces.maintenance import MaintenanceBusy, MaintenanceGate
from fastapi.testclient import TestClient

AREA_CSV = b"area id,kannada name,english name\nA-01,Mangaluru,Mangaluru\n"

#: Tables whose whole contents have to survive a round trip. `sqlite_sequence` is included
#: because a restored database that renumbers its own inserts is not the same database.
COMPARED_TABLES = (
    "activity",
    "area",
    "date_lock",
    "date_review",
    "draft_revision",
    "dtc",
    "feeder",
    "general_holiday",
    "generation_entry",
    "generation_row",
    "generation_run",
    "generation_visit",
    "import_batch",
    "import_staging_row",
    "master_version",
    "month_finalisation",
    "month_state",
    "register",
    "revision_decision",
    "schema_migration",
)


# --- the operational state the office would actually have ------------------


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
    """A database holding the whole journey, with the identifiers of what it holds."""

    path = tmp_path / "office" / "dailyworkreg.db"
    path.parent.mkdir(parents=True)
    connection = open_database(path)
    try:
        seed_approved_identities(connection)
        for index in range(1, 7):
            masters.upsert_dtc(
                connection,
                dtc_id=f"SCD-{index}",
                name=f"Office DTC {index}",
                dtc_class="DEPT",
                kva=100.0 + index,
                canonical_feeder_id=f"F-0{(index % 2) + 1}",
            )
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-11-10",
            holiday_date=date(2026, 11, 10),
            name="Office holiday",
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
        # A staged import nobody accepted: it is operational state too.
        batch, _ = staged_imports.stage_bytes(connection, "area", "areas.csv", AREA_CSV)

        prepared = prepare_generation(connection, _spec(date(2026, 11, 1), date(2026, 12, 31)))
        draft = generate_draft(prepared.request)
        assert draft.status == DraftStatus.ALLOCATION_COMPLETE
        run_id = generations.save_run(connection, prepared, draft)

        edited = add_entry(
            connection,
            run_id,
            day=date(2026, 11, 5),
            kind="activity",
            title_en="Meter reading",
            title_kn="ಮೀಟರ್ ಓದುವಿಕೆ",
        )
        run_id = edited.run_id
        revisions.lock_date(connection, run_id, date(2026, 11, 6))
        _review(connection, run_id, "2026-11")
        _review(connection, run_id, "2026-12")

        # Two months closed from one revision, then November corrected.
        november = finalise.finalise_month(
            connection, run_id, "2026-11", note="November closed"
        )
        december = finalise.finalise_month(connection, run_id, "2026-12")

        correction = finalise.create_correction_revision(
            connection, run_id, reason="a visit recorded on the wrong day"
        )
        corrected = add_entry(
            connection,
            correction["correction_run_id"],
            day=date(2026, 11, 12),
            kind="activity",
            title_en="Added by the correction",
        )
        _review(connection, corrected.run_id, "2026-11")
        november_v2 = finalise.finalise_month(
            connection, corrected.run_id, "2026-11", note="November corrected"
        )
    finally:
        connection.close()

    return {
        "path": path,
        "run_id": run_id,
        "correction_run_id": correction["correction_run_id"],
        "corrected_run_id": corrected.run_id,
        "november": november["finalisation_id"],
        "december": december["finalisation_id"],
        "november_v2": november_v2["finalisation_id"],
        "batch_id": batch.batch_id,
    }


@pytest.fixture()
def client(office: dict) -> TestClient:
    return TestClient(create_app(office["path"]))


# --- helpers ---------------------------------------------------------------


def rows(path: Path, table: str) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return [
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY 1, 2"  # noqa: S608 - fixed table names
            )
        ]
    finally:
        connection.close()


def whole_database(path: Path) -> dict[str, list[tuple]]:
    return {table: rows(path, table) for table in COMPARED_TABLES}


def make_package(client: TestClient) -> tuple[str, bytes, dict]:
    answer = client.post("/api/backups")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    download = client.get(body["download"])
    assert download.status_code == 200
    return body["filename"], download.content, body


def rebuild(content: bytes, *, mutate=None, manifest_change=None, extra: dict | None = None) -> bytes:
    """A package with its payload mutated and/or its manifest changed, checksums kept right.

    Used to build the packages a broken install or a careless edit would produce, so the
    reader is tested against real archives rather than against a mocked error.
    """

    with zipfile.ZipFile(BytesIO(content)) as archive:
        manifest = archive.read(package_module.MANIFEST_NAME).decode("utf-8")
        payload = archive.read(package_module.PAYLOAD_NAME)
    import json

    document = json.loads(manifest)
    if mutate is not None:
        payload = mutate(payload)
        document["payload"]["bytes"] = len(payload)
        document["payload"]["sha256"] = __import__("hashlib").sha256(payload).hexdigest()
    if manifest_change is not None:
        manifest_change(document)
    entries = {
        package_module.MANIFEST_NAME: json.dumps(document, indent=2, sort_keys=True).encode(),
        package_module.PAYLOAD_NAME: payload,
    }
    if extra:
        entries.update(extra)
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def payload_of(content: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        return archive.read(package_module.PAYLOAD_NAME)


def write(tmp_path: Path, name: str, content: bytes) -> Path:
    target = tmp_path / name
    target.write_bytes(content)
    return target


def register_content_of(workbook_bytes: bytes) -> list[tuple]:
    """What an exported workbook says, independent of the archive's own timestamps."""

    from openpyxl import load_workbook

    book = load_workbook(BytesIO(workbook_bytes))
    register = book["Register"]
    values = [tuple(row) for row in register.iter_rows(values_only=True)]
    issued = [tuple(row) for row in book["Metadata"].iter_rows(values_only=True)]
    return values + issued


def register_content(client: TestClient, finalisation_id: str) -> list[tuple]:
    answer = client.get(f"/api/finalisations/{finalisation_id}/export.xlsx")
    assert answer.status_code == 200, answer.text
    return register_content_of(answer.content)


def validate(office: dict, path: Path, *, token: str = "tok") -> backup_restore.StagedRestore:
    return backup_restore.validate_package(path, target_db=office["path"], token=token)


def refused(path: Path, office: dict, *, token: str = "tok") -> str:
    with pytest.raises(backup_restore.BackupError) as failure:
        validate(office, path, token=token)
    return failure.value.constraint


# --- a consistent backup ---------------------------------------------------


def test_a_backup_holds_the_whole_office_state(office, tmp_path):
    connection = open_database(office["path"])
    try:
        package = backup_restore.create_backup(connection)
    finally:
        connection.close()

    assert package.filename.endswith(".zip")
    assert package.summary["format"] == package_module.FORMAT_NAME
    assert package.summary["format_version"] == package_module.FORMAT_VERSION
    assert package.summary["schema_version"] == schema_module.SCHEMA_VERSION
    assert package.verified["integrity"] == "ok"
    assert package.summary["contents"]["finalisations"] == 3
    assert package.summary["contents"]["import_batches"] >= 1

    payload = tmp_path / "payload.db"
    payload.write_bytes(payload_of(package.content))
    facts = inspect(payload)
    assert facts.healthy
    for table in ("month_finalisation", "month_state", "generation_run", "date_lock"):
        assert rows(payload, table) == rows(office["path"], table), table


def test_a_backup_of_a_wal_database_holds_committed_work(office, tmp_path):
    connection = open_database(office["path"])
    try:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        masters.upsert_dtc(
            connection,
            dtc_id="SCD-COMMITTED",
            name="Committed after opening",
            dtc_class="DEPT",
            kva=99.0,
            canonical_feeder_id="F-01",
        )
        package = backup_restore.create_backup(connection)
    finally:
        connection.close()

    payload = tmp_path / "payload.db"
    payload.write_bytes(payload_of(package.content))
    assert any(row[0] == "SCD-COMMITTED" for row in rows(payload, "dtc"))


def test_an_uncommitted_change_is_not_in_the_backup(office, tmp_path):
    writer = sqlite3.connect(str(office["path"]), isolation_level=None)
    reader = sqlite3.connect(str(office["path"]), isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            """
            INSERT INTO dtc (dtc_id, name, dtc_class, kva, canonical_feeder_id, active,
                             revision, valid_from_version, updated_at)
            VALUES ('SCD-PENDING', 'Not committed', 'DEPT', 50.0, 'F-01', 1,
                    1, 1, '2026-11-16T00:00:00Z')
            """
        )
        package = backup_restore.create_backup(reader)
        writer.execute("ROLLBACK")
    finally:
        writer.close()
        reader.close()

    payload = tmp_path / "payload.db"
    payload.write_bytes(payload_of(package.content))
    identifiers = {row[0] for row in rows(payload, "dtc")}
    assert "SCD-PENDING" not in identifiers
    assert "SCD-PENDING" not in {row[0] for row in rows(office["path"], "dtc")}


def test_the_live_database_is_usable_after_a_backup(office):
    connection = open_database(office["path"])
    try:
        backup_restore.create_backup(connection)
        masters.upsert_dtc(
            connection,
            dtc_id="SCD-AFTER",
            name="Written after the backup",
            dtc_class="DEPT",
            kva=77.0,
            canonical_feeder_id="F-02",
        )
        stored = connection.execute(
            "SELECT count(*) FROM dtc WHERE dtc_id = 'SCD-AFTER'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert stored == 1


def test_a_backup_changes_nothing_in_the_live_database(office):
    before = whole_database(office["path"])
    connection = open_database(office["path"])
    try:
        backup_restore.create_backup(connection)
    finally:
        connection.close()
    assert whole_database(office["path"]) == before


def test_a_backup_can_be_made_repeatedly(office):
    before = whole_database(office["path"])
    summaries = []
    connection = open_database(office["path"])
    try:
        for _ in range(3):
            summaries.append(backup_restore.create_backup(connection).summary["contents"])
    finally:
        connection.close()
    assert summaries[0] == summaries[1] == summaries[2]
    assert whole_database(office["path"]) == before


def test_the_backup_does_not_depend_on_where_it_was_made(office, tmp_path):
    connection = open_database(office["path"])
    try:
        package = backup_restore.create_backup(connection)
    finally:
        connection.close()

    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    moved = elsewhere / "moved.zip"
    moved.write_bytes(package.content)
    staged = validate(office, moved)
    assert staged.facts.healthy
    assert staged.migrations_applied == ()
    assert staged.source_schema_version == schema_module.SCHEMA_VERSION


def test_the_backup_carries_no_machine_specific_detail(office):
    connection = open_database(office["path"])
    try:
        package = backup_restore.create_backup(connection)
    finally:
        connection.close()

    text = str(package.manifest)
    assert str(office["path"]) not in text
    assert "Temp" not in text and "Users" not in text
    assert set(package.summary) >= {
        "format",
        "format_version",
        "created_at",
        "schema_version",
        "payload_bytes",
        "payload_sha256",
        "contents",
    }


# --- package validation ----------------------------------------------------


def corrupt_page_holding(payload: bytes, marker: bytes) -> bytes:
    """Overwrite the 4 KiB page that carries a known stored string.

    Corrupting a page found by its content is deterministic, where corrupting an arbitrary
    offset may land in a free page that SQLite never reads.
    """

    index = payload.find(marker)
    assert index > 4096, "the marker is not in a data page"
    page = index // 4096 + 1
    start = (page - 1) * 4096
    return payload[:start] + b"\xff" * 4096 + payload[start + 4096 :]


def test_a_good_package_is_accepted(office, client, tmp_path):
    _, content, _ = make_package(client)
    package = write(tmp_path, "good.zip", content)

    staged = validate(office, package)

    assert staged.facts.healthy
    assert staged.migrations_applied == ()
    assert staged.staged.parent == office["path"].parent
    assert staged.staged.name.startswith(office["path"].name + ".restore-")


def test_a_file_that_is_not_an_archive_is_refused(office, tmp_path):
    plain = write(tmp_path, "not-a-backup.zip", b"this is not a backup at all")
    assert refused(plain, office) == backup_restore.C_PACKAGE_UNREADABLE


def test_an_archive_that_is_not_a_backup_is_refused(office, tmp_path):
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(package_module.MANIFEST_NAME, '{"format": "something-else"}')
        archive.writestr(package_module.PAYLOAD_NAME, b"whatever")
    assert refused(write(tmp_path, "p.zip", out.getvalue()), office) == (
        backup_restore.C_PACKAGE_FORMAT_UNKNOWN
    )


def test_a_format_version_this_reader_does_not_know_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    future = rebuild(
        content, manifest_change=lambda document: document.update({"format_version": 99})
    )
    assert refused(write(tmp_path, "p.zip", future), office) == (
        backup_restore.C_PACKAGE_FORMAT_UNSUPPORTED
    )


def test_a_package_without_a_manifest_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(content)) as archive:
        payload = archive.read(package_module.PAYLOAD_NAME)
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(package_module.PAYLOAD_NAME, payload)
    assert refused(write(tmp_path, "p.zip", out.getvalue()), office) == (
        backup_restore.C_PACKAGE_ENTRY_MISSING
    )


def test_a_package_without_a_payload_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(content)) as archive:
        manifest = archive.read(package_module.MANIFEST_NAME)
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(package_module.MANIFEST_NAME, manifest)
    assert refused(write(tmp_path, "p.zip", out.getvalue()), office) == (
        backup_restore.C_PACKAGE_ENTRY_MISSING
    )


def test_an_unexpected_entry_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    extra = rebuild(content, extra={"notes.txt": b"hello"})
    assert refused(write(tmp_path, "p.zip", extra), office) == (
        backup_restore.C_PACKAGE_ENTRIES
    )


def test_a_path_that_climbs_out_of_the_archive_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    climbing = rebuild(content, extra={"../escaped.db": b"payload"})
    assert refused(write(tmp_path, "p.zip", climbing), office) == (
        backup_restore.C_PACKAGE_ENTRIES
    )


def test_an_absolute_path_in_the_archive_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    absolute = rebuild(content, extra={"/etc/passwd": b"x"})
    assert refused(write(tmp_path, "p.zip", absolute), office) == (
        backup_restore.C_PACKAGE_ENTRIES
    )


def test_duplicate_entries_are_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    with zipfile.ZipFile(BytesIO(content)) as archive:
        manifest = archive.read(package_module.MANIFEST_NAME)
        payload = archive.read(package_module.PAYLOAD_NAME)
    out = BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile warns about the duplicate
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr(package_module.MANIFEST_NAME, manifest)
            archive.writestr(package_module.PAYLOAD_NAME, payload)
            archive.writestr(package_module.PAYLOAD_NAME, payload)
    assert refused(write(tmp_path, "p.zip", out.getvalue()), office) == (
        backup_restore.C_PACKAGE_ENTRIES
    )


def test_a_payload_that_does_not_match_its_checksum_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    with zipfile.ZipFile(BytesIO(content)) as archive:
        manifest = archive.read(package_module.MANIFEST_NAME)
        payload = archive.read(package_module.PAYLOAD_NAME)
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(package_module.MANIFEST_NAME, manifest)
        archive.writestr(package_module.PAYLOAD_NAME, payload + b"tampered")
    assert refused(write(tmp_path, "p.zip", out.getvalue()), office) == (
        backup_restore.C_PACKAGE_CHECKSUM
    )


def test_a_truncated_package_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    truncated = write(tmp_path, "p.zip", content[: len(content) // 2])
    assert refused(truncated, office) in {
        backup_restore.C_PACKAGE_UNREADABLE,
        backup_restore.C_PACKAGE_ENTRY_MISSING,
    }


def test_a_payload_that_is_not_a_database_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    garbled = rebuild(content, mutate=lambda payload: b"not a database" * 100)
    assert refused(write(tmp_path, "p.zip", garbled), office) == (
        backup_restore.C_PACKAGE_NOT_A_DATABASE
    )


def test_a_database_that_fails_its_integrity_check_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    damaged = rebuild(
        content, mutate=lambda payload: corrupt_page_holding(payload, b"Office DTC 3")
    )
    assert refused(write(tmp_path, "p.zip", damaged), office) == (
        backup_restore.C_PACKAGE_INTEGRITY
    )


def test_a_database_whose_rows_reference_missing_records_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    payload = payload_of(content)
    scratch = write(tmp_path, "orphaned.db", payload)
    connection = sqlite3.connect(scratch, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM generation_run")
    finally:
        connection.close()
    orphaned = rebuild(content, mutate=lambda _: scratch.read_bytes())
    assert refused(write(tmp_path, "p.zip", orphaned), office) == (
        backup_restore.C_PACKAGE_FOREIGN_KEYS
    )


def test_a_backup_from_a_newer_tool_is_refused_rather_than_approximated(office, client, tmp_path):
    _, content, _ = make_package(client)
    payload = payload_of(content)
    scratch = write(tmp_path, "newer.db", payload)
    connection = sqlite3.connect(scratch, isolation_level=None)
    try:
        connection.execute(
            "INSERT INTO schema_migration (version, name, applied_at) VALUES (99, 'future', 'now')"
        )
    finally:
        connection.close()
    newer = rebuild(content, mutate=lambda _: scratch.read_bytes())
    assert refused(write(tmp_path, "p.zip", newer), office) == (
        backup_restore.C_PACKAGE_SCHEMA_NEWER
    )


def test_a_backup_from_a_supported_older_tool_is_migrated_on_the_staging_copy(
    office, client, tmp_path
):
    _, content, _ = make_package(client)
    payload = payload_of(content)
    scratch = write(tmp_path, "older.db", payload)
    connection = sqlite3.connect(scratch, isolation_level=None)
    try:
        connection.execute("DELETE FROM schema_migration WHERE version >= 5")
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall():
            connection.execute(f"DROP TRIGGER {name}")  # noqa: S608 - names read from the database
    finally:
        connection.close()
    # A backup written by the earlier tool declares the schema it was made at, so the
    # manifest and the payload agree in the way a real older package would.
    older = rebuild(
        content,
        mutate=lambda _: scratch.read_bytes(),
        manifest_change=lambda document: document["application"].update({"schema_version": 4}),
    )
    package = write(tmp_path, "older.zip", older)

    before = whole_database(office["path"])
    staged = validate(office, package)

    assert staged.source_schema_version == 4
    assert staged.migrations_applied == (5, 6)
    assert staged.facts.schema_version == schema_module.SCHEMA_VERSION
    assert not staged.facts.missing_triggers
    assert staged.warnings  # the operator is told the backup was brought forward
    # The live database was not migrated: only the staging copy was.
    live = inspect(office["path"])
    assert live.schema_version == schema_module.SCHEMA_VERSION
    assert whole_database(office["path"]) == before


def test_an_oversized_package_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    package = write(tmp_path, "big.zip", content)
    with pytest.raises(backup_restore.BackupError) as failure:
        backup_restore.validate_package(
            package,
            target_db=office["path"],
            token="big",
            limits=Limits(max_package_bytes=len(content) // 2),
        )
    assert failure.value.constraint == backup_restore.C_PACKAGE_TOO_LARGE


def test_a_payload_larger_than_the_limit_is_refused_before_it_is_written(
    office, client, tmp_path
):
    _, content, _ = make_package(client)
    package = write(tmp_path, "p.zip", content)
    with pytest.raises(backup_restore.BackupError) as failure:
        backup_restore.validate_package(
            package,
            target_db=office["path"],
            token="payload",
            limits=Limits(max_payload_bytes=1024),
        )
    assert failure.value.constraint == backup_restore.C_PACKAGE_TOO_LARGE


def test_a_package_whose_issued_records_do_not_hold_together_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    payload = payload_of(content)
    scratch = write(tmp_path, "broken.db", payload)
    connection = sqlite3.connect(scratch, isolation_level=None)
    try:
        # A month pointer aimed at a record that is not there: the kind of damage that only
        # shows up when somebody opens the month. The record triggers do not guard this
        # table, so the damage is written rather than simulated.
        connection.execute("UPDATE month_state SET latest_finalisation_id = 'MF-not-here'")
    finally:
        connection.close()
    broken = rebuild(content, mutate=lambda _: scratch.read_bytes())
    assert refused(write(tmp_path, "p.zip", broken), office) == (
        backup_restore.C_PACKAGE_STRUCTURE
    )


def test_the_staged_copy_is_beside_the_database_and_never_in_the_working_tree(office, client, tmp_path):
    _, content, _ = make_package(client)
    staged = validate(office, write(tmp_path, "p.zip", content))

    assert staged.staged.parent == office["path"].parent
    assert staged.staged.suffix == ".staged"
    assert not (tmp_path / "dailyworkreg").exists()
    assert "strategies" not in str(staged.staged).lower()


# --- restore: the whole journey, and every way it can fail ------------------


def test_a_restore_reproduces_every_row_of_the_office_state(office, client, tmp_path):
    _, content, _ = make_package(client)
    expected = inspect(write(tmp_path, "expected.db", payload_of(content))).counts

    validation = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    )
    assert validation.status_code == 200, validation.text
    body = validation.json()
    assert body["can_restore"] is True
    assert body["confirmation_word"] == backup_restore.CONFIRMATION_WORD

    restored = client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    )
    assert restored.status_code == 200, restored.text
    answer = restored.json()
    assert answer["restored"] is True
    assert answer["verified"]["integrity"] == "ok"
    assert answer["verified"]["foreign_keys"] == "ok"
    assert answer["verified"]["schema_version"] == schema_module.SCHEMA_VERSION
    assert len(answer["verified"]["issued_versions"]) == 3
    assert answer["rollback_copy"].startswith(office["path"].name + ".pre-restore-")

    restored_counts = inspect(office["path"], read_only=False).counts
    assert restored_counts == expected


def test_the_restored_database_opens_through_the_ordinary_path(office, client):
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    client.post(
        "/api/restore", json={"restore_token": body["restore_token"], "confirmation": "REPLACE"}
    )

    connection = open_database(office["path"])
    try:
        version = connection.execute("SELECT max(version) FROM schema_migration").fetchone()[0]
        assert version == schema_module.SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_a_restore_without_confirmation_changes_nothing(office, client):
    before = whole_database(office["path"])
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()

    for attempt in ({}, {"restore_token": body["restore_token"]}, {"restore_token": body["restore_token"], "confirmation": "yes"}):
        answer = client.post("/api/restore", json=attempt)
        assert answer.status_code == 400
        assert answer.json()["error"]["constraint"] == (
            backup_restore.C_RESTORE_CONFIRMATION_REQUIRED
        )
    assert whole_database(office["path"]) == before
    # The validated copy is still waiting, so the operator can confirm properly next time.
    assert body["restore_token"]
    staged = list(office["path"].parent.glob(office["path"].name + ".restore-*.staged"))
    assert len(staged) == 1


def test_a_package_that_was_never_validated_cannot_be_restored(office, client):
    before = whole_database(office["path"])
    answer = client.post(
        "/api/restore", json={"restore_token": "made-up", "confirmation": "REPLACE"}
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == backup_restore.C_RESTORE_TOKEN_UNKNOWN
    assert whole_database(office["path"]) == before


def test_an_invalid_backup_leaves_the_active_database_untouched(office, client, tmp_path):
    before = whole_database(office["path"])
    answer = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", b"not a backup", "application/zip")},
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == backup_restore.C_PACKAGE_UNREADABLE
    assert whole_database(office["path"]) == before
    assert not list(
        office["path"].parent.glob(office["path"].name + ".restore-*.staged")
    )


def test_a_staged_copy_that_changed_after_validation_is_not_installed(office, client):
    before = whole_database(office["path"])
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    staged = next(
        office["path"].parent.glob(office["path"].name + ".restore-*.staged")
    )
    staged.write_bytes(b"different bytes entirely")

    answer = client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == (
        backup_restore.C_RESTORE_REPLACEMENT_FAILED
    )
    assert whole_database(office["path"]) == before


def test_a_replacement_that_fails_keeps_the_original_and_a_recovery_copy(
    office, client, monkeypatch
):
    before = whole_database(office["path"])
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()

    def refuse(*_args, **_kwargs):
        raise replace_module.ReplacementError(
            "replacement_failed", "the file could not be replaced"
        )

    monkeypatch.setattr(backup_restore, "install_database", refuse)
    answer = client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    )

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == (
        backup_restore.C_RESTORE_REPLACEMENT_FAILED
    )
    assert whole_database(office["path"]) == before
    rollbacks = list(
        office["path"].parent.glob(office["path"].name + ".pre-restore-*.db")
    )
    assert rollbacks and inspect(rollbacks[0]).healthy


def test_a_post_restore_check_that_fails_puts_the_previous_database_back(
    office, client, monkeypatch
):
    before = whole_database(office["path"])
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()

    def broken(_path):
        raise backup_restore.BackupError(
            backup_restore.C_RESTORE_VERIFICATION_FAILED, "the restored file did not open"
        )

    monkeypatch.setattr(backup_restore, "verify_restored", broken)
    answer = client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    )

    assert answer.status_code == 400
    error = answer.json()["error"]
    assert error["constraint"] == backup_restore.C_RESTORE_VERIFICATION_FAILED
    assert error["restored"] is False
    assert error["rollback"].startswith(office["path"].name + ".pre-restore-")
    # The office's database is exactly as it was, and it still opens.
    assert whole_database(office["path"]) == before
    connection = open_database(office["path"])
    try:
        assert connection.execute("SELECT count(*) FROM month_finalisation").fetchone()[0] == 3
    finally:
        connection.close()


def test_no_request_is_admitted_while_a_restore_holds_the_barrier(office, tmp_path):
    app = create_app(office["path"])
    gate = app.state.maintenance_gate
    client = TestClient(app)
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()

    holding = threading.Event()
    release = threading.Event()
    original = backup_restore.restore

    def held(*args, **kwargs):
        holding.set()
        release.wait(10)
        return original(*args, **kwargs)

    import dailyworkreg.interfaces.api as api_module

    api_module.backup_restore.restore = held
    try:
        outcome = {}

        def run_restore():
            outcome["answer"] = TestClient(app).post(
                "/api/restore",
                json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
            )

        worker = threading.Thread(target=run_restore)
        worker.start()
        assert holding.wait(10), "the restore never reached the barrier"
        while not gate.draining:  # the flag is set before the exclusive hold is entered
            time.sleep(0.01)

        refused_here = client.get("/api/health")
        assert refused_here.status_code == 503
        assert refused_here.json()["error"]["constraint"] == "restore_in_progress"

        release.set()
        worker.join(20)
    finally:
        api_module.backup_restore.restore = original
        release.set()
    assert outcome["answer"].status_code == 200
    assert client.get("/api/health").status_code == 200


def test_the_barrier_gives_up_rather_than_replacing_under_a_live_connection():
    gate = MaintenanceGate(timeout=0.05)
    with gate.request():
        with pytest.raises(MaintenanceBusy):
            with gate.exclusive():
                pytest.fail("the barrier let a replacement through while a request was open")
        assert gate.draining is False
    with gate.exclusive():
        assert gate.draining is True
    assert gate.draining is False and gate.active == 0


def test_a_request_is_refused_while_the_barrier_is_draining():
    gate = MaintenanceGate(timeout=1.0)
    with gate.exclusive():
        with pytest.raises(MaintenanceBusy):
            with gate.request():
                pytest.fail("a request was admitted during a replacement")
    with gate.request():
        pass


def test_pruning_removes_only_this_tools_staged_copies(office):
    directory = office["path"].parent
    stale = directory / (office["path"].name + ".restore-old.staged")
    stale.write_bytes(b"stale")
    unrelated = directory / "notes.txt"
    unrelated.write_text("not mine", encoding="utf-8")

    removed = backup_restore.prune_staging(office["path"])

    assert stale.name in removed
    assert not stale.exists()
    assert unrelated.exists()


def test_a_refused_upload_does_not_discard_a_backup_already_accepted(office, client):
    """Cleaning up after a bad file must not clean up the good one the operator chose."""

    _, content, _ = make_package(client)
    accepted = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    )
    assert accepted.status_code == 200, accepted.text
    token = accepted.json()["restore_token"]

    refused = client.post(
        "/api/backup-validation",
        files={"file": ("broken.zip", b"not a backup", "application/zip")},
    )
    assert refused.status_code == 400

    staged = list(office["path"].parent.glob(office["path"].name + ".restore-*.staged"))
    assert len(staged) == 1, [path.name for path in staged]

    answer = client.post(
        "/api/restore", json={"restore_token": token, "confirmation": "REPLACE"}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["restored"] is True


# --- the issued versions themselves ---------------------------------------


def test_every_issued_snapshot_survives_byte_for_byte(office, client):
    connection = sqlite3.connect(str(office["path"]))
    connection.row_factory = sqlite3.Row
    try:
        before = [
            dict(row)
            for row in connection.execute(
                "SELECT finalisation_id, run_id, register_id, month, sequence, "
                "parent_finalisation_id, correction_of_run_id, finalised_at, fingerprint, "
                "content_json, acknowledged_json, readiness_json, master_version, note "
                "FROM month_finalisation ORDER BY finalisation_id"
            )
        ]
        pointers = [
            tuple(row) for row in connection.execute("SELECT * FROM month_state ORDER BY 1, 2")
        ]
    finally:
        connection.close()

    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    assert client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    ).status_code == 200

    connection = sqlite3.connect(str(office["path"]))
    connection.row_factory = sqlite3.Row
    try:
        after = [
            dict(row)
            for row in connection.execute(
                "SELECT finalisation_id, run_id, register_id, month, sequence, "
                "parent_finalisation_id, correction_of_run_id, finalised_at, fingerprint, "
                "content_json, acknowledged_json, readiness_json, master_version, note "
                "FROM month_finalisation ORDER BY finalisation_id"
            )
        ]
        pointers_after = [
            tuple(row) for row in connection.execute("SELECT * FROM month_state ORDER BY 1, 2")
        ]
    finally:
        connection.close()

    assert after == before
    assert pointers_after == pointers
    assert {row["finalisation_id"] for row in after} == {
        office["november"],
        office["december"],
        office["november_v2"],
    }


def test_a_superseded_version_still_exports_itself_after_a_restore(office, client):
    before = {
        identifier: (
            client.get(f"/api/finalisations/{identifier}/export.xlsx").content,
            client.get(f"/finalisations/{identifier}/print").text,
        )
        for identifier in (office["november"], office["november_v2"], office["december"])
    }

    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    assert client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    ).status_code == 200

    for identifier, (workbook, markup) in before.items():
        # The workbook is compared by what it holds rather than by its bytes: the archive
        # carries its own entry timestamps, which differ between two writes minutes apart.
        assert register_content(client, identifier) == register_content_of(workbook)
        assert client.get(f"/finalisations/{identifier}/print").text == markup
    # And the two versions of November are still two, with the earlier one superseded.
    first = client.get(f"/api/finalisations/{office['november']}").json()
    second = client.get(f"/api/finalisations/{office['november_v2']}").json()
    assert first["is_latest"] is False and second["is_latest"] is True
    assert second["parent_finalisation_id"] == office["november"]


def test_the_protections_are_installed_and_still_refuse_after_a_restore(office, client):
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    assert client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    ).status_code == 200

    facts = inspect(office["path"], read_only=False)
    assert not facts.missing_triggers
    assert len(facts.triggers) == 17

    connection = sqlite3.connect(str(office["path"]), isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO generation_entry (entry_id, run_id, row_date, kind, title_en,
                                              title_kn, provenance)
                VALUES ('E-new', ?, '2026-11-07', 'activity', 'Inserted later', NULL, 'manual')
                """,
                (office["run_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE month_finalisation SET note = 'rewritten' WHERE finalisation_id = ?",
                (office["november"],),
            )
    finally:
        connection.close()


def test_masters_reviews_locks_entries_and_staged_imports_survive(office, client):
    before = {
        table: rows(office["path"], table)
        for table in (
            "dtc",
            "area",
            "feeder",
            "activity",
            "general_holiday",
            "date_review",
            "date_lock",
            "generation_entry",
            "import_batch",
            "import_staging_row",
        )
    }

    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    assert client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    ).status_code == 200

    for table, expected in before.items():
        assert rows(office["path"], table) == expected, table
    assert any(row[0] == office["batch_id"] for row in rows(office["path"], "import_batch"))
    assert any(row[1] == "2026-11-06" for row in rows(office["path"], "date_lock"))


def test_a_correction_after_a_restore_leaves_the_earlier_versions_untouched(office, client):
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    assert client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    ).status_code == 200

    def snapshots() -> dict:
        connection = sqlite3.connect(str(office["path"]))
        connection.row_factory = sqlite3.Row
        try:
            return {
                row["finalisation_id"]: dict(row)
                for row in connection.execute("SELECT * FROM month_finalisation")
            }
        finally:
            connection.close()

    before = snapshots()
    connection = open_database(office["path"])
    try:
        correction = finalise.create_correction_revision(
            connection,
            office["corrected_run_id"],
            reason="a later correction on the restored database",
        )
        # An edit stores its own revision, so the run to finalise is the one the edit
        # returned rather than the one the correction opened.
        correction_id = add_entry(
            connection,
            correction["correction_run_id"],
            day=date(2026, 11, 13),
            kind="activity",
            title_en="Corrected again",
        ).run_id
        for item in revisions.review_state(connection, correction_id)["dates"]:
            if item["date"].startswith("2026-11"):
                revisions.mark_reviewed(
                    connection,
                    correction_id,
                    date.fromisoformat(item["date"]),
                    expected_fingerprint=item["fingerprint"],
                )
        later = finalise.finalise_month(connection, correction_id, "2026-11")
    finally:
        connection.close()

    after = snapshots()
    assert later["sequence"] == 3
    for identifier in (office["november"], office["november_v2"], office["december"]):
        assert after[identifier] == before[identifier], identifier
    assert after[later["finalisation_id"]]["parent_finalisation_id"] == office["november_v2"]


def test_no_temporary_file_is_left_beside_the_database_after_a_restore(office, client):
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    assert client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    ).status_code == 200

    leftovers = [
        path.name
        for path in office["path"].parent.iterdir()
        if path.name.startswith(office["path"].name + ".restore-")
    ]
    assert leftovers == []
    # The recovery copy remains, because it is the operator's to delete.
    assert list(office["path"].parent.glob(office["path"].name + ".pre-restore-*.db"))


def test_a_backup_taken_after_a_restore_holds_the_same_records(office, client, tmp_path):
    _, content, _ = make_package(client)
    body = client.post(
        "/api/backup-validation",
        files={"file": ("backup.zip", content, "application/zip")},
    ).json()
    assert client.post(
        "/api/restore",
        json={"restore_token": body["restore_token"], "confirmation": "REPLACE"},
    ).status_code == 200

    _, second, _ = make_package(client)
    first_payload = write(tmp_path, "first.db", payload_of(content))
    second_payload = write(tmp_path, "second.db", payload_of(second))
    for table in ("month_finalisation", "month_state", "generation_run", "dtc"):
        assert rows(second_payload, table) == rows(first_payload, table), table
