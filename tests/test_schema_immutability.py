"""Persistence-level immutability, and the migration that repairs an earlier schema 4.

Two things are pinned here that the application tests cannot see, because the application
refuses these acts long before they reach SQLite:

*   **Insertion is a change.** UPDATE and DELETE triggers stop a closed month being
    edited. Appending a row to it is also a change, and before migration 5 the database
    allowed it.
*   **A migration is not re-run because its source text changed.** Migration 4 reached
    databases in two shapes while it was being written: keyed by ``run_id`` with
    revision-wide triggers, and keyed by ``(run_id, month)`` with month-scoped ones. The
    version number says migration 4 ran; it does not say which text ran. These tests build
    both earlier shapes and prove migration 5 corrects them in place without losing a row.

The earlier schema is written out here rather than copied from a database file, so the test
states the shape it is testing instead of depending on a binary artifact.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application import finalisation as finalise
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application import revisions
from dailyworkreg.application.draft_generation import DraftMode, DraftStatus, generate_draft
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters, revision_state
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.schema import (
    MIGRATIONS,
    SCHEMA_VERSION,
    _apply_migration_5,
    _finalisation_trigger_names,
    _month_finalisation_is_keyed_by_run_only,
    applied_versions,
    apply_migrations,
    ensure_migration_table,
)
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities

CONTENT_TABLES = (
    "generation_row",
    "generation_visit",
    "generation_entry",
    "date_review",
    "date_lock",
)


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """A database that can close a month: every master the gate measures is present."""

    connection = open_database(tmp_path / "immutability.db")
    seed_approved_identities(connection)
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
    cal.upsert_holiday(
        connection,
        holiday_id="H-2026-12-25",
        holiday_date=date(2026, 12, 25),
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
    yield connection
    connection.close()


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


# --- the two shapes migration 4 reached a database in -----------------------

LEGACY_MONTH_FINALISATION = """
CREATE TABLE month_finalisation (
    finalisation_id        TEXT PRIMARY KEY,
    register_id            TEXT NOT NULL,
    month                  TEXT NOT NULL,
    run_id                 TEXT NOT NULL REFERENCES generation_run (run_id),
    revision_number        INTEGER NOT NULL,
    sequence               INTEGER NOT NULL,
    parent_finalisation_id TEXT,
    correction_of_run_id   TEXT,
    finalised_at           TEXT NOT NULL,
    fingerprint            TEXT NOT NULL,
    content_json           TEXT NOT NULL,
    acknowledged_json      TEXT NOT NULL DEFAULT '[]',
    readiness_json         TEXT NOT NULL DEFAULT '[]',
    master_version         INTEGER NOT NULL DEFAULT 0,
    note                   TEXT,
    UNIQUE (run_id),
    UNIQUE (register_id, month, sequence)
)
"""

LEGACY_MONTH_STATE = """
CREATE TABLE month_state (
    register_id            TEXT NOT NULL,
    month                  TEXT NOT NULL,
    latest_finalisation_id TEXT,
    open_correction_run_id TEXT,
    PRIMARY KEY (register_id, month)
)
"""

LEGACY_RUN = """
INSERT INTO generation_run (run_id, created_at, mode, start_date, end_date, months_json,
    seed, generator_version, allocator_version, outcome, is_draft, master_version,
    request_schema_version, request_snapshot_json, readiness_json, missing_content_json,
    register_id, parent_run_id, kind, change_reason, correction_of_run_id)
VALUES (?, '2026-11-16T09:00:00', 'coverage_cycle', '2026-11-01', '2026-12-31',
    '["2026-11", "2026-12"]', 7, 'g1', 'a1', 'allocation_complete', 1, 12, 1, '{}', '[]',
    '[]', 'R-legacy', ?, ?, ?, ?)
