"""The Act 13 integrity gaps, each pinned by a test that would have caught it.

Five corrections are proven here, and each test exercises the real implementation against a
real database or a real archive:

*   A backup never commits, or otherwise moves, a transaction the caller began.
*   A backup or a recovery copy is not called healthy while a finalisation protection is
    missing, while an older payload is still allowed to reach the migration path.
*   A month pointer is checked against the register and month it claims, not only against
    the existence of the record it names.
*   A recovery copy never overwrites an earlier one, and the copy belonging to this restore
    is the one reported.
*   Every manifest claim is compared with the payload it describes.

The richer fixture (masters, one revision over two months, reviews, a lock, two closed
months and a correction) is shared with the Act 13 module rather than rebuilt here, because
two copies of it would drift.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from dailyworkreg.application import backup_restore
from dailyworkreg.infrastructure.backup import package as package_module
from dailyworkreg.infrastructure.backup import replace as replace_module
from dailyworkreg.infrastructure.sqlite import schema as schema_module
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.snapshot import (
    FINALISATION_TRIGGERS,
    CopyRefused,
    inspect,
    snapshot_to,
)
from test_backup_restore import (  # noqa: PGH003 - the shared fixtures, by design
    client,
    make_package,
    office,
    payload_of,
    rebuild,
    whole_database,
    write,
)

# --- Correction A: the caller's transaction is never committed --------------


def test_a_backup_refuses_rather_than_committing_the_callers_open_change(office):
    """The defect: `snapshot_to` called `connection.commit()` on the caller's connection."""

    connection = open_database(office["path"])
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO dtc (dtc_id, name, dtc_class, kva, canonical_feeder_id, active,
                             revision, valid_from_version, updated_at)
            VALUES ('SCD-IN-FLIGHT', 'Not committed yet', 'DEPT', 55.0, 'F-01', 1,
                    1, 1, '2026-11-16T00:00:00Z')
            """
        )
        assert connection.in_transaction is True

        with pytest.raises(backup_restore.BackupError) as refusal:
            backup_restore.create_backup(connection)

        assert refusal.value.constraint == backup_restore.C_BACKUP_SOURCE_BUSY
        # The refusal, not a commit: the caller's transaction is still open.
        assert connection.in_transaction is True
        connection.execute("ROLLBACK")
        assert connection.in_transaction is False
    finally:
        connection.close()

    # The row the caller rolled back is nowhere, and a later backup does not invent it.
    live = sqlite3.connect(office["path"])
    try:
        assert "SCD-IN-FLIGHT" not in {row[0] for row in live.execute("SELECT dtc_id FROM dtc")}
    finally:
        live.close()
    connection = open_database(office["path"])
    try:
        package = backup_restore.create_backup(connection)
    finally:
        connection.close()
    payload = Path(str(office["path"]) + ".check.db")
    payload.write_bytes(payload_of(package.content))
    copied = sqlite3.connect(payload)
    try:
        identifiers = {row[0] for row in copied.execute("SELECT dtc_id FROM dtc")}
    finally:
        copied.close()
        payload.unlink()
    assert "SCD-IN-FLIGHT" not in identifiers


def test_the_copy_helper_itself_refuses_an_open_transaction(office, tmp_path):
    connection = sqlite3.connect(str(office["path"]), isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("SELECT count(*) FROM dtc").fetchone()
        destination = tmp_path / "copy.db"
        with pytest.raises(CopyRefused) as refusal:
            snapshot_to(connection, destination)
        assert refusal.value.reason == "source_transaction_open"
        assert connection.in_transaction is True
        connection.execute("ROLLBACK")
        assert not destination.exists()
    finally:
        connection.close()


def test_a_copy_refuses_to_destroy_a_file_that_is_already_there(office, tmp_path):
    destination = tmp_path / "already-here.db"
    destination.write_bytes(b"an earlier recovery copy")

    connection = sqlite3.connect(str(office["path"]))
    try:
        with pytest.raises(CopyRefused) as refusal:
            snapshot_to(connection, destination)
    finally:
        connection.close()

    assert refusal.value.reason == "destination_exists"
    assert destination.read_bytes() == b"an earlier recovery copy"


# --- Correction B: a verified backup needs all seventeen protections --------


def without_trigger(path: Path, name: str) -> None:
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute(f"DROP TRIGGER {name}")  # noqa: S608 - a name from the fixed set
    finally:
        connection.close()


def copy_of(office: dict, destination: Path) -> Path:
    connection = sqlite3.connect(str(office["path"]))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        snapshot_to(connection, destination, overwrite=True)
    finally:
        connection.close()
    return destination


def test_a_backup_is_refused_when_a_protection_is_missing(office, tmp_path):
    damaged = copy_of(office, tmp_path / "damaged" / "dailyworkreg.db")
    removed = "trg_finalised_generation_entry_update"
    without_trigger(damaged, removed)

    facts = inspect(damaged)
    assert facts.readable and facts.integrity_ok
    assert facts.missing_triggers == {removed}
    assert facts.healthy is False

    connection = sqlite3.connect(str(damaged))
    try:
        with pytest.raises(backup_restore.BackupError) as refusal:
            backup_restore.create_backup(connection)
    finally:
        connection.close()

    assert refusal.value.constraint == backup_restore.C_BACKUP_PROTECTIONS
    assert removed in refusal.value.detail


def test_a_recovery_copy_is_not_declared_healthy_without_a_protection(office, tmp_path):
    damaged = copy_of(office, tmp_path / "damaged" / "dailyworkreg.db")
    removed = "trg_finalisation_delete"
    without_trigger(damaged, removed)

    with pytest.raises(replace_module.ReplacementError) as refusal:
        replace_module.rollback_copy(damaged, stamp="20260101T000000Z")

    assert refusal.value.reason == "rollback_copy_unhealthy"
    assert removed in refusal.value.detail
    # A recovery copy that failed its own checks is not left behind as though it were one.
    assert list(damaged.parent.glob(damaged.name + ".pre-restore-*.db")) == []


def test_the_whole_current_trigger_set_is_what_health_requires():
    assert len(FINALISATION_TRIGGERS) == 17


def test_an_older_payload_is_still_readable_before_its_migration(office, client, tmp_path):
    """The pre-migration check must not require the triggers an older schema never had."""

    _, content, _ = make_package(client)
    payload = payload_of(content)
    scratch = write(tmp_path, "older.db", payload)
    connection = sqlite3.connect(scratch, isolation_level=None)
    try:
        connection.execute("DELETE FROM schema_migration WHERE version >= 5")
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall():
            connection.execute(f"DROP TRIGGER {name}")  # noqa: S608 - names from the database
    finally:
        connection.close()

    older_facts = inspect(scratch)
    assert older_facts.readable is True
    assert older_facts.healthy is False  # protections gone, so not a current database
    assert older_facts.missing_triggers  # named, so the refusal can say which

    archive = rebuild(
        content,
        mutate=lambda _: scratch.read_bytes(),
        manifest_change=lambda document: document["application"].update({"schema_version": 4}),
    )
    staged = backup_restore.validate_package(
        write(tmp_path, "older.zip", archive),
        target_db=office["path"],
        token="older",
    )

    # Accepted, because it was migrated first and now carries every protection.
    assert staged.source_schema_version == 4
    assert staged.migrations_applied == (5, 6, 7)
    assert staged.facts.missing_triggers == frozenset()
    assert staged.facts.healthy is True


# --- Correction C: a pointer must name its own month's record --------------


def pointer_mismatch_office(office: dict, tmp_path: Path) -> Path:
    """A copy whose November pointer names December's issued version."""

    damaged = copy_of(office, tmp_path / "misplaced" / "dailyworkreg.db")
    connection = sqlite3.connect(str(damaged), isolation_level=None)
    try:
        december = connection.execute(
            "SELECT finalisation_id FROM month_finalisation WHERE month = '2026-12'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE month_state SET latest_finalisation_id = ? WHERE month = '2026-11'",
            (december,),
        )
    finally:
        connection.close()
    return damaged


