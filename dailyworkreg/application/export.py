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
*   Each date is also reduced to the one Activity cell the register prints: every item
    recorded against that date, joined in one fixed order. Both the workbook and the
    printed page read that same cell, so the two cannot disagree about what a date says.

Read-only in the strict sense: the functions here issue no statement that writes, and a
test compares the whole finalisation record before and after an export.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..domain import wording
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

#: What a date with nothing recorded shows in the register. An em dash is a full stop
#: rather than a blank: a blank cell reads as an omission, and the register never omits a
#: date.
EMPTY_DAY_TEXT = "\u2014"

#: The order in which one date's recorded items are written into its single Activity cell.
#: Fixed rather than incidental, so the same issued version always reads the same way: the
#: field visits that were scheduled first, then the activities and other entries recorded
#: against the date, then the calendar reasons that explain the date, then the warnings the
#: draft carried. Nothing is dropped for being unusual, and nothing is added for a date
#: that recorded nothing.
DISPLAY_ITEM_ORDER = ("visits", "entries", "reasons", "warnings")

#: The separator between two items of one date, as the register prints them.
ACTIVITY_SEPARATOR = ", "

#: Which language a date's items are written in, as the operator chose when generating.
#: The choices themselves live in :mod:`dailyworkreg.domain.wording`; this module reads
#: them and never invents a fourth.
LANGUAGE_ENGLISH = wording.LANGUAGE_ENGLISH
LANGUAGE_KANNADA = wording.LANGUAGE_KANNADA
LANGUAGE_BOTH = wording.LANGUAGE_BOTH
ENTRY_LANGUAGES = wording.ENTRY_LANGUAGES
DEFAULT_ENTRY_LANGUAGE = wording.DEFAULT_ENTRY_LANGUAGE
LANGUAGE_LABELS = wording.LANGUAGE_DESCRIPTIONS

#: How a calendar reason is classified in the register, shared with the generation
#: request so the two cannot disagree about what a Sunday is.
REASON_KIND_LABELS = wording.REASON_KIND_LABELS

#: What a field visit says when it is written out. A visit is work, not an entry in a list,
#: so the register names what was done at the installation rather than only the installation:
#: a date reading "DIST TRAINING COLLEGE [SCD-44]" says where somebody went and not what the
#: register is a record of. This is presentation - the visit, its date and its asset are the
#: record - and an asset kind the templates do not know reads the neutral sentence rather
#: than no sentence.
#: How a visit reads, by the kind of asset it named. The sentence names the work and the
#: installation. Only the DTC carries its roster identifier (`SCD-44`): transformer work is
#: filed, reported and defended by that number, so a DTC entry without it cannot be matched
#: to the transformer it is about. Areas and feeders are named by their names in a register
#: the office files, and their identifiers stay in the record alone.
VISIT_PHRASES = {
    "DTC": (
        "Inspected {name} [{asset_id}] for transformer loading, oil level and earthing, "
        "and checked the consumer supply"
    ),
    "FEEDER": (
        "Patrolled {name} feeder for loading, tripping history and tree clearance along the route"
    ),
    "AREA": (
        "Inspected {name} area for DTC loading, street-light complaints and service connections"
    ),
}

#: The fallback when a record does not say which kind of asset a visit named.
GENERIC_VISIT_PHRASE = "Visited {name} and checked the supply and complaints there"


