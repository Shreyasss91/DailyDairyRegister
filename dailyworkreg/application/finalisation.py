"""Closing one month of a register, and the record that results.

A register is worked on for weeks. Finalising a month is the act that closes it, and the
month that follows stays open: a November section can be final while December is still
being corrected, because the office does not wait for the year to end.

Three rules carry the design.

*   **Finalisation is monthly, and a month is closed by its own record.** The pointer
    lives per ``(register, month)``, so a neighbouring month's edit or regeneration
    cannot make a finalised month mutable, and closing November cannot finalise
    December.
*   **A finalised record is never reopened.** It is written once and protected below the
    application by triggers. A mistake is corrected by a *new* revision that supersedes
    it, and the superseded record stays exactly as it was approved.
*   **The decision to allow it is made here, not by a route or a page.** The interface
    asks why a month cannot be finalised and is given structured blockers; it never
    decides eligibility, and it never parses a sentence to find out what is wrong.

Every gate answers a question an operator can act on: is the month's allocation complete
and feasible, has every date — including an empty Sunday — been read against the content
still in front of them, are the masters the diary depends on present *now*, are the
calendar's conflicts acknowledged, and is the over-coverage the allocator reported
acknowledged for this exact revision. A gate that cannot be evaluated names the
dependency it is missing rather than passing silently.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..infrastructure.sqlite import generation_store, revision_state
from . import readiness as readiness_module
from . import revisions

C_FINALISATION_UNKNOWN = "finalisation_unknown"
C_MONTH_NOT_IN_REVISION = "month_is_not_in_this_revision"
C_REVISION_NOT_CURRENT = "revision_is_not_current"
C_ALLOCATION_INCOMPLETE = "month_has_no_complete_allocation"
C_ALLOCATION_INFEASIBLE = "month_allocation_infeasible"
C_ALLOCATION_INDETERMINATE = "month_allocation_unresolved"
C_DATES_NOT_REVIEWED = "dates_not_reviewed"
C_REVIEW_STALE = "review_content_changed_since_it_was_shown"
C_OPERATIONAL_MASTERS_MISSING = "operational_masters_missing"
C_CALENDAR_CHANGED = "calendar_changed_since_generation"
C_CONFLICT_NOT_ACKNOWLEDGED = "calendar_conflict_not_acknowledged"
C_OVER_COVERAGE_NOT_ACKNOWLEDGED = "over_coverage_not_acknowledged"
C_ACKNOWLEDGEMENT_UNKNOWN = "acknowledgement_names_no_finding"
C_PARTIAL_MONTH_UNSUPPORTED = "partial_month_finalisation_unsupported"
C_ALREADY_FINALISED = "month_already_finalised"
C_REVISION_FINALISED = "revision_is_finalised_and_immutable"
C_MONTH_FINALISED = "month_is_finalised_and_immutable"
C_CONTENT_CHANGED_WHILE_FINALISING = "revision_changed_while_finalising"
C_STALE_FINALISATION = "revision_is_no_longer_current"
C_FINALISED_MONTHS_PRESENT = "register_has_finalised_months"
C_FINALISED_MONTH_CONTENT_CHANGED = "finalised_month_content_would_change"

#: Exception kinds that stop a month being closed. Over-coverage is not one of them: it
#: is acknowledged instead, because an over-covered commitment is a fact to record, not a
#: fault to repair.
BLOCKING_EXCEPTION_KINDS = ("infeasible", "unresolved")


@dataclass(frozen=True)
class Blocker:
    """One reason a month cannot be finalised, in the operator's terms.

    ``code`` is stable so a caller can act without reading ``detail``. ``dates`` names
    the dates concerned when the reason is about specific dates, and is empty when it is
    about the month or the office's masters as a whole.
    """

    code: str
    label: str
    detail: str
    dates: tuple[str, ...] = ()
    asset_id: str | None = None
    limit: int | None = None
    actual: int | None = None
    #: For an acknowledgement gate: the token the operator must acknowledge.
    token: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "detail": self.detail,
            "dates": list(self.dates),
            "asset_id": self.asset_id,
            "limit": self.limit,
            "actual": self.actual,
            "token": self.token,
        }


@dataclass(frozen=True)
class FinalisationCheck:
    """Why a month can or cannot be closed, as of right now."""

    run_id: str
    register_id: str
    month: str
    eligible: bool
    blockers: tuple[Blocker, ...] = ()
    reviewed_dates: int = 0
    total_dates: int = 0
    locked_dates: int = 0
    is_current: bool = False
    allocation_status: str = ""
    is_operational_diary: bool = False
    missing_content: tuple[str, ...] = ()
    over_coverage: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()
    acknowledged: tuple[str, ...] = ()
    finalisation_id: str | None = None
    fingerprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "register_id": self.register_id,
            "month": self.month,
            "eligible": self.eligible,
            "is_current": self.is_current,
            "allocation_status": self.allocation_status,
            "is_operational_diary": self.is_operational_diary,
            "missing_content": list(self.missing_content),
            "reviewed_dates": self.reviewed_dates,
            "total_dates": self.total_dates,
            "locked_dates": self.locked_dates,
            "blockers": [item.as_dict() for item in self.blockers],
            "over_coverage": list(self.over_coverage),
            "conflicts": list(self.conflicts),
            "acknowledged": list(self.acknowledged),
            "finalisation_id": self.finalisation_id,
            "fingerprint": self.fingerprint,
        }


class FinalisationError(ValueError):
    """A finalisation that cannot be carried out, with every reason attached."""

    def __init__(
        self,
        constraint: str,
        detail: str,
        blockers: tuple[Blocker, ...] = (),
    ):
        super().__init__(f"{constraint}: {detail}")
        self.constraint = constraint
        self.detail = detail
        self.blockers = blockers


# --- what the record holds --------------------------------------------------


def month_record(
    connection: sqlite3.Connection, run_id: str, month: str
) -> generation_store.MonthRecord | None:
    for item in generation_store.read_run(connection, run_id).months:
        if item.month_key == month:
            return item
    return None


def month_content(connection: sqlite3.Connection, run_id: str, month: str) -> dict[str, Any]:
    """Everything about one month that a finalised record has to keep.

    Visits, entries and warnings come from the stored rows through the same canonical
    form a review fingerprint uses, so "this month did not change" is decided by the same
    comparison a carried review uses. The month's status, obligations and evidence are
    included because a finalised record that lost them could not explain what it closed.
    """

    content = revisions.stored_row_content(connection, run_id)
    rows = {day: content[day] for day in sorted(content) if day.startswith(month)}
    record = month_record(connection, run_id, month)
    locks = sorted(
        day for day in revision_state.locks(connection, run_id) if day.startswith(month)
    )
    return {
        "month": month,
        "rows": rows,
        "locked_dates": locks,
        "status": "" if record is None else record.status,
        "allocator_status": "" if record is None else record.allocator_status,
        "obligations": {} if record is None else json.loads(record.obligations_json),
        "declared_obligations": (
            {} if record is None else json.loads(record.declared_obligations_json)
        ),
        "evidence": [] if record is None else json.loads(record.evidence_json),
    }


def month_fingerprint(connection: sqlite3.Connection, run_id: str, month: str) -> str:
    return revisions.fingerprint(month_content(connection, run_id, month))


def finalised_months(
    connection: sqlite3.Connection, register_id: str
) -> dict[str, str]:
    """Each finalised month of a register, with the content it was closed at."""

    latest: dict[str, str] = {}
    for row in revision_state.month_states(connection, register_id):
        if row.latest_finalisation_id is None:
            continue
        record = revision_state.finalisation(connection, row.latest_finalisation_id)
        if record is not None:
            latest[record.month] = record.fingerprint
    return latest


def is_finalised(connection: sqlite3.Connection, run_id: str) -> bool:
    return revision_state.finalisation_for_run(connection, run_id) is not None


def finalised_months_of_run(
    connection: sqlite3.Connection, run_id: str
) -> tuple[str, ...]:
    """Which months of this revision have been closed."""

    record = revision_state.finalisation_for_run(connection, run_id)
    if record is None:
        return ()
    return (record.month,)


def assert_month_not_finalised(
    connection: sqlite3.Connection, run_id: str, month: str
) -> None:
    """Refuse a change to a month this revision has already closed.

    Scoped to the month on purpose: a revision may have closed November and still be
    worked on in December, and refusing the whole revision would make correcting a
    neighbouring month impossible.
    """

    record = revision_state.finalisation_for_run(connection, run_id)
    if record is None or record.month != month:
        return
    raise FinalisationError(
        C_MONTH_FINALISED,
        (
            f"{month} of this revision was finalised on {record.finalised_at[:10]} and "
            "cannot be changed; create a correction revision for that month instead"
        ),
    )


def assert_not_finalised(connection: sqlite3.Connection, run_id: str) -> None:
    """Refuse to mutate a revision that has been finalised.

    Called by every path that can change a revision's content, review state or locks.
    The database refuses it too, but a route that reaches this refusal gets a sentence an
    operator can act on instead of an integrity error.
    """

    record = revision_state.finalisation_for_run(connection, run_id)
    if record is None:
        return
    raise FinalisationError(
        C_REVISION_FINALISED,
        (
            f"this revision was finalised on {record.finalised_at[:10]} and cannot be "
            "changed; a correction is a new revision created from that finalised record"
        ),
    )


def assert_no_finalised_months(
    connection: sqlite3.Connection, register_id: str, *, action: str
) -> None:
    """Refuse an operation that could rearrange a month that is already closed.

    Regeneration and candidate acceptance both replace the register's arrangement
    wholesale. A register that has closed a month cannot be rearranged wholesale without
    risking that month, so both are refused with the reason and the way forward: correct
    the open month by editing it, or create a correction revision for the closed one.
    """

    months = finalised_months(connection, register_id)
    if not months:
        return
    raise FinalisationError(
        C_FINALISED_MONTHS_PRESENT,
        (
            f"{action} would rearrange a register that has already closed "
            f"{', '.join(sorted(months))}; correct an open month by editing it, or "
            "create a correction revision for a closed one"
        ),
    )


def assert_finalised_months_preserved(
    connection: sqlite3.Connection, run_id: str
) -> None:
    """Refuse a revision whose copy of a closed month differs from the record."""

    register_id = revisions.register_of(connection, run_id)
    for month, recorded in finalised_months(connection, register_id).items():
        if month_fingerprint(connection, run_id, month) != recorded:
            raise FinalisationError(
                C_FINALISED_MONTH_CONTENT_CHANGED,
                (
                    f"{month} is finalised and this revision would change it; a closed "
                    "month only changes through a correction revision"
                ),
            )


def finalised_month_of(
    connection: sqlite3.Connection, register_id: str, month: str
) -> dict[str, Any] | None:
    state = revision_state.month_state(connection, register_id, month)
    if state is None or state.latest_finalisation_id is None:
        return None
    record = revision_state.finalisation(connection, state.latest_finalisation_id)
    return None if record is None else finalisation_payload(connection, record)


# --- the gates --------------------------------------------------------------


def _token(*parts: Any) -> str:
    return "|".join("" if part is None else str(part) for part in parts)


def _over_coverage(
    connection: sqlite3.Connection, run_id: str, month: str
) -> tuple[dict[str, Any], ...]:
    """The over-coverage the draft recorded for this month.

    Read from the stored exceptions, not recomputed: an over-covered commitment is
    something the allocator concluded when it reconciled the cycle, and re-deriving it
    here would be a second opinion about the same arithmetic.
    """

    stored = generation_store.read_run(connection, run_id)
    found: list[dict[str, Any]] = []
    for item in stored.exceptions:
        if item.kind != "over_covered":
            continue
        if item.month_key is not None and item.month_key != month:
            continue
        found.append(
            {
                "constraint": item.constraint_name,
                "detail": item.detail,
                "asset_id": item.asset_id,
                "limit": item.limit_value,
                "actual": item.actual_value,
                "visit_date": item.visit_date,
                "token": _token(
                    "over_covered", item.constraint_name, item.asset_id, item.visit_date
                ),
            }
        )
    return tuple(found)


def _calendar_conflicts(
    connection: sqlite3.Connection, run_id: str
) -> tuple[dict[str, Any], ...]:
    """Conflicts the effective calendar still reports for this month.

    Re-projected from the masters in force now, because a conflict is a fact about the
    calendar rather than about the draft: a holiday recorded after generation is exactly
    the kind of thing that must not be silently ignored at closing time.
    """

    from .calendar_projection import load_weekly_off_rules, project_calendar

    stored = generation_store.read_run(connection, run_id)
    start = date.fromisoformat(stored.run.start_date)
    end = date.fromisoformat(stored.run.end_date)
    projection = project_calendar(
        connection, start, end, rules=load_weekly_off_rules(connection)
    )
    return tuple(
        {
            "detail": text,
            "token": _token("calendar_conflict", text),
        }
        for text in projection.conflicts
    )


def _working_days_changed(
    connection: sqlite3.Connection, run_id: str
) -> tuple[str, ...]:
    """Dates whose working-day status differs from the stored register's.

    A finalised month must describe the calendar it was built on. If the masters moved
    underneath it the register is stale, and closing it would freeze a description the
    office no longer believes.
    """

    from .calendar_projection import load_weekly_off_rules, project_calendar

    stored = generation_store.read_run(connection, run_id)
    start = date.fromisoformat(stored.run.start_date)
    end = date.fromisoformat(stored.run.end_date)
    projection = project_calendar(
        connection, start, end, rules=load_weekly_off_rules(connection)
    )
    now = {item.day.isoformat(): bool(item.is_working_day) for item in projection.days}
    changed = [
        row.row_date
        for row in stored.rows
        if row.row_date in now and bool(row.is_working_day) != now[row.row_date]
    ]
    return tuple(sorted(changed))


def check_finalisation(
    connection: sqlite3.Connection,
    run_id: str,
    month: str,
    *,
    acknowledged: tuple[str, ...] = (),
) -> FinalisationCheck:
    """Decide whether one month of one revision may be closed, and say why not."""

    register_id = revisions.register_of(connection, run_id)
    stored = generation_store.read_run(connection, run_id)
    record = month_record(connection, run_id, month)
    if record is None:
        raise FinalisationError(
            C_MONTH_NOT_IN_REVISION,
            f"{month} is not a month of revision {run_id}; it covers "
            f"{stored.run.start_date} to {stored.run.end_date}",
        )

    blockers: list[Blocker] = []
    current = revisions.current_run(connection, register_id)
    is_current = current == run_id
    if not is_current:
        blockers.append(
            Blocker(
                C_REVISION_NOT_CURRENT,
                "a newer revision is in force",
                (
                    "this revision is not the register's current revision, so closing "
                    "it would finalise content nobody is working from; open the current "
                    "revision instead"
                ),
            )
        )

    existing = revision_state.finalisation_for_run(connection, run_id)
    if existing is not None:
        blockers.append(
            Blocker(
                C_ALREADY_FINALISED,
                "this revision is already finalised",
                (
                    f"{month} was finalised from this revision on "
                    f"{existing.finalised_at[:10]} and the record cannot be re-made"
                ),
            )
        )

    # Allocation.
    if record.status != "allocation_complete":
        blockers.append(
            Blocker(
                C_ALLOCATION_INCOMPLETE,
                "the month's field visits were not completely allocated",
                (
                    "only a month whose allocation completed can be finalised; "
                    f"this one is {record.status}"
                ),
            )
        )
    if record.allocator_status == "INFEASIBLE":
        blockers.append(
            Blocker(
                C_ALLOCATION_INFEASIBLE,
                "the month cannot be covered",
                (
                    "the allocator proved this month's obligations cannot be "
                    "scheduled inside the policy limits, so there is nothing to close"
                ),
            )
        )
    elif record.allocator_status == "INDETERMINATE":
        blockers.append(
            Blocker(
                C_ALLOCATION_INDETERMINATE,
                "the month was not settled",
                (
                    "the search ended without proving either way, so the month may be "
                    "coverable but has not been shown to be; raise the budget or reduce "
                    "the roster and generate again"
                ),
            )
        )

    # Review: every date, including the empty ones, read against what is stored now.
    reviews = revision_state.reviews(connection, run_id)
    fingerprints = revisions.fingerprints(connection, run_id)
    month_days = sorted(day for day in fingerprints if day.startswith(month))
    unreviewed = [
        day
        for day in month_days
        if reviews.get(day) is None or reviews[day].state != revision_state.REVIEWED
    ]
    if unreviewed:
        blockers.append(
            Blocker(
                C_DATES_NOT_REVIEWED,
                f"{len(unreviewed)} date(s) not reviewed",
                (
                    "every date of the month must be read before it is closed, "
                    "including Sundays, weekly offs, holidays and dates with no visits"
                ),
                dates=tuple(unreviewed),
            )
        )
    stale = [
        day
        for day in month_days
        if reviews.get(day) is not None
        and reviews[day].state == revision_state.REVIEWED
        and reviews[day].content_fingerprint != fingerprints[day]
    ]
    if stale:
        blockers.append(
            Blocker(
                C_REVIEW_STALE,
                f"{len(stale)} review(s) no longer match the content",
                (
                    "these dates changed after they were reviewed, so the review "
                    "attests content nobody is looking at; review them again"
                ),
                dates=tuple(stale),
            )
        )

    # Operational masters, measured now rather than trusted from generation time.
    start = date.fromisoformat(month_days[0]) if month_days else date.fromisoformat(
        stored.run.start_date
    )
    end = date.fromisoformat(month_days[-1]) if month_days else start
    status = readiness_module.assess_readiness(connection, start, end)
    for item in status.items:
        if item.satisfied:
            continue
        blockers.append(
            Blocker(
                C_OPERATIONAL_MASTERS_MISSING,
                item.label,
                item.detail,
            )
        )

    # The calendar the register describes.
    changed = tuple(day for day in _working_days_changed(connection, run_id) if day.startswith(month))
    if changed:
        blockers.append(
            Blocker(
                C_CALENDAR_CHANGED,
                "the calendar changed after this register was generated",
                (
                    "these dates are working days in the stored register and not in the "
                    "calendar now in force, or the reverse; generate again so the "
                    "register matches the calendar it will be closed against"
                ),
                dates=changed,
            )
        )

    # Exceptions the allocator reported for the month.
    for item in json.loads(record.evidence_json):
        kind = str(item.get("kind", ""))
        if kind not in BLOCKING_EXCEPTION_KINDS:
            continue
        blockers.append(
            Blocker(
                C_ALLOCATION_INFEASIBLE
                if kind == "infeasible"
                else C_ALLOCATION_INDETERMINATE,
                "the month carries an unresolved finding",
                str(item.get("detail", "")),
                asset_id=item.get("asset_id"),
                limit=item.get("limit"),
                actual=item.get("actual"),
            )
        )

    # Acknowledgements.
    over_coverage = _over_coverage(connection, run_id, month)
    conflicts = _calendar_conflicts(connection, run_id)
    acknowledged_tokens = set(acknowledged)
    known_tokens = {item["token"] for item in over_coverage} | {
        item["token"] for item in conflicts
    }
    unknown = sorted(acknowledged_tokens - known_tokens)
    if unknown:
        blockers.append(
            Blocker(
                C_ACKNOWLEDGEMENT_UNKNOWN,
                "an acknowledgement names nothing in this month",
                (
                    "the acknowledged finding does not exist for this revision, so the "
                    "acknowledgement would attest something nobody saw"
                ),
            )
        )
    unacknowledged_over = [
        item for item in over_coverage if item["token"] not in acknowledged_tokens
    ]
    if unacknowledged_over:
        blockers.append(
            Blocker(
                C_OVER_COVERAGE_NOT_ACKNOWLEDGED,
                f"{len(unacknowledged_over)} over-covered commitment(s) not acknowledged",
                (
                    "over-coverage is recorded rather than removed, and closing a month "
                    "with it needs the operator to acknowledge each finding"
                ),
                dates=tuple(
                    item["visit_date"] for item in unacknowledged_over if item["visit_date"]
                ),
                token=unacknowledged_over[0]["token"],
            )
        )
    unacknowledged_conflicts = [
        item for item in conflicts if item["token"] not in acknowledged_tokens
    ]
    if unacknowledged_conflicts:
        blockers.append(
            Blocker(
                C_CONFLICT_NOT_ACKNOWLEDGED,
                f"{len(unacknowledged_conflicts)} calendar conflict(s) not acknowledged",
                (
                    "; ".join(item["detail"] for item in unacknowledged_conflicts[:3])
                    + (
                        ""
                        if len(unacknowledged_conflicts) <= 3
                        else f"; and {len(unacknowledged_conflicts) - 3} more"
                    )
                ),
                token=unacknowledged_conflicts[0]["token"],
            )
        )

    reviewed = sum(
        1
        for day in month_days
        if reviews.get(day) is not None
        and reviews[day].state == revision_state.REVIEWED
        and reviews[day].content_fingerprint == fingerprints[day]
    )

    return FinalisationCheck(
        run_id=run_id,
        register_id=register_id,
        month=month,
        eligible=not blockers,
        blockers=tuple(blockers),
        reviewed_dates=reviewed,
        total_dates=len(month_days),
        locked_dates=len(
            [day for day in revision_state.locks(connection, run_id) if day.startswith(month)]
        ),
        is_current=is_current,
        allocation_status=record.allocator_status,
        is_operational_diary=not status.missing_content,
        missing_content=status.missing_content,
        over_coverage=over_coverage,
        conflicts=conflicts,
        acknowledged=tuple(sorted(acknowledged_tokens)),
        finalisation_id=None if existing is None else existing.finalisation_id,
        fingerprint=month_fingerprint(connection, run_id, month),
    )
# --- deciding, then closing -------------------------------------------------


def _partial_month(connection: sqlite3.Connection, run_id: str, month: str) -> bool:
    """Whether the revision holds only part of this calendar month.

    Read from the stored rows rather than from the request, so a revision whose range
    covers an incomplete month is refused even if something upstream accepted it.
    """

    content = revisions.stored_row_content(connection, run_id)
    days = sorted(day for day in content if day.startswith(month))
    if not days:
        return True
    first, last = days[0], days[-1]
    year, month_number = int(month[:4]), int(month[5:7])
    start_ok = first == date(year, month_number, 1).isoformat()
    if month_number == 12:
        end_of_month = date(year + 1, 1, 1)
    else:
        end_of_month = date(year, month_number + 1, 1)
    end_ok = last == date.fromordinal(end_of_month.toordinal() - 1).isoformat()
    return not (start_ok and end_ok)


def finalise_month(
    connection: sqlite3.Connection,
    run_id: str,
    month: str,
    *,
    acknowledged: tuple[str, ...] = (),
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Close one month of one revision, or refuse and say why.

    The gates run outside a write transaction, because operational readiness and calendar
    projection read the masters and that is not work to hold a lock across. The write is
    then short and re-checks the two things that would make the decision stale: the month
    content's fingerprint and the register's current revision. If either moved, the
    finalisation is refused and the operator is asked to look again, because a record
    written against content that has since changed is a record of nothing.
    """

    check = check_finalisation(connection, run_id, month, acknowledged=acknowledged)
    if _partial_month(connection, run_id, month):
        raise FinalisationError(
            C_PARTIAL_MONTH_UNSUPPORTED,
            (
                "this revision does not cover the whole calendar month, and a part-month "
                "cannot be closed; partial-month scheduling is not implemented"
            ),
            check.blockers,
        )
    if not check.eligible:
        first = check.blockers[0]
        raise FinalisationError(first.code, first.detail, check.blockers)

    fingerprint = check.fingerprint
    register_id = check.register_id
    revision = revision_state.revision(connection, run_id)
    if revision is None:
        raise FinalisationError(
            revisions.C_REVISION_UNKNOWN, f"unknown revision {run_id}"
        )
    correction_of = revision_state.correction_of(connection, run_id)
    state = revision_state.month_state(connection, register_id, month)
    parent_finalisation_id = (
        None if state is None else state.latest_finalisation_id
    )
    finalisation_id = f"MF-{uuid.uuid4().hex[:12]}"
    stamp = (now or datetime.now()).isoformat()
    content = month_content(connection, run_id, month)
    sequence = revision_state.next_finalisation_sequence(connection, register_id, month)
    status = readiness_module.assess_readiness(
        connection,
        date.fromisoformat(sorted(content["rows"])[0]),
        date.fromisoformat(sorted(content["rows"])[-1]),
    )

    record = revision_state.FinalisationRow(
        finalisation_id=finalisation_id,
        register_id=register_id,
        month=month,
        run_id=run_id,
        revision_number=revision.revision_number,
        sequence=sequence,
        parent_finalisation_id=parent_finalisation_id,
        correction_of_run_id=correction_of,
        finalised_at=stamp,
        fingerprint=fingerprint,
        content_json=json.dumps(content, sort_keys=True, ensure_ascii=False),
        acknowledged_json=json.dumps(
            [
                item
                for item in (
                    *check.over_coverage,
                    *check.conflicts,
                )
                if item["token"] in set(acknowledged)
            ],
            sort_keys=True,
            ensure_ascii=False,
        ),
        readiness_json=json.dumps(
            [
                {
                    "key": item.key,
                    "label": item.label,
                    "satisfied": item.satisfied,
                    "detail": item.detail,
                }
                for item in status.items
            ],
            sort_keys=True,
            ensure_ascii=False,
        ),
        master_version=_master_version(connection),
        note=note,
    )

    with generation_store.transaction(connection):
        again = month_fingerprint(connection, run_id, month)
        if again != fingerprint:
            raise FinalisationError(
                C_CONTENT_CHANGED_WHILE_FINALISING,
                (
                    "the month changed while it was being finalised, so nothing was "
                    "written; reload the register and close it again"
                ),
            )
        if revisions.current_run(connection, register_id) != run_id:
            raise FinalisationError(
                C_STALE_FINALISATION,
                (
                    "the register moved to another revision while this month was being "
                    "finalised, so nothing was written; open the current revision"
                ),
            )
        if revision_state.finalisation_for_run(connection, run_id) is not None:
            raise FinalisationError(
                C_ALREADY_FINALISED,
                "this revision was finalised a moment ago and cannot be finalised again",
            )
        revision_state.insert_finalisation(connection, record)
        revision_state.set_latest_finalisation(
            connection, register_id, month, finalisation_id
        )
        if state is not None and state.open_correction_run_id == run_id:
            revision_state.clear_open_correction(connection, register_id, month)

    return finalisation_payload(connection, record)


