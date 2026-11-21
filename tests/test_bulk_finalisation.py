"""Closing every open month at once: one preflight, one act, all or nothing.

A register is worked on month by month, and an office that has read every date of a
quarter would rather close it in one action than repeat the same confirmation three
times. The risk that buys is a partial close: two months closed and the third refused,
leaving records that say "approved" for a stretch of time nobody signed off as a whole.
These tests pin the three properties that make the bulk act safe — the server derives the
targets, the caller must quote what it was shown, and the write is one transaction — and
then pin the behaviour that must *not* change: single-month finalisation, correction
history, and the record a closed month keeps.
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
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters, revision_state
from dailyworkreg.infrastructure.sqlite.database import open_database
from master_fixtures import seed_office_masters
from dailyworkreg.interfaces.server import create_app
from fastapi.testclient import TestClient

REQUEST = Path(__file__).resolve().parent.parent


# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    """A database with every master a two-month revision needs to be closable.

    Synthetic and complete on purpose: the roster, the holiday baseline and the approved
    activity catalogue are the office's own data and a test that needed them would
    either fabricate them or never run.
    """

    connection = open_database(tmp_path / "bulk.db")
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
    for index in range(1, 3):
        masters.upsert_activity(
            connection,
            activity_id=f"A-{index:03d}",
            category="Inspection",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
        )
    # One recorded General Holiday is what tells the readiness gate that a calendar for
    # the year exists at all; without one, no month can close whatever else is ready.
    cal.upsert_holiday(
        connection,
        holiday_id="H-2026-11-20",
        holiday_date=date(2026, 11, 20),
        name="Recorded holiday",
        source="karnataka",
    )
    yield connection
    connection.close()


@pytest.fixture()
def database(tmp_path: Path) -> Path:
    path = tmp_path / "office.db"
    connection = open_database(path)
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
    for index in range(1, 3):
        masters.upsert_activity(
            connection,
            activity_id=f"A-{index:03d}",
            category="Inspection",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
        )
    # One recorded General Holiday is what tells the readiness gate that a calendar for
    # the year exists at all; without one, no month can close whatever else is ready.
    cal.upsert_holiday(
        connection,
        holiday_id="H-2026-11-20",
        holiday_date=date(2026, 11, 20),
        name="Recorded holiday",
        source="karnataka",
    )
    connection.close()
    return path


@pytest.fixture()
def client(database: Path) -> TestClient:
    return TestClient(create_app(database))


def spec(
    *,
    start: date = date(2026, 11, 1),
    end: date = date(2026, 12, 31),
    cycle_months: int = 2,
    seed: int = 7,
) -> GenerationSpec:
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


def review_every_date(
    db: sqlite3.Connection, run_id: str, *, month: str | None = None
) -> None:
    for item in revisions.review_state(db, run_id)["dates"]:
        if month is not None and not item["date"].startswith(month):
            continue
        revisions.mark_reviewed(
            db,
            run_id,
            date.fromisoformat(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )


def registered(db: sqlite3.Connection, run_id: str) -> str:
    return revision_state.register_for_run(db, run_id) or ""


def month_map(preflight: dict, *, skip: tuple[str, ...] = ()) -> dict[str, dict]:
    """The confirmation a page would send, built from what the preflight showed."""

    return {
        item["month"]: {
            "fingerprint": item["fingerprint"],
            "acknowledged": list(item["unacknowledged_tokens"]),
        }
        for item in preflight["months"]
        if item["month"] in preflight["target_months"] and item["month"] not in skip
    }


def finalisation_count(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COUNT(*) AS total FROM month_finalisation").fetchone()
    return int(row["total"])


def add_holiday(db: sqlite3.Connection, day: date) -> None:
    """A General Holiday recorded after generation, which the calendar has not seen."""

    cal.upsert_holiday(
        db,
        holiday_id=f"H-{day.isoformat()}",
        holiday_date=day,
        name="Recorded later",
        source="karnataka",
    )


def add_sunday_holiday(db: sqlite3.Connection, day: date = date(2026, 11, 1)) -> None:
    """A General Holiday on a weekly off, which the calendar reports as a conflict."""

    cal.upsert_holiday(
        db,
        holiday_id=f"H-{day.isoformat()}",
        holiday_date=day,
        name="Founder's Day",
        source="karnataka",
    )


# --- the preflight ----------------------------------------------------------


def test_the_preflight_names_every_month_of_the_revision_as_a_target(db):
    run_id = stored(db)

    preflight = finalise.bulk_preflight(db, run_id)

    assert preflight["run_id"] == run_id
    assert preflight["is_current"] is True
    assert [item["month"] for item in preflight["months"]] == ["2026-11", "2026-12"]
    assert preflight["target_months"] == ["2026-11", "2026-12"]
    assert preflight["already_finalised"] == []
    # Dates have not been read yet, so neither month may close and the panel says so.
    assert preflight["eligible_months"] == []
    assert preflight["blocked_months"] == ["2026-11", "2026-12"]
    assert preflight["can_finalise"] is False
    codes = [item["code"] for item in preflight["months"][0]["blockers"]]
    assert finalise.C_DATES_NOT_REVIEWED in codes


def test_the_preflight_writes_nothing(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    def reviewed(state: dict) -> int:
        return len([item for item in state["dates"] if item["reviewed_at"]])

    before = db.execute("SELECT COUNT(*) AS total FROM month_finalisation").fetchone()[0]
    reviews_before = reviewed(revisions.review_state(db, run_id))

    finalise.bulk_preflight(db, run_id)

    after = db.execute("SELECT COUNT(*) AS total FROM month_finalisation").fetchone()[0]
    assert after == before
    assert reviewed(revisions.review_state(db, run_id)) == reviews_before
    assert revision_state.month_state(db, registered(db, run_id), "2026-12") is None


def test_the_preflight_reports_a_month_that_can_close_once_its_dates_are_read(db):
    run_id = stored(db)
    review_every_date(db, run_id, month="2026-11")

    preflight = finalise.bulk_preflight(db, run_id)

    assert preflight["eligible_months"] == ["2026-11"]
    assert preflight["blocked_months"] == ["2026-12"]
    assert preflight["can_finalise"] is False


def test_the_preflight_excludes_a_month_this_revision_has_already_closed(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    preflight = finalise.bulk_preflight(db, run_id)

    assert preflight["target_months"] == ["2026-12"]
    assert preflight["already_finalised"] == ["2026-11"]
    closed = next(item for item in preflight["months"] if item["month"] == "2026-11")
    assert closed["sequence"] == 1
    # "Eligible" is a question about closing now, and a closed month is not asked again;
    # the blocker says why, and the month is not part of the act either way.
    assert closed["eligible"] is False
    assert finalise.C_ALREADY_FINALISED in [
        item["code"] for item in closed["blockers"]
    ]


def test_a_month_held_open_by_a_correction_is_a_target_even_though_it_is_issued(db):
    """The target set comes from this revision's own records, not from the pointer.

    A correction revision holds a copy of a month that is already issued, so the register
    says the month has a latest finalisation. It is still open *on this revision*, and a
    bulk close must include it rather than skip it and report success.
    """

    first = stored(db)
    review_every_date(db, first)
    issued = finalise.finalise_month(db, first, "2026-11")
    correction = finalise.create_correction_revision(
        db, first, reason="A date was recorded against the wrong DTC"
    )["correction_run_id"]
    review_every_date(db, correction)

    preflight = finalise.bulk_preflight(db, correction)

    assert preflight["target_months"] == ["2026-11", "2026-12"]
    assert revision_state.month_state(
        db, registered(db, correction), "2026-11"
    ).latest_finalisation_id == issued["finalisation_id"]


def test_the_preflight_lists_the_findings_a_month_needs_acknowledged(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    add_sunday_holiday(db)

    preflight = finalise.bulk_preflight(db, run_id)

    november = preflight["months"][0]
    assert [item["kind"] for item in november["findings"]] == ["calendar_conflict"]
    assert november["findings"][0]["acknowledged"] is False
    assert november["unacknowledged_tokens"] == [november["findings"][0]["token"]]
    assert finalise.C_CONFLICT_NOT_ACKNOWLEDGED in [
        item["code"] for item in november["blockers"]
    ]


# --- the act ----------------------------------------------------------------


def test_every_open_month_is_closed_in_one_act(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    preflight = finalise.bulk_preflight(db, run_id)
    assert preflight["can_finalise"] is True
    confirmation = month_map(preflight)

    result = finalise.finalise_months(
        db,
        run_id,
        fingerprints={month: entry["fingerprint"] for month, entry in confirmation.items()},
        acknowledgements={month: tuple(entry["acknowledged"]) for month, entry in confirmation.items()},
        note="Quarter closed in one sitting",
    )

    assert result["count"] == 2
    assert result["finalised_months"] == ["2026-11", "2026-12"]
    assert [item["sequence"] for item in result["months"]] == [1, 1]
    assert all(item["is_latest"] for item in result["months"])
    assert all(item["note"] == "Quarter closed in one sitting" for item in result["months"])
    # Every month's pointer now names its own record, and nothing closed twice.
    assert finalisation_count(db) == 2
    for item in result["months"]:
        state = revision_state.month_state(db, registered(db, run_id), item["month"])
        assert state.latest_finalisation_id == item["finalisation_id"]


def test_a_blocked_month_refuses_the_whole_act_and_writes_nothing(db):
    run_id = stored(db)
    review_every_date(db, run_id, month="2026-11")  # December is still unread
    preflight = finalise.bulk_preflight(db, run_id)
    confirmation = month_map(preflight)

    with pytest.raises(finalise.FinalisationError) as failure:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints={month: entry["fingerprint"] for month, entry in confirmation.items()},
            acknowledgements={month: entry["acknowledged"] for month, entry in confirmation.items()},
        )

    assert failure.value.constraint == finalise.C_BULK_BLOCKED
    assert "2026-12" in failure.value.detail
    assert finalisation_count(db) == 0
    # November was closable on its own and is still open, which is the whole point.
    assert revision_state.month_state(db, registered(db, run_id), "2026-11") is None


def test_a_failure_while_writing_rolls_back_the_months_already_written(
    db, monkeypatch: pytest.MonkeyPatch
):
    """The act is one transaction, so a late failure cannot leave a partial close."""

    run_id = stored(db)
    review_every_date(db, run_id)
    preflight = finalise.bulk_preflight(db, run_id)
    confirmation = month_map(preflight)
    written: list[str] = []
    original = finalise._write_finalisation

    def flaky(connection, prepared):
        if written:
            raise finalise.FinalisationError(
                finalise.C_CONTENT_CHANGED_WHILE_FINALISING,
                "the second month changed while it was being finalised",
            )
        original(connection, prepared)
        written.append(prepared.record.month)

    monkeypatch.setattr(finalise, "_write_finalisation", flaky)

    with pytest.raises(finalise.FinalisationError):
        finalise.finalise_months(
            db,
            run_id,
            fingerprints={month: entry["fingerprint"] for month, entry in confirmation.items()},
            acknowledgements={month: entry["acknowledged"] for month, entry in confirmation.items()},
        )

    assert written == ["2026-11"]
    assert finalisation_count(db) == 0
    assert revision_state.month_state(db, registered(db, run_id), "2026-11") is None


def test_a_month_that_does_not_match_the_fingerprint_that_was_shown_is_refused(db):
    """What the operator saw is what is closed; a stale page closes nothing."""

    run_id = stored(db)
    review_every_date(db, run_id)
    confirmation = month_map(finalise.bulk_preflight(db, run_id))
    confirmation["2026-12"]["fingerprint"] = "0" * 64

    with pytest.raises(finalise.FinalisationError) as failure:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints={month: entry["fingerprint"] for month, entry in confirmation.items()},
            acknowledgements={month: entry["acknowledged"] for month, entry in confirmation.items()},
        )

    assert failure.value.constraint == finalise.C_BULK_FINGERPRINT_STALE
    assert "2026-12" in failure.value.detail
    assert finalisation_count(db) == 0


def test_a_month_changed_after_its_dates_were_read_is_refused(db):
    """A holiday recorded after the review moves the calendar, so the reviews are stale."""

    run_id = stored(db)
    review_every_date(db, run_id)
    confirmation = month_map(finalise.bulk_preflight(db, run_id))
    add_holiday(db, date(2026, 12, 15))  # a working day becomes a holiday

    preflight = finalise.bulk_preflight(db, run_id)

    assert preflight["can_finalise"] is False
    codes = [
        item["code"]
        for month in preflight["months"]
        for item in month["blockers"]
    ]
    assert finalise.C_CALENDAR_CHANGED in codes or finalise.C_REVIEW_STALE in codes
    with pytest.raises(finalise.FinalisationError) as failure:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints={month: entry["fingerprint"] for month, entry in confirmation.items()},
            acknowledgements={month: entry["acknowledged"] for month, entry in confirmation.items()},
        )
    assert failure.value.constraint in (
        finalise.C_BULK_BLOCKED,
        finalise.C_BULK_FINGERPRINT_STALE,
    )
    assert finalisation_count(db) == 0


def test_the_month_set_must_be_what_the_server_derived(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    preflight = finalise.bulk_preflight(db, run_id)
    confirmation = month_map(preflight)

    with pytest.raises(finalise.FinalisationError) as missing:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints={"2026-11": confirmation["2026-11"]["fingerprint"]},
            acknowledgements={},
        )
    assert missing.value.constraint == finalise.C_BULK_TARGETS_MISMATCH

    with pytest.raises(finalise.FinalisationError) as extra:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints={
                month: entry["fingerprint"] for month, entry in confirmation.items()
            }
            | {"2027-01": "0" * 64},
            acknowledgements={},
        )
    assert extra.value.constraint == finalise.C_BULK_TARGETS_MISMATCH

    assert finalisation_count(db) == 0


def test_an_acknowledgement_for_a_month_outside_the_act_is_refused(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    preflight = finalise.bulk_preflight(db, run_id)
    confirmation = month_map(preflight)

    with pytest.raises(finalise.FinalisationError) as failure:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints={month: entry["fingerprint"] for month, entry in confirmation.items()},
            acknowledgements={"2027-03": ("tok",)},
        )

    assert failure.value.constraint == finalise.C_BULK_ACKNOWLEDGEMENT_UNKNOWN
    assert finalisation_count(db) == 0


def test_an_act_with_nothing_open_is_refused_rather_than_reported_as_success(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")
    finalise.finalise_month(db, run_id, "2026-12")

    with pytest.raises(finalise.FinalisationError) as failure:
        finalise.finalise_months(db, run_id, fingerprints={}, acknowledgements={})

    assert failure.value.constraint == finalise.C_BULK_NO_TARGETS
    assert finalisation_count(db) == 2


def test_an_acknowledged_finding_is_recorded_against_every_month_that_has_it(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    add_sunday_holiday(db)
    preflight = finalise.bulk_preflight(db, run_id)
    confirmation = month_map(preflight)
    token = preflight["months"][0]["findings"][0]["token"]
    assert token in confirmation["2026-11"]["acknowledged"]

    result = finalise.finalise_months(
        db,
        run_id,
        fingerprints={month: entry["fingerprint"] for month, entry in confirmation.items()},
        acknowledgements={month: tuple(entry["acknowledged"]) for month, entry in confirmation.items()},
    )

    for item in result["months"]:
        assert [entry["token"] for entry in item["acknowledged"]] == [token]


def test_a_bulk_close_on_a_correction_keeps_the_history_it_corrected(db):
    first = stored(db)
    review_every_date(db, first)
    issued = finalise.finalise_month(db, first, "2026-11")
    correction = finalise.create_correction_revision(
        db, first, reason="A date was recorded against the wrong DTC"
    )["correction_run_id"]
    review_every_date(db, correction)
    preflight = finalise.bulk_preflight(db, correction)
    confirmation = month_map(preflight)

    result = finalise.finalise_months(
        db,
        correction,
        fingerprints={month: entry["fingerprint"] for month, entry in confirmation.items()},
        acknowledgements={month: entry["acknowledged"] for month, entry in confirmation.items()},
    )

    november = next(item for item in result["months"] if item["month"] == "2026-11")
    assert november["sequence"] == 2
    assert november["correction_of_run_id"] == first
    assert november["parent_finalisation_id"] == issued["finalisation_id"]
    # The version it replaced still exports its own snapshot and is no longer in force.
    superseded = finalise.finalisation_record(db, issued["finalisation_id"])
    assert superseded["is_latest"] is False
    assert superseded["content"] == issued["content"]
    # The month is no longer held open by an unfinished correction.
    assert finalise.open_correction_of(db, registered(db, correction), "2026-11") is None


def test_a_part_month_is_named_as_a_blocker_and_never_closed(db):
    """Generation refuses a part-month, so this shape is built below the application.

    The preflight still has to name it rather than assume it cannot happen: a stored
    revision that lost its first days is exactly the kind of state a partial close would
    turn into a record of the wrong period.
    """

    run_id = stored(db, start=date(2026, 11, 1), end=date(2026, 11, 30), cycle_months=1)
    review_every_date(db, run_id)
    db.execute("DELETE FROM generation_row WHERE run_id = ? AND row_date < '2026-11-05'", (run_id,))
    db.commit()

    preflight = finalise.bulk_preflight(db, run_id)

    assert preflight["target_months"] == ["2026-11"]
    assert finalise.C_PARTIAL_MONTH_UNSUPPORTED in [
        item["code"] for item in preflight["months"][0]["blockers"]
    ]
    assert preflight["can_finalise"] is False
    with pytest.raises(finalise.FinalisationError) as failure:
        finalise.finalise_months(
            db,
            run_id,
            fingerprints={"2026-11": preflight["months"][0]["fingerprint"]},
            acknowledgements={},
        )
    assert failure.value.constraint == finalise.C_BULK_BLOCKED
    assert finalisation_count(db) == 0


def test_closing_one_month_on_its_own_still_behaves_exactly_as_before(db):
    """The shared preparation must not change the single-month contract."""

    run_id = stored(db)
    review_every_date(db, run_id)

    first = finalise.finalise_month(db, run_id, "2026-11")
    assert first["sequence"] == 1

    with pytest.raises(finalise.FinalisationError) as again:
        finalise.finalise_month(db, run_id, "2026-11")
    assert again.value.constraint == finalise.C_ALREADY_FINALISED

    # The single-month close of the neighbour still works afterwards.
    second = finalise.finalise_month(db, run_id, "2026-12")
    assert second["sequence"] == 1
    assert finalisation_count(db) == 2


# --- the interface ----------------------------------------------------------


def test_the_preflight_route_reports_the_same_targets(client: TestClient, database: Path):
    connection = open_database(database)
    try:
        run_id = stored(connection)
        review_every_date(connection, run_id)
    finally:
        connection.close()

    answer = client.get(f"/api/generations/{run_id}/finalisation/preflight")

    assert answer.status_code == 200
    body = answer.json()
    assert body["target_months"] == ["2026-11", "2026-12"]
    assert body["can_finalise"] is True
    assert body["months"][0]["fingerprint"]


def test_the_bulk_route_closes_every_month_in_one_request(
    client: TestClient, database: Path
):
    connection = open_database(database)
    try:
        run_id = stored(connection)
        review_every_date(connection, run_id)
        preflight = finalise.bulk_preflight(connection, run_id)
        confirmation = month_map(preflight)
    finally:
        connection.close()

    answer = client.post(
        f"/api/generations/{run_id}/finalisation/bulk",
        json={"months": confirmation, "note": "Quarter closed"},
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["count"] == 2
    assert body["finalised_months"] == ["2026-11", "2026-12"]

    connection = open_database(database)
    try:
        after = finalise.bulk_preflight(connection, run_id)
    finally:
        connection.close()
    assert after["already_finalised"] == ["2026-11", "2026-12"]
    assert after["target_months"] == []
    assert after["can_finalise"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"months": {}},
        {"months": {"2026-11": "fingerprint"}},
        {"months": {"2026-11": {"acknowledged": []}}},
        {"months": {"2026-11": {"fingerprint": "abc", "acknowledged": "tok"}}},
    ],
)
def test_a_malformed_bulk_request_is_a_client_error(
    client: TestClient, database: Path, payload: dict
):
    connection = open_database(database)
    try:
        run_id = stored(connection)
    finally:
        connection.close()

    answer = client.post(f"/api/generations/{run_id}/finalisation/bulk", json=payload)

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == finalise.C_BULK_TARGETS_MISMATCH


def test_the_bulk_route_refuses_an_unknown_revision(client: TestClient):
    answer = client.post(
        "/api/generations/no-such-run/finalisation/bulk",
        json={"months": {"2026-11": {"fingerprint": "0" * 64, "acknowledged": []}}},
    )

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == revisions.C_REVISION_UNKNOWN


def test_the_bulk_route_returns_every_blocker_of_every_blocked_month(
    client: TestClient, database: Path
):
    connection = open_database(database)
    try:
        run_id = stored(connection)
        review_every_date(connection, run_id, month="2026-11")
        preflight = finalise.bulk_preflight(connection, run_id)
        confirmation = month_map(preflight)
    finally:
        connection.close()

    answer = client.post(
        f"/api/generations/{run_id}/finalisation/bulk", json={"months": confirmation}
    )

    assert answer.status_code == 400
    error = answer.json()["error"]
    assert error["constraint"] == finalise.C_BULK_BLOCKED
    assert "2026-12" in error["detail"]
    assert [item["code"] for item in error["blockers"]].count(
        finalise.C_DATES_NOT_REVIEWED
    ) == 1

    connection = open_database(database)
    try:
        assert finalisation_count(connection) == 0
    finally:
        connection.close()


def test_a_second_identical_request_closes_nothing_a_second_time(
    client: TestClient, database: Path
):
    connection = open_database(database)
    try:
        run_id = stored(connection)
        review_every_date(connection, run_id)
        confirmation = month_map(finalise.bulk_preflight(connection, run_id))
    finally:
        connection.close()

    assert client.post(
        f"/api/generations/{run_id}/finalisation/bulk", json={"months": confirmation}
    ).status_code == 200
    again = client.post(
        f"/api/generations/{run_id}/finalisation/bulk", json={"months": confirmation}
    )

    assert again.status_code == 400
    assert again.json()["error"]["constraint"] == finalise.C_BULK_NO_TARGETS
    connection = open_database(database)
    try:
        assert finalisation_count(connection) == 2
    finally:
        connection.close()


def test_the_closed_months_are_exportable_straight_after_a_bulk_close(
    client: TestClient, database: Path
):
    connection = open_database(database)
    try:
        run_id = stored(connection)
        review_every_date(connection, run_id)
        confirmation = month_map(finalise.bulk_preflight(connection, run_id))
    finally:
        connection.close()
    closed = client.post(
        f"/api/generations/{run_id}/finalisation/bulk", json={"months": confirmation}
    ).json()

    for record in closed["months"]:
        workbook = client.get(f"/api/finalisations/{record['finalisation_id']}/export.xlsx")
        printed = client.get(f"/finalisations/{record['finalisation_id']}/print")
        assert workbook.status_code == 200, record["month"]
        assert printed.status_code == 200, record["month"]
        assert record["month"] in printed.text


# --- the page ---------------------------------------------------------------


def test_the_preflight_separates_a_finding_from_a_blocker(db):
    """The page must not confuse "not ticked yet" with "cannot be done"."""

    run_id = stored(db)
    review_every_date(db, run_id)
    add_sunday_holiday(db)

    preflight = finalise.bulk_preflight(db, run_id)

    assert preflight["can_finalise"] is False
    assert preflight["can_finalise_after_acknowledgements"] is True
    assert preflight["hard_blocked_months"] == []
    for item in preflight["months"]:
        assert item["hard_blockers"] == []
        assert item["eligible_after_acknowledgements"] is True
        assert item["eligible"] is False
    tokens = [(item["month"], item["token"]) for item in preflight["pending_acknowledgements"]]
    assert [item["month"] for item in preflight["pending_acknowledgements"]] == [
        "2026-11",
        "2026-12",
    ]
    assert all(token.startswith("calendar_conflict|") for _, token in tokens)


def test_the_preflight_names_a_blocker_no_acknowledgement_can_clear(db):
    run_id = stored(db)
    review_every_date(db, run_id, month="2026-11")

    preflight = finalise.bulk_preflight(db, run_id)

    assert preflight["hard_blocked_months"] == ["2026-12"]
    assert preflight["can_finalise_after_acknowledgements"] is False
    assert preflight["pending_acknowledgements"] == []
    december = next(item for item in preflight["months"] if item["month"] == "2026-12")
    assert finalise.C_DATES_NOT_REVIEWED in [item["code"] for item in december["hard_blockers"]]


def test_a_month_already_closed_is_not_eligible_even_with_nothing_acknowledged(db):
    run_id = stored(db)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    preflight = finalise.bulk_preflight(db, run_id)

    november = next(item for item in preflight["months"] if item["month"] == "2026-11")
    assert november["already_finalised"] is True
    assert november["eligible_after_acknowledgements"] is False
    assert november["month"] not in preflight["pending_acknowledgements"]
    assert preflight["target_months"] == ["2026-12"]


def test_the_page_offers_one_register_level_bulk_close():
    script = (REQUEST / "dailyworkreg/interfaces/static/app.js").read_text(
        encoding="utf-8"
    )

    assert "async function bulkFinalisationPanel(" in script
    assert "finalisation/preflight" in script
    assert "finalisation/bulk" in script
    assert "Finalize all months" in script
    # Reached from the month panel rather than from a hidden menu.
    assert "target.append(await bulkFinalisationPanel(registerId, runId));" in script


def test_the_page_shows_the_targets_the_blockers_and_the_acknowledgements():
    script = (REQUEST / "dailyworkreg/interfaces/static/app.js").read_text(
        encoding="utf-8"
    )
    panel = script.split("async function bulkFinalisationPanel(", 1)[1].split(
        "async function renderMonths(", 1
    )[0]

    assert "'Month', 'Dates read', 'Allocation', 'Findings', 'Status'" in panel
    assert "What is in the way, and cannot be cleared here" in panel
    assert "Recorded findings to acknowledge" in panel
    assert "'data-month': item.month" in panel
    assert "already_finalised" in panel


def test_the_page_cannot_submit_the_bulk_close_twice():
    script = (REQUEST / "dailyworkreg/interfaces/static/app.js").read_text(
        encoding="utf-8"
    )
    panel = script.split("async function bulkFinalisationPanel(", 1)[1].split(
        "async function renderMonths(", 1
    )[0]

    # One act, one request: the guard refuses a click that is not a ready act, and the
    # flag is set before the request leaves so a second click cannot start another.
    assert "if (submitting || evaluate() !== BULK_STATE.READY) return;" in panel
    assert "submitting = true;" in panel
    # declared, then cleared on the success path and again after a refusal
    assert panel.count("submitting = false") == 3
    assert "button.textContent = submitting ? 'Closing…'" in panel
    # A refusal does not leave a dead panel: it marks the checks out of date and offers
    # the refresh that reads them again.
    assert "phase = BULK_STATE.STALE;" in panel
    assert "Refresh finalisation checks" in panel
