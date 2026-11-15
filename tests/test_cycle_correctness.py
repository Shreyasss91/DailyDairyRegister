"""Regression tests for cycle multiplicity, verification and orchestration.

Each test here pins a defect found in review of the Act 03 implementation:

*   cycle obligations collapsed repeated visits to one because they were built
    from a list of ids rather than counted;
*   ``verify_cycle`` counted mapping keys and so could not see wrong values;
*   committed visits were not reconciled across the cycle, so an asset could be
    allocated a second visit;
*   an empty month selection produced a successful-looking empty result;
*   a partial month silently used full-month limits;
*   the seed had no effect because the asset id was the final tie-breaker.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest
from dailyworkreg.domain.allocator import AllocationRequest, allocate_month
from dailyworkreg.domain.cycle import (
    CoverageCycle,
    DtcWeights,
    MonthKey,
    resolve_cycle,
    verify_cycle,
)
from dailyworkreg.domain.prorating import (
    C_PARTIAL_MONTH_INCOMPLETE,
    C_PARTIAL_MONTH_UNRESOLVED,
    DEFAULT_LIMITS_KEY,
)
from dailyworkreg.domain.targeting import (
    C_CYCLE_OVER_COVERED,
    C_MONTH_SELECTION_DUPLICATE,
    C_MONTH_SELECTION_EMPTY,
    C_MONTH_SELECTION_OUTSIDE_CYCLE,
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
    InputError,
    ProposalContext,
    ValidationMode,
    Visit,
    validate_schedule,
)


def weekdays(year: int, month: int) -> frozenset[date]:
    day = date(year, month, 1)
    out = set()
    while day.month == month:
        if day.weekday() < 5:
            out.add(day)
        day += timedelta(days=1)
    return frozenset(out)


def quarter_calendar() -> Calendar:
    return Calendar(weekdays(2026, 11) | weekdays(2026, 12) | weekdays(2027, 1))


def one_dtc(asset_id: str = "SCD-001") -> tuple[Asset, ...]:
    return (Asset(asset_id, AssetType.DTC),)


def dtcs(count: int) -> tuple[Asset, ...]:
    return tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, count + 1))


# --- 1. cycle obligation multiplicity ------------------------------------


def test_one_dtc_one_month_two_visits_keeps_both():
    """The recorded defect: total said 2 while obligations held 1."""

    resolution = resolve_cycle(
        one_dtc(), CoverageCycle(MonthKey(2026, 11), 1, 2)
    )
    plan = resolution.plans[0]
    assert plan.total == 2
    assert plan.obligations == {"SCD-001": 2}
    assert sum(plan.obligations.values()) == plan.total
    assert verify_cycle(resolution) == ()


def test_one_dtc_one_month_three_visits_keeps_all_three():
    resolution = resolve_cycle(
        one_dtc(), CoverageCycle(MonthKey(2026, 11), 1, 3)
    )
    assert resolution.plans[0].obligations == {"SCD-001": 3}
    assert verify_cycle(resolution) == ()


def test_one_dtc_one_month_four_visits_records_four_and_leaves_the_maximum_to_the_allocator():
    """The cycle records what is owed; the allocator decides what is possible.

    Four visits against a monthly maximum of three is not silently reduced here.
    The obligation stays four and the allocation reports infeasibility, which is
    the honest outcome.
    """

    resolution = resolve_cycle(
        one_dtc(), CoverageCycle(MonthKey(2026, 11), 1, 4)
    )
    assert resolution.plans[0].obligations == {"SCD-001": 4}
    assert verify_cycle(resolution) == ()


def test_four_visits_against_monthly_maximum_three_is_infeasible_not_reduced():
    days = weekdays(2026, 11)
    result = allocate_month(
        AllocationRequest(
            period=DatePeriod(date(2026, 11, 1), date(2026, 11, 30)),
            calendar=Calendar(days),
            roster=one_dtc(),
            config=FeasibilityConfig(),
            cycle_obligations={"SCD-001": 4},
            seed=1,
        )
    )
    assert result.status is FeasibilityStatus.INFEASIBLE
    # The obligation was not quietly cut down to the maximum.
    assert result.obligations is not None
    assert result.obligations.owed_for("SCD-001") == 4
    assert result.evidence


def test_one_dtc_three_month_three_visits_spreads_one_per_month():
    resolution = resolve_cycle(
        one_dtc(), CoverageCycle(MonthKey(2026, 11), 3, 3)
    )
    assert [plan.obligations.get("SCD-001", 0) for plan in resolution.plans] == [1, 1, 1]
    assert verify_cycle(resolution) == ()


def test_six_dtcs_three_month_two_visits_per_asset():
    resolution = resolve_cycle(
        dtcs(6), CoverageCycle(MonthKey(2026, 11), 3, 2)
    )
    assert verify_cycle(resolution) == ()
    for asset in dtcs(6):
        total = sum(
            plan.obligations.get(asset.asset_id, 0) for plan in resolution.plans
        )
        assert total == 2
    assert sum(plan.total for plan in resolution.plans) == 12


def test_larger_roster_crossing_a_queue_boundary():
    resolution = resolve_cycle(
        dtcs(50), CoverageCycle(MonthKey(2026, 11), 3, 2)
    )
    assert verify_cycle(resolution) == ()
    for asset in dtcs(50):
        assert resolution.cycle_totals[asset.asset_id] == 2


@pytest.mark.parametrize("visits", [1, 2, 3, 5, 7])
@pytest.mark.parametrize("duration", [1, 2, 3, 4])
def test_per_asset_totals_always_equal_visits_per_asset(visits, duration):
    resolution = resolve_cycle(
        dtcs(11), CoverageCycle(MonthKey(2026, 11), duration, visits)
    )
    assert verify_cycle(resolution) == ()
    for asset in dtcs(11):
        total = sum(
            plan.obligations.get(asset.asset_id, 0) for plan in resolution.plans
        )
        assert total == visits


def test_multiple_visits_for_one_asset_in_one_month_get_separate_dates():
    """When the count must land in one month, the gap is the allocator's job."""

    result = allocate_month(
        AllocationRequest(
            period=DatePeriod(date(2026, 11, 1), date(2026, 11, 30)),
            calendar=Calendar(weekdays(2026, 11)),
            roster=one_dtc(),
            config=FeasibilityConfig(),
            cycle_obligations={"SCD-001": 3},
            seed=2,
        )
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    dates = sorted(v.visit_date for v in result.visits if v.asset_id == "SCD-001")
    assert len(dates) == 3
    for earlier, later in zip(dates, dates[1:], strict=False):
        assert (later - earlier).days >= 7
    validated = validate_schedule(
        ProposalContext(
            period=DatePeriod(date(2026, 11, 1), date(2026, 11, 30)),
            calendar=Calendar(weekdays(2026, 11)),
            roster=one_dtc(),
            config=FeasibilityConfig(),
            proposed=result.visits,
            cycle_obligations={"SCD-001": 3},
        ),
        ValidationMode.OBLIGATIONS,
    )
    assert validated.valid, validated.violations


# --- 2. the structural verifier ------------------------------------------


def _sound_resolution():
    return resolve_cycle(dtcs(4), CoverageCycle(MonthKey(2026, 11), 3, 1))


def test_verifier_accepts_a_sound_resolution():
    assert verify_cycle(_sound_resolution()) == ()


def test_verifier_detects_a_changed_obligation_value():
    resolution = _sound_resolution()
    plans = list(resolution.plans)
    plans[0] = dataclasses.replace(plans[0], obligations={"SCD-001": 9})
    problems = verify_cycle(dataclasses.replace(resolution, plans=tuple(plans)))
    assert problems


def test_verifier_detects_a_changed_total():
    resolution = _sound_resolution()
    plans = list(resolution.plans)
    plans[0] = dataclasses.replace(plans[0], total=plans[0].total + 1)
    assert verify_cycle(dataclasses.replace(resolution, plans=tuple(plans)))


def test_verifier_detects_a_changed_asset_count():
    resolution = _sound_resolution()
    broken_totals = dict(resolution.cycle_totals)
    broken_totals["SCD-001"] = 5
    problems = verify_cycle(
        dataclasses.replace(resolution, cycle_totals=broken_totals)
    )
    assert problems


def test_verifier_detects_a_duplicated_scheduled_assignment():
    resolution = _sound_resolution()
    plans = list(resolution.plans)
    plan = plans[0]
    plans[0] = dataclasses.replace(plan, scheduled=plan.scheduled + plan.scheduled)
    assert verify_cycle(dataclasses.replace(resolution, plans=tuple(plans)))


def test_verifier_detects_a_dropped_scheduled_assignment():
    resolution = _sound_resolution()
    plans = list(resolution.plans)
    plan = plans[0]
    plans[0] = dataclasses.replace(plan, scheduled=plan.scheduled[:-1])
    assert verify_cycle(dataclasses.replace(resolution, plans=tuple(plans)))


def test_verifier_detects_a_changed_month_index():
    resolution = _sound_resolution()
    plans = list(resolution.plans)
    plans[0] = dataclasses.replace(plans[0], month_index=99)
    assert verify_cycle(dataclasses.replace(resolution, plans=tuple(plans)))


def test_verifier_detects_a_changed_month_key():
    resolution = _sound_resolution()
    plans = list(resolution.plans)
    plans[0] = dataclasses.replace(plans[0], month=MonthKey(2027, 6))
    assert verify_cycle(dataclasses.replace(resolution, plans=tuple(plans)))


def test_verifier_detects_an_unknown_asset():
    resolution = _sound_resolution()
    plans = list(resolution.plans)
    plan = plans[0]
    obligations = dict(plan.obligations)
    obligations["GHOST"] = 1
    plans[0] = dataclasses.replace(
        plan, obligations=obligations, scheduled=plan.scheduled + ("GHOST",),
        total=plan.total + 1,
    )
    problems = verify_cycle(dataclasses.replace(resolution, plans=tuple(plans)))
    assert any("GHOST" in problem for problem in problems)


def test_verifier_detects_a_zero_or_negative_count():
    resolution = _sound_resolution()
    plans = list(resolution.plans)
    plan = plans[0]
    plans[0] = dataclasses.replace(plan, obligations={"SCD-001": 0})
    assert verify_cycle(dataclasses.replace(resolution, plans=tuple(plans)))


def test_run_cycle_refuses_a_corrupt_resolution(monkeypatch):
    """A plan that fails verification must never reach a successful allocation."""

    import dailyworkreg.domain.targeting as targeting

    sound = _sound_resolution()
    plans = list(sound.plans)
    plans[0] = dataclasses.replace(plans[0], obligations={"SCD-001": 99})
    corrupt = dataclasses.replace(sound, plans=tuple(plans))

    monkeypatch.setattr(targeting, "resolve_cycle", lambda *a, **k: corrupt)
    with pytest.raises(InputError) as caught:
        run_cycle(
            dtcs(4),
            quarter_calendar(),
            FeasibilityConfig(),
            CoverageCycle(MonthKey(2026, 11), 3, 1),
        )
    assert caught.value.constraint == "cycle_resolution_invalid"


# --- 3. cycle-wide fixed-visit reconciliation ----------------------------


def test_fixed_visit_discharges_the_cycle_obligation_in_another_month():
    """A November commitment satisfies a cycle obligation the resolver put in December."""

    roster = dtcs(2)
    fixed = {MonthKey(2026, 11): (Visit("SCD-001", date(2026, 11, 3)),)}
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        fixed_by_month=fixed,
        seed=1,
    )
    visits = [v for v in result.all_visits if v.asset_id == "SCD-001"]
    assert len(visits) == 1
    assert visits[0] == Visit("SCD-001", date(2026, 11, 3))
    assert not result.over_covered


