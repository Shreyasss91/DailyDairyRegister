"""Deterministic tests for the date-level field-visit feasibility validator.

Every test uses synthetic calendar fixtures whose working and non-working dates
are stated explicitly. No official holiday dataset is loaded, so these tests do
not depend on the unvalidated Karnataka 2025/2026 data.

Tests are grouped by the section 10.6 test-vector table and by the boundary
cases required for the validator contract. Each assertion states the expected
outcome of the recorded policy, not an implementation detail.
"""

from __future__ import annotations

from datetime import date

import pytest  # noqa: F401  retained for pytest exception and fixture surface

from dailyworkreg.domain.validator import (
    C_AGG_AREA_CAPACITY,
    C_AGG_COMBINED_CAPACITY,
    C_AGG_DTC_CAPACITY,
    C_AREA_DAILY_CAP,
    C_ASSET_MINIMUM,
    C_ASSET_MONTHLY_MAX,
    C_COMBINED_DAILY_CAP,
    C_DTC_DAILY_CAP,
    C_INACTIVE_ASSET,
    C_MIN_EXCEEDS_MAX,
    C_OUTSIDE_PERIOD,
    C_REVISIT_GAP,
    C_UNKNOWN_ASSET,
    C_WORKING_DATE,
    Asset,
    AssetLimits,
    AssetType,
    Calendar,
    DatePeriod,
    FeasibilityConfig,
    FeasibilityStatus,
    ObligationContext,
    ProposalContext,
    ValidationMode,
    Visit,
    assess_feasibility,
    calendar_day_gap,
    validate_schedule,
)

# --- synthetic calendar fixtures -------------------------------------------

# A month expressed as plain calendar dates with explicit working/non-working
# decisions, so the fixture never depends on real holidays.
def _month_days(year: int, month: int) -> list[date]:
    import calendar as _cal

    return [date(year, month, d) for d in range(1, _cal.monthrange(year, month)[1] + 1)]


def _all_working(year: int, month: int) -> frozenset[date]:
    """Every calendar date eligible. Used where weekday semantics do not matter."""

    return frozenset(_month_days(year, month))


def _weekday_working(year: int, month: int, exclude: tuple[date, ...] = ()) -> frozenset[date]:
    """Monday to Friday eligible, excluding any dates listed in ``exclude``.

    This models a normal working week plus explicit holiday/leave/override
    decisions supplied by the caller.
    """

    return frozenset(
        d for d in _month_days(year, month)
        if d.weekday() < 5 and d not in exclude
    )


def _month_period(year: int, month: int) -> DatePeriod:
    days = _month_days(year, month)
    return DatePeriod(start=days[0], end=days[-1])


AREA_ID = "area-01"
AREA2_ID = "area-02"
AREA3_ID = "area-03"
DTC_ID = "SCD-01"
FEEDER_ID = "feeder-01"


def _roster(*assets: Asset) -> tuple[Asset, ...]:
    return assets


def _standard_roster() -> tuple[Asset, ...]:
    return (
        Asset(AREA_ID, AssetType.AREA),
        Asset(AREA2_ID, AssetType.AREA),
        Asset(AREA3_ID, AssetType.AREA),
        Asset(DTC_ID, AssetType.DTC),
        Asset(FEEDER_ID, AssetType.FEEDER),
    )


def _validate(proposed, *, period=None, calendar=None, roster=None, config=None,
              history=(), mode=ValidationMode.SCHEDULE_ONLY):
    return validate_schedule(
        ProposalContext(
            period=period or _month_period(2026, 10),
            calendar=calendar or Calendar(_all_working(2026, 10)),
            roster=roster or _standard_roster(),
            config=config or FeasibilityConfig(),
            proposed=tuple(proposed),
            history=tuple(history),
        ),
        mode=mode,
    )


def _assess(*, required, period=None, calendar=None, roster=None, config=None,
            history=(), cycle=None):
    return assess_feasibility(
        ObligationContext(
            period=period or _month_period(2026, 10),
            calendar=calendar or Calendar(_all_working(2026, 10)),
            roster=roster or _standard_roster(),
            config=config or FeasibilityConfig(),
            history=tuple(history),
            required_visits=dict(required),
            cycle_obligations=dict(cycle or {}),
        )
    )


