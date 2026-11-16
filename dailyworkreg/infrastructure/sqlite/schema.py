"""The SQLite schema and its migration mechanism.

SQLite is the canonical store for operational master data. Configuration files
hold application settings only, so nothing here reads a YAML or JSON file to learn
what an area is called.

Two rules shape the design:

* Imported source values and canonical values live in different places. A source
  record is preserved as supplied, including blanks and ``#N/A``; the canonical
  record holds what the office actually uses. An override therefore never
  overwrites the evidence for it.
* A generated draft is never rewritten. Each generation is a new row, and the
  snapshot it carries is enough to rebuild its result without consulting live
  master records, which may have changed since.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass

#: Bumped when the schema below changes. Migrations are applied in order and each
#: one is idempotent, so re-running is safe and a fresh database simply replays the
#: list.
SCHEMA_VERSION = 4


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]
    #: Optional Python step run inside the same transaction, after the statements.
    #: Needed where a change moves data rather than only shaping it, which plain
    #: DDL cannot express. Must be idempotent, like the statements beside it.
    apply: Callable[[sqlite3.Connection], None] | None = None


# The master-version counter is a single row so that every canonical change can
# bump exactly one value inside the same transaction. A generation run records the
# version it read, which is what makes "this draft was built from that master
# state" answerable later.
_MIGRATION_1 = Migration(
    version=1,
    name="canonical masters, import staging and generation history",
    statements=(
        """
        CREATE TABLE IF NOT EXISTS master_version (
            id       INTEGER PRIMARY KEY CHECK (id = 1),
            version  INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT OR IGNORE INTO master_version (id, version, updated_at)
        VALUES (1, 0, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        """,
        """
        CREATE TABLE IF NOT EXISTS app_setting (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        # --- areas ---
        """
        CREATE TABLE IF NOT EXISTS area (
            area_id            TEXT PRIMARY KEY,
            name_kn            TEXT NOT NULL,
            name_en            TEXT,
            weight             REAL NOT NULL DEFAULT 1.0,
            monthly_min        INTEGER NOT NULL DEFAULT 0,
            monthly_max        INTEGER NOT NULL DEFAULT 3,
            revisit_gap_days   INTEGER NOT NULL DEFAULT 7,
            active             INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            revision           INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at         TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS area_alias (
            alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_id  TEXT NOT NULL REFERENCES area (area_id) ON DELETE CASCADE,
            name_kn  TEXT NOT NULL,
            name_en  TEXT,
            UNIQUE (area_id, name_kn)
        )
        """,
        # --- feeders ---
        """
        CREATE TABLE IF NOT EXISTS feeder (
            feeder_id        TEXT PRIMARY KEY,
            name             TEXT NOT NULL UNIQUE,
            manual_multiplier REAL NOT NULL DEFAULT 1.0,
            monthly_min      INTEGER NOT NULL DEFAULT 0,
            monthly_max      INTEGER NOT NULL DEFAULT 3,
            revisit_gap_days INTEGER NOT NULL DEFAULT 7,
            active           INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            revision         INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        # --- DTCs ---
        # dtc_class is the scheduling class (DEPT or SELF_DTC). source_class keeps
        # the supplied DEPT/MSB/OWN value, because MSB and OWN both become
        # SELF_DTC for scheduling and collapsing them would destroy the record.
        """
        CREATE TABLE IF NOT EXISTS dtc (
            dtc_id             TEXT PRIMARY KEY,
            name               TEXT NOT NULL,
            canonical_feeder_id TEXT REFERENCES feeder (feeder_id) ON DELETE SET NULL,
            kva                REAL,
            station            TEXT,
            dtc_class          TEXT NOT NULL CHECK (dtc_class IN ('DEPT', 'SELF_DTC')),
            source_class       TEXT,
            individual_adjustment REAL NOT NULL DEFAULT 1.0,
            monthly_min        INTEGER NOT NULL DEFAULT 0,
            monthly_max        INTEGER NOT NULL DEFAULT 3,
            revisit_gap_days   INTEGER NOT NULL DEFAULT 7,
            active             INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            revision           INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at         TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dtc_source_record (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            dtc_id         TEXT,
            batch_id       TEXT,
            row_number     INTEGER NOT NULL,
            raw_name       TEXT,
            raw_feeder     TEXT,
            raw_feeder_code TEXT,
            raw_mdm_code   TEXT,
            raw_kva        TEXT,
            raw_station    TEXT,
            raw_class      TEXT,
            UNIQUE (batch_id, row_number)
        )
        """,
        # --- activities ---
        """
        CREATE TABLE IF NOT EXISTS activity (
            activity_id      TEXT PRIMARY KEY,
            category         TEXT NOT NULL,
            description_en   TEXT NOT NULL,
            description_kn   TEXT,
            weight           REAL NOT NULL DEFAULT 1.0,
            monthly_min      INTEGER NOT NULL DEFAULT 0,
            monthly_max      INTEGER NOT NULL DEFAULT 10,
            repeat_gap_days  INTEGER NOT NULL DEFAULT 2,
            weekday_mask     TEXT,
            active           INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            revision         INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        # --- holidays: General only ---
        # There is no restricted_holiday table, by requirement §4.1: restricted
        # holidays are ordinary working days and their dates and names are ignored
        # entirely, so storing one would create a reason to display it.
        """
        CREATE TABLE IF NOT EXISTS general_holiday (
            holiday_id       TEXT PRIMARY KEY,
            holiday_date     TEXT NOT NULL,
            name             TEXT NOT NULL,
            description      TEXT,
            holiday_year     INTEGER NOT NULL,
            source           TEXT NOT NULL,
            revision         INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at       TEXT NOT NULL,
            UNIQUE (holiday_date, name)
        )
        """,
        # --- leave ---
        """
        CREATE TABLE IF NOT EXISTS leave_type (
            leave_type_id    TEXT PRIMARY KEY,
            name             TEXT NOT NULL UNIQUE,
            active           INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            revision         INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS leave_record (
            leave_id         TEXT PRIMARY KEY,
            leave_date       TEXT NOT NULL,
            leave_type_id    TEXT NOT NULL REFERENCES leave_type (leave_type_id),
            note             TEXT,
            revision         INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        # --- dated events ---
        """
        CREATE TABLE IF NOT EXISTS dated_event (
            event_id         TEXT PRIMARY KEY,
            event_date       TEXT NOT NULL,
            title_en         TEXT NOT NULL,
            title_kn         TEXT,
            event_kind       TEXT NOT NULL DEFAULT 'event',
            note             TEXT,
            revision         INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        # --- explicit date overrides ---
        # An override records the effective classification but never deletes the
        # underlying holiday, weekly off or leave record. Both are kept and the
        # conflict is surfaced, per requirements §4.1.
        """
        CREATE TABLE IF NOT EXISTS date_override (
            override_id      TEXT PRIMARY KEY,
            override_date    TEXT NOT NULL UNIQUE,
            is_working_day   INTEGER NOT NULL CHECK (is_working_day IN (0, 1)),
            reason_en        TEXT,
            reason_kn        TEXT,
            note             TEXT,
            revision         INTEGER NOT NULL DEFAULT 1,
            valid_from_version INTEGER NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        # --- staged imports ---
        """
        CREATE TABLE IF NOT EXISTS import_batch (
            batch_id        TEXT PRIMARY KEY,
            dataset         TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            source_sha256   TEXT NOT NULL,
            staged_at       TEXT NOT NULL,
            status          TEXT NOT NULL
                CHECK (status IN ('staged', 'accepted', 'rejected', 'superseded')),
            row_count       INTEGER NOT NULL DEFAULT 0,
            error_count     INTEGER NOT NULL DEFAULT 0,
            warning_count   INTEGER NOT NULL DEFAULT 0,
            accepted_at     TEXT,
            decision_note   TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS import_staging_row (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id      TEXT NOT NULL REFERENCES import_batch (batch_id) ON DELETE CASCADE,
            row_number    INTEGER NOT NULL,
            raw_json      TEXT NOT NULL,
            normalized_json TEXT,
            severity      TEXT NOT NULL CHECK (severity IN ('ok', 'warning', 'error')),
            messages_json TEXT NOT NULL DEFAULT '[]',
            decision      TEXT,
            UNIQUE (batch_id, row_number)
        )
        """,
        # --- generation history ---
        # A run is one generation attempt. is_draft is 1 only for an
        # allocation-complete outcome, so a failed or unresolved attempt can never
        # be mistaken for a reviewable draft.
        """
        CREATE TABLE IF NOT EXISTS generation_run (
            run_id             TEXT PRIMARY KEY,
            created_at         TEXT NOT NULL,
            mode               TEXT NOT NULL
                CHECK (mode IN ('coverage_cycle', 'capacity_fallback')),
            start_date         TEXT NOT NULL,
            end_date           TEXT NOT NULL,
            months_json        TEXT NOT NULL,
            seed               INTEGER NOT NULL,
            generator_version  TEXT NOT NULL,
            allocator_version  TEXT NOT NULL,
            outcome            TEXT NOT NULL
                CHECK (outcome IN ('allocation_complete', 'infeasible', 'unresolved')),
            is_draft           INTEGER NOT NULL CHECK (is_draft IN (0, 1)),
            master_version     INTEGER NOT NULL,
            request_schema_version INTEGER NOT NULL,
            request_snapshot_json  TEXT NOT NULL,
            readiness_json     TEXT NOT NULL,
            missing_content_json TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS draft_revision (
            run_id          TEXT PRIMARY KEY REFERENCES generation_run (run_id) ON DELETE CASCADE,
            revision_number INTEGER NOT NULL UNIQUE,
            created_at      TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS generation_month (
            run_id            TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
            ordinal           INTEGER NOT NULL,
            month_key         TEXT NOT NULL,
            status            TEXT NOT NULL,
            allocator_status  TEXT NOT NULL,
            obligations_json  TEXT NOT NULL,
            declared_obligations_json TEXT NOT NULL,
            evidence_json     TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (run_id, month_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS generation_row (
            run_id        TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
            ordinal       INTEGER NOT NULL,
            row_date      TEXT NOT NULL,
            weekday       INTEGER NOT NULL,
            kind          TEXT NOT NULL,
            is_working_day INTEGER NOT NULL,
            reasons_en_json TEXT NOT NULL DEFAULT '[]',
            reasons_kn_json TEXT NOT NULL DEFAULT '[]',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (run_id, row_date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS generation_visit (
            run_id     TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
            ordinal    INTEGER NOT NULL,
            visit_date TEXT NOT NULL,
            asset_id   TEXT NOT NULL,
            provenance TEXT NOT NULL CHECK (provenance IN ('fixed', 'allocated')),
            month_key  TEXT NOT NULL,
            name_en    TEXT NOT NULL,
            name_kn    TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS generation_exception (
            run_id       TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
            ordinal      INTEGER NOT NULL,
            kind         TEXT NOT NULL,
            constraint_name TEXT NOT NULL,
            detail       TEXT NOT NULL,
            month_key    TEXT,
            asset_id     TEXT,
            asset_type   TEXT,
            visit_date   TEXT,
            offending_dates_json TEXT NOT NULL DEFAULT '[]',
            limit_value  INTEGER,
            actual_value INTEGER
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_area_alias_area ON area_alias (area_id)",
        "CREATE INDEX IF NOT EXISTS idx_dtc_feeder ON dtc (canonical_feeder_id)",
        "CREATE INDEX IF NOT EXISTS idx_holiday_date ON general_holiday (holiday_date)",
        "CREATE INDEX IF NOT EXISTS idx_staging_batch ON import_staging_row (batch_id)",
        "CREATE INDEX IF NOT EXISTS idx_generation_run_created ON generation_run (created_at)",
        "CREATE INDEX IF NOT EXISTS idx_generation_visit_run ON generation_visit (run_id)",
        "CREATE INDEX IF NOT EXISTS idx_generation_exception_run ON generation_exception (run_id)",
    ),
)

# A generated draft can carry a finding caused jointly by several assets, such as
# an area or combined capacity proof. Which assets those are is a fact the
# validator established, so it is stored rather than left in the detail text. Rows
# written before this column existed read as no attribution, which is the truth
# about them.
_MIGRATION_2 = Migration(
    version=2,
    name="attribute aggregate generation exceptions to their contributing assets",
    statements=(
        """
        ALTER TABLE generation_exception
        ADD COLUMN contributing_assets_json TEXT NOT NULL DEFAULT '[]'
        """,
    ),
)


# The review lifecycle. A generated register is inspected date by date, corrected,
# partially locked and regenerated; none of that may rewrite what was generated. A
# logical register therefore owns a chain of revisions, and each revision owns its
# own review and lock state, because a review is a statement about one exact row of
# one exact revision. Dates the operator locked are recorded here, not inferred from
# the absence of a change.
_MIGRATION_3_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS register (
        register_id   TEXT PRIMARY KEY,
        mode          TEXT NOT NULL,
        start_date    TEXT NOT NULL,
        end_date      TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        current_run_id TEXT REFERENCES generation_run (run_id) ON DELETE SET NULL
    )
    """,
    "ALTER TABLE generation_run ADD COLUMN register_id TEXT",
    "ALTER TABLE generation_run ADD COLUMN parent_run_id TEXT",
    "ALTER TABLE generation_run ADD COLUMN kind TEXT NOT NULL DEFAULT 'generation'",
    "ALTER TABLE generation_run ADD COLUMN change_reason TEXT",
    # One row per date per revision. A date with no visits still needs a row here:
    # reviewing an empty Sunday is exactly as much part of the job as reviewing a
    # busy Monday, and a register that only tracks busy dates cannot be reviewed.
    """
    CREATE TABLE IF NOT EXISTS date_review (
        run_id            TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
        row_date          TEXT NOT NULL,
        state             TEXT NOT NULL CHECK (state IN ('pending_review', 'reviewed')),
        reviewed_at       TEXT,
        note              TEXT,
        content_fingerprint TEXT NOT NULL,
        carried_from_run_id TEXT,
        PRIMARY KEY (run_id, row_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS date_lock (
        run_id    TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
        row_date  TEXT NOT NULL,
        locked_at TEXT NOT NULL,
        note      TEXT,
        PRIMARY KEY (run_id, row_date)
    )
    """,
    # Entries carry their own identity so a correction targets a record, not a
    # sentence. English and Kannada are separate columns because one is not a
    # rendering of the other.
    """
    CREATE TABLE IF NOT EXISTS generation_entry (
        run_id     TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
        entry_id   TEXT NOT NULL,
        row_date   TEXT NOT NULL,
        kind       TEXT NOT NULL,
        title_en   TEXT NOT NULL,
        title_kn   TEXT,
        provenance TEXT NOT NULL
            CHECK (provenance IN ('calendar', 'manual', 'locked')),
        PRIMARY KEY (run_id, entry_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS revision_decision (
        run_id              TEXT PRIMARY KEY REFERENCES generation_run (run_id) ON DELETE CASCADE,
        decision            TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
        decided_at          TEXT NOT NULL,
        compared_to_run_id  TEXT,
        note                TEXT
    )
    """,
    # Every manual mutation is an audit row. The stored rows are the outcome; this
    # is the account of how they came to be that way.
    """
    CREATE TABLE IF NOT EXISTS revision_change (
        run_id     TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
        ordinal    INTEGER NOT NULL,
        action     TEXT NOT NULL,
        row_date   TEXT,
        entry_id   TEXT,
        asset_id   TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, ordinal)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_run_register ON generation_run (register_id)",
    "CREATE INDEX IF NOT EXISTS idx_entry_run_date ON generation_entry (run_id, row_date)",
)


def _backfill_registers(connection: sqlite3.Connection) -> None:
    """Give every pre-existing draft a register, a lineage and scoped numbering.

    Runs once, inside migration 3. An installation upgrading from schema 2 keeps
    every stored generation and every attribution: each draft is attached to a
    register, and drafts that shared a mode and date range are chained as revisions
    of the same register in revision order, which is how they were produced.
    """

    rows = connection.execute(
        """
        SELECT r.run_id, r.revision_number, r.created_at, g.mode, g.start_date,
               g.end_date
        FROM draft_revision r JOIN generation_run g ON g.run_id = r.run_id
        ORDER BY r.revision_number
        """
    ).fetchall()

    groups: dict[tuple[str, str, str], str] = {}
    for row in rows:
        key = (row["mode"], row["start_date"], row["end_date"])
        register_id = groups.get(key)
        if register_id is None:
            register_id = "R-" + uuid.uuid4().hex[:12]
            groups[key] = register_id
            connection.execute(
                """
                INSERT INTO register (register_id, mode, start_date, end_date,
                    created_at, current_run_id)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (register_id, row["mode"], row["start_date"], row["end_date"], row["created_at"]),
            )
        connection.execute(
            """
            UPDATE generation_run
            SET register_id = ?, kind = 'generation'
            WHERE run_id = ?
            """,
            (register_id, row["run_id"]),
        )
        connection.execute(
            "UPDATE register SET current_run_id = ? WHERE register_id = ?",
            (row["run_id"], register_id),
        )

    # Draft revisions are numbered within their register. SQLite cannot relax the
    # original global UNIQUE constraint in place, so the table is rebuilt.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS draft_revision_scoped (
            run_id          TEXT PRIMARY KEY
                REFERENCES generation_run (run_id) ON DELETE CASCADE,
            register_id     TEXT,
            revision_number INTEGER NOT NULL,
            created_at      TEXT NOT NULL,
            UNIQUE (register_id, revision_number)
        )
        """
    )
    numbered: dict[str, int] = {}
    for row in rows:
        register_id = connection.execute(
            "SELECT register_id FROM generation_run WHERE run_id = ?",
            (row["run_id"],),
        ).fetchone()[0]
        numbered[register_id] = numbered.get(register_id, 0) + 1
        connection.execute(
            """
            INSERT OR IGNORE INTO draft_revision_scoped
                (run_id, register_id, revision_number, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (row["run_id"], register_id, numbered[register_id], row["created_at"]),
        )
    connection.execute("DROP TABLE draft_revision")
    connection.execute("ALTER TABLE draft_revision_scoped RENAME TO draft_revision")


#: Tables whose rows are the content of a revision. A finalised revision is never
#: mutated again, and these triggers are what makes that true below the application as
#: well as inside it: no route, no script and no future refactor can rewrite a record the
#: office approved.
_FINALISED_CONTENT_TABLES = (
    "generation_row",
    "generation_visit",
    "generation_entry",
    "date_review",
    "date_lock",
)


def _immutability_triggers() -> tuple[str, ...]:
    # Scoped to the row's own month: a revision may have closed November and still be
    # worked on in December, and freezing the whole revision would make a neighbouring
    # month's correction impossible.
    statements: list[str] = []
    for table in _FINALISED_CONTENT_TABLES:
        column = "visit_date" if table == "generation_visit" else "row_date"
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_finalised_{table}_{operation.lower()}
                BEFORE {operation} ON {table}
                WHEN EXISTS (
                    SELECT 1 FROM month_finalisation f
                    WHERE f.run_id = OLD.run_id
                      AND substr(OLD.{column}, 1, 7) = f.month
                )
                BEGIN
                    SELECT RAISE(ABORT, 'finalised_revision_is_immutable');
                END
                """
            )
    # A finalised record is itself the approved document, so it is not editable either.
    for operation in ("UPDATE", "DELETE"):
        statements.append(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_finalisation_{operation.lower()}
            BEFORE {operation} ON month_finalisation
            BEGIN
                SELECT RAISE(ABORT, 'finalised_record_is_immutable');
            END
            """
        )
    return tuple(statements)


_MIGRATION_4_STATEMENTS = (
    # Which finalised record a revision corrects. A revision is finalised at most once
    # (UNIQUE run_id below), so this single column identifies the record being corrected
    # without a second pointer that could disagree with it.
    "ALTER TABLE generation_run ADD COLUMN correction_of_run_id TEXT",
    # One row per finalised month of a register, forever. The content snapshot is stored
    # rather than recomputed, because the record that was approved has to remain readable
    # even if the code that renders it changes.
    """
    CREATE TABLE IF NOT EXISTS month_finalisation (
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
        -- One revision can close several months of its range, so the key is the pair.
        -- Uniqueness on the revision alone would make closing November prevent December
        -- from being closed from the same revision, which is the opposite of monthly
        -- finalisation.
        UNIQUE (run_id, month),
        UNIQUE (register_id, month, sequence)
    )
    """,
    # The month's pointer: which finalised record is authoritative, and whether a
    # correction branch is open for it. Per month, so finalising November does not make
    # December final and correcting November cannot touch December.
    """
    CREATE TABLE IF NOT EXISTS month_state (
        register_id          TEXT NOT NULL,
        month                TEXT NOT NULL,
        latest_finalisation_id TEXT,
        open_correction_run_id TEXT,
        PRIMARY KEY (register_id, month)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_finalisation_register_month "
    "ON month_finalisation (register_id, month, sequence)",
    "CREATE INDEX IF NOT EXISTS idx_finalisation_run ON month_finalisation (run_id)",
) + _immutability_triggers()


_MIGRATION_3 = Migration(
    version=3,
    name="register revisions, date review, date locks and revision lineage",
    statements=_MIGRATION_3_STATEMENTS,
    apply=_backfill_registers,
)


_MIGRATION_4 = Migration(
    version=4,
    name="monthly finalisation records and finalised-revision immutability",
    statements=_MIGRATION_4_STATEMENTS,
)


MIGRATIONS: tuple[Migration, ...] = (
    _MIGRATION_1,
    _MIGRATION_2,
    _MIGRATION_3,
    _MIGRATION_4,
)


def ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def applied_versions(connection: sqlite3.Connection) -> tuple[int, ...]:
    ensure_migration_table(connection)
    rows = connection.execute(
        "SELECT version FROM schema_migration ORDER BY version"
    ).fetchall()
    return tuple(row["version"] for row in rows)


def apply_migrations(connection: sqlite3.Connection) -> tuple[int, ...]:
    """Bring the database to :data:`SCHEMA_VERSION`, returning what was applied.

    Every statement uses ``IF NOT EXISTS`` or an idempotent insert, so a database
    that already carries a migration is left alone rather than failing. The whole
    replay runs in one transaction: a half-applied schema is worse than none.
    """

    ensure_migration_table(connection)
    already = set(applied_versions(connection))
    applied: list[int] = []
    pending = [m for m in MIGRATIONS if m.version not in already]
    if not pending:
        return ()

    connection.execute("BEGIN IMMEDIATE")
    try:
        for migration in pending:
            for statement in migration.statements:
                connection.execute(statement)
            if migration.apply is not None:
                migration.apply(connection)
            connection.execute(
                """
                INSERT INTO schema_migration (version, name, applied_at)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (migration.version, migration.name),
            )
            applied.append(migration.version)
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return tuple(applied)
