"""Register revisions, date review and date locks.

Whatever else is true about a generated register, one thing must be: reading it,
correcting it and protecting a date must never rewrite what was generated. These tests
pin that, and they pin the two states apart — a date can be locked and never read, or
read and left unlocked, and the register must not confuse the two.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application.draft_generation import (
    DayEntry,
    DayEntryKind,
    DraftMode,
    DraftStatus,
    generate_draft,
)
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.application.revisions import (
    C_DATE_NOT_IN_REVISION,
    C_DATE_NOT_LOCKED,
    C_LOCKED_CONTENT_CHANGED,
    C_REGISTER_UNKNOWN,
    C_REVISION_NOT_CURRENT,
    C_STALE_REVIEW,
    DateReviewState,
    MonthReviewState,
    RevisionError,
    clear_review,
    fingerprints,
    lock_date,
    mark_reviewed,
    register_of,
    register_summary,
    registers,
    review_state,
    revision_summary,
    stored_row_content,
    unlock_date,
)
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters, revision_state
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "review.db")
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
    for holiday in (date(2026, 11, 10),):
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-11-10",
            holiday_date=holiday,
            name="Recorded holiday",
            source="karnataka",
        )
    yield connection
    connection.close()


def spec(*, seed: int = 7) -> GenerationSpec:
    start = date(2026, 11, 1)
    return GenerationSpec(
        start=start,
        end=date(2026, 11, 30),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=seed,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=1,
        cycle_visits_per_asset=1,
    )


def stored(db: sqlite3.Connection, *, seed: int = 7) -> str:
    prepared = prepare_generation(db, spec(seed=seed))
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    return generations.save_run(db, prepared, draft)


def reviewed_dates(db: sqlite3.Connection, run_id: str) -> set[str]:
    return {
        item["date"]
        for item in review_state(db, run_id)["dates"]
        if item["state"] == DateReviewState.REVIEWED.value
    }


# --- what a register is --------------------------------------------------


def test_a_generated_draft_starts_a_register_and_becomes_its_current_revision(db):
    run_id = stored(db)

    register_id = register_of(db, run_id)
    summary = registers(db)[0]
    assert summary["register_id"] == register_id
    assert summary["current_run_id"] == run_id
    assert [item["run_id"] for item in summary["revisions"]] == [run_id]
    assert summary["revisions"][0]["revision_number"] == 1
    assert summary["revisions"][0]["is_current"] is True


def test_a_fresh_revision_reviews_nothing_on_the_operators_behalf(db):
    run_id = stored(db)

    state = review_state(db, run_id)
    assert state["total_dates"] == 30
    assert state["reviewed_dates"] == 0
    assert reviewed_dates(db, run_id) == set()
    assert state["months"] == {"2026-11": MonthReviewState.PENDING_REVIEW.value}


def test_every_calendar_date_carries_a_review_state_including_empty_ones(db):
    run_id = stored(db)

    days = {item["date"]: item for item in review_state(db, run_id)["dates"]}
    # A Sunday and a holiday are on the register even though nothing is visited.
    assert days["2026-11-01"]["state"] == DateReviewState.PENDING_REVIEW.value
    assert days["2026-11-10"]["state"] == DateReviewState.PENDING_REVIEW.value
    assert {item["locked"] for item in days.values()} == {False}


# --- review --------------------------------------------------------------


def test_reviewing_a_date_records_when_and_against_which_content(db):
    run_id = stored(db)
    stamp = datetime(2026, 11, 30, 10, 15)

    answer = mark_reviewed(db, run_id, date(2026, 11, 3), note="checked", now=stamp)

    assert answer["state"] == DateReviewState.REVIEWED.value
    assert answer["reviewed_at"] == stamp.isoformat()
    row = revision_state.reviews(db, run_id)["2026-11-03"]
    assert row.note == "checked"
    assert row.content_fingerprint == fingerprints(db, run_id)["2026-11-03"]


def test_a_month_is_reviewed_only_when_every_date_is_reviewed(db):
    run_id = stored(db)
    days = [item["date"] for item in review_state(db, run_id)["dates"]]

    for label in days[:-1]:
        mark_reviewed(db, run_id, date.fromisoformat(label))

    state = review_state(db, run_id)
    assert state["months"]["2026-11"] == MonthReviewState.PENDING_REVIEW.value
    assert state["reviewed_dates"] == len(days) - 1

    mark_reviewed(db, run_id, date.fromisoformat(days[-1]))
    state = review_state(db, run_id)
    assert state["months"]["2026-11"] == MonthReviewState.REVIEWED.value
    assert state["reviewed_dates"] == len(days)


def test_a_review_can_be_withdrawn_and_the_date_stays_on_the_register(db):
    run_id = stored(db)
    mark_reviewed(db, run_id, date(2026, 11, 5))

    answer = clear_review(db, run_id, date(2026, 11, 5))

    assert answer["state"] == DateReviewState.PENDING_REVIEW.value
    assert "2026-11-05" in stored_row_content(db, run_id)
    assert revision_state.reviews(db, run_id)["2026-11-05"].state == "pending_review"


def test_reviewing_a_date_the_revision_does_not_contain_is_refused(db):
    run_id = stored(db)
    with pytest.raises(RevisionError) as error:
        mark_reviewed(db, run_id, date(2026, 12, 25))
    assert error.value.constraint == C_DATE_NOT_IN_REVISION


def test_a_review_of_content_the_operator_no_longer_sees_is_refused(db):
    run_id = stored(db)
    with pytest.raises(RevisionError) as error:
        mark_reviewed(
            db, run_id, date(2026, 11, 3), expected_fingerprint="stale-fingerprint"
        )
    assert error.value.constraint == C_STALE_REVIEW


# --- locks ---------------------------------------------------------------


def test_locking_a_date_does_not_review_it(db):
    run_id = stored(db)

    answer = lock_date(db, run_id, date(2026, 11, 4), note="office closed")

    assert answer["locked"] is True
    state = review_state(db, run_id)
    assert state["locked_dates"] == 1
    assert state["reviewed_dates"] == 0
    day = {item["date"]: item for item in state["dates"]}["2026-11-04"]
    assert day["locked"] is True
    assert day["state"] == DateReviewState.PENDING_REVIEW.value
    assert day["note"] is None


def test_reviewing_a_date_does_not_lock_it(db):
    run_id = stored(db)
    mark_reviewed(db, run_id, date(2026, 11, 6))

    state = review_state(db, run_id)
    assert state["locked_dates"] == 0
    assert state["reviewed_dates"] == 1


def test_unlocking_a_date_that_is_not_locked_is_refused(db):
    run_id = stored(db)
    with pytest.raises(RevisionError) as error:
        unlock_date(db, run_id, date(2026, 11, 7))
    assert error.value.constraint == C_DATE_NOT_LOCKED


def test_a_lock_keeps_the_content_it_protected(db):
    run_id = stored(db)
    before = stored_row_content(db, run_id)["2026-11-04"]

    lock_date(db, run_id, date(2026, 11, 4))

    assert stored_row_content(db, run_id)["2026-11-04"] == before
    assert revision_state.locks(db, run_id)["2026-11-04"].locked_at


def test_unlocking_leaves_the_date_and_its_content_alone(db):
    run_id = stored(db)
    lock_date(db, run_id, date(2026, 11, 4))
    before = stored_row_content(db, run_id)["2026-11-04"]

    answer = unlock_date(db, run_id, date(2026, 11, 4))

    assert answer["locked"] is False
    assert revision_state.locks(db, run_id) == {}
    assert stored_row_content(db, run_id)["2026-11-04"] == before


# --- revisions are immutable --------------------------------------------


def _edit(db: sqlite3.Connection, base: str, *, add_entry_on: date | None = None) -> str:
    """Store a corrected revision of an existing one, the way an edit will."""

    prepared = prepare_generation(db, spec())
    draft = generate_draft(prepared.request)
    if add_entry_on is not None:
        rows = tuple(
            dataclasses.replace(
                row,
                entries=tuple(row.entries)
                + (
                    DayEntry(
                        entry_id="E-manual-1",
                        day=add_entry_on,
                        kind=DayEntryKind.ACTIVITY,
                        title_en="Meter reading at the office",
                        title_kn="ಕಚೇರಿಯಲ್ಲಿ ಮೀಟರ್ ಓದುವಿಕೆ",
                    ),
                ),
            )
            if row.day == add_entry_on
            else row
            for row in draft.rows
        )
        draft = dataclasses.replace(draft, rows=rows)
    return generations.save_run(
        db,
        prepared,
        draft,
        register_id=register_of(db, base),
        parent_run_id=base,
        kind="edit",
        change_reason="test correction",
    )


def test_a_correction_becomes_a_new_revision_and_keeps_the_old_one_readable(db):
    first = stored(db)
    before = stored_row_content(db, first)

    second = _edit(db, first)

    assert stored_row_content(db, first) == before
    assert register_of(db, first) == register_of(db, second)
    summary = registers(db)[0]
    assert [item["revision_number"] for item in summary["revisions"]] == [1, 2]
    assert summary["current_run_id"] == second
    assert revision_summary(db, second)["parent_run_id"] == first
    assert revision_summary(db, second)["kind"] == "edit"
    assert revision_summary(db, second)["change_reason"] == "test correction"


def test_an_unchanged_row_keeps_its_review_across_a_revision_and_says_where(db):
    first = stored(db)
    mark_reviewed(db, first, date(2026, 11, 3), note="read it")

    second = _edit(db, first)

    row = revision_state.reviews(db, second)["2026-11-03"]
    assert row.state == "reviewed"
    assert row.note == "read it"
    assert row.carried_from_run_id == first
    assert row.reviewed_at == revision_state.reviews(db, first)["2026-11-03"].reviewed_at


def test_a_changed_row_does_not_inherit_the_review_its_correction_replaced(db):
    first = stored(db)
    mark_reviewed(db, first, date(2026, 11, 3), note="read it")

    second = _edit(db, first, add_entry_on=date(2026, 11, 3))

    assert stored_row_content(db, first)["2026-11-03"] != stored_row_content(db, second)[
        "2026-11-03"
    ]
    row = revision_state.reviews(db, second)["2026-11-03"]
    assert row.state == "pending_review"
    assert row.carried_from_run_id is None
    assert "2026-11-03" not in reviewed_dates(db, second)
    # The review of the revision that was read is still there: history is not erased.
    assert revision_state.reviews(db, first)["2026-11-03"].state == "reviewed"


def test_a_lock_survives_a_revision_and_a_changed_locked_date_is_refused(db):
    first = stored(db)
    lock_date(db, first, date(2026, 11, 4))

    second = _edit(db, first)
    assert set(revision_state.locks(db, second)) == {"2026-11-04"}

    with pytest.raises(RevisionError) as error:
        _edit(db, first, add_entry_on=date(2026, 11, 4))
    assert error.value.constraint == C_LOCKED_CONTENT_CHANGED


def test_a_superseded_revision_cannot_be_reviewed_or_locked(db):
    first = stored(db)
    second = _edit(db, first)

    for call in (
        lambda: mark_reviewed(db, first, date(2026, 11, 3)),
        lambda: lock_date(db, first, date(2026, 11, 3)),
    ):
        with pytest.raises(RevisionError) as error:
            call()
        assert error.value.constraint == C_REVISION_NOT_CURRENT

    assert revision_summary(db, second)["is_current"] is True
    assert revision_summary(db, first)["is_current"] is False


def test_an_unknown_register_is_reported_as_unknown_and_not_as_empty(db):
    with pytest.raises(RevisionError) as error:
        register_summary(db, "R-does-not-exist")
    assert error.value.constraint == C_REGISTER_UNKNOWN


# --- the schema upgrade --------------------------------------------------


def test_a_database_left_at_schema_two_gains_registers_and_scoped_revisions(tmp_path):
    from dailyworkreg.infrastructure.sqlite.schema import (
        MIGRATIONS,
        SCHEMA_VERSION,
        applied_versions,
        apply_migrations,
    )

    connection = sqlite3.connect(tmp_path / "legacy.db")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in MIGRATIONS:
        if migration.version <= 2:
            for statement in migration.statements:
                connection.execute(statement)
        if migration.version <= 2:
            connection.execute(
                "INSERT OR IGNORE INTO schema_migration (version, name, applied_at) "
                "VALUES (?, ?, '2026-01-01T00:00:00')",
                (migration.version, migration.name),
            )
    for ordinal in (1, 3):
        connection.execute(
            """
            INSERT INTO generation_run (run_id, created_at, mode, start_date, end_date,
                months_json, seed, generator_version, allocator_version, outcome,
                is_draft, master_version, request_schema_version,
                request_snapshot_json, readiness_json, missing_content_json)
            VALUES (?, '2026-01-01T00:00:00', 'coverage_cycle', '2026-11-01',
                '2026-11-30', '["2026-11"]', 7, 'g1', 'a1', 'allocation_complete',
                1, 1, 1, '{}', '[]', '[]')
            """,
            (f"run-{ordinal}",),
        )
        connection.execute(
            "INSERT INTO draft_revision (run_id, revision_number, created_at) "
            "VALUES (?, ?, '2026-01-01T00:00:00')",
            (f"run-{ordinal}", ordinal),
        )
    connection.commit()

    apply_migrations(connection)

    # Every migration in the file has been applied, including the ones that arrived
    # after this test was written: a database stuck at schema two must reach today's
    # schema, not schema three.
    assert applied_versions(connection) == tuple(
        migration.version for migration in MIGRATIONS
    )
    assert SCHEMA_VERSION == max(migration.version for migration in MIGRATIONS)
    registers_found = connection.execute("SELECT * FROM register").fetchall()
    assert len(registers_found) == 1
    rows = connection.execute(
        "SELECT run_id, register_id, revision_number FROM draft_revision "
        "ORDER BY revision_number"
    ).fetchall()
    assert [(row["run_id"], row["revision_number"]) for row in rows] == [
        ("run-1", 1),
        ("run-3", 2),
    ]
    assert {row["register_id"] for row in rows} == {registers_found[0]["register_id"]}
    assert registers_found[0]["current_run_id"] == "run-3"
    connection.close()