# --- section 10.6 vector: calendar gap boundaries ---------------------------


def test_calendar_gap_day_1_to_day_8_allowed():
    """Day 1 to day 8 is exactly 7 calendar days, so the gap is satisfied."""

    result = _validate([
        Visit(AREA_ID, date(2026, 10, 1)),
        Visit(AREA_ID, date(2026, 10, 8)),
    ])
    assert result.valid, result.violations
    assert calendar_day_gap(date(2026, 10, 1), date(2026, 10, 8)) == 7


def test_calendar_gap_day_1_to_day_7_rejected():
    """Day 1 to day 7 is 6 calendar days, one short of the 7-day minimum."""

    result = _validate([
        Visit(AREA_ID, date(2026, 10, 1)),
        Visit(AREA_ID, date(2026, 10, 7)),
    ])
    assert not result.valid
    assert C_REVISIT_GAP in result.violation_constraints


def test_weekend_elapsed_gap_friday_to_following_friday_allowed():
    """Friday to the following Friday is 7 calendar days, so weekends count."""

    friday = date(2026, 10, 2)
    next_friday = date(2026, 10, 9)
    assert friday.weekday() == 4 and next_friday.weekday() == 4
    result = _validate(
        [Visit(AREA_ID, friday), Visit(AREA_ID, next_friday)],
        calendar=Calendar(_all_working(2026, 10)),
    )
    assert result.valid, result.violations
    assert calendar_day_gap(friday, next_friday) == 7


def test_six_day_gap_friday_to_following_thursday_rejected():
    """Friday to the following Thursday is 6 calendar days and is refused."""

    friday = date(2026, 10, 2)
    thursday = date(2026, 10, 8)
    assert calendar_day_gap(friday, thursday) == 6
    result = _validate([Visit(AREA_ID, friday), Visit(AREA_ID, thursday)])
    assert not result.valid
    assert C_REVISIT_GAP in result.violation_constraints


# --- section 10.6 vector: per-asset monthly maximum ------------------------


def test_three_visits_to_one_asset_in_month_allowed():
    """Three visits at valid gaps stay within the default monthly maximum of 3."""

    result = _validate([
        Visit(AREA_ID, date(2026, 10, 1)),
        Visit(AREA_ID, date(2026, 10, 8)),
        Visit(AREA_ID, date(2026, 10, 15)),
    ])
    assert result.valid, result.violations


def test_four_visits_to_one_asset_in_month_rejected():
    """A fourth visit exceeds the default monthly maximum of 3."""

    result = _validate([
        Visit(AREA_ID, date(2026, 10, 1)),
        Visit(AREA_ID, date(2026, 10, 8)),
        Visit(AREA_ID, date(2026, 10, 15)),
        Visit(AREA_ID, date(2026, 10, 22)),
    ])
    assert not result.valid
    assert C_ASSET_MONTHLY_MAX in result.violation_constraints


def test_three_visits_spanning_14_calendar_days_allowed():
    """First and last visit 14 calendar days apart with valid gaps is allowed."""

    first, last = date(2026, 10, 1), date(2026, 10, 15)
    assert calendar_day_gap(first, last) == 14
    result = _validate([
        Visit(AREA_ID, first),
        Visit(AREA_ID, date(2026, 10, 8)),
        Visit(AREA_ID, last),
    ])
    assert result.valid, result.violations


# --- section 10.6 vector: cross-month and cross-cycle gaps -----------------


def test_cross_month_gap_six_days_rejected():
    """Prior visit six calendar days before a proposed visit is refused."""

    period = DatePeriod(start=date(2026, 10, 1), end=date(2026, 10, 31))
    result = _validate(
        [Visit(AREA_ID, date(2026, 10, 5))],
        period=period,
        history=[Visit(AREA_ID, date(2026, 9, 29))],
        calendar=Calendar(_all_working(2026, 9) | _all_working(2026, 10)),
    )
    assert calendar_day_gap(date(2026, 9, 29), date(2026, 10, 5)) == 6
    assert not result.valid
    assert C_REVISIT_GAP in result.violation_constraints


