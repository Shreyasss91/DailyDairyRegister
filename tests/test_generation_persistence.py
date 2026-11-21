"""Generation persistence: every attempt kept, drafts immutable, inputs replayable.

A generation that is not stored is a claim the office cannot check tomorrow. These
tests pin the properties that make the stored record worth having: a failed attempt is
kept with its evidence, a new attempt never overwrites an old one, and the stored
inputs still describe the same work after the masters change underneath them.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application.draft_generation import (
    DraftMode,
    DraftStatus,
    generate_draft,
)
from dailyworkreg.application.draft_requests import (
    GenerationInputError,
    GenerationSpec,
    PreparedGeneration,
    prepare_generation,
)
from dailyworkreg.application.snapshot import (
    C_SNAPSHOT_VERSION_UNSUPPORTED,
    REQUEST_SNAPSHOT_SCHEMA,
    SnapshotError,
    decode_request,
    encode_request,
)
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from master_fixtures import seed_office_masters


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "test.db")
    seed_office_masters(connection)
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


def cycle_spec(
    *, start: date = date(2026, 11, 1), end: date = date(2026, 11, 30), seed: int = 7
) -> GenerationSpec:
    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=seed,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=1,
        cycle_visits_per_asset=1,
    )


def generated(
    db: sqlite3.Connection, spec: GenerationSpec
) -> tuple[PreparedGeneration, object]:
    prepared = prepare_generation(db, spec)
    return prepared, generate_draft(prepared.request)


# --- request preparation -------------------------------------------------


def test_the_request_is_built_from_the_stored_masters(db):
    prepared, _ = generated(db, cycle_spec())
    asset_ids = {asset.asset_id for asset in prepared.request.roster}
    assert len([a for a in asset_ids if a.startswith("SCD-")]) == 24
    assert {"A-01", "F-01"} <= asset_ids
    assert {display.asset_id for display in prepared.request.display} == asset_ids


def test_no_active_asset_is_left_without_display_metadata(db):
    prepared, _ = generated(db, cycle_spec())
    assert len(prepared.request.display) == len(prepared.request.roster)


def test_a_roster_asset_that_is_inactive_is_not_offered(db):
    masters.upsert_dtc(
        db, dtc_id="SCD-99", name="Retired", dtc_class="DEPT", kva=50.0, active=False
    )
    prepared, _ = generated(db, cycle_spec())
    assert "SCD-99" not in {asset.asset_id for asset in prepared.request.roster}


def test_the_generation_refuses_when_no_asset_is_stored(tmp_path):
    connection = open_database(tmp_path / "empty.db")
    seed_office_masters(connection)
    connection.execute("DELETE FROM dtc")
    connection.execute("DELETE FROM feeder")
    connection.execute("DELETE FROM area")
    with pytest.raises(GenerationInputError) as error:
        prepare_generation(connection, cycle_spec())
    assert error.value.constraint == "generation_roster_empty"
    connection.close()


def test_the_stored_master_version_is_recorded_with_the_preparation(db):
    prepared, _ = generated(db, cycle_spec())
    assert prepared.master_version == masters.current_version(db)


def test_the_weights_come_from_the_domain_model(db):
    prepared, _ = generated(db, cycle_spec())
    model = prepared.request.weights
    assert model is not None
    # A department DTC weighs its kVA; a self DTC weighs kVA at the lower multiplier.
    assert model.kva["SCD-1"] == 101.0
    assert model.weight_for(
        next(a for a in prepared.request.roster if a.asset_id == "SCD-1")
    ) == pytest.approx(101.0)
    assert model.weight_for(
        next(a for a in prepared.request.roster if a.asset_id == "SCD-3")
    ) == pytest.approx(103.0 * model.self_dtc_multiplier)


def test_a_missing_month_selection_is_refused(db):
    spec = GenerationSpec(
        start=date(2026, 11, 5),
        end=date(2026, 11, 4),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=0,
    )
    with pytest.raises(GenerationInputError) as error:
        prepare_generation(db, spec)
    assert error.value.constraint == "draft_range_reversed"


# --- snapshot ------------------------------------------------------------


def test_the_request_survives_a_round_trip_through_the_snapshot(db):
    prepared, _ = generated(db, cycle_spec())
    restored = decode_request(encode_request(prepared.request))
    assert restored.start == prepared.request.start
    assert restored.end == prepared.request.end
    assert restored.mode is prepared.request.mode
    assert restored.seed == prepared.request.seed
    assert restored.roster == prepared.request.roster
    assert restored.display == prepared.request.display
    assert restored.calendar_days == prepared.request.calendar_days
    assert restored.entries == prepared.request.entries
    assert restored.config == prepared.request.config
    assert restored.cycle == prepared.request.cycle
    assert restored.weights == prepared.request.weights


def test_the_snapshot_carries_its_schema_version(db):
    prepared, _ = generated(db, cycle_spec())
    assert encode_request(prepared.request)["schema"] == REQUEST_SNAPSHOT_SCHEMA


def test_a_snapshot_from_a_future_schema_is_refused(db):
    prepared, _ = generated(db, cycle_spec())
    payload = encode_request(prepared.request)
    payload["schema"] = REQUEST_SNAPSHOT_SCHEMA + 1
    with pytest.raises(SnapshotError) as error:
        decode_request(payload)
    assert error.value.constraint == C_SNAPSHOT_VERSION_UNSUPPORTED


def test_a_malformed_snapshot_is_refused_rather_than_half_read(db):
    prepared, _ = generated(db, cycle_spec())
    payload = encode_request(prepared.request)
    del payload["calendar_days"]
    with pytest.raises(SnapshotError) as error:
        decode_request(payload)
    assert error.value.constraint == "request_snapshot_malformed"


# --- persistence ---------------------------------------------------------


def test_a_completed_run_is_stored_with_its_rows_and_visits(db):
    prepared, draft = generated(db, cycle_spec())
    run_id = generations.save_run(db, prepared, draft)

    run = db.execute(
        "SELECT * FROM generation_run WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert run["outcome"] == "allocation_complete"
    assert run["is_draft"] == 1
    assert run["seed"] == 7
    assert run["generator_version"] == draft.generator_version

    rows = db.execute(
        "SELECT COUNT(*) AS count FROM generation_row WHERE run_id = ?", (run_id,)
    ).fetchone()["count"]
    visits = db.execute(
        "SELECT COUNT(*) AS count FROM generation_visit WHERE run_id = ?", (run_id,)
    ).fetchone()["count"]
    assert rows == len(draft.rows)
    assert visits == len(draft.visits)


def test_a_stored_run_reads_back_exactly_as_it_was_presented(db):
    prepared, draft = generated(db, cycle_spec())
    run_id = generations.save_run(db, prepared, draft)
    loaded = generations.load_register(db, run_id)

    assert loaded.status is draft.status
    assert [row.day for row in loaded.rows] == [row.day for row in draft.rows]
    assert [row.kind for row in loaded.rows] == [row.kind for row in draft.rows]
    assert [row.reasons_en for row in loaded.rows] == [
        row.reasons_en for row in draft.rows
    ]
    assert [
        (visit.visit.asset_id, visit.visit.visit_date, visit.provenance)
        for visit in loaded.visits
    ] == [
        (visit.visit.asset_id, visit.visit.visit_date, visit.provenance)
        for visit in draft.visits
    ]
    assert [m.month for m in loaded.months] == [m.month for m in draft.months]


def test_a_stored_run_keeps_its_fixed_and_allocated_visits_apart(db):
    prepared, draft = generated(db, cycle_spec())
    run_id = generations.save_run(db, prepared, draft)
    loaded = generations.load_register(db, run_id)
    fixed = sum(len(row.fixed_visits) for row in loaded.rows)
    allocated = sum(len(row.allocated_visits) for row in loaded.rows)
    assert fixed + allocated == len(loaded.visits)


def test_a_second_generation_starts_its_own_register_and_leaves_the_first_alone(db):
    prepared, draft = generated(db, cycle_spec())
    first = generations.save_run(db, prepared, draft)

    prepared2, draft2 = generated(db, cycle_spec(seed=11))
    second = generations.save_run(db, prepared2, draft2)

    assert first != second
    revisions = db.execute(
        """SELECT r.run_id, r.revision_number, g.register_id
           FROM draft_revision r JOIN generation_run g ON g.run_id = r.run_id
           ORDER BY r.created_at, r.run_id"""
    ).fetchall()
    # Revision numbers are per register. An unrelated generation is not the second
    # revision of the first one's register; it is revision 1 of its own.
    assert [(row["run_id"], row["revision_number"]) for row in revisions] == [
        (first, 1),
        (second, 1),
    ]
    assert revisions[0]["register_id"] != revisions[1]["register_id"]
    # The first draft is unchanged by the arrival of the second.
    assert generations.load_register(db, first).seed == 7
    assert generations.load_register(db, second).seed == 11


def test_an_infeasible_attempt_is_kept_with_evidence_and_no_draft(db):
    spec = GenerationSpec(
        start=date(2026, 11, 1),
        end=date(2026, 11, 30),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=1,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=1,
        cycle_visits_per_asset=9,
    )
    prepared, draft = generated(db, spec)
    assert draft.status is not DraftStatus.ALLOCATION_COMPLETE
    run_id = generations.save_run(db, prepared, draft)

    run = db.execute(
        "SELECT * FROM generation_run WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert run["outcome"] in {"infeasible", "unresolved"}
    assert run["is_draft"] == 0
    revision = db.execute(
        "SELECT COUNT(*) AS count FROM draft_revision WHERE run_id = ?", (run_id,)
    ).fetchone()["count"]
    assert revision == 0

    loaded = generations.load_register(db, run_id)
    assert loaded.status is draft.status
    assert loaded.is_allocation_complete is False
    assert len(loaded.exceptions) == len(draft.exceptions) > 0


def test_a_failed_attempt_still_records_its_readiness_snapshot(db):
    prepared, draft = generated(db, cycle_spec())
    run_id = generations.save_run(db, prepared, draft)
    readiness = generations.load_readiness(db, run_id)
    assert {item["key"] for item in readiness} == {
        item.key for item in prepared.readiness.items
    }
    assert all("satisfied" in item for item in readiness)


def test_the_missing_content_that_was_true_at_the_time_is_recorded(tmp_path):
    connection = open_database(tmp_path / "bare.db")
    seed_office_masters(connection)
    masters.upsert_dtc(
        connection, dtc_id="SCD-1", name="Only DTC", dtc_class="DEPT", kva=100.0
    )
    prepared, draft = generated(connection, cycle_spec())
    run_id = generations.save_run(connection, prepared, draft)

    summary = next(
        run for run in generations.list_runs(connection) if run.run_id == run_id
    )
    assert "activity catalogue" in summary.missing_content
    assert "official Karnataka General Holiday baseline for 2026" in (
        summary.missing_content
    )
    connection.close()


def test_history_separates_completed_drafts_from_attempts_that_produced_none(db):
    prepared, draft = generated(db, cycle_spec())
    generations.save_run(db, prepared, draft)

    tight = GenerationSpec(
        start=date(2026, 11, 1),
        end=date(2026, 11, 30),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=1,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=1,
        cycle_visits_per_asset=9,
    )
    prepared2, draft2 = generated(db, tight)
    generations.save_run(db, prepared2, draft2)

    grouped = generations.runs_by_outcome(db)
    assert len(grouped["allocation_complete"]) == 1
    assert len(grouped["infeasible"]) + len(grouped["unresolved"]) == 1


def test_an_unknown_run_is_refused_with_a_stable_constraint(db):
    with pytest.raises(generations.GenerationStorageError) as error:
        generations.load_register(db, "not-a-run")
    assert error.value.constraint == "generation_run_unknown"


def test_nothing_is_written_when_the_register_cannot_be_stored(db):
    prepared, draft = generated(db, cycle_spec())
    broken = type(draft)(
        **{
            **{
                name: getattr(draft, name)
                for name in (
                    "status",
                    "mode",
                    "start",
                    "end",
                    "seed",
                    "generator_version",
                    "allocator_version",
                    "months",
                    "visits",
                    "exceptions",
                    "warnings",
                    "snapshot",
                    "missing_content",
                )
            },
            # Two rows sharing a date violates the primary key, so the write fails
            # part way through and the whole attempt must disappear.
            "rows": (draft.rows[0], draft.rows[0]),
        }
    )
    with pytest.raises(sqlite3.IntegrityError):
        generations.save_run(db, prepared, broken)
    assert db.execute("SELECT COUNT(*) AS c FROM generation_run").fetchone()["c"] == 0
    assert (
        db.execute("SELECT COUNT(*) AS c FROM generation_visit").fetchone()["c"] == 0
    )


# --- reproducibility -----------------------------------------------------


def test_the_stored_inputs_generate_the_same_visits_again(db):
    prepared, draft = generated(db, cycle_spec())
    run_id = generations.save_run(db, prepared, draft)
    again = generations.regenerate(db, run_id)
    assert [
        (visit.visit.asset_id, visit.visit.visit_date, visit.provenance)
        for visit in again.visits
    ] == [
        (visit.visit.asset_id, visit.visit.visit_date, visit.provenance)
        for visit in draft.visits
    ]


def test_a_later_master_change_does_not_alter_the_stored_draft(db):
    prepared, draft = generated(db, cycle_spec())
    run_id = generations.save_run(db, prepared, draft)
    before = [
        (visit.name_en, visit.visit.visit_date) for visit in draft.visits
    ]

    masters.upsert_dtc(
        db, dtc_id="SCD-1", name="Renamed DTC", dtc_class="DEPT", kva=100.0
    )
    masters.upsert_dtc(
        db, dtc_id="SCD-500", name="Brand new DTC", dtc_class="DEPT", kva=90.0
    )

    loaded = generations.load_register(db, run_id)
    assert [(visit.name_en, visit.visit.visit_date) for visit in loaded.visits] == before
    assert "SCD-500" not in {visit.visit.asset_id for visit in loaded.visits}


def test_restoring_from_the_snapshot_is_not_affected_by_new_masters(db):
    prepared, draft = generated(db, cycle_spec())
    run_id = generations.save_run(db, prepared, draft)
    before = [
        (visit.visit.asset_id, visit.visit.visit_date) for visit in draft.visits
    ]

    masters.upsert_dtc(
        db, dtc_id="SCD-500", name="Brand new DTC", dtc_class="DEPT", kva=90.0
    )

    again = generations.regenerate(db, run_id)
    assert [(visit.visit.asset_id, visit.visit.visit_date) for visit in again.visits] == before
