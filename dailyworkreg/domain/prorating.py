"""Effective period limits for full and partial months.

The requirements say a partial month's per-asset and area-level minimum/maximum
is *prorated by eligible working days*. They do not fix an integer rounding rule,
and there is exactly one arithmetic answer to "how many of 3 visits fit in a
month with 11 of 23 working days" only if a rounding rule is chosen. Two
conventions are both defensible and give different numbers:

*   round down:  ``floor(3 * 11 / 23) = 1``  - never over-schedules
*   round up:    ``ceil(3 * 11 / 23)  = 2``  - never under-schedules a minimum

Choosing either silently would change every prorated minimum and maximum in the
product, so this module refuses to guess. A partial month must arrive with
explicit effective limits, produced by whatever rule the owner adopts. A full
month needs no such input, because its configured values already apply.

This keeps the allocator honest: it can schedule full months today and partial
months as soon as the rule is declared, and it never implies a proration rule it
was not given.

The rule *has* since been declared - nearest integer, exact halves upward, recorded in
requirements §23 - and is implemented below as pure arithmetic. Declaring it does not
enable a partial month: scheduling part of a month additionally needs completed-visit
accounting outside the selected range and cycle-commitment continuity, so
:func:`resolve_period_config` still refuses a partial period without explicit effective
limits. The functions at the end of this module are the rule itself, available to the
act that will wire it through the domain, the application and validation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .validator import Asset, AssetLimits, DatePeriod, FeasibilityConfig, InputError

#: Identifies the arithmetic implemented below, so a caller can state which rule it used.
ROUNDING_RULE = "round_half_up"

#: Raised when a month has no eligible working day at all, so nothing can be prorated.
C_PRORATION_ZERO_DENOMINATOR = "proration_needs_a_non_zero_eligible_day_count"
#: Raised when the selected range claims more eligible days than the whole month has.
C_PRORATION_PERIOD_EXCEEDS_MONTH = "proration_period_exceeds_its_month"
#: Raised for a negative count, which no eligibility rule can make meaningful.
C_PRORATION_NEGATIVE_COUNT = "proration_needs_a_non_negative_count"

#: Raised when a partial month has no explicit effective limits.
C_PARTIAL_MONTH_UNRESOLVED = "partial_month_effective_limits_missing"
#: Raised when supplied effective limits contradict the configured ones.
C_PARTIAL_MONTH_INCONSISTENT = "partial_month_effective_limits_inconsistent"
#: Raised when supplied effective limits are empty or leave limits unaccounted for.
C_PARTIAL_MONTH_INCOMPLETE = "partial_month_effective_limits_incomplete"
#: Raised when effective limits name an asset that is not in the roster.
C_PARTIAL_MONTH_UNKNOWN_ASSET = "partial_month_effective_limits_unknown_asset"

#: Key used to supply the default effective limits explicitly.
DEFAULT_LIMITS_KEY = "__default__"


def full_month_config(config: FeasibilityConfig) -> FeasibilityConfig:
    """Return the configuration to use for a full month, unchanged.

    Present as an explicit step so every caller states which basis it used. A
    full month's configured limits are already its effective limits.
    """

    return config


def partial_month_config(
    config: FeasibilityConfig,
    effective_limits: Mapping[str, AssetLimits] | None,
    eligible_days_in_month: int | None,
    period: DatePeriod,
) -> FeasibilityConfig:
    """Return a configuration carrying explicit prorated limits.

    Args:
        config: The configured, un-prorated limits.
        effective_limits: Caller-supplied prorated per-asset limits.
        eligible_days_in_month: Eligible working days in the whole month, used
            only for the diagnostic message.
        period: The partial period being assessed.

    Returns:
        A configuration whose per-asset limits are the supplied effective ones.

    Raises:
        InputError: If the limits are missing for a partial month, or if a
            supplied limit is above its configured ceiling or below its
            configured minimum, which would mean the proration was miscomputed.
    """

    if effective_limits is None or not effective_limits:
        raise InputError(
            C_PARTIAL_MONTH_UNRESOLVED,
            (
                f"{period.start.isoformat()} to {period.end.isoformat()} is a "
                "partial month and needs explicit prorated per-asset limits. The "
                "requirements define prorating by eligible working days but do not "
                "fix an integer rounding rule, so the effective limits must be "
                "supplied rather than inferred."
            ),
        )

    default = effective_limits.get(DEFAULT_LIMITS_KEY)
    merged: dict[str, AssetLimits] = {}
    for asset_id, limits in effective_limits.items():
        if asset_id == DEFAULT_LIMITS_KEY:
            continue
        configured = config.limits_for(asset_id)
        _check_prorated(asset_id, configured, limits)
        merged[asset_id] = limits

    # The default limits must be accounted for explicitly. Falling back to the
    # configured default silently applied full-month limits to a shorter period,
    # which is precisely the bypass this contract exists to close.
    if default is None:
        raise InputError(
            C_PARTIAL_MONTH_INCOMPLETE,
            (
                "a partial month must supply effective default limits under "
                f"{DEFAULT_LIMITS_KEY!r}; omitting them would silently apply the "
                "un-prorated configured default"
            ),
        )
    _check_prorated(DEFAULT_LIMITS_KEY, config.default_asset_limits, default)

    new_default = default
    return FeasibilityConfig(
        dtc_daily_cap=config.dtc_daily_cap,
        area_daily_cap=config.area_daily_cap,
        feeder_daily_cap=config.feeder_daily_cap,
        combined_daily_cap=config.combined_daily_cap,
        default_asset_limits=new_default,
        asset_limits=merged,
        search_node_limit=config.search_node_limit,
    )


def _check_prorated(asset_id: str, configured: AssetLimits, effective: AssetLimits) -> None:
    """A prorated limit may only shrink a maximum and can never create an obligation.

    Proration reduces the number of visits a shorter month can hold. An effective
    maximum above the configured one, or an effective minimum above the
    configured one, means the values were not derived by prorating at all, so
    they are rejected instead of silently widening the policy.
    """

    if effective.monthly_max > configured.monthly_max:
        raise InputError(
            C_PARTIAL_MONTH_INCONSISTENT,
            (
                f"effective monthly maximum {effective.monthly_max} for "
                f"{asset_id!r} exceeds the configured {configured.monthly_max}; a "
                "prorated limit cannot raise a maximum"
            ),
            None if asset_id == "__default__" else asset_id,
        )
    if effective.monthly_min > configured.monthly_min:
        raise InputError(
            C_PARTIAL_MONTH_INCONSISTENT,
            (
                f"effective monthly minimum {effective.monthly_min} for "
                f"{asset_id!r} exceeds the configured {configured.monthly_min}; a "
                "prorated limit cannot raise a minimum"
            ),
            None if asset_id == "__default__" else asset_id,
        )


def resolve_period_config(
    period: DatePeriod,
    config: FeasibilityConfig,
    calendar_working_days: Iterable,
    effective_limits: Mapping[str, AssetLimits] | None = None,
) -> FeasibilityConfig:
    """Return the effective configuration for ``period``.

    A full calendar month uses its configured limits. A partial month requires
    explicit effective limits, per :func:`partial_month_config`.
    """

    if period.is_full_month:
        return full_month_config(config)

    month_days = [
        d
        for d in calendar_working_days
        if (d.year, d.month) == period.month_key
    ]
    return partial_month_config(
        config,
        effective_limits,
        len(month_days),
        period,
    )


# --- the settled rule ------------------------------------------------------
#
# Requirements §23 fixes the arithmetic:
#
#     effective = floor((2 * L * W_selected + W_full) / (2 * W_full))
#
# with L the configured monthly count and W the eligible working dates in the selected
# range and in the complete calendar month. Integer arithmetic only: Python's built-in
# ``round()`` rounds ties to even, which is a different rule and would quietly disagree
# with the one approved.


def prorate(
    count: int,
    *,
    eligible_days_in_period: int,
    eligible_days_in_month: int,
) -> int:
    """Round ``count`` proportionally to eligible working days, exact halves upward.

    Args:
        count: The configured monthly integer count.
        eligible_days_in_period: Eligible working dates inside the selected range.
        eligible_days_in_month: Eligible working dates in the complete calendar month.

    Returns:
        The effective count for the selected range.

    Raises:
        InputError: If the count is negative, the month has no eligible working day,
            or the range claims more eligible days than the month contains.
    """

    if count < 0:
        raise InputError(
            C_PRORATION_NEGATIVE_COUNT,
            f"cannot prorate {count}: a monthly count is never negative",
        )
    if eligible_days_in_month <= 0:
        raise InputError(
            C_PRORATION_ZERO_DENOMINATOR,
            (
                "this month has no eligible working day, so nothing can be prorated "
                "against it; check the weekly offs, General Holidays, leave and date "
                "overrides that produced the calendar"
            ),
        )
    if eligible_days_in_period < 0 or eligible_days_in_period > eligible_days_in_month:
        raise InputError(
            C_PRORATION_PERIOD_EXCEEDS_MONTH,
            (
                f"the selected range has {eligible_days_in_period} eligible working "
                f"day(s) but its month has {eligible_days_in_month}; a period cannot "
                "cover more of a month than the month contains"
            ),
        )

    return (2 * count * eligible_days_in_period + eligible_days_in_month) // (
        2 * eligible_days_in_month
    )


def eligible_working_days(days: Iterable) -> int:
    """Count the dates the effective calendar makes eligible.

    The calendar is the one the projection produced, so weekly offs, General Holidays,
    leave and explicit date overrides are already applied and are not re-derived here. A
    Restricted Holiday has no classification by contract, so it cannot reduce the count.

    Accepts either projected calendar days (anything with ``is_working_day``) or plain
    booleans, so a caller holding only the working-day set uses the same rule.
    """

    total = 0
    for day in days:
        working = day if isinstance(day, bool) else bool(day.is_working_day)
        total += 1 if working else 0
    return total


@dataclass(frozen=True)
class ProrationInputs:
    """The two day counts a proration needs, and nothing else."""

    eligible_days_in_period: int
    eligible_days_in_month: int

    def __post_init__(self) -> None:
        if self.eligible_days_in_month <= 0:
            raise InputError(
                C_PRORATION_ZERO_DENOMINATOR,
                (
                    "this month has no eligible working day, so nothing can be "
                    "prorated against it"
                ),
            )

    def apply(self, count: int) -> int:
        return prorate(
            count,
            eligible_days_in_period=self.eligible_days_in_period,
            eligible_days_in_month=self.eligible_days_in_month,
        )


def prorated_limits(limits: AssetLimits, inputs: ProrationInputs) -> AssetLimits:
    """Prorate an asset's monthly minimum and maximum by the settled rule.

    The revisit interval is a policy gap in calendar days, not a monthly count, so it is
    carried unchanged. A minimum and a maximum go through the same monotonic formula, so
    a valid ``minimum <= maximum`` does not become contradictory by rounding.
    """

    return AssetLimits(
        monthly_max=inputs.apply(limits.monthly_max),
        monthly_min=inputs.apply(limits.monthly_min),
        revisit_gap_days=limits.revisit_gap_days,
    )


def prorated_config(
    config: FeasibilityConfig,
    inputs: ProrationInputs,
    *,
    asset_ids: Iterable[str] = (),
) -> FeasibilityConfig:
    """A configuration whose per-asset monthly counts are prorated, caps untouched.

    Only monthly frequency counts depend on how much of the month is selected. The
    combined and area daily caps are per-date limits and are not reduced by a shorter
    period; the search budget is a computational bound, not a policy count.

    A DTC coverage-cycle obligation is not part of a ``FeasibilityConfig`` at all, which
    is why it cannot be prorated here and must not be prorated anywhere else: it is
    declared over a cycle and stays whole.
    """

    merged: dict[str, AssetLimits] = {
        asset_id: prorated_limits(config.limits_for(asset_id), inputs)
        for asset_id in asset_ids
    }
    return FeasibilityConfig(
        dtc_daily_cap=config.dtc_daily_cap,
        area_daily_cap=config.area_daily_cap,
        feeder_daily_cap=config.feeder_daily_cap,
        combined_daily_cap=config.combined_daily_cap,
        default_asset_limits=prorated_limits(config.default_asset_limits, inputs),
        asset_limits=merged,
        search_node_limit=config.search_node_limit,
    )


def prorate_counts(counts: Mapping[str, int], inputs: ProrationInputs) -> dict[str, int]:
    """Prorate a mapping of *monthly frequency counts* by the settled rule.

    Present so an explicitly prorated monthly count target uses the same arithmetic. It
    is not a cycle obligation: passing a cycle's declared per-asset visits here would be
    the thing requirements §23 forbids, so a caller holding a cycle obligation must not
    call it.
    """

    return {key: inputs.apply(value) for key, value in counts.items()}