def test_cross_month_gap_exactly_seven_days_allowed():
    """A prior visit exactly seven calendar days earlier satisfies the gap."""

    period = DatePeriod(start=date(2026, 10, 1), end=date(2026, 10, 31))
    result = _validate(
        [Visit(AREA_ID, date(2026, 10, 6))],
        period=period,
        history=[Visit(AREA_ID, date(2026, 9, 29))],
        calendar=Calendar(_all_working(2026, 9) | _all_working(2026, 10)),
    )
    assert calendar_day_gap(date(2026, 9, 29), date(2026, 10, 6)) == 7
    assert result.valid, result.violations


def test_cross_cycle_boundary_does_not_reset_gap():
    """A cycle boundary is not a reset: the gap still applies across it."""

    period = DatePeriod(start=date(2026, 10, 1), end=date(2026, 10, 31))
    result = _validate(
        [Visit(DTC_ID, date(2026, 10, 2))],
        period=period,
        # Prior cycle visit 3 calendar days earlier.
        history=[Visit(DTC_ID, date(2026, 9, 29))],
    )
    assert not result.valid
    assert C_REVISIT_GAP in result.violation_constraints


def test_previous_visit_day_before_period_rejected():
    """A prior visit the day before the period blocks an immediate revisit."""

    period = DatePeriod(start=date(2026, 10, 1), end=date(2026, 10, 31))
    result = _validate(
        [Visit(AREA_ID, date(2026, 10, 1))],
        period=period,
        history=[Visit(AREA_ID, date(2026, 9, 30))],
    )
    assert not result.valid
    assert C_REVISIT_GAP in result.violation_constraints


# --- section 10.6 vector: daily capacity ----------------------------------


def test_two_area_visits_one_date_allowed():
    """Two area visits on one date are within a stated area limit of 2."""

    result = _validate(
        [
            Visit(AREA_ID, date(2026, 10, 1)),
            Visit(AREA2_ID, date(2026, 10, 1)),
        ],
        config=FeasibilityConfig(area_daily_cap=2),
    )
    assert result.valid, result.violations
    assert result.days_used[date(2026, 10, 1)] == 2


def test_three_area_visits_one_date_rejected():
    """Three area visits on one date exceed a stated area limit of 2."""

    result = _validate(
        [
            Visit(AREA_ID, date(2026, 10, 1)),
            Visit(AREA2_ID, date(2026, 10, 1)),
            Visit(AREA3_ID, date(2026, 10, 1)),
        ],
        config=FeasibilityConfig(area_daily_cap=2),
    )
    assert not result.valid
    assert C_AREA_DAILY_CAP in result.violation_constraints


def test_two_areas_plus_one_dtc_one_date_allowed():
    """Two areas and one DTC each fit their own kind's ceiling on one date."""

    result = _validate(
        [
            Visit(AREA_ID, date(2026, 10, 1)),
            Visit(AREA2_ID, date(2026, 10, 1)),
            Visit(DTC_ID, date(2026, 10, 1)),
        ],
        config=FeasibilityConfig(area_daily_cap=2),
    )
    assert result.valid, result.violations


def test_two_areas_plus_one_feeder_one_date_allowed():
    """Two areas and one feeder share the date, each within its own ceiling."""

    result = _validate(
        [
            Visit(AREA_ID, date(2026, 10, 1)),
            Visit(AREA2_ID, date(2026, 10, 1)),
            Visit(FEEDER_ID, date(2026, 10, 1)),
        ],
        config=FeasibilityConfig(area_daily_cap=2),
    )
    assert result.valid, result.violations


def test_two_areas_plus_dtc_plus_feeder_fit_because_each_kind_is_bounded_alone():
    """Two areas, one DTC and one feeder is four visits, and each kind is within
    its own ceiling: the area ceiling of 2 is not spent by the DTC or the feeder."""

    result = _validate(
        [
            Visit(AREA_ID, date(2026, 10, 1)),
            Visit(AREA2_ID, date(2026, 10, 1)),
            Visit(DTC_ID, date(2026, 10, 1)),
            Visit(FEEDER_ID, date(2026, 10, 1)),
        ],
        config=FeasibilityConfig(area_daily_cap=2),
    )
    assert result.valid