@dataclass(frozen=True)
class DocumentVisit:
    """One field visit, exactly as the record stored it."""

    asset_id: str
    provenance: str
    provenance_label: str
    name_en: str
    name_kn: str | None
    #: The kind of asset the visit named, read from the revision's own roster. Empty when
    #: a record written before this existed is read, which the phrasing handles rather than
    #: guesses at.
    asset_type: str = ""


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
    language: str = DEFAULT_ENTRY_LANGUAGE

    @property
    def items(self) -> tuple[str, ...]:
        """Everything this date recorded, as the register prints it, in one fixed order.

        The order is :data:`DISPLAY_ITEM_ORDER`, and each kind keeps the order the record
        stored it in, so the same issued version always renders identically. Identifiers
        are kept beside a name because a field visit without the asset it names is not
        traceable to an installation, and a stored Kannada name is printed beside its
        English one rather than translated, transliterated or left out.
        """

        items: list[str] = []
        for name in DISPLAY_ITEM_ORDER:
            if name == "visits":
                items.extend(
                    _visit_item(visit, self.language) for visit in self.visits
                )
            elif name == "entries":
                items.extend(
                    _entry_item(entry, self.language) for entry in self.entries
                )
            elif name == "reasons":
                # The classification is a fact about the date, not about each reason, so it
                # is stated once beside the first one rather than repeated on every line.
                label = REASON_KIND_LABELS.get(self.kind)
                for position, reason in enumerate(self._printable_reasons()):
                    items.append(_reason_item(reason, label if position == 0 else None))
            elif name == "warnings":
                items.extend(_warning_item(*warning) for warning in self.warnings)
        return tuple(item for item in items if item)

    @property
    def activity_text(self) -> str:
        """The one Activity cell of this date: its items, or an em dash when it has none."""

        items = self.items
        return ACTIVITY_SEPARATOR.join(items) if items else EMPTY_DAY_TEXT

    def _printable_reasons(self) -> tuple[str, ...]:
        """The reasons this date prints, in the chosen language, each one once.

        The stored English and Kannada lists are parallel facts rather than translations -
        a Sunday stores "Sunday" and, in Kannada, "weekly off" - so the chosen language
        picks the list that says it best, and a list that is empty falls back to the other
        rather than making a non-working date look unexplained. Two lists naming the same
        text are printed once.
        """

        if self.language == LANGUAGE_ENGLISH:
            chosen = self.reasons_en or self.reasons_kn
        elif self.language == LANGUAGE_KANNADA:
            chosen = self.reasons_kn or self.reasons_en
        else:
            chosen = self.reasons_en + tuple(
                reason for reason in self.reasons_kn if reason not in self.reasons_en
            )
        seen: set[str] = set()
        kept: list[str] = []
        for reason in chosen:
            if reason and reason not in seen:
                seen.add(reason)
                kept.append(reason)
        return tuple(kept)

    @property
    def visit_provenance(self) -> tuple[str, ...]:
        """The provenance of each visit on this date, for the document's own summary."""

        return tuple(visit.provenance for visit in self.visits)


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
    entry_language: str = DEFAULT_ENTRY_LANGUAGE

    @property
    def language_label(self) -> str:
        """How the document names the language its items are written in."""

        return LANGUAGE_LABELS.get(self.entry_language, self.entry_language)

    @property
    def version_label(self) -> str:
        """How the document names itself: issued version 2 of November 2026."""

        return f"Issued version {self.sequence} of {self.month_label}"

    @property
    def standing_label(self) -> str:
        return "In force" if self.is_latest else "Superseded by a later version"

    @property
    def heading_line(self) -> str:
        """The month and year a printed register is headed with, in capitals."""

        return self.month_label.upper()

    @property
    def visit_count(self) -> int:
        return sum(len(day.visits) for day in self.days)

    @property
    def recorded_provenance(self) -> tuple[tuple[str, int], ...]:
        """How many of this month's visits carry each provenance, most frequent first.

        A count of what the record stored, never a re-derivation: the register itself
        prints only the asset a visit names, so the distinction between a visit that was
        committed before generation and one the allocator placed is kept here. A
        provenance the labels do not know is counted under its own stored word rather
        than folded into a known one.
        """

        counts: dict[str, int] = {}
        for day in self.days:
            for provenance in day.visit_provenance:
                counts[provenance] = counts.get(provenance, 0) + 1
        return tuple(
            sorted(
                (
                    (PROVENANCE_LABELS.get(word, word), count)
                    for word, count in counts.items()
                ),
                key=lambda item: (-item[1], item[0]),
            )
        )


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
    language = content.get("entry_language") or DEFAULT_ENTRY_LANGUAGE
    if language not in ENTRY_LANGUAGES:
        # A stored value this build does not know is not quietly rendered as another one;
        # the register prints both texts, which is the reading that leaves nothing out.
        language = DEFAULT_ENTRY_LANGUAGE
    asset_types = {
        str(key): str(value)
        for key, value in (content.get("asset_types") or {}).items()
    }
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
        entry_language=language,
        days=tuple(
            _day(
                day,
                row,
                locked=day in set(locked),
                language=language,
                asset_types=asset_types,
            )
            for day, row in ordered.items()
        ),
    )


