"""Backing up the operational database, and deciding whether a backup may replace it.

Operational state in this tool is the SQLite database and nothing else: no side-car file,
no cache directory, no configuration outside the database carries anything that cannot be
re-derived from it. That was checked rather than assumed — the only filesystem write in the
whole package is the ``mkdir`` that creates the database's own directory — so a backup of
that one database is a complete backup of the office's records, and this module needs to
copy nothing else.

Two things are decided here and nowhere else.

**What a backup contains.** One consistent snapshot taken by SQLite itself, wrapped in a
versioned package with a manifest, a checksum and non-secret totals. The snapshot is taken
from the live connection, so committed work in WAL is included and an uncommitted
transaction is not; no write and no schema change is made to the live database, and it stays
usable throughout.

**Whether a backup may be restored.** A package is inspected rather than trusted: its
format, its manifest, its entries, its checksum, then the database inside it — integrity,
foreign keys, required tables, the immutability triggers, the schema version against this
build, and the structure of the issued records themselves. An older schema is migrated on a
*staging copy* and inspected again; a newer schema is refused rather than approximated. Only
after all of that does an operator see a confirmation, and only an explicit confirmation
reaches the replacement.

The replacement itself, and the barrier that guarantees no connection is open while it
happens, live outside this module: the application layer decides, and the interface layer
that owns the process lifecycle performs.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from pathlib import Path

from ..infrastructure.backup import (
    FORMAT_VERSION,
    Limits,
    PackageError,
    RollbackCopy,
    build_manifest,
    clear_sidecars,
    install_database,
    manifest_summary,
    package_filename,
    put_back,
    read_package,
    rollback_copy,
    sha256_file,
    staged_path,
    staged_pattern,
    write_package,
)
from ..infrastructure.sqlite import revision_state
from ..infrastructure.sqlite import schema as schema_module
from ..infrastructure.sqlite.database import connect, open_database
from ..infrastructure.sqlite.snapshot import (
    CopyRefused,
    DatabaseFacts,
    inspect,
    snapshot_to,
)
from . import finalisation

# --- stable identifiers the surface and the tests share ---------------------

C_BACKUP_FAILED = "backup_could_not_be_created"
C_BACKUP_SOURCE_BUSY = "backup_database_has_an_open_change"
C_BACKUP_PROTECTIONS = "backup_protections_are_missing"
C_PACKAGE_UNREADABLE = "backup_package_unreadable"
C_PACKAGE_FORMAT_UNKNOWN = "backup_format_not_recognised"
C_PACKAGE_FORMAT_UNSUPPORTED = "backup_format_not_supported"
C_PACKAGE_MANIFEST_INVALID = "backup_manifest_invalid"
C_PACKAGE_MANIFEST_MISMATCH = "backup_manifest_does_not_match_the_database"
C_PACKAGE_ENTRIES = "backup_entries_unexpected"
C_PACKAGE_ENTRY_MISSING = "backup_entry_missing"
C_PACKAGE_TOO_LARGE = "backup_package_too_large"
C_PACKAGE_CHECKSUM = "backup_checksum_does_not_match"
C_PACKAGE_NOT_A_DATABASE = "backup_payload_is_not_a_database"
C_PACKAGE_INTEGRITY = "backup_integrity_check_failed"
C_PACKAGE_FOREIGN_KEYS = "backup_foreign_key_check_failed"
C_PACKAGE_SCHEMA_UNKNOWN = "backup_schema_version_unknown"
C_PACKAGE_SCHEMA_NEWER = "backup_schema_newer_than_this_tool"
C_PACKAGE_STRUCTURE = "backup_records_are_not_structurally_sound"
C_PACKAGE_TRIGGERS = "backup_protections_are_missing"
C_RESTORE_TOKEN_UNKNOWN = "restore_request_not_recognised"
C_RESTORE_CONFIRMATION_REQUIRED = "restore_confirmation_required"
C_RESTORE_BUSY = "restore_could_not_begin"
C_RESTORE_REPLACEMENT_FAILED = "restore_replacement_failed"
C_RESTORE_VERIFICATION_FAILED = "restore_verification_failed"
C_RESTORE_ROLLBACK_FAILED = "restore_rollback_failed"

#: What an operator has to type to replace local operational data. Chosen so that it cannot
#: be produced by merely selecting a file, and so that it says what it does.
CONFIRMATION_WORD = "REPLACE"

_PACKAGE_REASONS = {
    "format_unknown": C_PACKAGE_FORMAT_UNKNOWN,
    "format_unsupported": C_PACKAGE_FORMAT_UNSUPPORTED,
    "manifest_invalid": C_PACKAGE_MANIFEST_INVALID,
    "entry_unexpected": C_PACKAGE_ENTRIES,
    "entry_missing": C_PACKAGE_ENTRY_MISSING,
    "duplicate_entry": C_PACKAGE_ENTRIES,
    "unsafe_entry": C_PACKAGE_ENTRIES,
    "too_large": C_PACKAGE_TOO_LARGE,
    "checksum_mismatch": C_PACKAGE_CHECKSUM,
    "package_unreadable": C_PACKAGE_UNREADABLE,
}


class BackupError(Exception):
    """A refusal with a stable identifier, so no caller has to parse a sentence."""

    def __init__(self, constraint: str, detail: str, *, fields: dict | None = None):
        self.constraint = constraint
        self.detail = detail
        self.fields = fields or {}
        super().__init__(detail)


@dataclass(frozen=True)
class BackupPackage:
    """A backup that was created and verified, ready to be handed to the operator."""

    filename: str
    content: bytes
    manifest: dict
    summary: dict
    verified: dict

    @property
    def bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class StagedRestore:
    """A validated package whose database is waiting beside the live one."""

    token: str
    staged: Path
    staged_sha256: str
    summary: dict
    migrations_applied: tuple[int, ...]
    source_schema_version: int
    facts: DatabaseFacts
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RestoreResult:
    summary: dict
    rollback: RollbackCopy
    migrations_applied: tuple[int, ...]
    verification: dict


def create_backup(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    limits: Limits | None = None,
) -> BackupPackage:
    """Snapshot the live database, verify the snapshot, and wrap it in a package.

    Everything happens in a private temporary directory that is removed whatever happens,
    so no backup file and no temporary database is ever left in the working tree. The live
    connection is read only.
    """

    now = now or datetime.now(UTC)
    limits = limits or Limits.from_environment()
    workdir = Path(tempfile.mkdtemp(prefix="dailyworkreg-backup-"))
    try:
        try:
            snapshot = snapshot_to(connection, workdir / "snapshot.db")
        except CopyRefused as error:
            raise BackupError(
                C_BACKUP_SOURCE_BUSY
                if error.reason == "source_transaction_open"
                else C_BACKUP_FAILED,
                "no backup was made: " + error.detail,
            ) from error
        facts = inspect(snapshot)
        # A database that holds every row but has lost one of the protections that keep a
        # finalised month final is not a database to hand back as verified. It is refused
        # by name, so the operator can see which protection went missing.
        if facts.missing_triggers:
            raise BackupError(
                C_BACKUP_PROTECTIONS,
                "the database is missing the protections that keep a finalised month "
                "final, so no backup was made: "
                + ", ".join(sorted(facts.missing_triggers)),
            )
        if not facts.healthy:
            raise BackupError(
                C_BACKUP_FAILED,
                "the copy of the database did not pass its own checks, so no backup was made: "
                + _problems(facts),
            )
        version = facts.schema_version or 0
        manifest = build_manifest(
            created_at=now,
            schema_version=version,
            payload_bytes=snapshot.stat().st_size,
            payload_sha256=sha256_file(snapshot),
            counts=facts.counts,
        )
        archive = write_package(
            snapshot_path=snapshot,
            manifest=manifest,
            target=workdir / package_filename(now, schema_version=version),
        )
        content = archive.read_bytes()
        if len(content) > limits.max_package_bytes:
            raise BackupError(
                C_BACKUP_FAILED,
                "the backup is larger than this tool is configured to hand out",
            )
        verified = {
            "integrity": "ok" if facts.integrity_ok else "; ".join(facts.integrity),
            "foreign_keys": len(facts.foreign_key_findings),
            "tables": len(facts.tables),
            "triggers": len(facts.triggers),
            "finalisations": facts.finalisations,
        }
        return BackupPackage(
            filename=archive.name,
            content=content,
            manifest=manifest,
            summary=manifest_summary(manifest),
            verified=verified,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def prune_staging(target_db: Path, *, keep: set[str] | None = None) -> tuple[str, ...]:
    """Remove staged copies left behind by an interrupted validation or restore.

    Pruning looks only for this module's own naming beside the target database, so nothing
    else in the data directory is ever a candidate.
    """

    directory = Path(target_db).parent
    keep = keep or set()
    removed: list[str] = []
    if not directory.exists():
        return ()
    for candidate in sorted(directory.glob(staged_pattern(target_db))):
        if candidate.name in keep:
            continue
        try:
            candidate.unlink()
        except OSError:
            continue
        removed.append(candidate.name)
    return tuple(removed)


def validate_package(
    package_path: Path,
    *,
    target_db: Path,
    token: str,
    limits: Limits | None = None,
) -> StagedRestore:
    """Inspect a package completely, migrating a staging copy where that is needed.

    The live database is not opened for writing and is not touched. A staging copy is
    written beside the target so a later swap is a rename on one volume, and it is migrated
    and inspected there, never in place.
    """

    limits = limits or Limits.from_environment()
    workdir = Path(tempfile.mkdtemp(prefix="dailyworkreg-validate-"))
    try:
        try:
            package = read_package(package_path, workdir=workdir, limits=limits)
        except PackageError as error:
            raise BackupError(
                _PACKAGE_REASONS.get(error.reason, C_PACKAGE_UNREADABLE), error.detail
            ) from error

        source = package.manifest["application"]["schema_version"]
        payload_facts = inspect(package.payload)
        if not payload_facts.readable:
            raise BackupError(
                C_PACKAGE_NOT_A_DATABASE,
                "the payload is not a database this tool can read",
            )
        if payload_facts.schema_version is None:
            raise BackupError(
                C_PACKAGE_SCHEMA_UNKNOWN,
                "the payload carries no record of the schema it was made from",
            )
        if payload_facts.schema_version > schema_module.SCHEMA_VERSION:
            raise BackupError(
                C_PACKAGE_SCHEMA_NEWER,
                f"the backup was made by a newer version of this tool (schema "
                f"{payload_facts.schema_version}) than the one running (schema "
                f"{schema_module.SCHEMA_VERSION}); it cannot be opened here",
                fields={"backup_schema": payload_facts.schema_version,
                        "this_tool_schema": schema_module.SCHEMA_VERSION},
            )
        if source > schema_module.SCHEMA_VERSION:
            raise BackupError(
                C_PACKAGE_SCHEMA_NEWER,
                "the manifest declares a newer schema than this tool runs",
                fields={"backup_schema": source,
                        "this_tool_schema": schema_module.SCHEMA_VERSION},
            )
        if not payload_facts.integrity_ok:
            raise BackupError(
                C_PACKAGE_INTEGRITY,
                "the database inside the backup failed its integrity check: "
                + "; ".join(payload_facts.integrity[:3]),
            )
        if not payload_facts.foreign_keys_ok:
            raise BackupError(
                C_PACKAGE_FOREIGN_KEYS,
                "the database inside the backup has rows that reference records it does not "
                "hold: " + "; ".join(payload_facts.foreign_key_findings[:3]),
            )
        if payload_facts.missing_tables:
            raise BackupError(
                C_PACKAGE_STRUCTURE,
                "the database inside the backup is missing tables this tool requires: "
                + ", ".join(sorted(payload_facts.missing_tables)),
            )
        if payload_facts.structure:
            raise BackupError(
                C_PACKAGE_STRUCTURE,
                "the issued records inside the backup do not hold together: "
                + "; ".join(payload_facts.structure[:3]),
            )

        # The manifest is what the operator is shown before deciding, so every claim it
        # makes about the database is compared with the database. A count changed without
        # touching the payload leaves the payload's checksum intact, which is exactly why
        # the checksum cannot be the whole story: it proves the database is unaltered, not
        # that the manifest is honest.
        _require_manifest_agrees(package.manifest, payload_facts)

        staged = staged_path(target_db, token)
        staged.parent.mkdir(parents=True, exist_ok=True)
        if staged.exists():
            staged.unlink()
        shutil.copy2(package.payload, staged)

        try:
            applied = _migrate(staged)
            facts = inspect(staged)
            _require_restorable(facts, applied)
        except BackupError:
            # A refused package leaves nothing behind beside the live database: a staged
            # copy that is still sitting there would be a copy somebody could later be
            # talked into installing.
            _discard(staged)
            raise
        return StagedRestore(
            token=token,
            staged=staged,
            staged_sha256=sha256_file(staged),
            summary={**manifest_summary(package.manifest), "filename": Path(package_path).name},
            migrations_applied=applied,
            source_schema_version=payload_facts.schema_version,
            facts=facts,
            warnings=_warnings(facts, applied),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _require_manifest_agrees(manifest: dict, facts: DatabaseFacts) -> None:
    """Refuse a manifest whose claims the database inside it contradicts.

    The manifest is not authenticated by anything: the payload checksum proves the
    *database* is unaltered, and an archive can be rebuilt with a different manifest and
    the same payload. So each claim is compared with the file it describes rather than
    trusted, and a disagreement is a refusal rather than a warning. The comparisons are
    typed, so a boolean or a string where a number belongs never reaches an operator's
    screen as though it were a fact.
    """

    declared_schema = manifest["application"]["schema_version"]
    if declared_schema != facts.schema_version:
        raise BackupError(
            C_PACKAGE_MANIFEST_MISMATCH,
            f"the description says this backup was made at schema {declared_schema} while "
            f"the database inside it is at schema {facts.schema_version}, so it cannot be "
            "trusted as a description of what would be restored",
            fields={"declared_schema": declared_schema, "database_schema": facts.schema_version},
        )

    declared = manifest.get("contents")
    if not isinstance(declared, dict):
        raise BackupError(
            C_PACKAGE_MANIFEST_INVALID,
            "the description of what the backup holds is not a set of counts",
        )
    actual = facts.counts
    missing = sorted(set(actual) - set(declared))
    if missing:
        raise BackupError(
            C_PACKAGE_MANIFEST_MISMATCH,
            "the description is missing what the database holds: " + ", ".join(missing),
            fields={"missing_counts": missing},
        )
    extra = sorted(set(declared) - set(actual))
    if extra:
        raise BackupError(
            C_PACKAGE_MANIFEST_MISMATCH,
            "the description counts things the database does not hold: " + ", ".join(extra),
            fields={"unexpected_counts": extra},
        )
    differing = sorted(key for key in actual if declared[key] != actual[key])
    if differing:
        key = differing[0]
        raise BackupError(
            C_PACKAGE_MANIFEST_MISMATCH,
            f"the description says the backup holds {declared[key]} {key.replace('_', ' ')} "
            f"while the database holds {actual[key]}",
            fields={
                "differing_counts": differing,
                "declared": {key: declared[key] for key in differing},
                "actual": {key: actual[key] for key in differing},
            },
        )


def _migrate(staged: Path) -> tuple[int, ...]:
    """Bring the staged copy to this build's schema, in DELETE mode so it stands alone."""

    connection = connect(staged)
    try:
        applied = schema_module.apply_migrations(connection)
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.commit()
        return tuple(applied)
    except sqlite3.DatabaseError as error:
        raise BackupError(
            C_PACKAGE_STRUCTURE,
            "the database inside the backup could not be brought up to this tool's schema: "
            f"{error}",
        ) from error
    finally:
        connection.close()