def _master_version(connection: sqlite3.Connection) -> int:
    from ..infrastructure.sqlite import masters

    return masters.current_version(connection)


# --- reading the record -----------------------------------------------------


def finalisation_payload(
    connection: sqlite3.Connection, record: revision_state.FinalisationRow
) -> dict[str, Any]:
    """One finalised record, readable exactly as it was written."""

    state = revision_state.month_state(connection, record.register_id, record.month)
    is_latest = (
        state is not None and state.latest_finalisation_id == record.finalisation_id
    )
    return {
        "finalisation_id": record.finalisation_id,
        "register_id": record.register_id,
        "month": record.month,
        "run_id": record.run_id,
        "revision_number": record.revision_number,
        "sequence": record.sequence,
        "parent_finalisation_id": record.parent_finalisation_id,
        "correction_of_run_id": record.correction_of_run_id,
        "finalised_at": record.finalised_at,
        "fingerprint": record.fingerprint,
        "master_version": record.master_version,
        "note": record.note,
        "is_latest": is_latest,
        "acknowledged": json.loads(record.acknowledged_json),
        "readiness": json.loads(record.readiness_json),
        "content": json.loads(record.content_json),
    }


def finalisation_record(
    connection: sqlite3.Connection, finalisation_id: str
) -> dict[str, Any]:
    record = revision_state.finalisation(connection, finalisation_id)
    if record is None:
        raise FinalisationError(
            C_FINALISATION_UNKNOWN, f"unknown finalised record {finalisation_id}"
        )
    return finalisation_payload(connection, record)


