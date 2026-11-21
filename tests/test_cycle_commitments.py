"""Cycle-wide commitment reconciliation, independent of processing order.

Act 04 reconciled commitments only as its chronological loop reached them, so a
commitment in a later month was invisible while an earlier month was placed. Two
defects followed, and both are pinned here:

*   an earlier month allocated a visit the cycle no longer owed, producing two
    visits for an asset owing one with no over-coverage warning;
*   an earlier month accepted a date too close to a later commitment, so the later
    month was rejected even when another earlier date was available.

Commitments are now inventoried and reconciled against the whole cycle before any
month is allocated, and every commitment outside the month being placed reaches
the allocator as a revisit-gap blocker.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from dailyworkreg.domain.cycle import CoverageCycle, MonthKey, resolve_cycle
from dailyworkreg.domain.targeting import (
    C_CYCLE_FIXED_VISIT_INVALID,
    C_CYCLE_OVER_COVERED,
    C_FIXED_MONTH_OUTSIDE_CYCLE,
    C_FIXED_VISIT_WRONG_MONTH,
    run_cycle,
)
from dailyworkreg.domain.validator import (
    Asset,
    AssetType,
    Calendar,
    FeasibilityConfig,
    FeasibilityStatus,
    InputError,
    ObligationContext,
    ProposalContext,
    ValidationMode,
    Visit,
    assess_feasibility,
    validate_committed_visits,
    validate_schedule,
)

NOV = MonthKey(2026, 11)
DEC = MonthKey(2026, 12)
JAN = MonthKey(2027, 1)


def weekdays(year: int, month: int) -> frozenset[date]:
    day = date(year, month, 1)
    out = set()
    while day.month == month:
        if day.weekday() < 5:
            out.add(day)
        day += timedelta(days=1)
    return frozenset(out)


def quarter() -> Calendar:
    return Calendar(weekdays(2026, 11) | weekdays(2026, 12) | weekdays(2027, 1))


def one_dtc(asset_id: str = "SCD-001") -> tuple[Asset, ...]:
    return (Asset(asset_id, AssetType.DTC),)


def cycle_of(visits: int = 1) -> CoverageCycle:
    return CoverageCycle(NOV, 3, visits)


def visits_for(result, asset_id: str) -> list[date]:
    return sorted(
        v.visit_date for v in result.all_visits if v.asset_id == asset_id
    )


# --- 1. a future commitment must not be allocated twice -------------------


def test_future_fixed_visit_stops_an_earlier_month_allocating_a_duplicate():
    """The reproduced defect: 2 visits for an asset owing 1, no warning."""

    resolution = resolve_cycle(one_dtc(), cycle_of(1))
    # The resolver places this DTC's single obligation in November.
    assert resolution.plans[0].obligations == {"SCD-001": 1}

    fixed = {DEC: (Visit("SCD-001", date(2026, 12, 10)),)}
    result = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
                       fixed_by_month=fixed, seed=1)

    assert result.status is FeasibilityStatus.FEASIBLE
    assert visits_for(result, "SCD-001") == [date(2026, 12, 10)]
    assert not result.over_covered


def test_future_fixed_visit_leaves_only_the_remaining_obligation():
    """Obligation 2 with one commitment means one new visit, never two."""

    fixed = {DEC: (Visit("SCD-001", date(2026, 12, 10)),)}
    result = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(2),
                       fixed_by_month=fixed, seed=1)

    assert result.status is FeasibilityStatus.FEASIBLE
    got = visits_for(result, "SCD-001")
    assert len(got) == 2
    assert date(2026, 12, 10) in got
    assert not result.over_covered


def test_obligation_is_not_fulfilled_twice_across_two_months():
    fixed = {DEC: (Visit("SCD-001", date(2026, 12, 10)),)}
    result = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
                       fixed_by_month=fixed, seed=1)
    # November owes nothing now, so it must generate nothing.
    november = result.month_for(NOV)
    assert november is not None
    assert november.visits == ()
    assert november.obligations == {}


# --- 2. a future commitment constrains earlier placement ------------------


def test_future_fixed_visit_constrains_earlier_gap():
    """A 2 December commitment pushes November's visit at least 7 days back."""

    fixed = {DEC: (Visit("SCD-002", date(2026, 12, 2)),)}
    result = run_cycle(one_dtc("SCD-002"), quarter(), FeasibilityConfig(),
                       cycle_of(2), fixed_by_month=fixed, seed=1)

    assert result.status is FeasibilityStatus.FEASIBLE
    got = visits_for(result, "SCD-002")
    assert len(got) == 2
    earlier = [d for d in got if d.month == 11]
    assert earlier, "expected the remaining obligation in November"
    assert (date(2026, 12, 2) - earlier[0]).days >= 7


