"""Emptying the database so a second office can begin in the same installation.

This tool holds one office at a time. Handing the installation to another office therefore
means removing every record the first one entered, and it has to happen in one act: a reset
that stops halfway leaves a database holding one office's transformers beside another's
areas, and no screen would say so.

The approach is the one the restore path already proves rather than a new one. A brand-new
database is created from this build's own schema at a staging path beside the live file, the
few state-wide facts the operator asked to keep are copied into it, and the live file is then
replaced through :func:`dailyworkreg.application.backup_restore.restore` - which checks the
staged file's digest, takes and verifies a recovery copy of the old database, swaps by rename
on one volume, and verifies the result. A reset is not a different kind of danger from a
restore, so it does not get a different kind of mechanism.

Two decisions are worth stating because they are not visible from the code:

*   **A backup is taken first, always.** The operator cannot reset without a copy of what was
    removed existing on disk, and the same package is held for download. The recovery copy the
    swap makes is a second, independent one.
*   **The startup seed is told that the masters were cleared on purpose.** The approved areas,
    feeders and leave types are this office's, confirmed for it and seeded on a fresh install.
    Without a record of the operator's decision, the next restart would quietly put the
    previous office's sixteen areas and four feeders back into the new one.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..infrastructure.backup import sha256_file
from ..infrastructure.backup.replace import clear_sidecars
from ..infrastructure.sqlite import masters
from ..infrastructure.sqlite.database import open_database, resolve_db_path
from ..infrastructure.sqlite.seed import SEED_SETTING_KEY
from ..infrastructure.sqlite.snapshot import inspect
from .backup_restore import BackupPackage, StagedRestore, create_backup, restore

#: What the operator types to confirm. Deliberately not a button's label: the act removes
#: every record the office has entered.
CONFIRMATION_WORD = "RESET"

C_RESET_REFUSED = "reset_refused"

#: The two scopes, named once so the route, the page and the tests cannot disagree.
SCOPE_EVERYTHING = "everything"
SCOPE_KEEP_CALENDAR = "keep_calendar"
SCOPES = (SCOPE_EVERYTHING, SCOPE_KEEP_CALENDAR)

#: The facts that are the state's rather than the office's, and the only rows a reset can
#: carry across. The Karnataka General Holiday list is published for the whole state, the
#: leave types and the weekly-off pattern are the same in every section.
KEPT_CALENDAR_TABLES = ("general_holiday", "leave_type")
KEPT_CALENDAR_SETTINGS = (
    "calendar.sundays_off",
    "calendar.second_saturday_off",
    "calendar.fourth_saturday_off",
)

#: Written where the seed records that it has run, so a restart does not re-insert the
#: previous office's approved identities into this one.
CLEARED_SEED_VALUE = (
    "cleared by the operator: this database was emptied so that another office can begin"
)

#: What a reader wants to see counted before deciding, and what the result reports as
#: removed. Table names are constants, never operator input.
TRACKED_TABLES: tuple[tuple[str, str], ...] = (
    ("dtc", "transformers (DTCs)"),
    ("area", "areas"),
    ("feeder", "feeders"),
    ("activity", "approved activities"),
    ("general_holiday", "general holidays"),
    ("leave_type", "leave types"),
    ("leave_record", "leave records"),
    ("dated_event", "dated events"),
    ("date_override", "date overrides"),
    ("import_batch", "import batches"),
    ("register", "registers"),
    ("generation_run", "generated drafts"),
    ("month_finalisation", "issued months"),
)


@dataclass(frozen=True)
class ResetPreview:
    """What the live database holds, so the operator chooses with the numbers in front."""

    counts: dict[str, int]

    @property
    def empty(self) -> bool:
        return not any(self.counts.values())

    def as_payload(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "labels": dict(TRACKED_TABLES),
            "empty": self.empty,
            "confirmation_word": CONFIRMATION_WORD,
            "scopes": {
                SCOPE_EVERYTHING: (
                    "Everything goes: the masters, the calendar, the staged imports, every "
                    "draft, every review and lock, every closed month and every version "
                    "issued from it. The tool behaves as it did on its first start."
                ),
                SCOPE_KEEP_CALENDAR: (
                    "The masters, the staged imports, every draft, every review and lock, "
                    "every closed month and every version issued from it all go. The "
                    "Karnataka General Holiday list, the leave types and the weekly-off "
                    "rules stay, because they are the same for any office in the state."
                ),
            },
        }


@dataclass(frozen=True)
class ResetResult:
    """What was removed, what was kept, and where the two copies of the old data are."""

    scope: str
    cleared: dict[str, int]
    kept: dict[str, int]
    backup: BackupPackage
    recovery_name: str
    schema_version: int
    verification: dict
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_payload(self, *, download: str | None = None) -> dict[str, Any]:
        return {
            "reset": True,
            "scope": self.scope,
            "cleared": dict(self.cleared),
            "kept": dict(self.kept),
            "schema_version": self.schema_version,
            "recovery_copy": self.recovery_name,
            "completed_at": self.completed_at.isoformat(),
            "backup": {
                "filename": self.backup.filename,
                "size_bytes": self.backup.bytes,
                "created_at": self.backup.summary.get("created_at"),
                "download": download,
            },
            "verified": dict(self.verification),
        }


def preview(connection: sqlite3.Connection) -> ResetPreview:
    """Count what a reset would remove. Reads only, and never opens a second database."""

    counts: dict[str, int] = {}
    for table, _ in TRACKED_TABLES:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608 - fixed names
        counts[table] = int(row[0])
    return ResetPreview(counts=counts)


def _table_rows(connection: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    """Every row of one table with its columns, so it can be written into a fresh file."""

    cursor = connection.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed names
    columns = [description[0] for description in cursor.description]
    return columns, [tuple(row) for row in cursor.fetchall()]


def _copy_rows(
    connection: sqlite3.Connection, table: str, columns: list[str], rows: list[tuple]
) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" for _ in columns)
    names = ", ".join(columns)
    connection.executemany(
        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",  # noqa: S608 - fixed names
        rows,
    )


def reset(
    target_db: Path,
    *,
    scope: str = SCOPE_EVERYTHING,
    token: str | None = None,
    now: datetime | None = None,
) -> ResetResult:
    """Replace the live database with an empty one, after copying what is to be kept.

    Must be called while every other connection is closed; the caller owns that guarantee
    (the maintenance barrier). The order is fixed: read what is to be kept and take a backup
    of the whole database, build the new file beside the live one, then hand it to the
    restore path, which secures a recovery copy, swaps, and verifies.
    """

    if scope not in SCOPES:
        raise ValueError(f"unknown reset scope: {scope}")
    keep_calendar = scope == SCOPE_KEEP_CALENDAR
    token = token or uuid.uuid4().hex[:12]
    target_db = resolve_db_path(target_db)
    staged = target_db.with_name(f"{target_db.name}.reset-{token}.staged")
    if staged.exists():
        staged.unlink()

    live = open_database(target_db)
    try:
        counts = preview(live).counts
        kept: dict[str, int] = {}
        tables: dict[str, tuple[list[str], list[tuple]]] = {}
        settings: dict[str, str] = {}
        if keep_calendar:
            for table in KEPT_CALENDAR_TABLES:
                columns, rows = _table_rows(live, table)
                tables[table] = (columns, rows)
                kept[table] = len(rows)
            for key in KEPT_CALENDAR_SETTINGS:
                value = masters.get_setting(live, key)
                if value is not None:
                    settings[key] = value
            kept["settings"] = len(settings)
        package = create_backup(live, now=now)
    finally:
        live.close()

    try:
        fresh = open_database(staged)
        try:
            for table, (columns, rows) in tables.items():
                _copy_rows(fresh, table, columns, rows)
            for key, value in settings.items():
                masters.set_setting(fresh, key, value)
            # A fresh install would seed this office's approved identities on the next
            # start; the operator has just emptied them on purpose, so the record of the
            # seed says so and the restart leaves the new office's masters alone.
            masters.set_setting(fresh, SEED_SETTING_KEY, CLEARED_SEED_VALUE)
            fresh.commit()
        finally:
            fresh.close()

        facts = inspect(staged)
        result = restore(
            StagedRestore(
                token=token,
                staged=staged,
                staged_sha256=sha256_file(staged),
                summary={
                    "format": "emptied-database",
                    "created_at": (now or datetime.now(UTC)).isoformat(),
                    "schema_version": facts.schema_version,
                },
                migrations_applied=(),
                source_schema_version=facts.schema_version or 0,
                facts=facts,
            ),
            target_db=target_db,
        )
    finally:
        # The staged file is a database, so it has a journal and a shared-memory file beside
        # it. Leaving those behind would leave three files the operator never asked for
        # next to their live database on every reset.
        clear_sidecars(staged)
        if staged.exists():
            staged.unlink()

    return ResetResult(
        scope=scope,
        cleared=counts,
        kept=kept,
        backup=package,
        recovery_name=result.rollback.name,
        schema_version=facts.schema_version or 0,
        verification=result.verification,
    )
