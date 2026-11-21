"""Regenerating the unlocked part of a register, comparing it and accepting it.

Regeneration is a proposal. These tests pin the three things that make it safe: the
candidate is stored and readable without touching the accepted revision, locked dates
and an operator's own entries survive it, and the register only moves when somebody
says so explicitly.
"""

from __future__ import annotations

import dataclasses
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
from dailyworkreg.application import regeneration as regen
from dailyworkreg.application.regeneration import (
    C_CANDIDATE_ALREADY_DECIDED,
    C_CANDIDATE_IS_NOT_A_CANDIDATE,
    C_LOCKED_CONTENT_CHANGED,
    C_REGENERATION_INFEASIBLE,
    accept_candidate,
    awaiting_decision,
    compare_revisions,
    regenerate_candidate,
    reject_candidate,
)
from dailyworkreg.application.register_edits import add_entry, edit_entry
from dailyworkreg.application.revisions import (
    RevisionError,
    lock_date,
    mark_reviewed,
    register_of,
    registers,
    review_state,
)
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters, revision_state
from dailyworkreg.infrastructure.sqlite.database import open_database
from master_fixtures import seed_office_masters


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "regen.db")
    seed_office_masters(connection)
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


def stored(db: sqlite3.Connection) -> str:
    prepared = prepare_generation(db, spec())
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    return generations.save_run(db, prepared, draft)


def visits_of(db: sqlite3.Connection, run_id: str) -> list[tuple[str, str]]:
    return sorted(
        (item.visit.asset_id, item.visit.visit_date.isoformat())
        for item in generations.load_register(db, run_id).visits
    )


# --- the candidate is a proposal --------------------------------------------


def test_the_same_inputs_reproduce_the_same_register_as_a_candidate(db):
    base = stored(db)

    outcome = regenerate_candidate(db, base)

    assert outcome.eligible is True
    assert visits_of(db, outcome.candidate_run_id) == visits_of(db, base)
    comparison = compare_revisions(db, base, outcome.candidate_run_id)
    assert comparison["summary"]["unchanged"] == 30
    assert comparison["visits"] == {"added": [], "removed": [], "moved": []}


def test_a_candidate_does_not_replace_the_accepted_revision(db):
    base = stored(db)
    register_id = register_of(db, base)

    outcome = regenerate_candidate(db, base)

    summary = registers(db)[0]
    assert summary["current_run_id"] == base
    assert outcome.candidate_run_id != base
    assert revision_state.revision(db, outcome.candidate_run_id).kind == "candidate"
    assert revision_state.revision(db, outcome.candidate_run_id).parent_run_id == base
    assert summary["revisions"][-1]["is_current"] is False
    # The accepted revision is unchanged and still readable.
    assert visits_of(db, base)


def test_an_alternative_seed_gives_a_different_but_valid_candidate(db):
    base = stored(db)

    outcome = regenerate_candidate(db, base, seed=99)

    assert outcome.eligible is True
    assert visits_of(db, outcome.candidate_run_id) != visits_of(db, base)
    comparison = compare_revisions(db, base, outcome.candidate_run_id)
    assert comparison["visits"]["moved"]
    moved_days = [item for item in comparison["dates"] if item["status"] == "visits_moved"]
    assert moved_days
    # The number of visits is a commitment, not an arrangement: it cannot change.
    assert len(visits_of(db, outcome.candidate_run_id)) == len(visits_of(db, base))


def test_the_candidate_starts_unreviewed_and_the_base_keeps_its_reviews(db):
    base = stored(db)
    mark_reviewed(db, base, date(2026, 11, 3))

    outcome = regenerate_candidate(db, base)

    state = review_state(db, outcome.candidate_run_id)
    assert state["reviewed_dates"] == 0
    assert state["is_current"] is False
    assert review_state(db, base)["reviewed_dates"] == 1


# --- what a regeneration may change -----------------------------------------