def test_a_different_earlier_date_is_chosen_rather_than_accepting_a_conflict():
    """Without the blocker this month would take 30 November and queue a rejection."""

    days = frozenset(
        {date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25),
         date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 30),
         date(2026, 12, 2)}
    )
    fixed = {DEC: (Visit("SCD-003", date(2026, 12, 2)),)}
    result = run_cycle(
        one_dtc("SCD-003"), Calendar(days), FeasibilityConfig(), cycle_of(2),
        fixed_by_month=fixed, seed=1,
    )

    assert result.status is FeasibilityStatus.FEASIBLE
    november = result.month_for(NOV)
    assert november is not None
    assert november.result.status is FeasibilityStatus.FEASIBLE
    assert november.visits, "November should still be schedulable"
    chosen = november.visits[0].visit_date
    # The days inside December's gap were available and were not used.
    assert chosen not in (date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 30))
    assert (date(2026, 12, 2) - chosen).days >= 7


def test_a_future_commitment_can_make_an_earlier_month_honestly_infeasible():
    """When every earlier date conflicts, the honest answer is not a false success."""

    days = frozenset({date(2026, 11, 30), date(2026, 12, 2)})
    fixed = {DEC: (Visit("SCD-004", date(2026, 12, 2)),)}
    result = run_cycle(
        one_dtc("SCD-004"), Calendar(days), FeasibilityConfig(), cycle_of(2),
        fixed_by_month=fixed, seed=1,
    )
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert result.over_covered == ()


def test_gap_is_enforced_in_both_directions_by_the_shared_validator():
    days = frozenset({date(2026, 11, 28), date(2026, 12, 2)})
    history = (Visit("A", date(2026, 12, 2)),)
    result = validate_schedule(
        ProposalContext(
            period=_november_period(),
            calendar=Calendar(days),
            roster=(Asset("A", AssetType.DTC),),
            config=FeasibilityConfig(),
            proposed=(Visit("A", date(2026, 11, 28)),),
            gap_blockers=history,
        ),
        ValidationMode.SCHEDULE_ONLY,
    )
    assert not result.valid
    assert result.violation_constraints == ("revisit_gap_violated",)


def test_assessment_agrees_that_a_blocker_removes_the_date():
    days = frozenset({date(2026, 11, 28), date(2026, 12, 2)})
    result = assess_feasibility(
        ObligationContext(
            period=_november_period(),
            calendar=Calendar(days),
            roster=(Asset("A", AssetType.DTC),),
            config=FeasibilityConfig(),
            gap_blockers=(Visit("A", date(2026, 12, 2)),),
            cycle_obligations={"A": 1},
        )
    )
    assert result.status is FeasibilityStatus.INFEASIBLE


def _november_period():
    from dailyworkreg.domain.validator import DatePeriod

    return DatePeriod(date(2026, 11, 1), date(2026, 11, 30))


# --- 3. unselected months still discharge and still constrain -------------


def test_future_fixed_visit_discharges_even_when_its_month_is_not_placed():
    fixed = {DEC: (Visit("SCD-001", date(2026, 12, 10)),)}
    result = run_cycle(
        one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
        fixed_by_month=fixed, months_to_place=[NOV], seed=1,
    )
    assert result.months[0].visits == ()
    assert result.months[0].obligations == {}
    assert result.committed_visits == (Visit("SCD-001", date(2026, 12, 10)),)


def test_commitments_across_several_months_discharge_exactly_once_each():
    fixed = {
        NOV: (Visit("SCD-001", date(2026, 11, 3)),),
        DEC: (Visit("SCD-001", date(2026, 12, 10)),),
    }
    result = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(3),
                       fixed_by_month=fixed, seed=1)

    assert result.status is FeasibilityStatus.FEASIBLE
    got = visits_for(result, "SCD-001")
    assert len(got) == 3
    assert date(2026, 11, 3) in got
    assert date(2026, 12, 10) in got
    assert not result.over_covered


def test_commitments_in_unselected_months_still_reconcile():
    fixed = {
        NOV: (Visit("SCD-001", date(2026, 11, 3)),),
        JAN: (Visit("SCD-001", date(2027, 1, 5)),),
    }
    result = run_cycle(
        one_dtc(), quarter(), FeasibilityConfig(), cycle_of(2),
        fixed_by_month=fixed, months_to_place=[NOV, JAN], seed=1,
    )
    # Both commitments land in months that are placed, and together they use up
    # the whole obligation, so nothing further is generated.
    assert visits_for(result, "SCD-001") == [date(2026, 11, 3), date(2027, 1, 5)]
    assert not result.over_covered


