"""The settled integer proration rule (requirements §23).

The rule was undeclared for a long time and this module refused to guess, which is why a
partial month had to arrive with effective limits supplied by the caller. These tests pin
the arithmetic now that the owner has declared it, and pin the two things declaring it
does *not* do: no partial month is enabled, and no cycle obligation is prorated.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import date

import pytest
from dailyworkreg.application.calendar_projection import (
    WeeklyOffRules,
    project_calendar,
)
from dailyworkreg.domain import cycle as cycle_module
from dailyworkreg.domain.prorating import (
    C_PARTIAL_MONTH_UNRESOLVED,
    C_PRORATION_NEGATIVE_COUNT,
    C_PRORATION_PERIOD_EXCEEDS_MONTH,
    C_PRORATION_ZERO_DENOMINATOR,
    ProrationInputs,
    eligible_working_days,
    prorate,
    prorate_counts,
    prorated_config,
    prorated_limits,
    resolve_period_config,
)
from dailyworkreg.domain.validator import (
    AssetLimits,
    DatePeriod,
    FeasibilityConfig,
    InputError,
)
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite.database import open_database


@dataclass(frozen=True)
class Day:
    """The one attribute the eligible-day count reads from a projected day."""

    day: date
    is_working_day: bool


def test_the_formula_is_the_one_the_owner_settled():
    # floor((2 * L * W_selected + W_full) / (2 * W_full))
    for count, selected, month, expected in (
        (3, 11, 23, 1),     # 3 * 11 / 23 = 1.43 -> 1
        (3, 12, 23, 2),     # 1.57 -> 2
        (2, 7, 22, 1),      # 0.64 -> 1
        (4, 10, 21, 2),     # 1.90 -> 2
        (1, 2, 4, 1),       # 0.50 -> 1, a tie rounded up
        (1, 1, 4, 0),       # 0.25 -> 0
    ):
        assert prorate(
            count,
            eligible_days_in_period=selected,
            eligible_days_in_month=month,
        ) == expected


def test_exact_halves_round_upward_not_to_even():
    # Python's round() gives 0, 2, 2 for these, which is ties-to-even and not the rule.
    assert round(0.5) == 0 and round(1.5) == 2 and round(2.5) == 2
    assert prorate(1, eligible_days_in_period=1, eligible_days_in_month=2) == 1
    assert prorate(3, eligible_days_in_period=1, eligible_days_in_month=2) == 2
    assert prorate(5, eligible_days_in_period=1, eligible_days_in_month=2) == 3
    # 5 of 10 days * 3 = 1.5 -> 2
    assert prorate(3, eligible_days_in_period=5, eligible_days_in_month=10) == 2


def test_integer_arithmetic_is_used_throughout():
    # A float implementation drifts on large counts; this one cannot.
    big = prorate(10**15 + 1, eligible_days_in_period=1, eligible_days_in_month=2)
    assert big == (10**15 + 1) // 2 + 1


def test_a_full_month_keeps_its_configured_counts():
    inputs = ProrationInputs(eligible_days_in_period=23, eligible_days_in_month=23)
    limits = AssetLimits(monthly_max=3, monthly_min=1, revisit_gap_days=7)
    assert prorated_limits(limits, inputs) == limits
    assert inputs.apply(3) == 3


def test_the_ratio_comes_from_the_effective_calendar():
    month = [Day(date(2026, 11, day), day % 7 != 0) for day in range(1, 31)]
    selected = [item for item in month if item.day.day <= 15]
    assert eligible_working_days(month) == 26
    inputs = ProrationInputs(
        eligible_days_in_period=eligible_working_days(selected),
        eligible_days_in_month=eligible_working_days(month),
    )
    assert inputs.eligible_days_in_period == 13
    # 3 * 13 / 26 = 1.5 -> 2, because an exact half rounds upward.
    assert inputs.apply(3) == 2


def test_a_date_override_changes_the_numerator_and_denominator(tmp_path):
    connection = open_database(tmp_path / "proration.db")
    try:
        start, end = date(2026, 11, 1), date(2026, 11, 30)
        rules = WeeklyOffRules()
        before = project_calendar(connection, start, end, rules=rules)
        month_days = eligible_working_days(before.days)
        selected_days = eligible_working_days(
            [item for item in before.days if item.day <= date(2026, 11, 15)]
        )

        # A working Tuesday is made non-working by an explicit override: both the
        # selected count and the month count fall by one, because it lies in November.
        cal.upsert_override(
            connection,
            override_id="O-LOCKDOWN",
            override_date=date(2026, 11, 10),
            is_working_day=False,
            reason_en="Office closed",
        )
        after = project_calendar(connection, start, end, rules=rules)
        assert eligible_working_days(after.days) == month_days - 1
        assert (
            eligible_working_days(
                [item for item in after.days if item.day <= date(2026, 11, 15)]
            )
            == selected_days - 1
        )

        # An override outside the selected range moves only the denominator.
        cal.upsert_override(
            connection,
            override_id="O-LATE",
            override_date=date(2026, 11, 24),
            is_working_day=False,
            reason_en="Office closed",
        )
        third = project_calendar(connection, start, end, rules=rules)
        assert eligible_working_days(third.days) == month_days - 2
        assert (
            eligible_working_days(
                [item for item in third.days if item.day <= date(2026, 11, 15)]
            )
            == selected_days - 1
        )
    finally:
        connection.close()


def test_a_general_holiday_reduces_the_count_and_a_restricted_one_cannot():
    from dailyworkreg.application.draft_generation import CalendarDayKind

    # The classification has no Restricted Holiday member at all, so a Restricted
    # Holiday date is classified by whatever independently applies to it and is
    # counted as an ordinary day.
    assert not hasattr(CalendarDayKind, "RESTRICTED_HOLIDAY")
    labels = {member.value for member in CalendarDayKind}
    assert "restricted_holiday" not in labels
    assert "general_holiday" in labels

    days = [
        Day(date(2026, 11, 1), False),   # Sunday
        Day(date(2026, 11, 2), True),
        Day(date(2026, 11, 3), False),  # a General Holiday
    ]
    assert eligible_working_days(days) == 1


def test_a_zero_denominator_is_an_input_error():
    with pytest.raises(InputError) as error:
        prorate(3, eligible_days_in_period=0, eligible_days_in_month=0)
    assert error.value.constraint == C_PRORATION_ZERO_DENOMINATOR

    with pytest.raises(InputError) as error:
        ProrationInputs(eligible_days_in_period=0, eligible_days_in_month=0)
    assert error.value.constraint == C_PRORATION_ZERO_DENOMINATOR


def test_a_range_cannot_hold_more_days_than_its_month():
    with pytest.raises(InputError) as error:
        prorate(3, eligible_days_in_period=24, eligible_days_in_month=23)
    assert error.value.constraint == C_PRORATION_PERIOD_EXCEEDS_MONTH


def test_a_negative_count_is_an_input_error():
    with pytest.raises(InputError) as error:
        prorate(-1, eligible_days_in_period=1, eligible_days_in_month=2)
    assert error.value.constraint == C_PRORATION_NEGATIVE_COUNT


def test_a_minimum_and_a_maximum_never_cross_each_other():
    inputs = ProrationInputs(eligible_days_in_period=1, eligible_days_in_month=3)
    limits = prorated_limits(
        AssetLimits(monthly_max=3, monthly_min=1, revisit_gap_days=7), inputs
    )
    assert limits.monthly_min <= limits.monthly_max
    assert limits.revisit_gap_days == 7


def test_the_daily_caps_and_the_search_budget_are_not_prorated():
    inputs = ProrationInputs(eligible_days_in_period=1, eligible_days_in_month=2)
    config = FeasibilityConfig(
        combined_daily_cap=3,
        area_daily_cap=2,
        default_asset_limits=AssetLimits(monthly_max=3, monthly_min=1),
        asset_limits={"SCD-1": AssetLimits(monthly_max=3, monthly_min=1)},
        search_node_limit=500,
    )
    prorated = prorated_config(config, inputs, asset_ids=["SCD-1"])
    assert prorated.combined_daily_cap == 3
    assert prorated.area_daily_cap == 2
    assert prorated.search_node_limit == 500
    assert prorated.asset_limits["SCD-1"].monthly_max == 2


def test_a_cycle_obligation_is_never_prorated():
    # Structural, and deliberately so: the cycle planner holds no proration call, so no
    # later refactor can start prorating an obligation by accident.
    source = inspect.getsource(cycle_module)
    assert "prorate(" not in source
    assert "ProrationInputs" not in source
    assert "from .prorating import" not in source

    # And the proration helpers cannot be pointed at one by accident: they operate on a
    # monthly count mapping, which a cycle declaration is not.
    inputs = ProrationInputs(eligible_days_in_period=1, eligible_days_in_month=2)
    assert prorate_counts({"SCD-1": 2}, inputs) == {"SCD-1": 1}
    assert "obligations" not in inspect.signature(prorated_config).parameters


def test_partial_month_generation_is_still_refused():
    period = DatePeriod(start=date(2026, 11, 10), end=date(2026, 11, 20))
    config = FeasibilityConfig(
        combined_daily_cap=3,
        area_daily_cap=2,
        default_asset_limits=AssetLimits(monthly_max=3, monthly_min=1),
    )
    with pytest.raises(InputError) as error:
        resolve_period_config(period, config, [], effective_limits=None)
    assert error.value.constraint == C_PARTIAL_MONTH_UNRESOLVED


def test_a_full_month_needs_no_proration_at_all():
    period = DatePeriod(start=date(2026, 11, 1), end=date(2026, 11, 30))
    config = FeasibilityConfig(
        combined_daily_cap=3,
        area_daily_cap=2,
        default_asset_limits=AssetLimits(monthly_max=3, monthly_min=1),
    )
    assert resolve_period_config(period, config, [], effective_limits=None) is config