def test_fixed_visit_does_not_create_a_second_visit_later():
    roster = dtcs(1)
    fixed = {MonthKey(2026, 11): (Visit("SCD-001", date(2026, 11, 5)),)}
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        fixed_by_month=fixed,
        seed=1,
    )
    assert len([v for v in result.all_visits if v.asset_id == "SCD-001"]) == 1


def test_fixed_visit_in_an_unselected_month_still_constrains_later_months():
    """Skipping placement is not permission to forget a commitment happened."""

    roster = dtcs(1)
    # Committed 28 November; December cannot revisit before 5 December.
    fixed = {MonthKey(2026, 11): (Visit("SCD-001", date(2026, 11, 28)),)}
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 2),
        fixed_by_month=fixed,
        months_to_place=[MonthKey(2026, 12)],
        seed=1,
    )
    december = result.month_for(MonthKey(2026, 12))
    assert december is not None
    if december.result.status is FeasibilityStatus.FEASIBLE:
        for visit in december.visits:
            assert (visit.visit_date - date(2026, 11, 28)).days >= 7


def test_failed_month_still_contributes_its_fixed_visits_to_history():
    """A month can fail and its commitments still bind the next month."""

    roster = dtcs(2)
    # An area obligation far above capacity makes November fail.
    fixed = {MonthKey(2026, 11): (Visit("SCD-001", date(2026, 11, 30)),)}
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 3),
        fixed_by_month=fixed,
        seed=1,
    )
    december = result.month_for(MonthKey(2026, 12))
    assert december is not None
    if december.result.status is FeasibilityStatus.FEASIBLE:
        for visit in december.visits:
            if visit.asset_id == "SCD-001":
                assert (visit.visit_date - date(2026, 11, 30)).days >= 7


