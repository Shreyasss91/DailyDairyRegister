"""Rows for register revisions, date review and date locks.

This module holds rows and nothing else. It imports no application object, so the
application can depend on it without the two layers importing each other.

Two facts shape the design:

*   A revision is immutable. Nothing here updates a stored row, a stored visit or a
    stored entry; a correction is a new revision, and the earlier one keeps saying
    exactly what it said.
*   Review and lock state belong to one revision, not to the register. A review is a
    statement that a person inspected one exact row, so carrying it forward is only
    defensible when the row is provably identical, which is what the fingerprint
    stored beside it is for.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: A date that has not been looked at, and one that has. Nothing else: this is not a
#: verdict about whether the work happened, only that the register entry was read.
PENDING_REVIEW = "pending_review"
REVIEWED = "reviewed"


class RevisionStateError(RuntimeError):
    """Raised when the stored revision state is inconsistent with the request."""

    def __init__(self, constraint: str, detail: str) -> None:
        super().__init__(f"{constraint}: {detail}")
        self.constraint = constraint
        self.detail = detail


@dataclass(frozen=True)
class RegisterRow:
    register_id: str
    mode: str
    start_date: str
    end_date: str
    created_at: str
    current_run_id: str | None


@dataclass(frozen=True)
class RevisionRow:
    run_id: str
    revision_number: int
    created_at: str
    register_id: str | None
    parent_run_id: str | None
    kind: str
    change_reason: str | None
    outcome: str
    is_draft: int
    seed: int
    #: The finalised revision this one corrects, or None. Read from the run, so a
    #: revision cannot claim a correction relationship the run does not record.
    correction_of_run_id: str | None = None


@dataclass(frozen=True)
class ReviewRow:
    run_id: str
    row_date: str
    state: str
    reviewed_at: str | None
    note: str | None
    content_fingerprint: str
    carried_from_run_id: str | None


@dataclass(frozen=True)
class LockRow:
    run_id: str
    row_date: str
    locked_at: str
    note: str | None


@dataclass(frozen=True)
class EntryRow:
    run_id: str
    entry_id: str
    row_date: str
    kind: str
    title_en: str
    title_kn: str | None
    provenance: str


@dataclass(frozen=True)
class DecisionRow:
    run_id: str
    decision: str
    decided_at: str
    compared_to_run_id: str | None
    note: str | None


@dataclass(frozen=True)
class ChangeRow:
    run_id: str
    ordinal: int
    action: str
    row_date: str | None
    entry_id: str | None
    asset_id: str | None
    detail_json: str
    created_at: str


# --- registers and revisions ------------------------------------------------


def insert_register(
    connection: sqlite3.Connection,
    register_id: str,
    *,
    mode: str,
    start_date: str,
    end_date: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO register
            (register_id, mode, start_date, end_date, created_at, current_run_id)
        VALUES (?, ?, ?, ?, ?, NULL)
        """,
        (register_id, mode, start_date, end_date, created_at),
    )


def register(connection: sqlite3.Connection, register_id: str) -> RegisterRow | None:
    row = connection.execute(
        "SELECT * FROM register WHERE register_id = ?", (register_id,)
    ).fetchone()
    return None if row is None else _register(row)


def registers(connection: sqlite3.Connection) -> tuple[RegisterRow, ...]:
    rows = connection.execute(
        "SELECT * FROM register ORDER BY created_at, register_id"
    ).fetchall()
    return tuple(_register(row) for row in rows)


def _register(row: sqlite3.Row) -> RegisterRow:
    return RegisterRow(
        register_id=row["register_id"],
        mode=row["mode"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        created_at=row["created_at"],
        current_run_id=row["current_run_id"],
    )


def register_for_run(connection: sqlite3.Connection, run_id: str) -> str | None:
    row = connection.execute(
        "SELECT register_id FROM generation_run WHERE run_id = ?", (run_id,)
    ).fetchone()
    return None if row is None else row["register_id"]


def set_current(connection: sqlite3.Connection, register_id: str, run_id: str) -> None:
    connection.execute(
        "UPDATE register SET current_run_id = ? WHERE register_id = ?",
        (run_id, register_id),
    )


def attach_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    register_id: str,
    parent_run_id: str | None,
    kind: str,
    change_reason: str | None,
) -> None:
    """Record where a run sits in its register's lineage."""

    connection.execute(
        """
        UPDATE generation_run
        SET register_id = ?, parent_run_id = ?, kind = ?, change_reason = ?
        WHERE run_id = ?
        """,
        (register_id, parent_run_id, kind, change_reason, run_id),
    )


