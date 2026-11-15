"""Persisting a generated draft and reading one back.

The mapping between the application's objects and the stored rows lives here, in the
application layer, because it is the application that knows what a ``DraftRegister``
means. The store below it knows only rows, which is what keeps the two layers from
importing each other.

What is kept, and why:
- the request as given, so an old draft stays explicable after the masters change;
- the outcome of every attempt, including the ones that produced no draft, because
  "the allocator could not cover this month" is a finding and not an absence;
- the rows and visits exactly as presented, so reopening a draft shows what the office
  saw rather than what today's masters would produce;
- a new run for every attempt, never an update, and never a reviewed or finalised
  marker, because those are separate capabilities and not a side effect of generating.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, date, datetime
from typing import Any

from ..domain.validator import Evidence, Visit
from ..infrastructure.sqlite.generation_store import (
    ExceptionRecord,
    GenerationStorageError,
    MonthRecord,
    RowRecord,
    RunRecord,
    RunSummary,
    StoredRun,
    VisitRecord,
    insert_run,
    list_runs,
    read_run,
    runs_by_outcome,
)
from ..infrastructure.sqlite.generation_store import (
    next_revision_number as _next_revision_number,
)
from .draft_generation import (
    CalendarDayKind,
    DraftDayRow,
    DraftException,
    DraftExceptionKind,
    DraftMonth,
    DraftMonthStatus,
    DraftRegister,
    DraftStatus,
    GeneratedVisit,
    GenerationSnapshot,
    VisitProvenance,
    generate_draft,
)
from .draft_requests import PreparedGeneration
from .snapshot import REQUEST_SNAPSHOT_SCHEMA, decode_request, encode_request

__all__ = [
    "GenerationStorageError",
    "RunSummary",
    "StoredRun",
    "list_runs",
    "load_readiness",
    "load_register",
    "next_revision_number",
    "regenerate",
    "runs_by_outcome",
    "save_run",
]

OUTCOME_BY_STATUS = {
    DraftStatus.ALLOCATION_COMPLETE: "allocation_complete",
    DraftStatus.INFEASIBLE: "infeasible",
    DraftStatus.UNRESOLVED: "unresolved",
}
STATUS_BY_OUTCOME = {value: key for key, value in OUTCOME_BY_STATUS.items()}

next_revision_number = _next_revision_number


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _month_key(label: str):
    year_text, month_text = label.split("-", 1)
    from ..domain.cycle import MonthKey

    return MonthKey(int(year_text), int(month_text))


def _month_end(label: str) -> date:
    from ..domain.cycle import MonthKey

    following = _month_key(label).next()
    return date.fromordinal(date(following.year, following.month, 1).toordinal() - 1)


def _month_records(draft: DraftRegister) -> tuple[MonthRecord, ...]:
    return tuple(
        MonthRecord(
            ordinal=ordinal,
            month_key=month.month.label(),
            status=month.status.value,
            allocator_status=month.allocator_status,
            obligations_json=_json(dict(month.obligations)),
            declared_obligations_json=_json(dict(month.declared_obligations)),
            evidence_json=_json(
                [
                    {
                        "constraint": evidence.constraint,
                        "detail": evidence.detail,
                        "asset_id": evidence.asset_id,
                        "visit_date": (
                            None
                            if evidence.visit_date is None
                            else evidence.visit_date.isoformat()
                        ),
                        "limit": evidence.limit,
                        "actual": evidence.actual,
                    }
                    for evidence in month.evidence
                ]
            ),
        )
        for ordinal, month in enumerate(draft.months)
    )


def _row_records(draft: DraftRegister) -> tuple[RowRecord, ...]:
    return tuple(
        RowRecord(
            ordinal=ordinal,
            row_date=row.day.isoformat(),
            weekday=row.weekday,
            kind=row.kind.value,
            is_working_day=row.is_working_day,
            reasons_en_json=_json(list(row.reasons_en)),
            reasons_kn_json=_json(list(row.reasons_kn)),
            warnings_json=_json(list(row.warnings)),
        )
        for ordinal, row in enumerate(draft.rows)
    )


def _visit_records(draft: DraftRegister) -> tuple[VisitRecord, ...]:
    return tuple(
        VisitRecord(
            ordinal=ordinal,
            visit_date=item.visit.visit_date.isoformat(),
            asset_id=item.visit.asset_id,
            provenance=item.provenance.value,
            month_key=item.month.label(),
            name_en=item.name_en,
            name_kn=item.name_kn,
        )
        for ordinal, item in enumerate(draft.visits)
    )


def _exception_records(draft: DraftRegister) -> tuple[ExceptionRecord, ...]:
    return tuple(
        ExceptionRecord(
            ordinal=ordinal,
            kind=item.kind.value,
            constraint_name=item.constraint,
            detail=item.detail,
            month_key=None if item.month is None else item.month.label(),
            asset_id=item.asset_id,
            asset_type=item.asset_type,
            visit_date=(
                None if item.visit_date is None else item.visit_date.isoformat()
            ),
            offending_dates_json=_json(
                [day.isoformat() for day in item.offending_dates]
            ),
            limit_value=item.limit,
            actual_value=item.actual,
        )
        for ordinal, item in enumerate(draft.exceptions)
    )


def save_run(
    connection: sqlite3.Connection,
    prepared: PreparedGeneration,
    draft: DraftRegister,
    *,
    run_id: str | None = None,
    created_at: str | None = None,
) -> str:
    """Persist one attempt in full, including an attempt that produced no draft."""

    run_id = run_id or uuid.uuid4().hex
    created_at = created_at or _now()

    record = RunRecord(
        run_id=run_id,
        created_at=created_at,
        mode=prepared.request.mode.value,
        start_date=prepared.request.start.isoformat(),
        end_date=prepared.request.end.isoformat(),
        months_json=_json([month.label() for month in prepared.spec.months()]),
        seed=prepared.request.seed,
        generator_version=draft.generator_version,
        allocator_version=draft.allocator_version or "",
        outcome=OUTCOME_BY_STATUS[draft.status],
        is_draft=draft.is_allocation_complete,
        master_version=prepared.master_version,
        request_schema_version=REQUEST_SNAPSHOT_SCHEMA,
        request_snapshot_json=_json(encode_request(prepared.request)),
        readiness_json=_json(
            [
                {
                    "key": item.key,
                    "label": item.label,
                    "satisfied": item.satisfied,
                    "detail": item.detail,
                    "blocks_draft": item.blocks_draft,
                }
                for item in prepared.readiness.items
            ]
        ),
        missing_content_json=_json(list(prepared.readiness.missing_content)),
    )

    insert_run(
        connection,
        record,
        months=_month_records(draft),
        rows=_row_records(draft),
        visits=_visit_records(draft),
        exceptions=_exception_records(draft),
    )
    return run_id


def _evidence(payload: dict[str, Any]) -> Evidence:
    return Evidence(
        constraint=payload["constraint"],
        detail=payload["detail"],
        asset_id=payload.get("asset_id"),
        visit_date=(
            date.fromisoformat(payload["visit_date"])
            if payload.get("visit_date")
            else None
        ),
        limit=payload.get("limit"),
        actual=payload.get("actual"),
    )


def load_register(connection: sqlite3.Connection, run_id: str) -> DraftRegister:
    """Rebuild the register exactly as it was presented, from stored rows.

    This reads what was written, not what the allocator would say today: a draft shown
    to the office last week must not silently change because a feeder was renamed
    since. :func:`regenerate` is the separate call that re-derives the same inputs, and
    it is the one that answers whether the record is still reproducible.
    """

    stored = read_run(connection, run_id)
    request = decode_request(json.loads(stored.run.request_snapshot_json))

    generated = tuple(
        GeneratedVisit(
            visit=Visit(
                item.asset_id, date.fromisoformat(item.visit_date)
            ),
            provenance=VisitProvenance(item.provenance),
            month=_month_key(item.month_key),
            name_en=item.name_en,
            name_kn=item.name_kn,
        )
        for item in stored.visits
    )
    by_month: dict[str, list[GeneratedVisit]] = {}
    by_day: dict[date, list[GeneratedVisit]] = {}
    for item in generated:
        by_month.setdefault(item.month.label(), []).append(item)
        by_day.setdefault(item.visit.visit_date, []).append(item)

    months = tuple(
        DraftMonth(
            month=_month_key(item.month_key),
            period=_period(item.month_key),
            status=DraftMonthStatus(item.status),
            allocator_status=item.allocator_status,
            obligations=json.loads(item.obligations_json),
            declared_obligations=json.loads(item.declared_obligations_json),
            visits=tuple(by_month.get(item.month_key, ())),
            evidence=tuple(
                _evidence(entry) for entry in json.loads(item.evidence_json)
            ),
        )
        for item in stored.months
    )

    entries_by_day: dict[date, list[Any]] = {}
    for entry in request.entries:
        entries_by_day.setdefault(entry.day, []).append(entry)

    rows = tuple(
        DraftDayRow(
            day=date.fromisoformat(item.row_date),
            weekday=item.weekday,
            kind=CalendarDayKind(item.kind),
            is_working_day=item.is_working_day,
            reasons_en=tuple(json.loads(item.reasons_en_json)),
            reasons_kn=tuple(json.loads(item.reasons_kn_json)),
            fixed_visits=tuple(
                found
                for found in by_day.get(date.fromisoformat(item.row_date), ())
                if found.provenance is VisitProvenance.FIXED
            ),
            allocated_visits=tuple(
                found
                for found in by_day.get(date.fromisoformat(item.row_date), ())
                if found.provenance is VisitProvenance.ALLOCATED
            ),
            entries=tuple(entries_by_day.get(date.fromisoformat(item.row_date), ())),
            warnings=tuple(json.loads(item.warnings_json)),
        )
        for item in stored.rows
    )

    exceptions = tuple(
        DraftException(
            kind=DraftExceptionKind(item.kind),
            constraint=item.constraint_name,
            detail=item.detail,
            month=(None if item.month_key is None else _month_key(item.month_key)),
            asset_id=item.asset_id,
            asset_type=item.asset_type,
            visit_date=(
                None
                if item.visit_date is None
                else date.fromisoformat(item.visit_date)
            ),
            offending_dates=tuple(
                date.fromisoformat(day)
                for day in json.loads(item.offending_dates_json)
            ),
            limit=item.limit_value,
            actual=item.actual_value,
        )
        for item in stored.exceptions
    )

    cycle_payload = json.loads(stored.run.request_snapshot_json).get("cycle")
    snapshot = GenerationSnapshot(
        allocator_version=stored.run.allocator_version,
        generator_version=stored.run.generator_version,
        mode=request.mode,
        seed=stored.run.seed,
        start=date.fromisoformat(stored.run.start_date),
        end=date.fromisoformat(stored.run.end_date),
        months=tuple(
            _month_key(label) for label in json.loads(stored.run.months_json)
        ),
        rotation_offset=request.rotation_offset,
        attempt_budget=request.attempt_budget,
        # The fingerprints are integrity checks over live objects; the whole inputs are
        # stored instead, so a fingerprint that no longer matches is not a lost draft.
        roster_fingerprint="",
        config_fingerprint="",
        calendar_fingerprint="",
        display_fingerprint="",
        cycle=(
            None
            if cycle_payload is None
            else _cycle(cycle_payload)
        ),
        prior_history=request.prior_history,
    )

    return DraftRegister(
        status=STATUS_BY_OUTCOME[stored.run.outcome],
        mode=request.mode,
        start=snapshot.start,
        end=snapshot.end,
        seed=snapshot.seed,
        generator_version=stored.run.generator_version,
        allocator_version=stored.run.allocator_version,
        months=months,
        rows=rows,
        visits=generated,
        exceptions=exceptions,
        warnings=tuple(
            item for item in exceptions if item.kind is DraftExceptionKind.ROW_WARNING
        ),
        snapshot=snapshot,
        missing_content=tuple(json.loads(stored.run.missing_content_json)),
    )


def _period(label: str):
    from ..domain.validator import DatePeriod

    return DatePeriod(date.fromisoformat(label + "-01"), _month_end(label))


def _cycle(payload: dict[str, Any]):
    from ..domain.cycle import CoverageCycle

    return CoverageCycle(
        start_month=_month_key(payload["start_month"]),
        duration_months=int(payload["duration_months"]),
        visits_per_asset=int(payload["visits_per_asset"]),
    )


def regenerate(connection: sqlite3.Connection, run_id: str) -> DraftRegister:
    """Re-derive a stored run from its snapshot.

    This is the reproducibility check, not a way to replace a draft: it answers the
    question the office will eventually ask, which is whether the record of what was
    generated still describes what this release generates from the same inputs.
    """

    stored = read_run(connection, run_id)
    return generate_draft(decode_request(json.loads(stored.run.request_snapshot_json)))


def load_readiness(connection: sqlite3.Connection, run_id: str) -> tuple[Any, ...]:
    """The readiness report recorded with a run, as it stood at that moment."""

    return tuple(json.loads(read_run(connection, run_id).run.readiness_json))
