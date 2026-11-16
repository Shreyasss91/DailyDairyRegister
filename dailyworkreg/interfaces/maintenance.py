"""The barrier that makes replacing the database safe, and the desk that holds a package.

Replacing the database file while a request is reading it is not a race that can be won by
retrying: on Windows the file cannot be replaced at all while a handle is open, and on any
platform a connection that survives the swap is a connection reading a file that no longer
exists at the path it opened. So the replacement needs a moment when nothing else is using
the database, and that moment has to be arranged rather than hoped for.

This application already opens a connection per request and closes it in the same request
(``get_db``), so there is no pool and no long-lived handle. The barrier below turns that
into a guarantee:

*   An ordinary request takes a shared slot before its connection is opened and releases it
    after the connection is closed. If a restore has started, new requests are refused
    immediately with a stable identifier rather than queued behind an unknown wait.
*   A restore takes the exclusive slot: it sets the draining flag, waits for the shared
    count to reach zero, and only then lets the caller replace the file. The wait is
    bounded, and a timeout releases the flag and refuses the restore instead of replacing a
    database somebody may still be holding.

Nothing here decides whether a restore is allowed; that decision is in the application
layer. This module owns only the ordering, which is a property of the running process.

The desk beside it holds what the operator is between steps on: the most recent backup
packages, so a download follows a visible success, and the one validated restore awaiting
confirmation. Both are in memory and belong to this process; a restart drops them, which is
the right lifetime for a confirmation that must be deliberate.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from typing import Any

from ..application.backup_restore import CONFIRMATION_WORD, BackupPackage, StagedRestore

#: How long a restore waits for in-flight requests before giving up. Generous enough for a
#: slow generation request to finish, short enough that an operator is not left watching a
#: spinner with no explanation.
DRAIN_TIMEOUT_SECONDS = 30.0

#: How many backup packages are held in memory so a download can follow the creation. A
#: single-operator tool needs the last one; keeping a few costs nothing and covers a double
#: click.
BACKUPS_KEPT = 3


class MaintenanceBusy(Exception):
    """The tool is replacing its database, or another restore already holds the barrier."""

    constraint = "restore_in_progress"
    status = 503

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class MaintenanceGate:
    """Admit ordinary requests, or hold the whole process still for a replacement."""

    def __init__(self, *, timeout: float = DRAIN_TIMEOUT_SECONDS):
        self._condition = threading.Condition()
        self._active = 0
        self._draining = False
        self._timeout = timeout

    @property
    def draining(self) -> bool:
        with self._condition:
            return self._draining

    @property
    def active(self) -> int:
        with self._condition:
            return self._active

    @contextmanager
    def request(self):
        """One ordinary request's claim on the database."""

        with self._condition:
            if self._draining:
                raise MaintenanceBusy(
                    "the tool is replacing its database; this page will work again in a "
                    "moment"
                )
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    @contextmanager
    def exclusive(self):
        """The single claim that may replace the database file."""

        with self._condition:
            if self._draining:
                raise MaintenanceBusy("another restore is already replacing the database")
            self._draining = True
            deadline = time.monotonic() + self._timeout
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._draining = False
                    self._condition.notify_all()
                    raise MaintenanceBusy(
                        "the database is still in use by other requests; try the restore "
                        "again in a moment"
                    )
                self._condition.wait(remaining)
        try:
            yield
        finally:
            with self._condition:
                self._draining = False
                self._condition.notify_all()


@dataclass
class HeldBackup:
    """A backup package this process is holding so the operator can download it."""

    backup_id: str
    package: BackupPackage
    created_at: datetime

    def as_payload(self) -> dict[str, Any]:
        summary = self.package.summary
        contents = dict(summary.get("contents") or {})
        return {
            "backup_id": self.backup_id,
            "filename": self.package.filename,
            "created_at": summary.get("created_at") or self.created_at.isoformat(),
            "format": summary.get("format"),
            "format_version": summary.get("format_version"),
            "schema_version": summary.get("schema_version"),
            "size_bytes": self.package.bytes,
            "checksum": summary.get("payload_sha256"),
            "contents": contents,
            "verified": dict(self.package.verified),
            "download": f"/api/backups/{self.backup_id}/download",
        }


@dataclass
class PendingRestore:
    """A validated package whose database is staged beside the live one."""

    token: str
    staged: StagedRestore
    validated_at: datetime
    upload_name: str

    def as_payload(self) -> dict[str, Any]:
        summary = self.staged.summary
        return {
            "restore_token": self.token,
            "filename": self.upload_name,
            "format": summary.get("format"),
            "format_version": summary.get("format_version"),
            "created_at": summary.get("created_at"),
            "schema_version": summary.get("schema_version"),
            "backup_schema_version": self.staged.source_schema_version,
            "this_tool_schema_version": self.staged.facts.schema_version,
            "payload_bytes": summary.get("payload_bytes"),
            "checksum": summary.get("payload_sha256"),
            "contents": dict(summary.get("contents") or {}),
            "migrations_applied": list(self.staged.migrations_applied),
            "warnings": list(self.staged.warnings),
            "can_restore": True,
            "confirmation_word": CONFIRMATION_WORD,
        }


@dataclass
class BackupDesk:
    """What the operator is between steps on, for the lifetime of this process."""

    backups: OrderedDict[str, HeldBackup] = field(default_factory=OrderedDict)
    pending: PendingRestore | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def remember(self, package: BackupPackage, backup_id: str) -> HeldBackup:
        held = HeldBackup(
            backup_id=backup_id, package=package, created_at=datetime.now(UTC)
        )
        with self._lock:
            self.backups[backup_id] = held
            while len(self.backups) > BACKUPS_KEPT:
                self.backups.popitem(last=False)
        return held

    def backup(self, backup_id: str) -> HeldBackup | None:
        with self._lock:
            return self.backups.get(backup_id)

    def hold(self, pending: PendingRestore) -> PendingRestore:
        """Remember a validated restore, dropping any previous staged copy."""

        with self._lock:
            previous = self.pending
            self.pending = pending
        if previous is not None and previous.staged.staged != pending.staged.staged:
            try:
                if previous.staged.staged.exists():
                    previous.staged.staged.unlink()
            except OSError:
                pass
        return pending

    def outstanding(self, token: str) -> PendingRestore | None:
        with self._lock:
            if self.pending is None or self.pending.token != token:
                return None
            return self.pending

    def clear(self) -> None:
        with self._lock:
            previous, self.pending = self.pending, None
        if previous is not None:
            try:
                if previous.staged.staged.exists():
                    previous.staged.staged.unlink()
            except OSError:
                pass
