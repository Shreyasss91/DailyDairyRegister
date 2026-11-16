"""One issued monthly version, presented for export and for printing.

The record a finalisation wrote is the only input. This module reads that record and
nothing else: no master data, no generation tables, no allocator. A month that was issued
in November must read the same in March after six master imports, so anything that could
change between the two is out of reach here by construction.

What it adds is presentation and nothing else:

*   The stored row order is kept exactly as it was stored, never re-sorted, because the
    order is part of the document that was approved.
*   A day's stored classification is given a readable label beside the stored value, and a
    weekday is derived from the stored date. Both are formatting: the stored value stays
    beside its label, and an unrecognised classification is shown as itself rather than
    blanked or mapped onto a different one.
*   Nothing is filled in. A blank name stays blank, an empty date stays an empty date, and
    a day with no visits keeps its place in the register.

Read-only in the strict sense: the functions here issue no statement that writes, and a
test compares the whole finalisation record before and after an export.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from . import finalisation

#: Readable labels for the classifications a stored row can carry. Presentation only: the
#: stored value travels beside the label, and an unlisted value is shown as itself.
DAY_KIND_LABELS = {
    "working": "Working day",
    "sunday": "Sunday",
    "second_saturday": "Second Saturday",
    "fourth_saturday": "Fourth Saturday",
    "other_weekly_off": "Weekly off",
    "general_holiday": "General Holiday",
    "leave": "Leave",
    "other_non_working": "Non-working day",
}

#: Readable labels for the kind of a stored entry.
ENTRY_KIND_LABELS = {
    "activity": "Activity",
    "event": "Event",
    "safety_meeting": "Safety meeting",
    "leave": "Leave",
}

PROVENANCE_LABELS = {
    "fixed": "Committed before generation",
    "allocated": "Allocated",
}


@dataclass(frozen=True)
class DocumentVisit:
    """One field visit, exactly as the record stored it."""

    asset_id: str
    provenance: str
    provenance_label: str
    name_en: str
    name_kn: str | None


@dataclass(frozen=True)
class DocumentEntry:
    """One activity or other entry on a date, exactly as the record stored it."""

    entry_id: str
    kind: str
    kind_label: str
    title_en: str
    title_kn: str | None


@dataclass(frozen=True)
class DocumentDay:
    """One stored calendar date of the issued month."""

    day: str
    weekday: str
    kind: str
    kind_label: str
    working: bool
    reasons_en: tuple[str, ...]
    reasons_kn: tuple[str, ...]
    warnings: tuple[tuple[str, str], ...]
    visits: tuple[DocumentVisit, ...]
    entries: tuple[DocumentEntry, ...]
    locked: bool


@dataclass(frozen=True)
class RegisterDocument:
    """An issued version of one month, ready to render."""

    finalisation_id: str
    register_id: str
    month: str
    month_label: str
    revision_number: int
    sequence: int
    parent_finalisation_id: str | None
    correction_of_run_id: str | None
    finalised_at: str
    fingerprint: str
    master_version: int
    note: str | None
    is_latest: bool
    status: str
    allocator_status: str
    locked_dates: tuple[str, ...]
    acknowledged: tuple[dict[str, Any], ...]
    readiness: tuple[dict[str, Any], ...]
    obligations: tuple[tuple[str, Any], ...]
    declared_obligations: tuple[tuple[str, Any], ...]
    evidence: tuple[str, ...]
    days: tuple[DocumentDay, ...]

    @property
    def version_label(self) -> str:
        """How the document names itself: issued version 2 of November 2026."""

        return f"Issued version {self.sequence} of {self.month_label}"

    @property
    def standing_label(self) -> str:
        return "In force" if self.is_latest else "Superseded by a later version"


def document(connection: sqlite3.Connection, finalisation_id: str) -> RegisterDocument:
    """The issued version the identifier names, or a refusal.

    An identifier that names nothing, or names something that is not a finalised record,
    is refused here. There is deliberately no fallback to the month's latest version, to
    the register's current revision, or to a draft: exporting a different document from
    the one asked for is worse than exporting nothing.
    """

    record = finalisation.finalisation_record(connection, finalisation_id)
    content = record.get("content") or {}
    rows = content.get("rows") or {}
    locked = content.get("locked_dates") or []
    ordered = {day: rows[day] for day in rows}  # stored order, never re-sorted
    return RegisterDocument(
        finalisation_id=record["finalisation_id"],
        register_id=record["register_id"],
        month=record["month"],
        month_label=_month_label(record["month"]),
        revision_number=record["revision_number"],
        sequence=record["sequence"],
        parent_finalisation_id=record["parent_finalisation_id"],
        correction_of_run_id=record["correction_of_run_id"],
        finalised_at=record["finalised_at"],
        fingerprint=record["fingerprint"],
        master_version=record["master_version"],
        note=record["note"],
        is_latest=record["is_latest"],
        status=content.get("status") or "",
        allocator_status=content.get("allocator_status") or "",
        locked_dates=tuple(locked),
        acknowledged=tuple(record["acknowledged"]),
        readiness=tuple(record["readiness"]),
        obligations=_pairs(content.get("obligations")),
        declared_obligations=_pairs(content.get("declared_obligations")),
        evidence=tuple(
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in (content.get("evidence") or [])
        ),
        days=tuple(
            _day(day, row, locked=day in set(locked)) for day, row in ordered.items()
        ),
    )


def _day(day: str, row: dict[str, Any], *, locked: bool) -> DocumentDay:
    kind = _text(row.get("kind"))
    return DocumentDay(
        day=day,
        weekday=_weekday(day),
        kind=kind,
        kind_label=DAY_KIND_LABELS.get(kind, kind),
        working=bool(row.get("working")),
        reasons_en=tuple(_text(item) for item in row.get("reasons_en") or []),
        reasons_kn=tuple(_text(item) for item in row.get("reasons_kn") or []),
        warnings=tuple(
            (_text(item[0]), _text(item[1]))
            for item in row.get("warnings") or []
            if isinstance(item, list | tuple) and len(item) >= 2
        ),
        visits=tuple(_visit(item) for item in row.get("visits") or []),
        entries=tuple(_entry(item) for item in row.get("entries") or []),
        locked=locked,
    )


def _visit(item: Any) -> DocumentVisit:
    asset_id, provenance, name_en, name_kn = _four(item)
    return DocumentVisit(
        asset_id=asset_id,
        provenance=provenance,
        provenance_label=PROVENANCE_LABELS.get(provenance, provenance),
        name_en=name_en,
        name_kn=None if name_kn is None else name_kn,
    )


def _entry(item: Any) -> DocumentEntry:
    entry_id, kind, title_en, title_kn = _four(item)
    return DocumentEntry(
        entry_id=entry_id,
        kind=kind,
        kind_label=ENTRY_KIND_LABELS.get(kind, kind),
        title_en=title_en,
        title_kn=None if title_kn is None else title_kn,
    )


def _four(item: Any) -> tuple[str, str, str, str | None]:
    """The stored four-part tuple, padded rather than reshaped.

    A tuple written by another build may be shorter. Padding keeps the record readable
    instead of raising on a document that was already issued, and nothing is invented:
    an absent part is empty.
    """

    parts = list(item) if isinstance(item, list | tuple) else []
    parts += [""] * (4 - len(parts))
    head = [_text(parts[0]), _text(parts[1]), _text(parts[2])]
    tail = parts[3]
    return head[0], head[1], head[2], (None if tail in (None, "") else _text(tail))


def _pairs(value: Any) -> tuple[tuple[str, Any], ...]:
    """Stored keyed figures, in the order the record stored them."""

    if not isinstance(value, dict):
        return ()
    return tuple((_text(key), item) for key, item in value.items())


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _month_label(month: str) -> str:
    try:
        first = date.fromisoformat(f"{month}-01")
    except ValueError:
        return month
    return f"{first.strftime('%B')} {first.year}"


def _weekday(day: str) -> str:
    try:
        return date.fromisoformat(day).strftime("%A")
    except ValueError:
        return ""  # a stored key that is not a date is shown as itself, not guessed at