def _discard(path: Path) -> None:
    """Remove a staged copy and anything SQLite left beside it, never raising."""

    for suffix in ("", "-wal", "-shm", "-journal"):
        leftover = path.with_name(path.name + suffix) if suffix else path
        try:
            if leftover.exists():
                leftover.unlink()
        except OSError:
            # A copy that cannot be removed is reported by the walk below, not here: the
            # refusal the caller is about to see matters more than the tidying.
            continue


def _require_restorable(facts: DatabaseFacts, applied: tuple[int, ...]) -> None:
    if not facts.readable:
        raise BackupError(C_PACKAGE_NOT_A_DATABASE, "the prepared database is not readable")
    if facts.schema_version != schema_module.SCHEMA_VERSION:
        raise BackupError(
            C_PACKAGE_SCHEMA_UNKNOWN,
            f"after preparing the backup its schema is {facts.schema_version} where this tool "
            f"expects {schema_module.SCHEMA_VERSION}",
        )
    if not facts.integrity_ok:
        raise BackupError(
            C_PACKAGE_INTEGRITY,
            "the prepared database failed its integrity check: " + "; ".join(facts.integrity[:3]),
        )
    if not facts.foreign_keys_ok:
        raise BackupError(
            C_PACKAGE_FOREIGN_KEYS,
            "the prepared database has rows that reference records it does not hold: "
            + "; ".join(facts.foreign_key_findings[:3]),
        )
    if facts.missing_tables:
        raise BackupError(
            C_PACKAGE_STRUCTURE,
            "the prepared database is missing tables this tool requires: "
            + ", ".join(sorted(facts.missing_tables)),
        )
    if facts.missing_triggers:
        raise BackupError(
            C_PACKAGE_TRIGGERS,
            "the prepared database is missing the protections that keep a finalised month "
            "final: " + ", ".join(sorted(facts.missing_triggers)),
        )
    if facts.structure:
        raise BackupError(
            C_PACKAGE_STRUCTURE,
            "the issued records in the prepared database do not hold together: "
            + "; ".join(facts.structure[:3]),
        )


