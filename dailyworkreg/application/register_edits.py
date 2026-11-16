"""Correcting a stored register, one revision at a time.

A correction never rewrites the revision that was corrected. It reads the accepted
revision, applies exactly one typed change to it, and stores the result as a new
revision whose parent is the one it came from. The earlier revision keeps saying what
it said, which is what lets the office compare a correction with what it replaced.

Two rules decide what may be corrected in place:

*   A locked date is preserved exactly, so it cannot be edited, added to, moved into
    or moved out of. Unlocking is the operator's explicit act, not a side effect of
    correcting something beside it.
*   A field-visit date is a scheduling fact, so a moved visit is judged by the
    validator that judges every other schedule rather than by arithmetic here. This
    module owns no daily cap, revisit interval or monthly maximum.

A visit is moved within its own calendar month. The validator assesses one calendar
month at a time by contract, and a cross-month move would silently change two months'
obligations without either month being assessed as a whole; it is refused rather than
half-checked.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from ..domain.validator import (
    Calendar,
    ProposalContext,
    ValidationMode,
    Visit,
    validate_schedule,
)
from ..infrastructure.sqlite import generation_store, revision_state
from .draft_generation import (
    DayEntry,
    DayEntryKind,
    DayEntryProvenance,
    DraftDayRow,
    DraftMonth,
    DraftRegister,
    GeneratedVisit,
    VisitProvenance,
)
from .generation_runs import OUTCOME_BY_STATUS, load_register
from .snapshot import encode_request
from .revisions import C_DATE_NOT_IN_REVISION, RevisionError, require_current

C_ENTRY_UNKNOWN = "entry_not_on_this_revision"
C_DATE_LOCKED = "date_is_locked"
C_MANUAL_LEAVE_UNSUPPORTED = "manual_leave_entry_unsupported"
C_ENTRY_KIND_UNKNOWN = "entry_kind_unknown"
C_ENTRY_TEXT_EMPTY = "entry_text_empty"
C_VISIT_UNKNOWN = "visit_not_on_this_revision"
C_VISIT_MOVE_CROSSES_MONTH = "visit_move_crosses_month_unsupported"
C_VISIT_EDIT_REJECTED = "corrected_visit_fails_validation"


@dataclass(frozen=True)
class EditResult:
    run_id: str
    revision_number: int
    parent_run_id: str
    action: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def save_revision(
    connection: sqlite3.Connection,
    draft: DraftRegister,
    *,
    base_run_id: str,
    change_reason: str | None,
    run_id: str | None = None,
    kind: str = "edit",
    request: Any = None,
) -> str:
    """Store a corrected copy of a revision as its own revision.

    The generation inputs are copied from the revision being corrected rather than
    re-read from today's masters: a correction is not a regeneration, and its snapshot
    must keep describing the run it descends from. Only the master version advances,
    because the validation that judged the correction ran against the masters in force
    now.
    """

    from .revisions import adopt_revision, register_of

    base = generation_store.read_run(connection, base_run_id)
    register_id = register_of(connection, base_run_id)
    new_id = run_id or uuid.uuid4().hex
    created_at = _now()

    locks = revision_state.locks(connection, base_run_id)
    entries = []
    for row in draft.rows:
        for entry in row.entries:
            provenance = entry.provenance
            if row.day.isoformat() in locks and provenance == DayEntryProvenance.CALENDAR:
                # On a locked date the entry is preserved rather than re-derived, so it
                # is stored as such and a later regeneration leaves it alone.
                provenance = DayEntryProvenance.LOCKED
            entries.append(
                revision_state.EntryRow(
                    run_id="",
                    entry_id=entry.entry_id,
                    row_date=row.day.isoformat(),
                    kind=entry.kind.value,
                    title_en=entry.title_en,
                    title_kn=entry.title_kn,
                    provenance=provenance.value
                    if isinstance(provenance, DayEntryProvenance)
                    else provenance,
                )
            )

    record = generation_store.RunRecord(
        run_id=new_id,
        created_at=created_at,
        mode=base.run.mode,
        start_date=base.run.start_date,
        end_date=base.run.end_date,
        months_json=base.run.months_json,
        seed=base.run.seed,
        generator_version=draft.generator_version,
        allocator_version=draft.allocator_version or base.run.allocator_version,
        outcome=OUTCOME_BY_STATUS[draft.status],
        is_draft=draft.is_allocation_complete,
        master_version=_master_version(connection),
        request_schema_version=base.run.request_schema_version,
        request_snapshot_json=(
            base.run.request_snapshot_json
            if request is None
            else _json(encode_request(request))
        ),
        readiness_json=base.run.readiness_json,
        missing_content_json=base.run.missing_content_json,
    )

    with generation_store.transaction(connection):
        generation_store.insert_run(
            connection,
            record,
            months=generation_store_module()._month_records(draft),
            rows=generation_store_module()._row_records(draft),
            visits=generation_store_module()._visit_records(draft),
            exceptions=generation_store_module()._exception_records(draft),
            entries=tuple(entries),
            register_id=register_id,
            parent_run_id=base_run_id,
            kind=kind,
            change_reason=change_reason,
        )
        # A candidate is stored but not accepted, so nothing about it is carried
        # forward yet: it starts entirely pending and carries no locks. Only the
        # operator's decision moves the register onto it.
        adopt_revision(
            connection,
            new_id,
            base_run_id=None if kind == "candidate" else base_run_id,
        )
        if kind != "candidate":
            revision_state.set_current(connection, register_id, new_id)
    return new_id


def generation_store_module() -> Any:
    """The application-side record builders, imported lazily.

    They live in ``generation_runs`` beside the rest of the draft mapping; importing
    that module at import time would close a cycle, because it imports this one for
    nothing but the edit store.
    """

    from . import generation_runs

    return generation_runs


def _master_version(connection: sqlite3.Connection) -> int:
    from ..infrastructure.sqlite import masters

    return masters.current_version(connection)


# --- the one place a correction is applied ---------------------------------


def _revise(
    connection: sqlite3.Connection,
    run_id: str,
    transform: Callable[[DraftRegister], tuple[DraftRegister, dict[str, Any]]],
    *,
    action: str,
    reason: str | None,
) -> EditResult:
    require_current(connection, run_id)
    base = load_register(connection, run_id)
    draft, change = transform(base)
    if draft == base:
        raise RevisionError(
            "correction_changes_nothing",
            "the correction would leave the register exactly as it was",
        )
    new_id = save_revision(
        connection, draft, base_run_id=run_id, change_reason=reason or action
    )
    previous = revision_state.changes(connection, run_id)
    revision_state.copy_lineage(connection, run_id, new_id)
    revision_state.record_change(
        connection,
        new_id,
        ordinal=(previous[-1].ordinal + 1) if previous else 1,
        action=action,
        row_date=change.get("date"),
        entry_id=change.get("entry_id"),
        asset_id=change.get("asset_id"),
        detail_json=_json(change),
        created_at=_now(),
    )
    row = revision_state.revision(connection, new_id)
    return EditResult(
        run_id=new_id,
        revision_number=0 if row is None else row.revision_number,
        parent_run_id=run_id,
        action=action,
    )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _require_date(draft: DraftRegister, day: date) -> None:
    if day not in {row.day for row in draft.rows}:
        raise RevisionError(
            C_DATE_NOT_IN_REVISION, f"{day.isoformat()} is not on this revision"
        )


def _require_unlocked(
    connection: sqlite3.Connection, run_id: str, *days: date
) -> None:
    locked = revision_state.locks(connection, run_id)
    for day in days:
        if day.isoformat() in locked:
            raise RevisionError(
                C_DATE_LOCKED,
                f"{day.isoformat()} is locked; unlock it before correcting that date",
            )


def _find_entry(draft: DraftRegister, entry_id: str) -> tuple[DraftDayRow, DayEntry]:
    for row in draft.rows:
        for entry in row.entries:
            if entry.entry_id == entry_id:
                return row, entry
    raise RevisionError(
        C_ENTRY_UNKNOWN, f"no entry {entry_id!r} on this revision"
    )


def _replace_row(draft: DraftRegister, row: DraftDayRow) -> DraftRegister:
    rows = tuple(row if item.day == row.day else item for item in draft.rows)
    return rebuilt(draft, rows)


def rebuilt(draft: DraftRegister, rows: tuple[DraftDayRow, ...]) -> DraftRegister:
    """Rebuild the flattened visit list and the months from the corrected rows.

    The rows are the register; the flat visit list and each month's copy are derived
    from them. Leaving a corrected row and a stale derived list beside each other is
    how a register says one thing on screen and another in storage.
    """

    visits: list[GeneratedVisit] = []
    months: list[DraftMonth] = []
    for row in rows:
        visits.extend(row.fixed_visits)
        visits.extend(row.allocated_visits)
    for month in draft.months:
        months.append(
            dataclasses.replace(
                month,
                visits=tuple(
                    item
                    for item in visits
                    if (item.visit.visit_date.year, item.visit.visit_date.month)
                    == (month.month.year, month.month.month)
                ),
            )
        )
    return dataclasses.replace(draft, rows=rows, visits=tuple(visits), months=tuple(months))


# --- entries -----------------------------------------------------------------


def edit_entry(
    connection: sqlite3.Connection,
    run_id: str,
    entry_id: str,
    *,
    title_en: str | None = None,
    title_kn: str | None = None,
    kind: str | None = None,
    reason: str | None = None,
) -> EditResult:
    """Correct an entry's wording or its kind, on the revision's own entry id."""

    def transform(draft: DraftRegister) -> tuple[DraftRegister, dict[str, Any]]:
        row, entry = _find_entry(draft, entry_id)
        _require_unlocked(connection, run_id, row.day)
        if title_en is not None and not title_en.strip():
            raise RevisionError(
                C_ENTRY_TEXT_EMPTY, "an entry needs an English description"
            )
        new_kind = entry.kind
        if kind is not None:
            try:
                new_kind = DayEntryKind(kind)
            except ValueError as error:
                raise RevisionError(
                    C_ENTRY_KIND_UNKNOWN, f"{kind!r} is not an entry kind"
                ) from error
        corrected = dataclasses.replace(
            entry,
            title_en=entry.title_en if title_en is None else title_en,
            title_kn=entry.title_kn if title_kn is None else title_kn,
            kind=new_kind,
            provenance=DayEntryProvenance.MANUAL,
        )
        entries = tuple(
            corrected if item.entry_id == entry_id else item for item in row.entries
        )
        change = {
            "date": row.day.isoformat(),
            "entry_id": entry_id,
            "before": {
                "title_en": entry.title_en,
                "title_kn": entry.title_kn,
                "kind": entry.kind.value,
            },
            "after": {
                "title_en": corrected.title_en,
                "title_kn": corrected.title_kn,
                "kind": corrected.kind.value,
            },
        }
        return _replace_row(draft, dataclasses.replace(row, entries=entries)), change

    return _revise(connection, run_id, transform, action="edit_entry", reason=reason)


def add_entry(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    day: date,
    kind: str,
    title_en: str,
    title_kn: str | None = None,
    reason: str | None = None,
) -> EditResult:
    """Add an entry of the operator's own to a date, with both languages kept apart."""

    def transform(draft: DraftRegister) -> tuple[DraftRegister, dict[str, Any]]:
        _require_date(draft, day)
        _require_unlocked(connection, run_id, day)
        if not title_en.strip():
            raise RevisionError(
                C_ENTRY_TEXT_EMPTY, "an entry needs an English description"
            )
        try:
            entry_kind = DayEntryKind(kind)
        except ValueError as error:
            raise RevisionError(
                C_ENTRY_KIND_UNKNOWN, f"{kind!r} is not an entry kind"
            ) from error
        if entry_kind is DayEntryKind.LEAVE:
            # Leave is a stored leave record, not a sentence somebody typed. A manual
            # leave entry would claim approved absence the leave master does not hold.
            raise RevisionError(
                C_MANUAL_LEAVE_UNSUPPORTED,
                "leave is recorded from the leave master, not added as an entry",
            )
        entry = DayEntry(
            entry_id="E-" + uuid.uuid4().hex[:10],
            day=day,
            kind=entry_kind,
            title_en=title_en,
            title_kn=title_kn,
            provenance=DayEntryProvenance.MANUAL,
        )
        row = next(item for item in draft.rows if item.day == day)
        new_row = dataclasses.replace(row, entries=tuple(row.entries) + (entry,))
        change = {
            "date": day.isoformat(),
            "entry_id": entry.entry_id,
            "after": {
                "kind": entry.kind.value,
                "title_en": entry.title_en,
                "title_kn": entry.title_kn,
            },
        }
        return _replace_row(draft, new_row), change

    return _revise(connection, run_id, transform, action="add_entry", reason=reason)


