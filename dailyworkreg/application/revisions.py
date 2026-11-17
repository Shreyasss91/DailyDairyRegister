"""Register revisions, date review and date locks.

A generated register is not finished when the allocator returns. Somebody reads it
date by date, corrects what is wrong, locks what must not move, and regenerates the
rest. None of that may rewrite what was generated, so a register is a chain of
immutable revisions and every review is a statement about one exact row of one exact
revision.

Three rules carry the design:

*   A revision keeps saying exactly what it said. A correction is a new revision, and
    the earlier one is still readable.
*   A review belongs to the content it was made against. When a row changes, its
    review does not follow; when a row is provably identical, it may be carried
    forward, and the fingerprint is how "provably" is decided rather than trust.
*   A lock is not a review and a review is not a lock. A date can be locked and never
    read, or read and left unlocked, and the two states are reported separately.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from ..infrastructure.sqlite import generation_store, revision_state
from ..infrastructure.sqlite.database import transaction

#: Bumped only if the canonical form below changes, because a fingerprint computed by
#: another version cannot be compared with this one honestly.
FINGERPRINT_VERSION = 1

C_REGISTER_UNKNOWN = "register_unknown"
C_REVISION_UNKNOWN = "revision_unknown"
C_DATE_NOT_IN_REVISION = "date_not_in_revision"
C_REVISION_NOT_CURRENT = "revision_is_not_current"
C_DATE_NOT_LOCKED = "date_is_not_locked"
C_STALE_REVIEW = "review_content_changed_since_it_was_shown"
C_LOCKED_CONTENT_CHANGED = "locked_date_content_changed"


class RevisionError(ValueError):
    """A revision, review or lock operation that cannot be carried out.

    ``constraint`` is stable so a caller can act on it without reading the sentence.
    """

    def __init__(self, constraint: str, detail: str, violations: tuple[Any, ...] = ()):
        super().__init__(f"{constraint}: {detail}")
        self.constraint = constraint
        self.detail = detail
        self.violations = tuple(violations)


class DateReviewState(str, Enum):
    """Whether one date of one revision has been read.

    This records that the operator inspected the entry. It is not a claim that the
    work described happened, and it is not a lock.
    """

    PENDING_REVIEW = "pending_review"
    REVIEWED = "reviewed"


class MonthReviewState(str, Enum):
    """Derived from the dates: never set directly, so it cannot lie."""

    PENDING_REVIEW = "pending_review"
    REVIEWED = "reviewed"


#: One canonical spelling of a row's content, so "the row did not change" is a fact
#: about the record rather than about how it happens to be displayed today.
def canonical_row(
    *,
    day: str,
    kind: str,
    is_working_day: bool,
    reasons_en: tuple[str, ...],
    reasons_kn: tuple[str, ...],
    warnings: tuple[tuple[str, str], ...],
    visits: tuple[tuple[str, str, str, str | None], ...],
    entries: tuple[tuple[str, str, str, str | None], ...],
) -> dict[str, Any]:
    return {
        "version": FINGERPRINT_VERSION,
        "day": day,
        "kind": kind,
        "working": bool(is_working_day),
        "reasons_en": list(reasons_en),
        "reasons_kn": list(reasons_kn),
        "warnings": [list(item) for item in warnings],
        "visits": [list(item) for item in visits],
        "entries": [list(item) for item in entries],
    }


def fingerprint(canonical: dict[str, Any]) -> str:
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stored_row_content(
    connection: sqlite3.Connection, run_id: str
) -> dict[str, dict[str, Any]]:
    """The canonical content of every stored row, keyed by date.

    Read from what was written, so a review carried into a later revision compares
    stored record with stored record rather than stored record with a fresh object.
    """

    visits_by_day: dict[str, list[tuple[str, str, str, str | None]]] = {}
    for visit in generation_store.read_run(connection, run_id).visits:
        visits_by_day.setdefault(visit.visit_date, []).append(
            (visit.asset_id, visit.provenance, visit.name_en, visit.name_kn)
        )
    entries_by_day: dict[str, list[tuple[str, str, str, str | None]]] = {}
    for entry in revision_state.entries(connection, run_id):
        entries_by_day.setdefault(entry.row_date, []).append(
            (entry.entry_id, entry.kind, entry.title_en, entry.title_kn)
        )

    stored = generation_store.read_run(connection, run_id)
    content: dict[str, dict[str, Any]] = {}
    for row in stored.rows:
        content[row.row_date] = canonical_row(
            day=row.row_date,
            kind=row.kind,
            is_working_day=bool(row.is_working_day),
            reasons_en=tuple(json.loads(row.reasons_en_json)),
            reasons_kn=tuple(json.loads(row.reasons_kn_json)),
            warnings=tuple(
                (item["constraint"], item["detail"])
                for item in json.loads(row.warnings_json)
            ),
            visits=tuple(sorted(visits_by_day.get(row.row_date, []))),
            entries=tuple(sorted(entries_by_day.get(row.row_date, []))),
        )
    return content


def fingerprints(connection: sqlite3.Connection, run_id: str) -> dict[str, str]:
    return {
        day: fingerprint(canonical)
        for day, canonical in stored_row_content(connection, run_id).items()
    }


# --- registers and revisions -------------------------------------------------


def register_of(connection: sqlite3.Connection, run_id: str) -> str:
    register_id = revision_state.register_for_run(connection, run_id)
    if register_id is None:
        raise RevisionError(
            C_REVISION_UNKNOWN, f"generation run {run_id} belongs to no register"
        )
    return register_id


def current_run(connection: sqlite3.Connection, register_id: str) -> str:
    row = revision_state.register(connection, register_id)
    if row is None:
        raise RevisionError(C_REGISTER_UNKNOWN, f"unknown register {register_id}")
    if row.current_run_id is None:
        raise RevisionError(
            C_REGISTER_UNKNOWN, f"register {register_id} has no accepted revision"
        )
    return row.current_run_id


def require_month_mutable(
    connection: sqlite3.Connection, run_id: str, month: str
) -> None:
    """Reviewing or locking a date of a month that is not closed.

    Scoped to the month, because a revision can have closed November and still be
    worked on in December. Finalised content is frozen at the database as well; this is
    the sentence an operator can act on instead of an integrity error.
    """

    require_current(connection, run_id)
    from .finalisation import assert_month_not_finalised

    assert_month_not_finalised(connection, run_id, month)


def require_current(connection: sqlite3.Connection, run_id: str) -> None:
    """Refuse to change a revision that is not the register's accepted one.

    Reviewing or locking history would be an answer about a register nobody is
    working on, and an edit to it would silently fork the lineage.
    """

    register_id = register_of(connection, run_id)
    if current_run(connection, register_id) != run_id:
        raise RevisionError(
            C_REVISION_NOT_CURRENT,
            f"revision {run_id} is not the accepted revision of its register",
        )


def adopt_revision(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    base_run_id: str | None = None,
) -> None:
    """Seed review state for a new revision, carrying forward only what still holds.

    Every date starts pending. A date is carried as reviewed only when the base
    revision had it reviewed *and* its stored content and classification are
    identical, which is what the fingerprint comparison decides. Copying every
    ``reviewed`` flag forward would claim a person re-read a row they never saw.
    """

    content = stored_row_content(connection, run_id)
    new_fingerprints = {day: fingerprint(canonical) for day, canonical in content.items()}

    base_reviews: dict[str, revision_state.ReviewRow] = {}
    base_fingerprints: dict[str, str] = {}
    if base_run_id is not None:
        base_reviews = revision_state.reviews(connection, base_run_id)
        base_fingerprints = fingerprints(connection, base_run_id)

    seeded: list[revision_state.ReviewRow] = []
    for day in sorted(content):
        carried = base_reviews.get(day)
        if (
            carried is not None
            and carried.state == revision_state.REVIEWED
            and base_fingerprints.get(day) == new_fingerprints[day]
        ):
            seeded.append(
                revision_state.ReviewRow(
                    run_id=run_id,
                    row_date=day,
                    state=revision_state.REVIEWED,
                    reviewed_at=carried.reviewed_at,
                    note=carried.note,
                    content_fingerprint=new_fingerprints[day],
                    carried_from_run_id=base_run_id,
                )
            )
        else:
            seeded.append(
                revision_state.ReviewRow(
                    run_id=run_id,
                    row_date=day,
                    state=revision_state.PENDING_REVIEW,
                    reviewed_at=None,
                    note=None,
                    content_fingerprint=new_fingerprints[day],
                    carried_from_run_id=None,
                )
            )
    revision_state.insert_reviews(connection, run_id, tuple(seeded))

    if base_run_id is not None:
        base_locks = revision_state.locks(connection, base_run_id)
        for day, lock in base_locks.items():
            if day not in content:
                continue
            if base_fingerprints.get(day) != new_fingerprints[day]:
                raise RevisionError(
                    C_LOCKED_CONTENT_CHANGED,
                    f"locked date {day} would change; a locked date must be "
                    "preserved exactly, so unlock it first",
                )
            revision_state.lock(
                connection,
                run_id,
                day,
                locked_at=lock.locked_at,
                note=lock.note,
            )


def reconcile_with_base(
    connection: sqlite3.Connection, run_id: str, base_run_id: str
) -> None:
    """Bring a stored candidate up to the review and lock state it inherits.

    Called at acceptance, not at creation: a candidate is a proposal until the
    operator accepts it, and only then does the register's own review and lock state
    belong to it. A review is carried only where the base revision had it reviewed and
    the stored content is identical; a lock is carried only where the content it
    protected is unchanged, which is checked again here because this is the moment the
    guarantee has to hold.
    """

    base_fingerprints = fingerprints(connection, base_run_id)
    new_fingerprints = fingerprints(connection, run_id)
    base_reviews = revision_state.reviews(connection, base_run_id)
    current_reviews = revision_state.reviews(connection, run_id)

    for day, review in sorted(base_reviews.items()):
        if review.state != revision_state.REVIEWED:
            continue
        if day not in new_fingerprints:
            continue
        if base_fingerprints.get(day) != new_fingerprints[day]:
            continue
        existing = current_reviews.get(day)
        if existing is not None and existing.state == revision_state.REVIEWED:
            continue
        revision_state.set_review(
            connection,
            run_id,
            day,
            state=revision_state.REVIEWED,
            reviewed_at=review.reviewed_at,
            note=review.note,
            content_fingerprint=new_fingerprints[day],
        )
        revision_state.set_carried_from(connection, run_id, day, base_run_id)

    for day, lock in sorted(revision_state.locks(connection, base_run_id).items()):
        if day not in new_fingerprints:
            continue
        if base_fingerprints.get(day) != new_fingerprints[day]:
            raise RevisionError(
                C_LOCKED_CONTENT_CHANGED,
                f"locked date {day} would change; unlock it first",
            )
        revision_state.lock(
            connection, run_id, day, locked_at=lock.locked_at, note=lock.note
        )


def carry_decision(
    connection: sqlite3.Connection, run_id: str, base_run_id: str
) -> None:
    revision_state.copy_lineage(connection, base_run_id, run_id)


# --- review ------------------------------------------------------------------


def review_state(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Every date's review state, with the month states derived from them."""

    stored = generation_store.read_run(connection, run_id)
    rows = revision_state.reviews(connection, run_id)
    content = fingerprints(connection, run_id)
    locks = revision_state.locks(connection, run_id)
    register_id = register_of(connection, run_id)
    current = current_run(connection, register_id)

    days: list[dict[str, Any]] = []
    by_month: dict[str, list[str]] = {}
    for row in stored.rows:
        review = rows.get(row.row_date)
        state = (
            DateReviewState.PENDING_REVIEW
            if review is None or review.state != revision_state.REVIEWED
            else DateReviewState.REVIEWED
        )
        days.append(
            {
                "date": row.row_date,
                "state": state.value,
                "reviewed_at": None if review is None else review.reviewed_at,
                "note": None if review is None else review.note,
                "fingerprint": content.get(row.row_date, ""),
                "carried_from_run_id": (
                    None if review is None else review.carried_from_run_id
                ),
                "locked": row.row_date in locks,
                "locked_at": (
                    None if row.row_date not in locks else locks[row.row_date].locked_at
                ),
            }
        )
        by_month.setdefault(row.row_date[:7], []).append(state.value)

    months = {
        label: (
            MonthReviewState.REVIEWED.value
            if states and all(item == DateReviewState.REVIEWED.value for item in states)
            else MonthReviewState.PENDING_REVIEW.value
        )
        for label, states in sorted(by_month.items())
    }

    return {
        "run_id": run_id,
        "register_id": register_id,
        "is_current": current == run_id,
        "dates": days,
        "months": months,
        "reviewed_dates": sum(
            1 for day in days if day["state"] == DateReviewState.REVIEWED.value
        ),
        "locked_dates": sum(1 for day in days if day["locked"]),
        "total_dates": len(days),
    }