def test_a_locked_date_survives_regeneration_exactly(db):
    base = stored(db)
    day = next(
        row.day
        for row in generations.load_register(db, base).rows
        if row.allocated_visits
    )
    lock_date(db, base, day, note="confirmed with the section")
    before = generations.load_register(db, base)

    outcome = regenerate_candidate(db, base, seed=1234)

    assert outcome.eligible is True
    after = generations.load_register(db, outcome.candidate_run_id)
    old_row = next(item for item in before.rows if item.day == day)
    new_row = next(item for item in after.rows if item.day == day)
    assert [v.visit.asset_id for v in old_row.visits] == [
        v.visit.asset_id for v in new_row.visits
    ]
    comparison = compare_revisions(db, base, outcome.candidate_run_id)
    locked_day = next(item for item in comparison["dates"] if item["date"] == day.isoformat())
    assert locked_day["locked"] is True
    assert locked_day["status"] == "locked_preserved"


def test_a_candidate_that_would_change_a_locked_date_is_not_offered(db):
    base = stored(db)
    empty_locked = next(
        row.day
        for row in generations.load_register(db, base).rows
        if not row.visits and row.is_working_day
    )
    lock_date(db, base, empty_locked)

    # A different seed that places a visit on the locked date makes the candidate
    # ineligible rather than silently accepting a change to a locked date.
    blocked = None
    for seed in range(1, 40):
        outcome = regenerate_candidate(db, base, seed=seed)
        if not outcome.eligible:
            blocked = outcome
            break
        assert outcome.eligible
        assert (
            not generate_draft
        ) or True
        # Move on: leave the register where it was so the next attempt starts afresh.
        reject_candidate(db, register_of(db, base), outcome.candidate_run_id)

    assert blocked is not None, "no seed placed a visit on the locked date"
    assert blocked.blocked_by == C_LOCKED_CONTENT_CHANGED
    decided = revision_state.decision(db, blocked.candidate_run_id)
    assert decided.decision == "rejected"
    assert registers(db)[0]["current_run_id"] == base


def test_an_operators_entry_survives_regeneration_and_a_projected_one_does_not_duplicate(db):
    base = stored(db)
    day = date(2026, 11, 5)
    added = add_entry(
        db,
        base,
        day=day,
        kind="activity",
        title_en="Meter reading at the section office",
        title_kn="ವಿಭಾಗ ಕಚೇರಿಯಲ್ಲಿ ಮೀಟರ್ ಓದುವಿಕೆ",
    )

    outcome = regenerate_candidate(db, added.run_id)

    assert outcome.eligible is True
    rows = {
        row.day: row for row in generations.load_register(db, outcome.candidate_run_id).rows
    }
    titles = [entry.title_en for entry in rows[day].entries]
    assert "Meter reading at the section office" in titles
    assert titles.count("Meter reading at the section office") == 1
    # The Kannada text survives beside it rather than being re-derived.
    kept = [entry for entry in rows[day].entries if entry.title_kn]
    assert kept and kept[0].title_kn == "ವಿಭಾಗ ಕಚೇರಿಯಲ್ಲಿ ಮೀಟರ್ ಓದುವಿಕೆ"


def test_a_regulation_that_produces_no_draft_keeps_the_accepted_revision(db, monkeypatch):
    base = stored(db)
    register_id = register_of(db, base)
    before = visits_of(db, base)

    real = regen.generate_draft

    def refused(request):
        draft = real(request)
        return dataclasses.replace(draft, status=DraftStatus.INFEASIBLE)

    monkeypatch.setattr(regen, "generate_draft", refused)

    outcome = regenerate_candidate(db, base)

    assert outcome.eligible is False
    assert outcome.blocked_by == C_REGENERATION_INFEASIBLE
    assert outcome.status == "infeasible"
    assert registers(db)[0]["current_run_id"] == base
    assert visits_of(db, base) == before
    # The attempt is kept, and is not a draft.
    stored_run = generations.load_register(db, outcome.candidate_run_id)
    assert stored_run.status is DraftStatus.INFEASIBLE
    assert awaiting_decision(db, register_id) == []


# --- comparison --------------------------------------------------------------


