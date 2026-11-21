"""Contract-hardening tests for the field-visit validator.

These tests cover the corrections made after the initial validator landed:
malformed obligation inputs must be rejected rather than silently dropped, the
period contract is one calendar month, committed in-period visits are modelled
separately from prior history, and violations carry structured fields so nothing
has to be recovered by parsing message text.

They are kept beside the original suite rather than merged into it so the
originally recorded section 10.6 vectors stay readable as recorded evidence.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dailyworkreg.domain.validator import (
    C_AREA_DAILY_CAP,
    C_ASSET_MINIMUM,
    C_ASSET_MONTHLY_MAX,
    C_COMBINED_DAILY_CAP,
    C_DTC_DAILY_CAP,
    C_CYCLE_EXCEEDS_MONTHLY,
    C_DUPLICATE_ASSET_ID,
    C_FIXED_VISIT_NON_WORKING,
    C_FIXED_VISIT_OUTSIDE_PERIOD,
    C_MINIMUM_ON_INACTIVE_ASSET,
    C_NEGATIVE_OBLIGATION,
    C_OBLIGATION_ON_INACTIVE_ASSET,
    C_REVISIT_GAP,
    C_UNKNOWN_LIMIT_OVERRIDE,
    C_UNKNOWN_OBLIGATION_ASSET,
    Asset,
    AssetLimits,
    AssetType,
    Calendar,
    DatePeriod,
    FeasibilityConfig,
    FeasibilityStatus,
    InputError,
    ObligationContext,
    ProposalContext,
    ValidationMode,
    Visit,
    assess_feasibility,
    resolve_obligations,
    validate_schedule,
)

AREA_ID = "area-01"
AREA2_ID = "area-02"
AREA3_ID = "area-03"
DTC_ID = "SCD-01"
DTC2_ID = "SCD-02"
FEEDER_ID = "feeder-01"

NOVEMBER = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))
NOV_DAYS = frozenset(date(2026, 11, 1) + timedelta(i) for i in range(30))


def _roster() -> tuple[Asset, ...]:
    return (
        Asset(AREA_ID, AssetType.AREA),
        Asset(AREA2_ID, AssetType.AREA),
        Asset(AREA3_ID, AssetType.AREA),
        Asset(DTC_ID, AssetType.DTC),
        Asset(DTC2_ID, AssetType.DTC),
        Asset(FEEDER_ID, AssetType.FEEDER),
    )


def obligation_context(**overrides) -> ObligationContext:
    """An obligation context over November 2026 with every date eligible."""

    base = {
        "period": NOVEMBER,
        "calendar": Calendar(NOV_DAYS),
        "roster": (Asset("a", AssetType.AREA),),
        "config": FeasibilityConfig(),
    }
    base.update(overrides)
    return ObligationContext(**base)


def proposal_context(**overrides) -> ProposalContext:
    base = {
        "period": NOVEMBER,
        "calendar": Calendar(NOV_DAYS),
        "roster": (Asset("a", AssetType.AREA),),
        "config": FeasibilityConfig(),
        "proposed": (),
    }
    base.update(overrides)
    return ProposalContext(**base)


# --- 1. malformed obligation inputs are rejected --------------------------


def test_duplicate_asset_ids_are_rejected():
    """An ambiguous roster is an input error, not a resolvable overlap."""

    roster = (Asset("a", AssetType.AREA), Asset("a", AssetType.DTC))
    with pytest.raises(InputError) as caught:
        assess_feasibility(obligation_context(roster=roster, required_visits={"a": 1}))
    assert caught.value.constraint == C_DUPLICATE_ASSET_ID


def test_unknown_required_visit_asset_is_rejected():
    """An obligation naming an unknown asset must never be dropped."""

    with pytest.raises(InputError) as caught:
        assess_feasibility(obligation_context(required_visits={"ghost": 5}))
    assert caught.value.constraint == C_UNKNOWN_OBLIGATION_ASSET


def test_unknown_cycle_obligation_asset_is_rejected():
    """A cycle obligation naming an unknown asset must never be dropped."""

    with pytest.raises(InputError) as caught:
        assess_feasibility(obligation_context(cycle_obligations={"ghost": 2}))
    assert caught.value.constraint == C_UNKNOWN_OBLIGATION_ASSET


def test_unknown_limit_override_asset_is_rejected():
    """A limit override matching no asset would otherwise never apply."""

    config = FeasibilityConfig(asset_limits={"ghost": AssetLimits()})
    with pytest.raises(InputError) as caught:
        assess_feasibility(obligation_context(config=config))
    assert caught.value.constraint == C_UNKNOWN_LIMIT_OVERRIDE


def test_negative_obligation_is_rejected():
    """A negative count is malformed input, not a satisfiable obligation."""

    with pytest.raises(InputError) as caught:
        assess_feasibility(obligation_context(required_visits={"a": -4}))
    assert caught.value.constraint == C_NEGATIVE_OBLIGATION


def test_obligation_on_inactive_asset_is_rejected_not_skipped():
    """A positive obligation on an inactive asset must not yield FEASIBLE."""

    roster = (Asset("a", AssetType.AREA), Asset("off", AssetType.AREA, active=False))
    with pytest.raises(InputError) as caught:
        assess_feasibility(obligation_context(roster=roster, required_visits={"off": 3}))
    assert caught.value.constraint == C_OBLIGATION_ON_INACTIVE_ASSET


def test_minimum_on_inactive_asset_is_rejected():
    """A configured minimum on an inactive asset cannot be discharged."""

    roster = (Asset("a", AssetType.AREA), Asset("off", AssetType.AREA, active=False))
    config = FeasibilityConfig(asset_limits={"off": AssetLimits(monthly_min=2)})
    with pytest.raises(InputError) as caught:
        assess_feasibility(obligation_context(roster=roster, config=config))
    assert caught.value.constraint == C_MINIMUM_ON_INACTIVE_ASSET


def test_malformed_input_is_distinct_from_mathematical_infeasibility():
    """A well-formed but impossible obligation set is INFEASIBLE, not an error."""

    result = assess_feasibility(obligation_context(required_visits={"a": 4}))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert result.evidence[0].constraint == C_ASSET_MONTHLY_MAX


def test_proposal_path_rejects_malformed_input_too():
    """The proposal path must not accept input the assessment path rejects."""

    with pytest.raises(InputError) as caught:
        validate_schedule(proposal_context(required_visits={"ghost": 1}))
    assert caught.value.constraint == C_UNKNOWN_OBLIGATION_ASSET


def test_resolve_obligations_uses_maximum_only_as_a_bound():
    """A monthly maximum never becomes an obligation."""

    roster = (Asset("a", AssetType.AREA),)
    config = FeasibilityConfig(
        default_asset_limits=AssetLimits(monthly_max=3, monthly_min=0)
    )
    spec = resolve_obligations(roster, config)
    assert spec.owed_for("a") == 0
    assert spec.assets_with_obligations() == ()


def test_resolve_obligations_takes_the_largest_source():
    """Minimum, explicit requirement and cycle portion resolve to the largest."""

    roster = (Asset("a", AssetType.DTC),)
    config = FeasibilityConfig(asset_limits={"a": AssetLimits(monthly_min=1)})
    spec = resolve_obligations(roster, config, {"a": 2}, {"a": 3})
    assert spec.owed_for("a") == 3


# --- 2. one-calendar-month period contract --------------------------------


def test_multi_month_period_is_rejected():
    """One month's limits must never be applied to two months at once."""

    with pytest.raises(ValueError):
        DatePeriod(date(2026, 11, 1), date(2026, 12, 30))