def test_a_pointer_to_another_month_is_not_caught_by_referential_checks_alone(
    office, tmp_path
):
    damaged = pointer_mismatch_office(office, tmp_path)

    connection = sqlite3.connect(str(damaged))
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        named = connection.execute(
            """
            SELECT s.month AS pointer_month, f.month AS record_month,
                   s.register_id AS pointer_register, f.register_id AS record_register
            FROM month_state s
            JOIN month_finalisation f ON f.finalisation_id = s.latest_finalisation_id
            WHERE s.month = '2026-11'
            """
        ).fetchone()
    finally:
        connection.close()
    # The reference is to a real row, so no foreign key can object; the months differ.
    assert named["pointer_month"] == "2026-11"
    assert named["record_month"] == "2026-12"
    assert named["pointer_register"] == named["record_register"]


def test_the_structural_inspector_identifies_the_mismatch(office, tmp_path):
    damaged = pointer_mismatch_office(office, tmp_path)
    facts = inspect(damaged)

    assert facts.readable and facts.integrity_ok and facts.foreign_keys_ok
    assert any("different register or month" in problem for problem in facts.structure)
    assert facts.healthy is False


def test_a_pointer_to_another_register_is_identified_too(office, tmp_path):
    damaged = copy_of(office, tmp_path / "foreign" / "dailyworkreg.db")
    connection = sqlite3.connect(str(damaged), isolation_level=None)
    try:
        november = connection.execute(
            "SELECT finalisation_id FROM month_finalisation WHERE month = '2026-11' AND sequence = 1"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO month_state (register_id, month, latest_finalisation_id,
                                     open_correction_run_id)
            VALUES ('R-someone-else', '2027-03', ?, NULL)
            """,
            (november,),
        )
    finally:
        connection.close()

    facts = inspect(damaged)
    assert any("different register or month" in problem for problem in facts.structure)


def test_a_package_holding_a_misplaced_pointer_is_refused_before_the_live_database(
    office, client, tmp_path
):
    before = whole_database(office["path"])
    _, content, _ = make_package(client)
    damaged = pointer_mismatch_office(office, tmp_path)
    archive = rebuild(content, mutate=lambda _: damaged.read_bytes())
    package = write(tmp_path, "misplaced.zip", archive)

    with pytest.raises(backup_restore.BackupError) as refusal:
        backup_restore.validate_package(package, target_db=office["path"], token="misplaced")

    assert refusal.value.constraint == backup_restore.C_PACKAGE_STRUCTURE
    assert "different register or month" in refusal.value.detail
    assert whole_database(office["path"]) == before
    assert not list(
        office["path"].parent.glob(office["path"].name + ".restore-*.staged")
    )


def test_every_valid_multi_month_pointer_still_passes(office):
    facts = inspect(office["path"])
    assert facts.structure == ()
    connection = sqlite3.connect(str(office["path"]))
    try:
        pointers = connection.execute(
            """
            SELECT s.month, f.month FROM month_state s
            JOIN month_finalisation f ON f.finalisation_id = s.latest_finalisation_id
            """
        ).fetchall()
    finally:
        connection.close()
    assert len(pointers) == 2
    assert all(pointer == record for pointer, record in pointers)


# --- Correction D: a recovery copy never overwrites an earlier one ----------


def test_two_recovery_copies_with_one_stamp_both_survive(office):
    stamp = "20260101T000000Z"
    first = replace_module.rollback_copy(office["path"], stamp=stamp)
    first_bytes = first.path.read_bytes()
    second = replace_module.rollback_copy(office["path"], stamp=stamp)

    assert second.name != first.name
    assert second.path.exists() and first.path.exists()
    assert first.path.read_bytes() == first_bytes  # untouched by the second copy
    assert inspect(first.path).healthy and inspect(second.path).healthy
    assert first.name.startswith(office["path"].name + f".pre-restore-{stamp}")
    assert second.name.startswith(office["path"].name + f".pre-restore-{stamp}")


def test_two_restores_report_their_own_recovery_copy(office, client):
    names = []
    for _ in range(2):
        _, content, _ = make_package(client)
        answer = client.post(
            "/api/backup-validation",
            files={"file": ("backup.zip", content, "application/zip")},
        )
        assert answer.status_code == 200, answer.text
        restored = client.post(
            "/api/restore",
            json={
                "restore_token": answer.json()["restore_token"],
                "confirmation": "REPLACE",
            },
        )
        assert restored.status_code == 200, restored.text
        names.append(restored.json()["rollback_copy"])

    assert names[0] != names[1]
    recovery = [office["path"].parent / name for name in names]
    assert all(path.exists() and inspect(path).healthy for path in recovery)


# --- Correction E: the manifest is checked against the database -------------


def validated(office: dict, tmp_path: Path, label: str, archive: bytes):
    package = write(tmp_path, f"{label}.zip", archive)
    return backup_restore.validate_package(
        package, target_db=office["path"], token=label
    )


def refused_manifest(office: dict, tmp_path: Path, label: str, archive: bytes) -> str:
    before = whole_database(office["path"])
    with pytest.raises(backup_restore.BackupError) as refusal:
        validated(office, tmp_path, label, archive)
    assert whole_database(office["path"]) == before
    assert not list(
        office["path"].parent.glob(office["path"].name + ".restore-*.staged")
    )
    assert refusal.value.constraint in {
        backup_restore.C_PACKAGE_MANIFEST_INVALID,
        backup_restore.C_PACKAGE_MANIFEST_MISMATCH,
        backup_restore.C_PACKAGE_SCHEMA_NEWER,
    }
    return refusal.value.constraint


def manifest_of(content: bytes) -> dict:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        return json.loads(archive.read(package_module.MANIFEST_NAME))


def test_a_valid_manifest_describes_the_database_exactly(office, client, tmp_path):
    _, content, held = make_package(client)
    staged = validated(office, tmp_path, "valid", content)

    manifest = manifest_of(content)
    assert manifest["application"]["name"] == package_module.APPLICATION_NAME
    assert manifest["application"]["schema_version"] == staged.source_schema_version
    assert manifest["contents"] == staged.facts.counts
    assert staged.summary["contents"] == staged.facts.counts
    assert held["contents"] == staged.facts.counts


def test_a_manifest_naming_another_application_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document["application"].update({"name": "Elsewhere"}),
    )
    assert refused_manifest(office, tmp_path, "app", archive) == (
        backup_restore.C_PACKAGE_MANIFEST_INVALID
    )


def test_a_manifest_declaring_an_older_schema_than_the_payload_is_refused(
    office, client, tmp_path
):
    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document["application"].update({"schema_version": 4}),
    )
    assert refused_manifest(office, tmp_path, "older", archive) == (
        backup_restore.C_PACKAGE_MANIFEST_MISMATCH
    )


def test_a_manifest_declaring_a_newer_schema_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document["application"].update({"schema_version": 99}),
    )
    assert refused_manifest(office, tmp_path, "newer", archive) == (
        backup_restore.C_PACKAGE_SCHEMA_NEWER
    )


def test_a_manifest_whose_counts_are_not_counts_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    for label, value in (
        ("list", ["dtcs", 6]),
        ("string", "six"),
        ("null", None),
    ):
        archive = rebuild(
            content,
            manifest_change=lambda document, value=value: document.update({"contents": value}),
        )
        assert refused_manifest(office, tmp_path, f"contents-{label}", archive) == (
            backup_restore.C_PACKAGE_MANIFEST_INVALID
        )


def test_a_manifest_missing_a_count_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document["contents"].pop("dtcs"),
    )
    assert refused_manifest(office, tmp_path, "missing", archive) == (
        backup_restore.C_PACKAGE_MANIFEST_MISMATCH
    )


def test_a_manifest_holding_a_negative_count_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document["contents"].update({"dtcs": -1}),
    )
    assert refused_manifest(office, tmp_path, "negative", archive) == (
        backup_restore.C_PACKAGE_MANIFEST_INVALID
    )


def test_a_manifest_holding_a_boolean_count_is_refused(office, client, tmp_path):
    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document["contents"].update({"dtcs": True}),
    )
    assert refused_manifest(office, tmp_path, "boolean", archive) == (
        backup_restore.C_PACKAGE_MANIFEST_INVALID
    )


def test_a_manifest_counting_something_the_format_does_not_define_is_refused(
    office, client, tmp_path
):
    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document["contents"].update({"sausages": 3}),
    )
    assert refused_manifest(office, tmp_path, "unknown", archive) == (
        backup_restore.C_PACKAGE_MANIFEST_INVALID
    )


def test_a_rehashed_archive_whose_count_was_changed_is_refused(office, client, tmp_path):
    """The payload checksum still matches; the description is what lies."""

    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document["contents"].update(
            {"finalisations": document["contents"]["finalisations"] + 1}
        ),
    )
    staged = write(tmp_path, "rehashed.zip", archive)
    with zipfile.ZipFile(staged) as reader:
        manifest = json.loads(reader.read(package_module.MANIFEST_NAME))
        payload = reader.read(package_module.PAYLOAD_NAME)
    import hashlib

    # The manifest still describes the payload it ships, to the byte.
    assert manifest["payload"]["sha256"] == hashlib.sha256(payload).hexdigest()

    assert refused_manifest(office, tmp_path, "rehashed-again", archive) == (
        backup_restore.C_PACKAGE_MANIFEST_MISMATCH
    )


def test_a_refused_manifest_reaches_the_operator_as_a_refusal_not_a_crash(office, client):
    _, content, _ = make_package(client)
    archive = rebuild(
        content,
        manifest_change=lambda document: document.update({"contents": True}),
    )
    answer = client.post(
        "/api/backup-validation",
        files={"file": ("odd.zip", archive, "application/zip")},
    )
    assert answer.status_code == 400, answer.text
    assert answer.json()["error"]["constraint"] == backup_restore.C_PACKAGE_MANIFEST_INVALID


def test_the_summary_of_a_malformed_manifest_does_not_raise():
    summary = package_module.manifest_summary(
        {"application": "none", "payload": [], "contents": ["dtcs", 3]}
    )
    assert summary["schema_version"] is None
    assert summary["contents"] == {}


def test_a_package_missing_a_protection_is_refused_and_the_protection_is_named(
    office, client, tmp_path
):
    """A database that holds every row but can no longer keep a month final is not one to install."""

    before = whole_database(office["path"])
    _, content, _ = make_package(client)
    payload = payload_of(content)
    damaged = write(tmp_path, "unprotected.db", payload)
    removed = "trg_finalised_generation_row_delete"
    without_trigger(damaged, removed)
    archive = rebuild(content, mutate=lambda _: damaged.read_bytes())

    with pytest.raises(backup_restore.BackupError) as refusal:
        validated(office, tmp_path, "unprotected", archive)

    assert refusal.value.constraint == backup_restore.C_PACKAGE_TRIGGERS
    assert removed in refusal.value.detail
    assert whole_database(office["path"]) == before
    assert not list(office["path"].parent.glob(office["path"].name + ".restore-*.staged"))
