"""Draft-register generation: a typed request projected from allocator decisions.

This module is the integration boundary between the scheduling domain and the
daily-register workflow. Its one rule is that **it consumes and presents allocator
decisions; it never makes them**. Every obligation, date, capacity and revisit-gap
decision reaching a register row is the allocator's, passed through unchanged.

What it does do:

*   turns a typed :class:`DraftRequest` into exactly one ordered row for every
    calendar date in the selected full months, including non-working dates;
*   projects each allocator visit once, keeping fixed commitments and newly
    allocated visits distinguishable;
*   preserves every outcome the allocator reported, including ``INFEASIBLE`` and
    ``INDETERMINATE``, and surfaces over-coverage as a resolved exception;
*   records enough about the inputs to reproduce the same draft.

What it deliberately does not do, because a second implementation of a scheduling
rule is how two answers to one question appear:

*   no DTC target calculation, cycle distribution, gap calculation, daily or
    monthly capacity calculation, or monthly-maximum override;
*   no moving of a visit to another date, and no visit invented to fill capacity;
*   no relaxation of an obligation, and no weight treated as an obligation;
*   no proration of a partial month, because the integer rounding rule is not
    declared by the owner.

**Scope of the output.** This produces an allocator-backed draft of the *field
visit* content plus the regular activities the catalogue places on working days (see
:func:`dailyworkreg.domain.activities.allocate_activities`). The official Karnataka
holiday baseline and the source DTC roster are separate dependencies; where one is
absent, :attr:`DraftRegister.missing_content` names it. A month whose visits
allocated successfully is therefore allocation-complete, not a complete
operational diary, and the attribute says which dependencies are still missing.
"""

from __future__ import annotations

import calendar as _stdlib_calendar
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any

from ..domain import wording
from ..domain.activities import (
    ActivityInfeasible,
    ActivitySpec,
    allocate_activities,
)
from ..domain.allocator import ALLOCATOR_VERSION
from ..domain.cycle import CoverageCycle, CycleResolution, DtcWeights, MonthKey
from ..domain.targeting import (
    MultiMonthResult,
    TargetMode,
    run_cycle,
    run_fallback,
)
from ..domain.validator import (
    Asset,
    AssetType,
    Calendar,
    DatePeriod,
    Evidence,
    FeasibilityConfig,
    FeasibilityStatus,
    InputError,
    Violation,
    Visit,
)
from ..domain.wording import DEFAULT_ENTRY_LANGUAGE

#: Version of this generation service. Recorded on every draft so a projection can
#: be explained after the code moves, independently of the allocator version.
GENERATOR_VERSION = "1.0.0"

# --- stable input-error identifiers ---------------------------------------

#: The selected range is not a sequence of complete calendar months.
C_PARTIAL_SELECTION = "partial_month_selection_unsupported"
#: The selected range's end precedes its start.
C_RANGE_REVERSED = "draft_range_reversed"
#: A date in the range has no supplied metadata.
C_DATE_METADATA_MISSING = "calendar_date_metadata_missing"
#: A date in the range is described more than once.
C_DATE_METADATA_DUPLICATE = "calendar_date_metadata_duplicate"
#: Date metadata was supplied for a date outside the range.
C_COMMITMENT_DATE_METADATA_MISSING = "commitment_date_metadata_missing"
#: The working-day metadata contradict themselves for one date.
C_DATE_METADATA_INCONSISTENT = "calendar_date_metadata_inconsistent"
#: An asset in the roster has no display metadata.
C_DISPLAY_METADATA_MISSING = "asset_display_metadata_missing"
#: Display metadata names an asset that is not in the roster.
C_DISPLAY_METADATA_UNKNOWN_ASSET = "asset_display_metadata_unknown_asset"
#: Display metadata describes one asset more than once.
C_DISPLAY_METADATA_DUPLICATE = "asset_display_metadata_duplicate"
#: A supplied day entry falls outside the selected range.
C_ENTRY_OUTSIDE_RANGE = "day_entry_outside_range"
#: The activity catalogue cannot place the requested number of activities per day.
C_ACTIVITY_ALLOCATION_INFEASIBLE = "activity_allocation_infeasible"
#: Cycle mode was requested without a declared coverage cycle.
C_CYCLE_REQUIRED = "coverage_cycle_required"
#: Fallback mode was requested together with a coverage cycle.
C_FALLBACK_CONFLICT = "fallback_mode_with_coverage_cycle"
#: An input belongs to the other allocation mode.
C_MODE_INPUT_CONFLICT = "input_belongs_to_other_mode"
#: The selected range is not covered by the declared cycle.
C_RANGE_OUTSIDE_CYCLE = "draft_range_outside_coverage_cycle"
#: A committed visit was filed under a month that does not contain its date.
C_FIXED_VISIT_WRONG_MONTH = "fixed_visit_in_wrong_month"
#: The roster is empty.
C_ROSTER_REQUIRED = "asset_roster_required"
#: The requested entry language is not one the platform writes.
C_ENTRY_LANGUAGE_INVALID = "entry_language_invalid"


class DraftMode(str, Enum):
    """Which allocator entry point produced the draft."""

    #: A declared coverage cycle, the primary policy.
    COVERAGE_CYCLE = "coverage_cycle"
    #: The capacity-bounded monthly suggestion, used when no cycle is declared.
    CAPACITY_FALLBACK = "capacity_fallback"


