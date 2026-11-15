"""Application services that compose the domain into a consumable capability.

The domain layer decides feasibility and placement and owns every scheduling rule.
This layer turns a typed request into a result a caller can present, and does no
scheduling of its own.
"""

from __future__ import annotations

from .calendar_projection import (
    SAFETY_MEETING_WORDING,
    CalendarProjection,
    WeeklyOffRules,
    load_weekly_off_rules,
    project_calendar,
    save_weekly_off_rules,
)
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
from .draft_requests import (
    GenerationInputError,
    GenerationSpec,
    PreparedGeneration,
    prepare_generation,
)
from .generation_runs import (
    load_readiness,
    load_register,
    regenerate,
    save_run,
)
from .readiness import Readiness, ReadinessItem, assess_readiness
from .snapshot import (
    REQUEST_SNAPSHOT_SCHEMA,
    SnapshotError,
    decode_request,
    encode_request,
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
    "GenerationInputError",
    "GenerationSpec",
    "PreparedGeneration",
    "prepare_generation",
    "Readiness",
    "ReadinessItem",
    "assess_readiness",
    "save_run",
    "load_register",
    "regenerate",
    "load_readiness",
    "REQUEST_SNAPSHOT_SCHEMA",
    "SnapshotError",
    "decode_request",
    "encode_request",
    "CalendarProjection",
    "WeeklyOffRules",
    "load_weekly_off_rules",
    "project_calendar",
    "save_weekly_off_rules",
    "SAFETY_MEETING_WORDING",
]
