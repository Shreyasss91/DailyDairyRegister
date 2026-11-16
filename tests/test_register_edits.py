"""Correcting a register: entries and field-visit dates, one revision at a time.

A correction is never an update. It is a new revision whose parent is the one that was
corrected, so the earlier revision is still readable and still says what it said. These
tests pin that, and they pin the two rules that decide what may be corrected in place:
a locked date is untouchable, and a moved visit is judged by the register's own
validator rather than by arithmetic in the edit layer.
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
    DayEntryKind,
    generate_draft,
)
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.application.register_edits import (
    C_DATE_LOCKED,
    C_ENTRY_TEXT_EMPTY,
    C_ENTRY_UNKNOWN,
    C_MANUAL_LEAVE_UNSUPPORTED,
    C_VISIT_EDIT_REJECTED,
    C_VISIT_MOVE_CROSSES_MONTH,
    C_VISIT_UNKNOWN,
    add_entry,
    edit_entry,
    move_entry,
    move_visit,
    remove_entry,
)
from dailyworkreg.application.revisions import (
    C_DATE_NOT_IN_REVISION,
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
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "edits.db")
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


def spec(*, seed: int = 7, visits: int = 1) -> GenerationSpec:
    start = date(2026, 11, 1)
    return GenerationSpec(
        start=start,
        end=date(2026, 11, 30),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=seed,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=1,
        cycle_visits_per_asset=visits,
    )


def stored(db: sqlite3.Connection, *, visits: int = 1) -> str:
    prepared = prepare_generation(db, spec(visits=visits))
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    return generations.save_run(db, prepared, draft)


def rows_by_day(db: sqlite3.Connection, run_id: str) -> dict[date, object]:
    return {row.day: row for row in generations.load_register(db, run_id).rows}


def first_entry(db: sqlite3.Connection, run_id: str):
    for row in generations.load_register(db, run_id).rows:
        if row.entries:
            return row.day, row.entries[0]
    raise AssertionError("the fixture revision carries no entry to correct")


def first_visit(db: sqlite3.Connection, run_id: str):
    for row in generations.load_register(db, run_id).rows:
        for item in row.allocated_visits:
            return row.day, item.visit.asset_id
    raise AssertionError("the fixture revision carries no visit to move")


# --- entries -----------------------------------------------------------------


def test_correcting_an_entry_stores_a_new_revision_and_leaves_the_old_one_alone(db):
    base = stored(db)
    day, entry = first_entry(db, base)
    before = generations.load_register(db, base)

    result = edit_entry(
        db,
        base,
        entry.entry_id,
        title_en="Corrected description",
        title_kn="ಸರಿಪಡಿಸಿದ ವಿವರಣೆ",
        reason="typo in the office register",
    )

    after = generations.load_register(db, result.run_id)
    original = next(item for item in before.rows if item.day == day)
    corrected_row = next(item for item in after.rows if item.day == day)
    corrected = next(
        item for item in corrected_row.entries if item.entry_id == entry.entry_id
    )
    assert corrected.title_en == "Corrected description"
    assert corrected.title_kn == "ಸರಿಪಡಿಸಿದ ವಿವರಣೆ"
    assert corrected.provenance == "manual"
    # The revision that was corrected is unchanged.
    assert (
        next(item for item in original.entries if item.entry_id == entry.entry_id).title_en
        == entry.title_en
    )
    assert result.parent_run_id == base
    assert registers(db)[0]["current_run_id"] == result.run_id
    assert [item["revision_number"] for item in registers(db)[0]["revisions"]] == [1, 2]


def test_one_language_of_an_entry_can_be_corrected_without_the_other(db):
    base = stored(db)
    day, entry = first_entry(db, base)

    result = edit_entry(db, base, entry.entry_id, title_en="English only change")

    row = next(
        item for item in generations.load_register(db, result.run_id).rows if item.day == day
    )
    corrected = next(item for item in row.entries if item.entry_id == entry.entry_id)
    assert corrected.title_en == "English only change"
    assert corrected.title_kn == entry.title_kn


def test_an_entry_correction_carries_an_audit_row_on_the_new_revision(db):
    base = stored(db)
    _, entry = first_entry(db, base)

    result = edit_entry(db, base, entry.entry_id, title_en="Audited change")

    changes = revision_state.changes(db, result.run_id)
    assert [item.action for item in changes] == ["edit_entry"]
    assert changes[0].entry_id == entry.entry_id
    assert "Audited change" in changes[0].detail_json


def test_an_entry_that_is_not_on_the_revision_is_refused(db):
    base = stored(db)
    with pytest.raises(RevisionError) as error:
        edit_entry(db, base, "E-does-not-exist", title_en="x")
    assert error.value.constraint == C_ENTRY_UNKNOWN


def test_an_entry_cannot_be_blanked(db):
    base = stored(db)
    _, entry = first_entry(db, base)
    with pytest.raises(RevisionError) as error:
        edit_entry(db, base, entry.entry_id, title_en="   ")
    assert error.value.constraint == C_ENTRY_TEXT_EMPTY


def test_an_entry_on_a_locked_date_cannot_be_corrected(db):
    base = stored(db)
    day, entry = first_entry(db, base)
    lock_date(db, base, day)

    with pytest.raises(RevisionError) as error:
        edit_entry(db, base, entry.entry_id, title_en="not allowed")
    assert error.value.constraint == C_DATE_LOCKED


def test_an_added_entry_is_manual_and_carries_a_stable_id(db):
    base = stored(db)
    day = date(2026, 11, 5)

    result = add_entry(
        db,
        base,
        day=day,
        kind=DayEntryKind.ACTIVITY.value,
        title_en="Meter reading",
        title_kn="ಮೀಟರ್ ಓದುವಿಕೆ",
    )

    row = next(
        item for item in generations.load_register(db, result.run_id).rows if item.day == day
    )
    added = [item for item in row.entries if item.entry_id.startswith("E-")]
    assert len(added) == 1
    assert added[0].provenance == "manual"
    assert added[0].title_kn == "ಮೀಟರ್ ಓದುವಿಕೆ"
    stored_rows = [
        item for item in revision_state.entries(db, result.run_id)
        if item.entry_id == added[0].entry_id
    ]
    assert len(stored_rows) == 1


def test_a_manual_leave_entry_is_refused_because_leave_comes_from_the_master(db):
    base = stored(db)
    with pytest.raises(RevisionError) as error:
        add_entry(
            db,
            base,
            day=date(2026, 11, 5),
            kind=DayEntryKind.LEAVE.value,
            title_en="Casual leave",
        )
    assert error.value.constraint == C_MANUAL_LEAVE_UNSUPPORTED


def test_an_entry_can_be_added_to_a_date_that_holds_nothing(db):
    base = stored(db)
    blank = next(
        row.day
        for row in generations.load_register(db, base).rows
        if not row.entries and not row.allocated_visits and not row.fixed_visits
    )

    result = add_entry(
        db,
        base,
        day=blank,
        kind=DayEntryKind.EVENT.value,
        title_en="Office inspection",
    )

    row = next(
        item for item in generations.load_register(db, result.run_id).rows if item.day == blank
    )
    assert [item.title_en for item in row.entries] == ["Office inspection"]


def test_a_date_outside_the_revision_cannot_receive_an_entry(db):
    base = stored(db)
    with pytest.raises(RevisionError) as error:
        add_entry(
            db,
            base,
            day=date(2026, 12, 3),
            kind=DayEntryKind.ACTIVITY.value,
            title_en="Outside the range",
        )
    assert error.value.constraint == C_DATE_NOT_IN_REVISION


def test_removing_an_entry_leaves_the_revision_it_came_from_intact(db):
    base = stored(db)
    day, entry = first_entry(db, base)

    result = remove_entry(db, base, entry.entry_id)

    row = next(
        item for item in generations.load_register(db, result.run_id).rows if item.day == day
    )
    assert entry.entry_id not in {item.entry_id for item in row.entries}
    old = next(
        item for item in generations.load_register(db, base).rows if item.day == day
    )
    assert entry.entry_id in {item.entry_id for item in old.entries}


def test_an_entry_moves_between_dates_without_changing_its_identity(db):
    base = stored(db)
    day, entry = first_entry(db, base)
    target = date(2026, 11, 6)
    assert target != day

    result = move_entry(db, base, entry.entry_id, to_day=target)

    rows = rows_by_day(db, result.run_id)
    assert entry.entry_id not in {item.entry_id for item in rows[day].entries}
    moved = next(item for item in rows[target].entries if item.entry_id == entry.entry_id)
    assert moved.day == target
    assert moved.provenance == "manual"


def test_an_entry_cannot_be_moved_into_a_locked_date(db):
    base = stored(db)
    _, entry = first_entry(db, base)
    target = date(2026, 11, 6)
    lock_date(db, base, target)

    with pytest.raises(RevisionError) as error:
        move_entry(db, base, entry.entry_id, to_day=target)
    assert error.value.constraint == C_DATE_LOCKED


# --- field-visit dates --------------------------------------------------------


def test_a_visit_moves_to_another_working_date_of_its_month(db):
    base = stored(db)
    day, asset_id = first_visit(db, base)
    target = next(
        row.day
        for row in generations.load_register(db, base).rows
        if row.is_working_day and row.day > day and not row.is_working_day is False
    )

    result = move_visit(db, base, asset_id=asset_id, from_day=day, to_day=target)

    before = rows_by_day(db, base)
    after = rows_by_day(db, result.run_id)
    # The earlier revision still holds the visit where it was.
    assert asset_id in {item.visit.asset_id for item in before[day].allocated_visits}
    assert asset_id not in {item.visit.asset_id for item in after[day].allocated_visits}
    assert asset_id in {item.visit.asset_id for item in after[target].allocated_visits}
    register = generations.load_register(db, result.run_id)
    assert len(register.visits) == len(generations.load_register(db, base).visits)


def test_a_visit_cannot_be_moved_to_a_non_working_date(db):
    base = stored(db)
    day, asset_id = first_visit(db, base)
    sunday = date(2026, 11, 1)
    assert not rows_by_day(db, base)[sunday].is_working_day

    with pytest.raises(RevisionError) as error:
        move_visit(db, base, asset_id=asset_id, from_day=day, to_day=sunday)
    assert error.value.constraint == C_VISIT_EDIT_REJECTED
    assert any(
        item.constraint == "not_a_working_date" for item in error.value.violations
    )


def test_a_visit_cannot_be_moved_inside_the_seven_day_interval(db):
    # Two visits owed by every asset in one month, so one of them can be moved next to
    # the other and the revisit interval is the constraint that refuses it.
    base = stored(db, visits=2)
    by_asset: dict[str, list[date]] = {}
    for row in generations.load_register(db, base).rows:
        for item in row.allocated_visits:
            by_asset.setdefault(item.visit.asset_id, []).append(row.day)
    asset_id, days = next(
        (asset, sorted(when)) for asset, when in by_asset.items() if len(when) == 2
    )
    working = sorted(
        row.day
        for row in generations.load_register(db, base).rows
        if row.is_working_day
    )
    target = working[working.index(days[0]) + 3]
    assert target < days[1]

    with pytest.raises(RevisionError) as error:
        move_visit(db, base, asset_id=asset_id, from_day=days[1], to_day=target)

    assert error.value.constraint == C_VISIT_EDIT_REJECTED
    assert "revisit_gap_violated" in {
        item.constraint for item in error.value.violations
    }


def test_a_visit_cannot_be_moved_across_a_month_boundary(db):
    base = stored(db)
    day, asset_id = first_visit(db, base)
    with pytest.raises(RevisionError) as error:
        move_visit(db, base, asset_id=asset_id, from_day=day, to_day=date(2026, 12, 2))
    assert error.value.constraint == C_VISIT_MOVE_CROSSES_MONTH


def test_moving_a_visit_that_is_not_on_the_date_is_refused(db):
    base = stored(db)
    with pytest.raises(RevisionError) as error:
        move_visit(db, base, asset_id="SCD-999", from_day=date(2026, 11, 3), to_day=date(2026, 11, 4))
    assert error.value.constraint == C_VISIT_UNKNOWN


def test_a_visit_on_a_locked_date_cannot_be_moved(db):
    base = stored(db)
    day, asset_id = first_visit(db, base)
    lock_date(db, base, day)

    with pytest.raises(RevisionError) as error:
        move_visit(db, base, asset_id=asset_id, from_day=day, to_day=date(2026, 11, 20))
    assert error.value.constraint == C_DATE_LOCKED


def test_a_visit_move_keeps_the_months_own_obligations_satisfied(db):
    base = stored(db)
    day, asset_id = first_visit(db, base)
    target = date(2026, 11, 20)

    result = move_visit(db, base, asset_id=asset_id, from_day=day, to_day=target)

    month = generations.load_register(db, result.run_id).months[0]
    counts: dict[str, int] = {}
    for item in month.visits:
        counts[item.visit.asset_id] = counts.get(item.visit.asset_id, 0) + 1
    assert counts.get(asset_id, 0) == month.obligations.get(asset_id, 0)


# --- review and revision interaction -----------------------------------------


def test_a_correction_invalidates_the_review_of_the_date_it_changed_and_no_other(db):
    base = stored(db)
    day, entry = first_entry(db, base)
    other = date(2026, 11, 5)
    mark_reviewed(db, base, day, note="read")
    mark_reviewed(db, base, other, note="read")

    result = edit_entry(db, base, entry.entry_id, title_en="Different wording")

    state = review_state(db, result.run_id)
    states = {item["date"]: item["state"] for item in state["dates"]}
    assert states[day.isoformat()] == "pending_review"
    assert states[other.isoformat()] == "reviewed"
    carried = {item["date"]: item["carried_from_run_id"] for item in state["dates"]}
    assert carried[other.isoformat()] == base


def test_a_correction_keeps_the_register_and_its_earlier_revisions_readable(db):
    base = stored(db)
    day, entry = first_entry(db, base)

    one = edit_entry(db, base, entry.entry_id, title_en="First correction")
    two = edit_entry(db, one.run_id, entry.entry_id, title_en="Second correction")

    assert register_of(db, base) == register_of(db, two.run_id)
    summary = registers(db)[0]
    assert [item["revision_number"] for item in summary["revisions"]] == [1, 2, 3]
    assert summary["current_run_id"] == two.run_id
    titles = []
    for run in (base, one.run_id, two.run_id):
        row = next(
            item for item in generations.load_register(db, run).rows if item.day == day
        )
        titles.append(
            next(item for item in row.entries if item.entry_id == entry.entry_id).title_en
        )
    assert titles == [entry.title_en, "First correction", "Second correction"]


def test_a_superseded_revision_cannot_be_corrected(db):
    base = stored(db)
    _, entry = first_entry(db, base)
    edit_entry(db, base, entry.entry_id, title_en="Superseding correction")

    with pytest.raises(RevisionError) as error:
        edit_entry(db, base, entry.entry_id, title_en="Too late")
    assert error.value.constraint == "revision_is_not_current"