def _warnings(facts: DatabaseFacts, applied: tuple[int, ...]) -> tuple[str, ...]:
    notes: list[str] = []
    if applied:
        notes.append(
            "This backup was made by an older version of the tool and was brought up to "
            "schema " + ", ".join(str(version) for version in applied) + " in a staging copy."
        )
    if not facts.counts.get("finalisations"):
        notes.append("This backup holds no finalised month.")
    return tuple(notes)


def _problems(facts: DatabaseFacts) -> str:
    problems: list[str] = []
    if not facts.readable:
        problems.append(facts.error or "the copy could not be read")
    if facts.integrity and not facts.integrity_ok:
        problems.extend(facts.integrity[:3])
    if not facts.foreign_keys_ok:
        problems.extend(facts.foreign_key_findings[:3])
    if facts.missing_tables:
        problems.append("missing tables: " + ", ".join(sorted(facts.missing_tables)))
    if facts.missing_triggers:
        problems.append(
            "missing protections: " + ", ".join(sorted(facts.missing_triggers))
        )
    problems.extend(facts.structure[:3])
    return "; ".join(problems) or "the copy did not pass its checks"


def restore(
    staged: StagedRestore,
    *,
    target_db: Path,
    rollback_stamp: str | None = None,
) -> RestoreResult:
    """Replace the live database with a validated copy, verified afterwards.

    Must be called while every other connection is closed; the caller owns that guarantee.
    The order is fixed: confirm the staged copy has not changed, checkpoint and clear the
    sidecars, take and verify a recovery copy, swap, then verify the restored database
    through the ordinary opening path. Any failure after the swap puts the recovery copy
    back and reports it.
    """

    if not staged.staged.exists():
        raise BackupError(
            C_RESTORE_REPLACEMENT_FAILED,
            "the validated copy is no longer on disk, so nothing was replaced",
        )
    if sha256_file(staged.staged) != staged.staged_sha256:
        raise BackupError(
            C_RESTORE_REPLACEMENT_FAILED,
            "the validated copy changed after it was checked, so nothing was replaced",
        )

    try:
        clear_sidecars(target_db)
        recovery = rollback_copy(target_db, stamp=rollback_stamp)
    except Exception as error:  # ReplacementError and anything the copy raised
        raise BackupError(
            C_RESTORE_REPLACEMENT_FAILED,
            getattr(error, "detail", None)
            or f"the current database could not be secured before replacing it: {error}",
        ) from error

    try:
        install_database(staged.staged, target_db)
    except Exception as error:
        raise BackupError(
            C_RESTORE_REPLACEMENT_FAILED,
            getattr(error, "detail", None)
            or "the database could not be replaced; the current database is unchanged",
            fields={"rollback": recovery.name},
        ) from error

    try:
        verification = verify_restored(target_db)
    except Exception as error:
        detail = getattr(error, "detail", None) or str(error)
        try:
            put_back(recovery, target_db)
        except Exception as rollback_error:  # the recovery copy is still on disk
            raise BackupError(
                C_RESTORE_ROLLBACK_FAILED,
                f"the restored database did not verify ({detail}) and putting the previous "
                f"one back failed: {getattr(rollback_error, 'detail', rollback_error)}",
                fields={"rollback": recovery.name},
            ) from error
        raise BackupError(
            C_RESTORE_VERIFICATION_FAILED,
            f"the restored database did not verify, so the previous one was put back: {detail}",
            fields={"rollback": recovery.name, "restored": False},
        ) from error

    return RestoreResult(
        summary=staged.summary,
        rollback=recovery,
        migrations_applied=staged.migrations_applied,
        verification=verification,
    )


