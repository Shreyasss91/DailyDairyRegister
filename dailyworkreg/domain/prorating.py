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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .validator import Asset, AssetLimits, DatePeriod, FeasibilityConfig, InputError

#: Raised when a partial month has no explicit effective limits.
C_PARTIAL_MONTH_UNRESOLVED = "partial_month_effective_limits_missing"
#: Raised when supplied effective limits contradict the configured ones.
C_PARTIAL_MONTH_INCONSISTENT = "partial_month_effective_limits_inconsistent"


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

    if effective_limits is None:
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

    default = effective_limits.get("__default__")
    merged: dict[str, AssetLimits] = {}
    for asset_id, limits in effective_limits.items():
        if asset_id == "__default__":
            continue
        configured = config.limits_for(asset_id)
        _check_prorated(asset_id, configured, limits)
        merged[asset_id] = limits

    if default is not None:
        _check_prorated("__default__", config.default_asset_limits, default)

    new_default = default if default is not None else config.default_asset_limits
    return FeasibilityConfig(
        combined_daily_cap=config.combined_daily_cap,
        area_daily_cap=config.area_daily_cap,
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