def remove_entry(
    connection: sqlite3.Connection,
    run_id: str,
    entry_id: str,
    *,
    reason: str | None = None,
) -> EditResult:
    """Remove an entry. A projected one returns on the next regeneration, by design."""

    def transform(draft: DraftRegister) -> tuple[DraftRegister, dict[str, Any]]:
        row, entry = _find_entry(draft, entry_id)
        _require_unlocked(connection, run_id, row.day)
        entries = tuple(item for item in row.entries if item.entry_id != entry_id)
        change = {
            "date": row.day.isoformat(),
            "entry_id": entry_id,
            "before": {
                "kind": entry.kind.value,
                "title_en": entry.title_en,
                "title_kn": entry.title_kn,
                "provenance": str(entry.provenance),
            },
        }
        return _replace_row(draft, dataclasses.replace(row, entries=entries)), change

    return _revise(connection, run_id, transform, action="remove_entry", reason=reason)


def move_entry(
    connection: sqlite3.Connection,
    run_id: str,
    entry_id: str,
    *,
    to_day: date,
    reason: str | None = None,
) -> EditResult:
    """Move an entry to another date of the same revision."""

    def transform(draft: DraftRegister) -> tuple[DraftRegister, dict[str, Any]]:
        from_day, entry = _find_entry(draft, entry_id)
        _require_date(draft, to_day)
        _require_unlocked(connection, run_id, from_day.day, to_day)
        moved = dataclasses.replace(
            entry, day=to_day, provenance=DayEntryProvenance.MANUAL
        )
        rows = []
        for row in draft.rows:
            if row.day == from_day.day:
                row = dataclasses.replace(
                    row,
                    entries=tuple(
                        item for item in row.entries if item.entry_id != entry_id
                    ),
                )
            if row.day == to_day:
                row = dataclasses.replace(row, entries=tuple(row.entries) + (moved,))
            rows.append(row)
        change = {
            "date": to_day.isoformat(),
            "from_date": from_day.day.isoformat(),
            "entry_id": entry_id,
        }
        return rebuilt(draft, tuple(rows)), change

    return _revise(connection, run_id, transform, action="move_entry", reason=reason)


