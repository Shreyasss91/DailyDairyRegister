"""The guards that keep a closed month closed, and the repair that puts them back.

A backup and a reset are both refused on a database whose immutability triggers are
incomplete, and that refusal is right: a copy that cannot keep a finalised month final is
not a copy to hand back, and a database in that state is a database somebody can rewrite an
issued register in. What it must never be is a state the tool leaves behind, because a
refusal that names three trigger names gives the operator nothing they can do.

Migration 6 rebuilt `generation_entry` and dropped the three triggers that live on it, and
the step meant to reinstall them filtered the trigger statements for one table with a
substring that appears in none of them, so it installed nothing. Every database that passed
through schema 5 lost those three protections silently. Schema 8 repairs what schema 6 lost.

These tests pin the three facts that matter to an office:

*   a database that lost the protections is refused a backup, by name;
*   the build repairs it when it opens, once, and the backup then succeeds;
*   the repaired guards actually refuse - insertion, update and deletion of a closed
    month's rows - rather than merely existing under the expected names.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from dailyworkreg.application.backup_restore import BackupError, create_backup
from dailyworkreg.infrastructure.sqlite import schema as schema_module
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.snapshot import FINALISATION_TRIGGERS, inspect

ENTRY_TRIGGERS = tuple(
    f"trg_finalised_generation_entry_{operation}" for operation in ("insert", "update", "delete")
)

RUN = """
    INSERT INTO generation_run (
        run_id, created_at, mode, start_date, end_date, months_json, seed,
        generator_version, allocator_version, outcome, is_draft, master_version,
        request_schema_version, request_snapshot_json, readiness_json, missing_content_json,
        register_id, kind
    ) VALUES ('R-1', '2026-11-01T00:00:00Z', 'coverage_cycle', '2026-11-01', '2026-11-30',
        '["2026-11"]', 7, 'generator-1', 'allocator-1', 'allocation_complete', 0, 1, 6,
        '{}', '[]', '[]', 'REG-1', 'generation')
"""

REGISTER = """
    INSERT INTO register (register_id, mode, start_date, end_date, created_at, current_run_id)
    VALUES ('REG-1', 'coverage_cycle', '2026-11-01', '2026-11-30', '2026-11-01T00:00:00Z', 'R-1')
"""

FINALISATION = """
    INSERT INTO month_finalisation (
        finalisation_id, register_id, month, run_id, revision_number, sequence,
        finalised_at, fingerprint, content_json
    ) VALUES ('MF-1', 'REG-1', '2026-11', 'R-1', 1, 1, '2026-11-30T00:00:00Z',
        'fingerprint', '{"month": "2026-11", "rows": {}}')
"""

ENTRY = """
    INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en, provenance)
    VALUES ('R-1', 'ACT-2026-11-05-one', '2026-11-05', 'activity', 'Meter reading', 'generated')