def test_three_dtc_visits_on_one_date_exceed_the_dtc_ceiling():
    """A kind is refused by its own ceiling, not by a total that mixes the kinds."""

    roster = tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 4))
    day = date(2026, 10, 1)
    result = _validate(
        [
            Visit("SCD-001", day),
            Visit("SCD-002", day),
            Visit("SCD-003", day),
        ],
        roster=roster,
        config=FeasibilityConfig(dtc_daily_cap=2),
    )
    assert not result.valid
    assert C_DTC_DAILY_CAP in result.violation_constraints
    assert C_COMBINED_DAILY_CAP not in result.violation_constraints



def test_multiple_asset_types_competing_for_same_day_capacity():
    """A stated total is an extra ceiling over the per-kind ones."""

    config = FeasibilityConfig(combined_daily_cap=3, area_daily_cap=2, dtc_daily_cap=2)
    allowed = _validate(
        [
            Visit(AREA_ID, date(2026, 10, 1)),
            Visit(DTC_ID, date(2026, 10, 1)),
            Visit(FEEDER_ID, date(2026, 10, 1)),
        ],
        config=config,
    )
    assert allowed.valid, allowed.violations
    rejected = _validate(
        [
            Visit(AREA_ID, date(2026, 10, 1)),
            Visit(AREA2_ID, date(2026, 10, 1)),
            Visit(DTC_ID, date(2026, 10, 1)),
            Visit(FEEDER_ID, date(2026, 10, 1)),
        ],
        config=config,
    )
    assert rejected.violation_constraints == (C_COMBINED_DAILY_CAP,)


# --- section 10.6 vector: calendar eligibility ----------------------------


def test_visit_on_non_working_date_rejected():
    """A proposed visit on an ineligible date is refused."""

    holiday = date(2026, 10, 2)
    result = _validate(
        [Visit(AREA_ID, holiday)],
        calendar=Calendar(_weekday_working(2026, 10, exclude=(holiday,))),
    )
    assert not result.valid
    assert C_WORKING_DATE in result.violation_constraints


def test_override_making_non_working_date_eligible_allowed():
    """A date override that makes a Saturday eligible is honoured as supplied."""

    saturday = date(2026, 10, 3)
    assert saturday.weekday() == 5
    calendar = Calendar(_weekday_working(2026, 10) | {saturday})
    result = _validate([Visit(AREA_ID, saturday)], calendar=calendar)
    assert result.valid, result.violations


def test_holiday_and_leave_dates_remain_ineligible():
    """A holiday and a leave date supplied as ineligible both refuse visits."""

    holiday = date(2026, 10, 2)
    leave = date(2026, 10, 5)
    calendar = Calendar(_weekday_working(2026, 10, exclude=(holiday, leave)))
    result = _validate(
        [Visit(AREA_ID, holiday), Visit(AREA2_ID, leave)], calendar=calendar
    )
    assert not result.valid
    assert result.violation_constraints.count(C_WORKING_DATE) == 2


def test_unknown_and_inactive_assets_rejected():
    """Unknown and inactive assets are reported, not silently dropped."""

    roster = (
        Asset(AREA_ID, AssetType.AREA),
        Asset("area-off", AssetType.AREA, active=False),
    )
    result = _validate(
        [
            Visit("does-not-exist", date(2026, 10, 1)),
            Visit("area-off", date(2026, 10, 2)),
        ],
        roster=roster,
    )
    assert not result.valid
    assert C_UNKNOWN_ASSET in result.violation_constraints
    assert C_INACTIVE_ASSET in result.violation_constraints


def test_visit_outside_period_rejected():
    """A visit outside the selected period is reported explicitly."""

    period = DatePeriod(start=date(2026, 10, 5), end=date(2026, 10, 20))
    result = _validate([Visit(AREA_ID, date(2026, 10, 1))], period=period)
    assert not result.valid
    assert C_OUTSIDE_PERIOD in result.violation_constraints