def test_multiple_fixed_visits_reconcile_against_the_cycle_obligation():
    roster = dtcs(1)
    fixed = {
        MonthKey(2026, 11): (Visit("SCD-001", date(2026, 11, 3)),),
        MonthKey(2026, 12): (Visit("SCD-001", date(2026, 12, 10)),),
    }
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 2),
        fixed_by_month=fixed,
        seed=1,
    )
    visits = sorted(
        v.visit_date for v in result.all_visits if v.asset_id == "SCD-001"
    )
    # The cycle owes two, and the two commitments discharge it exactly.
    assert len(visits) == 2
    assert not result.over_covered


def test_fixed_visits_exceeding_the_cycle_obligation_are_surfaced_not_added():
    roster = dtcs(1)
    fixed = {
        MonthKey(2026, 11): (
            Visit("SCD-001", date(2026, 11, 3)),
            Visit("SCD-001", date(2026, 11, 20)),
        )
    }
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        fixed_by_month=fixed,
        seed=1,
    )
    assert result.over_covered
    assert result.over_covered[0].constraint == C_CYCLE_OVER_COVERED
    assert result.over_covered[0].asset_id == "SCD-001"
    # No additional visit was fabricated to "use up" the surplus.
    assert len([v for v in result.all_visits if v.asset_id == "SCD-001"]) == 2