def test_partial_month_period_is_accepted():
    """A partial month stays within one calendar month and is allowed."""

    period = DatePeriod(date(2026, 11, 5), date(2026, 11, 20))
    assert period.month_key == (2026, 11)
    assert period.is_full_month is False


def test_full_month_reports_itself_as_full():
    assert NOVEMBER.is_full_month is True


def test_three_visits_in_each_of_two_months_are_both_valid():
    """Three visits per month is legal when each month is assessed separately."""

    dec_days = frozenset(date(2026, 12, 1) + timedelta(i) for i in range(31))
    roster = (Asset("a", AssetType.AREA),)
    november_visits = [date(2026, 11, 1), date(2026, 11, 8), date(2026, 11, 15)]
    december_visits = [date(2026, 12, 1), date(2026, 12, 8), date(2026, 12, 15)]

    cases = (
        (NOVEMBER, NOV_DAYS, november_visits),
        (DatePeriod(date(2026, 12, 1), date(2026, 12, 31)), dec_days, december_visits),
    )
    for period, days, visits in cases:
        result = validate_schedule(
            ProposalContext(
                period=period,
                calendar=Calendar(days),
                roster=roster,
                config=FeasibilityConfig(),
                proposed=tuple(Visit("a", d) for d in visits),
            )
        )
        assert result.valid, result.violations