# --- section 10.6 vector: obligations and minima --------------------------


def test_configured_minimum_not_met_reported_in_obligation_mode():
    """A configured minimum is enforced only when obligations are required."""

    config = FeasibilityConfig(
        default_asset_limits=AssetLimits(monthly_max=3, monthly_min=0),
        asset_limits={AREA_ID: AssetLimits(monthly_max=3, monthly_min=2)},
    )
    proposed = [Visit(AREA_ID, date(2026, 10, 1))]
    lenient = _validate(proposed, config=config, mode=ValidationMode.SCHEDULE_ONLY)
    assert lenient.valid, lenient.violations
    strict = _validate(proposed, config=config, mode=ValidationMode.OBLIGATIONS)
    assert not strict.valid
    assert C_ASSET_MINIMUM in strict.violation_constraints


def test_configured_minimum_above_maximum_is_impossible():
    """A minimum above the effective maximum is reported as impossible."""

    config = FeasibilityConfig(
        asset_limits={AREA_ID: AssetLimits(monthly_max=2, monthly_min=5)},
    )
    result = _validate(
        [Visit(AREA_ID, date(2026, 10, 1))],
        config=config,
        mode=ValidationMode.OBLIGATIONS,
    )
    assert not result.valid
    assert C_MIN_EXCEEDS_MAX in result.violation_constraints


def test_monthly_count_not_reset_by_gap_spanning_boundary():
    """A valid gap across the boundary does not reset the in-period count."""

    period = DatePeriod(start=date(2026, 10, 1), end=date(2026, 10, 31))
    proposed = [
        Visit(AREA_ID, date(2026, 10, 1)),
        Visit(AREA_ID, date(2026, 10, 8)),
        Visit(AREA_ID, date(2026, 10, 15)),
    ]
    result = _validate(
        proposed,
        period=period,
        history=[Visit(AREA_ID, date(2026, 9, 24))],
        calendar=Calendar(_all_working(2026, 9) | _all_working(2026, 10)),
    )
    # Three in-period visits plus a history visit exactly seven days earlier.
    assert result.valid, result.violations
    assert result.days_used[date(2026, 10, 1)] == 1


# --- section 10.6 vector: aggregate necessary conditions ------------------
#
# The aggregate bounds use W, the number of eligible working dates actually
# supplied. These fixtures declare their eligible dates explicitly so that W is
# exactly the value named in the recorded vector, rather than whatever real
# weekday arithmetic a month happens to produce.


def _consecutive_eligible(start: date, count: int) -> frozenset[date]:
    from datetime import timedelta

    return frozenset(start + timedelta(days=i) for i in range(count))


def _fixture_w23() -> tuple[frozenset[date], DatePeriod]:
    eligible = _consecutive_eligible(date(2026, 11, 1), 23)
    return eligible, DatePeriod(start=date(2026, 11, 1), end=date(2026, 11, 23))


def _fixture_w25() -> tuple[frozenset[date], DatePeriod]:
    eligible = _consecutive_eligible(date(2026, 11, 1), 25)
    return eligible, DatePeriod(start=date(2026, 11, 1), end=date(2026, 11, 25))


def test_aggregate_capacity_passes_but_is_not_a_feasibility_proof():
    """W=23 at 3 DTC a day gives 69 slots; meeting the bound only passes the aggregate."""

    eligible, period = _fixture_w23()
    assert len(eligible) == 23
    assert len(eligible) * 3 == 69  # C * W
    roster = tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 24))
    result = _assess(
        required={a.asset_id: 3 for a in roster},
        period=period,
        calendar=Calendar(eligible),
        roster=roster,
        config=FeasibilityConfig(search_node_limit=20_000, dtc_daily_cap=3),
    )
    # The aggregate bounds are satisfied, so rejection cannot come from them.
    assert all(
        e.constraint not in (C_AGG_AREA_CAPACITY, C_AGG_COMBINED_CAPACITY)
        for e in result.evidence
    )
    assert sum(3 for _ in roster) == 69
    # Passing the aggregate bound is not proof of date-level feasibility: the
    # joint packing search could not settle it within its declared budget, so
    # the honest verdict is INDETERMINATE rather than FEASIBLE.
    assert result.status is FeasibilityStatus.INDETERMINATE
    assert result.is_proven is False


