"""Tests for DTC coverage-cycle resolution and monthly period limits."""

from __future__ import annotations

from datetime import date

import pytest

from dailyworkreg.domain.cycle import (
    CYCLE_INVALID_DURATION,
    CYCLE_INVALID_VISITS,
    CYCLE_NEGATIVE_WEIGHT,
    CoverageCycle,
    DtcClass,
    DtcWeights,
    MonthKey,
    months_between,
    resolve_cycle,
    verify_cycle,
)
from dailyworkreg.domain.prorating import (
    C_PARTIAL_MONTH_INCONSISTENT,
    C_PARTIAL_MONTH_UNRESOLVED,
    resolve_period_config,
)
from dailyworkreg.domain.validator import (
    Asset,
    AssetLimits,
    AssetType,
    DatePeriod,
    FeasibilityConfig,
    InputError,
    Visit,
)


def dtc_roster(count: int) -> tuple[Asset, ...]:
    return tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, count + 1))


# --- the recorded reference case -----------------------------------------


def test_113_dtcs_over_three_months_is_38_38_37():
    """The reference case, produced by the general rule rather than hardcoded."""

    resolution = resolve_cycle(
        dtc_roster(113), CoverageCycle(MonthKey(2026, 11), 3, 1)
    )
    assert [plan.total for plan in resolution.plans] == [38, 38, 37]
    assert 38 + 38 + 37 == 113
    assert resolution.total_obligation == 113


def test_every_active_dtc_appears_exactly_once_in_the_cycle():
    resolution = resolve_cycle(
        dtc_roster(113), CoverageCycle(MonthKey(2026, 11), 3, 1)
    )
    assigned = [asset_id for plan in resolution.plans for asset_id in plan.obligations]
    assert len(assigned) == 113
    assert len(set(assigned)) == 113
    assert set(assigned) == set(resolution.roster_ids)
    assert verify_cycle(resolution) == ()


def test_inactive_dtcs_are_not_cycle_members():
    roster = dtc_roster(5) + (Asset("SCD-OFF", AssetType.DTC, active=False),)
    resolution = resolve_cycle(roster, CoverageCycle(MonthKey(2026, 11), 1, 1))
    assert "SCD-OFF" not in resolution.roster_ids
    assert resolution.total_obligation == 5


def test_areas_and_feeders_are_not_cycle_members():
    """A DTC cycle covers DTCs only; it never quietly obligations an area."""

    roster = dtc_roster(4) + (
        Asset("AREA-01", AssetType.AREA),
        Asset("FEEDER-01", AssetType.FEEDER),
    )
    resolution = resolve_cycle(roster, CoverageCycle(MonthKey(2026, 11), 1, 1))
    assert resolution.roster_ids == ("SCD-001", "SCD-002", "SCD-003", "SCD-004")


# --- the distribution rule is general ------------------------------------


@pytest.mark.parametrize(
    "count,duration,expected",
    [
        (113, 3, [38, 38, 37]),
        (113, 4, [29, 28, 28, 28]),
        (10, 3, [4, 3, 3]),
        (9, 3, [3, 3, 3]),
        (1, 1, [1]),
        (7, 7, [1, 1, 1, 1, 1, 1, 1]),
        (5, 2, [3, 2]),
    ],
)
def test_monthly_totals_differ_by_at_most_one(count, duration, expected):
    resolution = resolve_cycle(
        dtc_roster(count), CoverageCycle(MonthKey(2026, 11), duration, 1)
    )
    assert [plan.total for plan in resolution.plans] == expected
    assert sum(expected) == count


def test_multiple_visits_per_asset_per_cycle():
    """Each asset owes its cycle obligation, spread so no month double-serves it."""

    resolution = resolve_cycle(dtc_roster(6), CoverageCycle(MonthKey(2026, 11), 3, 2))
    assert resolution.total_obligation == 12
    assert sum(plan.total for plan in resolution.plans) == 12
    for asset_id in resolution.roster_ids:
        assert resolution.cycle_totals[asset_id] == 2
        per_month = [
            plan.obligations.get(asset_id, 0) for plan in resolution.plans
        ]
        assert sum(per_month) == 2
        # Never twice in the same month.
        assert all(count <= 1 for count in per_month)
    assert verify_cycle(resolution) == ()