def mark_reviewed(
    connection: sqlite3.Connection,
    run_id: str,
    day: date,
    *,
    note: str | None = None,
    expected_fingerprint: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record that one date was inspected, against the content inspected."""

    require_month_mutable(connection, run_id, day.isoformat()[:7])
    content = fingerprints(connection, run_id)
    label = day.isoformat()
    if label not in content:
        raise RevisionError(
            C_DATE_NOT_IN_REVISION, f"{label} is not a date of revision {run_id}"
        )
    if expected_fingerprint is not None and expected_fingerprint != content[label]:
        raise RevisionError(
            C_STALE_REVIEW,
            f"{label} changed since it was shown; reload before reviewing it",
        )
    stamp = (now or datetime.now()).isoformat()
    revision_state.set_review(
        connection,
        run_id,
        label,
        state=revision_state.REVIEWED,
        reviewed_at=stamp,
        note=note,
        content_fingerprint=content[label],
    )
    return {"date": label, "state": DateReviewState.REVIEWED.value, "reviewed_at": stamp}



def mark_all_reviewed(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    expected_fingerprints: Mapping[str, str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Review every pending date against the exact content the operator saw.

    The complete fingerprint set is checked before any write. Pending dates are then
    marked together in one transaction, so a stale row or a finalised month cannot
    leave a partly reviewed register. Existing reviews are preserved, including their
    original timestamp and note. Locks remain an independent state.
    """

    if not isinstance(expected_fingerprints, Mapping):
        raise RevisionError(
            C_STALE_REVIEW,
            "the date fingerprints are missing; reload the draft before reviewing it",
        )

    with transaction(connection):
        require_current(connection, run_id)
        content = fingerprints(connection, run_id)
        if set(expected_fingerprints) != set(content):
            raise RevisionError(
                C_STALE_REVIEW,
                "the dates in this screen no longer match the stored register; "
                "reload the draft before reviewing all dates",
            )

        stale = sorted(
            day
            for day, current_fingerprint in content.items()
            if expected_fingerprints.get(day) != current_fingerprint
        )
        if stale:
            listed = ", ".join(stale[:5])
            suffix = " and other dates" if len(stale) > 5 else ""
            raise RevisionError(
                C_STALE_REVIEW,
                f"{listed}{suffix} changed since they were shown; "
                "reload the draft before reviewing all dates",
            )

        existing = revision_state.reviews(connection, run_id)
        pending = [
            day
            for day in sorted(content)
            if day not in existing or existing[day].state != revision_state.REVIEWED
        ]

        # Validate every affected month before writing any review state. A closed
        # month stays immutable, while already-reviewed dates in it remain untouched.
        for month in sorted({day[:7] for day in pending}):
            require_month_mutable(connection, run_id, month)

        stamp = (now or datetime.now()).isoformat()
        for day in pending:
            revision_state.set_review(
                connection,
                run_id,
                day,
                state=DateReviewState.REVIEWED.value,
                reviewed_at=stamp,
                note=None,
                content_fingerprint=content[day],
            )

        return {
            "run_id": run_id,
            "reviewed_dates": len(pending),
            "already_reviewed_dates": len(content) - len(pending),
            "total_dates": len(content),
        }


def clear_review(
    connection: sqlite3.Connection, run_id: str, day: date
) -> dict[str, Any]:
    """Withdraw a review, leaving the date present and pending."""

    require_month_mutable(connection, run_id, day.isoformat()[:7])
    content = fingerprints(connection, run_id)
    label = day.isoformat()
    if label not in content:
        raise RevisionError(
            C_DATE_NOT_IN_REVISION, f"{label} is not a date of revision {run_id}"
        )
    revision_state.set_review(
        connection,
        run_id,
        label,
        state=revision_state.PENDING_REVIEW,
        reviewed_at=None,
        note=None,
        content_fingerprint=content[label],
    )
    return {"date": label, "state": DateReviewState.PENDING_REVIEW.value}


# --- locks -------------------------------------------------------------------


def lock_date(
    connection: sqlite3.Connection,
    run_id: str,
    day: date,
    *,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Protect a date's content. Locking does not review it, and review does not lock."""

    require_month_mutable(connection, run_id, day.isoformat()[:7])
    content = fingerprints(connection, run_id)
    label = day.isoformat()
    if label not in content:
        raise RevisionError(
            C_DATE_NOT_IN_REVISION, f"{label} is not a date of revision {run_id}"
        )
    stamp = (now or datetime.now()).isoformat()
    revision_state.lock(connection, run_id, label, locked_at=stamp, note=note)
    return {"date": label, "locked": True, "locked_at": stamp}


def unlock_date(connection: sqlite3.Connection, run_id: str, day: date) -> dict[str, Any]:
    require_month_mutable(connection, run_id, day.isoformat()[:7])
    label = day.isoformat()
    removed = revision_state.unlock(connection, run_id, label)
    if not removed:
        raise RevisionError(C_DATE_NOT_LOCKED, f"{label} is not locked")
    return {"date": label, "locked": False}


def locked_dates(connection: sqlite3.Connection, run_id: str) -> tuple[str, ...]:
    return tuple(sorted(revision_state.locks(connection, run_id)))


def is_locked(connection: sqlite3.Connection, run_id: str, day: date) -> bool:
    return day.isoformat() in revision_state.locks(connection, run_id)


# --- history -----------------------------------------------------------------


def revision_summary(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = revision_state.revision(connection, run_id)
    if row is None:
        raise RevisionError(C_REVISION_UNKNOWN, f"unknown revision {run_id}")
    decided = revision_state.decision(connection, run_id)
    register_id = row.register_id or ""
    pointer = revision_state.register(connection, register_id)
    return {
        "run_id": row.run_id,
        "register_id": register_id,
        "revision_number": row.revision_number,
        "created_at": row.created_at,
        "kind": row.kind,
        "change_reason": row.change_reason,
        "parent_run_id": row.parent_run_id,
        "outcome": row.outcome,
        "is_current": pointer is not None and pointer.current_run_id == run_id,
        "decision": None if decided is None else decided.decision,
        "decided_at": None if decided is None else decided.decided_at,
        "compared_to_run_id": (
            None if decided is None else decided.compared_to_run_id
        ),
        "seed": row.seed,
    }


def register_summary(
    connection: sqlite3.Connection, register_id: str
) -> dict[str, Any]:
    row = revision_state.register(connection, register_id)
    if row is None:
        raise RevisionError(C_REGISTER_UNKNOWN, f"unknown register {register_id}")
    revisions = [
        revision_summary(connection, item.run_id)
        for item in revision_state.revisions(connection, register_id)
    ]
    return {
        "register_id": row.register_id,
        "mode": row.mode,
        "start": row.start_date,
        "end": row.end_date,
        "created_at": row.created_at,
        "current_run_id": row.current_run_id,
        "month": row.start_date[:7],
        "revisions": revisions,
    }


def registers(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        register_summary(connection, row.register_id)
        for row in revision_state.registers(connection)
    ]
