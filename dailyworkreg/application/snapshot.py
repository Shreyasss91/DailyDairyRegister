"""Serialisation of a generation request, so an old draft stays explainable.

A generation run stores the exact inputs it was given, not a summary of them. A
fingerprint answers "is this the same master state" but cannot rebuild a request, and
the office will edit masters tomorrow: an old draft that can only be explained by
reading today's live records is an old draft that can no longer be explained at all.

The payload carries a schema version so a later release can read a historical
generation deliberately, or migrate it, instead of guessing at its shape.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..domain import wording
from ..domain.activities import ActivitySpec
from ..domain.cycle import CoverageCycle, DtcClass, DtcWeights, MonthKey
from ..domain.validator import (
    Asset,
    AssetLimits,
    AssetType,
    FeasibilityConfig,
    Visit,
)
from .draft_generation import (
    AssetDisplay,
    CalendarDay,
    CalendarDayKind,
    DayEntry,
    DayEntryKind,
    DayEntryProvenance,
    DraftMode,
    DraftRequest,
)

#: Bumped when the payload below changes shape. A reader refuses a version it does
#: not know rather than half-reading it.
#:
#: Schema 2 adds the activity catalogue the generator places and the count of
#: activities a day records, so a stored draft can be re-derived exactly. A schema 1
#: snapshot is still read: it carries no catalogue, so it reproduces the visits-only
#: draft it was generated as, which is what it recorded.
#:
#: Schema 3 adds the language the register is written in. Schema 1 and 2 snapshots are
#: still read, and they hold no language because the register was filed in both then, so
#: that is what they are read as - the document they were issued as.
REQUEST_SNAPSHOT_SCHEMA = 3

#: Versions this reader understands.
REQUEST_SNAPSHOT_READABLE = (1, 2, 3)

C_SNAPSHOT_VERSION_UNSUPPORTED = "request_snapshot_version_unsupported"
C_SNAPSHOT_MALFORMED = "request_snapshot_malformed"


class SnapshotError(ValueError):
    """A stored snapshot cannot be read by this version of the code."""

    def __init__(self, constraint: str, detail: str):
        self.constraint = constraint
        self.detail = detail
        super().__init__(f"{constraint}: {detail}")


def _date(value: date) -> str:
    return value.isoformat()


def _month(label: str) -> MonthKey:
    """Read a stored month label back. Kept here so the domain gains no parser."""

    year_text, month_text = label.split("-", 1)
    return MonthKey(int(year_text), int(month_text))


def _limits(limits: AssetLimits) -> dict[str, Any]:
    return {
        "monthly_max": limits.monthly_max,
        "monthly_min": limits.monthly_min,
        "revisit_gap_days": limits.revisit_gap_days,
    }


def _visit(visit: Visit) -> dict[str, Any]:
    return {"asset_id": visit.asset_id, "visit_date": _date(visit.visit_date)}


def _activity(item: ActivitySpec) -> dict[str, Any]:
    return {
        "activity_id": item.activity_id,
        "category": item.category,
        "title_en": item.title_en,
        "title_kn": item.title_kn,
        "weight": item.weight,
        "monthly_min": item.monthly_min,
        "monthly_max": item.monthly_max,
        "repeat_gap_days": item.repeat_gap_days,
        "weekday_mask": item.weekday_mask,
    }


def encode_request(request: DraftRequest) -> dict[str, Any]:
    """The whole request as plain data, sufficient to rebuild it exactly."""

    return {
        "schema": REQUEST_SNAPSHOT_SCHEMA,
        "start": _date(request.start),
        "end": _date(request.end),
        "mode": request.mode.value,
        "seed": request.seed,
        "attempt_budget": request.attempt_budget,
        "rotation_offset": request.rotation_offset,
        "roster": [
            {"asset_id": a.asset_id, "asset_type": a.asset_type.value, "active": a.active}
            for a in request.roster
        ],
        "display": [
            {"asset_id": d.asset_id, "name_en": d.name_en, "name_kn": d.name_kn}
            for d in request.display
        ],
        "calendar_days": [
            {
                "day": _date(c.day),
                "kind": c.kind.value,
                "is_working_day": c.is_working_day,
                "reasons_en": list(c.reasons_en),
                "reasons_kn": list(c.reasons_kn),
            }
            for c in request.calendar_days
        ],
        "config": {
            "combined_daily_cap": request.config.combined_daily_cap,
            "area_daily_cap": request.config.area_daily_cap,
            "default_asset_limits": _limits(request.config.default_asset_limits),
            "asset_limits": {
                asset_id: _limits(limits)
                for asset_id, limits in request.config.asset_limits.items()
            },
            "search_node_limit": request.config.search_node_limit,
        },
        "prior_history": [_visit(v) for v in request.prior_history],
        "fixed_by_month": {
            month.label(): [_visit(v) for v in visits]
            for month, visits in request.fixed_by_month.items()
        },
        "entries": [
            {
                "entry_id": e.entry_id,
                "day": _date(e.day),
                "kind": e.kind.value,
                "title_en": e.title_en,
                "title_kn": e.title_kn,
                "provenance": (
                    e.provenance.value
                    if isinstance(e.provenance, DayEntryProvenance)
                    else str(e.provenance)
                ),
            }
            for e in request.entries
        ],
        "activities": [_activity(item) for item in request.activities],
        "activities_per_day": request.activities_per_day,
        "entry_language": request.entry_language,
        "cycle": (
            None
            if request.cycle is None
            else {
                "start_month": request.cycle.start_month.label(),
                "duration_months": request.cycle.duration_months,
                "visits_per_asset": request.cycle.visits_per_asset,
            }
        ),
        "weights": (
            None
            if request.weights is None
            else {
                "kva": dict(request.weights.kva),
                "individual_adjustment": dict(
                    request.weights.individual_adjustment
                ),
                "classification": {
                    asset_id: value.value
                    for asset_id, value in request.weights.classification.items()
                },
                "department_multiplier": request.weights.department_multiplier,
                "self_dtc_multiplier": request.weights.self_dtc_multiplier,
            }
        ),
        "area_required": {
            month.label(): dict(counts)
            for month, counts in (request.area_required or {}).items()
        },
        "feeder_required": {
            month.label(): dict(counts)
            for month, counts in (request.feeder_required or {}).items()
        },
        "dtc_target": request.dtc_target,
    }


def _asset_limits(payload: dict[str, Any]) -> AssetLimits:
    return AssetLimits(
        monthly_max=int(payload["monthly_max"]),
        monthly_min=int(payload["monthly_min"]),
        revisit_gap_days=int(payload["revisit_gap_days"]),
    )


def decode_request(payload: dict[str, Any]) -> DraftRequest:
    """Rebuild a :class:`DraftRequest` from a stored snapshot.

    A snapshot from an unknown schema version is refused. Reading it optimistically
    would produce a draft that looks reproducible and is not.
    """

    version = payload.get("schema")
    if version not in REQUEST_SNAPSHOT_READABLE:
        raise SnapshotError(
            C_SNAPSHOT_VERSION_UNSUPPORTED,
            (
                f"this generation was recorded in snapshot schema {version!r}, and "
                "this version reads schema "
                + ", ".join(str(item) for item in REQUEST_SNAPSHOT_READABLE)
            ),
        )
    try:
        config_payload = payload["config"]
        config = FeasibilityConfig(
            combined_daily_cap=int(config_payload["combined_daily_cap"]),
            area_daily_cap=int(config_payload["area_daily_cap"]),
            default_asset_limits=_asset_limits(config_payload["default_asset_limits"]),
            asset_limits={
                asset_id: _asset_limits(limits)
                for asset_id, limits in config_payload["asset_limits"].items()
            },
            search_node_limit=int(config_payload["search_node_limit"]),
        )
        cycle_payload = payload.get("cycle")
        cycle = (
            None
            if cycle_payload is None
            else CoverageCycle(
                start_month=_month(cycle_payload["start_month"]),
                duration_months=int(cycle_payload["duration_months"]),
                visits_per_asset=int(cycle_payload["visits_per_asset"]),
            )
        )
        return DraftRequest(
            start=date.fromisoformat(payload["start"]),
            end=date.fromisoformat(payload["end"]),
            mode=DraftMode(payload["mode"]),
            roster=tuple(
                Asset(
                    asset_id=item["asset_id"],
                    asset_type=AssetType(item["asset_type"]),
                    active=bool(item["active"]),
                )
                for item in payload["roster"]
            ),
            display=tuple(
                AssetDisplay(
                    asset_id=item["asset_id"],
                    name_en=item["name_en"],
                    name_kn=item["name_kn"],
                )
                for item in payload["display"]
            ),
            calendar_days=tuple(
                CalendarDay(
                    day=date.fromisoformat(item["day"]),
                    kind=CalendarDayKind(item["kind"]),
                    is_working_day=bool(item["is_working_day"]),
                    reasons_en=tuple(item["reasons_en"]),
                    reasons_kn=tuple(item["reasons_kn"]),
                )
                for item in payload["calendar_days"]
            ),
            config=config,
            seed=int(payload["seed"]),
            attempt_budget=int(payload["attempt_budget"]),
            rotation_offset=int(payload["rotation_offset"]),
            prior_history=tuple(
                Visit(item["asset_id"], date.fromisoformat(item["visit_date"]))
                for item in payload["prior_history"]
            ),
            fixed_by_month={
                _month(label): tuple(
                    Visit(item["asset_id"], date.fromisoformat(item["visit_date"]))
                    for item in visits
                )
                for label, visits in payload["fixed_by_month"].items()
            },
            entries=tuple(
                DayEntry(
                    entry_id=item["entry_id"],
                    day=date.fromisoformat(item["day"]),
                    kind=DayEntryKind(item["kind"]),
                    title_en=item["title_en"],
                    title_kn=item["title_kn"],
                    # Schema 1 did not record provenance, and every entry it stored
                    # came from the calendar; that is what it meant, so that is what
                    # it reads back as.
                    provenance=DayEntryProvenance(
                        item.get("provenance", DayEntryProvenance.CALENDAR.value)
                    ),
                )
                for item in payload["entries"]
            ),
            activities=tuple(
                ActivitySpec(
                    activity_id=item["activity_id"],
                    category=item["category"],
                    title_en=item["title_en"],
                    title_kn=item["title_kn"],
                    weight=float(item["weight"]),
                    monthly_min=int(item["monthly_min"]),
                    monthly_max=int(item["monthly_max"]),
                    repeat_gap_days=int(item["repeat_gap_days"]),
                    weekday_mask=item["weekday_mask"],
                )
                for item in payload.get("activities") or ()
            ),
            activities_per_day=int(payload.get("activities_per_day") or 0),
            # A snapshot written before the language existed records a register filed in
            # both, which is exactly what it was. It is not upgraded to today's default,
            # because there was no default then and the draft it describes was bilingual.
            entry_language=wording.normalise_language(payload.get("entry_language")),
            cycle=cycle,
            weights=(
                None
                if payload["weights"] is None
                else DtcWeights(
                    kva=dict(payload["weights"]["kva"]),
                    individual_adjustment=dict(
                        payload["weights"]["individual_adjustment"]
                    ),
                    classification={
                        asset_id: DtcClass(value)
                        for asset_id, value in payload["weights"][
                            "classification"
                        ].items()
                    },
                    department_multiplier=float(
                        payload["weights"]["department_multiplier"]
                    ),
                    self_dtc_multiplier=float(
                        payload["weights"]["self_dtc_multiplier"]
                    ),
                )
            ),
            area_required={
                _month(label): dict(counts)
                for label, counts in payload["area_required"].items()
            },
            feeder_required={
                _month(label): dict(counts)
                for label, counts in payload["feeder_required"].items()
            },
            dtc_target=payload["dtc_target"],
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, SnapshotError):
            raise
        raise SnapshotError(
            C_SNAPSHOT_MALFORMED,
            f"the stored request snapshot could not be read: {error}",
        ) from error