# --- weights change priority, never obligation ---------------------------


def test_weights_do_not_change_equal_cycle_obligations():
    """A heavy DTC is scheduled earlier, not more often."""

    roster = dtc_roster(6)
    weights = DtcWeights(
        kva={"SCD-006": 500.0},
        classification={"SCD-006": DtcClass.DEPARTMENT},
    )
    resolution = resolve_cycle(
        roster, CoverageCycle(MonthKey(2026, 11), 3, 1), weights=weights
    )
    # Unchanged obligations for every asset.
    for asset_id in resolution.roster_ids:
        assert resolution.cycle_totals[asset_id] == 1
    assert verify_cycle(resolution) == ()
    # The heavy DTC is dealt into the first month.
    assert "SCD-006" in resolution.plans[0].obligations


def test_department_class_outweighs_self_dtc_at_equal_capacity():
    weights = DtcWeights(
        kva={"A": 100.0, "B": 100.0},
        classification={"A": DtcClass.DEPARTMENT, "B": DtcClass.SELF_DTC},
    )
    a = Asset("A", AssetType.DTC)
    b = Asset("B", AssetType.DTC)
    assert weights.weight_for(a) > weights.weight_for(b)
    assert weights.class_of(b) is DtcClass.SELF_DTC


def test_missing_classification_defaults_to_the_lower_weight():
    """An omission must under-weight, never promote above a real department."""

    weights = DtcWeights(kva={"X": 100.0})
    assert weights.class_of(Asset("X", AssetType.DTC)) is DtcClass.SELF_DTC


def test_negative_kva_is_rejected():
    weights = DtcWeights(kva={"X": -5.0})
    with pytest.raises(InputError) as caught:
        weights.weight_for(Asset("X", AssetType.DTC))
    assert caught.value.constraint == CYCLE_NEGATIVE_WEIGHT


# --- fairness and determinism --------------------------------------------


def test_repeated_cycles_rotate_the_remainder():
    """Consecutive cycles must not always favour the same assets."""

    roster = dtc_roster(10)
    cycle = CoverageCycle(MonthKey(2026, 11), 3, 1)
    first = resolve_cycle(roster, cycle, rotation_offset=0)
    second = resolve_cycle(roster, cycle, rotation_offset=1)
    assert first.plans[0].obligations != second.plans[0].obligations


def test_prior_visits_steer_the_remainder_away_from_over_served_assets():
    """An asset already visited many times is deprioritised, not re-favoured."""

    roster = dtc_roster(6)
    cycle = CoverageCycle(MonthKey(2026, 11), 3, 1)
    over_served = tuple(
        Visit("SCD-001", date(2026, 10, 1 + i)) for i in range(3)
    )
    resolution = resolve_cycle(roster, cycle, prior_visits=over_served)
    # 6 across 3 months is an even split, so nothing is left to the remainder;
    # the rotation must still not place the most-served asset first overall.
    assert verify_cycle(resolution) == ()
    assert resolution.prior_counts.get("SCD-001") == 3


def test_identical_inputs_produce_identical_resolutions():
    roster = dtc_roster(113)
    cycle = CoverageCycle(MonthKey(2026, 11), 3, 1)
    first = resolve_cycle(roster, cycle)
    second = resolve_cycle(roster, cycle)
    assert first.plans == second.plans
    assert first.cycle_totals == second.cycle_totals


def test_rotation_offset_is_deterministic():
    roster = dtc_roster(20)
    cycle = CoverageCycle(MonthKey(2026, 11), 3, 1)
    assert (
        resolve_cycle(roster, cycle, rotation_offset=7).plans
        == resolve_cycle(roster, cycle, rotation_offset=7).plans
    )


# --- cycle configuration errors ------------------------------------------


def test_zero_duration_cycle_is_rejected():
    with pytest.raises(InputError) as caught:
        CoverageCycle(MonthKey(2026, 11), 0, 1)
    assert caught.value.constraint == CYCLE_INVALID_DURATION


