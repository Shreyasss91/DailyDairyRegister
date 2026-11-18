"""The bulk close decides and writes in one transaction, so nothing can slip between.

A month's content fingerprint covers the rows of the register. It says nothing about
whether those rows have been read, whether the calendar has since gained a holiday that
conflicts with a working day, whether the masters are still ready, or which findings the
operator has acknowledged. Those are live questions, and a bulk close that asked them
outside the write transaction let the answers change between the question and the record
that reported it — a month closed as approved while a date of it had just been put back
to unread.

These tests place the change exactly at the transaction boundary: the mutation is made
immediately before the act's transaction begins, which is after every gate would have run
under the old ordering and before every gate runs under the current one. That is a
deterministic seam, not a race won by sleeping.
"""

from __future__ import annotations

import contextlib
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
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters, revision_state
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities

# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "atomic.db")
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
    for index in range(1, 3):
        masters.upsert_activity(
            connection,
            activity_id=f"A-{index:03d}",
            category="Inspection",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
        )
    cal.upsert_holiday(
        connection,
        holiday_id="H-2026-11-20",
        holiday_date=date(2026, 11, 20),
        name="Recorded holiday",
        source="karnataka",
    )
    yield connection
    connection.close()


def spec(*, start: date = date(2026, 11, 1), end: date = date(2026, 12, 31)) -> GenerationSpec:
    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=7,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=2,
        cycle_visits_per_asset=1,
    )


def stored(db: sqlite3.Connection, **kwargs) -> str:
    prepared = prepare_generation(db, spec(**kwargs))
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    return generations.save_run(db, prepared, draft)


def review_every_date(db: sqlite3.Connection, run_id: str) -> None:
    for item in revisions.review_state(db, run_id)["dates"]:
        revisions.mark_reviewed(
            db,
            run_id,
            date.fromisoformat(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )


def confirmation(db: sqlite3.Connection, run_id: str) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """The confirmation a page would send, built from the preflight it was shown."""

    preflight = finalise.bulk_preflight(db, run_id)
    fingerprints = {
        item["month"]: item["fingerprint"]
        for item in preflight["months"]
        if item["month"] in preflight["target_months"]
    }
    acknowledgements: dict[str, tuple[str, ...]] = dict.fromkeys(preflight["target_months"], ())
    for item in preflight["pending_acknowledgements"]:
        acknowledgements[item["month"]] = (
            *acknowledgements[item["month"]],
            item["token"],
        )
    return fingerprints, acknowledgements


@contextlib.contextmanager
def boundary_hook(monkeypatch, connection: sqlite3.Connection, change) -> None:
    """Run ``change`` immediately before the act's transaction begins.

    This is the last moment the old ordering would have been safe and the first moment
    the current one is not: under the previous code every gate had already run, under the
    current code none has.
    """

    real = finalise.generation_store.transaction
    state = {"fired": False}

    @contextlib.contextmanager
    def hooked(target: sqlite3.Connection):
        if not state["fired"]:
            state["fired"] = True
            change(connection)
        with real(target) as opened:
            yield opened

    monkeypatch.setattr(finalise.generation_store, "transaction", hooked)


def counts(db: sqlite3.Connection) -> tuple[int, int, int]:
    """(finalisations, months with a latest pointer, rows in the register)."""

    finalisations = db.execute(
        "SELECT COUNT(*) AS total FROM month_finalisation"
    ).fetchone()["total"]
    pointers = db.execute(
        "SELECT COUNT(*) AS total FROM month_state WHERE latest_finalisation_id IS NOT NULL"
    ).fetchone()["total"]
    rows = db.execute("SELECT COUNT(*) AS total FROM generation_row").fetchone()["total"]
    return int(finalisations), int(pointers), int(rows)


# --- A: a review withdrawn at the boundary ---------------------------------


def test_a_review_withdrawn_at_the_boundary_refuses_the_whole_act(db, monkeypatch):
    run_id = stored(db)
    review_every_date(db, run_id)
    fingerprints, acknowledgements = confirmation(db, run_id)
    before = counts(db)

    def withdraw(connection: sqlite3.Connection) -> None:
        date_to_clear = next(
            item["date"]
            for item in revisions.review_state(connection, run_id)["dates"]
            if item["date"].startswith("2026-12")
        )
        revisions.clear_review(
            connection, run_id, date.fromisoformat(date_to_clear)
        )

    boundary_hook(monkeypatch, db, withdraw)

    with pytest.raises(finalise.FinalisationError) as refused:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints=fingerprints,
            acknowledgements=acknowledgements,
        )

    assert refused.value.constraint == finalise.C_BULK_BLOCKED
    assert "2026-12" in refused.value.detail
    assert counts(db) == before
    assert revision_state.month_state(
        db, revision_state.register_for_run(db, run_id), "2026-11"
    ) is None


