"""Regenerating the unlocked part of a register, comparing it, and accepting it.

Regeneration is a proposal, never a replacement. It produces a *candidate* revision
that is stored, readable and comparable, and the register keeps pointing at the
revision the office already has until somebody accepts the candidate explicitly. A
failed regeneration leaves the accepted revision exactly where it was.

The candidate is produced by the same generator and the same allocator the first draft
came from; this module decides nothing about dates. What it does decide is what the
generator is given:

*   locked dates are passed as committed in-period visits, so the allocator treats them
    as work that is already done and cannot move them;
*   an operator's own entry survives, because it is not something the calendar can
    re-derive;
*   everything else is left to the calendar and the allocator, which is the whole point
    of regenerating.

A commitment is supplied once and only once: as the generator's committed visit for its
month. It is never also given as prior history or as a gap blocker, because the
generator derives those from the same committed set and being handed a fact twice is
how a visit gets counted twice.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from ..infrastructure.sqlite import generation_store, revision_state
from .draft_generation import (
    DayEntry,
    DayEntryKind,
    DayEntryProvenance,
    DraftRegister,
    DraftStatus,
    VisitProvenance,
    generate_draft,
)
from .draft_requests import GenerationInputError
from .generation_runs import load_register
from .register_edits import rebuilt, save_revision
from .revisions import (
    C_LOCKED_CONTENT_CHANGED,
    RevisionError,
    current_run,
    fingerprints,
    register_of,
    require_current,
    stored_row_content,
)
from .snapshot import decode_request

C_CANDIDATE_NOT_FOUND = "candidate_not_found"
C_CANDIDATE_ALREADY_DECIDED = "candidate_already_decided"
C_CANDIDATE_IS_NOT_A_CANDIDATE = "revision_is_not_a_candidate"
C_REGENERATION_INFEASIBLE = "regeneration_produced_no_draft"

#: The statuses a comparison can report for one date.
UNCHANGED = "unchanged"
ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"
VISITS_MOVED = "visits_moved"
LOCKED_PRESERVED = "locked_preserved"


@dataclass(frozen=True)
class RegenerationOutcome:
    base_run_id: str
    candidate_run_id: str
    status: str
    seed: int
    eligible: bool
    blocked_by: str | None = None
    detail: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def regenerate_candidate(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    seed: int | None = None,
    reason: str | None = None,
) -> RegenerationOutcome:
    """Generate a candidate revision from the accepted one, leaving it accepted.

    Every failure path returns or raises without touching the register's current
    revision: an infeasible or unsettled regeneration is an attempt with evidence, not a
    draft, and it is stored as such.
    """

    require_current(connection, run_id)
    from . import finalisation

    # A register that has closed any month cannot be rearranged wholesale: a candidate
    # could move that month's work. The closed month changes only by a correction.
    finalisation.assert_no_finalised_months(
        connection, register_of(connection, run_id), action="regeneration"
    )
    base = load_register(connection, run_id)
    stored = generation_store.read_run(connection, run_id)
    request = decode_request(_loads(stored.run.request_snapshot_json))
    locked = revision_state.locks(connection, run_id)

    committed = _committed_by_month(request, base, locked)
    entries = _entries_to_keep(base, locked)
    candidate_request = dataclasses.replace(
        request,
        seed=request.seed if seed is None else seed,
        fixed_by_month=committed,
        entries=entries,
    )

    draft = _restore_locked_provenance(generate_draft(candidate_request), base, locked)
    candidate_id = save_revision(
        connection,
        draft,
        base_run_id=run_id,
        change_reason=reason or "regenerated from the accepted revision",
        kind="candidate",
        request=candidate_request,
    )

    if not draft.is_allocation_complete:
        revision_state.record_decision(
            connection,
            candidate_id,
            decision="rejected",
            decided_at=_now(),
            compared_to_run_id=run_id,
            note="regeneration produced no draft",
        )
        return RegenerationOutcome(
            base_run_id=run_id,
            candidate_run_id=candidate_id,
            status=draft.status.value,
            seed=candidate_request.seed,
            eligible=False,
            blocked_by=C_REGENERATION_INFEASIBLE,
            detail=(
                "the allocator did not complete this month; the accepted revision is "
                "unchanged and the attempt is kept with its evidence"
            ),
        )

    if locked:
        blocked = _locked_content_blocker(connection, run_id, candidate_id)
        if blocked is not None:
            revision_state.record_decision(
                connection,
                candidate_id,
                decision="rejected",
                decided_at=_now(),
                compared_to_run_id=run_id,
                note=blocked,
            )
            return RegenerationOutcome(
                base_run_id=run_id,
                candidate_run_id=candidate_id,
                status=draft.status.value,
                seed=candidate_request.seed,
                eligible=False,
                blocked_by=C_LOCKED_CONTENT_CHANGED,
                detail=blocked,
            )

    return RegenerationOutcome(
        base_run_id=run_id,
        candidate_run_id=candidate_id,
        status=draft.status.value,
        seed=candidate_request.seed,
        eligible=True,
    )


def _committed_by_month(
    request: Any, base: DraftRegister, locked: dict[str, Any]
) -> dict[Any, tuple[Any, ...]]:
    """The commitments the generator is given, each one exactly once.

    The revision's original commitments are kept, and every visit on a locked date is
    added as a commitment so the allocator treats it as work already done rather than
    as a date it may fill. They are merged on ``(asset, date)``: supplying the same
    fact twice would let a single visit discharge two obligations.
    """

    grouped: dict[Any, dict[tuple[str, date], Any]] = {}
    for month, visits in request.fixed_by_month.items():
        bucket = grouped.setdefault(month, {})
        for visit in visits:
            bucket[(visit.asset_id, visit.visit_date)] = visit
    for row in base.rows:
        if row.day.isoformat() not in locked:
            continue
        for item in row.fixed_visits + row.allocated_visits:
            bucket = grouped.setdefault(item.month, {})
            bucket.setdefault((item.visit.asset_id, item.visit.visit_date), item.visit)
    return {
        month: tuple(
            visit for _key, visit in sorted(items.items(), key=lambda item: item[0])
        )
        for month, items in grouped.items()
    }


def _entries_to_keep(
    base: DraftRegister, locked: dict[str, Any]
) -> tuple[DayEntry, ...]:
    """Every entry the accepted revision holds, each one once.

    The generator does not re-project a calendar: entries arrive with the request, so a
    revision that dropped them here would lose its safety meetings. A manual entry is
    supplied exactly like a projected one, because reading a request is not the place
    to decide whose work survives — that is what the provenance is recorded for, and
    nothing in this path may erase it.
    """

    kept: list[DayEntry] = []
    seen: set[str] = set()
    for row in base.rows:
        for entry in row.entries:
            if entry.entry_id in seen:
                continue
            seen.add(entry.entry_id)
            kept.append(
                DayEntry(
                    entry_id=entry.entry_id,
                    day=entry.day,
                    kind=DayEntryKind(entry.kind),
                    title_en=entry.title_en,
                    title_kn=entry.title_kn,
                    provenance=DayEntryProvenance(entry.provenance),
                )
            )
    return tuple(kept)


def _restore_locked_provenance(
    draft: DraftRegister, base: DraftRegister, locked: dict[str, Any]
) -> DraftRegister:
    """Keep a locked visit's provenance as the accepted revision recorded it.

    The allocator is given a locked visit as a commitment and returns it as one, which
    is how it is treated and not what the register said. Rewriting ``allocated`` as
    ``fixed`` would make a preserved date look changed and refuse a candidate that in
    fact left it alone, so the recorded provenance is restored: the lock table is where
    "this was locked afterwards" is kept.
    """

    if not locked:
        return draft
    recorded = {
        (item.visit.asset_id, item.visit.visit_date): item.provenance
        for row in base.rows
        for item in row.fixed_visits + row.allocated_visits
    }
    rows = []
    for row in draft.rows:
        if row.day.isoformat() not in locked:
            rows.append(row)
            continue
        fixed: list[Any] = []
        allocated: list[Any] = []
        for item in row.fixed_visits + row.allocated_visits:
            provenance = recorded.get(
                (item.visit.asset_id, item.visit.visit_date), item.provenance
            )
            restored = dataclasses.replace(item, provenance=provenance)
            if provenance is VisitProvenance.FIXED:
                fixed.append(restored)
            else:
                allocated.append(restored)
        rows.append(
            dataclasses.replace(
                row, fixed_visits=tuple(fixed), allocated_visits=tuple(allocated)
            )
        )
    return rebuilt(draft, tuple(rows))


def _locked_content_blocker(
    connection: sqlite3.Connection, base_run_id: str, candidate_run_id: str
) -> str | None:
    locked = revision_state.locks(connection, base_run_id)
    if not locked:
        return None
    base_fingerprints = fingerprints(connection, base_run_id)
    candidate_fingerprints = fingerprints(connection, candidate_run_id)
    changed = [
        label
        for label in sorted(locked)
        if base_fingerprints.get(label) != candidate_fingerprints.get(label)
    ]
    if not changed:
        return None
    return (
        "a date that is locked would change: "
        + ", ".join(changed[:5])
        + ". Unlock it before regenerating, or accept a candidate that leaves it alone."
    )


# --- comparison --------------------------------------------------------------


def _visits_by_day(content: dict[str, dict[str, Any]]) -> dict[str, set[tuple[str, str]]]:
    return {
        label: {(item[0], item[1]) for item in canonical["visits"]}
        for label, canonical in content.items()
    }


def _entries_by_day(content: dict[str, dict[str, Any]]) -> dict[str, dict[str, tuple]]:
    return {
        label: {item[0]: tuple(item) for item in canonical["entries"]}
        for label, canonical in content.items()
    }


def compare_revisions(
    connection: sqlite3.Connection, base_run_id: str, candidate_run_id: str
) -> dict[str, Any]:
    """A typed, date-wise comparison of two revisions of one register.

    The comparison is by identity, never by text: a visit is the same visit when its
    asset id matches, and an entry is the same entry when its entry id matches. Nothing
    here reads a description to decide whether two rows are the same row.
    """

    if register_of(connection, base_run_id) != register_of(connection, candidate_run_id):
        raise RevisionError(
            "revisions_belong_to_different_registers",
            "two revisions of different registers have nothing to compare",
        )

    base_content = stored_row_content(connection, base_run_id)
    candidate_content = stored_row_content(connection, candidate_run_id)
    base_visits = _visits_by_day(base_content)
    candidate_visits = _visits_by_day(candidate_content)
    base_entries = _entries_by_day(base_content)
    candidate_entries = _entries_by_day(candidate_content)
    locks = revision_state.locks(connection, base_run_id)
    reviews = revision_state.reviews(connection, base_run_id)

    removed = {
        (asset, label)
        for label, visits in base_visits.items()
        for asset, _kind in visits
    } - {
        (asset, label)
        for label, visits in candidate_visits.items()
        for asset, _kind in visits
    }
    added = {
        (asset, label)
        for label, visits in candidate_visits.items()
        for asset, _kind in visits
    } - {
        (asset, label)
        for label, visits in base_visits.items()
        for asset, _kind in visits
    }
    moved = _pair_moves(removed, added)

    days: list[dict[str, Any]] = []
    for label in sorted(set(base_content) | set(candidate_content)):
        before = base_content.get(label)
        after = candidate_content.get(label)
        moved_here = [
            {"asset_id": asset, "from": origin, "to": destination}
            for asset, origin, destination in moved
            if label in {origin, destination}
        ]
        status = _date_status(
            label, before, after, moved_here, label in locks
        )
        days.append(
            {
                "date": label,
                "status": status,
                "locked": label in locks,
                "review_state": (
                    "none"
                    if label not in reviews
                    else reviews[label].state
                ),
                "base": before,
                "proposed": after,
                "visits": {
                    "added": sorted(
                        asset for asset, day in added if day == label
                    ),
                    "removed": sorted(
                        asset for asset, day in removed if day == label
                    ),
                    "moved": moved_here,
                },
                "entries": _entry_difference(
                    base_entries.get(label, {}), candidate_entries.get(label, {})
                ),
                "warnings": _warnings(before, after),
            }
        )

    return {
        "base_run_id": base_run_id,
        "candidate_run_id": candidate_run_id,
        "dates": days,
        "summary": {
            status: sum(1 for item in days if item["status"] == status)
            for status in (
                UNCHANGED,
                ADDED,
                REMOVED,
                CHANGED,
                VISITS_MOVED,
                LOCKED_PRESERVED,
            )
        },
        "visits": {
            "added": sorted(f"{asset}@{day}" for asset, day in added),
            "removed": sorted(f"{asset}@{day}" for asset, day in removed),
            "moved": [
                {"asset_id": asset, "from": origin, "to": destination}
                for asset, origin, destination in moved
            ],
        },
        "months": _month_comparison(connection, base_run_id, candidate_run_id),
    }


def _pair_moves(
    removed: set[tuple[str, str]], added: set[tuple[str, str]]
) -> list[tuple[str, str, str]]:
    """Pair a removed visit with an added one of the same asset: that is a move."""

    pending_removed: dict[str, list[str]] = {}
    for asset, label in sorted(removed):
        pending_removed.setdefault(asset, []).append(label)
    pairs: list[tuple[str, str, str]] = []
    for asset, label in sorted(added):
        origin = pending_removed.get(asset)
        if origin:
            pairs.append((asset, origin.pop(0), label))
    return pairs


def _date_status(
    label: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    moved_here: list[dict[str, Any]],
    locked: bool,
) -> str:
    if before is None:
        return ADDED
    if after is None:
        return REMOVED
    if before == after:
        return LOCKED_PRESERVED if locked else UNCHANGED
    if (
        before["entries"] == after["entries"]
        and before["kind"] == after["kind"]
        and before["working"] == after["working"]
        and before["reasons_en"] == after["reasons_en"]
        and before["reasons_kn"] == after["reasons_kn"]
        and before["warnings"] == after["warnings"]
        and moved_here
    ):
        return VISITS_MOVED
    return CHANGED


def _entry_difference(
    before: dict[str, tuple], after: dict[str, tuple]
) -> dict[str, list[dict[str, Any]]]:
    added = [
        {"entry_id": key, "kind": after[key][1], "title_en": after[key][2]}
        for key in sorted(set(after) - set(before))
    ]
    removed = [
        {"entry_id": key, "kind": before[key][1], "title_en": before[key][2]}
        for key in sorted(set(before) - set(after))
    ]
    changed = [
        {
            "entry_id": key,
            "before": {"kind": before[key][1], "title_en": before[key][2]},
            "after": {"kind": after[key][1], "title_en": after[key][2]},
        }
        for key in sorted(set(before) & set(after))
        if before[key] != after[key]
    ]
    return {"added": added, "removed": removed, "changed": changed}


def _warnings(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Differences an operator should see but that are not a change of work."""

    warnings: list[dict[str, str]] = []
    if before is None or after is None:
        return warnings
    if before["kind"] != after["kind"]:
        warnings.append(
            {
                "constraint": "day_classification_changed",
                "detail": f"{before['kind']} became {after['kind']}",
            }
        )
    if before["reasons_en"] != after["reasons_en"]:
        warnings.append(
            {
                "constraint": "day_reason_changed",
                "detail": "; ".join(after["reasons_en"]),
            }
        )
    return warnings


