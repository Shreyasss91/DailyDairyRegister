"""A consistent copy of a live database, and the checks that decide whether it is usable.

Two different questions live here, and keeping them apart is the point of the module.

*   **Taking a copy.** ``sqlite3.Connection.backup`` is SQLite's own online backup
    mechanism. It reads the database through the connection, so a committed transaction is
    included whatever journal mode is in force — including WAL, where the newest committed
    pages are still in the sidecar and an ordinary file copy would miss them, or copy a
    half-written pair. An uncommitted transaction is not visible to the copy, because the
    copy sees committed state only. No write is issued against the live database and no
    schema change is made, so taking a copy leaves the running application exactly as it
    was.
*   **Judging a copy.** ``inspect`` answers, in structured terms, whether a file is a
    usable daily-work register: is it SQLite at all, does ``PRAGMA integrity_check`` pass,
    does ``PRAGMA foreign_key_check`` pass, which schema version has it reached, does it
    carry the tables an operational database must carry, and are its finalisations,
    month pointers and correction lineage structurally sound. It never repairs anything
    and never raises for a database that is merely wrong; it reports, and the caller
    decides.

A copy is opened in ``DELETE`` journal mode so that no ``-wal`` or ``-shm`` sidecar
travels with it: a backup that depends on two extra files is a backup that will be restored
from one of them.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

#: The tables an operational register must have before it is worth restoring. Named here
#: rather than derived from the schema module so that a future migration which renames or
#: drops one of them fails loudly at the restore boundary instead of silently.
REQUIRED_TABLES = frozenset(
    {
        "activity",
        "app_setting",
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
        "month_finalisation",
        "month_state",
        "register",
        "revision_decision",
        "schema_migration",
    }
)

#: The trigger names the finalisation immutability guarantee is made of. A restored
#: database without them has lost the protection the whole design rests on.
FINALISATION_TRIGGERS = frozenset(
    {
        "trg_finalisation_update",
        "trg_finalisation_delete",
        *{
            f"trg_finalised_{table}_{operation}"
            for table in ("generation_row", "generation_visit", "generation_entry")
            for operation in ("insert", "update", "delete")
        },
        *{
            f"trg_finalised_{table}_{operation}"
            for table in ("date_review", "date_lock")
            for operation in ("insert", "update", "delete")
        },
    }
)

_HEX = re.compile(r"^[0-9a-f]{64}$")


def snapshot_to(connection: sqlite3.Connection, target: Path) -> Path:
    """Write one consistent copy of ``connection``'s database to ``target``.

    The copy is taken by SQLite, not by the filesystem, so committed data in WAL is
    included and a concurrent write cannot produce a torn file. The copy is then switched
    to ``DELETE`` journal mode so it stands alone, and the destination is left closed and
    flushed.
    """

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm", "-journal"):
        stale = target.with_name(target.name + suffix)
        if stale.exists():
            stale.unlink()
    if target.exists():
        target.unlink()

    destination = sqlite3.connect(str(target))
    try:
        connection.commit() if connection.in_transaction else None
        connection.backup(destination)
        destination.execute("PRAGMA journal_mode = DELETE")
        destination.commit()
    finally:
        destination.close()
    return target


@dataclass(frozen=True)
class DatabaseFacts:
    """What a database file turned out to be, in terms a caller can refuse on."""

    readable: bool = False
    integrity: tuple[str, ...] = ()
    foreign_key_findings: tuple[str, ...] = ()
    schema_version: int | None = None
    tables: frozenset[str] = frozenset()
    triggers: frozenset[str] = frozenset()
    finalisations: int = 0
    months_finalised: int = 0
    structure: tuple[str, ...] = ()
    error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def integrity_ok(self) -> bool:
        return bool(self.integrity) and all(item == "ok" for item in self.integrity)

    @property
    def foreign_keys_ok(self) -> bool:
        return not self.foreign_key_findings

    @property
    def missing_tables(self) -> frozenset[str]:
        return REQUIRED_TABLES - self.tables

    @property
    def missing_triggers(self) -> frozenset[str]:
        return FINALISATION_TRIGGERS - self.triggers

    @property
    def healthy(self) -> bool:
        """Whether this file is a complete, self-consistent operational register."""

        return (
            self.readable
            and self.integrity_ok
            and self.foreign_keys_ok
            and not self.missing_tables
            and not self.structure
        )


def _rows(connection: sqlite3.Connection, sql: str) -> list[tuple]:
    try:
        return [tuple(row) for row in connection.execute(sql)]
    except sqlite3.DatabaseError:
        return []


def _scalar(connection: sqlite3.Connection, sql: str):
    rows = _rows(connection, sql)
    return rows[0][0] if rows and rows[0] else None


def _schema_version(connection: sqlite3.Connection) -> int | None:
    """The highest migration the file records, or 0 when it has never been migrated."""

    if "schema_migration" not in _table_names(connection):
        return None
    value = _scalar(connection, "SELECT max(version) FROM schema_migration")
    return int(value) if value is not None else 0


def _table_names(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row[0]
        for row in _rows(
            connection, "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    )


def _trigger_names(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row[0]
        for row in _rows(
            connection, "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    )


def _structure(connection: sqlite3.Connection, tables: frozenset[str]) -> tuple[str, ...]:
    """Whether issued versions, month pointers and lineage point at rows that exist."""

    problems: list[str] = []
    if not {"month_finalisation", "month_state", "generation_run"} <= tables:
        return ()

    orphan_runs = _scalar(
        connection,
        """
        SELECT count(*) FROM month_finalisation f
        WHERE NOT EXISTS (SELECT 1 FROM generation_run r WHERE r.run_id = f.run_id)
        """,
    )
    if orphan_runs:
        problems.append(f"{orphan_runs} finalised record(s) name a revision that is absent")

    orphan_parents = _scalar(
        connection,
        """
        SELECT count(*) FROM month_finalisation f
        WHERE f.parent_finalisation_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM month_finalisation p
              WHERE p.finalisation_id = f.parent_finalisation_id
          )
        """,
    )
    if orphan_parents:
        problems.append(f"{orphan_parents} finalised record(s) name a parent that is absent")

    orphan_corrections = _scalar(
        connection,
        """
        SELECT count(*) FROM month_finalisation f
        WHERE f.correction_of_run_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM generation_run r WHERE r.run_id = f.correction_of_run_id
          )
        """,
    )
    if orphan_corrections:
        problems.append(f"{orphan_corrections} finalised record(s) name a corrected revision that is absent")

    orphan_pointers = _scalar(
        connection,
        """
        SELECT count(*) FROM month_state s
        WHERE s.latest_finalisation_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM month_finalisation f
              WHERE f.finalisation_id = s.latest_finalisation_id
          )
        """,
    )
    if orphan_pointers:
        problems.append(f"{orphan_pointers} month pointer(s) name a record that is absent")

    orphan_correction_branches = _scalar(
        connection,
        """
        SELECT count(*) FROM month_state s
        WHERE s.open_correction_run_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM generation_run r WHERE r.run_id = s.open_correction_run_id
          )
        """,
    )
    if orphan_correction_branches:
        problems.append(
            f"{orphan_correction_branches} open correction(s) name a revision that is absent"
        )

    unreadable_snapshots = 0
    for (content,) in _rows(
        connection,
        "SELECT content_json FROM month_finalisation",
    ):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            unreadable_snapshots += 1
            continue
        if not isinstance(parsed, dict) or not isinstance(parsed.get("rows"), dict):
            unreadable_snapshots += 1
    if unreadable_snapshots:
        problems.append(
            f"{unreadable_snapshots} issued snapshot(s) are not a readable month of rows"
        )

    blank_fingerprints = _scalar(
        connection,
        """
        SELECT count(*) FROM month_finalisation
        WHERE fingerprint IS NULL OR length(trim(fingerprint)) = 0
        """,
    )
    if blank_fingerprints:
        problems.append(f"{blank_fingerprints} finalised record(s) carry no fingerprint")

    return tuple(problems)


def _gather_counts(connection: sqlite3.Connection, tables: frozenset[str]) -> dict[str, int]:
    """Non-secret totals, for identifying a backup without reading its contents."""

    wanted = {
        "registers": "register",
        "revisions": "draft_revision",
        "finalisations": "month_finalisation",
        "finalised_months": "month_state",
        "rows": "generation_row",
        "visits": "generation_visit",
        "entries": "generation_entry",
        "reviews": "date_review",
        "locks": "date_lock",
        "dtcs": "dtc",
        "areas": "area",
        "feeders": "feeder",
        "activities": "activity",
        "holidays": "general_holiday",
        "import_batches": "import_batch",
        "staged_rows": "import_staging_row",
    }
    counts: dict[str, int] = {}
    for label, table in wanted.items():
        if table in tables:
            value = _scalar(connection, f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed names
            counts[label] = int(value or 0)
    return counts


def _open_quietly(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        uri = f"{Path(path).resolve().as_uri()}?mode=ro"
        return sqlite3.connect(uri, uri=True)
    return sqlite3.connect(str(path))


def inspect(path: Path, *, read_only: bool = True, check_structure: bool = True) -> DatabaseFacts:
    """Everything a caller needs to accept or refuse a database file, and nothing else.

    A file that is not a database is reported as unreadable rather than raised, because
    "the operator picked the wrong file" is an ordinary answer at this boundary.
    """

    path = Path(path)
    try:
        connection = _open_quietly(path, read_only=read_only)
    except sqlite3.Error as error:
        return DatabaseFacts(error=str(error))

    try:
        try:
            connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.DatabaseError as error:
            return DatabaseFacts(error=str(error))

        integrity = tuple(
            str(row[0]) for row in _rows(connection, "PRAGMA integrity_check")
        )
        foreign_keys = tuple(
            "; ".join(str(value) for value in row)
            for row in _rows(connection, "PRAGMA foreign_key_check")
        )
        tables = _table_names(connection)
        triggers = _trigger_names(connection)
        version = _schema_version(connection)
        counts = _gather_counts(connection, tables)
        structure = (
            _structure(connection, tables)
            if check_structure and "month_finalisation" in tables
            else ()
        )
        return DatabaseFacts(
            readable=True,
            integrity=integrity,
            foreign_key_findings=foreign_keys,
            schema_version=version,
            tables=tables,
            triggers=triggers,
            finalisations=counts.get("finalisations", 0),
            months_finalised=counts.get("finalised_months", 0),
            structure=structure,
            counts=counts,
        )
    finally:
        connection.close()


def digest(value: str) -> bool:
    """Whether a string looks like a SHA-256 digest the manifest could have carried."""

    return bool(_HEX.match(value or ""))
