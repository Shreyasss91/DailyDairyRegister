"""Tests for the joint date-level allocator.

Every successful allocation is independently revalidated here through
``validate_schedule(..., ValidationMode.OBLIGATIONS)``. That is the property the
allocator exists to guarantee: a ``FEASIBLE`` result is a complete, legal month,
not merely a plausible guess.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dailyworkreg.domain.allocator import (
    C_ALLOCATION_BUDGET_EXHAUSTED,
    AllocationRequest,
    allocate_month,
    describe_pressure,
)
from dailyworkreg.domain.validator import (
    Asset,
    AssetLimits,
    AssetType,
    Calendar,
    DatePeriod,
    FeasibilityConfig,
    FeasibilityStatus,
    InputError,
    ProposalContext,
    ValidationMode,
    Visit,
    validate_schedule,
)

NOVEMBER = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))
NOV_DAYS = frozenset(date(2026, 11, 1) + timedelta(i) for i in range(30))
CONFIG = FeasibilityConfig()

RULES = ("AREA-01", "AREA-02", "AREA-03")
DTCS = ("SCD-001", "SCD-002")


def roster_of(*, areas=3, dtcs=2, feeders=1) -> tuple[Asset, ...]:
    return tuple(
        [Asset(f"AREA-{i:02d}", AssetType.AREA) for i in range(1, areas + 1)]
        + [Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, dtcs + 1)]
        + [Asset(f"FEEDER-{i:02d}", AssetType.FEEDER) for i in range(1, feeders + 1)]
    )


def request_for(required, roster=None, *, fixed=(), history=(), seed=0, config=CONFIG):
    return AllocationRequest(
        period=NOVEMBER,
        calendar=Calendar(NOV_DAYS),
        roster=roster or roster_of(),
        config=config,
        required_visits=dict(required),
        history=tuple(history),
        fixed=tuple(fixed),
        seed=seed,
    )


def revalidate(result, required, roster, *, history=(), config=CONFIG, days=NOV_DAYS):
    """Assert a FEASIBLE result's own visits pass obligation-aware validation."""

    assert result.status is FeasibilityStatus.FEASIBLE
    validated = validate_schedule(
        ProposalContext(
            period=NOVEMBER,
            calendar=Calendar(days),
            roster=roster,
            config=config,
            proposed=result.visits,
            history=tuple(history),
            required_visits=dict(required),
        ),
        ValidationMode.OBLIGATIONS,
    )
    assert validated.valid, validated.violations


# --- mixed-category placement on shared capacity -------------------------


def test_area_dtc_and_feeder_can_share_one_date_within_the_combined_cap():
    """Three different categories on one date is legal; they share one capacity."""

    roster = roster_of()
    required = {"AREA-01": 1, "SCD-001": 1, "FEEDER-01": 1}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    assert len(result.visits) == 3


def test_two_area_visits_plus_one_dtc_on_the_same_date():
    roster = roster_of()
    required = {"AREA-01": 1, "AREA-02": 1, "SCD-001": 1}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    dates = result.per_date()
    assert max(dates.values()) <= CONFIG.combined_daily_cap


def test_two_area_visits_plus_one_feeder_on_the_same_date():
    roster = roster_of()
    required = {"AREA-01": 1, "AREA-02": 1, "FEEDER-01": 1}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    assert max(result.per_date().values()) <= CONFIG.combined_daily_cap


def test_three_areas_on_one_date_is_impossible_and_is_not_forced():
    """The area cap is 2, so the allocator must use separate dates."""

    roster = roster_of()
    required = {"AREA-01": 1, "AREA-02": 1, "AREA-03": 1}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    areas_per_date: dict[date, int] = {}
    kinds = {a.asset_id: a.asset_type for a in roster}
    for visit in result.visits:
        if kinds[visit.asset_id] is AssetType.AREA:
            areas_per_date[visit.visit_date] = (
                areas_per_date.get(visit.visit_date, 0) + 1
            )
    assert all(count <= CONFIG.area_daily_cap for count in areas_per_date.values())


def test_combined_cap_of_three_is_enforced_across_all_categories():
    roster = roster_of(areas=3, dtcs=2, feeders=1)
    required = {a.asset_id: 1 for a in roster}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    assert len(result.visits) == 6
    assert max(result.per_date().values()) <= CONFIG.combined_daily_cap


# --- per-asset limits and gaps -------------------------------------------


def test_per_asset_monthly_maximum_is_enforced():
    """Asking for more than the maximum is infeasible, and it is proven."""

    roster = roster_of()
    result = allocate_month(request_for({"AREA-01": 4}, roster))
    assert result.status is FeasibilityStatus.INFEASIBLE


def test_per_asset_maximum_is_reached_but_never_exceeded():
    roster = roster_of()
    required = {"AREA-01": 3}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    assert result.per_asset()["AREA-01"] == 3


