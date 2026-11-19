"""The gap test construction relies on, and the cost of answering it.

A ten-month generation over the office's own masters was dominated by one question asked
tens of millions of times: "is this date too close to a visit this asset already has?" It
was answered by scanning every visit placed so far, for every (asset, candidate date) pair,
at every step - quadratic in the size of the month and the largest single cost in the run.

It is now answered from a sorted index, and each asset's viable dates are maintained rather
than recomputed. Both are only allowed to be faster, never different, so this module pins
the equivalence rather than the timing: a change that makes generation quicker by choosing
a different month would be a defect, and a wall-clock assertion would not catch it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from dailyworkreg.domain import allocator
from dailyworkreg.domain.validator import (
    Asset,
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

NOVEMBER = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))
NOV_DAYS = frozenset(date(2026, 11, 1) + timedelta(i) for i in range(30))


def request_for(required, roster, *, seed=0, config=None):
    return allocator.AllocationRequest(
        period=NOVEMBER,
        calendar=Calendar(NOV_DAYS),
        roster=roster,
        config=config or FeasibilityConfig(),
        required_visits=dict(required),
        seed=seed,
    )


def reference_gap(dates, day, gap):
    """The rule as originally written: scan everything for this asset."""

    return allocator._violates_gap("", day, [Visit("", when) for when in dates], gap)


@pytest.mark.parametrize("gap", [1, 2, 3, 7, 15, 30])
def test_the_indexed_gap_test_answers_exactly_what_the_scan_answers(gap):
    base = date(2026, 11, 1)
    dates = [base, base + timedelta(7), base + timedelta(14), base + timedelta(20)]
    for offset in range(0, 31):
        day = base + timedelta(offset)
        assert allocator._gap_violated(dates, day, gap) == reference_gap(dates, day, gap), (
            day,
            gap,
        )


def test_no_visits_is_never_a_violation():
    assert allocator._gap_violated((), date(2026, 11, 4), 7) is False


def test_a_visit_earlier_on_the_same_day_blocks_the_date():
    day = date(2026, 11, 4)
    assert allocator._gap_violated([day], day, 7) is True


def test_a_visit_later_in_the_period_blocks_the_date_outright():
    """The settled reading, kept deliberately: a later visit makes the elapsed gap negative.

    A negative gap is below every positive interval, so a commitment later in the cycle
    closes the earlier dates rather than only the dates near it. That is the behaviour the
    cycle tests expect; this test exists so a faster reading cannot quietly relax it.
    """

    later = date(2026, 11, 20)
    assert allocator._gap_violated([later], date(2026, 11, 4), 7) is True
    assert reference_gap([later], date(2026, 11, 4), 7) is True


def test_an_interval_at_least_the_gap_away_leaves_the_date_open():
    day = date(2026, 11, 8)
    assert allocator._gap_violated([date(2026, 11, 1)], day, 7) is False
    assert allocator._gap_violated([date(2026, 11, 1)], day, 8) is True


def test_the_plan_is_unchanged_when_the_reference_scan_does_the_work(monkeypatch):
    """A quicker allocator must be the same allocator.

    The whole month is allocated twice: once as shipped, and once with the gap question
    answered by the original scan, which is slower but is the definition of the rule. The
    two plans must be identical visit for visit, or the speed-up changed the register.
    """

    request = request_for(
        {
            "AREA-01": 3,
            "AREA-02": 2,
            "SCD-001": 2,
            "SCD-002": 1,
        },
        roster=(
            Asset("AREA-01", AssetType.AREA),
            Asset("AREA-02", AssetType.AREA),
            Asset("SCD-001", AssetType.DTC),
            Asset("SCD-002", AssetType.DTC),
        ),
        seed=3,
    )

    shipped = allocator.allocate_month(request)
    assert shipped.status is FeasibilityStatus.FEASIBLE

    monkeypatch.setattr(allocator, "_gap_violated", reference_gap)
    reference = allocator.allocate_month(request)

    assert reference.status is shipped.status
    assert reference.visits == shipped.visits
    assert [v.asset_id for v in reference.visits] == [v.asset_id for v in shipped.visits]


def test_the_gap_question_is_asked_once_per_viable_check_not_once_per_placed_visit(
    monkeypatch,
):
    """The cost this replaced, pinned without a clock.

    Recomputing every asset's viable dates at every step asks the gap question
    ``assets x dates x placements`` times. Maintaining them asks it once per asset, plus
    once more for each date a placement closes. A generous ceiling still separates the two
    by an order of magnitude, and a wall-clock assertion would not survive a slow machine.
    """

    assets = tuple(
        [Asset(f"AREA-{i:02d}", AssetType.AREA) for i in range(1, 9)]
        + [Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 9)]
    )
    required = {asset.asset_id: 3 for asset in assets}
    request = request_for(required, roster=assets, seed=5)

    asks = 0
    attempts = 0
    original_gap = allocator._gap_violated
    original_attempt = allocator._attempt_placement

    def counted_gap(dates, day, gap):
        nonlocal asks
        asks += 1
        return original_gap(dates, day, gap)

    def counted_attempt(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return original_attempt(*args, **kwargs)

    monkeypatch.setattr(allocator, "_gap_violated", counted_gap)
    monkeypatch.setattr(allocator, "_attempt_placement", counted_attempt)
    result = allocator.allocate_month(request)

    assert result.status is FeasibilityStatus.FEASIBLE
    placements = len(result.visits)
    dates = len(NOV_DAYS)
    # Maintained: one pass per asset, and one pass for each asset still in play whenever
    # a placement closes something. Rebuilding: assets x dates for every placement.
    maintained = attempts * (len(assets) * dates + placements * dates)
    rebuilding = attempts * placements * len(assets) * dates
    assert asks <= maintained, (asks, maintained)
    assert rebuilding > maintained * 3, (rebuilding, maintained)


def test_the_faster_allocator_still_returns_a_legal_month():
    """The property that matters more than the speed: a FEASIBLE month is a legal month."""

    assets = tuple(
        [Asset(f"AREA-{i:02d}", AssetType.AREA) for i in range(1, 4)]
        + [Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 4)]
    )
    request = request_for({asset.asset_id: 2 for asset in assets}, roster=assets, seed=9)
    result = allocator.allocate_month(request)

    validated = validate_schedule(
        ProposalContext(
            period=NOVEMBER,
            calendar=Calendar(NOV_DAYS),
            roster=assets,
            config=FeasibilityConfig(),
            proposed=result.visits,
            required_visits=dict(request.required_visits),
        ),
        ValidationMode.OBLIGATIONS,
    )
    assert validated.valid, [violation.message for violation in validated.violations]
