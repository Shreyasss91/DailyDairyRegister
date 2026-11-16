"""Monthly finalisation: the record, its immutability and its lineage.

A register is worked on for weeks. Finalising a month is the act that closes it, and the
record that results must stay exactly what was approved: not edited, not withdrawn, not
re-opened, and not replaced by a neighbouring month's work. These tests pin that record
and the database-level refusals that protect it, then the gate that decides whether a
month may be closed at all, then the correction that supersedes it without touching it.
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
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters, revision_state
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.schema import (
    MIGRATIONS,
    SCHEMA_VERSION,
    applied_versions,
)
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """A database with every master the office needs, so a month can be closed.

    Synthetic and complete on purpose: the real 113-SCD roster, the Karnataka holiday
    baseline and the approved activity catalogue are not in this repository, and a test
    that needed them would either fabricate them silently or never run.
    """

    connection = open_database(tmp_path / "finalisation.db")
    seed_approved_identities(connection)
    for index in range(1, 7):
        masters.upsert_dtc(
            connection,
            dtc_id=f"SCD-{index}",
            name=f"Test DTC {index}",
            dtc_class="DEPT",
            kva=100.0 + index,
            canonical_feeder_id=f"F-0{(index % 2) + 1}",
        )
    for index, day in enumerate((date(2026, 11, 10),), start=1):
        cal.upsert_holiday(
            connection,
            holiday_id=f"H-{day.isoformat()}",
            holiday_date=day,
            name=f"Recorded holiday {index}",
            source="karnataka",
        )
    for index in range(1, 3):
        masters.upsert_activity(
            connection,
            activity_id=f"A-{index:03d}",
            category="Inspection",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
        )
    yield connection
    connection.close()


def spec(*, start: date = date(2026, 11, 1), end: date = date(2026, 11, 30), seed: int = 7):
    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=seed,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=1,
        cycle_visits_per_asset=1,
    )


def stored(db: sqlite3.Connection, **kwargs) -> str:
    prepared = prepare_generation(db, spec(**kwargs))
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    return generations.save_run(db, prepared, draft)


def a_finalisation(db: sqlite3.Connection, run_id: str, *, month: str = "2026-11"):
    """Write a finalisation row directly, so the triggers can be tested alone."""

    register_id = revision_state.register_for_run(db, run_id) or ""
    revision = revision_state.revision(db, run_id)
    return revision_state.FinalisationRow(
        finalisation_id="F-1",
        register_id=register_id,
        month=month,
        run_id=run_id,
        revision_number=0 if revision is None else revision.revision_number,
        sequence=1,
        parent_finalisation_id=None,
        correction_of_run_id=None,
        finalised_at="2026-11-16T10:00:00",
        fingerprint="f" * 64,
        content_json="{}",
        acknowledged_json="[]",
        readiness_json="[]",
        master_version=1,
        note=None,
    )


# --- schema 4 --------------------------------------------------------------


def test_the_schema_carries_every_migration(db):
    assert SCHEMA_VERSION == 4
    assert applied_versions(db) == tuple(item.version for item in MIGRATIONS)
    tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"month_finalisation", "month_state"} <= tables
    triggers = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    # Ten content triggers plus two on the record itself.
    assert len([name for name in triggers if name.startswith("trg_finalised_")]) == 10
    assert len([name for name in triggers if name.startswith("trg_finalisation_")]) == 2


def test_a_finalised_revision_cannot_be_rewritten_in_the_database(db):
    run_id = stored(db)
    # Every table the refusal protects has a row, so a passing test cannot pass
    # because a statement happened to match nothing.
    revision_state.lock(db, run_id, "2026-11-03", locked_at="2026-11-16T09:00:00", note=None)
    revision_state.insert_entries(
        db,
        run_id,
        (
            revision_state.EntryRow(
                run_id=run_id,
                entry_id="E-1",
                row_date="2026-11-03",
                kind="activity",
                title_en="Entered by the office",
                title_kn=None,
                provenance="manual",
            ),
        ),
    )
    db.commit()
    revision_state.insert_finalisation(db, a_finalisation(db, run_id))
    db.commit()

    for statement in (
        "UPDATE generation_row SET reasons_en_json = '[]' WHERE run_id = ?",
        "DELETE FROM generation_row WHERE run_id = ?",
        "UPDATE generation_visit SET provenance = 'allocated' WHERE run_id = ?",
        "DELETE FROM date_review WHERE run_id = ?",
        "UPDATE date_lock SET note = 'x' WHERE run_id = ?",
        "DELETE FROM generation_entry WHERE run_id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError) as error:
            db.execute(statement, (run_id,))
        assert "finalised_revision_is_immutable" in str(error.value)
        db.rollback()


def test_the_finalised_record_itself_cannot_be_rewritten(db):
    run_id = stored(db)
    revision_state.insert_finalisation(db, a_finalisation(db, run_id))
    db.commit()

    for statement in (
        "UPDATE month_finalisation SET fingerprint = 'x'",
        "DELETE FROM month_finalisation",
    ):
        with pytest.raises(sqlite3.IntegrityError) as error:
            db.execute(statement)
        assert "finalised_record_is_immutable" in str(error.value)
        db.rollback()


def test_an_unfinalised_revision_stays_writable(db):
    run_id = stored(db)
    db.execute("UPDATE date_lock SET note = 'x' WHERE run_id = ?", (run_id,))
    db.rollback()
    # Nothing raised: a draft is still a draft.
    assert revision_state.finalisation_for_run(db, run_id) is None


def test_the_rows_round_trip(db):
    run_id = stored(db)
    register_id = revision_state.register_for_run(db, run_id) or ""
    row = a_finalisation(db, run_id)
    revision_state.insert_finalisation(db, row)
    revision_state.set_latest_finalisation(
        db, register_id, "2026-11", row.finalisation_id
    )
    db.commit()

    assert revision_state.finalisation(db, "F-1") == row
    assert revision_state.finalisation_for_run(db, run_id) == row
    assert revision_state.finalisations(db, register_id, "2026-11") == (row,)
    assert revision_state.next_finalisation_sequence(db, register_id, "2026-11") == 2
    state = revision_state.month_state(db, register_id, "2026-11")
    assert state is not None
    assert state.latest_finalisation_id == "F-1"
    assert state.open_correction_run_id is None
    assert revision_state.finalised_run_ids(db, register_id) == (run_id,)

    revision_state.set_open_correction(db, register_id, "2026-11", "correction-run")
    state = revision_state.month_state(db, register_id, "2026-11")
    assert state is not None
    assert state.open_correction_run_id == "correction-run"
    # Opening a correction branch does not move the latest finalised pointer.
    assert state.latest_finalisation_id == "F-1"

    revision_state.clear_open_correction(db, register_id, "2026-11")
    state = revision_state.month_state(db, register_id, "2026-11")
    assert state is not None
    assert state.open_correction_run_id is None
    assert state.latest_finalisation_id == "F-1"


def test_a_revision_is_finalised_at_most_once(db):
    first = stored(db)
    row = a_finalisation(db, first)
    revision_state.insert_finalisation(db, row)
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        revision_state.insert_finalisation(db, a_finalisation(db, first))
    db.rollback()

    assert revision_state.next_finalisation_sequence(db, row.register_id, "2026-11") == 2
    assert revision_state.finalisations(db, row.register_id, "2026-11") == (row,)