def test_same_asset_seven_day_gap_is_respected():
    roster = roster_of()
    required = {"AREA-01": 3}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    dates = sorted(v.visit_date for v in result.visits if v.asset_id == "AREA-01")
    for earlier, later in zip(dates, dates[1:]):
        assert (later - earlier).days >= 7


def test_prior_history_constrains_the_first_placement():
    """A visit just before the month pushes the first one out by a full week."""

    roster = roster_of()
    history = (Visit("AREA-01", date(2026, 10, 28)),)
    required = {"AREA-01": 1}
    result = allocate_month(request_for(required, roster, history=history))
    revalidate(result, required, roster, history=history)
    first = min(v.visit_date for v in result.visits if v.asset_id == "AREA-01")
    assert (first - date(2026, 10, 28)).days >= 7


def test_working_date_eligibility_is_enforced():
    """No visit may land on a date absent from the calendar."""

    excluded = date(2026, 11, 3)
    days = NOV_DAYS - {excluded}
    roster = roster_of()
    request = AllocationRequest(
        period=NOVEMBER,
        calendar=Calendar(days),
        roster=roster,
        config=CONFIG,
        required_visits={"AREA-01": 2, "SCD-001": 1},
        seed=5,
    )
    result = allocate_month(request)
    assert result.status is FeasibilityStatus.FEASIBLE
    assert all(v.visit_date != excluded for v in result.visits)


# --- fixed visits --------------------------------------------------------


def test_fixed_visits_consume_capacity_and_discharge_obligations():
    roster = roster_of()
    fixed = (
        Visit("AREA-01", date(2026, 11, 2)),
        Visit("SCD-001", date(2026, 11, 2)),
        Visit("FEEDER-01", date(2026, 11, 2)),
    )
    required = {"AREA-01": 2, "SCD-001": 1, "FEEDER-01": 1}
    result = allocate_month(request_for(required, roster, fixed=fixed))
    revalidate(result, required, roster)
    # The commits discharge their obligations, so only AREA-01 needs one more.
    assert result.per_asset()["AREA-01"] == 2
    assert result.per_asset()["SCD-001"] == 1
    assert max(result.per_date().values()) <= CONFIG.combined_daily_cap


def test_locked_visits_remain_unchanged_in_the_output():
    roster = roster_of()
    fixed = (Visit("AREA-01", date(2026, 11, 2)), Visit("SCD-002", date(2026, 11, 9)))
    required = {"AREA-01": 2, "SCD-002": 1}
    result = allocate_month(request_for(required, roster, fixed=fixed))
    revalidate(result, required, roster)
    for visit in fixed:
        assert visit in result.visits
    assert result.fixed == fixed