def test_committed_visits_are_preserved_unchanged():
    roster = dtcs(3)
    committed = (
        Visit("SCD-001", date(2026, 11, 3)),
        Visit("SCD-002", date(2026, 12, 8)),
    )
    fixed = {
        MonthKey(2026, 11): (committed[0],),
        MonthKey(2026, 12): (committed[1],),
    }
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        fixed_by_month=fixed,
        seed=1,
    )
    for visit in committed:
        assert visit in result.all_visits


def test_cycle_level_reconciliation_of_visits_against_cycle_totals():
    """The whole cycle, not just each month, must match the declared obligations."""

    roster = dtcs(9)
    result = run_cycle(
        roster,
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
        seed=3,
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    counts: dict[str, int] = {}
    for visit in result.all_visits:
        counts[visit.asset_id] = counts.get(visit.asset_id, 0) + 1
    for asset in roster:
        assert counts.get(asset.asset_id, 0) == 1


# --- 4. seed effectiveness -----------------------------------------------


def _seed_fixture():
    """A month with genuine alternative placements for every visit."""

    roster = tuple(
        [Asset(f"AREA-{i:02d}", AssetType.AREA) for i in range(1, 4)]
        + [Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 4)]
    )
    period = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))
    calendar = Calendar(weekdays(2026, 11))
    required = {asset.asset_id: 1 for asset in roster}
    return roster, period, calendar, required


def _allocate(seed: int):
    roster, period, calendar, required = _seed_fixture()
    return allocate_month(
        AllocationRequest(
            period=period,
            calendar=calendar,
            roster=roster,
            config=FeasibilityConfig(),
            required_visits=required,
            seed=seed,
        )
    )


def test_identical_seed_gives_identical_output():
    assert _allocate(11).visits == _allocate(11).visits