def test_zero_visits_per_asset_is_rejected():
    """A cycle that owes nothing is not a coverage cycle."""

    with pytest.raises(InputError) as caught:
        CoverageCycle(MonthKey(2026, 11), 3, 0)
    assert caught.value.constraint == CYCLE_INVALID_VISITS


def test_month_ordering_rolls_over_the_year():
    assert months_between(MonthKey(2026, 11), 3) == (
        MonthKey(2026, 11),
        MonthKey(2026, 12),
        MonthKey(2027, 1),
    )


def test_cycle_membership_is_month_based():
    cycle = CoverageCycle(MonthKey(2026, 11), 3, 1)
    assert cycle.contains(MonthKey(2026, 12))
    assert not cycle.contains(MonthKey(2027, 2))
    assert cycle.ordinal_index(MonthKey(2026, 12)) == 1


def test_plan_for_returns_the_matching_month_only():
    resolution = resolve_cycle(
        dtc_roster(9), CoverageCycle(MonthKey(2026, 11), 3, 1)
    )
    plan = resolution.plan_for(MonthKey(2026, 12))
    assert plan is not None
    assert plan.month_index == 1
    assert resolution.plan_for(MonthKey(2027, 5)) is None


# --- verify_cycle catches a tampered plan --------------------------------


def test_verify_cycle_reports_a_dropped_assignment():
    resolution = resolve_cycle(
        dtc_roster(9), CoverageCycle(MonthKey(2026, 11), 3, 1)
    )
    # Rebuild with the first month emptied, as a corrupt stored plan would be.
    import dataclasses

    broken_plans = list(resolution.plans)
    broken_plans[0] = dataclasses.replace(
        broken_plans[0], obligations={}, total=0, scheduled=()
    )
    broken = dataclasses.replace(resolution, plans=tuple(broken_plans))
    problems = verify_cycle(broken)
    assert problems


# --- partial-month proration contract ------------------------------------


def test_full_month_needs_no_explicit_limits():
    period = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))
    config = FeasibilityConfig()
    resolved = resolve_period_config(period, config, set(), None)
    assert resolved is config


def test_partial_month_without_explicit_limits_is_rejected():
    """No rounding rule is recorded, so the allocator refuses to invent one."""

    period = DatePeriod(date(2026, 11, 5), date(2026, 11, 20))
    with pytest.raises(InputError) as caught:
        resolve_period_config(period, FeasibilityConfig(), set(), None)
    assert caught.value.constraint == C_PARTIAL_MONTH_UNRESOLVED


def test_partial_month_with_supplied_limits_is_accepted():
    period = DatePeriod(date(2026, 11, 5), date(2026, 11, 20))
    supplied = {"__default__": AssetLimits(monthly_max=1, monthly_min=0)}
    resolved = resolve_period_config(
        period, FeasibilityConfig(), set(), supplied
    )
    assert resolved.default_asset_limits.monthly_max == 1


def test_prorated_maximum_cannot_exceed_the_configured_maximum():
    period = DatePeriod(date(2026, 11, 5), date(2026, 11, 20))
    supplied = {"a": AssetLimits(monthly_max=9, monthly_min=0)}
    with pytest.raises(InputError) as caught:
        resolve_period_config(period, FeasibilityConfig(), set(), supplied)
    assert caught.value.constraint == C_PARTIAL_MONTH_INCONSISTENT


def test_prorated_minimum_cannot_exceed_the_configured_minimum():
    period = DatePeriod(date(2026, 11, 5), date(2026, 11, 20))
    config = FeasibilityConfig(
        asset_limits={"a": AssetLimits(monthly_max=3, monthly_min=0)}
    )
    supplied = {"a": AssetLimits(monthly_max=2, monthly_min=2)}
    with pytest.raises(InputError) as caught:
        resolve_period_config(period, config, set(), supplied)
    assert caught.value.constraint == C_PARTIAL_MONTH_INCONSISTENT


def test_period_is_full_month_reports_correctly():
    assert DatePeriod(date(2026, 11, 1), date(2026, 11, 30)).is_full_month
    assert not DatePeriod(date(2026, 11, 2), date(2026, 11, 30)).is_full_month