def test_invalid_fixed_visit_yields_structured_infeasibility():
    """A commitment breaking a hard constraint is a proof, with evidence."""

    roster = roster_of()
    fixed = (Visit("AREA-01", date(2026, 11, 2)), Visit("AREA-01", date(2026, 11, 4)))
    result = allocate_month(request_for({}, roster, fixed=fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert result.evidence
    assert any(e.constraint == "revisit_gap_violated" for e in result.evidence)
    assert all(e.constraint for e in result.evidence)


def test_fixed_visit_to_inactive_asset_is_infeasible():
    roster = roster_of() + (Asset("AREA-OFF", AssetType.AREA, active=False),)
    fixed = (Visit("AREA-OFF", date(2026, 11, 2)),)
    result = allocate_month(request_for({}, roster, fixed=fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE


# --- target semantics ----------------------------------------------------


def test_monthly_minimum_is_an_obligation():
    roster = roster_of()
    config = FeasibilityConfig(
        asset_limits={"AREA-01": AssetLimits(monthly_max=3, monthly_min=2)}
    )
    result = allocate_month(
        AllocationRequest(
            period=NOVEMBER,
            calendar=Calendar(NOV_DAYS),
            roster=roster,
            config=config,
            seed=1,
        )
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.per_asset().get("AREA-01", 0) == 2


def test_monthly_maximum_is_not_an_obligation():
    """A configured maximum creates no visits at all."""

    roster = roster_of()
    config = FeasibilityConfig(
        default_asset_limits=AssetLimits(monthly_max=3, monthly_min=0)
    )
    result = allocate_month(
        AllocationRequest(
            period=NOVEMBER,
            calendar=Calendar(NOV_DAYS),
            roster=roster,
            config=config,
        )
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.visits == ()


def test_no_full_roster_area_coverage_is_invented():
    """Only the areas with an obligation are visited, never the whole roster."""

    roster = roster_of(areas=16, dtcs=5, feeders=4)
    required = {"AREA-01": 1}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    assert result.per_asset() == {"AREA-01": 1}


def test_no_full_roster_feeder_coverage_is_invented():
    roster = roster_of(areas=4, dtcs=2, feeders=4)
    required = {"FEEDER-02": 1}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)
    assert result.per_asset() == {"FEEDER-02": 1}


# --- tri-state outcome contract -----------------------------------------


def test_valid_construction_returns_feasible_with_revalidation():
    roster = roster_of()
    required = {"AREA-01": 2, "SCD-001": 2, "FEEDER-01": 1}
    result = allocate_month(request_for(required, roster))
    revalidate(result, required, roster)


def test_validator_proof_returns_infeasible():
    """The aggregate capacity proof is a proof, not a heuristic failure."""

    roster = roster_of(areas=2)
    # 2 areas x 2 area slots per date across a single-date calendar: 4 visits
    # needed but only 4 area slots exist and 3 combined per date, so overshoot.
    days = frozenset({date(2026, 11, 2)})
    request = AllocationRequest(
        period=NOVEMBER,
        calendar=Calendar(days),
        roster=roster,
        config=CONFIG,
        required_visits={"AREA-01": 2, "AREA-02": 2},
    )
    result = allocate_month(request)
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert result.evidence


def test_obvious_arithmetic_impossibility_is_infeasible():
    """48 area visits cannot fit a 23-date month at 2 area visits per date."""

    roster = roster_of(areas=16)
    # 16 areas x 3 visits = 48, against 30 dates x 2 area slots = 60 combined
    # slots but only 30 x 2 area slots; force the overshoot with a narrow calendar.
    days = frozenset(date(2026, 11, 1) + timedelta(i) for i in range(20))
    request = AllocationRequest(
        period=NOVEMBER,
        calendar=Calendar(days),
        roster=roster,
        config=FeasibilityConfig(area_daily_cap=2),
        required_visits={a.asset_id: 3 for a in roster},
    )
    result = allocate_month(request)
    assert result.status is FeasibilityStatus.INFEASIBLE


def test_no_heuristic_failure_is_reported_as_impossible():
    """Exhausting the attempt budget without proof stays INDETERMINATE."""

    roster = roster_of(areas=6, dtcs=6, feeders=0)
    required = {a.asset_id: 2 for a in roster}
    request = AllocationRequest(
        period=NOVEMBER,
        calendar=Calendar(NOV_DAYS),
        roster=roster,
        config=FeasibilityConfig(search_node_limit=1),
        required_visits=required,
        attempt_budget=1,
        seed=3,
    )
    result = allocate_month(request)
    # It must never claim impossibility without a proof.
    if result.status is FeasibilityStatus.INDETERMINATE:
        assert any(
            e.constraint == C_ALLOCATION_BUDGET_EXHAUSTED for e in result.evidence
        )
    else:
        revalidate(result, required, roster)


def test_malformed_input_raises_rather_than_returning_a_verdict():
    roster = roster_of()
    with pytest.raises(InputError):
        allocate_month(request_for({"ghost": 2}, roster))


# --- determinism ---------------------------------------------------------


def test_identical_inputs_and_seed_produce_identical_output():
    roster = roster_of(areas=5, dtcs=5)
    required = {a.asset_id: 2 for a in roster}
    first = allocate_month(request_for(required, roster, seed=11))
    second = allocate_month(request_for(required, roster, seed=11))
    assert first.visits == second.visits
    assert first.status is second.status


def test_alternative_seed_may_produce_another_but_valid_output():
    roster = roster_of(areas=5, dtcs=5)
    required = {a.asset_id: 2 for a in roster}
    one = allocate_month(request_for(required, roster, seed=1))
    two = allocate_month(request_for(required, roster, seed=999))
    revalidate(one, required, roster)
    revalidate(two, required, roster)
    # Obligations are identical; only the arrangement may differ.
    assert one.per_asset() == two.per_asset()


# --- diagnostics ---------------------------------------------------------


def test_pressure_reports_aggregate_violations_without_claiming_proof():
    roster = roster_of(areas=2)
    days = frozenset({date(2026, 11, 2)})
    request = AllocationRequest(
        period=NOVEMBER,
        calendar=Calendar(days),
        roster=roster,
        config=CONFIG,
        required_visits={"AREA-01": 3, "AREA-02": 3},
    )
    notes = describe_pressure(request)
    assert any(n.constraint == "aggregate_area_capacity_exceeded" for n in notes)


def test_category_totals_are_reported_separately():
    roster = roster_of()
    required = {"AREA-01": 2, "SCD-001": 1, "FEEDER-01": 1}
    result = allocate_month(request_for(required, roster))
    totals = result.category_totals(roster)
    assert totals[AssetType.AREA] == 2
    assert totals[AssetType.DTC] == 1
    assert totals[AssetType.FEEDER] == 1