class CalendarDayKind(str, Enum):
    """Classification of one calendar date, supplied by the caller.

    The register displays this classification; it never infers it. The owner has
    not approved a Karnataka holiday baseline, so inventing a General Holiday or a
    weekly-off rule here would be fabricating master data.

    There is deliberately no Restricted Holiday member. Requirements §4.1 makes a
    Restricted Holiday an ordinary working day and says its dates and names are to
    be ignored entirely, so a classification for it would invite exactly the
    behaviour the requirement forbids. A date that is only a Restricted Holiday is
    therefore classified by whatever independently applies to it, which for most
    such dates means ``WORKING``.
    """

    WORKING = "working"
    SUNDAY = "sunday"
    SECOND_SATURDAY = "second_saturday"
    FOURTH_SATURDAY = "fourth_saturday"
    OTHER_WEEKLY_OFF = "other_weekly_off"
    GENERAL_HOLIDAY = "general_holiday"
    LEAVE = "leave"
    OTHER_NON_WORKING = "other_non_working"


class VisitProvenance(str, Enum):
    """Where a projected visit came from. Never inferred from text."""

    #: Already committed before generation and preserved unchanged.
    FIXED = "fixed"
    #: Newly allocated by the allocator.
    ALLOCATED = "allocated"


class DayEntryProvenance(str, Enum):
    """Where a non-visit entry came from, which decides whether it survives."""

    CALENDAR = "calendar"
    MANUAL = "manual"
    LOCKED = "locked"
    #: A placement the generator itself made from the activity catalogue. It is
    #: re-derived on every generation, unlike a manual entry, and it is never the
    #: reason a person's correction is dropped.
    GENERATED = "generated"


class DayEntryKind(str, Enum):
    """A non-visit entry on a date row.

    These are projected for display and are never visit capacity: an event or a
    safety meeting is not a field visit and does not consume the combined or area
    daily cap on the allocator's behalf.
    """

    ACTIVITY = "activity"
    EVENT = "event"
    SAFETY_MEETING = "safety_meeting"
    LEAVE = "leave"


class DraftMonthStatus(str, Enum):
    """The allocation outcome of one month, carried through unchanged.

    ``UNRESOLVED`` is deliberately distinct from ``INFEASIBLE``. A bounded search
    that established neither feasibility nor impossibility proves nothing, so
    presenting it as either verdict would be a claim the allocator did not make.
    """

    ALLOCATION_COMPLETE = "allocation_complete"
    INFEASIBLE = "infeasible"
    UNRESOLVED = "unresolved"


class DraftStatus(str, Enum):
    """The worst allocation outcome across the selected months."""

    ALLOCATION_COMPLETE = "allocation_complete"
    INFEASIBLE = "infeasible"
    UNRESOLVED = "unresolved"


class DraftExceptionKind(str, Enum):
    """Why a draft carries an exception."""

    #: Committed visits exceed the declared cycle obligation for one asset.
    OVER_COVERED = "over_covered"
    #: The allocator proved the month's obligations cannot be scheduled.
    INFEASIBLE = "infeasible"
    #: The allocator's bounded search ended without a verdict.
    UNRESOLVED = "unresolved"
    #: A date row carries a warning derived from supplied metadata.
    ROW_WARNING = "row_warning"


# --- immutable generation inputs ------------------------------------------


@dataclass(frozen=True)
class AssetDisplay:
    """Display metadata for one roster asset.

    Names are held separately per language and are never translated at runtime.
    When Kannada text has not been supplied it stays absent rather than being
    machine-translated, because an unreviewed translation of an official register
    is worse than an empty cell.
    """

    asset_id: str
    name_en: str
    name_kn: str | None = None


@dataclass(frozen=True)
class CalendarDay:
    """Classified metadata for one calendar date in the selected range.

    ``is_working_day`` is the authority for allocator eligibility. It must agree
    with ``kind``: a day classified as a holiday, a weekly off or leave cannot
    simultaneously be a working day, so a contradiction is rejected rather than
    resolved by preferring one field.
    """

    day: date
    kind: CalendarDayKind
    is_working_day: bool
    reasons_en: tuple[str, ...] = ()
    reasons_kn: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        weekly_off = {
            CalendarDayKind.SUNDAY,
            CalendarDayKind.SECOND_SATURDAY,
            CalendarDayKind.FOURTH_SATURDAY,
            CalendarDayKind.OTHER_WEEKLY_OFF,
        }
        non_working = weekly_off | {
            CalendarDayKind.GENERAL_HOLIDAY,
            CalendarDayKind.LEAVE,
            CalendarDayKind.OTHER_NON_WORKING,
        }
        if self.is_working_day and self.kind in non_working:
            raise InputError(
                C_DATE_METADATA_INCONSISTENT,
                (
                    f"{self.day.isoformat()} is classified {self.kind.value} but "
                    "marked as a working day"
                ),
            )
        if not self.is_working_day and self.kind is CalendarDayKind.WORKING:
            raise InputError(
                C_DATE_METADATA_INCONSISTENT,
                (
                    f"{self.day.isoformat()} is classified working but marked as "
                    "not a working day"
                ),
            )


@dataclass(frozen=True)
class DayEntry:
    """A non-visit entry for a date row, with where it came from.

    ``provenance`` is what lets a regeneration tell an entry it may re-derive from the
    calendar apart from one an operator wrote. A projected safety meeting carries
    ``calendar`` and will be projected again; a correction carries ``manual`` and must
    survive every later generation; an entry on a locked date carries ``locked``
    because that date is preserved rather than rebuilt. Regenerating over a manual
    entry would erase a person's work without a trace.
    """

    entry_id: str
    day: date
    kind: DayEntryKind
    title_en: str
    title_kn: str | None = None
    provenance: str = DayEntryProvenance.CALENDAR