def test_four_visits_within_one_month_are_rejected():
    """Four visits in a single month exceed the default maximum of 3."""

    result = validate_schedule(
        proposal_context(
            proposed=tuple(
                Visit("a", d)
                for d in (
                    date(2026, 11, 1),
                    date(2026, 11, 8),
                    date(2026, 11, 15),
                    date(2026, 11, 22),
                )
            )
        )
    )
    assert not result.valid
    assert C_ASSET_MONTHLY_MAX in result.violation_constraints


def test_cycle_portion_above_monthly_maximum_is_reported():
    """A cycle portion above the monthly maximum is flagged, not silently capped."""

    result = validate_schedule(
        proposal_context(cycle_obligations={"a": 9}),
        ValidationMode.OBLIGATIONS,
    )
    assert C_CYCLE_EXCEEDS_MONTHLY in result.violation_constraints


def test_quarterly_cycle_allocates_38_38_37():
    """A 113-DTC quarterly cycle splits into three monthly portions totalling 113."""

    allocations = (38, 38, 37)
    assert sum(allocations) == 113

    roster = tuple(Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 39))
    result = assess_feasibility(
        obligation_context(
            roster=roster,
            # Thirty-eight visits in one month needs two a day on this calendar.
            config=FeasibilityConfig(
                dtc_daily_cap=2, default_asset_limits=AssetLimits(monthly_max=1)
            ),
            cycle_obligations={a.asset_id: 1 for a in roster},
        )
    )
    # A 38-visit monthly portion is far inside November's aggregate capacity.
    assert result.status is not FeasibilityStatus.INFEASIBLE


# --- 3. committed in-period visits ---------------------------------------


def test_fixed_visits_consume_daily_capacity():
    """A committed visit occupies a slot before any proposed visit is placed."""

    roster = _roster()
    fixed = (Visit(AREA_ID, date(2026, 11, 1)), Visit(DTC_ID, date(2026, 11, 1)))
    fits = validate_schedule(
        proposal_context(
            roster=roster,
            fixed=fixed,
            proposed=(Visit(FEEDER_ID, date(2026, 11, 1)),),
        )
    )
    assert fits.valid, fits.violations

    overflows = validate_schedule(
        proposal_context(
            roster=roster,
            fixed=fixed,
            proposed=(
                Visit(FEEDER_ID, date(2026, 11, 1)),
                Visit(AREA2_ID, date(2026, 11, 1)),
                Visit(AREA3_ID, date(2026, 11, 1)),
            ),
        )
    )
    # The committed area visit plus two more areas is three on one date, over the
    # area allowance of two; the committed DTC and the feeder have their own ceilings.
    assert not overflows.valid
    assert C_AREA_DAILY_CAP in overflows.violation_constraints


def test_fixed_visits_count_toward_monthly_maximum():
    """Committed visits are part of the month's count, not exempt from it."""

    fixed = (
        Visit("a", date(2026, 11, 1)),
        Visit("a", date(2026, 11, 8)),
        Visit("a", date(2026, 11, 15)),
    )
    result = validate_schedule(
        proposal_context(fixed=fixed, proposed=(Visit("a", date(2026, 11, 22)),))
    )
    assert not result.valid
    assert C_ASSET_MONTHLY_MAX in result.violation_constraints