def _day(
    day: str,
    row: dict[str, Any],
    *,
    locked: bool,
    language: str,
    asset_types: Mapping[str, str],
) -> DocumentDay:
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
        visits=tuple(
            _visit(item, asset_types) for item in row.get("visits") or []
        ),
        entries=tuple(_entry(item) for item in row.get("entries") or []),
        locked=locked,
        language=language,
    )


def _visit(item: Any, asset_types: Mapping[str, str]) -> DocumentVisit:
    asset_id, provenance, name_en, name_kn = _four(item)
    return DocumentVisit(
        asset_id=asset_id,
        provenance=provenance,
        provenance_label=PROVENANCE_LABELS.get(provenance, provenance),
        name_en=name_en,
        name_kn=None if name_kn is None else name_kn,
        asset_type=asset_types.get(asset_id, ""),
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


def _visit_item(visit: DocumentVisit, language: str = DEFAULT_ENTRY_LANGUAGE) -> str:
    """One field visit as the register prints it: what was done, and where.

    The sentence names the work rather than only the installation, and the installation is
    named in the language the register is being written in. An asset with no stored Kannada
    name keeps its English one rather than printing a blank, and a record that does not say
    which kind of asset it visited reads the neutral sentence instead of a guessed verb.

    Only the DTC sentence carries an identifier, and it carries it because the DTC is the
    one asset the office files by number: `SCD-44` is how transformer work is reported, so
    an entry without it cannot be matched to the transformer it is about. An area or a
    feeder is named by its name, and its identifier - `A-15`, `F-01` - is bookkeeping the
    record keeps and the document does not print.
    """

    name = _in_language(visit.name_en, visit.name_kn, language).strip()
    template = VISIT_PHRASES.get(visit.asset_type or "", GENERIC_VISIT_PHRASE)
    return template.format(name=name, asset_id=visit.asset_id).strip()


def _entry_item(entry: DocumentEntry, language: str = DEFAULT_ENTRY_LANGUAGE) -> str:
    """One activity or other entry, in the language the register is written in."""

    return _in_language(entry.title_en, entry.title_kn, language).strip()


def _reason_item(reason: str, label: str | None) -> str:
    """One calendar reason, classified so a date's reason cannot be mistaken for work.

    The stored reason stays as the record wrote it, and what that day is to the office - a
    weekly off, a holiday, leave - is written beside it, which turns a bare "Sunday" into
    a statement about the date rather than a label for it.
    """

    text = reason.strip()
    if not text:
        return ""
    return f"{text} ({label})" if label else text


def _in_language(english: str, kannada: str | None, language: str) -> str:
    """The text of one item in the chosen language, never blank if something is stored.

    A Kannada register whose record holds no Kannada for an item keeps its English, because
    a register that prints an empty cell for real recorded work is worse than one that
    prints what it holds in the other language.
    """

    english = (english or "").strip()
    kannada = (kannada or "").strip()

    if language == LANGUAGE_KANNADA:
        return kannada or english
    if language == LANGUAGE_ENGLISH:
        return english or kannada
    if english and kannada:
        return f"{english} ({kannada})"
    return english or kannada


def _warning_item(constraint: str, detail: str) -> str:
    """A recorded warning, labelled so it cannot be mistaken for an activity."""

    detail = detail.strip()
    if not constraint:
        return f"Warning: {detail}" if detail else ""
    return f"Warning: {detail} [{constraint}]" if detail else f"Warning [{constraint}]"


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
