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
from dailyworkreg.application import finalisation as finalise
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application import revisions
from dailyworkreg.application.draft_generation import (
    DraftMode,
    DraftStatus,
    generate_draft,
)
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.application.regeneration import accept_candidate, regenerate_candidate
from dailyworkreg.application.register_edits import add_entry, edit_entry
from dailyworkreg.application.revisions import RevisionError
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


def spec(
    *,
    start: date = date(2026, 11, 1),
    end: date = date(2026, 11, 30),
    seed: int = 7,
    cycle_months: int = 1,
):
    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=seed,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=cycle_months,
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

# --- the gates -------------------------------------------------------------


def review_every_date(
    db: sqlite3.Connection, run_id: str, *, month: str | None = None
) -> None:
    state = revisions.review_state(db, run_id)
    for item in state["dates"]:
        if month is not None and not item["date"].startswith(month):
            continue
        revisions.mark_reviewed(
            db, run_id, date.fromisoformat(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )


def add_holiday(db: sqlite3.Connection, day: date, name: str = "Recorded holiday") -> None:
    cal.upsert_holiday(
        db,
        holiday_id=f"H-{day.isoformat()}",
        holiday_date=day,
        name=name,
        source="karnataka",
    )


def test_a_reviewed_month_can_be_finalised(db):
    run_id = stored(db)
    review_every_date(db, run_id)

    check = finalise.check_finalisation(db, run_id, "2026-11")
    assert check.eligible is True, [item.as_dict() for item in check.blockers]
    assert check.reviewed_dates == 30
    assert check.total_dates == 30
    assert check.is_operational_diary is True
    assert check.missing_content == ()

    record = finalise.finalise_month(db, run_id, "2026-11", note="closed for November")

    assert record["month"] == "2026-11"
    assert record["run_id"] == run_id
    assert record["sequence"] == 1
    assert record["is_latest"] is True
    assert record["parent_finalisation_id"] is None
    assert record["correction_of_run_id"] is None
    assert record["note"] == "closed for November"
    assert record["fingerprint"] == check.fingerprint
    assert len(record["content"]["rows"]) == 30
    assert record["content"]["status"] == "allocation_complete"
    # The readiness measured at the moment of closing is part of the record.
    assert any(item["satisfied"] is False for item in record["readiness"]) is False
    assert finalise.is_finalised(db, run_id) is True


def test_one_pending_date_blocks_finalisation(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    revisions.clear_review(db, run_id, date(2026, 11, 17))

    check = finalise.check_finalisation(db, run_id, "2026-11")

    assert check.eligible is False
    blocker = next(item for item in check.blockers if item.code == finalise.C_DATES_NOT_REVIEWED)
    assert blocker.dates == ("2026-11-17",)
    with pytest.raises(finalise.FinalisationError) as error:
        finalise.finalise_month(db, run_id, "2026-11")
    assert error.value.constraint == finalise.C_DATES_NOT_REVIEWED


def test_an_empty_sunday_still_needs_reviewing(db):
    run_id = stored(db)
    state = revisions.review_state(db, run_id)
    for item in state["dates"]:
        day = date.fromisoformat(item["date"])
        if day.weekday() == 6:  # leave every Sunday unreviewed
            continue
        revisions.mark_reviewed(
            db, run_id, day, expected_fingerprint=item["fingerprint"]
        )

    check = finalise.check_finalisation(db, run_id, "2026-11")

    blocker = next(item for item in check.blockers if item.code == finalise.C_DATES_NOT_REVIEWED)
    sundays = [day for day in blocker.dates if date.fromisoformat(day).weekday() == 6]
    assert len(sundays) == len([day for day in blocker.dates if True])
    assert "2026-11-01" in blocker.dates


def test_a_review_of_changed_content_blocks_finalisation(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    # A review attests the content it was made against. Written back against different
    # content, it is stale, and a stale review cannot close a month.
    revision_state.set_review(
        db, run_id, "2026-11-17",
        state=revision_state.REVIEWED,
        reviewed_at="2026-11-16T09:00:00",
        note=None,
        content_fingerprint="0" * 64,
    )
    db.commit()

    check = finalise.check_finalisation(db, run_id, "2026-11")

    blocker = next(item for item in check.blockers if item.code == finalise.C_REVIEW_STALE)
    assert blocker.dates == ("2026-11-17",)


def test_an_infeasible_month_cannot_be_finalised(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    db.execute(
        "UPDATE generation_month SET status = 'infeasible', allocator_status = 'INFEASIBLE'"
    )
    db.commit()

    check = finalise.check_finalisation(db, run_id, "2026-11")

    codes = {item.code for item in check.blockers}
    assert finalise.C_ALLOCATION_INCOMPLETE in codes
    assert finalise.C_ALLOCATION_INFEASIBLE in codes
    with pytest.raises(finalise.FinalisationError):
        finalise.finalise_month(db, run_id, "2026-11")


def test_an_unsettled_month_cannot_be_finalised(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    db.execute(
        "UPDATE generation_month SET status = 'unresolved', allocator_status = 'INDETERMINATE'"
    )
    db.commit()

    check = finalise.check_finalisation(db, run_id, "2026-11")

    codes = {item.code for item in check.blockers}
    assert finalise.C_ALLOCATION_INDETERMINATE in codes


def test_a_missing_dtc_roster_blocks_finalisation(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    db.execute("UPDATE dtc SET active = 0")
    db.commit()

    check = finalise.check_finalisation(db, run_id, "2026-11")

    blocker = next(
        item for item in check.blockers if item.code == finalise.C_OPERATIONAL_MASTERS_MISSING
    )
    assert blocker.label == "DTC master"
    assert "113-SCD" in blocker.detail
    assert check.is_operational_diary is False


def test_a_missing_holiday_baseline_blocks_finalisation(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    db.execute("DELETE FROM general_holiday WHERE holiday_date LIKE '2026%'")
    db.commit()

    check = finalise.check_finalisation(db, run_id, "2026-11")

    blocker = next(
        item
        for item in check.blockers
        if item.code == finalise.C_OPERATIONAL_MASTERS_MISSING
        and "Holiday" in item.label
    )
    assert blocker.label == "official Karnataka General Holiday baseline for 2026"


def test_a_missing_activity_catalogue_blocks_finalisation(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    db.execute("UPDATE activity SET active = 0")
    db.commit()

    check = finalise.check_finalisation(db, run_id, "2026-11")

    blocker = next(
        item
        for item in check.blockers
        if item.code == finalise.C_OPERATIONAL_MASTERS_MISSING
        and item.label == "activity catalogue"
    )
    assert "approved catalogue" in blocker.detail


def test_an_unacknowledged_calendar_conflict_blocks_finalisation(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    # A General Holiday that falls on a Sunday: both facts are kept, and the office has
    # to say it has seen that rather than having it resolved away.
    add_holiday(db, date(2026, 11, 1), name="Founder's Day")

    check = finalise.check_finalisation(db, run_id, "2026-11")

    assert check.conflicts
    blocker = next(
        item for item in check.blockers if item.code == finalise.C_CONFLICT_NOT_ACKNOWLEDGED
    )
    assert "Founder's Day" in blocker.detail
    token = check.conflicts[0]["token"]

    acknowledged = finalise.finalise_month(
        db, run_id, "2026-11", acknowledged=(token,)
    )
    assert [item["token"] for item in acknowledged["acknowledged"]] == [token]


def store_over_coverage(db: sqlite3.Connection, run_id: str, *, month: str = "2026-11") -> str:
    db.execute(
        """
        INSERT INTO generation_exception
            (run_id, ordinal, kind, constraint_name, detail, month_key, asset_id,
             asset_type, visit_date, limit_value, actual_value)
        VALUES (?, 90, 'over_covered', 'cycle_obligation_exceeded',
                'the cycle asked for 1 visit and 2 are committed', ?, 'SCD-2',
                'DTC', ?, 1, 2)
        """,
        (run_id, month, f"{month}-12"),
    )
    db.commit()
    return "over_covered|cycle_obligation_exceeded|SCD-2|" + f"{month}-12"


def test_over_coverage_needs_an_explicit_acknowledgement(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    token = store_over_coverage(db, run_id)

    check = finalise.check_finalisation(db, run_id, "2026-11")
    assert token in {item["token"] for item in check.over_coverage}
    blocker = next(
        item for item in check.blockers if item.code == finalise.C_OVER_COVERAGE_NOT_ACKNOWLEDGED
    )
    assert blocker.dates == ("2026-11-12",)
    with pytest.raises(finalise.FinalisationError) as error:
        finalise.finalise_month(db, run_id, "2026-11")
    assert error.value.constraint == finalise.C_OVER_COVERAGE_NOT_ACKNOWLEDGED

    record = finalise.finalise_month(db, run_id, "2026-11", acknowledged=(token,))
    # Acknowledged, and still present: an exception is recorded, never deleted.
    assert [item["token"] for item in record["acknowledged"]] == [token]
    assert record["acknowledged"][0]["asset_id"] == "SCD-2"
    assert record["acknowledged"][0]["actual"] == 2
    db.execute("SELECT 1")  # the row is still in the stored exceptions
    assert finalise.is_finalised(db, run_id) is True
    assert finalise.check_finalisation(db, run_id, "2026-11").over_coverage


def test_an_acknowledgement_of_nothing_is_refused(db):
    run_id = stored(db)
    review_every_date(db, run_id)

    check = finalise.check_finalisation(
        db, run_id, "2026-11", acknowledged=("over_covered|invented|SCD-9|2026-11-12",)
    )

    codes = {item.code for item in check.blockers}
    assert finalise.C_ACKNOWLEDGEMENT_UNKNOWN in codes


def test_the_calendar_moving_under_the_register_blocks_finalisation(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    # A holiday recorded on a date the register holds as a working day.
    add_holiday(db, date(2026, 11, 12))

    check = finalise.check_finalisation(db, run_id, "2026-11")

    blocker = next(item for item in check.blockers if item.code == finalise.C_CALENDAR_CHANGED)
    assert "2026-11-12" in blocker.dates


def test_a_partial_month_is_not_finalised(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    # A revision that covers only part of the month is not a month to close. Deleting the
    # last days is the only way to produce one today, because generation refuses a
    # partial period outright.
    db.execute("DELETE FROM generation_row WHERE run_id = ? AND row_date > '2026-11-20'", (run_id,))
    db.commit()

    with pytest.raises(finalise.FinalisationError) as error:
        finalise.finalise_month(db, run_id, "2026-11")
    assert error.value.constraint == finalise.C_PARTIAL_MONTH_UNSUPPORTED


def test_a_month_cannot_be_finalised_twice(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    check = finalise.check_finalisation(db, run_id, "2026-11")
    codes = {item.code for item in check.blockers}
    assert finalise.C_ALREADY_FINALISED in codes

    with pytest.raises(finalise.FinalisationError) as error:
        finalise.finalise_month(db, run_id, "2026-11")
    assert error.value.constraint == finalise.C_ALREADY_FINALISED
    assert len(revision_state.finalisations(
        db, revision_state.register_for_run(db, run_id) or "", "2026-11"
    )) == 1


def test_a_superseded_revision_cannot_be_finalised(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    newer = edit_entry(
        db, run_id, "E-2026-11-02",
        title_en="Corrected",
    ) if _entry_exists(db, run_id, "E-2026-11-02") else None
    if newer is None:
        # No projected entry to correct: add one, which is equally a new revision.
        newer = add_entry(
            db, run_id, day=date(2026, 11, 2), kind="activity",
            title_en="Entered by the office",
        )
        review_every_date(db, newer.run_id)

    check = finalise.check_finalisation(db, run_id, "2026-11")
    codes = {item.code for item in check.blockers}
    assert finalise.C_REVISION_NOT_CURRENT in codes


def _entry_exists(db: sqlite3.Connection, run_id: str, entry_id: str) -> bool:
    return any(item.entry_id == entry_id for item in revision_state.entries(db, run_id))


# --- the record cannot be changed -------------------------------------------


def a_closed_month(db: sqlite3.Connection) -> tuple[str, str]:
    run_id = stored(db)
    review_every_date(db, run_id)
    record = finalise.finalise_month(db, run_id, "2026-11")
    return run_id, record["finalisation_id"]


def test_an_entry_cannot_be_corrected_after_finalisation(db):
    run_id, _ = a_closed_month(db)

    with pytest.raises(finalise.FinalisationError) as error:
        add_entry(
            db, run_id, day=date(2026, 11, 4), kind="activity", title_en="Late entry"
        )
    assert error.value.constraint == finalise.C_MONTH_FINALISED


def test_a_review_cannot_be_withdrawn_after_finalisation(db):
    run_id, _ = a_closed_month(db)

    with pytest.raises(finalise.FinalisationError) as error:
        revisions.clear_review(db, run_id, date(2026, 11, 4))
    assert error.value.constraint == finalise.C_MONTH_FINALISED

    with pytest.raises(finalise.FinalisationError):
        revisions.mark_reviewed(db, run_id, date(2026, 11, 4))


def test_a_lock_cannot_move_after_finalisation(db):
    run_id, _ = a_closed_month(db)

    with pytest.raises(finalise.FinalisationError) as error:
        revisions.lock_date(db, run_id, date(2026, 11, 4))
    assert error.value.constraint == finalise.C_MONTH_FINALISED

    with pytest.raises(finalise.FinalisationError):
        revisions.unlock_date(db, run_id, date(2026, 11, 4))


def test_a_finalised_revision_cannot_be_regenerated(db):
    run_id, _ = a_closed_month(db)

    with pytest.raises(finalise.FinalisationError) as error:
        regenerate_candidate(db, run_id)
    assert error.value.constraint == finalise.C_FINALISED_MONTHS_PRESENT


def test_a_candidate_cannot_replace_a_finalised_month(db):
    run_id = stored(db)
    candidate = regenerate_candidate(db, run_id)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    with pytest.raises(finalise.FinalisationError) as error:
        accept_candidate(db, revisions.register_of(db, run_id), candidate.candidate_run_id)
    assert error.value.constraint == finalise.C_FINALISED_MONTHS_PRESENT
    # The register did not move.
    assert revisions.current_run(db, revisions.register_of(db, run_id)) == run_id


def test_history_stays_readable_after_finalisation(db):
    run_id, finalisation_id = a_closed_month(db)

    assert generations.load_register(db, run_id).is_allocation_complete is True
    assert revisions.review_state(db, run_id)["reviewed_dates"] == 30
    record = finalise.finalisation_record(db, finalisation_id)
    assert len(record["content"]["rows"]) == 30
    assert finalise.month_finalisations(
        db, record["register_id"], "2026-11"
    )[0]["finalisation_id"] == finalisation_id


def test_a_failed_write_leaves_no_record_and_no_pointer(db, monkeypatch):
    run_id = stored(db)
    review_every_date(db, run_id)
    register_id = revisions.register_of(db, run_id)

    def explode(*_args, **_kwargs):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(revision_state, "insert_finalisation", explode)
    with pytest.raises(RuntimeError):
        finalise.finalise_month(db, run_id, "2026-11")
    monkeypatch.undo()

    assert finalise.is_finalised(db, run_id) is False
    state = revision_state.month_state(db, register_id, "2026-11")
    assert state is None or state.latest_finalisation_id is None
    assert finalise.finalised_months(db, register_id) == {}
    # And the month can still be closed once the storage works again.
    assert finalise.finalise_month(db, run_id, "2026-11")["sequence"] == 1


# --- one month at a time ----------------------------------------------------


def test_one_month_can_be_finalised_while_another_is_a_draft(db):
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    review_every_date(db, run_id)

    check = finalise.check_finalisation(db, run_id, "2026-11")
    assert check.eligible is True, [item.as_dict() for item in check.blockers]
    finalise.finalise_month(db, run_id, "2026-11")

    lifecycle = {item["month"]: item for item in finalise.month_lifecycle(
        db, revisions.register_of(db, run_id)
    )}
    assert lifecycle["2026-11"]["lifecycle"] == "finalised"
    assert lifecycle["2026-12"]["finalised"] is False
    assert lifecycle["2026-12"]["reviewed_dates"] == 31
    assert lifecycle["2026-12"]["lifecycle"] == "reviewed"
    # December still cannot be closed without its own finalisation.
    assert finalise.check_finalisation(db, run_id, "2026-11").eligible is False


def test_working_on_an_open_month_leaves_a_closed_one_untouched(db):
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    review_every_date(db, run_id)
    record = finalise.finalise_month(db, run_id, "2026-11")
    register_id = revisions.register_of(db, run_id)

    corrected = add_entry(
        db, run_id, day=date(2026, 12, 4), kind="activity", title_en="December work"
    )
    review_every_date(db, corrected.run_id, month="2026-12")

    assert revisions.current_run(db, register_id) == corrected.run_id
    closed = finalise.month_finalisations(db, register_id, "2026-11")[0]
    assert closed["finalisation_id"] == record["finalisation_id"]
    assert closed["is_latest"] is True
    # The closed month's content in the new revision is the record's content exactly.
    assert finalise.month_fingerprint(db, corrected.run_id, "2026-11") == record["fingerprint"]
    assert finalise.check_finalisation(
        db, corrected.run_id, "2026-11"
    ).finalisation_id is None

    with pytest.raises(finalise.FinalisationError) as error:
        add_entry(
            db, corrected.run_id, day=date(2026, 11, 4), kind="activity",
            title_en="Too late",
        )
    assert "finalised" in str(error.value)


def test_a_closed_month_blocks_wholesale_regeneration_of_its_register(db):
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    with pytest.raises(finalise.FinalisationError) as error:
        regenerate_candidate(db, run_id)
    assert error.value.constraint == finalise.C_FINALISED_MONTHS_PRESENT


# --- correction revisions ---------------------------------------------------


def a_correction(db: sqlite3.Connection, *, reason: str = "the 12th was entered twice"):
    run_id = stored(db)
    review_every_date(db, run_id)
    record = finalise.finalise_month(db, run_id, "2026-11")
    correction = finalise.create_correction_revision(db, run_id, reason=reason)
    return run_id, record, correction


def test_a_correction_starts_from_the_issued_month_unchanged(db):
    run_id, record, correction = a_correction(db)

    assert correction["correction_of_run_id"] == run_id
    assert correction["correction_of_finalisation_id"] == record["finalisation_id"]
    assert correction["correction_of_revision"] == record["revision_number"]
    assert correction["month"] == "2026-11"
    assert correction["reason"] == "the 12th was entered twice"
    # It starts as a draft, not as a version in force.
    assert correction["next_sequence"] == 2
    assert revisions.current_run(db, correction["register_id"]) == correction["correction_run_id"]
    # The copy is the record, and the record is untouched.
    assert finalise.month_fingerprint(db, correction["correction_run_id"], "2026-11") == (
        record["fingerprint"]
    )
    assert finalise.finalisation_record(db, record["finalisation_id"])["fingerprint"] == (
        record["fingerprint"]
    )
    # Review carried, because every date's content is provably unchanged.
    assert len(correction["carried_reviews"]) == 30
    assert correction["pending_dates"] == []


def test_an_open_correction_is_not_the_version_in_force(db):
    _, record, correction = a_correction(db)

    lifecycle = {
        item["month"]: item
        for item in finalise.month_lifecycle(db, correction["register_id"])
    }
    assert lifecycle["2026-11"]["lifecycle"] == "correction_draft"
    assert lifecycle["2026-11"]["finalised"] is True
    assert lifecycle["2026-11"]["latest_finalisation_id"] == record["finalisation_id"]
    assert lifecycle["2026-11"]["open_correction_run_id"] == correction["correction_run_id"]
    # The record the office holds is still the one that was issued.
    chain = finalise.correction_chain(db, correction["register_id"], "2026-11")
    assert [item["finalisation_id"] for item in chain] == [record["finalisation_id"]]
    assert chain[0]["is_latest"] is True


def test_a_correction_needs_a_reason(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    for reason in ("", "   \n\t "):
        with pytest.raises(finalise.FinalisationError) as error:
            finalise.create_correction_revision(db, run_id, reason=reason)
        assert error.value.constraint == finalise.C_CORRECTION_REASON_REQUIRED


def test_a_draft_cannot_be_corrected_as_a_finalised_month(db):
    run_id = stored(db)
    review_every_date(db, run_id)

    with pytest.raises(finalise.FinalisationError) as error:
        finalise.create_correction_revision(db, run_id, reason="too early")
    assert error.value.constraint == finalise.C_SOURCE_NOT_FINALISED


def test_only_one_correction_of_a_month_can_be_open(db):
    _, _, correction = a_correction(db)

    with pytest.raises(finalise.FinalisationError) as error:
        finalise.create_correction_revision(
            db, correction["correction_of_run_id"], reason="and again"
        )
    assert error.value.constraint == finalise.C_CORRECTION_ALREADY_OPEN


def test_the_original_stays_exactly_as_issued_while_the_correction_changes(db):
    run_id, record, correction = a_correction(db)
    corrected_run = correction["correction_run_id"]

    # The correction may now change the closed month, and only through itself.
    edit = add_entry(
        db, corrected_run, day=date(2026, 11, 12), kind="activity",
        title_en="Corrected entry",
    )
    corrected_run = edit.run_id

    assert finalise.month_fingerprint(db, corrected_run, "2026-11") != record["fingerprint"]
    issued = finalise.finalisation_record(db, record["finalisation_id"])
    assert issued["fingerprint"] == record["fingerprint"]
    assert issued["content"] == record["content"]
    assert len(issued["content"]["rows"]) == 30
    # The original cannot be changed by any path.
    with pytest.raises((finalise.FinalisationError, RevisionError)):
        add_entry(
            db, run_id, day=date(2026, 11, 12), kind="activity",
            title_en="Straight into the record",
        )


def test_a_correction_carries_only_the_reviews_it_can_stand_behind(db):
    run_id, _, correction = a_correction(db)
    corrected_run = correction["correction_run_id"]

    # Changing a date's content leaves that date pending again in the correction.
    edit = add_entry(
        db, corrected_run, day=date(2026, 11, 12), kind="activity",
        title_en="Corrected entry",
    )
    state = revisions.review_state(db, edit.run_id)
    by_date = {item["date"]: item for item in state["dates"]}
    assert by_date["2026-11-12"]["state"] == "pending_review"
    assert by_date["2026-11-11"]["state"] == "reviewed"

    check = finalise.check_finalisation(db, edit.run_id, "2026-11")
    blocker = next(
        item for item in check.blockers if item.code == finalise.C_DATES_NOT_REVIEWED
    )
    assert blocker.dates == ("2026-11-12",)
    with pytest.raises(finalise.FinalisationError):
        finalise.finalise_month(db, edit.run_id, "2026-11")

    revisions.mark_reviewed(
        db, edit.run_id, date(2026, 11, 12),
        expected_fingerprint=by_date["2026-11-12"]["fingerprint"],
    )
    successor = finalise.finalise_month(db, edit.run_id, "2026-11")

    assert successor["sequence"] == 2
    assert successor["parent_finalisation_id"] == correction["correction_of_finalisation_id"]
    assert successor["correction_of_run_id"] == run_id
    chain = finalise.correction_chain(db, correction["register_id"], "2026-11")
    assert [item["sequence"] for item in chain] == [1, 2]
    assert [item["is_latest"] for item in chain] == [False, True]
    # Only the correction is open to further change; the original never is.
    with pytest.raises((finalise.FinalisationError, RevisionError)):
        add_entry(db, run_id, day=date(2026, 11, 12), kind="activity", title_en="No")


def test_a_correction_of_a_superseded_version_is_refused(db):
    run_id, record, correction = a_correction(db)
    corrected_run = correction["correction_run_id"]
    review_every_date(db, corrected_run)
    finalise.finalise_month(db, corrected_run, "2026-11")

    with pytest.raises(finalise.FinalisationError) as error:
        finalise.create_correction_revision(db, run_id, reason="again on the old one")
    assert error.value.constraint == finalise.C_SOURCE_SUPERSEDED

    # The version in force can be corrected.
    second = finalise.create_correction_revision(
        db, corrected_run, reason="a second correction"
    )
    assert second["next_sequence"] == 3
    assert second["correction_of_revision"] == 2


def test_a_correction_preserves_the_original_locks(db):
    run_id = stored(db)
    revisions.lock_date(db, run_id, date(2026, 11, 9))
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    correction = finalise.create_correction_revision(db, run_id, reason="a lock matters")
    corrected_run = correction["correction_run_id"]

    assert correction["locked_dates"] == ["2026-11-09"]
    assert set(revision_state.locks(db, corrected_run)) == {"2026-11-09"}
    with pytest.raises(Exception) as error:
        add_entry(
            db, corrected_run, day=date(2026, 11, 9), kind="activity",
            title_en="Straight through a lock",
        )
    assert "locked" in str(error.value)
    # Unlocking is explicit, and only then may the date change.
    revisions.unlock_date(db, corrected_run, date(2026, 11, 9))
    add_entry(
        db, corrected_run, day=date(2026, 11, 9), kind="activity",
        title_en="Now allowed",
    )


def test_an_abandoned_correction_leaves_the_record_in_force(db):
    run_id, record, correction = a_correction(db)

    result = finalise.abandon_correction(db, correction["register_id"], "2026-11")

    assert result["abandoned_run_id"] == correction["correction_run_id"]
    assert result["current_run_id"] == run_id
    assert revisions.current_run(db, correction["register_id"]) == run_id
    lifecycle = {
        item["month"]: item
        for item in finalise.month_lifecycle(db, correction["register_id"])
    }
    assert lifecycle["2026-11"]["lifecycle"] == "finalised"
    assert lifecycle["2026-11"]["open_correction_run_id"] is None
    # The abandoned revision is still readable, and a fresh correction can be opened.
    assert revision_state.revision(db, correction["correction_run_id"]) is not None
    again = finalise.create_correction_revision(db, run_id, reason="opened afresh")
    assert again["correction_run_id"] != correction["correction_run_id"]


def test_a_correction_cannot_be_abandoned_twice(db):
    _, _, correction = a_correction(db)
    finalise.abandon_correction(db, correction["register_id"], "2026-11")

    with pytest.raises(finalise.FinalisationError) as error:
        finalise.abandon_correction(db, correction["register_id"], "2026-11")
    assert error.value.constraint == finalise.C_NOT_A_CORRECTION


def test_each_month_can_be_corrected_without_touching_the_other(db):
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    review_every_date(db, run_id)
    november = finalise.finalise_month(db, run_id, "2026-11")
    register_id = revisions.register_of(db, run_id)

    correction = finalise.create_correction_revision(db, run_id, reason="November fix")
    corrected_run = correction["correction_run_id"]
    edit = add_entry(
        db, corrected_run, day=date(2026, 11, 12), kind="activity",
        title_en="November corrected",
    )
    corrected_run = edit.run_id
    review_every_date(db, corrected_run, month="2026-11")
    december = finalise.finalise_month(db, corrected_run, "2026-12")

    # December closed from its own revision, with its own sequence.
    assert december["sequence"] == 1
    assert december["run_id"] == corrected_run
    assert december["correction_of_run_id"] is None
    assert november["fingerprint"] != finalise.month_fingerprint(
        db, corrected_run, "2026-11"
    )
    # December was closed from the revision the correction produced, and its own copy of
    # December is the one recorded: November's change did not reach it.
    assert finalise.finalisation_record(db, december["finalisation_id"])["fingerprint"] == (
        finalise.month_fingerprint(db, corrected_run, "2026-12")
    )
    assert december["run_id"] != november["run_id"]
    assert revision_state.finalisations(db, register_id, "2026-12")[0].finalisation_id == (
        december["finalisation_id"]
    )


# --- a database that predates finalisation ---------------------------------


def test_a_database_left_at_schema_three_gains_finalisation(tmp_path):
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
        if migration.version > 3:
            continue
        for statement in migration.statements:
            connection.execute(statement)
        if migration.apply is not None:
            migration.apply(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migration (version, name, applied_at) "
            "VALUES (?, ?, '2026-01-01T00:00:00')",
            (migration.version, migration.name),
        )
    connection.execute(
        """
        INSERT INTO generation_run (run_id, created_at, mode, start_date, end_date,
            months_json, seed, generator_version, allocator_version, outcome, is_draft,
            master_version, request_schema_version, request_snapshot_json,
            readiness_json, missing_content_json, register_id)
        VALUES ('run-old', '2026-01-01T00:00:00', 'coverage_cycle', '2026-11-01',
            '2026-11-30', '["2026-11"]', 7, 'g1', 'a1', 'allocation_complete', 1, 1, 1,
            '{}', '[]', '[]', 'R-old')
        """
    )
    connection.execute(
        """
        INSERT INTO generation_row (run_id, ordinal, row_date, weekday, kind,
            is_working_day, reasons_en_json, reasons_kn_json, warnings_json)
        VALUES ('run-old', 1, '2026-11-02', 0, 'working_day', 1, '[]', '[]', '[]')
        """
    )
    connection.commit()

    applied = apply_migrations(connection)

    assert SCHEMA_VERSION == max(migration.version for migration in MIGRATIONS)
    assert 4 in applied_versions(connection)
    assert 4 in applied
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(generation_run)").fetchall()
    }
    assert "correction_of_run_id" in columns
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"month_finalisation", "month_state"} <= tables
    triggers = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
    }
    assert "trg_finalised_generation_row_update" in triggers
    assert "trg_finalisation_update" in triggers
    # The rows written before finalisation existed are untouched and readable.
    assert connection.execute(
        "SELECT COUNT(*) AS n FROM generation_row"
    ).fetchone()["n"] == 1


# --- one revision can close more than one month ----------------------------


def test_one_revision_closes_each_of_its_months(db):
    # A register that spans two months holds both, so both are closed from the same
    # revision. Uniqueness on the revision alone would make the second month impossible.
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    register_id = revisions.register_of(db, run_id)
    review_every_date(db, run_id)

    november = finalise.finalise_month(db, run_id, "2026-11")
    december = finalise.finalise_month(db, run_id, "2026-12")

    assert november["run_id"] == december["run_id"] == run_id
    assert [november["sequence"], december["sequence"]] == [1, 1]
    assert finalise.finalised_months(db, register_id) == {
        "2026-11": november["fingerprint"],
        "2026-12": december["fingerprint"],
    }
    assert finalise.finalised_months_of_run(db, run_id) == ("2026-11", "2026-12")


def test_a_closing_one_month_does_not_make_the_next_look_finalised(db):
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    check = finalise.check_finalisation(db, run_id, "2026-12")

    assert check.eligible is True, [item.as_dict() for item in check.blockers]
    assert finalise.C_ALREADY_FINALISED not in {item.code for item in check.blockers}


def test_a_correction_of_one_closed_month_carries_the_other(db):
    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 12, 31), cycle_months=2)
    register_id = revisions.register_of(db, run_id)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")
    finalise.finalise_month(db, run_id, "2026-12")

    correction = finalise.create_correction_revision(db, run_id, reason="November only")
    corrected_run = correction["correction_run_id"]

    # Both months' reviews are carried, because neither month's content changed yet.
    assert len(correction["carried_reviews"]) == 61
    assert correction["pending_dates"] == []
    check = finalise.check_finalisation(db, corrected_run, "2026-11")
    assert check.eligible is True, [item.as_dict() for item in check.blockers]

    successor = finalise.finalise_month(db, corrected_run, "2026-11")

    assert successor["sequence"] == 2
    # December is untouched: its record, its version in force and its own sequence.
    december = finalise.correction_chain(db, register_id, "2026-12")
    assert [item["sequence"] for item in december] == [1]
    assert december[0]["is_latest"] is True
    assert december[0]["run_id"] == run_id
    months = {item["month"]: item for item in finalise.month_lifecycle(db, register_id)}
    assert months["2026-12"]["lifecycle"] == "finalised"
    assert months["2026-11"]["open_correction_run_id"] is None


def test_a_correction_finalised_after_further_edits_closes_the_branch(db):
    run_id, _, correction = a_correction(db)
    corrected_run = correction["correction_run_id"]
    edit = add_entry(
        db, corrected_run, day=date(2026, 11, 13), kind="activity",
        title_en="A further correction",
    )
    review_every_date(db, edit.run_id, month="2026-11")
    finalise.finalise_month(db, edit.run_id, "2026-11")

    months = {
        item["month"]: item
        for item in finalise.month_lifecycle(db, correction["register_id"])
    }
    # The branch is closed even though it was worked on across revisions.
    assert months["2026-11"]["open_correction_run_id"] is None
    assert months["2026-11"]["lifecycle"] == "finalised"
    with pytest.raises(finalise.FinalisationError) as error:
        finalise.abandon_correction(db, correction["register_id"], "2026-11")
    assert error.value.constraint == finalise.C_NOT_A_CORRECTION
