"""Application services that compose the domain into a consumable capability.

The domain layer decides feasibility and placement and owns every scheduling rule.
This layer turns a typed request into a result a caller can present, and does no
scheduling of its own.
"""

from __future__ import annotations

from .draft_generation import (
    GENERATOR_VERSION,
    AssetDisplay,
    CalendarDay,
    CalendarDayKind,
    DayEntry,
    DayEntryKind,
    DraftDayRow,
    DraftException,
    DraftExceptionKind,
    DraftMode,
    DraftMonth,
    DraftMonthStatus,
    DraftRegister,
    DraftRequest,
    DraftStatus,
    GeneratedVisit,
    GenerationSnapshot,
    VisitProvenance,
    generate_draft,
)

__all__ = [
    "GENERATOR_VERSION",
    "AssetDisplay",
    "CalendarDay",
    "CalendarDayKind",
    "DayEntry",
    "DayEntryKind",
    "DraftDayRow",
    "DraftException",
    "DraftExceptionKind",
    "DraftMode",
    "DraftMonth",
    "DraftMonthStatus",
    "DraftRegister",
    "DraftRequest",
    "DraftStatus",
    "GeneratedVisit",
    "GenerationSnapshot",
    "VisitProvenance",
    "generate_draft",
]
