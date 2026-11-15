"""Row storage for generation runs: attempts, revisions, months, rows and visits.

This module reads and writes rows and nothing else. It holds no draft objects and
imports nothing from the application layer, which is what lets the application layer
depend on it without the two becoming each other's importers.

Every attempt is stored, whether or not it produced a usable draft. An attempt that
could not cover a month is a finding the office will be asked about later, and it has
to be inspectable rather than remembered.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from .database import transaction

C_RUN_UNKNOWN = "generation_run_unknown"

OUTCOMES = ("allocation_complete", "infeasible", "unresolved")


class GenerationStorageError(ValueError):
    """A stored run cannot be read or written as asked."""

    def __init__(self, constraint: str, detail: str):
        self.constraint = constraint
        self.detail = detail
        super().__init__(f"{constraint}: {detail}")


@dataclass(frozen=True)
class RunRecord:
    """Every stored column of one attempt, as plain values."""

    run_id: str
    created_at: str
    mode: str
    start_date: str
    end_date: str
    months_json: str
    seed: int
    generator_version: str
    allocator_version: str
    outcome: str
    is_draft: bool
    master_version: int
    request_schema_version: int
    request_snapshot_json: str
    readiness_json: str
    missing_content_json: str


@dataclass(frozen=True)
class MonthRecord:
    ordinal: int
    month_key: str
    status: str
    allocator_status: str
    obligations_json: str
    declared_obligations_json: str
    evidence_json: str


@dataclass(frozen=True)
class RowRecord:
    ordinal: int
    row_date: str
    weekday: int
    kind: str
    is_working_day: bool
    reasons_en_json: str
    reasons_kn_json: str
    warnings_json: str


@dataclass(frozen=True)
class VisitRecord:
    ordinal: int
    visit_date: str
    asset_id: str
    provenance: str
    month_key: str
    name_en: str
    name_kn: str | None


@dataclass(frozen=True)
class ExceptionRecord:
    ordinal: int
    kind: str
    constraint_name: str
    detail: str
    month_key: str | None
    asset_id: str | None
    asset_type: str | None
    visit_date: str | None
    offending_dates_json: str
    limit_value: int | None
    actual_value: int | None


@dataclass(frozen=True)
class StoredRun:
    run: RunRecord
    months: tuple[MonthRecord, ...] = field(default=())
    rows: tuple[RowRecord, ...] = field(default=())
    visits: tuple[VisitRecord, ...] = field(default=())
    exceptions: tuple[ExceptionRecord, ...] = field(default=())
    revision_number: int | None = None


@dataclass(frozen=True)
class RunSummary:
    """One line of the generation history, without the register attached."""

    run_id: str
    created_at: str
    mode: str
    start: date
    end: date
    months: tuple[str, ...]
    seed: int
    outcome: str
    is_draft: bool
    master_version: int
    revision_number: int | None
    missing_content: tuple[str, ...]


def next_revision_number(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(revision_number), 0) AS highest FROM draft_revision"
    ).fetchone()
    return int(row["highest"]) + 1


def insert_run(
    connection: sqlite3.Connection,
    run: RunRecord,
    *,
    months: tuple[MonthRecord, ...] = (),
    rows: tuple[RowRecord, ...] = (),
    visits: tuple[VisitRecord, ...] = (),
    exceptions: tuple[ExceptionRecord, ...] = (),
) -> int | None:
    """Write one attempt and everything derived from it, or write nothing.

    One transaction covers bookkeeping rows only. Allocation has already finished by
    the time this is called, so no long-running transaction is held across it, and a
    failure leaves no half-written run to be mistaken for a complete one.

    Returns the draft revision number assigned, or None when the attempt produced no
    draft. A revision is allocated here, inside the same transaction, because two
    concurrent attempts must not both believe they are revision 3.
    """

    with transaction(connection):
        connection.execute(
            """
            INSERT INTO generation_run (run_id, created_at, mode, start_date, end_date,
                months_json, seed, generator_version, allocator_version, outcome,
                is_draft, master_version, request_schema_version,
                request_snapshot_json, readiness_json, missing_content_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.created_at,
                run.mode,
                run.start_date,
                run.end_date,
                run.months_json,
                run.seed,
                run.generator_version,
                run.allocator_version,
                run.outcome,
                int(run.is_draft),
                run.master_version,
                run.request_schema_version,
                run.request_snapshot_json,
                run.readiness_json,
                run.missing_content_json,
            ),
        )

        revision: int | None = None
        if run.is_draft:
            revision = next_revision_number(connection)
            connection.execute(
                """
                INSERT INTO draft_revision (run_id, revision_number, created_at)
                VALUES (?, ?, ?)
                """,
                (run.run_id, revision, run.created_at),
            )

        connection.executemany(
            """
            INSERT INTO generation_month (run_id, ordinal, month_key, status,
                allocator_status, obligations_json, declared_obligations_json,
                evidence_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run.run_id,
                    month.ordinal,
                    month.month_key,
                    month.status,
                    month.allocator_status,
                    month.obligations_json,
                    month.declared_obligations_json,
                    month.evidence_json,
                )
                for month in months
            ],
        )
        connection.executemany(
            """
            INSERT INTO generation_row (run_id, ordinal, row_date, weekday, kind,
                is_working_day, reasons_en_json, reasons_kn_json, warnings_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run.run_id,
                    row.ordinal,
                    row.row_date,
                    row.weekday,
                    row.kind,
                    int(row.is_working_day),
                    row.reasons_en_json,
                    row.reasons_kn_json,
                    row.warnings_json,
                )
                for row in rows
            ],
        )
        connection.executemany(
            """
            INSERT INTO generation_visit (run_id, ordinal, visit_date, asset_id,
                provenance, month_key, name_en, name_kn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run.run_id,
                    visit.ordinal,
                    visit.visit_date,
                    visit.asset_id,
                    visit.provenance,
                    visit.month_key,
                    visit.name_en,
                    visit.name_kn,
                )
                for visit in visits
            ],
        )
        connection.executemany(
            """
            INSERT INTO generation_exception (run_id, ordinal, kind, constraint_name,
                detail, month_key, asset_id, asset_type, visit_date,
                offending_dates_json, limit_value, actual_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run.run_id,
                    item.ordinal,
                    item.kind,
                    item.constraint_name,
                    item.detail,
                    item.month_key,
                    item.asset_id,
                    item.asset_type,
                    item.visit_date,
                    item.offending_dates_json,
                    item.limit_value,
                    item.actual_value,
                )
                for item in exceptions
            ],
        )
    return revision


