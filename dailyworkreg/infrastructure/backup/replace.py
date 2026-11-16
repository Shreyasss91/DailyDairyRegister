"""Putting a validated database in place of the live one, and putting it back if it fails.

This is the only module in the project that replaces a file the application runs from, so
it is deliberately small and deliberately paranoid.

The mechanics, and why each is what it is:

*   **Sidecars first.** A database in WAL mode owns two extra files. Replacing the database
    while a ``-wal`` from the old one survives would let a stale journal be replayed over
    the new file, which is silent corruption rather than an error. The journal is
    checkpointed and the sidecars removed before anything is replaced, and only ever while
    the caller has established that no connection is open.
*   **Same directory.** The staged database is written beside the target, not in the
    temporary directory, so ``os.replace`` is a rename on one volume and therefore atomic.
    A cross-volume rename is a copy, and a copy interrupted halfway leaves neither the old
    database nor the new one.
*   **A rollback copy before the swap.** The live database is copied through SQLite's own
    backup mechanism into a named recovery file and verified before the swap, so the
    operation always has somewhere to go back to. The recovery file is kept afterwards: it
    is the operator's to delete, and this project has no retention policy of its own.
*   **Verification after the swap, and an automatic return on failure.** A restored file
    that opens but cannot answer for its own registers is not a successful restore. If the
    post-swap checks fail, the recovery file is put back and the failure is reported with
    the recovery name, never as success.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path

from ..sqlite.snapshot import CopyRefused, inspect, snapshot_to


class ReplacementError(Exception):
    """The file could not be replaced, or it had to be put back."""

    def __init__(self, reason: str, detail: str, *, recovery: str | None = None):
        self.reason = reason
        self.detail = detail
        self.recovery = recovery
        super().__init__(detail)


@dataclass(frozen=True)
class RollbackCopy:
    """The verified copy of the database as it stood before the restore."""

    path: Path
    name: str
    bytes: int
    sha256: str


def staged_path(target: Path, token: str) -> Path:
    """Where a validated database waits, beside the target so the swap is a rename."""

    return Path(target).with_name(f"{Path(target).name}.restore-{token}.staged")


def staged_pattern(target: Path) -> str:
    """The name shape of a staged file, for pruning what an interrupted run left behind."""

    return f"{Path(target).name}.restore-*.staged"


def clear_sidecars(target: Path) -> None:
    """Checkpoint the journal and remove the sidecars, with no connection left open.

    Called only by the caller that has established that nothing else holds the database.
    A missing sidecar is not an error; a sidecar that will not delete is.
    """

    target = Path(target)
    if target.exists():
        connection = sqlite3.connect(str(target))
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
        except sqlite3.DatabaseError:
            # A database in DELETE mode has no journal to checkpoint; that is not a problem.
            pass
        finally:
            connection.close()
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = target.with_name(target.name + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError as error:
                raise ReplacementError(
                    "database_in_use",
                    f"the database is still open: {sidecar.name} could not be removed "
                    f"({error.strerror or error})",
                ) from error


def rollback_copy(target: Path, *, stamp: str | None = None) -> RollbackCopy:
    """Copy the live database through SQLite and verify the copy before returning it.

    The destination is chosen before a byte is written, and it is chosen so that an
    earlier recovery copy is never overwritten: the timestamp carries microseconds, and a
    name already taken is pushed to the next free suffix rather than reused. Two restores
    that somehow share a stamp therefore leave two recovery files, each independently
    readable, and the copy that belongs to this restore is the one reported back.
    """

    target = Path(target)
    stamp = stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = _free_recovery_path(target, stamp)

    connection = sqlite3.connect(str(target))
    try:
        snapshot_to(connection, destination)
    except CopyRefused as error:
        raise ReplacementError(
            "rollback_copy_failed",
            "the current database could not be copied, so nothing was replaced: "
            f"{error.detail}",
        ) from error
    except sqlite3.DatabaseError as error:
        raise ReplacementError(
            "rollback_copy_failed",
            "the current database could not be copied, so nothing was replaced",
        ) from error
    finally:
        connection.close()

    facts = inspect(destination)
    if not facts.healthy:
        # A copy that failed its own checks is not a recovery copy, so it is not left on
        # disk under a name that suggests it is one; the refusal names what was missing.
        for suffix in ("", "-wal", "-shm", "-journal"):
            leftover = destination.with_name(destination.name + suffix)
            if leftover.exists():
                leftover.unlink()
        raise ReplacementError(
            "rollback_copy_unhealthy",
            "the copy of the current database did not pass its own checks, so nothing was "
            "replaced: " + _shortcomings(facts),
        )
    return RollbackCopy(
        path=destination,
        name=destination.name,
        bytes=destination.stat().st_size,
        sha256=_digest(destination),
    )


def _free_recovery_path(target: Path, stamp: str) -> Path:
    """A recovery name that no file already holds, chosen before the copy is written."""

    for attempt in range(1, 1000):
        suffix = "" if attempt == 1 else f"-{attempt}"
        candidate = target.with_name(f"{target.name}.pre-restore-{stamp}{suffix}.db")
        if not candidate.exists():
            return candidate
    raise ReplacementError(
        "rollback_name_unavailable",
        "no free name was available for a recovery copy, so nothing was replaced",
    )


def _shortcomings(facts) -> str:
    """Why a copy is not usable, named so the operator knows what is missing."""

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


def install_database(staged: Path, target: Path) -> None:
    """Replace ``target`` with ``staged`` atomically, on the same volume."""

    staged = Path(staged)
    target = Path(target)
    if not staged.exists():
        raise ReplacementError(
            "staged_database_missing",
            "the validated copy is no longer on disk, so nothing was replaced",
        )
    try:
        os.replace(staged, target)
    except OSError as error:
        raise ReplacementError(
            "replacement_failed",
            f"the database could not be replaced ({error.strerror or error}); "
            "the current database is unchanged",
        ) from error
    clear_sidecars(target)


def put_back(rollback: RollbackCopy | Path, target: Path) -> None:
    """Put a recovery copy back in place of the target, after a failed restore."""

    path = Path(getattr(rollback, "path", rollback))
    target = Path(target)
    clear_sidecars(target)
    try:
        os.replace(path, target)
    except OSError as error:
        raise ReplacementError(
            "rollback_failed",
            f"the previous database could not be put back ({error.strerror or error}); "
            f"the recovery copy is {path.name}",
            recovery=path.name,
        ) from error


def _digest(path: Path) -> str:
    import hashlib

    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