@dataclass(frozen=True)
class DraftRequest:
    """Everything one draft generation needs, explicitly.

    Nothing here is defaulted from master data. There is no implicit roster, no
    assumed working week and no invented holiday: a caller that has not supplied a
    fact has not supplied it, and generation says so rather than guessing.

    Mode-specific inputs are validated strictly. Cycle mode requires ``cycle`` and
    rejects fallback-only inputs, because passing a DTC target to a cycle run would
    silently do nothing. Fallback mode requires that no cycle is declared, because
    a declared cycle means the coverage commitment is the primary policy.

    Attributes:
        start: First day of the selected range. Must be the first of its month.
        end: Last day of the selected range. Must be the last of its month.
        mode: Which allocator entry point to use.
        roster: Complete asset roster with stable ids.
        display: Display metadata, one entry per roster asset.
        calendar_days: Classified metadata, one entry per date in the range.
        config: Capacities, limits and search budget.
        seed: Deterministic seed, passed to the allocator unchanged.
        prior_history: Visits before the range, constraining the revisit gap.
        fixed_by_month: Committed visits, keyed by the month containing them.
        entries: Non-visit day entries (activities, events, safety meetings).
        activities: The approved activity catalogue the generator places on working
            days, in the catalogue's own order. Empty is allowed: a request without
            a catalogue generates visits only.
        activities_per_day_min: The floor of the regular-activity count on every
            eligible working day. A day never records fewer.
        activities_per_day_max: The ceiling of that count. A day never records more,
            and between the two ends the day's own count is drawn from the seed.
            ``0`` for both leaves the days to visits alone.
        entry_language: Which language the register is written in - ``en``, ``kn`` or
            ``both``. It is recorded with the draft so an issued month keeps the
            language it was approved in, and it changes presentation only: the same
            requests with the same seed place the same work in every language.
        cycle: Declared coverage cycle, cycle mode only.
        weights: DTC scheduling weights, influence ordering only.
        rotation_offset: Rotation state carried between cycles.
        area_required: Area obligations per month, fallback mode only.
        feeder_required: Feeder obligations per month, fallback mode only.
        dtc_target: Capacity-bounded DTC target, fallback mode only.
        attempt_budget: Constructive attempts per month.
    """

    start: date
    end: date
    mode: DraftMode
    roster: tuple[Asset, ...]
    display: tuple[AssetDisplay, ...]
    calendar_days: tuple[CalendarDay, ...]
    config: FeasibilityConfig
    seed: int = 0
    prior_history: tuple[Visit, ...] = ()
    fixed_by_month: Mapping[MonthKey, tuple[Visit, ...]] = field(default_factory=dict)
    entries: tuple[DayEntry, ...] = ()
    activities: tuple[ActivitySpec, ...] = ()
    activities_per_day_min: int = 0
    activities_per_day_max: int = 0
    entry_language: str = DEFAULT_ENTRY_LANGUAGE
    cycle: CoverageCycle | None = None
    weights: DtcWeights | None = None
    rotation_offset: int = 0
    area_required: Mapping[MonthKey, Mapping[str, int]] = field(default_factory=dict)
    feeder_required: Mapping[MonthKey, Mapping[str, int]] = field(default_factory=dict)
    dtc_target: int | None = None
    attempt_budget: int = 400

    def months(self) -> tuple[MonthKey, ...]:
        """The selected months, chronologically, as full calendar months."""

        months: list[MonthKey] = []
        year, month = self.start.year, self.start.month
        while (year, month) <= (self.end.year, self.end.month):
            months.append(MonthKey(year, month))
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
        return tuple(months)


# --- immutable generation outputs -----------------------------------------


@dataclass(frozen=True)
class DraftException:
    """A structured exception carried by a draft.

    The fields are the ones the domain already reports. Nothing here is recovered
    by parsing a message, so a caller never has to match English text to learn
    which asset or which constraint is involved.
    """

    kind: DraftExceptionKind
    constraint: str
    detail: str
    month: MonthKey | None = None
    asset_id: str | None = None
    asset_type: AssetType | None = None
    visit_date: date | None = None
    offending_dates: tuple[date, ...] = ()
    limit: int | None = None
    actual: int | None = None
    #: The assets that jointly caused an aggregate finding, when the domain
    #: identified them. Empty for a single-asset or unattributable finding.
    contributing_assets: tuple[tuple[str, AssetType], ...] = ()

    @classmethod
    def from_evidence(
        cls,
        kind: DraftExceptionKind,
        evidence: Evidence,
        month: MonthKey | None = None,
    ) -> DraftException:
        """Build from a domain ``Evidence`` without reinterpreting it."""

        return cls(
            kind=kind,
            constraint=evidence.constraint,
            detail=evidence.detail,
            month=month,
            asset_id=evidence.asset_id,
            asset_type=evidence.asset_type,
            visit_date=evidence.visit_date,
            offending_dates=tuple(evidence.offending_dates),
            limit=evidence.limit,
            actual=evidence.actual,
            contributing_assets=tuple(evidence.contributing_assets),
        )

    @classmethod
    def from_violation(cls, violation: Violation) -> DraftException:
        """Build from a domain ``Violation`` without reinterpreting it."""

        return cls(
            kind=DraftExceptionKind.ROW_WARNING,
            constraint=violation.constraint,
            detail=violation.message,
            asset_id=violation.asset_id,
            asset_type=violation.asset_type,
            visit_date=violation.visit_date,
            offending_dates=tuple(violation.offending_dates),
            limit=violation.limit,
            actual=violation.actual,
            contributing_assets=tuple(violation.contributing_assets),
        )