def month_finalisations(
    connection: sqlite3.Connection, register_id: str, month: str
) -> list[dict[str, Any]]:
    return [
        finalisation_payload(connection, record)
        for record in revision_state.finalisations(connection, register_id, month)
    ]


def month_lifecycle(
    connection: sqlite3.Connection, register_id: str
) -> list[dict[str, Any]]:
    """Where each month of a register stands, as four separate dimensions.

    Review, lock, allocation and finalisation answer different questions, so they are
    reported separately rather than collapsed into one state that would have to be read
    as whichever is most alarming.
    """

    summary = revisions.register_summary(connection, register_id)
    current = summary["current_run_id"]
    months: list[dict[str, Any]] = []
    if current is None:
        return months

    stored = generation_store.read_run(connection, current)
    reviews = revisions.review_state(connection, current)
    by_month: dict[str, list[dict[str, Any]]] = {}
    for item in reviews["dates"]:
        by_month.setdefault(item["date"][:7], []).append(item)

    for record in sorted(stored.months, key=lambda item: item.month_key):
        label = record.month_key
        days = by_month.get(label, [])
        state = revision_state.month_state(connection, register_id, label)
        latest = (
            None
            if state is None or state.latest_finalisation_id is None
            else revision_state.finalisation(connection, state.latest_finalisation_id)
        )
        open_correction = None if state is None else state.open_correction_run_id
        correction_revision = (
            None
            if open_correction is None
            else revision_state.revision(connection, open_correction)
        )
        months.append(
            {
                "month": label,
                "reviewed_dates": sum(
                    1 for item in days if item["state"] == "reviewed"
                ),
                "total_dates": len(days),
                "locked_dates": sum(1 for item in days if item["locked"]),
                "allocator_status": record.allocator_status,
                "allocation_complete": record.status == "allocation_complete",
                "finalised": latest is not None,
                "latest_finalisation_id": (
                    None if latest is None else latest.finalisation_id
                ),
                "latest_finalisation_revision": (
                    None if latest is None else latest.revision_number
                ),
                "finalised_at": None if latest is None else latest.finalised_at,
                "open_correction_run_id": open_correction,
                "open_correction_revision": (
                    None if correction_revision is None else correction_revision.revision_number
                ),
                "lifecycle": _lifecycle_label(
                    record.status,
                    days,
                    latest is not None,
                    open_correction is not None,
                ),
            }
        )
    return months


def _lifecycle_label(
    status: str,
    days: list[dict[str, Any]],
    finalised: bool,
    correction_open: bool,
) -> str:
    """One word for the page, and one only, in the order a month passes through.

    A correction draft is *not* finalised: the pointer still names the record it will
    supersede, and calling the open branch final would tell an operator their correction
    is already in force when it is not.
    """

    if correction_open:
        return "correction_draft"
    if finalised:
        return "finalised"
    if status != "allocation_complete":
        return "draft"
    if days and all(item["state"] == "reviewed" for item in days):
        return "reviewed"
    return "draft"


def month_breakdown(connection: sqlite3.Connection, run_id: str) -> list[str]:
    """The month labels of a revision, in order."""

    return [item.month_key for item in generation_store.read_run(connection, run_id).months]