"""


def legacy_trigger(table: str, operation: str, *, revision_wide: bool) -> str:
    """An earlier trigger: no insertion, and possibly no month in the predicate."""

    column = "visit_date" if table == "generation_visit" else "row_date"
    predicate = (
        "WHERE f.run_id = OLD.run_id"
        if revision_wide
        else f"WHERE f.run_id = OLD.run_id AND substr(OLD.{column}, 1, 7) = f.month"
    )
    return f"""
        CREATE TRIGGER trg_finalised_{table}_{operation.lower()}
        BEFORE {operation} ON {table}
        WHEN EXISTS (SELECT 1 FROM month_finalisation f {predicate})
        BEGIN
            SELECT RAISE(ABORT, 'finalised_revision_is_immutable');
        END
        """


def seed_legacy_data(connection: sqlite3.Connection) -> None:
    """One register, a closed November and an open December, on the same revision."""

    connection.execute(LEGACY_RUN, ("run-nov", None, "generation", None, None))
    connection.execute(
        LEGACY_RUN,
        ("run-correction", "run-nov", "correction", "the 4th was wrong", "run-nov"),
    )
    connection.execute(
        "INSERT INTO generation_row (run_id, ordinal, row_date, weekday, kind,"
        " is_working_day) VALUES ('run-nov', 1, '2026-11-04', 2, 'working_day', 1)"
    )
    connection.execute(
        "INSERT INTO generation_row (run_id, ordinal, row_date, weekday, kind,"
        " is_working_day) VALUES ('run-nov', 2, '2026-12-04', 4, 'working_day', 1)"
    )
    connection.execute(
        "INSERT INTO generation_visit (run_id, ordinal, visit_date, asset_id, provenance,"
        " month_key, name_en) VALUES ('run-nov', 1, '2026-11-04', 'SCD-1', 'allocated',"
        " '2026-11', 'Section office')"
    )
    connection.execute(
        "INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en,"
        " provenance) VALUES ('run-nov', 'E-1', '2026-11-04', 'activity', 'Inspection',"
        " 'calendar')"
    )
    connection.execute(
        "INSERT INTO date_review (run_id, row_date, state, reviewed_at,"
        " content_fingerprint) VALUES ('run-nov', '2026-11-04', 'reviewed',"
        " '2026-11-16T09:30:00', 'abfingerprint')"
    )
    connection.execute(
        "INSERT INTO date_lock (run_id, row_date, locked_at)"
        " VALUES ('run-nov', '2026-11-04', '2026-11-16T09:31:00')"
    )
    snapshot = '{"month": "2026-11", "rows": {"2026-11-04": {}}, "status": "allocation_complete"}'
    acknowledged = '[{"token": "over_covered|cycle|SCD-1|2026-11-04", "detail": "one too many"}]'
    readiness = '[{"key": "roster.dtc", "label": "DTC master", "satisfied": true}]'
    connection.execute(
        """
        INSERT INTO month_finalisation (finalisation_id, register_id, month, run_id,
            revision_number, sequence, parent_finalisation_id, correction_of_run_id,
            finalised_at, fingerprint, content_json, acknowledged_json, readiness_json,
            master_version, note)
        VALUES ('MF-legacy', 'R-legacy', '2026-11', 'run-nov', 1, 1, NULL, NULL,
            '2026-11-16T11:24:41.871631', 'abfingerprint', ?, ?, ?, 12,
            'November closed')
        """,
        (snapshot, acknowledged, readiness),
    )
    connection.execute(
        "INSERT INTO month_state (register_id, month, latest_finalisation_id,"
        " open_correction_run_id) VALUES ('R-legacy', '2026-11', 'MF-legacy', NULL)"
    )
    connection.commit()


def build_intermediate_schema4(path: Path, *, revision_wide: bool) -> sqlite3.Connection:
    """A database stamped at schema 4 in one of the two shapes Act 10 left behind."""

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    ensure_migration_table(connection)
    for migration in MIGRATIONS:
        if migration.version > 3:
            continue
        for statement in migration.statements:
            connection.execute(statement)
        if migration.apply is not None:
            migration.apply(connection)
        connection.execute(
            "INSERT INTO schema_migration (version, name, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.name, "2026-11-16T05:54:40.427Z"),
        )
    # The earlier migration 4 also added this column, before creating its record table.
    connection.execute("ALTER TABLE generation_run ADD COLUMN correction_of_run_id TEXT")
    connection.execute(LEGACY_MONTH_FINALISATION)
    connection.execute(LEGACY_MONTH_STATE)
    connection.execute(
        "CREATE INDEX idx_finalisation_register_month"
        " ON month_finalisation (register_id, month, sequence)"
    )
    connection.execute("CREATE INDEX idx_finalisation_run ON month_finalisation (run_id)")
    for table in CONTENT_TABLES:
        for operation in ("UPDATE", "DELETE"):
            connection.execute(legacy_trigger(table, operation, revision_wide=revision_wide))
    for operation in ("UPDATE", "DELETE"):
        connection.execute(
            f"""
            CREATE TRIGGER trg_finalisation_{operation.lower()}
            BEFORE {operation} ON month_finalisation
            BEGIN
                SELECT RAISE(ABORT, 'finalised_record_is_immutable');
            END
            """
        )
    connection.execute(
        "INSERT INTO schema_migration (version, name, applied_at) VALUES"
        " (4, 'monthly finalisation records and finalised-revision immutability',"
        " '2026-11-16T05:54:40.433Z')"
    )
    seed_legacy_data(connection)
    return connection


@pytest.fixture()
def legacy(request, tmp_path: Path):
    connection = build_intermediate_schema4(
        tmp_path / "legacy4.db", revision_wide=getattr(request, "param", False)
    )
    yield connection
    connection.close()


# --- rows for the two directions -------------------------------------------


def closed_month_rows(run_id: str = "run-nov") -> dict[str, str]:
    """A valid new row in each protected table, dated inside the closed November."""

    return {
        "generation_row": (
            "INSERT INTO generation_row (run_id, ordinal, row_date, weekday, kind,"
            f" is_working_day) VALUES ('{run_id}', 9, '2026-11-05', 3, 'working_day', 1)"
        ),
        "generation_visit": (
            "INSERT INTO generation_visit (run_id, ordinal, visit_date, asset_id,"
            f" provenance, month_key, name_en) VALUES ('{run_id}', 9, '2026-11-05',"
            " 'SCD-2', 'allocated', '2026-11', 'Added after closing')"
        ),
        "generation_entry": (
            "INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en,"
            f" provenance) VALUES ('{run_id}', 'E-9', '2026-11-05', 'activity', 'Late',"
            " 'manual')"
        ),
        "date_review": (
            "INSERT INTO date_review (run_id, row_date, state, content_fingerprint)"
            f" VALUES ('{run_id}', '2026-11-05', 'reviewed', 'deadbeef')"
        ),
        "date_lock": (
            "INSERT INTO date_lock (run_id, row_date, locked_at)"
            f" VALUES ('{run_id}', '2026-11-05', '2026-11-17T09:00:00')"
        ),
    }


def open_month_rows(run_id: str = "run-nov") -> dict[str, str]:
    """The same five inserts, dated in a month of that revision that is not closed."""

    return {
        "generation_row": (
            "INSERT INTO generation_row (run_id, ordinal, row_date, weekday, kind,"
            f" is_working_day) VALUES ('{run_id}', 10, '2027-01-05', 5, 'working_day', 1)"
        ),
        "generation_visit": (
            "INSERT INTO generation_visit (run_id, ordinal, visit_date, asset_id,"
            f" provenance, month_key, name_en) VALUES ('{run_id}', 10, '2027-01-05',"
            " 'SCD-3', 'allocated', '2027-01', 'December visit')"
        ),
        "generation_entry": (
            "INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en,"
            f" provenance) VALUES ('{run_id}', 'E-10', '2027-01-05', 'activity',"
            " 'December', 'manual')"
        ),
        "date_review": (
            "INSERT INTO date_review (run_id, row_date, state, content_fingerprint)"
            f" VALUES ('{run_id}', '2027-01-05', 'reviewed', 'cafebabe')"
        ),
        "date_lock": (
            "INSERT INTO date_lock (run_id, row_date, locked_at)"
            f" VALUES ('{run_id}', '2027-01-05', '2026-11-17T09:00:00')"
        ),
    }


# --- insertion is a change -------------------------------------------------


def test_the_schema_is_at_seven_and_covers_insertion(db):
    assert SCHEMA_VERSION == 7
    assert applied_versions(db) == tuple(item.version for item in MIGRATIONS)
    triggers = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    # Five tables, three operations each, plus two on the record itself.
    assert len([n for n in triggers if n.startswith("trg_finalised_")]) == 15
    assert len([n for n in triggers if n.startswith("trg_finalisation_")]) == 2


@pytest.mark.parametrize("table", CONTENT_TABLES)
def test_a_direct_insert_into_a_finalised_month_is_refused(db, table):
    run_id = stored(db)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    with pytest.raises(sqlite3.IntegrityError) as error:
        db.execute(closed_month_rows(run_id)[table])

    assert "finalised_revision_is_immutable" in str(error.value)


@pytest.mark.parametrize("table", CONTENT_TABLES)
def test_an_insert_into_an_open_month_of_the_same_revision_is_allowed(db, table):
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    db.execute(open_month_rows(run_id)[table])  # not a closed month: ordinary work

    table_count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    assert table_count >= 1


def test_an_insert_into_a_correction_revision_is_allowed(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")
    correction = finalise.create_correction_revision(db, run_id, reason="wrong entry")

    db.execute(
        "INSERT INTO generation_entry (run_id, entry_id, row_date, kind, title_en,"
        " provenance) VALUES (?, 'E-new', '2026-11-05', 'activity', 'Corrected', 'manual')",
        (correction["correction_run_id"],),
    )

    assert any(
        item.entry_id == "E-new"
        for item in revision_state.entries(db, correction["correction_run_id"])
    )


@pytest.mark.parametrize("table", CONTENT_TABLES)
def test_update_and_delete_are_still_refused_in_the_finalised_month(db, table):
    run_id = stored(db)
    # A locked date leaves a row in date_lock, so the DELETE below has one to refuse.
    revisions.lock_date(db, run_id, date(2026, 11, 4), note="checked")
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")
    column = "visit_date" if table == "generation_visit" else "row_date"

    with pytest.raises(sqlite3.IntegrityError) as error:
        db.execute(f"UPDATE {table} SET {column} = {column} WHERE run_id = ?", (run_id,))
    assert "finalised_revision_is_immutable" in str(error.value)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))


def test_the_record_refuses_change_but_accepts_the_insert_that_closes_a_month(db):
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    review_every_date(db, run_id)
    review_every_date(db, run_id, month="2026-12")
    november = finalise.finalise_month(db, run_id, "2026-11")
    december = finalise.finalise_month(db, run_id, "2026-12")

    assert (november["sequence"], december["sequence"]) == (1, 1)
    with pytest.raises(sqlite3.IntegrityError) as error:
        db.execute(
            "UPDATE month_finalisation SET note = 'changed' WHERE finalisation_id = ?",
            (november["finalisation_id"],),
        )
    assert "finalised_record_is_immutable" in str(error.value)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "DELETE FROM month_finalisation WHERE finalisation_id = ?",
            (november["finalisation_id"],),
        )


# --- the migration that repairs an earlier schema 4 ------------------------


@pytest.mark.parametrize("legacy", [False, True], indirect=True)
def test_an_intermediate_schema_four_database_upgrades(legacy):
    assert applied_versions(legacy)[-1] == 4
    assert _month_finalisation_is_keyed_by_run_only(legacy) is True

    applied = apply_migrations(legacy)

    assert 5 in applied
    assert SCHEMA_VERSION == max(migration.version for migration in MIGRATIONS)
    assert applied_versions(legacy) == tuple(m.version for m in MIGRATIONS)
    assert _month_finalisation_is_keyed_by_run_only(legacy) is False
    unique_sets = tuple(
        tuple(row[2] for row in legacy.execute(f"PRAGMA index_info('{index[1]}')"))
        for index in legacy.execute("PRAGMA index_list('month_finalisation')")
        if index[2]
    )
    assert ("run_id", "month") in unique_sets
    assert ("run_id",) not in unique_sets


@pytest.mark.parametrize("legacy", [False, True], indirect=True)
def test_every_finalisation_row_survives_the_upgrade_exactly(legacy):
    before = tuple(
        dict(row)
        for row in legacy.execute("SELECT * FROM month_finalisation ORDER BY sequence")
    )
    before_state = tuple(dict(row) for row in legacy.execute("SELECT * FROM month_state"))

    apply_migrations(legacy)

    after = tuple(
        dict(row)
        for row in legacy.execute("SELECT * FROM month_finalisation ORDER BY sequence")
    )
    assert after == before
    assert (
        tuple(dict(row) for row in legacy.execute("SELECT * FROM month_state"))
        == before_state
    )
    # The bytes of the snapshot and the acknowledgement, not merely equal JSON.
    assert after[0]["content_json"].startswith('{"month": "2026-11"')
    assert "over_covered|cycle|SCD-1|2026-11-04" in after[0]["acknowledged_json"]
    assert after[0]["readiness_json"].startswith('[{"key": "roster.dtc"')
    assert after[0]["fingerprint"] == "abfingerprint"


@pytest.mark.parametrize("legacy", [False, True], indirect=True)
def test_the_pointer_and_the_next_sequence_are_correct_after_the_upgrade(legacy):
    apply_migrations(legacy)

    state = revision_state.month_state(legacy, "R-legacy", "2026-11")
    assert state.latest_finalisation_id == "MF-legacy"
    assert state.open_correction_run_id is None
    assert revision_state.next_finalisation_sequence(legacy, "R-legacy", "2026-11") == 2
    assert revision_state.finalisation(legacy, "MF-legacy").sequence == 1


@pytest.mark.parametrize("legacy", [False, True], indirect=True)
def test_a_migrated_database_can_close_a_second_month_from_one_revision(legacy):
    apply_migrations(legacy)

    # The defect: UNIQUE (run_id) refused the second month of the same revision.
    legacy.execute(
        """
        INSERT INTO month_finalisation (finalisation_id, register_id, month, run_id,
            revision_number, sequence, finalised_at, fingerprint, content_json)
        VALUES ('MF-december', 'R-legacy', '2026-12', 'run-nov', 1, 1,
            '2026-11-17T10:00:00', 'decfingerprint', '{"month": "2026-12"}')
        """
    )

    assert revision_state.next_finalisation_sequence(legacy, "R-legacy", "2026-12") == 2
    assert [
        item.month for item in revision_state.finalisations_of_run(legacy, "run-nov")
    ] == ["2026-11", "2026-12"]


@pytest.mark.parametrize("legacy", [False, True], indirect=True)
def test_every_finalised_content_table_refuses_an_insert_after_the_upgrade(legacy):
    apply_migrations(legacy)

    for statement in closed_month_rows().values():
        with pytest.raises(sqlite3.IntegrityError):
            legacy.execute(statement)


@pytest.mark.parametrize("legacy", [False, True], indirect=True)
def test_the_corrected_triggers_are_month_scoped_after_the_upgrade(legacy):
    apply_migrations(legacy)

    triggers = {
        row["name"]
        for row in legacy.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    for name in _finalisation_trigger_names():
        if name == "trg_finalisation_insert":
            assert name not in triggers  # closing a month is an insert
            continue
        assert name in triggers
    sql = legacy.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'trg_finalised_generation_row_update'"
    ).fetchone()[0]
    assert "substr" in sql  # month-scoped, never revision-wide
    legacy.execute(open_month_rows()["generation_row"])  # December still writable


@pytest.mark.parametrize("legacy", [False, True], indirect=True)
def test_foreign_keys_are_intact_after_the_table_rebuild(legacy):
    apply_migrations(legacy)

    assert legacy.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        legacy.execute(
            """
            INSERT INTO month_finalisation (finalisation_id, register_id, month, run_id,
                revision_number, sequence, finalised_at, fingerprint, content_json)
            VALUES ('MF-orphan', 'R-legacy', '2027-01', 'no-such-run', 1, 1,
                '2026-11-17T10:00:00', 'x', '{}')
            """
        )
    assert legacy.execute("SELECT COUNT(*) FROM generation_run").fetchone()[0] == 2


@pytest.mark.parametrize("legacy", [False, True], indirect=True)
def test_the_migration_is_idempotent(legacy):
    apply_migrations(legacy)
    def snapshot() -> tuple:
        return (
            tuple(dict(row) for row in legacy.execute("SELECT * FROM month_finalisation")),
            tuple(
                dict(row)
                for row in legacy.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('trigger', 'index')"
                    " ORDER BY name"
                )
            ),
        )
    after_once = snapshot()

    assert apply_migrations(legacy) == ()

    legacy.execute("BEGIN IMMEDIATE")
    try:
        _apply_migration_5(legacy)
        legacy.execute("COMMIT")
    except BaseException:
        legacy.execute("ROLLBACK")
        raise

    assert snapshot() == after_once


def test_a_schema_five_database_gets_nothing_new_from_the_repair(db):
    assert _month_finalisation_is_keyed_by_run_only(db) is False

    db.execute("BEGIN IMMEDIATE")
    try:
        _apply_migration_5(db)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise

    assert _month_finalisation_is_keyed_by_run_only(db) is False
    assert applied_versions(db) == tuple(m.version for m in MIGRATIONS)