def _run_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        created_at=row["created_at"],
        mode=row["mode"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        months_json=row["months_json"],
        seed=int(row["seed"]),
        generator_version=row["generator_version"],
        allocator_version=row["allocator_version"],
        outcome=row["outcome"],
        is_draft=bool(row["is_draft"]),
        master_version=int(row["master_version"]),
        request_schema_version=int(row["request_schema_version"]),
        request_snapshot_json=row["request_snapshot_json"],
        readiness_json=row["readiness_json"],
        missing_content_json=row["missing_content_json"],
    )


def read_run(connection: sqlite3.Connection, run_id: str) -> StoredRun:
    """Everything stored for one attempt."""

    row = connection.execute(
        "SELECT * FROM generation_run WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise GenerationStorageError(
            C_RUN_UNKNOWN, f"no generation run is stored with id {run_id!r}"
        )

    months = connection.execute(
        "SELECT * FROM generation_month WHERE run_id = ? ORDER BY ordinal", (run_id,)
    ).fetchall()
    rows = connection.execute(
        "SELECT * FROM generation_row WHERE run_id = ? ORDER BY ordinal", (run_id,)
    ).fetchall()
    visits = connection.execute(
        "SELECT * FROM generation_visit WHERE run_id = ? ORDER BY ordinal", (run_id,)
    ).fetchall()
    exceptions = connection.execute(
        "SELECT * FROM generation_exception WHERE run_id = ? ORDER BY ordinal",
        (run_id,),
    ).fetchall()
    revision = connection.execute(
        "SELECT revision_number FROM draft_revision WHERE run_id = ?", (run_id,)
    ).fetchone()

    return StoredRun(
        run=_run_record(row),
        months=tuple(
            MonthRecord(
                ordinal=int(item["ordinal"]),
                month_key=item["month_key"],
                status=item["status"],
                allocator_status=item["allocator_status"],
                obligations_json=item["obligations_json"],
                declared_obligations_json=item["declared_obligations_json"],
                evidence_json=item["evidence_json"],
            )
            for item in months
        ),
        rows=tuple(
            RowRecord(
                ordinal=int(item["ordinal"]),
                row_date=item["row_date"],
                weekday=int(item["weekday"]),
                kind=item["kind"],
                is_working_day=bool(item["is_working_day"]),
                reasons_en_json=item["reasons_en_json"],
                reasons_kn_json=item["reasons_kn_json"],
                warnings_json=item["warnings_json"],
            )
            for item in rows
        ),
        visits=tuple(
            VisitRecord(
                ordinal=int(item["ordinal"]),
                visit_date=item["visit_date"],
                asset_id=item["asset_id"],
                provenance=item["provenance"],
                month_key=item["month_key"],
                name_en=item["name_en"],
                name_kn=item["name_kn"],
            )
            for item in visits
        ),
        exceptions=tuple(
            ExceptionRecord(
                ordinal=int(item["ordinal"]),
                kind=item["kind"],
                constraint_name=item["constraint_name"],
                detail=item["detail"],
                month_key=item["month_key"],
                asset_id=item["asset_id"],
                asset_type=item["asset_type"],
                visit_date=item["visit_date"],
                offending_dates_json=item["offending_dates_json"],
                limit_value=item["limit_value"],
                actual_value=item["actual_value"],
            )
            for item in exceptions
        ),
        revision_number=None if revision is None else int(revision["revision_number"]),
    )


