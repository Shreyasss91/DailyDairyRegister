"""A leave date says so in the register, and a month of leave is a month it can hold.

The office asked for one thing in two halves: a leave date must carry the sentence the
office files - "I was on leave this day (Full Pay Leave)" - and a month on leave from end
to end must not stop a register being generated. The second half follows from the first
only if the month owes nothing, so this module also pins the cycle rule that decides it:
a month holding no working date carries no share of the cycle, and its share is dealt to
the months that can hold work.

Every fixture is synthetic. No real leave record, holiday or roster is read here.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from pathlib import Path

import pytest
from dailyworkreg.application import (
    AssetDisplay,
    CalendarDay,
    CalendarDayKind,
    DayEntryKind,
    DraftMode,
    DraftMonthStatus,
    DraftRequest,
    DraftStatus,
    generate_draft,
    snapshot,
)
from dailyworkreg.domain import wording
from dailyworkreg.domain.cycle import (
    CoverageCycle,
    CycleResolution,
    MonthKey,
    resolve_cycle,
    verify_cycle,
)
from dailyworkreg.domain.validator import (
    C_NO_ELIGIBLE_WORKING_DATE,
    Asset,
    AssetType,
    Calendar,
    FeasibilityConfig,
    FeasibilityStatus,
)

NOV = MonthKey(2026, 11)
DEC = MonthKey(2026, 12)

DTCS = (
    Asset("SCD-001", AssetType.DTC),
    Asset("SCD-002", AssetType.DTC),
    Asset("SCD-003", AssetType.DTC),
)


# --- fixtures -------------------------------------------------------------


def display_for(assets=DTCS) -> tuple[AssetDisplay, ...]:
    return tuple(AssetDisplay(a.asset_id, a.asset_id) for a in assets)


def days_in_month(month: MonthKey) -> tuple[date, date]:
    start = date(month.year, month.month, 1)
    next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return start, next_month - timedelta(days=1)


def working_calendar(start: date, end: date) -> tuple[CalendarDay, ...]:
    """Weekdays only, so a month holds enough dates for a small roster."""

    out = []
    day = start
    while day <= end:
        if day.weekday() >= 5:
            out.append(CalendarDay(day, CalendarDayKind.SUNDAY, False))
        else:
            out.append(CalendarDay(day, CalendarDayKind.WORKING, True))
        day += timedelta(days=1)
    return tuple(out)


def leave_calendar(start: date, end: date, leave_type="Full Pay Leave"):
    """Every date on leave, which is what a full-month leave sheet projects to."""

    out = []
    day = start
    while day <= end:
        out.append(
            CalendarDay(
                day,
                CalendarDayKind.LEAVE,
                False,
                reasons_en=(leave_type,),
            )
        )
        day += timedelta(days=1)
    return tuple(out)


def request_over(months: tuple[MonthKey, ...], **overrides) -> DraftRequest:
    start, _ = days_in_month(months[0])
    _, end = days_in_month(months[-1])
    roster = DTCS + (Asset("AREA-01", AssetType.AREA),)
    base = {
        "start": start,
        "end": end,
        "mode": DraftMode.COVERAGE_CYCLE,
        "roster": roster,
        "display": display_for(roster),
        "calendar_days": working_calendar(start, end),
        "config": FeasibilityConfig(),
        "seed": 0,
        "cycle": CoverageCycle(months[0], len(months), 1),
        "activities_per_day_max": 0,
    }
    base.update(overrides)
    return DraftRequest(**base)


# --- the sentence ---------------------------------------------------------


def test_the_sentence_is_the_office_s_own_and_names_no_leave_type():
    # The leave type is stated by the date's stored reason, which prints beside this
    # sentence as "Full Pay Leave (Leave)". Naming it in both places reads as two facts
    # where there is one, so the sentence carries none of them.
    assert wording.LEAVE_ENTRY_TEXT == "I was on leave this day"
    assert "(" not in wording.LEAVE_ENTRY_TEXT


# --- the register row -----------------------------------------------------


def test_a_leave_date_carries_the_sentence_in_its_activity_cell():
    start, end = days_in_month(NOV)
    calendar = [
        CalendarDay(
            day,
            CalendarDayKind.LEAVE,
            False,
            reasons_en=("Casual Leave",),
        )
        if day == date(2026, 11, 5)
        else CalendarDay(day, CalendarDayKind.WORKING, True)
        for day in (start + timedelta(days=offset) for offset in range((end - start).days + 1))
    ]

    draft = generate_draft(request_over((NOV,), calendar_days=tuple(calendar)))

    row = next(r for r in draft.rows if r.day == date(2026, 11, 5))
    leave_entries = [e for e in row.entries if e.kind is DayEntryKind.LEAVE]
    assert len(leave_entries) == 1
    assert leave_entries[0].title_en == "I was on leave this day"
    assert leave_entries[0].entry_id == "LEAVE-2026-11-05"
    assert leave_entries[0].provenance == "calendar"
    assert leave_entries[0].title_kn is None


def test_a_working_date_carries_no_leave_sentence():
    draft = generate_draft(request_over((NOV,)))
    for row in draft.rows:
        assert not [e for e in row.entries if e.kind is DayEntryKind.LEAVE]


def test_a_holiday_is_not_written_as_leave():
    # Leave is a decision the calendar records against a person; a holiday closes the
    # section for everybody, and claiming an absence on it would be a different fact.
    if NOV.month == 1:
        raise AssertionError("the fixture month must not be January")
    start, end = days_in_month(NOV)
    calendar = [
        CalendarDay(day, CalendarDayKind.GENERAL_HOLIDAY, False, reasons_en=("Rajyotsava",))
        if day == date(2026, 11, 1)
        else CalendarDay(day, CalendarDayKind.WORKING, True)
        for day in (start + timedelta(days=offset) for offset in range((end - start).days + 1))
    ]
    draft = generate_draft(request_over((NOV,), calendar_days=tuple(calendar)))
    row = next(r for r in draft.rows if r.day == date(2026, 11, 1))
    assert row.entries == ()


def test_every_date_of_a_leave_month_carries_the_sentence():
    start, end = days_in_month(NOV)
    draft = generate_draft(
        request_over((NOV,), calendar_days=leave_calendar(start, end))
    )

    assert len(draft.rows) == (end - start).days + 1
    for row in draft.rows:
        assert [e.title_en for e in row.entries] == ["I was on leave this day"]


# --- a month of leave is a month that can be generated -------------------


def test_a_month_of_leave_generates_with_no_visits_and_its_share_moves_on():
    """The recorded defect: one month of leave stopped the whole range.

    The office generated a range whose October was on leave from end to end, and the run
    came back infeasible for that month alone, because the cycle owed it a share no date
    could discharge. The month now carries no obligation, so it is a month the register
    holds - every date stating the leave - and the visits it would have carried are dealt
    to the month that can work.
    """

    nov_start, nov_end = days_in_month(NOV)
    dec_start, dec_end = days_in_month(DEC)
    calendar = leave_calendar(nov_start, nov_end) + working_calendar(dec_start, dec_end)

    draft = generate_draft(request_over((NOV, DEC), calendar_days=calendar))

    assert draft.status is DraftStatus.ALLOCATION_COMPLETE
    november, december = draft.months
    assert november.status is DraftMonthStatus.ALLOCATION_COMPLETE
    assert november.visits == ()
    assert sum(november.obligations.values()) == 0
    assert december.status is DraftMonthStatus.ALLOCATION_COMPLETE
    assert len(december.visits) == len(DTCS)
    assert draft.exceptions == ()


def test_the_generated_leave_month_still_reports_a_feasible_status_per_month():
    nov_start, nov_end = days_in_month(NOV)
    dec_start, dec_end = days_in_month(DEC)
    calendar = leave_calendar(nov_start, nov_end) + working_calendar(dec_start, dec_end)

    draft = generate_draft(request_over((NOV, DEC), calendar_days=calendar))

    assert [m.status for m in draft.months] == [
        DraftMonthStatus.ALLOCATION_COMPLETE,
        DraftMonthStatus.ALLOCATION_COMPLETE,
    ]


# --- the cycle rule ------------------------------------------------------


def test_a_month_holding_no_working_date_carries_no_share_and_the_total_is_unchanged():
    dec_start, dec_end = days_in_month(DEC)
    resolution = resolve_cycle(
        DTCS + (Asset("AREA-01", AssetType.AREA),),
        CoverageCycle(NOV, 2, 1),
        calendar=Calendar(
            frozenset(
                d.day for d in working_calendar(dec_start, dec_end) if d.is_working_day
            )
        ),
    )

    assert resolution.plan_for(NOV).total == 0
    assert resolution.plan_for(DEC).total == len(DTCS)
    assert resolution.closed_months == (NOV,)
    assert sum(plan.total for plan in resolution.plans) == len(DTCS)
    assert verify_cycle(resolution) == ()


def test_a_closed_month_that_owes_visits_fails_verification():
    dec_start, dec_end = days_in_month(DEC)
    resolution = resolve_cycle(
        DTCS,
        CoverageCycle(NOV, 2, 1),
        calendar=Calendar(
            frozenset(
                d.day for d in working_calendar(dec_start, dec_end) if d.is_working_day
            )
        ),
    )
    plans = tuple(
        dataclasses.replace(plan, obligations={"SCD-001": 1}, total=1, scheduled=("SCD-001",))
        if plan.month == NOV
        else plan
        for plan in resolution.plans
    )
    broken = dataclasses.replace(resolution, plans=plans)

    problems = verify_cycle(broken)
    assert any("holds no working date" in problem for problem in problems)


def test_a_cycle_with_no_working_month_anywhere_keeps_its_obligation():
    # Nothing anywhere can discharge the cycle, so it must not be quietly redistributed to
    # nothing: the month is reported as an empty month and the obligation stays visible.
    resolution = resolve_cycle(
        DTCS, CoverageCycle(NOV, 2, 1), calendar=Calendar(frozenset())
    )

    assert resolution.closed_months == ()
    assert sum(plan.total for plan in resolution.plans) == len(DTCS)
    assert verify_cycle(resolution) == ()


def test_a_cycle_resolved_without_a_calendar_distributes_as_before():
    # The plain division is the contract a caller without a calendar already relies on.
    resolution = resolve_cycle(DTCS, CoverageCycle(NOV, 3, 1))

    assert [plan.total for plan in resolution.plans] == [1, 1, 1]
    assert resolution.closed_months == ()
    assert verify_cycle(resolution) == ()


def test_the_closed_month_is_recorded_on_the_resolution_not_looked_up_again():
    # A stored plan is verified against the facts it was built from, so the closed months
    # travel with it rather than being re-derived from a calendar that may have changed.
    dec_start, dec_end = days_in_month(DEC)
    resolution = resolve_cycle(
        DTCS,
        CoverageCycle(NOV, 2, 1),
        calendar=Calendar(
            frozenset(
                d.day for d in working_calendar(dec_start, dec_end) if d.is_working_day
            )
        ),
    )
    assert isinstance(resolution, CycleResolution)
    assert resolution.closed_months == (NOV,)


# --- the snapshot --------------------------------------------------------


def test_a_snapshot_that_still_carries_leave_types_still_reads():
    # A run generated while the sentence named the leave type wrote ``leave_types`` into its
    # snapshot. The field is gone; a payload that still holds it is read and the extra key is
    # ignored rather than refused, so such a run can be replayed.
    start, end = days_in_month(NOV)
    payload = snapshot.encode_request(request_over((NOV,)))
    for item in payload["calendar_days"]:
        item["leave_types"] = ["Full Pay Leave"]

    restored = snapshot.decode_request(payload)

    assert {d.day for d in restored.calendar_days} == {
        start + timedelta(days=offset) for offset in range((end - start).days + 1)
    }
    assert not hasattr(restored.calendar_days[0], "leave_types")


def test_the_client_offers_no_controls_on_a_line_the_calendar_wrote():
    """A leave line is projected again on every generation, so editing it is theatre.

    The per-entry controls convert a kind through a select that knows only the entry kinds a
    person may type, and leave is deliberately not one of them: leave is the calendar's
    record. The register therefore shows the sentence and no edit, remove or move control,
    and the operator changes the leave on the Calendar tab where it is stored.
    """

    page = (
        Path(__file__).resolve().parents[1]
        / "dailyworkreg/interfaces/static/app.js"
    ).read_text(encoding="utf-8")

    assert "entry.kind === 'leave' ? null : entryControls(row, entry)" in page


def test_the_activity_cell_reads_the_sentence_and_then_the_reason():
    """The one line the office sees on a leave date, pinned as a whole.

    It is the leave sentence, then the date's stored reason in the calendar's vocabulary -
    "I was on leave this day, Full Pay Leave (Leave)" - and the type appears exactly once.
    """

    from dailyworkreg.application.export import DocumentDay, DocumentEntry

    day = DocumentDay(
        day="2026-11-05",
        weekday="Thursday",
        kind="leave",
        kind_label="Leave",
        working=False,
        reasons_en=("Full Pay Leave",),
        reasons_kn=("ರಜೆ",),
        warnings=(),
        visits=(),
        entries=(
            DocumentEntry(
                entry_id="LEAVE-2026-11-05",
                kind="leave",
                kind_label="Leave",
                title_en=wording.LEAVE_ENTRY_TEXT,
                title_kn=None,
            ),
        ),
        locked=False,
    )

    assert day.activity_text == "I was on leave this day, Full Pay Leave (Leave)"
    assert day.activity_text.count("Full Pay Leave") == 1