def test_area_capacity_infeasible_at_48_with_23_working_days():
    """W=23 and A=48 exceeds the area bound of 46, which is a proven rejection."""

    eligible, period = _fixture_w23()
    roster = tuple(Asset(f"area-{i:02d}", AssetType.AREA) for i in range(1, 17))
    result = _assess(
        required={a.asset_id: 3 for a in roster},
        period=period,
        calendar=Calendar(eligible),
        roster=roster,
    )
    assert 16 * 3 == 48
    assert 48 > 2 * 23 == 46
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert any(e.constraint == C_AGG_AREA_CAPACITY for e in result.evidence)


def test_area_capacity_boundary_at_46_passes_aggregate_only():
    """A=46 equals the aggregate area bound at two a day; the check passes and does no more."""

    eligible, period = _fixture_w23()
    assert 46 <= 2 * 23
    # 46 area visits without breaching the per-asset maximum of 3:
    # 15 areas x 3 + 1 area x 1 = 46.
    roster = tuple(Asset(f"area-{i:02d}", AssetType.AREA) for i in range(1, 17))
    required = {a.asset_id: 3 for a in roster[:15]}
    required[roster[15].asset_id] = 1
    assert sum(required.values()) == 46
    result = _assess(
        required=required,
        period=period,
        calendar=Calendar(eligible),
        roster=roster,
        config=FeasibilityConfig(search_node_limit=20_000, area_daily_cap=2),
    )
    # The aggregate bounds are satisfied, so no aggregate rejection is reported.
    assert all(e.constraint != C_AGG_AREA_CAPACITY for e in result.evidence)
    assert all(e.constraint != C_AGG_COMBINED_CAPACITY for e in result.evidence)
    # Satisfying the boundary is a necessary condition only. The joint packing
    # search does not settle this input within its budget, so the result stays
    # INDETERMINATE and full feasibility is not inferred.
    assert result.status is FeasibilityStatus.INDETERMINATE


def test_dtc_capacity_excess_rejected_at_76_over_75():
    """W=25 at 3 DTC a day creates 75 slots, so 76 DTC duties are proven infeasible."""

    eligible, period = _fixture_w25()
    assert len(eligible) == 25
    assert 3 * 25 == 75
    roster = tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 77))
    result = _assess(
        required={a.asset_id: 1 for a in roster},
        period=period,
        calendar=Calendar(eligible),
        roster=roster,
        config=FeasibilityConfig(dtc_daily_cap=3),
    )
    assert sum(1 for _ in roster) == 76
    assert 76 > 3 * 25
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert any(e.constraint == C_AGG_DTC_CAPACITY for e in result.evidence)


# --- section 10.6 vector: DTC coverage cycle ------------------------------


def test_one_hundred_thirteen_dtc_quarterly_cycle_yields_113_obligations():
    """113 active DTCs at one visit each per cycle is exactly 113 obligations."""

    roster = tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 114))
    assert len(roster) == 113
    cycle = {a.asset_id: 1 for a in roster}
    context = ObligationContext(
        period=_month_period(2026, 10),
        calendar=Calendar(_all_working(2026, 10)),
        roster=roster,
        config=FeasibilityConfig(),
        cycle_obligations=cycle,
    )
    assert sum(context.obligation_for(a.asset_id) for a in roster) == 113


def test_cycle_allocation_sums_to_113():
    """A three-month split of 38, 38 and 37 totals exactly 113."""

    assert 38 + 38 + 37 == 113


