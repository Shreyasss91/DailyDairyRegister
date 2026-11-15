"""Tests for weighted-fair targeting, the no-cycle fallback and multi-month runs."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dailyworkreg.domain.cycle import CoverageCycle, DtcClass, DtcWeights, MonthKey
from dailyworkreg.domain.targeting import (
    TargetMode,
    fallback_dtc_target,
    run_cycle,
    run_fallback,
)
from dailyworkreg.domain.validator import (
    Asset,
    AssetLimits,
    AssetType,
    Calendar,
    DatePeriod,
    FeasibilityConfig,
    FeasibilityStatus,
    ProposalContext,
    ValidationMode,
    Visit,
    validate_schedule,
)


def weekdays(year: int, month: int) -> frozenset[date]:
    """Synthetic weekday calendar; no official holiday data is used or implied."""

    day = date(year, month, 1)
    out = set()
    while day.month == month:
        if day.weekday() < 5:
            out.add(day)
        day += timedelta(days=1)
    return frozenset(out)


def period_of(month: MonthKey) -> DatePeriod:
    start = date(month.year, month.month, 1)
    end = (
        date(month.year + 1, 1, 1)
        if month.month == 12
        else date(month.year, month.month + 1, 1)
    )
    return DatePeriod(start, end - timedelta(days=1))


def big_roster(dtcs: int = 20) -> tuple[Asset, ...]:
    return tuple(
        [Asset(f"AREA-{i:02d}", AssetType.AREA) for i in range(1, 5)]
        + [Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, dtcs + 1)]
        + [Asset(f"FEEDER-{i:02d}", AssetType.FEEDER) for i in range(1, 3)]
    )


def quarter_calendar() -> Calendar:
    return Calendar(
        weekdays(2026, 11) | weekdays(2026, 12) | weekdays(2027, 1)
    )


# --- the reference cycle, end to end -------------------------------------


def test_113_dtc_cycle_allocates_every_month_and_covers_each_dtc_once():
    roster = tuple(
        [Asset(f"AREA-{i:02d}", AssetType.AREA) for i in range(1, 17)]
        + [Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 114)]
        + [Asset(f"FEEDER-{i:02d}", AssetType.FEEDER) for i in range(1, 5)]
    )
    calendar = quarter_calendar()
    result = run_cycle(
        roster,
        calendar,
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        seed=1,
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    assert [len(m.visits) for m in result.months] == [38, 38, 37]

    visited = [v.asset_id for v in result.all_visits]
    dtc_visited = [a for a in visited if a.startswith("SCD-")]
    assert len(dtc_visited) == 113
    assert len(set(dtc_visited)) == 113


def test_cycle_months_are_independently_valid():
    roster = big_roster()
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        seed=2,
    )
    for month in result.months:
        validated = validate_schedule(
            ProposalContext(
                period=month.period,
                calendar=quarter_calendar(),
                roster=roster,
                config=FeasibilityConfig(),
                proposed=month.visits,
                required_visits=dict(month.obligations),
            ),
            ValidationMode.OBLIGATIONS,
        )
        assert validated.valid, validated.violations


def test_only_requested_months_are_placed_but_the_cycle_is_fully_resolved():
    """Unselected months still appear in the resolution, without invented dates."""

    roster = big_roster()
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        months_to_place=[MonthKey(2026, 12)],
    )
    assert len(result.months) == 1
    assert result.months[0].month == MonthKey(2026, 12)
    assert result.cycle_resolution is not None
    assert len(result.cycle_resolution.plans) == 3


def test_cycle_gap_survives_the_month_boundary():
    """A DTC visited at the end of November is not revisited in early December."""

    roster = tuple(
        [Asset("SCD-001", AssetType.DTC), Asset("SCD-002", AssetType.DTC)]
        + [Asset(f"AREA-{i:02d}", AssetType.AREA) for i in range(1, 3)]
    )
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 3),
        seed=4,
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    per_asset: dict[str, list[date]] = {}
    for visit in result.all_visits:
        per_asset.setdefault(visit.asset_id, []).append(visit.visit_date)
    for asset_id, dates in per_asset.items():
        ordered = sorted(dates)
        for earlier, later in zip(ordered, ordered[1:]):
            assert (later - earlier).days >= 7, f"{asset_id} gap broke at {later}"


def test_cycle_continuity_never_resets_history_at_a_boundary():
    """An asset owing one visit per month must respect the gap across months.

    With ``visits_per_asset=1`` over three months a single asset can only be
    scheduled once in the whole cycle, so 1/0/0 is arithmetically forced and
    exercises nothing. Owing three visits spreads it to 1/1/1, one per month,
    which is what actually tests the month boundary.
    """

    roster = (Asset("SCD-001", AssetType.DTC),)
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 3),
        seed=6,
    )
    dates = sorted(v.visit_date for v in result.all_visits)
    assert len(dates) == 3
    # One visit in each month of the cycle.
    assert {d.month for d in dates} == {11, 12, 1}
    for earlier, later in zip(dates, dates[1:]):
        assert (later - earlier).days >= 7


# --- fallback mode -------------------------------------------------------


def test_fallback_is_labelled_a_capacity_suggestion():
    """Without a cycle there is no coverage commitment, and the mode says so."""

    result = run_fallback(
        big_roster(),
        quarter_calendar(),
        FeasibilityConfig(),
        [MonthKey(2026, 11)],
        dtc_target=5,
        seed=1,
    )
    assert result.mode is TargetMode.CAPACITY_SUGGESTION
    assert result.mode.value == "capacity_based_suggestion"
    assert result.cycle_resolution is None


def test_fallback_target_is_bounded_by_capacity():
    roster = big_roster(dtcs=40)
    target = fallback_dtc_target(
        roster,
        period_of(MonthKey(2026, 11)),
        quarter_calendar(),
        FeasibilityConfig(),
        requested_target=100,
        area_and_feeder_demand=20,
    )
    # Cannot exceed the DTC capacity that remains after areas and feeders.
    eligible = len(weekdays(2026, 11))
    assert target.target_count <= eligible * FeasibilityConfig().combined_daily_cap - 20
    assert target.target_count <= 40


def test_fallback_does_not_imply_every_dtc_is_visited():
    roster = big_roster(dtcs=40)
    target = fallback_dtc_target(
        roster,
        period_of(MonthKey(2026, 11)),
        quarter_calendar(),
        FeasibilityConfig(),
        requested_target=5,
    )
    assert len(target.selected) == 5
    assert len(target.selected) < 40


def test_fallback_prefers_least_recently_visited():
    roster = (Asset("SCD-001", AssetType.DTC), Asset("SCD-002", AssetType.DTC))
    history = (Visit("SCD-001", date(2026, 10, 20)),)
    target = fallback_dtc_target(
        roster,
        period_of(MonthKey(2026, 11)),
        quarter_calendar(),
        FeasibilityConfig(),
        history=history,
        requested_target=1,
    )
    assert target.selected == ("SCD-002",)


def test_fallback_rotation_changes_who_is_selected_first():
    roster = (Asset("SCD-001", AssetType.DTC), Asset("SCD-002", AssetType.DTC))
    first = fallback_dtc_target(
        roster, period_of(MonthKey(2026, 11)), quarter_calendar(),
        FeasibilityConfig(), requested_target=1, rotation_offset=0,
    )
    second = fallback_dtc_target(
        roster, period_of(MonthKey(2026, 11)), quarter_calendar(),
        FeasibilityConfig(), requested_target=1, rotation_offset=1,
    )
    assert first.selected != second.selected


def test_weight_orders_fallback_priority_but_not_the_count():
    roster = (
        Asset("SCD-001", AssetType.DTC),
        Asset("SCD-002", AssetType.DTC),
    )
    weights = DtcWeights(kva={"SCD-002": 800.0})
    target = fallback_dtc_target(
        roster,
        period_of(MonthKey(2026, 11)),
        quarter_calendar(),
        FeasibilityConfig(),
        weights=weights,
        requested_target=1,
    )
    assert target.selected == ("SCD-002",)
    # A weight never manufactures extra visits.
    assert target.target_count == 1


def test_fallback_respects_monthly_maximum_zero():
    """An asset with no monthly capacity cannot be selected."""

    roster = (Asset("SCD-001", AssetType.DTC), Asset("SCD-002", AssetType.DTC))
    config = FeasibilityConfig(
        asset_limits={"SCD-001": AssetLimits(monthly_max=0, monthly_min=0)}
    )
    target = fallback_dtc_target(
        roster, period_of(MonthKey(2026, 11)), quarter_calendar(), config,
        requested_target=2,
    )
    assert "SCD-001" not in target.selected


def test_fallback_integrates_area_and_feeder_obligations_jointly():
    roster = big_roster(dtcs=10)
    result = run_fallback(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        [MonthKey(2026, 11)],
        area_required={MonthKey(2026, 11): {"AREA-01": 2, "AREA-02": 1}},
        feeder_required={MonthKey(2026, 11): {"FEEDER-01": 1}},
        dtc_target=4,
        seed=3,
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    month = result.months[0]
    visited = {v.asset_id for v in month.visits}
    assert {"AREA-01", "AREA-02", "FEEDER-01"} <= visited
    assert len([a for a in visited if a.startswith("SCD-")]) == 4

    validated = validate_schedule(
        ProposalContext(
            period=month.period,
            calendar=quarter_calendar(),
            roster=roster,
            config=FeasibilityConfig(),
            proposed=month.visits,
            required_visits=dict(month.obligations),
        ),
        ValidationMode.OBLIGATIONS,
    )
    assert validated.valid, validated.violations


def test_cycle_mode_takes_precedence_over_the_fallback():
    """When a cycle is supplied, its obligations are used, not a target guess."""

    roster = big_roster(dtcs=10)
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        seed=1,
    )
    assert result.mode is TargetMode.COVERAGE_CYCLE
    visited = {v.asset_id for v in result.all_visits}
    assert {f"SCD-{i:03d}" for i in range(1, 11)} <= visited


# --- multi-month status and continuity -----------------------------------


def test_multi_month_status_reports_the_worst_month():
    """A partial failure must not be presented as an overall success."""

    roster = big_roster(dtcs=2)
    result = run_fallback(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        [MonthKey(2026, 11), MonthKey(2026, 12)],
        area_required={MonthKey(2026, 12): {"AREA-01": 9}},
        dtc_target=1,
        seed=1,
    )
    statuses = [m.result.status for m in result.months]
    assert FeasibilityStatus.INFEASIBLE in statuses
    assert result.status is FeasibilityStatus.INFEASIBLE


def test_fixed_visits_are_honoured_per_month():
    roster = big_roster(dtcs=4)
    locked = {MonthKey(2026, 11): (Visit("SCD-001", date(2026, 11, 3)),)}
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        fixed_by_month=locked,
        seed=1,
    )
    november = result.month_for(MonthKey(2026, 11))
    assert november is not None
    if november.result.status is FeasibilityStatus.FEASIBLE:
        assert Visit("SCD-001", date(2026, 11, 3)) in november.visits


def test_identical_seeds_produce_identical_multi_month_runs():
    roster = big_roster(dtcs=6)
    first = run_cycle(
        roster, quarter_calendar(), FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1), seed=8,
    )
    second = run_cycle(
        roster, quarter_calendar(), FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1), seed=8,
    )
    assert first.all_visits == second.all_visits


def test_different_rotation_offset_changes_who_goes_first():
    roster = big_roster(dtcs=10)
    cycle = CoverageCycle(MonthKey(2026, 11), 3, 1)
    first = run_cycle(
        roster, quarter_calendar(), FeasibilityConfig(), cycle,
        seed=1, rotation_offset=0,
    )
    second = run_cycle(
        roster, quarter_calendar(), FeasibilityConfig(), cycle,
        seed=1, rotation_offset=1,
    )
    assert first.cycle_resolution is not None and second.cycle_resolution is not None
    assert (
        first.cycle_resolution.plans[0].obligations
        != second.cycle_resolution.plans[0].obligations
    )
