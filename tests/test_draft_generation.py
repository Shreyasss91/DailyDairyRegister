"""Draft-register generation: allocator integration and date-row projection.

The suite proves two things at once: that a draft faithfully presents what the
allocator decided, and that the register layer makes no scheduling decision of its
own. Where a test can compare the draft against the allocator result directly, it
does, because "looks plausible" is not evidence that nothing was changed.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import dailyworkreg.application.draft_generation as draft_generation
import pytest
from dailyworkreg.application import (
    GENERATOR_VERSION,
    AssetDisplay,
    CalendarDay,
    CalendarDayKind,
    DayEntry,
    DayEntryKind,
    DraftExceptionKind,
    DraftMode,
    DraftMonthStatus,
    DraftRequest,
    DraftStatus,
    VisitProvenance,
    generate_draft,
)
from dailyworkreg.application.draft_generation import (
    C_COMMITMENT_DATE_METADATA_MISSING,
    C_CYCLE_REQUIRED,
    C_DATE_METADATA_DUPLICATE,
    C_DATE_METADATA_INCONSISTENT,
    C_DATE_METADATA_MISSING,
    C_DISPLAY_METADATA_DUPLICATE,
    C_DISPLAY_METADATA_MISSING,
    C_DISPLAY_METADATA_UNKNOWN_ASSET,
    C_ENTRY_OUTSIDE_RANGE,
    C_FALLBACK_CONFLICT,
    C_FIXED_VISIT_WRONG_MONTH,
    C_MODE_INPUT_CONFLICT,
    C_PARTIAL_SELECTION,
    C_RANGE_OUTSIDE_CYCLE,
    C_RANGE_REVERSED,
    C_ROSTER_REQUIRED,
)
from dailyworkreg.domain.allocator import AllocationResult
from dailyworkreg.domain.cycle import CoverageCycle, MonthKey
from dailyworkreg.domain.targeting import MonthPlan, MultiMonthResult
from dailyworkreg.domain.validator import (
    Asset,
    AssetType,
    Calendar,
    DatePeriod,
    Evidence,
    FeasibilityConfig,
    FeasibilityStatus,
    InputError,
    ProposalContext,
    ValidationMode,
    Visit,
    validate_schedule,
)

NOV = MonthKey(2026, 11)
DEC = MonthKey(2026, 12)


# --- fixtures -------------------------------------------------------------


def roster() -> tuple[Asset, ...]:
    return (
        Asset("SCD-001", AssetType.DTC),
        Asset("SCD-002", AssetType.DTC),
        Asset("SCD-003", AssetType.DTC),
        Asset("AREA-01", AssetType.AREA),
        Asset("FEEDER-01", AssetType.FEEDER),
    )


def display_for(assets=None) -> tuple[AssetDisplay, ...]:
    assets = assets or roster()
    return tuple(
        AssetDisplay(a.asset_id, a.asset_id.replace("-", " ").title()) for a in assets
    )


def _is_second_saturday(day: date) -> bool:
    return day.weekday() == 5 and 8 <= day.day <= 14


def _is_fourth_saturday(day: date) -> bool:
    return day.weekday() == 5 and 22 <= day.day <= 28


def calendar_days(start: date, end: date, holidays=()) -> tuple[CalendarDay, ...]:
    """A weekday calendar with Sundays and second Saturdays off.

    Metres deliberately kept explicit: the layer displays what it is given and is
    never asked to infer a weekly off or a holiday.
    """

    holidays = set(holidays)
    out = []
    day = start
    while day <= end:
        if day in holidays:
            out.append(
                CalendarDay(
                    day,
                    CalendarDayKind.GENERAL_HOLIDAY,
                    False,
                    reasons_en=("Karnataka Rajyotsava",),
                    reasons_kn=("ಕರ್ನಾಟಕ ರಾಜ್ಯೋತ್ಸವ",),
                )
            )
        elif day.weekday() == 6:
            out.append(CalendarDay(day, CalendarDayKind.SUNDAY, False))
        elif _is_second_saturday(day):
            out.append(CalendarDay(day, CalendarDayKind.SECOND_SATURDAY, False))
        elif _is_fourth_saturday(day):
            out.append(CalendarDay(day, CalendarDayKind.FOURTH_SATURDAY, False))
        else:
            out.append(CalendarDay(day, CalendarDayKind.WORKING, True))
        day += timedelta(days=1)
    return tuple(out)


def november() -> tuple[date, date]:
    return date(2026, 11, 1), date(2026, 11, 30)


def cycle_request(**overrides) -> DraftRequest:
    start, end = november()
    base = {
        "start": start,
        "end": end,
        "mode": DraftMode.COVERAGE_CYCLE,
        "roster": roster(),
        "display": display_for(),
        "calendar_days": calendar_days(start, end),
        "config": FeasibilityConfig(),
        "seed": 7,
        "cycle": CoverageCycle(NOV, 1, 1),
    }
    base.update(overrides)
    return DraftRequest(**base)


def fallback_request(**overrides) -> DraftRequest:
    start, end = november()
    base = {
        "start": start,
        "end": end,
        "mode": DraftMode.CAPACITY_FALLBACK,
        "roster": roster(),
        "display": display_for(),
        "calendar_days": calendar_days(start, end),
        "config": FeasibilityConfig(),
        "seed": 7,
        "dtc_target": 2,
    }
    base.update(overrides)
    return DraftRequest(**base)


# --- allocation integration ----------------------------------------------


def test_cycle_mode_preserves_the_allocator_month_result():
    request = cycle_request()
    draft = generate_draft(request)

    assert draft.mode is DraftMode.COVERAGE_CYCLE
    assert len(draft.months) == 1
    month = draft.months[0]
    assert month.allocator_status is FeasibilityStatus.FEASIBLE
    assert month.status is DraftMonthStatus.ALLOCATION_COMPLETE
    # Three DTCs owe one visit each in a one-month cycle.
    assert sum(month.obligations.values()) == 3


def test_fallback_mode_uses_the_fallback_entry_point():
    draft = generate_draft(fallback_request())
    assert draft.mode is DraftMode.CAPACITY_FALLBACK
    assert draft.months[0].declared_obligations == {}
    # A fallback run is a suggestion, so it must not manufacture a per-DTC
    # obligation for the whole roster.
    assert len(draft.months[0].obligations) <= len(roster())


def test_the_draft_adds_no_visit_the_allocator_did_not_return(monkeypatch):
    captured = {}
    real = draft_generation.run_cycle

    def capture(*args, **kwargs):
        result = real(*args, **kwargs)
        captured["result"] = result
        return result

    monkeypatch.setattr(draft_generation, "run_cycle", capture)
    draft = generate_draft(cycle_request())

    allocator_visits = captured["result"].all_visits
    assert draft.visits, "expected the allocator to place something"
    assert tuple(v.visit for v in draft.visits) == allocator_visits


def test_the_draft_projects_each_allocator_visit_exactly_once(monkeypatch):
    captured = {}
    real = draft_generation.run_cycle

    def capture(*args, **kwargs):
        captured["result"] = real(*args, **kwargs)
        return captured["result"]

    monkeypatch.setattr(draft_generation, "run_cycle", capture)
    draft = generate_draft(cycle_request())

    from_rows = [v.visit for row in draft.rows for v in row.visits]
    assert sorted(from_rows) == sorted(captured["result"].all_visits)
    assert len(from_rows) == len(set(from_rows))


def test_fixed_visits_appear_once_on_their_actual_dates():
    committed = Visit("SCD-001", date(2026, 11, 4))
    draft = generate_draft(
        cycle_request(fixed_by_month={NOV: (committed,)})
    )

    row = draft.row_for(date(2026, 11, 4))
    assert row is not None
    assert [v.visit for v in row.fixed_visits] == [committed]
    # And nowhere else.
    occurrences = [
        (r.day, v.provenance) for r in draft.rows for v in r.visits
        if v.visit == committed
    ]
    assert occurrences == [(date(2026, 11, 4), VisitProvenance.FIXED)]


def test_unselected_month_commitments_do_not_leak_into_another_month():
    other = Visit("SCD-001", date(2026, 12, 4))
    start, end = november()
    draft = generate_draft(
        cycle_request(
            # December is outside the selected range, but the cycle spans it.
            cycle=CoverageCycle(NOV, 2, 1),
            fixed_by_month={DEC: (other,)},
            calendar_days=calendar_days(start, date(2026, 12, 31)),
        )
    )
    projected = [v.visit for v in draft.visits]
    assert other not in projected
    assert draft.row_for(date(2026, 12, 4)) is None
    # It is not silently lost either: the allocator was told about it.
    assert other in draft.snapshot.committed_visits


def test_prior_history_and_commitments_reach_the_allocator(monkeypatch):
    captured = {}
    real = draft_generation.run_cycle

    def capture(*args, **kwargs):
        captured["history"] = kwargs.get("prior_visits")
        captured["fixed"] = kwargs.get("fixed_by_month")
        return real(*args, **kwargs)

    monkeypatch.setattr(draft_generation, "run_cycle", capture)
    history = (Visit("SCD-001", date(2026, 10, 20)),)
    committed = Visit("SCD-002", date(2026, 11, 4))
    generate_draft(
        cycle_request(prior_history=history, fixed_by_month={NOV: (committed,)})
    )
    assert captured["history"] == history
    assert captured["fixed"] == {NOV: (committed,)}


def test_declared_and_effective_obligations_are_both_preserved():
    committed = Visit("SCD-001", date(2026, 11, 4))
    draft = generate_draft(cycle_request(fixed_by_month={NOV: (committed,)}))
    month = draft.months[0]
    # The cycle declares one visit per DTC; the commitment discharges SCD-001, so
    # the effective obligation no longer asks for it.
    assert month.declared_obligations.get("SCD-001") == 1
    assert "SCD-001" not in month.obligations
    assert month.obligations.get("SCD-002") == 1


def test_cycle_metadata_is_preserved_on_the_snapshot():
    request = cycle_request(cycle=CoverageCycle(NOV, 2, 1))
    start, end = date(2026, 11, 1), date(2026, 12, 31)
    request = dataclasses.replace(
        request, start=start, end=end, calendar_days=calendar_days(start, end)
    )
    draft = generate_draft(request)
    assert draft.snapshot.cycle == CoverageCycle(NOV, 2, 1)
    assert draft.snapshot.cycle_resolution is not None
    assert draft.snapshot.cycle_resolution.cycle.visits_per_asset == 1


# --- date-row projection --------------------------------------------------


def test_a_complete_month_produces_one_row_per_calendar_date():
    draft = generate_draft(cycle_request())
    assert len(draft.rows) == 30
    assert [r.day for r in draft.rows] == [
        date(2026, 11, 1) + timedelta(days=i) for i in range(30)
    ]


def test_rows_are_chronological_across_months():
    start, end = date(2026, 11, 1), date(2026, 12, 31)
    request = cycle_request(
        start=start,
        end=end,
        cycle=CoverageCycle(NOV, 2, 1),
        calendar_days=calendar_days(start, end),
    )
    draft = generate_draft(request)
    days = [r.day for r in draft.rows]
    assert days == sorted(days)
    assert len(days) == 61


def test_sundays_and_supplied_holidays_and_leave_get_rows():
    leave_day = date(2026, 11, 18)
    days = list(calendar_days(*november(), holidays=(date(2026, 11, 3),)))
    days = [
        dataclasses.replace(d, kind=CalendarDayKind.LEAVE, is_working_day=False,
                            reasons_en=("Earned leave",), reasons_kn=("ಅರಜೆ",))
        if d.day == leave_day
        else d
        for d in days
    ]
    draft = generate_draft(cycle_request(calendar_days=tuple(days)))

    sunday = draft.row_for(date(2026, 11, 1))
    assert sunday is not None and sunday.kind is CalendarDayKind.SUNDAY
    assert sunday.is_working_day is False

    holiday = draft.row_for(date(2026, 11, 3))
    assert holiday is not None
    assert holiday.kind is CalendarDayKind.GENERAL_HOLIDAY
    assert holiday.reasons_en == ("Karnataka Rajyotsava",)
    assert holiday.reasons_kn == ("ಕರ್ನಾಟಕ ರಾಜ್ಯೋತ್ಸವ",)

    leave = draft.row_for(leave_day)
    assert leave is not None and leave.kind is CalendarDayKind.LEAVE

    second_sat = draft.row_for(date(2026, 11, 14))
    assert second_sat is not None
    assert second_sat.kind is CalendarDayKind.SECOND_SATURDAY


def test_working_classification_follows_supplied_inputs_only():
    """A day the caller calls working is working, even on a Sunday."""

    sunday = date(2026, 11, 8)
    days = [
        dataclasses.replace(d, kind=CalendarDayKind.WORKING, is_working_day=True)
        if d.day == sunday
        else d
        for d in calendar_days(*november())
    ]
    draft = generate_draft(cycle_request(calendar_days=tuple(days)))
    row = draft.row_for(sunday)
    assert row is not None and row.is_working_day is True
    assert row.kind is CalendarDayKind.WORKING


def test_dates_without_visits_still_produce_rows():
    draft = generate_draft(cycle_request())
    empty = [r for r in draft.rows if r.is_empty]
    assert empty, "a month with three visits must still have empty rows"
    assert all(r.day for r in empty)


def test_visits_land_on_the_correct_date_and_asset():
    committed = Visit("AREA-01", date(2026, 11, 9))
    draft = generate_draft(cycle_request(fixed_by_month={NOV: (committed,)}))
    row = draft.row_for(date(2026, 11, 9))
    assert row is not None
    assert [v.asset_id for v in row.fixed_visits] == ["AREA-01"]
    assert row.fixed_visits[0].name_en == "Area 01"
    # No other row holds it.
    assert sum(1 for r in draft.rows for v in r.visits if v.asset_id == "AREA-01") == 1


def test_provenance_distinguishes_fixed_from_allocated():
    committed = Visit("SCD-001", date(2026, 11, 4))
    draft = generate_draft(cycle_request(fixed_by_month={NOV: (committed,)}))
    provenances = {v.provenance for v in draft.visits}
    assert VisitProvenance.FIXED in provenances
    assert VisitProvenance.ALLOCATED in provenances


def test_multiple_visit_categories_on_one_date_do_not_change_the_result():
    day = date(2026, 11, 5)
    committed = (
        Visit("SCD-001", day),
        Visit("AREA-01", day),
    )
    draft = generate_draft(cycle_request(fixed_by_month={NOV: committed}))
    row = draft.row_for(day)
    assert row is not None
    assert {v.asset_id for v in row.fixed_visits} == {"SCD-001", "AREA-01"}
    # The combined daily cap is 3, so two is legitimate and neither was moved.
    assert all(v.visit_date == day for v in row.fixed_visits)


def test_injected_entries_are_projected_without_touching_visits():
    day = date(2026, 11, 10)
    entry = DayEntry("EV-1", day, DayEntryKind.SAFETY_MEETING, "Monday safety meeting")
    draft = generate_draft(cycle_request(entries=(entry,)))

    row = draft.row_for(day)
    assert row is not None
    assert row.entries == (entry,)
    # An event is not a visit and consumes no field-visit capacity.
    assert row.visits == ()


def test_missing_date_metadata_is_rejected():
    days = calendar_days(*november())[:-1]
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(calendar_days=days))
    assert caught.value.constraint == C_DATE_METADATA_MISSING


def test_duplicate_date_metadata_is_rejected():
    days = calendar_days(*november())
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(calendar_days=days + (days[0],)))
    assert caught.value.constraint == C_DATE_METADATA_DUPLICATE


def test_date_metadata_for_an_unselected_month_is_accepted_but_not_projected():
    start, end = november()
    days = calendar_days(start, date(2026, 12, 31))
    draft = generate_draft(cycle_request(calendar_days=days))
    # Only the selected month is projected, even though December was described.
    assert all(r.day <= end for r in draft.rows)
    assert len(draft.rows) == 30


def test_a_commitment_without_calendar_metadata_is_rejected():
    committed = Visit("SCD-001", date(2026, 12, 4))
    with pytest.raises(InputError) as caught:
        generate_draft(
            cycle_request(
                cycle=CoverageCycle(NOV, 2, 1),
                fixed_by_month={DEC: (committed,)},
            )
        )
    assert caught.value.constraint == C_COMMITMENT_DATE_METADATA_MISSING
    assert caught.value.asset_id == "SCD-001"


def test_contradictory_day_classification_is_rejected():
    with pytest.raises(InputError) as caught:
        CalendarDay(date(2026, 11, 1), CalendarDayKind.SUNDAY, True)
    assert caught.value.constraint == C_DATE_METADATA_INCONSISTENT


# --- outcome semantics ----------------------------------------------------


def test_feasible_month_is_allocation_complete_and_validates():
    request = cycle_request()
    draft = generate_draft(request)
    month = draft.months[0]
    assert month.status is DraftMonthStatus.ALLOCATION_COMPLETE
    assert draft.status is DraftStatus.ALLOCATION_COMPLETE

    # The projected month is shown to originate from a valid allocator result:
    # replaying the projected visits through the shared hard-constraint path must
    # find nothing wrong.
    validated = validate_schedule(
        ProposalContext(
            period=month.period,
            calendar=Calendar(
                frozenset(d.day for d in request.calendar_days if d.is_working_day)
            ),
            roster=request.roster,
            config=FeasibilityConfig(),
            proposed=tuple(v.visit for v in month.visits),
            cycle_obligations=dict(month.declared_obligations),
            fixed=tuple(
                v.visit
                for v in month.visits
                if v.provenance is VisitProvenance.FIXED
            ),
        ),
        ValidationMode.OBLIGATIONS,
    )
    assert validated.valid, validated.violations


def test_a_genuinely_infeasible_month_is_reported_as_infeasible():
    """An obligation above the monthly maximum is a proven impossible month."""

    start, end = november()
    areas = (Asset("AREA-01", AssetType.AREA),)
    draft = generate_draft(
        DraftRequest(
            start=start,
            end=end,
            mode=DraftMode.CAPACITY_FALLBACK,
            roster=areas,
            display=display_for(areas),
            calendar_days=calendar_days(start, end),
            config=FeasibilityConfig(),
            seed=1,
            # No asset may be visited more than three times in a month, so asking
            # for ten is impossible and the allocator proves it.
            area_required={NOV: {"AREA-01": 10}},
            dtc_target=0,
        )
    )

    month = draft.months[0]
    assert month.status is DraftMonthStatus.INFEASIBLE
    assert month.allocator_status is FeasibilityStatus.INFEASIBLE
    assert draft.status is DraftStatus.INFEASIBLE
    assert month.is_allocation_complete is False
    # The reason travels as a structured exception, not as prose. This constraint
    # is raised by the aggregate assessment, whose evidence carries no single asset
    # identity, so the fields that are available are the ones asserted; nothing is
    # recovered by parsing the human-readable detail.
    assert month.evidence
    assert month.evidence[0].constraint == "asset_monthly_max_exceeded"
    assert month.evidence[0].limit == 3
    assert month.evidence[0].actual == 10
    assert month.evidence[0].detail

def test_a_failed_month_still_projects_its_committed_visits():
    """Losing a locked visit is worst exactly when the month could not be planned.

    The allocator reports an unschedulable month with no new visits and the
    commitments separately. A projection that read only the new visits would erase
    every locked visit from the failed month, and the register would tell a trader
    that a visit they already made never happened.
    """

    day = date(2026, 11, 3)
    areas = tuple(Asset(f"AREA-{i:02d}", AssetType.AREA) for i in range(1, 4))
    committed = tuple(Visit(a.asset_id, day) for a in areas)
    draft = generate_draft(
        DraftRequest(
            start=date(2026, 11, 1),
            end=date(2026, 11, 30),
            mode=DraftMode.CAPACITY_FALLBACK,
            roster=areas,
            display=display_for(areas),
            calendar_days=calendar_days(*november()),
            config=FeasibilityConfig(),
            seed=1,
            dtc_target=0,
            # Three area visits on one date breaks the area cap of two.
            fixed_by_month={NOV: committed},
        )
    )

    assert draft.status is DraftStatus.INFEASIBLE
    assert sorted(v.visit for v in draft.visits) == sorted(committed)
    assert all(v.provenance is VisitProvenance.FIXED for v in draft.visits)
    row = draft.row_for(day)
    assert row is not None
    assert len(row.fixed_visits) == 3
    assert draft.exceptions[0].constraint == "area_daily_cap_exceeded"


def test_infeasible_projection_keeps_evidence_and_fixed_visits(monkeypatch):
    """Drive the INFEASIBLE branch directly through the allocator result."""

    real = draft_generation.run_cycle
    captured = {}

    def capture(*args, **kwargs):
        result = real(*args, **kwargs)
        captured["result"] = result
        month = result.months[0]
        broken = dataclasses.replace(
            month,
            result=dataclasses.replace(
                month.result,
                status=FeasibilityStatus.INFEASIBLE,
                evidence=(
                    Evidence(
                        constraint="C_AGG_AREA_CAPACITY",
                        detail="not enough area capacity",
                        asset_id="AREA-01",
                        limit=2,
                        actual=5,
                    ),
                ),
            ),
        )
        return dataclasses.replace(result, months=(broken,))

    monkeypatch.setattr(draft_generation, "run_cycle", capture)
    committed = Visit("SCD-001", date(2026, 11, 4))
    draft = generate_draft(cycle_request(fixed_by_month={NOV: (committed,)}))

    month = draft.months[0]
    assert month.status is DraftMonthStatus.INFEASIBLE
    assert month.allocator_status is FeasibilityStatus.INFEASIBLE
    assert draft.status is DraftStatus.INFEASIBLE
    # Evidence survives with its structured fields.
    assert draft.exceptions
    assert draft.exceptions[0].constraint == "C_AGG_AREA_CAPACITY"
    assert draft.exceptions[0].kind is DraftExceptionKind.INFEASIBLE
    assert (draft.exceptions[0].limit, draft.exceptions[0].actual) == (2, 5)
    # A committed visit is never discarded to make the month look tidy.
    assert committed in [v.visit for v in draft.visits]


def test_indeterminate_becomes_unresolved_and_never_empty_success(monkeypatch):
    real = draft_generation.run_cycle

    def capture(*args, **kwargs):
        result = real(*args, **kwargs)
        month = result.months[0]
        broken = dataclasses.replace(
            month,
            result=dataclasses.replace(
                month.result,
                status=FeasibilityStatus.INDETERMINATE,
                evidence=(
                    Evidence(
                        constraint="allocation_search_budget_exhausted",
                        detail="the attempt budget was exhausted without a proof",
                    ),
                ),
            ),
        )
        return dataclasses.replace(result, months=(broken,))

    monkeypatch.setattr(draft_generation, "run_cycle", capture)
    draft = generate_draft(cycle_request())

    month = draft.months[0]
    assert month.status is DraftMonthStatus.UNRESOLVED
    assert draft.status is DraftStatus.UNRESOLVED
    assert draft.status is not DraftStatus.ALLOCATION_COMPLETE
    assert draft.status is not DraftStatus.INFEASIBLE
    assert draft.exceptions[0].kind is DraftExceptionKind.UNRESOLVED
    # It is not presented as a completed month.
    assert month.is_allocation_complete is False


def test_a_multi_month_result_keeps_an_unresolved_month_beside_a_feasible_one(
    monkeypatch,
):
    start, end = date(2026, 11, 1), date(2026, 12, 31)
    real = draft_generation.run_cycle

    def capture(*args, **kwargs):
        result = real(*args, **kwargs)
        first, second = result.months
        broken_second = dataclasses.replace(
            second,
            result=dataclasses.replace(
                second.result,
                status=FeasibilityStatus.INDETERMINATE,
                evidence=(Evidence("allocation_search_budget_exhausted", "budget"),),
            ),
        )
        return dataclasses.replace(result, months=(first, broken_second))

    monkeypatch.setattr(draft_generation, "run_cycle", capture)
    draft = generate_draft(
        cycle_request(
            start=start,
            end=end,
            cycle=CoverageCycle(NOV, 2, 1),
            calendar_days=calendar_days(start, end),
        )
    )

    statuses = {m.month: m.status for m in draft.months}
    assert statuses[NOV] is DraftMonthStatus.ALLOCATION_COMPLETE
    assert statuses[DEC] is DraftMonthStatus.UNRESOLVED
    assert draft.status is DraftStatus.UNRESOLVED
    # Both months keep their own evidence.
    assert draft.month_for(DEC) is not None
    assert draft.month_for(DEC).evidence


def test_the_overall_status_follows_the_allocators_worst_month_precedence(
    monkeypatch,
):
    """A proof of impossibility outranks an unsettled month.

    The domain already orders its three outcomes. The register must reuse that
    order rather than decide a second one, or one surface would call a run
    unresolved while another called it impossible.
    """

    start, end = date(2026, 11, 1), date(2026, 12, 31)
    real = draft_generation.run_cycle

    def capture(*args, **kwargs):
        result = real(*args, **kwargs)
        first, second = result.months
        broken_first = dataclasses.replace(
            first,
            result=dataclasses.replace(
                first.result,
                status=FeasibilityStatus.INFEASIBLE,
                evidence=(Evidence("asset_monthly_max_exceeded", "proof"),),
            ),
        )
        broken_second = dataclasses.replace(
            second,
            result=dataclasses.replace(
                second.result,
                status=FeasibilityStatus.INDETERMINATE,
                evidence=(Evidence("allocation_search_budget_exhausted", "budget"),),
            ),
        )
        return dataclasses.replace(result, months=(broken_first, broken_second))

    monkeypatch.setattr(draft_generation, "run_cycle", capture)
    draft = generate_draft(
        cycle_request(
            start=start,
            end=end,
            cycle=CoverageCycle(NOV, 2, 1),
            calendar_days=calendar_days(start, end),
        )
    )

    statuses = {m.month: m.status for m in draft.months}
    assert statuses[NOV] is DraftMonthStatus.INFEASIBLE
    assert statuses[DEC] is DraftMonthStatus.UNRESOLVED
    assert draft.status is DraftStatus.INFEASIBLE


def test_over_coverage_is_reported_without_deleting_commitments():
    committed = (
        Visit("SCD-001", date(2026, 11, 4)),
        Visit("SCD-001", date(2026, 11, 18)),
    )
    draft = generate_draft(cycle_request(fixed_by_month={NOV: committed}))

    over = [e for e in draft.exceptions if e.kind is DraftExceptionKind.OVER_COVERED]
    assert len(over) == 1
    assert over[0].constraint == "cycle_obligation_over_covered"
    assert over[0].asset_id == "SCD-001"
    assert (over[0].limit, over[0].actual) == (1, 2)
    # Both commitments survive in the projection.
    kept = [v.visit for v in draft.visits if v.asset_id == "SCD-001"]
    assert sorted(kept) == sorted(committed)


def test_over_coverage_is_visible_even_when_allocation_succeeded():
    committed = (
        Visit("SCD-001", date(2026, 11, 4)),
        Visit("SCD-001", date(2026, 11, 18)),
    )
    draft = generate_draft(cycle_request(fixed_by_month={NOV: committed}))
    assert draft.status is DraftStatus.ALLOCATION_COMPLETE
    assert any(
        e.kind is DraftExceptionKind.OVER_COVERED for e in draft.exceptions
    )


def test_invalid_commitments_preserve_structured_error_information():
    """A Saturday is not a working date; the error keeps its facts, not just text."""

    bad = {NOV: (Visit("SCD-001", date(2026, 11, 28)),)}
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(fixed_by_month=bad))
    error = caught.value
    assert error.constraint == "cycle_fixed_visit_invalid"
    assert error.asset_id == "SCD-001"
    assert error.violations
    assert error.violations[0].constraint == "fixed_visit_not_on_working_date"
    assert error.violations[0].visit_date == date(2026, 11, 28)


# --- input boundaries ----------------------------------------------------


def test_a_partial_first_month_is_rejected():
    start, end = date(2026, 11, 5), date(2026, 11, 30)
    with pytest.raises(InputError) as caught:
        generate_draft(
            cycle_request(start=start, end=end, calendar_days=calendar_days(start, end))
        )
    assert caught.value.constraint == C_PARTIAL_SELECTION


def test_a_partial_last_month_is_rejected():
    start, end = date(2026, 11, 1), date(2026, 11, 20)
    with pytest.raises(InputError) as caught:
        generate_draft(
            cycle_request(start=start, end=end, calendar_days=calendar_days(start, end))
        )
    assert caught.value.constraint == C_PARTIAL_SELECTION


def test_a_reversed_or_empty_range_is_rejected():
    start, end = date(2026, 11, 30), date(2026, 11, 1)
    with pytest.raises(InputError) as caught:
        generate_draft(
            cycle_request(start=start, end=end, calendar_days=calendar_days(end, start))
        )
    assert caught.value.constraint == C_RANGE_REVERSED


def test_an_empty_range_cannot_become_a_successful_draft():
    day = date(2026, 11, 15)
    with pytest.raises(InputError) as caught:
        generate_draft(
            cycle_request(start=day, end=day, calendar_days=calendar_days(day, day))
        )
    # A single day is not a complete calendar month, so it can never yield a
    # zero-month success.
    assert caught.value.constraint == C_PARTIAL_SELECTION


def test_a_single_complete_month_is_the_smallest_successful_selection():
    assert cycle_request().months() == (NOV,)


def test_an_empty_roster_is_rejected():
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(roster=(), display=()))
    assert caught.value.constraint == C_ROSTER_REQUIRED


def test_missing_display_metadata_is_not_invented():
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(display=display_for()[:-1]))
    assert caught.value.constraint == C_DISPLAY_METADATA_MISSING


def test_unknown_display_metadata_is_rejected():
    extra = AssetDisplay("GHOST", "Ghost")
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(display=display_for() + (extra,)))
    assert caught.value.constraint == C_DISPLAY_METADATA_UNKNOWN_ASSET


def test_duplicate_display_metadata_is_rejected():
    display = display_for()
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(display=display + (display[0],)))
    assert caught.value.constraint == C_DISPLAY_METADATA_DUPLICATE


def test_an_entry_outside_the_range_is_rejected():
    entry = DayEntry("EV-9", date(2026, 12, 5), DayEntryKind.EVENT, "Event")
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(entries=(entry,)))
    assert caught.value.constraint == C_ENTRY_OUTSIDE_RANGE


def test_cycle_mode_requires_a_cycle():
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(cycle=None))
    assert caught.value.constraint == C_CYCLE_REQUIRED


def test_cycle_mode_rejects_fallback_only_inputs():
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(dtc_target=4))
    assert caught.value.constraint == C_MODE_INPUT_CONFLICT


def test_fallback_mode_rejects_a_declared_cycle():
    with pytest.raises(InputError) as caught:
        generate_draft(fallback_request(cycle=CoverageCycle(NOV, 1, 1)))
    assert caught.value.constraint == C_FALLBACK_CONFLICT


def test_a_range_outside_the_declared_cycle_is_rejected():
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(cycle=CoverageCycle(DEC, 1, 1)))
    assert caught.value.constraint == C_RANGE_OUTSIDE_CYCLE


def test_a_commitment_filed_under_the_wrong_month_is_rejected():
    wrong = {DEC: (Visit("SCD-001", date(2026, 11, 4)),)}
    with pytest.raises(InputError) as caught:
        generate_draft(cycle_request(fixed_by_month=wrong))
    assert caught.value.constraint == C_FIXED_VISIT_WRONG_MONTH


# --- reproducibility ------------------------------------------------------


def test_identical_requests_produce_identical_drafts():
    request = cycle_request()
    first = generate_draft(request)
    second = generate_draft(request)
    assert first.rows == second.rows
    assert first.visits == second.visits
    assert first.snapshot == second.snapshot
    assert first.exceptions == second.exceptions


def test_different_seeds_preserve_obligations_and_constraints():
    counts = []
    for seed in (1, 2, 3):
        draft = generate_draft(cycle_request(seed=seed))
        assert draft.status is DraftStatus.ALLOCATION_COMPLETE
        counts.append(
            (dict(draft.months[0].obligations), len(draft.visits))
        )
        # Constraints hold for every seed.
        per_day = {}
        for visit in draft.visits:
            per_day[visit.visit_date] = per_day.get(visit.visit_date, 0) + 1
        assert max(per_day.values()) <= 3
    assert counts[0] == counts[1] == counts[2]


def test_the_projection_matches_the_allocator_without_additions(monkeypatch):
    captured = {}
    real = draft_generation.run_cycle

    def capture(*args, **kwargs):
        captured["result"] = real(*args, **kwargs)
        return captured["result"]

    monkeypatch.setattr(draft_generation, "run_cycle", capture)
    committed = Visit("SCD-003", date(2026, 11, 6))
    draft = generate_draft(cycle_request(fixed_by_month={NOV: (committed,)}))

    assert sorted(v.visit for v in draft.visits) == sorted(
        captured["result"].all_visits
    )


def test_snapshot_records_versions_and_fingerprints():
    draft = generate_draft(cycle_request())
    snapshot = draft.snapshot
    assert snapshot.generator_version == GENERATOR_VERSION
    assert snapshot.allocator_version == draft.allocator_version
    assert snapshot.seed == 7
    assert snapshot.start == date(2026, 11, 1)
    assert snapshot.months == (NOV,)
    for fingerprint in (
        snapshot.roster_fingerprint,
        snapshot.config_fingerprint,
        snapshot.calendar_fingerprint,
        snapshot.display_fingerprint,
    ):
        assert len(fingerprint) == 32


def test_a_different_calendar_changes_its_fingerprint():
    first = generate_draft(cycle_request()).snapshot.calendar_fingerprint
    days = calendar_days(*november(), holidays=(date(2026, 11, 4),))
    second = generate_draft(
        cycle_request(calendar_days=days)
    ).snapshot.calendar_fingerprint
    assert first != second


# --- scope honesty --------------------------------------------------------


def test_a_draft_never_claims_to_be_a_complete_operational_diary():
    draft = generate_draft(cycle_request())
    assert draft.missing_content
    assert draft.is_operational_diary is False
    assert any("activity catalogue" in item for item in draft.missing_content)