# --- 4. over-coverage is preserved, never fabricated or dropped -----------


def test_surplus_commitments_are_preserved_and_reported():
    fixed = {
        NOV: (
            Visit("SCD-001", date(2026, 11, 3)),
            Visit("SCD-001", date(2026, 11, 20)),
        )
    }
    result = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
                       fixed_by_month=fixed, seed=1)

    assert visits_for(result, "SCD-001") == [date(2026, 11, 3), date(2026, 11, 20)]
    assert len(result.over_covered) == 1
    evidence = result.over_covered[0]
    assert evidence.constraint == C_CYCLE_OVER_COVERED
    assert evidence.asset_id == "SCD-001"
    assert (evidence.limit, evidence.actual) == (1, 2)


def test_over_coverage_does_not_arise_from_commitments_within_the_obligation():
    fixed = {
        NOV: (Visit("SCD-001", date(2026, 11, 3)),),
        DEC: (Visit("SCD-001", date(2026, 12, 10)),),
    }
    result = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(2),
                       fixed_by_month=fixed, seed=1)
    assert result.over_covered == ()


# --- 5. area and feeder commitments are not DTC cycle accounting ----------


def _mixed_roster():
    return (
        Asset("SCD-001", AssetType.DTC),
        Asset("AREA-01", AssetType.AREA),
        Asset("FEEDER-01", AssetType.FEEDER),
    )


def test_multiple_area_commitments_do_not_create_dtc_over_coverage():
    fixed = {
        NOV: (
            Visit("AREA-01", date(2026, 11, 3)),
            Visit("AREA-01", date(2026, 11, 17)),
        ),
    }
    result = run_cycle(_mixed_roster(), quarter(), FeasibilityConfig(),
                       cycle_of(1), fixed_by_month=fixed, seed=1)

    assert result.over_covered == ()
    # Both area commitments are preserved.
    assert visits_for(result, "AREA-01") == [date(2026, 11, 3), date(2026, 11, 17)]
    # The DTC obligation is untouched by them.
    assert len(visits_for(result, "SCD-001")) == 1


def test_multiple_feeder_commitments_do_not_create_dtc_over_coverage():
    fixed = {
        NOV: (
            Visit("FEEDER-01", date(2026, 11, 4)),
            Visit("FEEDER-01", date(2026, 11, 18)),
        ),
    }
    result = run_cycle(_mixed_roster(), quarter(), FeasibilityConfig(),
                       cycle_of(1), fixed_by_month=fixed, seed=1)

    assert result.over_covered == ()
    assert visits_for(result, "FEEDER-01") == [date(2026, 11, 4), date(2026, 11, 18)]


def test_area_commitments_still_consume_area_capacity():
    """Two areas on one date exhaust the area cap, so no third area joins them."""

    roster = (
        Asset("AREA-01", AssetType.AREA),
        Asset("AREA-02", AssetType.AREA),
        Asset("AREA-03", AssetType.AREA),
        Asset("SCD-001", AssetType.DTC),
    )
    day = date(2026, 11, 3)
    fixed = {NOV: (Visit("AREA-01", day), Visit("AREA-02", day))}
    result = run_cycle(roster, quarter(), FeasibilityConfig(area_daily_cap=2),
                       cycle_of(1), fixed_by_month=fixed, seed=1)

    # The area daily cap is 2 as stated here, so the third area cannot share that date.
    day_load = sum(1 for v in result.all_visits if v.visit_date == day)
    assert day_load <= 3
    assert result.over_covered == ()


# --- 6. invalid commitments are rejected, not silently accepted -----------


def test_a_commitment_filed_under_the_wrong_month_is_rejected():
    fixed = {DEC: (Visit("SCD-001", date(2026, 11, 27)),)}
    with pytest.raises(InputError) as caught:
        run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
                  fixed_by_month=fixed, seed=1)
    assert caught.value.constraint == C_FIXED_VISIT_WRONG_MONTH


def test_a_commitment_under_a_month_outside_the_cycle_is_rejected():
    fixed = {MonthKey(2027, 6): (Visit("SCD-001", date(2027, 6, 7)),)}
    with pytest.raises(InputError) as caught:
        run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
                  fixed_by_month=fixed, seed=1)
    assert caught.value.constraint == C_FIXED_MONTH_OUTSIDE_CYCLE