def insert_revision(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    register_id: str,
    revision_number: int,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO draft_revision (run_id, register_id, revision_number, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (run_id, register_id, revision_number, created_at),
    )


def next_revision_number(connection: sqlite3.Connection, register_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(revision_number), 0) AS highest FROM draft_revision "
        "WHERE register_id = ?",
        (register_id,),
    ).fetchone()
    return int(row["highest"]) + 1


def revision(connection: sqlite3.Connection, run_id: str) -> RevisionRow | None:
    row = connection.execute(
        """
        SELECT r.run_id, r.revision_number, r.created_at, r.register_id,
               g.parent_run_id, g.kind, g.change_reason, g.outcome, g.is_draft,
               g.seed, g.correction_of_run_id
        FROM draft_revision r JOIN generation_run g ON g.run_id = r.run_id
        WHERE r.run_id = ?
        """,
        (run_id,),
    ).fetchone()
    return None if row is None else _revision(row)


def revisions(
    connection: sqlite3.Connection, register_id: str
) -> tuple[RevisionRow, ...]:
    rows = connection.execute(
        """
        SELECT r.run_id, r.revision_number, r.created_at, r.register_id,
               g.parent_run_id, g.kind, g.change_reason, g.outcome, g.is_draft,
               g.seed, g.correction_of_run_id
        FROM draft_revision r JOIN generation_run g ON g.run_id = r.run_id
        WHERE r.register_id = ?
        ORDER BY r.revision_number
        """,
        (register_id,),
    ).fetchall()
    return tuple(_revision(row) for row in rows)


def _revision(row: sqlite3.Row) -> RevisionRow:
    return RevisionRow(
        run_id=row["run_id"],
        revision_number=int(row["revision_number"]),
        created_at=row["created_at"],
        register_id=row["register_id"],
        parent_run_id=row["parent_run_id"],
        kind=row["kind"],
        change_reason=row["change_reason"],
        outcome=row["outcome"],
        is_draft=int(row["is_draft"]),
        seed=int(row["seed"]),
        correction_of_run_id=row["correction_of_run_id"],
    )


# --- decisions ---------------------------------------------------------------


def record_decision(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    decision: str,
    decided_at: str,
    compared_to_run_id: str | None,
    note: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO revision_decision
            (run_id, decision, decided_at, compared_to_run_id, note)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (run_id) DO UPDATE SET
            decision = excluded.decision,
            decided_at = excluded.decided_at,
            compared_to_run_id = excluded.compared_to_run_id,
            note = excluded.note
        """,
        (run_id, decision, decided_at, compared_to_run_id, note),
    )


def decision(connection: sqlite3.Connection, run_id: str) -> DecisionRow | None:
    row = connection.execute(
        "SELECT * FROM revision_decision WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    return DecisionRow(
        run_id=row["run_id"],
        decision=row["decision"],
        decided_at=row["decided_at"],
        compared_to_run_id=row["compared_to_run_id"],
        note=row["note"],
    )


# --- entries -----------------------------------------------------------------


def insert_entries(
    connection: sqlite3.Connection, run_id: str, entries: tuple[EntryRow, ...]
) -> None:
    if not entries:
        return
    connection.executemany(
        """
        INSERT INTO generation_entry
            (run_id, entry_id, row_date, kind, title_en, title_kn, provenance)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                item.entry_id,
                item.row_date,
                item.kind,
                item.title_en,
                item.title_kn,
                item.provenance,
            )
            for item in entries
        ],
    )


def entries(connection: sqlite3.Connection, run_id: str) -> tuple[EntryRow, ...]:
    rows = connection.execute(
        "SELECT * FROM generation_entry WHERE run_id = ? ORDER BY row_date, entry_id",
        (run_id,),
    ).fetchall()
    return tuple(
        EntryRow(
            run_id=row["run_id"],
            entry_id=row["entry_id"],
            row_date=row["row_date"],
            kind=row["kind"],
            title_en=row["title_en"],
            title_kn=row["title_kn"],
            provenance=row["provenance"],
        )
        for row in rows
    )