"""


def build(path: Path) -> Path:
    """A database holding one closed November and one entry recorded on a date in it."""

    connection = open_database(path)
    try:
        # The register's current pointer references the run, so the run comes first and
        # the register's own identifier is then recorded on it.
        connection.execute(RUN)
        connection.execute(REGISTER)
        # The entry is recorded while the month is still open; closing it afterwards is what
        # the guards exist to make irreversible.
        connection.execute(ENTRY)
        connection.execute(FINALISATION)
        connection.commit()
    finally:
        connection.close()
    return path


def triggers(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
    finally:
        connection.close()


def lose_the_entry_protections(path: Path) -> None:
    """Leave the database exactly as schema 6 left it: version 7, three triggers gone."""

    connection = sqlite3.connect(path)
    try:
        for name in ENTRY_TRIGGERS:
            connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute("DELETE FROM schema_migration WHERE version >= 8")
        connection.commit()
    finally:
        connection.close()


def rows(path: Path, table: str) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]  # noqa: S608
    finally:
        connection.close()


# --- the defect, and the repair -------------------------------------------


def test_a_database_that_lost_its_entry_protections_is_repaired_when_it_opens(
    tmp_path: Path,
) -> None:
    path = build(tmp_path / "office.db")
    lose_the_entry_protections(path)
    assert inspect(path).missing_triggers == frozenset(ENTRY_TRIGGERS)
    assert inspect(path).healthy is False

    open_database(path).close()

    assert inspect(path).missing_triggers == frozenset()
    assert triggers(path) >= FINALISATION_TRIGGERS
    assert inspect(path).healthy is True


def test_the_repair_touches_no_row(tmp_path: Path) -> None:
    path = build(tmp_path / "office.db")
    before = {
        table: rows(path, table)
        for table in ("generation_entry", "month_finalisation", "generation_run", "register")
    }
    lose_the_entry_protections(path)

    open_database(path).close()

    assert {table: rows(path, table) for table in before} == before


def test_the_repair_is_recorded_and_does_not_run_again(tmp_path: Path) -> None:
    path = build(tmp_path / "office.db")
    lose_the_entry_protections(path)

    first = open_database(path)
    applied = schema_module.applied_versions(first)
    first.close()
    second = open_database(path)
    again = schema_module.apply_migrations(second)
    second.close()

    assert applied[-1] == schema_module.SCHEMA_VERSION == 8
    assert again == ()
    assert len(triggers(path)) == len(FINALISATION_TRIGGERS)


def test_the_repair_replaces_a_trigger_left_behind_by_older_text(tmp_path: Path) -> None:
    # A guard that exists with the wrong predicate is worse than a missing one: the name is
    # there, so nothing reports it, and it does not guard. The repair drops by name and
    # creates from the one definition rather than trusting what it finds.
    path = build(tmp_path / "office.db")
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TRIGGER trg_finalised_generation_entry_insert")
        connection.execute(
            """
            CREATE TRIGGER trg_finalised_generation_entry_insert
            BEFORE INSERT ON generation_entry
            WHEN 0
            BEGIN
                SELECT RAISE(ABORT, 'never fires');
            END
            """
        )
        connection.execute("DELETE FROM schema_migration WHERE version >= 8")
        connection.commit()
    finally:
        connection.close()

    open_database(path).close()

    connection = sqlite3.connect(path)
    try:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'trg_finalised_generation_entry_insert'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert "never fires" not in sql
    assert "finalised_revision_is_immutable" in sql


def test_the_repair_restores_a_guard_that_actually_refuses(tmp_path: Path) -> None:
    # The point of the guard is the refusal, not the name.
    path = build(tmp_path / "office.db")
    lose_the_entry_protections(path)
    open_database(path).close()

    connection = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="finalised_revision_is_immutable"):
            connection.execute(
                """
                INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en, provenance)
                VALUES ('R-1', 'ACT-2026-11-09-late', '2026-11-09', 'activity', 'Added later', 'manual')
                """
            )
        for statement in (
            "UPDATE generation_entry SET title_en = 'rewritten' WHERE entry_id = 'ACT-2026-11-05-one'",
            "DELETE FROM generation_entry WHERE entry_id = 'ACT-2026-11-05-one'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="finalised_revision_is_immutable"):
                connection.execute(statement)
        # A date outside the closed month is still writable: the guard is month-scoped.
        connection.execute(
            """
            INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en, provenance)
            VALUES ('R-1', 'ACT-2026-12-02-open', '2026-12-02', 'activity', 'December work', 'manual')
            """
        )
        connection.rollback()
    finally:
        connection.close()


def test_opening_the_database_is_what_turns_the_refusal_into_a_backup(tmp_path: Path) -> None:
    # This is the operator's own path: the tool opens the database, so the build repairs it
    # before a single route runs and the backup they asked for is made.
    path = build(tmp_path / "office.db")
    lose_the_entry_protections(path)

    connection = open_database(path)
    try:
        package = create_backup(connection)
    finally:
        connection.close()

    assert package.bytes > 0
    assert inspect(path).healthy is True


def test_the_refusal_is_still_reached_by_a_connection_that_did_not_repair(
    tmp_path: Path,
) -> None:
    # A connection opened by hand, as a repair taken from a copy would be, still meets the
    # refusal the operator reported. It names the missing guards rather than guessing.
    path = build(tmp_path / "office.db")
    lose_the_entry_protections(path)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        with pytest.raises(BackupError) as refused:
            create_backup(connection)
    finally:
        connection.close()

    assert refused.value.constraint == "backup_protections_are_missing"
    assert "finalised month" in refused.value.detail


# --- what migration 6 should have done in the first place ------------------


def test_migration_six_installs_the_entry_guards_after_its_rebuild(tmp_path: Path) -> None:
    # The shape schema 6 met: a `generation_entry` whose provenance check does not yet allow
    # a generated activity. The rebuild drops the table's triggers, and the step after it
    # must put them back - the filter that matched nothing is the defect this pins.
    path = build(tmp_path / "office.db")
    connection = sqlite3.connect(path)
    # The migration reads its own answers by column name, as every connection the tool
    # opens does.
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("DROP TABLE generation_entry")
        connection.execute(
            schema_module._GENERATION_ENTRY_TABLE_SQL.replace(", 'generated'", "").replace(
                "generation_entry", "generation_entry", 1
            )
        )
        connection.execute(
            """
            INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en, provenance)
            VALUES ('R-1', 'cal-1', '2026-11-05', 'holiday', 'A holiday', 'calendar')
            """
        )
        connection.execute("DELETE FROM schema_migration WHERE version >= 6")
        connection.commit()

        schema_module._apply_migration_6(connection)

        present = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
    finally:
        connection.close()

    assert set(ENTRY_TRIGGERS) <= present


def test_migration_six_keeps_every_entry_across_its_rebuild(tmp_path: Path) -> None:
    path = build(tmp_path / "office.db")
    connection = sqlite3.connect(path)
    # The migration reads its own answers by column name, as every connection the tool
    # opens does.
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("DROP TABLE generation_entry")
        connection.execute(schema_module._GENERATION_ENTRY_TABLE_SQL.replace(", 'generated'", ""))
        for index in range(1, 6):
            connection.execute(
                """
                INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en, provenance)
                VALUES ('R-1', ?, '2026-11-05', 'calendar', 'A record', 'calendar')
                """,
                (f"e-{index}",),
            )
        connection.commit()

        schema_module._apply_migration_6(connection)

        kept = connection.execute("SELECT COUNT(*) FROM generation_entry").fetchone()[0]
        allows = (
            "generated"
            in (
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'generation_entry'"
                ).fetchone()[0]
            )
        )
    finally:
        connection.close()

    assert kept == 5
    assert allows