@dataclass(frozen=True)
class GeneratedVisit:
    """One projected visit, with its provenance and display names.

    The visit itself is the allocator's, unchanged. ``asset_id`` is the stable
    identity; the display names are resolved from the supplied roster snapshot and
    are never the source of truth.
    """

    visit: Visit
    provenance: VisitProvenance
    month: MonthKey
    name_en: str
    name_kn: str | None = None

    @property
    def asset_id(self) -> str:
        return self.visit.asset_id

    @property
    def visit_date(self) -> date:
        return self.visit.visit_date


@dataclass(frozen=True)
class DraftDayRow:
    """Exactly one row per calendar date in the selected range.

    Non-working dates get rows too. A register that lists only the dates carrying
    visits cannot be printed as a daily register, and omitting a Sunday silently is
    how a generated month stops matching the calendar it claims to describe.
    """

    day: date
    weekday: int
    kind: CalendarDayKind
    is_working_day: bool
    reasons_en: tuple[str, ...] = ()
    reasons_kn: tuple[str, ...] = ()
    fixed_visits: tuple[GeneratedVisit, ...] = ()
    allocated_visits: tuple[GeneratedVisit, ...] = ()
    entries: tuple[DayEntry, ...] = ()
    warnings: tuple[DraftException, ...] = ()

    @property
    def visits(self) -> tuple[GeneratedVisit, ...]:
        """Both visit kinds in a stable order, fixed first."""

        return self.fixed_visits + self.allocated_visits

    @property
    def is_empty(self) -> bool:
        return not (self.fixed_visits or self.allocated_visits or self.entries)


@dataclass(frozen=True)
class DraftMonth:
    """One selected month of a draft.

    ``declared_obligations`` is what the coverage cycle declared;
    ``obligations`` is what the allocator was actually asked for after commitments
    were reconciled. They are kept apart so neither is mistaken for the other.
    """

    month: MonthKey
    period: DatePeriod
    status: DraftMonthStatus
    allocator_status: FeasibilityStatus
    obligations: Mapping[str, int]
    declared_obligations: Mapping[str, int]
    visits: tuple[GeneratedVisit, ...]
    evidence: tuple[DraftException, ...] = ()

    @property
    def is_allocation_complete(self) -> bool:
        return self.status is DraftMonthStatus.ALLOCATION_COMPLETE


@dataclass(frozen=True)
class GenerationSnapshot:
    """What was needed to produce this draft.

    Large mutable inputs are reduced to content fingerprints, so a draft records
    which roster, configuration and calendar it was built from without copying
    objects that may later change in place. The seed, versions and cycle are kept
    whole because they are small and are the parts a reader actually needs to
    explain or replay a generation.
    """

    allocator_version: str
    generator_version: str
    mode: DraftMode
    seed: int
    start: date
    end: date
    months: tuple[MonthKey, ...]
    rotation_offset: int
    attempt_budget: int
    roster_fingerprint: str
    config_fingerprint: str
    calendar_fingerprint: str
    display_fingerprint: str
    cycle: CoverageCycle | None = None
    cycle_resolution: CycleResolution | None = None
    prior_history: tuple[Visit, ...] = ()
    committed_visits: tuple[Visit, ...] = ()


@dataclass(frozen=True)
class DraftRegister:
    """A generated draft register.

    This is a *generated draft* and nothing more. It is not reviewed, not
    finalised and not immutable revision storage; those transitions are separate
    capabilities and a draft is never mutated into one of them in place.

    ``allocation_status`` describes field-visit allocation only. It does not claim
    the operational daily diary is complete: ``missing_content`` names the masters
    that do not exist in this repository yet.
    """

    status: DraftStatus
    mode: DraftMode
    start: date
    end: date
    seed: int
    generator_version: str
    allocator_version: str
    months: tuple[DraftMonth, ...]
    rows: tuple[DraftDayRow, ...]
    visits: tuple[GeneratedVisit, ...]
    exceptions: tuple[DraftException, ...] = ()
    warnings: tuple[DraftException, ...] = ()
    snapshot: GenerationSnapshot | None = None
    #: Dependencies that stop this being a complete operational diary.
    missing_content: tuple[str, ...] = ()
    #: The language this register was generated in. Part of the draft rather than of the
    #: rendering, because a month is issued in one language and the record has to keep it.
    entry_language: str = DEFAULT_ENTRY_LANGUAGE

    @property
    def is_allocation_complete(self) -> bool:
        return self.status is DraftStatus.ALLOCATION_COMPLETE

    @property
    def is_operational_diary(self) -> bool:
        """Always False while required masters are absent.

        Kept as an explicit property rather than a field so no caller can set it
        to True merely because allocation succeeded.
        """

        return not self.missing_content

    def month_for(self, month: MonthKey) -> DraftMonth | None:
        for candidate in self.months:
            if candidate.month == month:
                return candidate
        return None

    def row_for(self, day: date) -> DraftDayRow | None:
        for row in self.rows:
            if row.day == day:
                return row
        return None

    def visits_on(self, day: date) -> tuple[GeneratedVisit, ...]:
        row = self.row_for(day)
        return row.visits if row is not None else ()



# --- request validation ---------------------------------------------------


def _month_last_day(month: MonthKey) -> date:
    return date(
        month.year, month.month, _stdlib_calendar.monthrange(month.year, month.month)[1]
    )