def test_area_roster_with_maximum_only_does_not_create_obligations():
    """Sixteen areas with maximum 3 and no minimum imply no required visits."""

    roster = tuple(Asset(f"area-{i:02d}", AssetType.AREA) for i in range(1, 17))
    assert 16 * 3 == 48  # the maximum-only arithmetic must not become a target
    context = ObligationContext(
        period=_month_period(2026, 10),
        calendar=Calendar(_all_working(2026, 10)),
        roster=roster,
        config=FeasibilityConfig(
            default_asset_limits=AssetLimits(monthly_max=3, monthly_min=0)
        ),
    )
    assert sum(context.obligation_for(a.asset_id) for a in roster) == 0
    result = assess_feasibility(context)
    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.witness == ()


# --- determinism ----------------------------------------------------------


def test_repeated_assessment_is_deterministic():
    """Identical inputs must produce an identical status and witness."""

    roster = tuple(Asset(f"area-{i:02d}", AssetType.AREA) for i in range(1, 6))
    required = {a.asset_id: 2 for a in roster}
    first = _assess(required=required, roster=roster)
    second = _assess(required=required, roster=roster)
    assert first.status is FeasibilityStatus.FEASIBLE
    assert first.status is second.status
    assert first.witness == second.witness


def test_validation_is_deterministic_regardless_of_proposal_order():
    """Reordering the proposal must not change the verdict or the constraints."""

    visits = [
        Visit(AREA_ID, date(2026, 10, 1)),
        Visit(AREA2_ID, date(2026, 10, 1)),
        Visit(DTC_ID, date(2026, 10, 8)),
    ]
    forward = _validate(visits)
    backward = _validate(list(reversed(visits)))
    assert forward.valid == backward.valid
    assert forward.violation_constraints == backward.violation_constraints


def test_witness_satisfies_its_own_validation():
    """A FEASIBLE witness must pass proposed-schedule validation unchanged."""

    roster = tuple(Asset(f"area-{i:02d}", AssetType.AREA) for i in range(1, 5))
    result = _assess(required={a.asset_id: 2 for a in roster}, roster=roster)
    assert result.status is FeasibilityStatus.FEASIBLE
    validated = _validate(list(result.witness), roster=roster)
    assert validated.valid, validated.violations


# --- declared cycle obligation exceeding aggregate capacity ---------------


def test_declared_cycle_obligation_exceeding_capacity_is_infeasible():
    """113 cycle obligations cannot fit into 75 slots and are proven infeasible."""

    eligible, period = _fixture_w25()
    roster = tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 114))
    result = _assess(
        required={},
        cycle={a.asset_id: 1 for a in roster},
        period=period,
        calendar=Calendar(eligible),
        roster=roster,
        config=FeasibilityConfig(dtc_daily_cap=3),
    )
    assert sum(1 for _ in roster) == 113
    assert 113 > 3 * 25 == 75
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert any(e.constraint == C_AGG_DTC_CAPACITY for e in result.evidence)


def test_search_budget_exhaustion_is_indeterminate_not_infeasible():
    """A tiny node budget must yield INDETERMINATE, never INFEASIBLE."""

    roster = tuple(Asset(f"area-{i:02d}", AssetType.AREA) for i in range(1, 9))
    result = _assess(
        required={a.asset_id: 2 for a in roster},
        roster=roster,
        config=FeasibilityConfig(search_node_limit=1),
    )
    assert result.status is FeasibilityStatus.INDETERMINATE
    assert result.is_proven is False


# --- soundness: no false verdicts -----------------------------------------
#
# These tests cross-check the solver against exhaustive brute force on small
# inputs. They exist because a heuristic that fails is not an infeasibility
# proof: only a complete search or an arithmetic argument is.


def brute_force_feasible(days, per_asset_counts, cap, gap):
    """Exhaustively decide feasibility for a single-category instance."""

    from itertools import combinations, product

    options = [
        [c for c in combinations(days, b)
         if all((c[i + 1] - c[i]).days >= gap for i in range(len(c) - 1))]
        for b in per_asset_counts
    ]
    if any(not option for option in options):
        return False
    for combo in product(*options):
        used: dict[date, int] = {}
        ok = True
        for placement in combo:
            for day in placement:
                used[day] = used.get(day, 0) + 1
                if used[day] > cap:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return True
    return False