# --- review ------------------------------------------------------------------


def insert_reviews(
    connection: sqlite3.Connection, run_id: str, reviews: tuple[ReviewRow, ...]
) -> None:
    if not reviews:
        return
    connection.executemany(
        """
        INSERT INTO date_review
            (run_id, row_date, state, reviewed_at, note, content_fingerprint,
             carried_from_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id, row_date) DO NOTHING
        """,
        [
            (
                run_id,
                item.row_date,
                item.state,
                item.reviewed_at,
                item.note,
                item.content_fingerprint,
                item.carried_from_run_id,
            )
            for item in reviews
        ],
    )


def reviews(connection: sqlite3.Connection, run_id: str) -> dict[str, ReviewRow]:
    rows = connection.execute(
        "SELECT * FROM date_review WHERE run_id = ? ORDER BY row_date", (run_id,)
    ).fetchall()
    return {
        row["row_date"]: ReviewRow(
            run_id=row["run_id"],
            row_date=row["row_date"],
            state=row["state"],
            reviewed_at=row["reviewed_at"],
            note=row["note"],
            content_fingerprint=row["content_fingerprint"],
            carried_from_run_id=row["carried_from_run_id"],
        )
        for row in rows
    }


def set_review(
    connection: sqlite3.Connection,
    run_id: str,
    row_date: str,
    *,
    state: str,
    reviewed_at: str | None,
    note: str | None,
    content_fingerprint: str,
) -> None:
    connection.execute(
        """
        INSERT INTO date_review
            (run_id, row_date, state, reviewed_at, note, content_fingerprint,
             carried_from_run_id)
        VALUES (?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT (run_id, row_date) DO UPDATE SET
            state = excluded.state,
            reviewed_at = excluded.reviewed_at,
            note = excluded.note,
            content_fingerprint = excluded.content_fingerprint,
            carried_from_run_id = NULL
        """,
        (run_id, row_date, state, reviewed_at, note, content_fingerprint),
    )


def set_carried_from(
    connection: sqlite3.Connection, run_id: str, row_date: str, base_run_id: str
) -> None:
    """Record which revision a carried review came from."""

    connection.execute(
        "UPDATE date_review SET carried_from_run_id = ? "
        "WHERE run_id = ? AND row_date = ?",
        (base_run_id, run_id, row_date),
    )


# --- locks -------------------------------------------------------------------