def _month_comparison(
    connection: sqlite3.Connection, base_run_id: str, candidate_run_id: str
) -> list[dict[str, Any]]:
    base = {item.month_key: item for item in generation_store.read_run(connection, base_run_id).months}
    candidate = {
        item.month_key: item
        for item in generation_store.read_run(connection, candidate_run_id).months
    }
    months: list[dict[str, Any]] = []
    for label in sorted(set(base) | set(candidate)):
        before = base.get(label)
        after = candidate.get(label)
        months.append(
            {
                "month": label,
                "base_status": None if before is None else before.status,
                "proposed_status": None if after is None else after.status,
                "unresolved_evidence": [] if after is None else _loads(after.evidence_json),
            }
        )
    return months


# --- decisions ---------------------------------------------------------------


def accept_candidate(
    connection: sqlite3.Connection,
    register_id: str,
    candidate_run_id: str,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Move the register onto a candidate, atomically and only if it is eligible."""

    from . import finalisation
    from .revisions import reconcile_with_base

    finalisation.assert_no_finalised_months(
        connection, register_id, action="accepting a candidate"
    )
    candidate = revision_state.revision(connection, candidate_run_id)
    if candidate is None:
        raise RevisionError(C_CANDIDATE_NOT_FOUND, f"unknown revision {candidate_run_id}")
    if candidate.register_id != register_id:
        raise RevisionError(
            C_CANDIDATE_NOT_FOUND,
            f"revision {candidate_run_id} is not a revision of {register_id}",
        )
    if candidate.kind != "candidate":
        raise RevisionError(
            C_CANDIDATE_IS_NOT_A_CANDIDATE,
            f"revision {candidate_run_id} was not produced by a regeneration",
        )
    decided = revision_state.decision(connection, candidate_run_id)
    if decided is not None and decided.decision == "rejected":
        raise RevisionError(
            C_CANDIDATE_ALREADY_DECIDED,
            "this candidate was refused; regenerate rather than accepting it",
        )
    base_run_id = current_run(connection, register_id)
    if base_run_id == candidate_run_id:
        decided = revision_state.decision(connection, candidate_run_id)
        return {
            "register_id": register_id,
            "run_id": candidate_run_id,
            "decision": "accepted",
            "already": True,
        }

    blocked = _locked_content_blocker(connection, base_run_id, candidate_run_id)
    if blocked is not None:
        raise RevisionError(C_LOCKED_CONTENT_CHANGED, blocked)

    with generation_store.transaction(connection):
        reconcile_with_base(connection, candidate_run_id, base_run_id)
        revision_state.set_current(connection, register_id, candidate_run_id)
        revision_state.record_decision(
            connection,
            candidate_run_id,
            decision="accepted",
            decided_at=_now(),
            compared_to_run_id=base_run_id,
            note=note,
        )
    return {
        "register_id": register_id,
        "run_id": candidate_run_id,
        "decision": "accepted",
        "replaces": base_run_id,
        "already": False,
    }


def reject_candidate(
    connection: sqlite3.Connection,
    register_id: str,
    candidate_run_id: str,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Refuse a candidate. The attempt stays readable and the register does not move."""

    candidate = revision_state.revision(connection, candidate_run_id)
    if candidate is None or candidate.register_id != register_id:
        raise RevisionError(C_CANDIDATE_NOT_FOUND, f"unknown revision {candidate_run_id}")
    if candidate.kind != "candidate":
        raise RevisionError(
            C_CANDIDATE_IS_NOT_A_CANDIDATE,
            f"revision {candidate_run_id} was not produced by a regeneration",
        )
    if current_run(connection, register_id) == candidate_run_id:
        raise RevisionError(
            C_CANDIDATE_ALREADY_DECIDED,
            "the accepted revision is not a candidate and cannot be refused",
        )

    revision_state.record_decision(
        connection,
        candidate_run_id,
        decision="rejected",
        decided_at=_now(),
        compared_to_run_id=current_run(connection, register_id),
        note=note,
    )
    return {
        "register_id": register_id,
        "run_id": candidate_run_id,
        "decision": "rejected",
        "current_run_id": current_run(connection, register_id),
    }


# --- small helpers -----------------------------------------------------------


def _loads(raw: str) -> Any:
    import json

    return json.loads(raw)


def awaiting_decision(connection: sqlite3.Connection, register_id: str) -> list[dict[str, Any]]:
    """Candidates stored but not decided yet, newest first."""

    pending: list[dict[str, Any]] = []
    for revision in revision_state.revisions(connection, register_id):
        if revision.kind != "candidate":
            continue
        decision = revision_state.decision(connection, revision.run_id)
        if decision is not None:
            continue
        pending.append(
            {
                "run_id": revision.run_id,
                "revision_number": revision.revision_number,
                "created_at": revision.created_at,
                "parent_run_id": revision.parent_run_id,
                "seed": revision.seed,
                "outcome": revision.outcome,
            }
        )
    return pending


def revision_date(connection: sqlite3.Connection, run_id: str) -> str | None:
    row = generation_store.read_run(connection, run_id)
    return None if row is None else row.run.start_date