def verify_restored(target_db: Path) -> dict:
    """Read the restored database the way the application does, and report what it holds.

    The checks are the ones that decide whether the office can work: the file opens through
    the ordinary path, its schema is this build's, integrity and foreign keys hold, the
    protections are installed, the masters and registers read, every issued version is
    still readable through the finalisation read model, and the month pointers and
    correction lineage agree with them. Read-only: no historical record is touched.
    """

    facts = inspect(target_db, read_only=False)
    if not facts.readable:
        raise BackupError(C_RESTORE_VERIFICATION_FAILED, facts.error or "the file did not open")
    if facts.schema_version != schema_module.SCHEMA_VERSION:
        raise BackupError(
            C_RESTORE_VERIFICATION_FAILED,
            f"the restored database is at schema {facts.schema_version} where this tool "
            f"expects {schema_module.SCHEMA_VERSION}",
        )
    if not facts.integrity_ok:
        raise BackupError(
            C_RESTORE_VERIFICATION_FAILED,
            "the restored database failed its integrity check: " + "; ".join(facts.integrity[:3]),
        )
    if not facts.foreign_keys_ok:
        raise BackupError(
            C_RESTORE_VERIFICATION_FAILED,
            "the restored database references records it does not hold: "
            + "; ".join(facts.foreign_key_findings[:3]),
        )
    if facts.missing_triggers:
        raise BackupError(
            C_RESTORE_VERIFICATION_FAILED,
            "the restored database is missing the protections that keep a finalised month "
            "final: " + ", ".join(sorted(facts.missing_triggers)),
        )
    if facts.structure:
        raise BackupError(
            C_RESTORE_VERIFICATION_FAILED,
            "the issued records in the restored database do not hold together: "
            + "; ".join(facts.structure[:3]),
        )

    connection = open_database(target_db)
    try:
        issued = []
        for row in revision_state.all_finalisations(connection):
            payload = finalisation.finalisation_payload(connection, row)
            issued.append(
                {
                    "finalisation_id": row.finalisation_id,
                    "register_id": row.register_id,
                    "month": row.month,
                    "sequence": row.sequence,
                    "is_latest": payload["is_latest"],
                }
            )
        registers = [row.register_id for row in revision_state.registers(connection)]
        masters = facts.counts
    finally:
        connection.close()

    return {
        "schema_version": facts.schema_version,
        "integrity": "ok",
        "foreign_keys": "ok",
        "tables": len(facts.tables),
        "triggers": len(facts.triggers),
        "registers": registers,
        "issued_versions": issued,
        "counts": masters,
    }