def lock(
    connection: sqlite3.Connection,
    run_id: str,
    row_date: str,
    *,
    locked_at: str,
    note: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO date_lock (run_id, row_date, locked_at, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (run_id, row_date) DO UPDATE SET
            locked_at = excluded.locked_at,
            note = excluded.note
        """,
        (run_id, row_date, locked_at, note),
    )


def unlock(connection: sqlite3.Connection, run_id: str, row_date: str) -> int:
    cursor = connection.execute(
        "DELETE FROM date_lock WHERE run_id = ? AND row_date = ?",
        (run_id, row_date),
    )
    return cursor.rowcount


def locks(connection: sqlite3.Connection, run_id: str) -> dict[str, LockRow]:
    rows = connection.execute(
        "SELECT * FROM date_lock WHERE run_id = ? ORDER BY row_date", (run_id,)
    ).fetchall()
    return {
        row["row_date"]: LockRow(
            run_id=row["run_id"],
            row_date=row["row_date"],
            locked_at=row["locked_at"],
            note=row["note"],
        )
        for row in rows
    }


# --- audit -------------------------------------------------------------------


def record_change(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    ordinal: int,
    action: str,
    row_date: str | None,
    entry_id: str | None,
    asset_id: str | None,
    detail_json: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO revision_change
            (run_id, ordinal, action, row_date, entry_id, asset_id, detail_json,
             created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, ordinal, action, row_date, entry_id, asset_id, detail_json, created_at),
    )


def changes(connection: sqlite3.Connection, run_id: str) -> tuple[ChangeRow, ...]:
    rows = connection.execute(
        "SELECT * FROM revision_change WHERE run_id = ? ORDER BY ordinal", (run_id,)
    ).fetchall()
    return tuple(
        ChangeRow(
            run_id=row["run_id"],
            ordinal=int(row["ordinal"]),
            action=row["action"],
            row_date=row["row_date"],
            entry_id=row["entry_id"],
            asset_id=row["asset_id"],
            detail_json=row["detail_json"],
            created_at=row["created_at"],
        )
        for row in rows
    )


def copy_lineage(
    connection: sqlite3.Connection, from_run_id: str, to_run_id: str
) -> None:
    """Copy the audit trail of a revision onto the revision it produced."""

    rows = changes(connection, from_run_id)
    for row in rows:
        record_change(
            connection,
            to_run_id,
            ordinal=row.ordinal,
            action=row.action,
            row_date=row.row_date,
            entry_id=row.entry_id,
            asset_id=row.asset_id,
            detail_json=row.detail_json,
            created_at=row.created_at,
        )


# --- monthly finalisation ----------------------------------------------------
#
# A finalised month is one row that never changes, plus one pointer that says which row
# is authoritative. The snapshot is stored rather than recomputed: the record the office
# approved has to stay readable even after the code that renders it changes.


@dataclass(frozen=True)
class FinalisationRow:
    finalisation_id: str
    register_id: str
    month: str
    run_id: str
    revision_number: int
    sequence: int
    parent_finalisation_id: str | None
    correction_of_run_id: str | None
    finalised_at: str
    fingerprint: str
    content_json: str
    acknowledged_json: str
    readiness_json: str
    master_version: int
    note: str | None


@dataclass(frozen=True)
class MonthStateRow:
    register_id: str
    month: str
    latest_finalisation_id: str | None
    open_correction_run_id: str | None


def _finalisation(row: sqlite3.Row) -> FinalisationRow:
    return FinalisationRow(
        finalisation_id=row["finalisation_id"],
        register_id=row["register_id"],
        month=row["month"],
        run_id=row["run_id"],
        revision_number=int(row["revision_number"]),
        sequence=int(row["sequence"]),
        parent_finalisation_id=row["parent_finalisation_id"],
        correction_of_run_id=row["correction_of_run_id"],
        finalised_at=row["finalised_at"],
        fingerprint=row["fingerprint"],
        content_json=row["content_json"],
        acknowledged_json=row["acknowledged_json"],
        readiness_json=row["readiness_json"],
        master_version=int(row["master_version"]),
        note=row["note"],
    )


def insert_finalisation(connection: sqlite3.Connection, row: FinalisationRow) -> None:
    connection.execute(
        """
        INSERT INTO month_finalisation
            (finalisation_id, register_id, month, run_id, revision_number, sequence,
             parent_finalisation_id, correction_of_run_id, finalised_at, fingerprint,
             content_json, acknowledged_json, readiness_json, master_version, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.finalisation_id,
            row.register_id,
            row.month,
            row.run_id,
            row.revision_number,
            row.sequence,
            row.parent_finalisation_id,
            row.correction_of_run_id,
            row.finalised_at,
            row.fingerprint,
            row.content_json,
            row.acknowledged_json,
            row.readiness_json,
            row.master_version,
            row.note,
        ),
    )


def finalisation(
    connection: sqlite3.Connection, finalisation_id: str
) -> FinalisationRow | None:
    row = connection.execute(
        "SELECT * FROM month_finalisation WHERE finalisation_id = ?",
        (finalisation_id,),
    ).fetchone()
    return None if row is None else _finalisation(row)


def finalisation_for_run(
    connection: sqlite3.Connection, run_id: str
) -> FinalisationRow | None:
    """The newest finalisation recorded from this revision, if any.

    One revision can close several months, so this is a summary of the revision rather
    than a statement about any one month; ask ``finalisation_for_run_month`` when the
    month matters.
    """

    row = connection.execute(
        "SELECT * FROM month_finalisation WHERE run_id = ? ORDER BY sequence DESC",
        (run_id,),
    ).fetchone()
    return None if row is None else _finalisation(row)


def finalisations_of_run(
    connection: sqlite3.Connection, run_id: str
) -> tuple[FinalisationRow, ...]:
    """Every month this revision closed, oldest first."""

    rows = connection.execute(
        "SELECT * FROM month_finalisation WHERE run_id = ? ORDER BY month, sequence",
        (run_id,),
    ).fetchall()
    return tuple(_finalisation(row) for row in rows)


def all_finalisations(connection: sqlite3.Connection) -> tuple[FinalisationRow, ...]:
    """Every finalised record in the database, oldest month first.

    Used where the question is about the whole store rather than about one register: a
    backup's structure, and the verification that every issued version survived a restore.
    """

    rows = connection.execute(
        "SELECT * FROM month_finalisation ORDER BY register_id, month, sequence"
    ).fetchall()
    return tuple(_finalisation(row) for row in rows)


def finalisation_for_run_month(
    connection: sqlite3.Connection, run_id: str, month: str
) -> FinalisationRow | None:
    row = connection.execute(
        "SELECT * FROM month_finalisation WHERE run_id = ? AND month = ?"
        " ORDER BY sequence DESC",
        (run_id, month),
    ).fetchone()
    return None if row is None else _finalisation(row)


def finalisations(
    connection: sqlite3.Connection, register_id: str, month: str
) -> tuple[FinalisationRow, ...]:
    rows = connection.execute(
        """
        SELECT * FROM month_finalisation
        WHERE register_id = ? AND month = ?
        ORDER BY sequence
        """,
        (register_id, month),
    ).fetchall()
    return tuple(_finalisation(row) for row in rows)


def next_finalisation_sequence(
    connection: sqlite3.Connection, register_id: str, month: str
) -> int:
    row = connection.execute(
        """
        SELECT COALESCE(MAX(sequence), 0) AS highest FROM month_finalisation
        WHERE register_id = ? AND month = ?
        """,
        (register_id, month),
    ).fetchone()
    return int(row["highest"]) + 1


def month_state(
    connection: sqlite3.Connection, register_id: str, month: str
) -> MonthStateRow | None:
    row = connection.execute(
        "SELECT * FROM month_state WHERE register_id = ? AND month = ?",
        (register_id, month),
    ).fetchone()
    if row is None:
        return None
    return MonthStateRow(
        register_id=row["register_id"],
        month=row["month"],
        latest_finalisation_id=row["latest_finalisation_id"],
        open_correction_run_id=row["open_correction_run_id"],
    )


def month_states(
    connection: sqlite3.Connection, register_id: str
) -> tuple[MonthStateRow, ...]:
    rows = connection.execute(
        "SELECT * FROM month_state WHERE register_id = ? ORDER BY month",
        (register_id,),
    ).fetchall()
    return tuple(
        MonthStateRow(
            register_id=row["register_id"],
            month=row["month"],
            latest_finalisation_id=row["latest_finalisation_id"],
            open_correction_run_id=row["open_correction_run_id"],
        )
        for row in rows
    )


def set_latest_finalisation(
    connection: sqlite3.Connection,
    register_id: str,
    month: str,
    finalisation_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO month_state (register_id, month, latest_finalisation_id,
            open_correction_run_id)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT (register_id, month) DO UPDATE
            SET latest_finalisation_id = excluded.latest_finalisation_id
        """,
        (register_id, month, finalisation_id),
    )


def set_open_correction(
    connection: sqlite3.Connection, register_id: str, month: str, run_id: str
) -> None:
    connection.execute(
        """
        INSERT INTO month_state (register_id, month, latest_finalisation_id,
            open_correction_run_id)
        VALUES (?, ?, NULL, ?)
        ON CONFLICT (register_id, month) DO UPDATE
            SET open_correction_run_id = excluded.open_correction_run_id
        """,
        (register_id, month, run_id),
    )


def clear_open_correction(
    connection: sqlite3.Connection, register_id: str, month: str
) -> None:
    connection.execute(
        """
        UPDATE month_state SET open_correction_run_id = NULL
        WHERE register_id = ? AND month = ?
        """,
        (register_id, month),
    )


def finalised_run_ids(
    connection: sqlite3.Connection, register_id: str
) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT run_id FROM month_finalisation WHERE register_id = ? ORDER BY sequence",
        (register_id,),
    ).fetchall()
    return tuple(row["run_id"] for row in rows)


def correction_of(connection: sqlite3.Connection, run_id: str) -> str | None:
    row = connection.execute(
        "SELECT correction_of_run_id FROM generation_run WHERE run_id = ?", (run_id,)
    ).fetchone()
    return None if row is None else row["correction_of_run_id"]