def _small_sweep_instances():
    from datetime import timedelta

    for w in (8, 9, 10):
        days = [date(2026, 11, 1) + timedelta(days=i) for i in range(w)]
        for cap in (2, 3):
            for asset_count in (2, 3):
                for per_asset in (1, 2, 3):
                    yield days, cap, asset_count, per_asset


def test_solver_never_claims_false_infeasible_or_false_feasible():
    """Every verdict must agree with exhaustive brute force on small inputs."""

    for days, cap, asset_count, per_asset in _small_sweep_instances():
        roster = tuple(
            Asset(f"a{i}", AssetType.DTC) for i in range(asset_count)
        )
        context = ObligationContext(
            period=DatePeriod(days[0], days[-1]),
            calendar=Calendar(days),
            roster=roster,
            # A DTC-only roster, so the per-kind ceiling is what the brute force
            # enumerates: `cap` DTC visits on one date.
            config=FeasibilityConfig(
                dtc_daily_cap=cap,
                default_asset_limits=AssetLimits(monthly_max=3),
                search_node_limit=500_000,
            ),
            required_visits={a.asset_id: per_asset for a in roster},
        )
        result = assess_feasibility(context)
        expected = brute_force_feasible(
            days, [per_asset] * asset_count, cap, 7
        )
        assert (result.status is FeasibilityStatus.FEASIBLE) is expected, (
            days[-1], cap, asset_count, per_asset, result.status
        )


def test_every_feasible_witness_passes_schedule_validation():
    """A witness the solver returns must satisfy every hard constraint itself."""

    for days, cap, asset_count, per_asset in _small_sweep_instances():
        roster = tuple(
            Asset(f"a{i}", AssetType.AREA) for i in range(asset_count)
        )
        config = FeasibilityConfig(
            combined_daily_cap=cap,
            area_daily_cap=min(2, cap),
            default_asset_limits=AssetLimits(monthly_max=3),
            search_node_limit=500_000,
        )
        result = assess_feasibility(
            ObligationContext(
                period=DatePeriod(days[0], days[-1]),
                calendar=Calendar(days),
                roster=roster,
                config=config,
                required_visits={a.asset_id: per_asset for a in roster},
            )
        )
        if result.status is not FeasibilityStatus.FEASIBLE:
            continue
        validated = validate_schedule(
            ProposalContext(
                period=DatePeriod(days[0], days[-1]),
                calendar=Calendar(days),
                roster=roster,
                config=config,
                proposed=result.witness,
            )
        )
        assert validated.valid, validated.violations
        assert len(result.witness) == per_asset * asset_count


def test_area_aggregate_bound_rejects_before_searching():
    """48 area visits against a 46-slot bound is proven without a search."""

    eligible, period = _fixture_w23()
    roster = tuple(Asset(f"area-{i:02d}", AssetType.AREA) for i in range(1, 17))
    result = _assess(
        required={a.asset_id: 3 for a in roster},
        period=period,
        calendar=Calendar(eligible),
        roster=roster,
        # A tiny budget proves the rejection came from arithmetic, not search.
        config=FeasibilityConfig(search_node_limit=10),
    )
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert result.evidence[0].constraint == C_AGG_AREA_CAPACITY


def test_large_roster_beyond_search_budget_is_indeterminate():
    """A roster too large to settle exhaustively reports INDETERMINATE."""

    eligible, period = _fixture_w23()
    roster = tuple(Asset(f"area-{i:02d}", AssetType.AREA) for i in range(1, 17))
    required = {a.asset_id: 3 for a in roster[:15]}
    required[roster[15].asset_id] = 1
    assert sum(required.values()) == 46  # exactly the aggregate boundary
    result = _assess(
        required=required,
        period=period,
        calendar=Calendar(eligible),
        roster=roster,
        config=FeasibilityConfig(search_node_limit=50_000, area_daily_cap=2),
    )
    # The aggregate bound passes but the joint packing is not settled, so the
    # answer must be INDETERMINATE rather than a guess in either direction.
    assert result.status is FeasibilityStatus.INDETERMINATE
    assert result.is_proven is False