def _fingerprint(*parts: object) -> str:
    """A stable content fingerprint for a snapshot field.

    Built from a canonical rendering rather than ``hash()``, whose value is
    process-randomised and so could not be compared between two runs.
    """

    digest = hashlib.sha256()
    for part in parts:
        digest.update(repr(part).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


def _validate_range(request: DraftRequest) -> tuple[MonthKey, ...]:
    """Reject anything that is not a run of complete calendar months.

    A partial month cannot be generated because the owner has not declared the
    integer rounding rule for prorating a partial month's targets and limits, and
    the multi-month allocator helpers support full calendar months only. Expanding a
    partial range to a whole month, or applying full-month limits to a shorter
    period, would invent a policy. The domain's explicit effective-limit mechanism
    is left intact for when the rule is declared.
    """

    if request.end < request.start:
        raise InputError(
            C_RANGE_REVERSED,
            (
                f"the selected range ends {request.end.isoformat()} before it "
                f"starts {request.start.isoformat()}"
            ),
        )

    months = request.months()
    first, last = months[0], months[-1]
    first_day = date(first.year, first.month, 1)
    if request.start != first_day:
        raise InputError(
            C_PARTIAL_SELECTION,
            (
                f"the selected range starts {request.start.isoformat()}; a draft "
                "can only be generated for complete calendar months, so it must "
                f"start on {first_day.isoformat()}, or the integer rounding rule "
                "for prorating a partial month must first be declared"
            ),
        )
    last_day = _month_last_day(last)
    if request.end != last_day:
        raise InputError(
            C_PARTIAL_SELECTION,
            (
                f"the selected range ends {request.end.isoformat()}; a draft can "
                "only be generated for complete calendar months, so it must end on "
                f"{last_day.isoformat()}, or the integer rounding rule for "
                "prorating a partial month must first be declared"
            ),
        )
    return months


def _validate_roster(request: DraftRequest) -> dict[str, AssetDisplay]:
    """Require complete display metadata for exactly the supplied roster."""

    if not request.roster:
        raise InputError(C_ROSTER_REQUIRED, "the asset roster is empty")

    roster_ids = {asset.asset_id for asset in request.roster}
    by_id: dict[str, AssetDisplay] = {}
    for display in request.display:
        if display.asset_id in by_id:
            raise InputError(
                C_DISPLAY_METADATA_DUPLICATE,
                f"display metadata for {display.asset_id!r} was supplied twice",
                asset_id=display.asset_id,
            )
        if display.asset_id not in roster_ids:
            raise InputError(
                C_DISPLAY_METADATA_UNKNOWN_ASSET,
                (
                    f"display metadata names {display.asset_id!r}, which is not in "
                    "the roster"
                ),
                asset_id=display.asset_id,
            )
        by_id[display.asset_id] = display

    missing = sorted(roster_ids - set(by_id))
    if missing:
        raise InputError(
            C_DISPLAY_METADATA_MISSING,
            (
                "no display metadata was supplied for "
                f"{', '.join(missing)}; a register row cannot invent a name"
            ),
            asset_id=missing[0],
        )
    return by_id


def _validate_calendar_days(request: DraftRequest) -> dict[date, CalendarDay]:
    """Require exactly one classification for every date in the range.

    Metadata for dates outside the range is accepted and used only to classify
    those dates for the allocator. A coverage cycle can span months that are not
    being generated, and a commitment in such a month still has to be checked
    against the real calendar, so refusing the extra dates would make the
    unselected-month commitment contract impossible to honour. Such dates are never
    projected as rows: rows exist only inside the selected range.
    """

    by_day: dict[date, CalendarDay] = {}
    for metadata in request.calendar_days:
        if metadata.day in by_day:
            raise InputError(
                C_DATE_METADATA_DUPLICATE,
                f"calendar metadata for {metadata.day.isoformat()} was supplied twice",
            )
        by_day[metadata.day] = metadata

    span = (request.end - request.start).days + 1
    expected = {request.start + timedelta(days=offset) for offset in range(span)}
    missing = sorted(expected - set(by_day))
    if missing:
        raise InputError(
            C_DATE_METADATA_MISSING,
            (
                "no calendar classification was supplied for "
                + ", ".join(d.isoformat() for d in missing[:5])
                + (" and others" if len(missing) > 5 else "")
                + "; the register shows the classification it is given and never "
                "infers a holiday or a weekly off"
            ),
        )
    return by_day


def _validate_language(request: DraftRequest) -> None:
    """Refuse a language the register is not written in.

    The choice is recorded with the draft, so an unknown value would be carried into the
    issued record and read back by a later build that does not know it either. It is
    refused where the request is made instead.
    """

    if not wording.is_known_language(request.entry_language):
        raise InputError(
            C_ENTRY_LANGUAGE_INVALID,
            (
                f"{request.entry_language!r} is not a language the register is written "
                "in; it is written in English, Kannada, or both"
            ),
        )


def _validate_entries(request: DraftRequest) -> None:
    for entry in request.entries:
        if entry.day < request.start or entry.day > request.end:
            raise InputError(
                C_ENTRY_OUTSIDE_RANGE,
                (
                    f"entry {entry.entry_id!r} falls on {entry.day.isoformat()}, "
                    "which is outside the selected range"
                ),
            )


def _validate_mode_inputs(request: DraftRequest, months: Sequence[MonthKey]) -> None:
    """Hold each mode to its own explicit inputs, with no silent no-ops."""

    if request.mode is DraftMode.COVERAGE_CYCLE:
        if request.cycle is None:
            raise InputError(
                C_CYCLE_REQUIRED,
                (
                    "coverage-cycle generation needs the declared coverage cycle; "
                    "without one there is no coverage commitment to satisfy"
                ),
            )
        conflicting = []
        if request.dtc_target is not None:
            conflicting.append("dtc_target")
        if request.area_required:
            conflicting.append("area_required")
        if request.feeder_required:
            conflicting.append("feeder_required")
        if conflicting:
            raise InputError(
                C_MODE_INPUT_CONFLICT,
                (
                    ", ".join(conflicting)
                    + " cannot be honoured in coverage-cycle mode; run_fallback "
                    "supplies those, and passing them to a cycle run would silently "
                    "do nothing"
                ),
            )
        outside = [m for m in months if not request.cycle.contains(m)]
        if outside:
            raise InputError(
                C_RANGE_OUTSIDE_CYCLE,
                (
                    f"{outside[0].label()} is outside the declared cycle starting "
                    f"{request.cycle.start_month.label()}"
                ),
            )
        return

    if request.cycle is not None:
        raise InputError(
            C_FALLBACK_CONFLICT,
            (
                "a capacity-based fallback run cannot also carry a declared "
                "coverage cycle; a declared cycle makes coverage the primary policy"
            ),
        )


def _validate_commitment_calendar(
    request: DraftRequest, days: Mapping[date, CalendarDay]
) -> None:
    """Require a classification for every date a commitment sits on.

    A commitment in a month outside the generated range is passed to the allocator
    unchanged, and the allocator must know whether that date is an eligible working
    date. Without metadata for it the allocator would read the date as ineligible and
    reject a commitment that may be perfectly valid, so the gap is reported here
    with the date that is missing rather than surfacing as a spurious violation.
    """

    committed: dict[date, str] = {}
    for visits in request.fixed_by_month.values():
        for visit in visits:
            committed.setdefault(visit.visit_date, visit.asset_id)

    missing = sorted(day for day in committed if day not in days)
    if missing:
        day = missing[0]
        raise InputError(
            C_COMMITMENT_DATE_METADATA_MISSING,
            (
                f"the committed visit for {committed[day]} on {day.isoformat()} "
                "needs a calendar classification for that date; a commitment in a "
                "month outside the selected range is still checked against the "
                "real calendar"
            ),
            asset_id=committed[day],
        )


def _validate_fixed_months(request: DraftRequest) -> None:
    """Reject a commitment filed under a month that does not contain its date.

    This is a structural check on the request, not a scheduling decision: a date
    belongs to exactly one month, and filing it under another would make the
    allocator's own month-keyed reconciliation read a different month than the
    caller meant.
    """

    for month, visits in request.fixed_by_month.items():
        for visit in visits:
            if (visit.visit_date.year, visit.visit_date.month) != (
                month.year,
                month.month,
            ):
                raise InputError(
                    C_FIXED_VISIT_WRONG_MONTH,
                    (
                        f"the committed visit for {visit.asset_id} on "
                        f"{visit.visit_date.isoformat()} was supplied under "
                        f"{month.label()}, which does not contain that date"
                    ),
                    asset_id=visit.asset_id,
                )


# --- generation -----------------------------------------------------------


def _allocator_result(
    request: DraftRequest,
    calendar: Calendar,
    months: tuple[MonthKey, ...],
) -> MultiMonthResult:
    """Call the allocator entry point for the requested mode.

    This is the only place generation reaches the scheduling layer, and it passes
    the caller's inputs through unchanged. Nothing here re-decides an obligation,
    a date or an outcome.
    """

    if request.mode is DraftMode.COVERAGE_CYCLE:
        assert request.cycle is not None  # validated before this point
        return run_cycle(
            roster=request.roster,
            calendar=calendar,
            config=request.config,
            cycle=request.cycle,
            seed=request.seed,
            weights=request.weights,
            prior_visits=request.prior_history,
            fixed_by_month=dict(request.fixed_by_month),
            months_to_place=months,
            rotation_offset=request.rotation_offset,
            attempt_budget=request.attempt_budget,
        )

    return run_fallback(
        roster=request.roster,
        calendar=calendar,
        config=request.config,
        months=months,
        area_required=dict(request.area_required),
        feeder_required=dict(request.feeder_required),
        dtc_target=request.dtc_target,
        seed=request.seed,
        weights=request.weights,
        prior_visits=request.prior_history,
        fixed_by_month=dict(request.fixed_by_month),
        rotation_offset=request.rotation_offset,
        attempt_budget=request.attempt_budget,
    )


_MONTH_STATUS = {
    FeasibilityStatus.FEASIBLE: DraftMonthStatus.ALLOCATION_COMPLETE,
    FeasibilityStatus.INFEASIBLE: DraftMonthStatus.INFEASIBLE,
    FeasibilityStatus.INDETERMINATE: DraftMonthStatus.UNRESOLVED,
}


def _month_exceptions(
    month: MonthKey, result
) -> tuple[DraftException, ...]:
    """Translate a month's allocator evidence without reinterpreting it."""

    kind = {
        DraftMonthStatus.INFEASIBLE: DraftExceptionKind.INFEASIBLE,
        DraftMonthStatus.UNRESOLVED: DraftExceptionKind.UNRESOLVED,
    }.get(_MONTH_STATUS[result.status])
    if kind is None:
        return ()
    return tuple(
        DraftException.from_evidence(kind, evidence, month=month)
        for evidence in result.evidence
    )


def _project_visits(
    plan_result,
    month: MonthKey,
    display: Mapping[str, AssetDisplay],
) -> tuple[tuple[GeneratedVisit, ...], tuple[GeneratedVisit, ...]]:
    """Split a month's allocator visits into fixed and newly allocated.

    Provenance comes from the allocator's own ``fixed`` input for the month, so a
    committed visit is never mistaken for one generation produced. The visits
    themselves are passed through in the allocator's order; none is moved or
    dropped.

    A month the allocator could not schedule reports no new visits, and it reports
    the commitments separately in ``fixed``. The commitments are therefore projected
    from both fields: reading only ``visits`` would silently erase every locked
    visit from a failed month, which is the one case where losing them matters
    most. Nothing is invented by doing so, because the visits were supplied by the
    caller and returned by the allocator unchanged.
    """

    fixed_commitments = {(v.asset_id, v.visit_date) for v in plan_result.fixed}

    fixed: list[GeneratedVisit] = []
    allocated: list[GeneratedVisit] = []

    def project(visit: Visit) -> GeneratedVisit:
        names = display[visit.asset_id]
        return GeneratedVisit(
            visit=visit,
            provenance=(
                VisitProvenance.FIXED
                if (visit.asset_id, visit.visit_date) in fixed_commitments
                else VisitProvenance.ALLOCATED
            ),
            month=month,
            name_en=names.name_en,
            name_kn=names.name_kn,
        )

    seen: set[tuple[str, date]] = set()
    for visit in plan_result.visits:
        seen.add((visit.asset_id, visit.visit_date))
        projected = project(visit)
        (fixed if projected.provenance is VisitProvenance.FIXED else allocated).append(
            projected
        )

    # Commitments the allocator returned only in ``fixed``, which is every
    # commitment in a month it could not schedule.
    for visit in sorted(
        plan_result.fixed, key=lambda v: (v.visit_date, v.asset_id)
    ):
        if (visit.asset_id, visit.visit_date) in seen:
            continue
        seen.add((visit.asset_id, visit.visit_date))
        fixed.append(project(visit))

    return tuple(fixed), tuple(allocated)


def _build_rows(
    request: DraftRequest,
    days: Mapping[date, CalendarDay],
    visits_by_date: Mapping[date, tuple[GeneratedVisit, ...]],
    warnings_by_date: Mapping[date, tuple[DraftException, ...]],
    entries_by_date: Mapping[date, tuple[DayEntry, ...]],
) -> tuple[DraftDayRow, ...]:
    """One ordered row per calendar date, working or not."""

    rows: list[DraftDayRow] = []
    span = (request.end - request.start).days + 1
    for offset in range(span):
        day = request.start + timedelta(days=offset)
        metadata = days[day]
        on_day = visits_by_date.get(day, ())
        rows.append(
            DraftDayRow(
                day=day,
                weekday=day.weekday(),
                kind=metadata.kind,
                is_working_day=metadata.is_working_day,
                reasons_en=metadata.reasons_en,
                reasons_kn=metadata.reasons_kn,
                fixed_visits=tuple(
                    v for v in on_day if v.provenance is VisitProvenance.FIXED
                ),
                allocated_visits=tuple(
                    v for v in on_day if v.provenance is VisitProvenance.ALLOCATED
                ),
                entries=entries_by_date.get(day, ()),
                warnings=warnings_by_date.get(day, ()),
            )
        )
    return tuple(rows)


def _activity_entries(
    request: DraftRequest, days: Mapping[date, Any]
) -> tuple[DayEntry, ...]:
    """The regular activities the catalogue places on this range's working days.

    The placement rule lives in :mod:`dailyworkreg.domain.activities`, which is free
    of I/O and of this module's projection concerns. This function only turns the
    placements into day entries, and refuses - rather than quietly under-filling a
    day or over-filling it - when the catalogue cannot meet the count the operator
    asked for.

    A working day is the caller's own decision, already projected into the calendar:
    a Sunday, a General Holiday or a leave day carries no activity, because the office
    was not working. An entry an operator or the calendar already placed with the same
    text on the same day is not duplicated.
    """

    low = request.activities_per_day_min
    high = request.activities_per_day_max
    if high <= 0:
        return ()

    eligible = [day for day in sorted(days) if days[day].is_working_day]
    try:
        placements = allocate_activities(
            days=eligible,
            activities=request.activities,
            per_day_min=low,
            per_day_max=high,
            seed=request.seed,
        )
    except ActivityInfeasible as error:
        raise InputError(
            C_ACTIVITY_ALLOCATION_INFEASIBLE,
            (
                f"between {low} and {high} activities a day cannot be placed: "
                f"{error.detail}. Lower the daily minimum or maximum, widen the "
                "catalogue, or extend the selected range"
            ),
        ) from error

    already: set[tuple[date, str]] = {
        (entry.day, entry.title_en.strip().casefold())
        for entry in request.entries
        if entry.kind is DayEntryKind.ACTIVITY
    }
    entries: list[DayEntry] = []
    for placement in placements:
        key = (placement.day, placement.activity.title_en.strip().casefold())
        if key in already:
            continue
        entries.append(
            DayEntry(
                entry_id=f"ACT-{placement.day.isoformat()}-{placement.activity.activity_id}",
                day=placement.day,
                kind=DayEntryKind.ACTIVITY,
                title_en=placement.activity.title_en,
                title_kn=placement.activity.title_kn,
                provenance=DayEntryProvenance.GENERATED,
            )
        )
    return tuple(entries)


def generate_draft(request: DraftRequest) -> DraftRegister:
    """Generate an allocator-backed draft register for the selected months.

    The request is validated first, then handed to :func:`run_cycle` or
    :func:`run_fallback`, and the allocator's result is projected into one row per
    calendar date. Every month keeps its own outcome: a month the allocator proved
    infeasible stays infeasible, and a month whose bounded search ended without a
    verdict stays unresolved, which is never reported as an empty success.

    Args:
        request: The complete typed generation request.

    Returns:
        A :class:`DraftRegister`. Its ``status`` describes field-visit allocation
        only; ``missing_content`` names the masters that stop it being a complete
        operational diary.

    Raises:
        InputError: The request is malformed for the selected mode, the range is
            not a run of complete calendar months, or a required input is missing.
            A malformed request never becomes a draft.
    """

    months = _validate_range(request)
    _validate_language(request)
    display = _validate_roster(request)
    days = _validate_calendar_days(request)
    _validate_entries(request)
    _validate_mode_inputs(request, months)
    _validate_fixed_months(request)
    _validate_commitment_calendar(request, days)

    working_days = frozenset(
        day for day, metadata in days.items() if metadata.is_working_day
    )
    calendar = Calendar(working_days)

    result = _allocator_result(request, calendar, months)

    draft_months: list[DraftMonth] = []
    visits_by_date: dict[date, list[GeneratedVisit]] = {}
    exceptions: list[DraftException] = []

    for plan in result.months:
        fixed, allocated = _project_visits(plan.result, plan.month, display)
        status = _MONTH_STATUS[plan.result.status]
        month_exceptions = _month_exceptions(plan.month, plan.result)
        exceptions.extend(month_exceptions)
        draft_months.append(
            DraftMonth(
                month=plan.month,
                period=plan.period,
                status=status,
                allocator_status=plan.result.status,
                obligations=dict(plan.obligations),
                declared_obligations=dict(plan.declared_obligations),
                visits=fixed + allocated,
                evidence=month_exceptions,
            )
        )
        for projected in fixed + allocated:
            visits_by_date.setdefault(projected.visit_date, []).append(projected)

    # Over-coverage is a resolved exception, not a licence to remove a commitment.
    # It is reported even when the overall allocation succeeded, because a cycle
    # can satisfy its remaining obligations and still hold surplus commitments.
    for evidence in result.over_covered:
        exceptions.append(
            DraftException.from_evidence(DraftExceptionKind.OVER_COVERED, evidence)
        )

    entries_by_date: dict[date, list[DayEntry]] = {}
    for entry in request.entries:
        entries_by_date.setdefault(entry.day, []).append(entry)
    for entry in _activity_entries(request, days):
        entries_by_date.setdefault(entry.day, []).append(entry)

    rows = _build_rows(
        request,
        days,
        {day: tuple(group) for day, group in visits_by_date.items()},
        {},
        {day: tuple(group) for day, group in entries_by_date.items()},
    )

    ordered_visits = tuple(
        visit for row in rows for visit in row.fixed_visits + row.allocated_visits
    )

    # The overall outcome is taken from the allocator's own worst-month precedence
    # (a proof of infeasibility outranks an unsettled month, which outranks success)
    # rather than recomputed here. Two orderings of the same three outcomes is how
    # one surface ends up calling a run unresolved while another calls it
    # impossible.
    status = DraftStatus(_MONTH_STATUS[result.status].value)

    snapshot = GenerationSnapshot(
        allocator_version=result.allocator_version,
        generator_version=GENERATOR_VERSION,
        mode=request.mode,
        seed=request.seed,
        start=request.start,
        end=request.end,
        months=months,
        rotation_offset=request.rotation_offset,
        attempt_budget=request.attempt_budget,
        roster_fingerprint=_fingerprint(
            tuple(sorted((a.asset_id, a.asset_type.value, a.active) for a in request.roster))
        ),
        config_fingerprint=_fingerprint(
            request.config.combined_daily_cap,
            request.config.area_daily_cap,
            request.config.default_asset_limits,
            tuple(sorted(request.config.asset_limits.items())),
            request.config.search_node_limit,
        ),
        calendar_fingerprint=_fingerprint(
            tuple(sorted((d.isoformat(), m.kind.value, m.is_working_day) for d, m in days.items()))
        ),
        display_fingerprint=_fingerprint(
            tuple(sorted((d.asset_id, d.name_en, d.name_kn) for d in request.display))
        ),
        cycle=request.cycle,
        cycle_resolution=result.cycle_resolution,
        prior_history=tuple(request.prior_history),
        committed_visits=tuple(result.committed_visits),
    )

    return DraftRegister(
        status=status,
        mode=request.mode,
        start=request.start,
        end=request.end,
        seed=request.seed,
        generator_version=GENERATOR_VERSION,
        allocator_version=result.allocator_version,
        months=tuple(draft_months),
        rows=rows,
        visits=ordered_visits,
        exceptions=tuple(exceptions),
        warnings=(),
        snapshot=snapshot,
        missing_content=MISSING_CONTENT,
        entry_language=request.entry_language,
    )


#: Masters the platform still expects but does not itself supply, so a generated
#: draft is an allocator-backed register and not a complete operational diary.
#: Naming them on every result is deliberate: a caller cannot mistake allocation
#: success for a finished register if the result says what is still absent. The
#: activity catalogue is no longer listed here because it is imported from the
#: office's own approved sheet and the generator now places it.
MISSING_CONTENT: tuple[str, ...] = (
    "Monday safety-meeting schedule and other dated calendar events",
    "official Karnataka General Holiday baseline for 2025 and 2026",
    "source 113-SCD roster and asset master data",
)