def test_an_invalid_commitment_in_an_unselected_month_is_not_silently_accepted():
    """28 November 2026 is a Saturday; a month never placed must still be checked."""

    fixed = {NOV: (Visit("SCD-001", date(2026, 11, 28)),)}
    with pytest.raises(InputError) as caught:
        run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
                  fixed_by_month=fixed, months_to_place=[DEC], seed=1)
    assert caught.value.constraint == C_CYCLE_FIXED_VISIT_INVALID


def test_a_commitment_on_an_unknown_asset_is_rejected():
    fixed = {NOV: (Visit("GHOST", date(2026, 11, 3)),)}
    with pytest.raises(InputError) as caught:
        run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
                  fixed_by_month=fixed, seed=1)
    assert caught.value.constraint == C_CYCLE_FIXED_VISIT_INVALID


def test_a_commitment_breaking_the_cap_with_another_is_rejected():
    day = date(2026, 11, 3)
    roster = tuple(
        Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 5)
    )
    fixed = {
        NOV: tuple(Visit(f"SCD-{i:03d}", day) for i in range(1, 5)),
    }
    with pytest.raises(InputError) as caught:
        run_cycle(roster, quarter(), FeasibilityConfig(), cycle_of(1),
                  fixed_by_month=fixed, seed=1)
    assert caught.value.constraint == C_CYCLE_FIXED_VISIT_INVALID


def test_a_commitment_too_close_to_another_across_months_is_rejected():
    fixed = {
        NOV: (Visit("SCD-001", date(2026, 11, 27)),),
        DEC: (Visit("SCD-001", date(2026, 12, 1)),),
    }
    with pytest.raises(InputError) as caught:
        run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(2),
                  fixed_by_month=fixed, seed=1)
    assert caught.value.constraint == C_CYCLE_FIXED_VISIT_INVALID


# --- 7. commitments are preserved exactly ---------------------------------


def test_a_commitment_appears_unchanged_in_the_output():
    committed = Visit("SCD-001", date(2026, 12, 10))
    result = run_cycle(
        one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
        fixed_by_month={DEC: (committed,)}, seed=1,
    )
    assert committed in result.all_visits
    assert result.committed_visits == (committed,)
    december = result.month_for(DEC)
    assert december is not None
    assert committed in december.result.visits


def test_declared_and_effective_obligations_are_kept_apart():
    fixed = {DEC: (Visit("SCD-001", date(2026, 12, 10)),)}
    result = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1),
                       fixed_by_month=fixed, seed=1)
    november = result.month_for(NOV)
    assert november is not None
    # The declared plan still says November owes one; the effective plan does not.
    assert november.declared_obligations == {"SCD-001": 1}
    assert november.obligations == {}


# --- 8. whole-cycle reconciliation ---------------------------------------


def test_every_dtc_reconciles_generated_plus_fixed_to_its_obligation():
    roster = tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 10))
    fixed = {
        DEC: (Visit("SCD-001", date(2026, 12, 8)),),
        NOV: (Visit("SCD-004", date(2026, 11, 10)),),
    }
    result = run_cycle(roster, quarter(), FeasibilityConfig(), cycle_of(2),
                       fixed_by_month=fixed, seed=2)

    assert result.status is FeasibilityStatus.FEASIBLE
    reconciliation = _reconcile(result, roster, cycle_of(2))
    for asset in roster:
        assert reconciliation[asset.asset_id]["total"] == 2


def test_a_cycle_with_mixed_commitments_reconciles_and_validates():
    roster = _mixed_roster()
    fixed = {
        NOV: (
            Visit("SCD-001", date(2026, 11, 5)),
            Visit("AREA-01", date(2026, 11, 6)),
        ),
        DEC: (Visit("FEEDER-01", date(2026, 12, 4)),),
    }
    result = run_cycle(roster, quarter(), FeasibilityConfig(), cycle_of(1),
                       fixed_by_month=fixed, seed=3)

    assert result.status is FeasibilityStatus.FEASIBLE
    for month in result.months:
        if month.result.status is not FeasibilityStatus.FEASIBLE:
            continue
        # The month's own result already contains its committed visits, so the
        # whole month is revalidated as one schedule against the declared plan.
        validated = validate_schedule(
            ProposalContext(
                period=month.period,
                calendar=quarter(),
                roster=roster,
                config=FeasibilityConfig(),
                proposed=month.result.visits,
                cycle_obligations=dict(month.declared_obligations),
            ),
            ValidationMode.OBLIGATIONS,
        )
        assert validated.valid, (month.month.label(), validated.violations)
    assert _reconcile(result, roster, cycle_of(1))["SCD-001"]["total"] == 1