def test_fixed_visits_discharge_declared_obligations():
    """A requirement already met by committed visits needs no new placement."""

    fixed = (
        Visit("a", date(2026, 11, 1)),
        Visit("a", date(2026, 11, 8)),
        Visit("a", date(2026, 11, 15)),
    )
    result = assess_feasibility(
        obligation_context(fixed=fixed, required_visits={"a": 3})
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    assert len(result.witness) == 3


def test_fixed_visits_reduce_what_remains_to_place():
    """Two committed visits leave exactly one to allocate for a requirement of three."""

    fixed = (Visit("a", date(2026, 11, 1)), Visit("a", date(2026, 11, 8)))
    result = assess_feasibility(
        obligation_context(fixed=fixed, required_visits={"a": 3})
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    # The witness reports the whole month, committed and newly allocated alike.
    assert len(result.witness) == 3


def test_fixed_visits_constrain_revisit_gap():
    """A proposed visit too close to a committed one is rejected."""

    result = validate_schedule(
        proposal_context(
            fixed=(Visit("a", date(2026, 11, 1)),),
            proposed=(Visit("a", date(2026, 11, 6)),),
        )
    )
    assert not result.valid
    assert C_REVISIT_GAP in result.violation_constraints


def test_fixed_visit_outside_period_is_reported():
    """A committed visit outside the month is reported, never dropped."""

    result = validate_schedule(proposal_context(fixed=(Visit("a", date(2026, 12, 5)),)))
    assert not result.valid
    assert C_FIXED_VISIT_OUTSIDE_PERIOD in result.violation_constraints


def test_fixed_visit_on_non_working_date_is_reported():
    """A committed visit on an ineligible date is reported, never dropped."""

    days = NOV_DAYS - {date(2026, 11, 5)}
    result = validate_schedule(
        ProposalContext(
            period=NOVEMBER,
            calendar=Calendar(days),
            roster=(Asset("a", AssetType.AREA),),
            config=FeasibilityConfig(),
            proposed=(),
            fixed=(Visit("a", date(2026, 11, 5)),),
        )
    )
    assert not result.valid
    assert C_FIXED_VISIT_NON_WORKING in result.violation_constraints


def test_fixed_area_visits_count_toward_area_capacity():
    """Committed area visits consume the area-specific cap as well as the combined one."""

    roster = _roster()
    result = validate_schedule(
        proposal_context(
            roster=roster,
            fixed=(Visit(AREA_ID, date(2026, 11, 1)), Visit(AREA2_ID, date(2026, 11, 1))),
            proposed=(Visit(AREA3_ID, date(2026, 11, 1)),),
        )
    )
    assert not result.valid
    assert C_AREA_DAILY_CAP in result.violation_constraints


# --- 4. obligation-aware witness validation -----------------------------


def test_witness_passes_obligation_aware_validation():
    """A FEASIBLE witness must satisfy the full obligation contract, not just dates."""

    roster = tuple(Asset(f"a{i}", AssetType.AREA) for i in range(3))
    required = {a.asset_id: 2 for a in roster}
    config = FeasibilityConfig()
    result = assess_feasibility(
        obligation_context(roster=roster, config=config, required_visits=required)
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    validated = validate_schedule(
        ProposalContext(
            period=NOVEMBER,
            calendar=Calendar(NOV_DAYS),
            roster=roster,
            config=config,
            proposed=result.witness,
            required_visits=required,
        ),
        ValidationMode.OBLIGATIONS,
    )
    assert validated.valid, validated.violations


def test_missing_cycle_obligation_is_reported():
    """A declared cycle obligation that is unmet is visible to the caller."""

    result = validate_schedule(
        proposal_context(
            roster=(Asset("a", AssetType.DTC),),
            proposed=(Visit("a", date(2026, 11, 1)),),
            cycle_obligations={"a": 2},
        ),
        ValidationMode.OBLIGATIONS,
    )
    assert not result.valid
    assert C_ASSET_MINIMUM in result.violation_constraints


def test_satisfied_cycle_obligation_passes_obligation_mode():
    """Meeting the declared cycle portion yields a valid obligation-aware result."""

    result = validate_schedule(
        proposal_context(
            roster=(Asset("a", AssetType.DTC),),
            proposed=(Visit("a", date(2026, 11, 1)), Visit("a", date(2026, 11, 10))),
            cycle_obligations={"a": 2},
        ),
        ValidationMode.OBLIGATIONS,
    )
    assert result.valid, result.violations


def test_schedule_only_mode_does_not_require_obligations():
    """A draft missing part of its obligation is still a structurally valid draft."""

    result = validate_schedule(
        proposal_context(
            roster=(Asset("a", AssetType.DTC),),
            proposed=(Visit("a", date(2026, 11, 1)),),
            cycle_obligations={"a": 2},
        ),
        ValidationMode.SCHEDULE_ONLY,
    )
    assert result.valid, result.violations


# --- 5. structured diagnostics ------------------------------------------


def test_a_stated_total_still_names_every_asset_sharing_the_date():
    """A total ceiling is not the policy any more, but a request that states one is
    obeyed, and its violation names every asset on the date with its type."""

    result = validate_schedule(
        proposal_context(
            roster=_roster(),
            config=FeasibilityConfig(combined_daily_cap=3),
            proposed=(
                Visit(AREA_ID, date(2026, 11, 1)),
                Visit(AREA2_ID, date(2026, 11, 1)),
                Visit(DTC_ID, date(2026, 11, 1)),
                Visit(FEEDER_ID, date(2026, 11, 1)),
            ),
        )
    )
    violation = next(
        v for v in result.violations if v.constraint == C_COMBINED_DAILY_CAP
    )
    assert violation.limit == 3
    assert violation.actual == 4
    assert violation.offending_dates == (date(2026, 11, 1),)
    assert dict(violation.contributing_assets) == {
        AREA_ID: AssetType.AREA,
        AREA2_ID: AssetType.AREA,
        DTC_ID: AssetType.DTC,
        FEEDER_ID: AssetType.FEEDER,
    }


def test_each_kind_is_refused_by_its_own_ceiling_and_names_only_that_kind():
    """A DTC overrun is refused as a DTC overrun, naming the transformers alone."""

    result = validate_schedule(
        proposal_context(
            roster=_roster(),
            config=FeasibilityConfig(dtc_daily_cap=1),
            proposed=(
                Visit(DTC_ID, date(2026, 11, 1)),
                Visit(DTC2_ID, date(2026, 11, 1)),
            ),
        )
    )
    violation = next(
        v for v in result.violations if v.constraint == C_DTC_DAILY_CAP
    )
    assert violation.limit == 1
    assert violation.actual == 2
    assert dict(violation.contributing_assets) == {
        DTC_ID: AssetType.DTC,
        DTC2_ID: AssetType.DTC,
    }
    assert violation.contributing_assets == tuple(
        sorted(violation.contributing_assets, key=lambda pair: pair[0])
    )


def test_area_cap_violation_lists_only_area_assets():
    """An area-cap violation reports the area assets that caused it."""

    result = validate_schedule(
        proposal_context(
            roster=_roster(),
            proposed=(
                Visit(AREA_ID, date(2026, 11, 1)),
                Visit(AREA2_ID, date(2026, 11, 1)),
                Visit(AREA3_ID, date(2026, 11, 1)),
            ),
        )
    )
    violation = next(
        v for v in result.violations if v.constraint == C_AREA_DAILY_CAP
    )
    assert {asset_id for asset_id, _ in violation.contributing_assets} == {
        AREA_ID,
        AREA2_ID,
        AREA3_ID,
    }
    assert all(kind is AssetType.AREA for _, kind in violation.contributing_assets)


def test_monthly_maximum_violation_exposes_every_counted_date():
    """A monthly-maximum violation exposes all counted dates, not only the last."""

    visits = (
        date(2026, 11, 1),
        date(2026, 11, 8),
        date(2026, 11, 15),
        date(2026, 11, 22),
    )
    result = validate_schedule(
        proposal_context(proposed=tuple(Visit("a", d) for d in visits))
    )
    violation = next(
        v for v in result.violations if v.constraint == C_ASSET_MONTHLY_MAX
    )
    assert violation.offending_dates == visits
    assert violation.conflicting_dates == visits
    assert violation.limit == 3
    assert violation.actual == 4


def test_revisit_violation_exposes_both_conflicting_dates():
    """A revisit-gap violation names the pair of dates that conflict."""

    result = validate_schedule(
        proposal_context(
            proposed=(Visit("a", date(2026, 11, 1)), Visit("a", date(2026, 11, 6)))
        )
    )
    violation = next(v for v in result.violations if v.constraint == C_REVISIT_GAP)
    assert violation.offending_dates == (date(2026, 11, 1), date(2026, 11, 6))
    assert violation.limit == 7
    assert violation.actual == 5