def _summary(row: sqlite3.Row, revision: int | None, months: tuple[str, ...],
             missing: tuple[str, ...]) -> RunSummary:
    return RunSummary(
        run_id=row["run_id"],
        created_at=row["created_at"],
        mode=row["mode"],
        start=date.fromisoformat(row["start_date"]),
        end=date.fromisoformat(row["end_date"]),
        months=months,
        seed=int(row["seed"]),
        outcome=row["outcome"],
        is_draft=bool(row["is_draft"]),
        master_version=int(row["master_version"]),
        revision_number=revision,
        missing_content=missing,
    )


def list_runs(connection: sqlite3.Connection) -> tuple[RunSummary, ...]:
    """Every attempt, newest first."""

    import json

    rows = connection.execute(
        """
        SELECT r.*, d.revision_number AS revision_number
        FROM generation_run r
        LEFT JOIN draft_revision d ON d.run_id = r.run_id
        ORDER BY r.created_at DESC, r.run_id DESC
        """
    ).fetchall()
    return tuple(
        _summary(
            row,
            None if row["revision_number"] is None else int(row["revision_number"]),
            tuple(json.loads(row["months_json"])),
            tuple(json.loads(row["missing_content_json"])),
        )
        for row in rows
    )


def runs_by_outcome(
    connection: sqlite3.Connection,
) -> dict[str, tuple[RunSummary, ...]]:
    """History split the way the office reads it: drafts, and attempts with none."""

    grouped: dict[str, list[RunSummary]] = {outcome: [] for outcome in OUTCOMES}
    for run in list_runs(connection):
        grouped[run.outcome].append(run)
    return {key: tuple(value) for key, value in grouped.items()}