# --- B: a calendar change at the boundary ----------------------------------


def test_a_calendar_change_at_the_boundary_refuses_the_whole_act(db, monkeypatch):
    run_id = stored(db)
    review_every_date(db, run_id)
    fingerprints, acknowledgements = confirmation(db, run_id)
    before = counts(db)

    def record_holiday(connection: sqlite3.Connection) -> None:
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-12-06",
            holiday_date=date(2026, 12, 6),
            name="Recorded while closing",
            source="karnataka",
        )

    boundary_hook(monkeypatch, db, record_holiday)

    with pytest.raises(finalise.FinalisationError) as refused:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints=fingerprints,
            acknowledgements=acknowledgements,
            note="one act",
        )

    assert refused.value.constraint == finalise.C_BULK_BLOCKED
    codes = [item.code for item in refused.value.blockers]
    assert finalise.C_CONFLICT_NOT_ACKNOWLEDGED in codes
    assert counts(db) == before


# --- C: a finding the caller has not answered ------------------------------


def test_a_finding_that_appears_at_the_boundary_is_not_answered_by_old_tokens(
    db, monkeypatch
):
    run_id = stored(db)
    review_every_date(db, run_id)
    cal.upsert_holiday(
        db,
        holiday_id="H-2026-11-01",
        holiday_date=date(2026, 11, 1),
        name="Founder's Day",
        source="karnataka",
    )
    fingerprints, acknowledgements = confirmation(db, run_id)
    assert acknowledgements["2026-11"], "the caller acknowledged the first finding"
    before = counts(db)

    def second_finding(connection: sqlite3.Connection) -> None:
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-11-22",
            holiday_date=date(2026, 11, 22),
            name="Recorded afterwards",
            source="karnataka",
        )

    boundary_hook(monkeypatch, db, second_finding)

    with pytest.raises(finalise.FinalisationError) as refused:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints=fingerprints,
            acknowledgements=acknowledgements,
        )

    assert refused.value.constraint == finalise.C_BULK_BLOCKED
    assert "2026-11" in refused.value.detail
    assert counts(db) == before


# --- D: a write that fails after an earlier write --------------------------


def test_a_write_that_fails_on_the_second_month_leaves_the_first_open(
    db, monkeypatch
):
    run_id = stored(db)
    review_every_date(db, run_id)
    fingerprints, acknowledgements = confirmation(db, run_id)
    before = counts(db)
    written: list[str] = []
    real = finalise._write_finalisation

    def refuse_the_last(connection: sqlite3.Connection, prepared) -> None:
        if written:
            raise finalise.FinalisationError(
                finalise.C_CONTENT_CHANGED_WHILE_FINALISING,
                "the month changed while it was being finalised",
            )
        written.append(prepared.record.month)
        real(connection, prepared)

    monkeypatch.setattr(finalise, "_write_finalisation", refuse_the_last)

    with pytest.raises(finalise.FinalisationError):
        finalise.finalise_months(
            db,
            run_id,
            fingerprints=fingerprints,
            acknowledgements=acknowledgements,
        )

    assert written == ["2026-11"], "the first month's write was attempted"
    assert counts(db) == before
    for month in ("2026-11", "2026-12"):
        assert revision_state.finalisation_for_run_month(db, run_id, month) is None


# --- E: several correction months in one act -------------------------------