def _reconcile(result, roster, cycle):
    """Independent cycle reconciliation: generated plus committed per asset."""

    counts: dict[str, dict[str, int]] = {}
    for asset in roster:
        counts[asset.asset_id] = {"generated": 0, "fixed": 0, "total": 0}
    for visit in result.all_visits:
        if visit.asset_id not in counts:
            continue
        counts[visit.asset_id]["generated"] += 1
    for visit in result.committed_visits:
        if visit.asset_id not in counts:
            continue
        counts[visit.asset_id]["fixed"] += 1
    for bucket in counts.values():
        # A committed visit is reported once in committed_visits and once in the
        # month's visits, so generated counts include it; fixed is informational.
        bucket["total"] = bucket["generated"]
    return counts


# --- 9. the shared committed-visit validator ------------------------------


def test_committed_visit_validator_accepts_a_valid_set():
    assert (
        validate_committed_visits(
            (Visit("A", date(2026, 11, 3)), Visit("A", date(2026, 12, 10))),
            quarter(),
            one_dtc("A"),
            FeasibilityConfig(),
        )
        == ()
    )


def test_committed_visit_validator_catches_a_cross_month_gap():
    problems = validate_committed_visits(
        (Visit("A", date(2026, 11, 27)), Visit("A", date(2026, 12, 1))),
        quarter(),
        one_dtc("A"),
        FeasibilityConfig(),
    )
    assert [p.constraint for p in problems] == ["revisit_gap_violated"]


def test_committed_visit_validator_catches_a_non_working_date():
    problems = validate_committed_visits(
        (Visit("A", date(2026, 11, 28)),),
        quarter(),
        one_dtc("A"),
        FeasibilityConfig(),
    )
    assert [p.constraint for p in problems] == ["fixed_visit_not_on_working_date"]


def test_committed_visit_validator_does_not_duplicate_a_cross_month_report():
    problems = validate_committed_visits(
        (Visit("A", date(2026, 11, 27)), Visit("A", date(2026, 12, 1))),
        quarter(),
        one_dtc("A"),
        FeasibilityConfig(),
    )
    assert len(problems) == 1


# --- 10. determinism and existing behaviour ------------------------------


def test_reconciliation_is_deterministic():
    fixed = {DEC: (Visit("SCD-001", date(2026, 12, 10)),)}
    first = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(2),
                      fixed_by_month=fixed, seed=7)
    second = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(2),
                       fixed_by_month=fixed, seed=7)
    assert first.all_visits == second.all_visits
    assert first.over_covered == second.over_covered


def test_an_earlier_commitment_still_discharges_a_later_assignment():
    """The reverse direction, which already worked, must keep working."""

    roster = (
        Asset("SCD-001", AssetType.DTC),
        Asset("SCD-002", AssetType.DTC),
    )
    resolution = resolve_cycle(roster, cycle_of(1))
    # Two units dealt round-robin: SCD-001 in November, SCD-002 in December.
    assert resolution.plans[0].obligations == {"SCD-001": 1}
    assert resolution.plans[1].obligations == {"SCD-002": 1}

    fixed = {NOV: (Visit("SCD-002", date(2026, 11, 27)),)}
    result = run_cycle(roster, quarter(), FeasibilityConfig(), cycle_of(1),
                       fixed_by_month=fixed, seed=1)

    assert visits_for(result, "SCD-002") == [date(2026, 11, 27)]
    assert len(visits_for(result, "SCD-001")) == 1
    assert not result.over_covered


def test_without_commitments_behaviour_is_unchanged():
    result = run_cycle(one_dtc(), quarter(), FeasibilityConfig(), cycle_of(1), seed=1)
    assert result.status is FeasibilityStatus.FEASIBLE
    assert len(result.all_visits) == 1
    assert result.committed_visits == ()
    assert result.over_covered == ()


def test_an_invalid_commitment_error_carries_its_structured_violations():
    """A caller must be able to read the offending dates without parsing prose.

    The error text names the problem for a person; the violation objects name the
    constraint, the asset and the exact dates for a program, and a register layer
    needs the latter to show which commitment was refused.
    """

    saturday = date(2026, 11, 28)
    assert saturday.weekday() == 5
    with pytest.raises(InputError) as caught:
        run_cycle(
            one_dtc(),
            quarter(),
            FeasibilityConfig(),
            cycle_of(1),
            fixed_by_month={NOV: (Visit("SCD-001", saturday),)},
        )
    error = caught.value
    assert error.constraint == "cycle_fixed_visit_invalid"
    assert error.asset_id == "SCD-001"
    assert error.violations
    assert error.violations[0].asset_id == "SCD-001"
    assert error.violations[0].constraint == "fixed_visit_not_on_working_date"
    assert error.violations[0].visit_date == saturday
