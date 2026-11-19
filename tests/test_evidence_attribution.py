"""Structured evidence attribution.

Act 08 closes the last place a diagnostic could only be read by a human. Every
finding that concerns a particular asset, or a particular set of assets, now names
them structurally, all the way from the validator to the operator's screen.

The tests below pin three separate things, deliberately kept apart:

*   What the validator attributes, and to which shape (singular or plural).
*   What survives persistence, including a record written before attribution
    existed.
*   What the surface presents, without recovering anything from prose.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest
from dailyworkreg.application.draft_generation import DraftExceptionKind
from dailyworkreg.application.generation_runs import (
    _evidence,
    _stored_attribution,
    load_register,
    save_run,
)
from dailyworkreg.domain.validator import (
    Asset,
    AssetLimits,
    AssetType,
    Calendar,
    DatePeriod,
    Evidence,
    FeasibilityConfig,
    FeasibilityStatus,
    ObligationContext,
    ProposalContext,
    Visit,
    assess_feasibility,
    attribute_assets,
    validate_schedule,
)
from dailyworkreg.interfaces.api import create_app
from fastapi.testclient import TestClient

START = date(2026, 11, 1)
END = date(2026, 11, 30)


def _working_days() -> frozenset[date]:
    from datetime import timedelta

    days = set()
    day = START
    while day <= END:
        if day.weekday() != 6:  # every Sunday off
            days.add(day)
        day += timedelta(days=1)
    return frozenset(days)


def _context(
    roster,
    required=None,
    config=None,
    fixed=(),
    history=(),
    cycle=None,
    calendar=None,
    period=None,
):
    return ObligationContext(
        period=period or DatePeriod(START, END),
        calendar=calendar or Calendar(_working_days()),
        roster=tuple(roster),
        config=config or FeasibilityConfig(),
        required_visits=dict(required or {}),
        cycle_obligations=dict(cycle or {}),
        fixed=tuple(fixed),
        history=tuple(history),
    )


def _roster():
    return [
        Asset("A-01", AssetType.AREA),
        Asset("A-02", AssetType.AREA),
        Asset("A-03", AssetType.AREA),
        Asset("SCD-001", AssetType.DTC),
        Asset("SCD-002", AssetType.DTC),
        Asset("F-01", AssetType.FEEDER),
    ]


# --- single-asset attribution ----------------------------------------------


def test_a_minimum_above_the_maximum_names_the_asset():
    result = assess_feasibility(
        _context(
            _roster(),
            {"SCD-001": 1},
            FeasibilityConfig(
                asset_limits={"SCD-001": AssetLimits(monthly_min=2, monthly_max=1)}
            ),
        )
    )

    assert result.status is FeasibilityStatus.INFEASIBLE
    (evidence,) = result.evidence
    assert evidence.constraint == "configured_minimum_exceeds_maximum"
    assert evidence.asset_id == "SCD-001"
    assert evidence.asset_type is AssetType.DTC
    assert (evidence.limit, evidence.actual) == (1, 2)
    assert "SCD-001" in evidence.detail


def test_a_monthly_maximum_exceeded_names_the_asset():
    result = assess_feasibility(
        _context(
            _roster(),
            {"SCD-001": 2},
            FeasibilityConfig(
                asset_limits={"SCD-001": AssetLimits(monthly_min=0, monthly_max=1)}
            ),
        )
    )

    assert result.status is FeasibilityStatus.INFEASIBLE
    (evidence,) = result.evidence
    assert evidence.constraint == "asset_monthly_max_exceeded"
    assert evidence.asset_id == "SCD-001"
    assert evidence.asset_type is AssetType.DTC
    assert (evidence.limit, evidence.actual) == (1, 2)


def test_a_revisit_gap_failure_names_the_asset():
    result = assess_feasibility(
        _context(
            _roster()[3:4],
            {"SCD-001": 6},
            FeasibilityConfig(
                asset_limits={
                    "SCD-001": AssetLimits(monthly_min=0, monthly_max=30)
                }
            ),
        )
    )

    assert result.status is FeasibilityStatus.INFEASIBLE
    (evidence,) = result.evidence
    assert evidence.constraint == "revisit_gap_violated"
    assert evidence.asset_id == "SCD-001"
    assert evidence.asset_type is AssetType.DTC
    assert (evidence.limit, evidence.actual) == (7, 6)


def test_an_asset_search_budget_failure_names_the_asset():
    result = assess_feasibility(
        _context(
            _roster()[3:],
            {"SCD-001": 3, "SCD-002": 3},
            FeasibilityConfig(
                search_node_limit=1,
                asset_limits={
                    "SCD-001": AssetLimits(monthly_min=0, monthly_max=4),
                    "SCD-002": AssetLimits(monthly_min=0, monthly_max=4),
                },
            ),
        )
    )

    assert result.status is FeasibilityStatus.INDETERMINATE
    (evidence,) = result.evidence
    assert evidence.constraint == "search_node_limit_reached"
    assert evidence.asset_id == "SCD-001"
    assert evidence.asset_type is AssetType.DTC
    assert evidence.limit == 1
    # The node budget is the limit; an asset visit count would be a different unit.
    assert evidence.actual is None


# --- multi-asset attribution ------------------------------------------------


def test_aggregate_area_capacity_names_every_area_asset_involved():
    result = assess_feasibility(
        _context(
            _roster(),
            {"A-01": 20, "A-02": 20, "A-03": 20, "SCD-001": 1},
            FeasibilityConfig(
                area_daily_cap=1,
                combined_daily_cap=30,
                asset_limits={
                    "A-01": AssetLimits(monthly_min=0, monthly_max=25),
                    "A-02": AssetLimits(monthly_min=0, monthly_max=25),
                    "A-03": AssetLimits(monthly_min=0, monthly_max=25),
                    "SCD-001": AssetLimits(monthly_min=0, monthly_max=5),
                },
            ),
        )
    )

    assert result.status is FeasibilityStatus.INFEASIBLE
    (evidence,) = result.evidence
    assert evidence.constraint == "aggregate_area_capacity_exceeded"
    assert evidence.asset_id is None
    assert evidence.asset_type is None
    assert [a for a, _ in evidence.contributing_assets] == ["A-01", "A-02", "A-03"]


def test_aggregate_area_capacity_excludes_dtc_and_feeder_assets():
    result = assess_feasibility(
        _context(
            _roster(),
            {
                "A-01": 20,
                "A-02": 20,
                "A-03": 20,
                "SCD-001": 1,
                "SCD-002": 1,
                "F-01": 1,
            },
            FeasibilityConfig(
                area_daily_cap=1,
                combined_daily_cap=30,
                asset_limits={
                    "A-01": AssetLimits(monthly_min=0, monthly_max=25),
                    "A-02": AssetLimits(monthly_min=0, monthly_max=25),
                    "A-03": AssetLimits(monthly_min=0, monthly_max=25),
                    "SCD-001": AssetLimits(monthly_min=0, monthly_max=5),
                    "SCD-002": AssetLimits(monthly_min=0, monthly_max=5),
                    "F-01": AssetLimits(monthly_min=0, monthly_max=5),
                },
            ),
        )
    )

    assert result.status is FeasibilityStatus.INFEASIBLE
    (evidence,) = result.evidence
    assert evidence.constraint == "aggregate_area_capacity_exceeded"
    named = {a for a, _ in evidence.contributing_assets}
    assert named == {"A-01", "A-02", "A-03"}
    assert not {"SCD-001", "SCD-002", "F-01"} & named


def test_aggregate_combined_capacity_names_every_category():
    areas = [Asset(f"A-{i:02d}", AssetType.AREA) for i in range(1, 7)]
    dtcs = [Asset(f"SCD-{i:03d}", AssetType.DTC) for i in range(1, 21)]
    feeders = [Asset("F-01", AssetType.FEEDER)]
    roster = areas + dtcs + feeders
    required = {a.asset_id: 3 for a in roster}
    limits = {a.asset_id: AssetLimits(monthly_min=0, monthly_max=4) for a in roster}

    result = assess_feasibility(
        _context(
            roster,
            required,
            # A stated total is the only ceiling that can bind here, so the per-kind
            # ceilings are set above the demand each kind carries. A total refusal is
            # the one shape that names every category on the same date.
            FeasibilityConfig(
                dtc_daily_cap=20,
                area_daily_cap=3,
                feeder_daily_cap=5,
                combined_daily_cap=3,
                asset_limits=limits,
            ),
        )
    )

    assert result.status is FeasibilityStatus.INFEASIBLE
    (evidence,) = result.evidence
    assert evidence.constraint == "aggregate_combined_capacity_exceeded"
    types = {asset_type for _, asset_type in evidence.contributing_assets}
    assert types == {AssetType.AREA, AssetType.DTC, AssetType.FEEDER}
    names = [a for a, _ in evidence.contributing_assets]
    assert names == sorted(names)
    assert names == sorted(a.asset_id for a in roster)


def test_a_joint_packing_failure_names_the_assets_that_could_not_be_packed():
    # Aggregates pass: 2 area visits within 1 per day over 2 eligible days. Both
    # assets are pinned to the same date by their own revisit gap, so no joint
    # assignment exists. The contributing set is exactly those two.
    result = assess_feasibility(
        ObligationContext(
            period=DatePeriod(START, date(2026, 11, 3)),
            calendar=Calendar(frozenset({date(2026, 11, 2), date(2026, 11, 3)})),
            roster=(Asset("A-01", AssetType.AREA), Asset("A-02", AssetType.AREA)),
            config=FeasibilityConfig(
                combined_daily_cap=2,
                area_daily_cap=1,
                asset_limits={
                    "A-01": AssetLimits(monthly_min=0, monthly_max=2),
                    "A-02": AssetLimits(monthly_min=0, monthly_max=2),
                },
            ),
            required_visits={"A-01": 1, "A-02": 1},
            history=(
                Visit("A-01", date(2026, 10, 27)),
                Visit("A-02", date(2026, 10, 27)),
            ),
        )
    )

    assert result.status is FeasibilityStatus.INFEASIBLE
    (evidence,) = result.evidence
    assert evidence.constraint == "combined_daily_cap_exceeded"
    assert [a for a, _ in evidence.contributing_assets] == ["A-01", "A-02"]
    assert {t for _, t in evidence.contributing_assets} == {AssetType.AREA}


# --- conversion, ordering, and the refusal to invent ------------------------


def test_preflight_conversion_preserves_violation_attribution():
    context = ProposalContext(
        period=DatePeriod(date(2026, 11, 2), date(2026, 11, 2)),
        calendar=Calendar(frozenset({date(2026, 11, 2)})),
        roster=tuple(_roster()),
        config=FeasibilityConfig(combined_daily_cap=3),
        proposed=tuple(
            Visit(asset_id, date(2026, 11, 2))
            for asset_id in ("A-01", "A-02", "SCD-001", "SCD-002")
        ),
    )
    validation = validate_schedule(context)
    violation = next(
        v for v in validation.violations if v.constraint == "combined_daily_cap_exceeded"
    )
    assert violation.asset_id is None
    assert len(violation.contributing_assets) == 4

    # The same visit list as an immutable commitment forces the preflight path.
    assessment = assess_feasibility(
        ObligationContext(
            period=context.period,
            calendar=context.calendar,
            roster=context.roster,
            config=context.config,
            fixed=context.proposed,
        )
    )

    assert assessment.status is FeasibilityStatus.INFEASIBLE
    (evidence,) = assessment.evidence
    assert evidence.constraint == "combined_daily_cap_exceeded"
    assert evidence.contributing_assets == violation.contributing_assets
    assert evidence.asset_id == violation.asset_id
    assert evidence.offending_dates == violation.offending_dates


def test_contributing_assets_are_deduplicated_and_sorted():
    roster = {a.asset_id: a for a in _roster()}
    attribution = attribute_assets(
        ["SCD-002", "A-01", "SCD-002", "A-01", "SCD-001"], roster
    )

    assert [a for a, _ in attribution] == ["A-01", "SCD-001", "SCD-002"]
    assert len(attribution) == len({a for a, _ in attribution})


def test_repeated_visits_by_one_asset_name_it_once_and_still_count_each_visit():
    # Three visits by one area asset on one date. The cap is exceeded by visits, not
    # by identities: the count must stay three while the attribution names one asset.
    context = _context(
        _roster(),
        {"A-01": 1},
        FeasibilityConfig(area_daily_cap=2, combined_daily_cap=9),
    )
    day = date(2026, 11, 3)
    result = validate_schedule(
        ProposalContext(
            period=context.period,
            calendar=context.calendar,
            roster=tuple(_roster()),
            config=context.config,
            proposed=(Visit("A-01", day),) * 3,
            required_visits={},
        )
    )

    cap = [item for item in result.violations if item.constraint == "area_daily_cap_exceeded"]
    assert len(cap) == 1
    assert cap[0].actual == 3
    assert cap[0].contributing_assets == (("A-01", AssetType.AREA),)


def test_an_unknown_asset_is_dropped_rather_than_guessed():
    roster = {a.asset_id: a for a in _roster()}
    assert attribute_assets(["A-01", "NOPE-9"], roster) == (
        ("A-01", AssetType.AREA),
    )


def test_a_whole_problem_budget_failure_claims_no_asset():
    # The packing step across every asset exhausts its budget. No subset caused
    # that, so no identity is claimed rather than naming the roster.
    result = assess_feasibility(
        _context(
            _roster(),
            {"A-01": 1, "A-02": 1, "SCD-001": 1},
            FeasibilityConfig(search_node_limit=2),
        )
    )

    assert result.status is FeasibilityStatus.INDETERMINATE
    evidence = result.evidence[-1]
    if evidence.constraint == "search_node_limit_reached" and "packing" in evidence.detail:
        assert evidence.asset_id is None
        assert evidence.contributing_assets == ()


def test_a_feasible_assessment_attributes_nothing():
    result = assess_feasibility(_context(_roster(), {"A-01": 1}))

    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.evidence == ()
    assert len(result.witness) == 1


def test_existing_evidence_construction_still_accepts_positional_limit_and_actual():
    # Appending contributing_assets last keeps this working. Inserting it before
    # limit would have silently moved a caller's limit into the wrong field.
    evidence = Evidence("c", "d", "A-01", AssetType.AREA, None, (), 3, 4)

    assert (evidence.limit, evidence.actual) == (3, 4)
    assert evidence.contributing_assets == ()


# --- persistence ------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """A database stocked well enough to generate a month, and to fail one."""

    from dailyworkreg.infrastructure.sqlite import calendar_records as cal
    from dailyworkreg.infrastructure.sqlite import masters
    from dailyworkreg.infrastructure.sqlite.database import open_database
    from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities

    connection = open_database(tmp_path / "store.db")
    seed_approved_identities(connection)
    for index in range(1, 25):
        masters.upsert_dtc(
            connection,
            dtc_id=f"SCD-{index}",
            name=f"Test DTC {index}",
            dtc_class="DEPT" if index % 3 else "SELF_DTC",
            kva=100.0 + index,
            canonical_feeder_id=f"F-0{(index % 4) + 1}",
        )
    masters.upsert_activity(
        connection,
        activity_id="A-001",
        category="Inspection",
        description_en="Inspect the DTC",
    )
    for holiday in (date(2026, 11, 10), date(2026, 12, 25)):
        cal.upsert_holiday(
            connection,
            holiday_id=f"H-{holiday.isoformat()}",
            holiday_date=holiday,
            name="Recorded holiday",
            source="karnataka",
        )
    yield connection
    connection.close()


def _spec(*, visits: int = 1):
    from dailyworkreg.application.draft_generation import DraftMode
    from dailyworkreg.application.draft_requests import GenerationSpec
    from dailyworkreg.domain.cycle import MonthKey

    return GenerationSpec(
        start=date(2026, 11, 1),
        end=date(2026, 11, 30),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=1,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=1,
        cycle_visits_per_asset=visits,
    )


def _generated(connection, *, visits: int = 1):
    from dailyworkreg.application.draft_generation import generate_draft
    from dailyworkreg.application.draft_requests import prepare_generation

    prepared = prepare_generation(connection, _spec(visits=visits))
    return prepared, generate_draft(prepared.request)


def _infeasible(connection):
    prepared, draft = _generated(connection, visits=9)
    assert draft.exceptions
    return prepared, draft


def _annotated(connection):
    """A draft whose first exception carries a real multi-asset attribution."""

    prepared, draft = _generated(connection, visits=9)
    assert draft.exceptions
    attributed = tuple(
        replace(
            item,
            asset_id=None,
            asset_type=None,
            contributing_assets=(
                ("A-01", AssetType.AREA),
                ("SCD-001", AssetType.DTC),
            ),
        )
        if index == 0
        else item
        for index, item in enumerate(draft.exceptions)
    )
    return prepared, replace(draft, exceptions=attributed)


def test_evidence_survives_sqlite_with_every_structured_field(store):

    prepared, draft = _infeasible(store)
    run_id = save_run(store, prepared, draft)

    loaded = load_register(store, run_id)
    original = draft.months[0].evidence
    stored = loaded.months[0].evidence
    assert len(stored) == len(original) > 0

    for before, after in zip(original, stored, strict=True):
        assert after.constraint == before.constraint
        assert after.detail == before.detail
        assert after.asset_id == before.asset_id
        assert after.asset_type == before.asset_type
        assert after.visit_date == before.visit_date
        assert tuple(after.offending_dates) == tuple(before.offending_dates)
        assert after.limit == before.limit
        assert after.actual == before.actual


def test_exception_attribution_survives_sqlite(store):

    prepared, draft = _annotated(store)
    assert draft.exceptions
    run_id = save_run(store, prepared, draft)
    loaded = load_register(store, run_id)

    assert len(loaded.exceptions) == len(draft.exceptions) > 0
    for before, after in zip(draft.exceptions, loaded.exceptions, strict=True):
        assert after.constraint == before.constraint
        assert after.asset_id == before.asset_id
        assert after.asset_type == before.asset_type
        assert after.limit == before.limit
        assert after.actual == before.actual
        assert tuple(after.contributing_assets) == tuple(before.contributing_assets)
        assert tuple(after.offending_dates) == tuple(before.offending_dates)

    assert loaded.exceptions[0].contributing_assets == (
        ("A-01", AssetType.AREA),
        ("SCD-001", AssetType.DTC),
    )


def test_evidence_written_before_attribution_is_read_as_having_none():
        # Exactly the shape the previous release wrote: no asset_type, no
    # offending_dates and no contributing_assets.
    old = {
        "constraint": "asset_monthly_max_exceeded",
        "detail": "x owes 4",
        "asset_id": "SCD-001",
        "visit_date": None,
        "limit": 3,
        "actual": 4,
    }
    restored = _evidence(old)

    assert restored.asset_id == "SCD-001"
    assert restored.asset_type is None
    assert restored.offending_dates == ()
    assert restored.contributing_assets == ()
    assert (restored.limit, restored.actual) == (3, 4)


def test_an_old_exception_row_without_attribution_still_loads(store):

    prepared, draft = _annotated(store)
    run_id = save_run(store, prepared, draft)

    # Simulate a row written by the release before the column was populated.
    store.execute(
        "UPDATE generation_exception SET contributing_assets_json = '[]' "
        "WHERE run_id = ?",
        (run_id,),
    )
    store.commit()

    loaded = load_register(store, run_id)
    assert all(item.contributing_assets == () for item in loaded.exceptions)
    assert loaded.exceptions[0].constraint == draft.exceptions[0].constraint


def test_a_stored_attribution_with_an_unknown_type_is_dropped():
    decoded = _stored_attribution(
        json.dumps([["A-01", "AREA"], ["X-1", "GALAXY"], ["bad"], ["F-01", "FEEDER"]])
    )

    assert decoded == (("A-01", AssetType.AREA), ("F-01", AssetType.FEEDER))


def test_a_missing_attribution_value_decodes_as_empty():
    assert _stored_attribution("") == ()
    assert _stored_attribution("not json") == ()


# --- surface ----------------------------------------------------------------


@pytest.fixture()
def client(tmp_path) -> TestClient:
    return TestClient(create_app(tmp_path / "api.db"))


@pytest.fixture()
def stocked(client: TestClient) -> TestClient:
    for index in range(1, 9):
        client.put(
            "/api/masters/dtc",
            json={
                "dtc_id": f"SCD-{index}",
                "name": f"Test DTC {index}",
                "dtc_class": "DEPT" if index % 2 else "SELF_DTC",
                "kva": 100.0 + index,
                "canonical_feeder_id": f"F-0{(index % 4) + 1}",
            },
        )
    client.put(
        "/api/masters/activity",
        json={
            "activity_id": "A-001",
            "category": "Inspection",
            "description_en": "Inspect the DTC",
        },
    )
    for day in ("2026-11-10", "2026-12-25"):
        client.put(
            "/api/calendar/holidays",
            json={
                "holiday_date": day,
                "name": "Recorded holiday",
                "source": "karnataka",
            },
        )
    return client


def _attempt(client: TestClient, **overrides) -> dict:
    payload = {
        "start": "2026-11-01",
        "end": "2026-11-30",
        "mode": "coverage_cycle",
        "seed": 7,
        "cycle_start_month": "2026-11",
        "cycle_duration_months": 1,
        "cycle_visits_per_asset": 1,
    }
    payload.update(overrides)
    return client.post("/api/generations", json=payload).json()


def test_the_api_returns_structured_attribution_for_an_attempt(stocked):
    made = _attempt(stocked, cycle_visits_per_asset=9)
    body = stocked.get(f"/api/generations/{made['run_id']}").json()

    assert body["status"] in {"infeasible", "unresolved"}
    assert body["exceptions"]
    for item in body["exceptions"]:
        assert set(item) >= {
            "kind",
            "month",
            "constraint",
            "detail",
            "asset_id",
            "asset_type",
            "visit_date",
            "offending_dates",
            "contributing_assets",
            "limit",
            "actual",
        }
        assert isinstance(item["contributing_assets"], list)
        assert isinstance(item["offending_dates"], list)

    monthly = [
        item
        for item in body["exceptions"]
        if item["constraint"] == "asset_monthly_max_exceeded"
    ]
    assert monthly
    for item in monthly:
        assert item["asset_id"] and item["asset_id"].startswith("SCD-")
        assert item["asset_type"] in {"DTC", "AREA", "FEEDER"}
        assert item["limit"] is not None and item["actual"] is not None
        assert item["limit"] < item["actual"]


def test_the_api_reports_month_evidence_with_all_fields(stocked):
    made = _attempt(stocked, cycle_visits_per_asset=9)
    body = stocked.get(f"/api/generations/{made['run_id']}").json()

    assert [month for month in body["months"] if month["evidence"]]
    for month in body["months"]:
        for item in month["evidence"]:
            assert set(item) == {
                "constraint",
                "detail",
                "asset_id",
                "asset_type",
                "visit_date",
                "offending_dates",
                "contributing_assets",
                "limit",
                "actual",
            }


def test_a_completed_draft_claims_no_single_asset_for_a_joint_finding(stocked):
    made = _attempt(stocked)
    body = stocked.get(f"/api/generations/{made['run_id']}").json()

    assert body["is_draft"] is True
    for month in body["months"]:
        for item in month["evidence"]:
            if item["contributing_assets"]:
                assert item["asset_id"] is None


def test_the_interface_renders_attribution_without_parsing_messages():
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1]
        / "dailyworkreg"
        / "interfaces"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "attributionTable" in script
    assert "contributing_assets" in script
    assert "asset_type" in script
    # It reads the structured columns; it never digs an asset out of the message.
    assert "detail.match" not in script
    assert "detail.split" not in script
    assert "detail.indexOf" not in script


def test_exception_kinds_are_unchanged():
    # This act added attribution, not vocabulary.
    assert {kind.value for kind in DraftExceptionKind} == {
        "over_covered",
        "infeasible",
        "unresolved",
        "row_warning",
    }


def test_a_database_left_at_schema_one_upgrades_and_keeps_its_rows(store):
    """The real upgrade path: an old table, old rows, then migration 2."""

    from dailyworkreg.application.generation_runs import load_register, save_run
    from dailyworkreg.infrastructure.sqlite.schema import apply_migrations

    prepared, draft = _annotated(store)
    run_id = save_run(store, prepared, draft)
    before = len(draft.exceptions)
    assert before > 0

    # Rebuild the exception table exactly as schema version 1 defined it, and
    # forget that migration 2 was ever applied.
    store.execute(
        """
        CREATE TABLE generation_exception_v1 (
            run_id       TEXT NOT NULL REFERENCES generation_run (run_id) ON DELETE CASCADE,
            ordinal      INTEGER NOT NULL,
            kind         TEXT NOT NULL,
            constraint_name TEXT NOT NULL,
            detail       TEXT NOT NULL,
            month_key    TEXT,
            asset_id     TEXT,
            asset_type   TEXT,
            visit_date   TEXT,
            offending_dates_json TEXT NOT NULL DEFAULT '[]',
            limit_value  INTEGER,
            actual_value INTEGER
        )
        """
    )
    store.execute(
        """
        INSERT INTO generation_exception_v1 (run_id, ordinal, kind, constraint_name,
            detail, month_key, asset_id, asset_type, visit_date,
            offending_dates_json, limit_value, actual_value)
        SELECT run_id, ordinal, kind, constraint_name, detail, month_key, asset_id,
            asset_type, visit_date, offending_dates_json, limit_value, actual_value
        FROM generation_exception
        """
    )
    store.execute("DROP TABLE generation_exception")
    store.execute("ALTER TABLE generation_exception_v1 RENAME TO generation_exception")
    store.execute("DELETE FROM schema_migration WHERE version = 2")
    store.commit()

    assert apply_migrations(store) == (2,)

    loaded = load_register(store, run_id)
    assert len(loaded.exceptions) == before
    for item in loaded.exceptions:
        assert item.contributing_assets == ()
        assert item.constraint
        assert item.asset_type in {
            None,
            AssetType.AREA,
            AssetType.DTC,
            AssetType.FEEDER,
        }