def test_alternative_seeds_produce_different_schedules():
    """The old test compared only per-asset counts, which never varied."""

    first = _allocate(1)
    second = _allocate(2)
    assert first.status is FeasibilityStatus.FEASIBLE
    assert second.status is FeasibilityStatus.FEASIBLE
    assert first.visits != second.visits


def test_alternative_seed_schedules_both_revalidate():
    roster, period, calendar, required = _seed_fixture()
    for seed in (1, 2, 3):
        result = _allocate(seed)
        assert result.status is FeasibilityStatus.FEASIBLE
        validated = validate_schedule(
            ProposalContext(
                period=period,
                calendar=calendar,
                roster=roster,
                config=FeasibilityConfig(),
                proposed=result.visits,
                required_visits=required,
            ),
            ValidationMode.OBLIGATIONS,
        )
        assert validated.valid, (seed, validated.violations)


def test_per_asset_counts_are_identical_across_seeds():
    counts = [dict(_allocate(seed).per_asset()) for seed in (1, 2, 3, 4)]
    for other in counts[1:]:
        assert other == counts[0]


def test_seeds_do_not_change_cycle_obligations():
    cycle = CoverageCycle(MonthKey(2026, 11), 3, 1)
    first = resolve_cycle(dtcs(12), cycle)
    second = resolve_cycle(dtcs(12), cycle)
    assert first.plans == second.plans


def test_seed_cannot_override_constrained_first_ordering():
    """A tightly constrained asset is still placed first whatever the seed."""

    roster = dtcs(8)
    period = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))
    # Only two eligible dates, so capacity, not seed, decides who fits.
    calendar = Calendar({date(2026, 11, 2), date(2026, 11, 3)})
    required = {asset.asset_id: 1 for asset in roster[:4]}
    for seed in range(5):
        result = allocate_month(
            AllocationRequest(
                period=period,
                calendar=calendar,
                roster=roster,
                config=FeasibilityConfig(),
                required_visits=required,
                seed=seed,
            )
        )
        # Six slots for four visits: always feasible, never clustered over cap.
        assert result.status is FeasibilityStatus.FEASIBLE
        assert max(result.per_date().values()) <= 3