# --- visits ------------------------------------------------------------------


def move_visit(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    asset_id: str,
    from_day: date,
    to_day: date,
    reason: str | None = None,
) -> EditResult:
    """Correct the date of a field visit, judged by the validator and nothing else.

    The move must stay inside one calendar month: the validator assesses a month at a
    time by contract, so a cross-month move would change two months' obligations with
    neither month assessed whole.
    """

    if (from_day.year, from_day.month) != (to_day.year, to_day.month):
        raise RevisionError(
            C_VISIT_MOVE_CROSSES_MONTH,
            "a visit is moved within its calendar month; correcting a month boundary "
            "needs a regeneration",
        )

    def transform(draft: DraftRegister) -> tuple[DraftRegister, dict[str, Any]]:
        _require_date(draft, to_day)
        _require_unlocked(connection, run_id, from_day, to_day)
        source = next(item for item in draft.rows if item.day == from_day)
        held = [
            item
            for item in source.fixed_visits + source.allocated_visits
            if item.visit.asset_id == asset_id
        ]
        if not held:
            raise RevisionError(
                C_VISIT_UNKNOWN,
                f"{asset_id} has no visit on {from_day.isoformat()}",
            )
        moved = held[0]

        rows = []
        for row in draft.rows:
            fixed = list(row.fixed_visits)
            allocated = list(row.allocated_visits)
            if row.day == from_day:
                fixed = [
                    item
                    for item in fixed
                    if not (
                        item.visit.asset_id == asset_id
                        and item.visit.visit_date == from_day
                    )
                ]
                allocated = [
                    item
                    for item in allocated
                    if not (
                        item.visit.asset_id == asset_id
                        and item.visit.visit_date == from_day
                    )
                ]
            if row.day == to_day:
                replacement = GeneratedVisit(
                    visit=Visit(asset_id=asset_id, visit_date=to_day),
                    provenance=moved.provenance,
                    month=moved.month,
                    name_en=moved.name_en,
                    name_kn=moved.name_kn,
                )
                if moved.provenance is VisitProvenance.FIXED:
                    fixed = fixed + [replacement]
                else:
                    allocated = allocated + [replacement]
            rows.append(
                dataclasses.replace(
                    row,
                    fixed_visits=tuple(fixed),
                    allocated_visits=tuple(allocated),
                )
            )

        candidate = rebuilt(draft, tuple(rows))
        _assert_month_still_valid(connection, run_id, candidate, from_day)
        change = {
            "date": to_day.isoformat(),
            "from_date": from_day.isoformat(),
            "asset_id": asset_id,
        }
        return candidate, change

    return _revise(connection, run_id, transform, action="move_visit", reason=reason)


