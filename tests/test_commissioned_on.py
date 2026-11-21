"""The date a DTC was commissioned, and the dates it cannot be visited on.

An asset does not exist before it was commissioned, so no visit may be placed on or
before that date. The rule is enforced in one place per layer: the master file is read
into a canonical date, the roster carries it, the validator refuses a visit that breaks
it, the allocator never offers such a date, and the coverage cycle deals an asset's
unit to a month where it can actually be served.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from dailyworkreg.application.draft_generation import DraftMode, generate_draft
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.application.snapshot import (
    REQUEST_SNAPSHOT_SCHEMA,
    decode_request,
    encode_request,
)
from dailyworkreg.domain.cycle import (
    CoverageCycle,
    MonthKey,
    resolve_cycle,
    verify_cycle,
)
from dailyworkreg.domain.validator import (
    C_ASSET_NOT_YET_COMMISSIONED,
    C_FIXED_VISIT_BEFORE_COMMISSION,
    C_VISIT_BEFORE_COMMISSION,
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
    validate_schedule,
)
from dailyworkreg.infrastructure.imports.datasets import judge_dataset
from dailyworkreg.infrastructure.imports.tabular import read_source_bytes
from dailyworkreg.infrastructure.sqlite import masters, open_database
from dailyworkreg.infrastructure.sqlite.imports import (
    accept_batch,
    stage_bytes,
    staging_rows,
)
from dailyworkreg.infrastructure.sqlite.schema import (
    MIGRATIONS,
    SCHEMA_VERSION,
    apply_migrations,
)
from master_fixtures import seed_office_masters

DTC_HEADER = "DTC ID,DTC Name,Class,Feeder,kVA,Station,Date of commision\n"


def master_csv(*rows: str) -> bytes:
    return (DTC_HEADER + "".join(row + "\n" for row in rows)).encode()


def judged(*rows: str):
    source = read_source_bytes("dtc.csv", master_csv(*rows))
    return judge_dataset("dtc", source.headers, source.rows).outcomes[0]


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "commissioned.db")
    seed_office_masters(connection)
    yield connection
    connection.close()


def working_days(start: date, end: date) -> tuple[date, ...]:
    day = start
    days: list[date] = []
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


# --- the master file -------------------------------------------------------


def test_the_commissioning_column_is_read_as_a_date():
    source = read_source_bytes(
        "dtc.csv", master_csv("SCD-1,Alpha,DEPT,MRS F-1,250,Station A,01-01-2000")
    )
    payload = judge_dataset("dtc", source.headers, source.rows)[0] if False else judge_dataset(
        "dtc", source.headers, source.rows
    ).outcomes[0].payload
    assert payload["commissioned_on"] == "2000-01-01"
    assert payload["source"]["commissioned_on"] == "01-01-2000"


def test_a_blank_commissioning_date_is_no_restriction_not_a_refusal():
    row = judged("SCD-1,Alpha,DEPT,,250,,")
    assert row.payload["commissioned_on"] is None
    assert row.messages == ()


def test_a_placeholder_commissioning_date_is_reported_and_carried_as_none():
    row = judged("SCD-1,Alpha,DEPT,,250,,#N/A")
    assert row.payload["commissioned_on"] is None
    assert any("placeholder" in message for message in row.messages)


def test_a_date_that_cannot_exist_is_named_rather_than_called_unreadable():
    # 31-09-2026 has the shape of a date and a day September does not have. Saying
    # "not a recognisable date" sends the operator looking for a wrong format when
    # the fault is a wrong day.
    row = judged("SCD-1,Alpha,DEPT,,250,,31-09-2026")
    assert row.payload["commissioned_on"] is None
    message = " ".join(row.messages)
    assert "not a real calendar date" in message
    assert "September 2026 has 30 days, so there is no 31" in message


def test_an_impossible_month_is_named_as_such():
    row = judged("SCD-1,Alpha,DEPT,,250,,31/31/2020")
    assert row.payload["commissioned_on"] is None
    assert any("there is no month 31" in message for message in row.messages)


def test_a_february_that_is_not_a_leap_year_is_named_with_its_own_length():
    row = judged("SCD-1,Alpha,DEPT,,250,,29-02-2025")
    assert row.payload["commissioned_on"] is None
    assert any("February 2025 has 28 days" in message for message in row.messages)


def test_an_impossible_date_still_includes_the_row_and_imposes_nothing():
    # The row is bad data on one column, not a reason to lose a transformer: it is
    # imported with no date, which the schedule reads as no restriction.
    row = judged("SCD-1,Alpha,DEPT,,250,,31-09-2026")
    assert row.decision == "include"
    assert row.severity in {"warning", "error"}
    assert row.severity == "warning"
    assert row.payload["commissioned_on"] is None


def test_a_value_that_is_not_a_date_at_all_is_still_unreadable():
    row = judged("SCD-1,Alpha,DEPT,,250,,next month")
    assert row.payload["commissioned_on"] is None
    assert any("not a recognisable date" in message for message in row.messages)


def test_a_slash_date_is_read_day_first_like_every_other_date_in_the_platform():
    # The reader accepts day-first slash dates, the same convention the holiday file
    # uses. What it will not do is guess a genuinely unreadable value.
    row = judged("SCD-1,Alpha,DEPT,,250,,03/04/2026")
    assert row.payload["commissioned_on"] == "2026-04-03"


def test_accepting_the_batch_stores_the_date_and_keeps_the_source_value(db):
    batch, _ = stage_bytes(
        db,
        "dtc",
        "dtc.csv",
        master_csv("SCD-1,Alpha,DEPT,,250,Station A,15-09-2026"),
    )
    accept_batch(db, batch.batch_id)
    stored = masters.get_dtc(db, "SCD-1")
    assert stored is not None
    assert stored.commissioned_on == date(2026, 9, 15)
    raw = db.execute(
        "SELECT raw_commissioned_on FROM dtc_source_record WHERE dtc_id = 'SCD-1'"
    ).fetchone()
    assert raw["raw_commissioned_on"] == "15-09-2026"


def test_a_record_without_a_date_stays_without_one_after_acceptance(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", master_csv("SCD-1,Alpha,DEPT,,250,,"))
    accept_batch(db, batch.batch_id)
    stored = masters.get_dtc(db, "SCD-1")
    assert stored is not None
    assert stored.commissioned_on is None


def test_the_date_survives_a_reimport_and_an_upsert(db):
    first, _ = stage_bytes(
        db, "dtc", "dtc.csv", master_csv("SCD-1,Alpha,DEPT,,250,,01-01-2000")
    )
    accept_batch(db, first.batch_id)
    second, _ = stage_bytes(
        db, "dtc", "dtc.csv", master_csv("SCD-1,Alpha,DEPT,,250,,02-02-2001")
    )
    accept_batch(db, second.batch_id)
    stored = masters.get_dtc(db, "SCD-1")
    assert stored is not None
    assert stored.commissioned_on == date(2001, 2, 2)


# --- the schema ------------------------------------------------------------


def test_the_schema_is_at_eight_with_the_commissioning_column(tmp_path: Path):
    assert SCHEMA_VERSION == 8
    assert MIGRATIONS[-1].version == 8
    connection = open_database(tmp_path / "schema.db")
    try:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(dtc)")
        }
        raw = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(dtc_source_record)")
        }
        # A second run applies nothing: the migration is recorded, not replayed.
        assert apply_migrations(connection) == ()
    finally:
        connection.close()
    assert "commissioned_on" in columns
    assert "raw_commissioned_on" in raw


def test_an_existing_record_keeps_no_date_and_no_restriction(db):
    # Every row written before the column existed keeps NULL, which the schedule
    # reads as "no restriction". An upgrade must not declare the whole roster
    # unvisitable.
    masters.upsert_dtc(db, dtc_id="SCD-1", name="Alpha", dtc_class="DEPT")
    stored = masters.get_dtc(db, "SCD-1")
    assert stored is not None
    assert stored.commissioned_on is None


# --- the roster contract ---------------------------------------------------


def test_an_asset_is_visitable_only_after_its_commissioning_date():
    commissioned = Asset("SCD-1", AssetType.DTC, commissioned_on=date(2026, 9, 15))
    assert not commissioned.is_available_on(date(2026, 9, 14))
    # The commissioning day itself is not a visit date.
    assert not commissioned.is_available_on(date(2026, 9, 15))
    assert commissioned.is_available_on(date(2026, 9, 16))


def test_an_asset_with_no_recorded_date_is_visitable_everywhere():
    undated = Asset("SCD-1", AssetType.DTC)
    assert undated.commissioned_on is None
    assert undated.is_available_on(date(1990, 1, 1))
    assert undated.is_available_on(date(2030, 12, 31))


def test_a_non_date_commissioning_value_is_refused():
    with pytest.raises(ValueError):
        Asset("SCD-1", AssetType.DTC, commissioned_on="2026-09-15")  # type: ignore[arg-type]


# --- the validator ---------------------------------------------------------

MONTH = DatePeriod(date(2026, 9, 1), date(2026, 9, 30))
WORKING = working_days(MONTH.start, MONTH.end)


def context(*, commissioned_on: date | None, fixed: tuple[Visit, ...] = ()) -> ObligationContext:
    return ObligationContext(
        period=MONTH,
        calendar=Calendar(WORKING),
        roster=(Asset("SCD-1", AssetType.DTC, commissioned_on=commissioned_on),),
        config=FeasibilityConfig(),
        fixed=fixed,
        required_visits={"SCD-1": 1},
    )


def proposal(*, commissioned_on: date | None, proposed: tuple[Visit, ...], fixed=()):
    return ProposalContext(
        period=MONTH,
        calendar=Calendar(WORKING),
        roster=(Asset("SCD-1", AssetType.DTC, commissioned_on=commissioned_on),),
        config=FeasibilityConfig(),
        proposed=proposed,
        fixed=tuple(fixed),
    )


def test_a_visit_on_the_commissioning_day_is_refused():
    result = validate_schedule(
        proposal(
            commissioned_on=date(2026, 9, 15),
            proposed=(Visit("SCD-1", date(2026, 9, 15)),),
        )
    )
    assert not result.valid
    assert [v.constraint for v in result.violations] == [C_VISIT_BEFORE_COMMISSION]
    assert "commissioned on 2026-09-15" in result.violations[0].message


def test_a_visit_before_the_commissioning_date_is_refused():
    result = validate_schedule(
        proposal(
            commissioned_on=date(2026, 9, 15),
            proposed=(Visit("SCD-1", date(2026, 9, 1)),),
        )
    )
    assert not result.valid
    assert result.violations[0].constraint == C_VISIT_BEFORE_COMMISSION


def test_the_day_after_the_commissioning_date_is_accepted():
    result = validate_schedule(
        proposal(
            commissioned_on=date(2026, 9, 15),
            proposed=(Visit("SCD-1", date(2026, 9, 16)),),
        )
    )
    assert result.valid


def test_a_committed_visit_before_the_commissioning_date_is_reported_not_dropped():
    result = validate_schedule(
        proposal(
            commissioned_on=date(2026, 9, 15),
            proposed=(),
            fixed=(Visit("SCD-1", date(2026, 9, 10)),),
        )
    )
    assert not result.valid
    assert [v.constraint for v in result.violations] == [
        C_FIXED_VISIT_BEFORE_COMMISSION
    ]


def test_a_month_that_ends_before_the_commissioning_date_is_infeasible():
    result = assess_feasibility(context(commissioned_on=date(2026, 10, 1)))
    assert result.status is FeasibilityStatus.INFEASIBLE
    evidence = result.evidence[0]
    assert evidence.constraint == C_ASSET_NOT_YET_COMMISSIONED
    assert "cannot be visited in it at all" in evidence.detail
    assert "2026-10-01" in evidence.detail


def test_a_commissioning_date_late_in_the_month_leaves_too_few_dates():
    # One usable date remains, but two visits are owed.
    result = assess_feasibility(
        ObligationContext(
            period=MONTH,
            calendar=Calendar(WORKING),
            roster=(Asset("SCD-1", AssetType.DTC, commissioned_on=date(2026, 9, 29)),),
            config=FeasibilityConfig(
                default_asset_limits=AssetLimits(monthly_max=3, revisit_gap_days=1)
            ),
            required_visits={"SCD-1": 2},
        )
    )
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert result.evidence[0].constraint == C_ASSET_NOT_YET_COMMISSIONED
    assert "leaving 1 eligible date(s)" in result.evidence[0].detail


def test_a_month_the_asset_is_commissioned_during_is_still_feasible():
    result = assess_feasibility(context(commissioned_on=date(2026, 9, 15)))
    assert result.status is FeasibilityStatus.FEASIBLE
    assert all(
        visit.visit_date > date(2026, 9, 15) for visit in result.witness
    )


# --- the allocator ---------------------------------------------------------


def test_the_allocator_never_places_a_visit_on_or_before_the_date():
    from dailyworkreg.domain.allocator import AllocationRequest, allocate_month

    result = allocate_month(
        AllocationRequest(
            period=MONTH,
            calendar=Calendar(WORKING),
            roster=(
                Asset("SCD-1", AssetType.DTC, commissioned_on=date(2026, 9, 15)),
                Asset("A-01", AssetType.AREA),
            ),
            config=FeasibilityConfig(),
            required_visits={"SCD-1": 3, "A-01": 2},
        )
    )
    assert result.status is FeasibilityStatus.FEASIBLE
    transformer_dates = [
        visit.visit_date for visit in result.visits if visit.asset_id == "SCD-1"
    ]
    assert transformer_dates
    assert all(day > date(2026, 9, 15) for day in transformer_dates)


# --- the coverage cycle ----------------------------------------------------


def roster(*commissioned: tuple[str, date | None]) -> tuple[Asset, ...]:
    return tuple(
        Asset(asset_id, AssetType.DTC, commissioned_on=day)
        for asset_id, day in commissioned
    )


def test_a_dtc_commissioned_mid_cycle_is_dealt_to_a_later_month():
    resolution = resolve_cycle(
        roster(
            ("SCD-1", None),
            ("SCD-2", None),
            ("SCD-3", date(2026, 9, 10)),
        ),
        CoverageCycle(MonthKey(2026, 8), 3, 1),
    )
    assert "SCD-3" not in resolution.plans[0].obligations
    assert any("SCD-3" in plan.obligations for plan in resolution.plans[1:])
    assert verify_cycle(resolution) == ()


def test_a_dtc_commissioned_after_the_cycle_is_excluded_and_reported():
    resolution = resolve_cycle(
        roster(("SCD-1", None), ("SCD-2", date(2027, 3, 1))),
        CoverageCycle(MonthKey(2026, 8), 3, 1),
    )
    assert "SCD-2" in resolution.excluded
    assert "2026-10" in resolution.excluded["SCD-2"]
    assert "SCD-2" not in resolution.cycle_totals
    assert all("SCD-2" not in plan.obligations for plan in resolution.plans)
    assert verify_cycle(resolution) == ()


def test_a_dtc_commissioned_on_the_last_date_of_a_month_cannot_use_it():
    # Commissioned on 30-09, the last calendar date of September: no September date
    # is later than it, so the cycle must place it in a later month.
    resolution = resolve_cycle(
        roster(("SCD-1", None), ("SCD-2", date(2026, 9, 30))),
        CoverageCycle(MonthKey(2026, 8), 3, 1),
    )
    assert "SCD-2" not in resolution.plans[0].obligations
    assert "SCD-2" not in resolution.plans[1].obligations
    assert "SCD-2" in resolution.plans[2].obligations
    assert verify_cycle(resolution) == ()


def test_the_distribution_is_unchanged_when_no_asset_carries_a_date():
    # 113 transformers over three months is the recorded reference case: the
    # availability rule must not move a single unit when there is nothing to move.
    resolution = resolve_cycle(
        roster(*((f"SCD-{index}", None) for index in range(1, 114))),
        CoverageCycle(MonthKey(2026, 8), 3, 1),
    )
    assert [plan.total for plan in resolution.plans] == [38, 38, 37]
    assert resolution.excluded == {}
    assert verify_cycle(resolution) == ()


# --- generation over a real month ------------------------------------------


def office(db: sqlite3.Connection, *commissioned: tuple[str, date | None]) -> None:
    """A tiny office: three transformers, one area, one feeder, one activity."""

    masters.upsert_area(
        db, area_id="A-01", name_kn="ಎ", name_en="Area", monthly_max=3, monthly_min=1,
        revisit_gap_days=1, weight=1.0,
    )
    masters.upsert_feeder(
        db, feeder_id="F-01", name="Feeder", monthly_max=3, monthly_min=1,
        revisit_gap_days=1, manual_multiplier=1.0,
    )
    dates = dict(commissioned)
    for index in range(1, 4):
        asset_id = f"SCD-{index}"
        masters.upsert_dtc(
            db,
            dtc_id=asset_id,
            name=f"Transformer {index}",
            dtc_class="DEPT",
            kva=100.0,
            commissioned_on=dates.get(asset_id),
        )


def test_generation_never_records_a_visit_before_the_transformer_existed(db):
    office(db, ("SCD-1", date(2026, 9, 15)))
    spec = GenerationSpec(
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        mode=DraftMode.CAPACITY_FALLBACK,
        seed=7,
        dtc_target=3,
        area_required={MonthKey(2026, 9): {"A-01": 1}},
        feeder_required={MonthKey(2026, 9): {"F-01": 1}},
    )
    draft = generate_draft(prepare_generation(db, spec).request)
    assert draft.is_allocation_complete
    dates = [
        row.visit_date for row in draft.visits if row.asset_id == "SCD-1"
    ]
    assert dates
    assert all(day > date(2026, 9, 15) for day in dates)


def test_generation_reports_a_transformer_it_could_not_cover_at_all(db):
    office(db, ("SCD-2", date(2027, 3, 1)))
    spec = GenerationSpec(
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        mode=DraftMode.CAPACITY_FALLBACK,
        seed=7,
        dtc_target=3,
    )
    draft = generate_draft(prepare_generation(db, spec).request)
    uncovered = dict(draft.uncovered)
    assert "SCD-2" in uncovered
    assert "2027-03-01" in uncovered["SCD-2"]
    assert all(row.asset_id != "SCD-2" for row in draft.visits)


# --- the request snapshot --------------------------------------------------


def test_the_snapshot_keeps_each_roster_date(db):
    office(db, ("SCD-1", date(2026, 9, 15)))
    spec = GenerationSpec(
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        mode=DraftMode.CAPACITY_FALLBACK,
        seed=7,
    )
    request = prepare_generation(db, spec).request
    payload = encode_request(request)
    assert payload["schema"] == REQUEST_SNAPSHOT_SCHEMA == 6
    dates = {item["asset_id"]: item["commissioned_on"] for item in payload["roster"]}
    assert dates["SCD-1"] == "2026-09-15"
    assert dates["SCD-2"] is None
    assert decode_request(payload).roster == request.roster


def test_a_snapshot_written_before_the_date_existed_reads_without_one(db):
    office(db, ("SCD-1", date(2026, 9, 15)))
    spec = GenerationSpec(
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        mode=DraftMode.CAPACITY_FALLBACK,
        seed=7,
    )
    payload = encode_request(prepare_generation(db, spec).request)
    # What a schema 5 payload held: no commissioning date on any roster member.
    payload["schema"] = 5
    for item in payload["roster"]:
        item.pop("commissioned_on", None)
    decoded = decode_request(payload)
    assert all(asset.commissioned_on is None for asset in decoded.roster)