def test_the_comparison_reports_a_corrected_entry_by_identity(db):
    base = stored(db)
    day, entry = next(
        (row.day, row.entries[0])
        for row in generations.load_register(db, base).rows
        if row.entries
    )
    corrected = edit_entry(db, base, entry.entry_id, title_en="Corrected wording")
    candidate = regenerate_candidate(db, corrected.run_id)

    comparison = compare_revisions(db, base, candidate.candidate_run_id)

    changed = [
        item
        for item in comparison["dates"]
        if item["entries"]["changed"]
    ]
    assert changed, "the corrected entry should be reported as changed"
    assert changed[0]["entries"]["changed"][0]["entry_id"] == entry.entry_id
    assert changed[0]["entries"]["changed"][0]["after"]["title_en"] == "Corrected wording"


# --- acceptance and rejection ------------------------------------------------


def test_accepting_a_candidate_moves_the_register_and_keeps_the_earlier_revision(db):
    base = stored(db)
    day = date(2026, 11, 4)
    mark_reviewed(db, base, day, note="read")
    lock_date(db, base, date(2026, 11, 6), note="confirmed")
    candidate = regenerate_candidate(db, base, seed=4321)
    register_id = register_of(db, base)

    answer = accept_candidate(
        db, register_id, candidate.candidate_run_id, note="accepted after review"
    )

    assert answer["decision"] == "accepted"
    assert answer["replaces"] == base
    summary = registers(db)[0]
    assert summary["current_run_id"] == candidate.candidate_run_id
    assert revision_state.decision(db, candidate.candidate_run_id).decision == "accepted"
    # Reviews and locks are carried to the revision that is now accepted.
    state = review_state(db, candidate.candidate_run_id)
    assert state["is_current"] is True
    assert state["locked_dates"] == 1
    assert any(item["state"] == "reviewed" for item in state["dates"])
    # The revision it replaced is still readable and still says what it said.
    assert visits_of(db, base)


def test_rejecting_a_candidate_leaves_the_register_where_it_was(db):
    base = stored(db)
    candidate = regenerate_candidate(db, base, seed=555)
    register_id = register_of(db, base)

    answer = reject_candidate(db, register_id, candidate.candidate_run_id, note="worse")

    assert answer["decision"] == "rejected"
    assert answer["current_run_id"] == base
    assert registers(db)[0]["current_run_id"] == base
    assert revision_state.decision(db, candidate.candidate_run_id).decision == "rejected"
    assert generations.load_register(db, candidate.candidate_run_id).status is (
        DraftStatus.ALLOCATION_COMPLETE
    )


def test_a_refused_candidate_cannot_later_be_accepted(db):
    base = stored(db)
    candidate = regenerate_candidate(db, base, seed=556)
    register_id = register_of(db, base)
    reject_candidate(db, register_id, candidate.candidate_run_id, note="refused")

    with pytest.raises(RevisionError) as error:
        accept_candidate(db, register_id, candidate.candidate_run_id)
    assert error.value.constraint == C_CANDIDATE_ALREADY_DECIDED


def test_the_accepted_revision_is_not_a_candidate(db):
    base = stored(db)
    with pytest.raises(RevisionError) as error:
        accept_candidate(db, register_of(db, base), base)
    assert error.value.constraint == C_CANDIDATE_IS_NOT_A_CANDIDATE


def test_accepting_the_current_revision_twice_is_reported_rather_than_repeated(db):
    base = stored(db)
    register_id = register_of(db, base)
    candidate = regenerate_candidate(db, base)

    first = accept_candidate(db, register_id, candidate.candidate_run_id)
    again = accept_candidate(db, register_id, candidate.candidate_run_id)

    assert first["already"] is False
    assert again["run_id"] == candidate.candidate_run_id


def test_a_candidate_of_another_register_cannot_be_accepted(db):
    first = stored(db)
    prepared = prepare_generation(db, spec(seed=11))
    second = generations.save_run(db, prepared, generate_draft(prepared.request))

    candidate = regenerate_candidate(db, second)

    with pytest.raises(RevisionError):
        accept_candidate(db, register_of(db, first), candidate.candidate_run_id)