def test_a_correction_revision_is_a_target_even_where_a_version_is_in_force(db):
    """A month the lifecycle already shows as issued is still open on a correction.

    This is why the target set is derived from ``finalisation_for_run_month`` and not
    from the lifecycle pointer: the pointer names the version in force, which is exactly
    the version a correction exists to supersede. Deriving targets from the pointer
    would leave every correction permanently unclosable.
    """

    run_id = stored(db)
    review_every_date(db, run_id)
    fingerprints, acknowledgements = confirmation(db, run_id)
    finalise.finalise_months(
        db,
        run_id,
        fingerprints=fingerprints,
        acknowledgements=acknowledgements,
    )
    register_id = revision_state.register_for_run(db, run_id)
    previous = {
        month: revision_state.finalisation_for_run_month(db, run_id, month)
        for month in ("2026-11", "2026-12")
    }
    assert revision_state.month_state(
        db, register_id, "2026-11"
    ).latest_finalisation_id == previous["2026-11"].finalisation_id

    for attempt in range(2):
        correction = finalise.create_correction_revision(
            db, run_id, reason=f"Correction {attempt + 1}"
        )["correction_run_id"]
        correction_fingerprints, correction_acknowledgements = confirmation(db, correction)

        # Every month of the correction is open on the correction, including the one the
        # register still shows as issued.
        assert sorted(correction_fingerprints) == ["2026-11", "2026-12"]
        closed = finalise.finalise_months(
            db,
            correction,
            fingerprints=correction_fingerprints,
            acknowledgements=correction_acknowledgements,
        )

        assert closed["count"] == 2
        for record in closed["months"]:
            parent = previous[record["month"]]
            assert record["sequence"] == parent.sequence + 1
            assert record["parent_finalisation_id"] == parent.finalisation_id
            assert record["correction_of_run_id"] == parent.run_id
            assert record["revision_number"] > parent.revision_number
            assert revision_state.month_state(
                db, register_id, record["month"]
            ).latest_finalisation_id == record["finalisation_id"]

        # The next attempt corrects the version just issued, not the first one.
        previous = {
            month: revision_state.finalisation_for_run_month(db, correction, month)
            for month in ("2026-11", "2026-12")
        }
        assert all(item is not None for item in previous.values())
        run_id = correction


# --- F: two closes of the same revision ------------------------------------


def test_a_second_close_of_the_same_revision_cannot_interleave(db, monkeypatch):
    """One revision cannot receive two finalisations for one month.

    The second attempt is made from its own connection while the first act is still
    holding its write transaction, which is what two clicks on two devices look like.
    SQLite refuses the second writer outright rather than letting it begin and then
    discover the duplicate, and once the first act has committed the same attempt is
    refused for having nothing left to close.
    """

    run_id = stored(db)
    review_every_date(db, run_id)
    fingerprints, acknowledgements = confirmation(db, run_id)
    second = sqlite3.connect(db.execute("PRAGMA database_list").fetchone()["file"])
    second.row_factory = sqlite3.Row
    second.execute("PRAGMA busy_timeout = 100")
    blocked: list[str] = []
    real = finalise._write_finalisation

    def attempt_while_armed(connection: sqlite3.Connection, prepared) -> None:
        if not blocked:
            blocked.append("attempted")
            try:
                second.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                blocked.append(str(error))
            else:
                second.execute("COMMIT")
        real(connection, prepared)

    monkeypatch.setattr(finalise, "_write_finalisation", attempt_while_armed)

    closed = finalise.finalise_months(
        db,
        run_id,
        fingerprints=fingerprints,
        acknowledgements=acknowledgements,
    )

    assert closed["count"] == 2
    assert len(blocked) == 2, blocked
    assert "locked" in blocked[1]
    for month in ("2026-11", "2026-12"):
        records = db.execute(
            "SELECT COUNT(*) AS total FROM month_finalisation WHERE run_id = ? AND month = ?",
            (run_id, month),
        ).fetchone()["total"]
        assert records == 1

    second.close()
    with pytest.raises(finalise.FinalisationError) as refused:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints=fingerprints,
            acknowledgements=acknowledgements,
        )
    assert refused.value.constraint == finalise.C_BULK_NO_TARGETS


# --- the property the act depends on ---------------------------------------


def test_a_nested_write_inside_the_act_does_not_commit_on_its_own(db):
    """The bulk act holds one transaction; a savepoint inside it must not end it.

    ``_write_finalisation`` runs inside the act's transaction, and each repository call
    it makes opens its own. If one of those opened a second ``BEGIN`` the act would
    either fail outright (SQLite refuses it) or commit early and leave the act's own
    rollback with nothing to undo.
    """

    from dailyworkreg.infrastructure.sqlite.database import transaction

    db.execute("CREATE TABLE IF NOT EXISTS t (value INTEGER)")

    with pytest.raises(RuntimeError):
        with transaction(db):
            db.execute("INSERT INTO t (value) VALUES (1)")
            with transaction(db):
                db.execute("INSERT INTO t (value) VALUES (2)")
            raise RuntimeError("the act failed after both writes")

    assert db.execute("SELECT COUNT(*) AS total FROM t").fetchone()["total"] == 0