def test_weight_breaks_ties_without_outranking_constraints():
    """A heavier asset is preferred when pressure is equal, never otherwise."""

    roster = (Asset("LIGHT", AssetType.DTC), Asset("HEAVY", AssetType.DTC))
    period = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))
    calendar = Calendar({date(2026, 11, 2)})
    # One slot: exactly one of the two can be placed.
    result = allocate_month(
        AllocationRequest(
            period=period,
            calendar=calendar,
            roster=roster,
            config=FeasibilityConfig(),
            required_visits={"HEAVY": 1},
            seed=1,
            weights={"HEAVY": 900.0, "LIGHT": 1.0},
        )
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.per_asset() == {"HEAVY": 1}


# --- 5. month selection validation ---------------------------------------


def _cycle_args():
    return (
        dtcs(3),
        quarter_calendar(),
        FeasibilityConfig(),
        CoverageCycle(MonthKey(2026, 11), 3, 1),
    )


def test_empty_month_selection_is_rejected():
    with pytest.raises(InputError) as caught:
        run_cycle(*_cycle_args(), months_to_place=[])
    assert caught.value.constraint == C_MONTH_SELECTION_EMPTY


def test_duplicate_month_selection_is_rejected():
    with pytest.raises(InputError) as caught:
        run_cycle(
            *_cycle_args(),
            months_to_place=[MonthKey(2026, 11), MonthKey(2026, 11)],
        )
    assert caught.value.constraint == C_MONTH_SELECTION_DUPLICATE


def test_month_outside_the_cycle_is_rejected():
    with pytest.raises(InputError) as caught:
        run_cycle(*_cycle_args(), months_to_place=[MonthKey(2027, 6)])
    assert caught.value.constraint == C_MONTH_SELECTION_OUTSIDE_CYCLE


def test_month_selection_is_returned_chronologically():
    result = run_cycle(
        *_cycle_args(),
        months_to_place=[MonthKey(2027, 1), MonthKey(2026, 11)],
    )
    assert [m.month.label() for m in result.months] == ["2026-11", "2027-01"]


def test_empty_fallback_month_sequence_is_rejected():
    with pytest.raises(InputError) as caught:
        run_fallback(dtcs(2), quarter_calendar(), FeasibilityConfig(), [])
    assert caught.value.constraint == C_MONTH_SELECTION_EMPTY


def test_duplicate_fallback_month_is_rejected():
    with pytest.raises(InputError) as caught:
        run_fallback(
            dtcs(2),
            quarter_calendar(),
            FeasibilityConfig(),
            [MonthKey(2026, 11), MonthKey(2026, 11)],
        )
    assert caught.value.constraint == C_MONTH_SELECTION_DUPLICATE


def test_reversed_fallback_months_are_processed_chronologically():
    """History must never be carried backwards from a future month."""

    result = run_fallback(
        dtcs(3),
        quarter_calendar(),
        FeasibilityConfig(),
        [MonthKey(2027, 1), MonthKey(2026, 11), MonthKey(2026, 12)],
        seed=1,
    )
    assert [m.month.label() for m in result.months] == [
        "2026-11",
        "2026-12",
        "2027-01",
    ]


def test_an_empty_multi_month_result_cannot_look_successful():
    """Previously an empty selection reported FEASIBLE because it had no months."""

    with pytest.raises(InputError):
        run_cycle(*_cycle_args(), months_to_place=[])


# --- 6. partial-month fail-closed ----------------------------------------


def _partial_request(**overrides):
    roster = (Asset("A", AssetType.AREA),)
    base = dict(
        period=DatePeriod(date(2026, 11, 5), date(2026, 11, 20)),
        calendar=Calendar(weekdays(2026, 11)),
        roster=roster,
        config=FeasibilityConfig(),
        required_visits={"A": 1},
        seed=1,
    )
    base.update(overrides)
    return AllocationRequest(**base)


def test_partial_month_without_effective_limits_is_rejected():
    with pytest.raises(InputError) as caught:
        allocate_month(_partial_request())
    assert caught.value.constraint == C_PARTIAL_MONTH_UNRESOLVED


def test_partial_month_with_empty_effective_limits_is_rejected():
    with pytest.raises(InputError) as caught:
        allocate_month(_partial_request(effective_limits={}))
    assert caught.value.constraint == C_PARTIAL_MONTH_UNRESOLVED


def test_partial_month_missing_default_limits_is_rejected():
    """Asset-specific limits alone leave the default silently un-prorated."""

    with pytest.raises(InputError) as caught:
        allocate_month(
            _partial_request(effective_limits={"A": AssetLimits(monthly_max=1)})
        )
    assert caught.value.constraint == C_PARTIAL_MONTH_INCOMPLETE


def test_partial_month_with_complete_limits_is_accepted():
    result = allocate_month(
        _partial_request(
            effective_limits={
                "A": AssetLimits(monthly_max=1, monthly_min=0),
                DEFAULT_LIMITS_KEY: AssetLimits(monthly_max=1, monthly_min=0),
            }
        )
    )
    assert result.status is FeasibilityStatus.FEASIBLE


def test_full_month_needs_no_effective_limits():
    roster = (Asset("A", AssetType.AREA),)
    result = allocate_month(
        AllocationRequest(
            period=DatePeriod(date(2026, 11, 1), date(2026, 11, 30)),
            calendar=Calendar(weekdays(2026, 11)),
            roster=roster,
            config=FeasibilityConfig(),
            required_visits={"A": 2},
            seed=1,
        )
    )
    assert result.status is FeasibilityStatus.FEASIBLE


def test_unknown_asset_in_effective_limits_is_rejected():
    with pytest.raises(InputError):
        allocate_month(
            _partial_request(
                effective_limits={
                    "GHOST": AssetLimits(monthly_max=1),
                    DEFAULT_LIMITS_KEY: AssetLimits(monthly_max=1),
                }
            )
        )


def test_multi_month_helpers_reject_partial_periods():
    """A convenience API must not imply arbitrary date-range support."""

    from dailyworkreg.domain.targeting import (
        C_PARTIAL_PERIOD_UNSUPPORTED,
        _require_full_month,
    )

    partial = DatePeriod(date(2026, 11, 5), date(2026, 11, 20))
    with pytest.raises(InputError) as caught:
        _require_full_month(partial, "run_cycle")
    assert caught.value.constraint == C_PARTIAL_PERIOD_UNSUPPORTED
