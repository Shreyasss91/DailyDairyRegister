"""Regression tests for committed-visit validation and the false-FEASIBLE path.

These tests pin a specific defect class. Before the correction, the feasibility
assessment subtracted what committed visits already discharged and, when nothing
remained to place, returned ``FEASIBLE`` without ever checking that the committed
visits themselves formed a valid schedule. The concrete schedule validator
rejected the very same input. Two answers to one question meant an invalid month
could be recorded as feasible.

Each test below asserts the invariant directly: if ``assess_feasibility`` reports
``FEASIBLE``, the full witness it returns must pass independent hard-constraint
and obligation-aware validation. Anything else is a false acceptance.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dailyworkreg.domain.validator import (
    C_ASSET_MONTHLY_MAX,
    C_AREA_DAILY_CAP,
    C_COMBINED_DAILY_CAP,
    C_FIXED_VISIT_INACTIVE_ASSET,
    C_FIXED_VISIT_NON_WORKING,
    C_FIXED_VISIT_OUTSIDE_PERIOD,
    C_NEGATIVE_OBLIGATION,
    C_REVISIT_GAP,
    C_UNKNOWN_ASSET,
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

NOVEMBER = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))
NOV_DAYS = frozenset(date(2026, 11, 1) + timedelta(i) for i in range(30))
CONFIG = FeasibilityConfig()

ROSTER = (
    Asset("a", AssetType.AREA),
    Asset("b", AssetType.DTC),
    Asset("c", AssetType.FEEDER),
)
THREE_AREAS = (
    Asset("a1", AssetType.AREA),
    Asset("a2", AssetType.AREA),
    Asset("a3", AssetType.AREA),
)


def serve(roster, fixed, required=None, config=CONFIG, days=NOV_DAYS):
    return ObligationContext(
        period=NOVEMBER,
        calendar=Calendar(days),
        roster=roster,
        config=config,
        fixed=tuple(fixed),
        required_visits=dict(required or {}),
    )


def judge(roster, proposed, required=None, config=CONFIG, days=NOV_DAYS, fixed=()):
    return ProposalContext(
        period=NOVEMBER,
        calendar=Calendar(days),
        roster=roster,
        config=config,
        proposed=tuple(proposed),
        fixed=tuple(fixed),
        required_visits=dict(required or {}),
    )


def assert_witness_is_a_valid_month(result, roster, required=None, config=CONFIG):
    """A FEASIBLE verdict is only trustworthy if its witness stands up alone.

    The witness is fed back through the concrete-schedule path in its
    obligation-aware mode. If that path rejects it, the assessment was wrong.
    """

    assert result.status is FeasibilityStatus.FEASIBLE
    validated = validate_schedule(
        judge(roster, result.witness, required=required, config=config),
        ValidationMode.OBLIGATIONS,
    )
    assert validated.valid, validated.violations


# --- the no-remaining-obligations branch must not bypass validation --------


def test_no_obligations_does_not_excuse_an_invalid_commitment():
    """The exact regression: nothing remains to place, but the month is invalid.

    Four committed visits against a monthly maximum of three leaves no
    obligation outstanding. Before the correction this returned FEASIBLE.
    """

    fixed = tuple(
        Visit("a", d)
        for d in (
            date(2026, 11, 1),
            date(2026, 11, 8),
            date(2026, 11, 15),
            date(2026, 11, 22),
        )
    )
    # No requirement at all, so the early return was reached unconditionally.
    result = assess_feasibility(serve(ROSTER, fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_ASSET_MONTHLY_MAX in {e.constraint for e in result.evidence}

    # The concrete path agrees, which is the property that was violated.
    assert not validate_schedule(judge(ROSTER, (), fixed=fixed)).valid


def test_no_obligations_does_not_excuse_combined_cap_overrun():
    """The early return must not accept four committed visits on one date."""

    fixed = (
        Visit("a", date(2026, 11, 1)),
        Visit("b", date(2026, 11, 1)),
        Visit("c", date(2026, 11, 1)),
        Visit("a", date(2026, 11, 1)),
    )
    result = assess_feasibility(serve(ROSTER, fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_COMBINED_DAILY_CAP in {e.constraint for e in result.evidence}


def test_no_obligations_does_not_excuse_area_cap_overrun():
    """Three committed area visits on one date exceed the area cap of two."""

    fixed = (
        Visit("a1", date(2026, 11, 1)),
        Visit("a2", date(2026, 11, 1)),
        Visit("a3", date(2026, 11, 1)),
    )
    result = assess_feasibility(serve(THREE_AREAS, fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_AREA_DAILY_CAP in {e.constraint for e in result.evidence}


def test_no_obligations_does_not_excuse_a_revisit_gap_violation():
    """Two committed visits three days apart break the seven-day interval."""

    fixed = (Visit("a", date(2026, 11, 1)), Visit("a", date(2026, 11, 4)))
    result = assess_feasibility(serve(ROSTER, fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_REVISIT_GAP in {e.constraint for e in result.evidence}


# --- individually malformed commitments ----------------------------------


def test_committed_visit_to_inactive_asset_is_rejected():
    """Activity is required of a commitment, not only of a proposal."""

    roster = (Asset("a", AssetType.AREA), Asset("off", AssetType.AREA, active=False))
    fixed = (Visit("off", date(2026, 11, 1)),)

    result = assess_feasibility(serve(roster, fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_FIXED_VISIT_INACTIVE_ASSET in {e.constraint for e in result.evidence}

    # The same input must be rejected by the concrete path, not merely the
    # assessment; two answers to one question is the defect being fixed.
    validated = validate_schedule(judge(roster, (), fixed=fixed))
    assert not validated.valid
    assert C_FIXED_VISIT_INACTIVE_ASSET in validated.violation_constraints


def test_committed_visit_to_unknown_asset_is_rejected():
    fixed = (Visit("ghost", date(2026, 11, 1)),)
    result = assess_feasibility(serve(ROSTER, fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_UNKNOWN_ASSET in {e.constraint for e in result.evidence}


def test_committed_visit_outside_period_is_rejected():
    fixed = (Visit("a", date(2026, 12, 5)),)
    result = assess_feasibility(serve(ROSTER, fixed))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_FIXED_VISIT_OUTSIDE_PERIOD in {e.constraint for e in result.evidence}


def test_committed_visit_on_non_working_date_is_rejected():
    days = NOV_DAYS - {date(2026, 11, 5)}
    fixed = (Visit("a", date(2026, 11, 5)),)
    result = assess_feasibility(serve(ROSTER, fixed, days=days))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_FIXED_VISIT_NON_WORKING in {e.constraint for e in result.evidence}


# --- evidence is structured, not text ------------------------------------


def test_infeasible_evidence_carries_structured_fields():
    """An invalid commitment is diagnosable without reading the message."""

    fixed = (Visit("a", date(2026, 11, 1)), Visit("a", date(2026, 11, 4)))
    result = assess_feasibility(serve(ROSTER, fixed))
    evidence = next(e for e in result.evidence if e.constraint == C_REVISIT_GAP)
    assert evidence.asset_id == "a"
    assert evidence.asset_type is AssetType.AREA
    assert evidence.offending_dates == (date(2026, 11, 1), date(2026, 11, 4))
    assert evidence.limit == 7
    assert evidence.actual == 3


# --- valid commitments still work ----------------------------------------


def test_valid_committed_only_schedule_is_feasible_with_a_valid_witness():
    """When obligations are already discharged, the committed month is the witness."""

    fixed = (
        Visit("a", date(2026, 11, 1)),
        Visit("a", date(2026, 11, 8)),
        Visit("a", date(2026, 11, 15)),
    )
    result = assess_feasibility(serve(ROSTER, fixed, required={"a": 3}))
    assert result.status is FeasibilityStatus.FEASIBLE
    assert len(result.witness) == 3
    assert set(result.witness) == set(fixed)
    assert_witness_is_a_valid_month(result, ROSTER, required={"a": 3})


def test_valid_commitments_plus_remaining_obligations_give_a_complete_witness():
    """The witness covers the committed visits and the newly allocated ones."""

    fixed = (Visit("a", date(2026, 11, 1)),)
    result = assess_feasibility(serve(ROSTER, fixed, required={"a": 2, "b": 2}))
    assert result.status is FeasibilityStatus.FEASIBLE
    assert len(result.witness) == 4
    assert Visit("a", date(2026, 11, 1)) in result.witness
    # The committed visit appears once, not duplicated by the allocator.
    assert result.witness.count(Visit("a", date(2026, 11, 1))) == 1
    assert_witness_is_a_valid_month(result, ROSTER, required={"a": 2, "b": 2})


def test_committed_visits_consume_the_last_available_daily_capacity():
    """A date filled to the cap by commitments cannot take another visit."""

    # Two commitments on one date leave exactly one slot under the cap of three.
    fixed = (Visit("a", date(2026, 11, 2)), Visit("b", date(2026, 11, 2)))
    result = assess_feasibility(serve(ROSTER, fixed, required={"a": 2, "b": 2}))
    assert result.status is FeasibilityStatus.FEASIBLE
    assert_witness_is_a_valid_month(result, ROSTER, required={"a": 2, "b": 2})
    per_date: dict[date, int] = {}
    for visit in result.witness:
        per_date[visit.visit_date] = per_date.get(visit.visit_date, 0) + 1
    assert max(per_date.values()) <= CONFIG.combined_daily_cap


def test_commitments_that_exhaust_the_monthly_maximum_make_more_impossible():
    """A month already carrying the maximum cannot absorb another visit."""

    # Three commitments fill asset a's month. One more is impossible.
    fixed = (
        Visit("a", date(2026, 11, 1)),
        Visit("a", date(2026, 11, 8)),
        Visit("a", date(2026, 11, 15)),
    )
    result = assess_feasibility(serve(ROSTER, fixed, required={"a": 4}))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert C_ASSET_MONTHLY_MAX in {e.constraint for e in result.evidence}


def test_feasible_verdict_at_the_boundary_still_produces_a_valid_witness():
    """Filling the month to exactly the maximum stays feasible and consistent."""

    fixed = (Visit("a", date(2026, 11, 1)), Visit("a", date(2026, 11, 8)))
    result = assess_feasibility(serve(ROSTER, fixed, required={"a": 3}))
    assert result.status is FeasibilityStatus.FEASIBLE
    assert len(result.witness) == 3
    assert_witness_is_a_valid_month(result, ROSTER, required={"a": 3})


# --- boolean counts are not visit counts ---------------------------------


@pytest.mark.parametrize("value", [True, False])
def test_boolean_required_visit_count_is_rejected(value):
    """``bool`` subclasses ``int``; a boolean is not a visit count."""

    with pytest.raises(InputError) as caught:
        resolve_obligations(ROSTER, CONFIG, {"a": value})
    assert caught.value.constraint == C_NEGATIVE_OBLIGATION


@pytest.mark.parametrize("value", [True, False])
def test_boolean_cycle_obligation_count_is_rejected(value):
    with pytest.raises(InputError) as caught:
        resolve_obligations(ROSTER, CONFIG, None, {"a": value})
    assert caught.value.constraint == C_NEGATIVE_OBLIGATION


def test_plain_integer_counts_are_still_accepted():
    """The boolean guard must not reject ordinary integers."""

    spec = resolve_obligations(ROSTER, CONFIG, {"a": 2}, {"b": 1})
    assert spec.owed_for("a") == 2
    assert spec.owed_for("b") == 1


def test_zero_count_is_accepted():
    spec = resolve_obligations(ROSTER, CONFIG, {"a": 0})
    assert spec.owed_for("a") == 0