def _assert_month_still_valid(
    connection: sqlite3.Connection, run_id: str, draft: DraftRegister, day: date
) -> None:
    """Judge the corrected month with the validator the allocator uses.

    The month's own obligations are the ones already stored on it, so this asks a
    single question: is the corrected schedule still a valid schedule for the work this
    month owes? It does not re-derive an obligation and does not relax one.
    """

    from .snapshot import decode_request

    stored = generation_store.read_run(connection, run_id)
    request = decode_request(_loads(stored.run.request_snapshot_json))
    month = next(
        item
        for item in draft.months
        if (item.month.year, item.month.month) == (day.year, day.month)
    )

    working = frozenset(
        item.day for item in request.calendar_days if item.is_working_day
    )
    proposed = tuple(
        Visit(asset_id=item.visit.asset_id, visit_date=item.visit.visit_date)
        for row in draft.rows
        if (row.day.year, row.day.month) == (day.year, day.month)
        for item in row.fixed_visits + row.allocated_visits
    )
    context = ProposalContext(
        period=month.period,
        calendar=Calendar(working),
        roster=request.roster,
        config=request.config,
        proposed=proposed,
        history=request.prior_history,
        fixed=(),
        gap_blockers=(),
        required_visits={},
        cycle_obligations=dict(month.obligations),
    )
    result = validate_schedule(context, ValidationMode.OBLIGATIONS)
    if not result.valid:
        detail = "; ".join(item.message for item in result.violations[:3])
        raise RevisionError(
            C_VISIT_EDIT_REJECTED,
            detail or "the corrected date fails the register's own constraints",
            violations=tuple(result.violations),
        )


def _loads(raw: str) -> Any:
    import json

    return json.loads(raw)
