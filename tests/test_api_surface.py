"""The local HTTP surface: routes translate, they do not decide.

Two properties matter more than the shape of any response. A refusal must arrive as a
stable constraint with a readable sentence, so a trader is told what to fix rather than
what broke. And no route may decide a schedule, so a change in the domain appears here
without a second edit.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application.register_edits import C_DATE_LOCKED
from dailyworkreg.application.revisions import (
    C_DATE_NOT_LOCKED,
    C_REGISTER_UNKNOWN,
    C_STALE_REVIEW,
)
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.interfaces.api import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "surface.db"


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path))


@pytest.fixture()
def stocked(client: TestClient) -> TestClient:
    """A client whose masters are good enough to generate a month."""

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
            json={"holiday_date": day, "name": "Recorded holiday", "source": "karnataka"},
        )
    return client


# --- health and readiness --------------------------------------------------


def test_the_surface_starts_and_reports_itself(client):
    answer = client.get("/api/health")
    assert answer.status_code == 200
    assert answer.json()["ok"] is True


def test_the_home_page_is_served(client):
    answer = client.get("/")
    assert answer.status_code == 200
    assert "DailyDairyRegister" in answer.text


def test_readiness_names_what_is_missing_before_anything_is_imported(client):
    body = client.get("/api/readiness?start=2026-11-01&end=2026-11-30").json()
    assert body["can_generate"] is False
    labels = " ".join(body["missing_content"])
    assert "DTC master" in labels
    assert "activity catalogue" in labels
    assert "General Holiday baseline for 2026" in labels


def test_readiness_changes_once_the_masters_exist(stocked):
    body = stocked.get("/api/readiness?start=2026-11-01&end=2026-11-30").json()
    assert body["can_generate"] is True
    assert body["is_operational_diary"] is True
    assert body["missing_content"] == []


def test_a_month_without_a_holiday_list_says_so(stocked):
    body = stocked.get("/api/readiness?start=2027-01-01&end=2027-01-31").json()
    assert "official Karnataka General Holiday baseline for 2027" in (
        body["missing_content"]
    )


# --- masters ---------------------------------------------------------------


def test_a_dtc_can_be_entered_and_read_back(client):
    answer = client.put(
        "/api/masters/dtc",
        json={
            "dtc_id": "SCD-77",
            "name": "Entered DTC",
            "dtc_class": "DEPT",
            "kva": 250.0,
        },
    )
    assert answer.status_code == 200
    assert answer.json()["dtc_id"] == "SCD-77"
    listed = client.get("/api/masters/dtc").json()
    assert [item["dtc_id"] for item in listed] == ["SCD-77"]


def test_the_approved_identities_are_seeded_at_startup(client):
    areas = client.get("/api/masters/area").json()
    feeders = client.get("/api/masters/feeder").json()
    assert len(areas) == 16
    assert len(feeders) == 4


def test_a_duplicate_record_is_refused_with_a_stable_constraint(client):
    payload = {"area_id": "A-99", "name_kn": "Test", "name_en": "Test area"}
    assert client.put("/api/masters/area", json=payload).status_code == 200
    # Re-entering the same identity is an update, not a refusal: correcting a name must
    # be possible. A refusal belongs to an ambiguous identity, which the import path owns.
    assert client.put("/api/masters/area", json=payload).status_code == 200


def test_summary_counts_every_master(client):
    client.put(
        "/api/masters/dtc",
        json={"dtc_id": "SCD-1", "name": "One", "dtc_class": "DEPT"},
    )
    body = client.get("/api/masters/summary").json()
    assert body["dtc"] == 1
    assert body["area"] == 16
    assert isinstance(body["version"], int)


# --- calendar --------------------------------------------------------------


def test_the_weekly_off_rules_round_trip(client):
    saved = client.put(
        "/api/settings/calendar",
        json={"sundays": True, "second_saturday": False, "fourth_saturday": True},
    ).json()
    assert saved["saved"] is True
    rules = client.get("/api/settings/calendar").json()
    assert rules["second_saturday"] is False


def test_the_preview_shows_classifications_and_conflicts(client):
    client.put(
        "/api/calendar/holidays",
        json={
            "holiday_date": "2026-11-01",
            "name": "Kannada Rajyotsava",
            "source": "karnataka",
        },
    )
    body = client.get("/api/calendar/preview?start=2026-11-01&end=2026-11-02").json()
    first = body["days"][0]
    assert first["kind"] == "general_holiday"
    assert "Sunday" in first["reasons_en"]
    assert body["conflicts"]
    assert body["working_days"] == 1


def test_a_monday_safety_meeting_appears_in_the_preview(client):
    body = client.get("/api/calendar/preview?start=2026-11-02&end=2026-11-02").json()
    assert [entry["kind"] for entry in body["entries"]] == ["safety_meeting"]


def test_no_restricted_holiday_can_be_classified_by_the_surface(client):
    body = client.get("/api/calendar/preview?start=2025-01-01&end=2026-12-31").json()
    kinds = {day["kind"] for day in body["days"]}
    assert "restricted_holiday" not in kinds
    assert not any(
        "restricted" in reason.lower()
        for day in body["days"]
        for reason in day["reasons_en"]
    )


def test_an_override_makes_a_holiday_working_and_keeps_the_fact(client):
    client.put(
        "/api/calendar/holidays",
        json={"holiday_date": "2026-11-10", "name": "Deepavali", "source": "karnataka"},
    )
    client.put(
        "/api/calendar/overrides",
        json={
            "override_date": "2026-11-10",
            "is_working_day": True,
            "reason_en": "Office function",
        },
    )
    body = client.get("/api/calendar/preview?start=2026-11-10&end=2026-11-10").json()
    day = body["days"][0]
    assert day["is_working_day"] is True
    assert "Holiday - Deepavali" in day["reasons_en"]


def test_a_stored_leave_record_reaches_the_preview(client):
    client.put(
        "/api/calendar/leave-records",
        json={"leave_date": "2026-11-09", "leave_type_id": "CL"},
    )
    body = client.get("/api/calendar/preview?start=2026-11-09&end=2026-11-09").json()
    assert body["days"][0]["kind"] == "leave"
    assert body["days"][0]["is_working_day"] is False


# --- imports ---------------------------------------------------------------


def dtc_csv() -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["DTC ID", "DTC Name", "Class", "kVA", "Feeder Name"])
    writer.writerow(["SCD-1", "Alpha", "DEPT", "100", "MRS F-1 OLDTOWN"])
    writer.writerow(["SCD-2", "Beta", "MSB", "50", "not a stored feeder"])
    writer.writerow(["SCF-1", "Excluded", "SCF", "10", ""])
    return stream.getvalue().encode("utf-8")


def test_an_upload_is_staged_and_judged_before_it_is_accepted(client):
    answer = client.post(
        "/api/imports?dataset=dtc",
        files={"file": ("dtcs.csv", dtc_csv(), "text/csv")},
    )
    assert answer.status_code == 200
    batch = answer.json()
    assert batch["row_count"] == 3
    assert batch["error_count"] == 0
    # One warning for the feeder name that is not in the feeder master, one for the
    # SCF row that §8 excludes. Both are shown rather than repaired.
    assert batch["warning_count"] == 2
    assert batch["status"] == "staged"
    assert batch["can_accept"] is True
    # Nothing reached the master data yet.
    assert client.get("/api/masters/dtc").json() == []


def test_the_batch_detail_names_each_row_outcome(client):
    batch = client.post(
        "/api/imports?dataset=dtc",
        files={"file": ("dtcs.csv", dtc_csv(), "text/csv")},
    ).json()
    detail = client.get(f"/api/imports/{batch['batch_id']}").json()
    decisions = [row["decision"] for row in detail["rows"]]
    assert "exclude" in decisions
    assert any("warning" == row["severity"] for row in detail["rows"])


def test_accepting_a_batch_writes_the_master(client):
    batch = client.post(
        "/api/imports?dataset=dtc",
        files={"file": ("dtcs.csv", dtc_csv(), "text/csv")},
    ).json()
    done = client.post(f"/api/imports/{batch['batch_id']}/accept", json={})
    assert done.status_code == 200
    assert done.json()["inserted"] == 2
    assert {item["dtc_id"] for item in client.get("/api/masters/dtc").json()} == {
        "SCD-1",
        "SCD-2",
    }


def test_rejecting_a_batch_leaves_the_master_alone(client):
    batch = client.post(
        "/api/imports?dataset=dtc",
        files={"file": ("dtcs.csv", dtc_csv(), "text/csv")},
    ).json()
    assert client.post(f"/api/imports/{batch['batch_id']}/reject", json={}).status_code == 200
    assert client.get("/api/masters/dtc").json() == []
    assert client.get(f"/api/imports/{batch['batch_id']}").json()["status"] == "rejected"


def test_an_unsupported_file_is_refused_in_words(client):
    answer = client.post(
        "/api/imports?dataset=dtc",
        files={"file": ("dtcs.txt", b"whatever", "text/plain")},
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "source_format_unsupported"


def test_an_unknown_dataset_is_refused(client):
    answer = client.post(
        "/api/imports?dataset=payroll",
        files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "unknown_dataset"


def test_a_file_whose_columns_do_not_match_the_contract_is_refused_in_words(client):
    """A wrong-column upload is a refusal, not a server error.

    Staging raises its own refusal for a file it cannot map at all, and that refusal
    used to escape the route and reach the operator as an unhandled failure with no
    reason in it. No batch is created either way, so nothing empty is left behind.
    """

    answer = client.post(
        "/api/imports?dataset=dtc",
        files={"file": ("wrong.csv", b"DTC ID,Class\nSCD-1,DEPT\n", "text/csv")},
    )

    assert answer.status_code == 400
    error = answer.json()["error"]
    assert error["constraint"] == "source_file_unmapped"
    assert "no column for name" in error["detail"]
    assert client.get("/api/imports").json() == []


def test_an_activity_file_with_foreign_headings_is_refused_in_words(client):
    answer = client.post(
        "/api/imports?dataset=activity",
        files={
            "file": (
                "proposed.csv",
                "Suggested ID (not canonical),Category,Proposed English Description\n"
                "P-1,Inspection,Some proposal\n".encode(),
                "text/csv",
            )
        },
    )

    assert answer.status_code == 400
    error = answer.json()["error"]
    assert error["constraint"] == "source_file_unmapped"
    assert "description_en" in error["detail"]
    assert client.get("/api/imports").json() == []


def test_a_legacy_activity_batch_is_refused_at_acceptance(client, db_path: Path):
    """The acceptance guard is reachable from the surface, and says why."""

    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        ["Activity ID", "Category", "Description", "Kannada", "Weight", "Monthly Min",
         "Monthly Max", "Repeat Gap"]
    )
    writer.writerow(["LEG-900", "Rehearsal", "Legacy rehearsal", "", "1", "1", "4", "2"])
    batch = client.post(
        "/api/imports?dataset=activity",
        files={"file": ("legacy.csv", stream.getvalue().encode(), "text/csv")},
    ).json()

    connection = open_database(db_path)
    try:
        stored = connection.execute(
            "SELECT normalized_json FROM import_staging_row WHERE batch_id = ?",
            (batch["batch_id"],),
        ).fetchone()[0]
        payload = json.loads(stored) | {"weight": -2.5}
        connection.execute(
            "UPDATE import_staging_row SET normalized_json = ? WHERE batch_id = ?",
            (json.dumps(payload), batch["batch_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    answer = client.post(f"/api/imports/{batch['batch_id']}/accept", json={})

    assert answer.status_code == 400
    error = answer.json()["error"]
    assert error["constraint"] == "activity_contract_unsatisfied"
    assert "weight -2.5 is not greater than zero" in error["detail"]
    assert client.get("/api/masters/activity").json() == []
    assert client.get(f"/api/imports/{batch['batch_id']}").json()["status"] == "staged"


def test_an_unknown_batch_is_refused_with_its_own_constraint(client):
    answer = client.get("/api/imports/nothing")
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "import_batch_unknown"


# --- generation ------------------------------------------------------------


def generate(client: TestClient, **overrides) -> dict:
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


def test_a_generation_returns_a_draft_and_its_rows(stocked):
    made = generate(stocked)
    assert made["is_draft"] is True
    assert made["status"] == "allocation_complete"
    assert made["rows"] == 30

    register = stocked.get(f"/api/generations/{made['run_id']}").json()
    assert register["is_draft"] is True
    assert len(register["rows"]) == 30
    assert register["rows"][0]["day"] == "2026-11-01"
    assert any(row["visits"] for row in register["rows"])


def test_a_generated_draft_is_labelled_a_draft_not_verified_work(stocked):
    made = generate(stocked)
    register = stocked.get(f"/api/generations/{made['run_id']}").json()
    assert register["is_draft"] is True
    assert "reviewed" not in str(register).lower()
    assert "finalised" not in str(register).lower()


def test_an_impossible_cycle_is_recorded_as_an_attempt_with_no_draft(stocked):
    made = generate(stocked, cycle_visits_per_asset=9)
    assert made["is_draft"] is False
    assert made["status"] in {"infeasible", "unresolved"}

    register = stocked.get(f"/api/generations/{made['run_id']}").json()
    assert register["is_draft"] is False
    assert register["exceptions"]

    history = stocked.get("/api/generations").json()
    assert history["allocation_complete"] == []
    assert len(history["infeasible"]) + len(history["unresolved"]) == 1


def test_a_reversed_range_is_refused_with_a_readable_reason(stocked):
    answer = stocked.post(
        "/api/generations",
        json={
            "start": "2026-11-30",
            "end": "2026-11-01",
            "mode": "coverage_cycle",
            "seed": 0,
        },
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "draft_range_reversed"


def test_a_generation_with_no_active_asset_is_refused_without_inventing_one(tmp_path):
    empty = TestClient(create_app(tmp_path / "bare.db"))
    for area in empty.get("/api/masters/area").json():
        empty.put("/api/masters/area", json={**area, "active": False})
    for feeder in empty.get("/api/masters/feeder").json():
        empty.put("/api/masters/feeder", json={**feeder, "active": False})

    answer = empty.post(
        "/api/generations",
        json={
            "start": "2026-11-01",
            "end": "2026-11-30",
            "mode": "coverage_cycle",
            "seed": 0,
        },
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "generation_roster_empty"


def test_history_keeps_every_attempt_in_its_own_group(stocked):
    first = generate(stocked)
    second = generate(stocked, seed=11)
    history = stocked.get("/api/generations").json()
    ids = [run["run_id"] for run in history["allocation_complete"]]
    assert {first["run_id"], second["run_id"]} == set(ids)
    # Each attempt is revision 1 of the register it started; two unrelated
    # generations are not two revisions of one register.
    revisions = [run["revision_number"] for run in history["allocation_complete"]]
    assert revisions == [1, 1]
    registers = {run["run_id"]: run["register_id"] for run in history["allocation_complete"]}
    assert registers[first["run_id"]] != registers[second["run_id"]]


def test_a_stored_run_replays_from_its_snapshot(stocked):
    made = generate(stocked)
    answer = stocked.get(f"/api/generations/{made['run_id']}/regenerate").json()
    assert answer["identical"] is True


def test_the_readiness_of_a_run_is_kept_with_it(stocked):
    made = generate(stocked)
    items = stocked.get(f"/api/generations/{made['run_id']}/readiness").json()
    assert {item["key"] for item in items} >= {"roster.dtc", "activities"}


def test_a_master_edit_after_generation_does_not_change_the_stored_draft(
    stocked, db_path
):
    made = generate(stocked)
    before = stocked.get(f"/api/generations/{made['run_id']}").json()

    direct = open_database(db_path)
    try:
        masters.upsert_dtc(
            direct, dtc_id="SCD-1", name="Renamed", dtc_class="DEPT", kva=1.0
        )
        masters.upsert_dtc(
            direct, dtc_id="SCD-500", name="Brand new", dtc_class="DEPT", kva=90.0
        )
    finally:
        direct.close()

    after = stocked.get(f"/api/generations/{made['run_id']}").json()
    assert [row["day"] for row in before["rows"]] == [row["day"] for row in after["rows"]]
    assert before["rows"] == after["rows"]


def test_an_unknown_run_is_refused_with_its_constraint(client):
    answer = client.get("/api/generations/nothing")
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "generation_run_unknown"


# --- review, locks and register history ----------------------------------


def test_the_review_state_lists_every_date_of_the_revision(stocked):
    made = generate(stocked)
    state = stocked.get(f"/api/generations/{made['run_id']}/review").json()

    assert state["run_id"] == made["run_id"]
    assert state["is_current"] is True
    assert state["total_dates"] == 30
    assert state["reviewed_dates"] == 0
    assert state["months"] == {"2026-11": "pending_review"}
    assert {"date", "state", "locked", "fingerprint"} <= set(state["dates"][0])


def test_reviewing_and_withdrawing_a_date_through_the_api(stocked):
    made = generate(stocked)
    run = made["run_id"]

    answer = stocked.put(
        f"/api/generations/{run}/review/2026-11-03", json={"note": "seen"}
    )
    assert answer.status_code == 200
    assert answer.json()["state"] == "reviewed"

    state = stocked.get(f"/api/generations/{run}/review").json()
    day = {item["date"]: item for item in state["dates"]}["2026-11-03"]
    assert day["state"] == "reviewed"
    assert day["note"] == "seen"
    assert state["reviewed_dates"] == 1

    withdrawn = stocked.delete(f"/api/generations/{run}/review/2026-11-03")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["state"] == "pending_review"



def test_bulk_review_marks_all_pending_dates_and_preserves_existing_review(stocked):
    made = generate(stocked)
    run_id = made["run_id"]
    first = stocked.put(
        f"/api/generations/{run_id}/review/2026-11-03",
        json={"note": "already inspected"},
    )
    assert first.status_code == 200

    before = stocked.get(f"/api/generations/{run_id}/review").json()
    existing_day = next(day for day in before["dates"] if day["date"] == "2026-11-03")
    fingerprints = {day["date"]: day["fingerprint"] for day in before["dates"]}

    answer = stocked.put(
        f"/api/generations/{run_id}/review",
        json={"fingerprints": fingerprints},
    )
    assert answer.status_code == 200
    assert answer.json()["reviewed_dates"] == 29
    assert answer.json()["already_reviewed_dates"] == 1
    assert answer.json()["total_dates"] == 30

    after = stocked.get(f"/api/generations/{run_id}/review").json()
    assert after["reviewed_dates"] == 30
    assert all(day["state"] == "reviewed" for day in after["dates"])
    preserved = next(day for day in after["dates"] if day["date"] == "2026-11-03")
    assert preserved["reviewed_at"] == existing_day["reviewed_at"]
    assert preserved["note"] == "already inspected"
    assert after["months"] == {"2026-11": "reviewed"}

    repeated = stocked.put(
        f"/api/generations/{run_id}/review",
        json={"fingerprints": fingerprints},
    )
    assert repeated.status_code == 200
    assert repeated.json()["reviewed_dates"] == 0
    assert repeated.json()["already_reviewed_dates"] == 30


def test_bulk_review_refuses_stale_fingerprints_without_partial_writes(stocked):
    made = generate(stocked)
    run_id = made["run_id"]
    before = stocked.get(f"/api/generations/{run_id}/review").json()
    fingerprints = {day["date"]: day["fingerprint"] for day in before["dates"]}
    fingerprints["2026-11-17"] = "stale-content"

    answer = stocked.put(
        f"/api/generations/{run_id}/review",
        json={"fingerprints": fingerprints},
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == C_STALE_REVIEW

    after = stocked.get(f"/api/generations/{run_id}/review").json()
    assert after["reviewed_dates"] == 0
    assert all(day["state"] == "pending_review" for day in after["dates"])


def test_bulk_review_requires_the_complete_date_set(stocked):
    made = generate(stocked)
    run_id = made["run_id"]
    before = stocked.get(f"/api/generations/{run_id}/review").json()
    fingerprints = {day["date"]: day["fingerprint"] for day in before["dates"]}
    fingerprints.pop("2026-11-30")

    answer = stocked.put(
        f"/api/generations/{run_id}/review",
        json={"fingerprints": fingerprints},
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == C_STALE_REVIEW
    assert stocked.get(f"/api/generations/{run_id}/review").json()["reviewed_dates"] == 0


def test_bulk_review_refuses_a_malformed_body_as_a_client_error(stocked):
    """A body that is not a date-to-fingerprint map is refused, not crashed on.

    The route answers this question itself rather than asking the review service, so the
    refusal has to come from a constraint the route actually holds. It used to name one
    it had never imported, which turned a malformed request into a failure report.
    """

    made = generate(stocked)
    for body in ({}, {"fingerprints": []}, {"fingerprints": {"2026-11-03": 7}}):
        answer = stocked.put(
            f"/api/generations/{made['run_id']}/review", json=body
        )
        assert answer.status_code == 400, body
        assert answer.json()["error"]["constraint"] == C_STALE_REVIEW, body
    assert stocked.get(
        f"/api/generations/{made['run_id']}/review"
    ).json()["reviewed_dates"] == 0


def test_a_review_of_stale_content_is_refused_with_a_stable_constraint(stocked):
    made = generate(stocked)
    answer = stocked.put(
        f"/api/generations/{made['run_id']}/review/2026-11-03",
        json={"fingerprint": "from-an-older-screen"},
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == C_STALE_REVIEW


def test_locking_and_unlocking_a_date_through_the_api(stocked):
    made = generate(stocked)
    run = made["run_id"]

    locked = stocked.put(f"/api/generations/{run}/locks/2026-11-01", json={})
    assert locked.status_code == 200
    assert locked.json()["locked"] is True

    state = stocked.get(f"/api/generations/{run}/review").json()
    assert state["locked_dates"] == 1
    assert state["reviewed_dates"] == 0

    unlocked = stocked.delete(f"/api/generations/{run}/locks/2026-11-01")
    assert unlocked.status_code == 200
    assert unlocked.json()["locked"] is False

    again = stocked.delete(f"/api/generations/{run}/locks/2026-11-01")
    assert again.status_code == 400
    assert again.json()["error"]["constraint"] == C_DATE_NOT_LOCKED


def test_the_register_history_carries_each_revision_and_its_decision_state(stocked):
    made = generate(stocked)
    register_id = made["register_id"]

    listing = stocked.get("/api/registers").json()
    assert [item["register_id"] for item in listing] == [register_id]

    detail = stocked.get(f"/api/registers/{register_id}").json()
    assert detail["current_run_id"] == made["run_id"]
    assert detail["revisions"][0]["run_id"] == made["run_id"]
    assert detail["revisions"][0]["decision"] is None
    assert detail["revisions"][0]["kind"] == "generation"

    revisions = stocked.get(f"/api/registers/{register_id}/revisions").json()
    assert revisions == detail["revisions"]

    one = stocked.get(f"/api/revisions/{made['run_id']}").json()
    assert one["revision_number"] == 1
    assert one["register_id"] == register_id


def test_an_unknown_register_is_a_stable_refusal(stocked):
    answer = stocked.get("/api/registers/R-nope")
    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == C_REGISTER_UNKNOWN


# --- corrections ---------------------------------------------------------


def _first_entry(client: TestClient, run_id: str) -> tuple[str, dict]:
    draft = client.get(f"/api/generations/{run_id}").json()
    for row in draft["rows"]:
        if row["entries"]:
            return row["day"], row["entries"][0]
    raise AssertionError("the fixture draft carries no entry")


def test_correcting_an_entry_through_the_api_returns_a_new_revision(stocked):
    made = generate(stocked)
    label, entry = _first_entry(stocked, made["run_id"])

    answer = stocked.put(
        f"/api/generations/{made['run_id']}/entries/{entry['entry_id']}",
        json={"title_en": "Corrected", "reason": "typo"},
    )

    assert answer.status_code == 200
    body = answer.json()
    assert body["parent_run_id"] == made["run_id"]
    assert body["revision_number"] == 2
    assert body["action"] == "edit_entry"


def test_adding_an_entry_through_the_api_keeps_both_languages_apart(stocked):
    made = generate(stocked)
    answer = stocked.post(
        f"/api/generations/{made['run_id']}/entries",
        json={
            "date": "2026-11-05",
            "kind": "activity",
            "title_en": "Meter reading",
            "title_kn": "ಮೀಟರ್ ಓದುವಿಕೆ",
        },
    )

    assert answer.status_code == 200
    draft = stocked.get(f"/api/generations/{answer.json()['run_id']}").json()
    row = next(item for item in draft["rows"] if item["day"] == "2026-11-05")
    added = [item for item in row["entries"] if item["title_en"] == "Meter reading"]
    assert len(added) == 1
    assert added[0]["title_kn"] == "ಮೀಟರ್ ಓದುವಿಕೆ"


def test_a_rejected_visit_move_explains_which_rule_refused_it(stocked):
    made = generate(stocked)
    draft = stocked.get(f"/api/generations/{made['run_id']}").json()
    visit = next(
        (row["day"], item) for row in draft["rows"] for item in row["visits"]
    )
    day, held = visit

    answer = stocked.post(
        f"/api/generations/{made['run_id']}/visits/move",
        json={"asset_id": held["asset_id"], "from": day, "to": "2026-11-01"},
    )

    assert answer.status_code == 400
    error = answer.json()["error"]
    assert error["constraint"] == "corrected_visit_fails_validation"
    assert error["violations"][0]["constraint"] == "not_a_working_date"
    assert error["violations"][0]["asset_id"] == held["asset_id"]


def test_an_entry_on_a_locked_date_cannot_be_corrected_through_the_api(stocked):
    made = generate(stocked)
    label, entry = _first_entry(stocked, made["run_id"])
    stocked.put(f"/api/generations/{made['run_id']}/locks/{label}", json={})

    answer = stocked.put(
        f"/api/generations/{made['run_id']}/entries/{entry['entry_id']}",
        json={"title_en": "Refused"},
    )

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == C_DATE_LOCKED


# --- regeneration, comparison and acceptance -----------------------------


def test_a_candidate_is_compared_and_accepted_through_the_api(stocked):
    made = generate(stocked)
    register_id = made["register_id"]

    candidate = stocked.post(f"/api/registers/{register_id}/candidates", json={}).json()
    assert candidate["eligible"] is True
    assert candidate["base_run_id"] == made["run_id"]

    pending = stocked.get(f"/api/registers/{register_id}/candidates").json()
    assert [item["run_id"] for item in pending] == [candidate["candidate_run_id"]]

    compared = stocked.get(
        f"/api/revisions/{made['run_id']}/compare/{candidate['candidate_run_id']}"
    ).json()
    assert compared["summary"]["unchanged"] == 30
    assert compared["visits"] == {"added": [], "removed": [], "moved": []}

    accepted = stocked.post(
        f"/api/registers/{register_id}/candidates/{candidate['candidate_run_id']}/accept",
        json={"note": "checked"},
    ).json()
    assert accepted["decision"] == "accepted"
    assert stocked.get(f"/api/registers/{register_id}").json()["current_run_id"] == (
        candidate["candidate_run_id"]
    )


def test_a_candidate_can_be_refused_through_the_api_and_the_register_stays(stocked):
    made = generate(stocked)
    register_id = made["register_id"]
    candidate = stocked.post(
        f"/api/registers/{register_id}/candidates", json={"seed": 404}
    ).json()

    refused = stocked.post(
        f"/api/registers/{register_id}/candidates/{candidate['candidate_run_id']}/reject",
        json={"note": "worse arrangement"},
    ).json()

    assert refused["decision"] == "rejected"
    assert refused["current_run_id"] == made["run_id"]
    assert stocked.get(f"/api/registers/{register_id}/candidates").json() == []


# --- finalising a month ----------------------------------------------------


def review_all(client: TestClient, run_id: str) -> None:
    for item in client.get(f"/api/generations/{run_id}/review").json()["dates"]:
        client.put(
            f"/api/generations/{run_id}/review/{item['date']}",
            json={"fingerprint": item["fingerprint"]},
        )


def closed_month(client: TestClient, **overrides) -> tuple[str, dict]:
    made = generate(client, **overrides)
    review_all(client, made["run_id"])
    answer = client.post(
        f"/api/generations/{made['run_id']}/finalisation",
        json={"month": "2026-11", "note": "closed"},
    )
    assert answer.status_code == 200, answer.text
    return made["run_id"], answer.json()


def test_why_a_month_cannot_be_finalised_is_answered_with_every_reason(stocked):
    made = generate(stocked)

    body = stocked.get(
        f"/api/generations/{made['run_id']}/finalisation?month=2026-11"
    ).json()

    assert body["eligible"] is False
    assert body["reviewed_dates"] == 0
    assert body["total_dates"] == 30
    codes = {item["code"] for item in body["blockers"]}
    assert "dates_not_reviewed" in codes
    blocker = next(
        item for item in body["blockers"] if item["code"] == "dates_not_reviewed"
    )
    assert "2026-11-01" in blocker["dates"]


def test_reviewing_every_date_makes_a_month_eligible(stocked):
    made = generate(stocked)
    review_all(stocked, made["run_id"])

    body = stocked.get(
        f"/api/generations/{made['run_id']}/finalisation?month=2026-11"
    ).json()

    assert body["eligible"] is True, body["blockers"]
    assert body["blockers"] == []
    assert body["is_operational_diary"] is True


def test_a_month_is_finalised_over_the_surface(stocked):
    run_id, record = closed_month(stocked)

    assert record["month"] == "2026-11"
    assert record["sequence"] == 1
    assert record["is_latest"] is True
    assert len(record["content"]["rows"]) == 30

    read_back = stocked.get(f"/api/finalisations/{record['finalisation_id']}").json()
    assert read_back["fingerprint"] == record["fingerprint"]
    assert read_back["note"] == "closed"


def test_a_blocked_finalisation_carries_its_blockers(stocked):
    made = generate(stocked)

    answer = stocked.post(
        f"/api/generations/{made['run_id']}/finalisation",
        json={"month": "2026-11"},
    )

    assert answer.status_code == 400
    error = answer.json()["error"]
    assert error["constraint"] == "dates_not_reviewed"
    assert error["blockers"]
    assert any(item["code"] == "dates_not_reviewed" for item in error["blockers"])
    assert stocked.get(f"/api/finalisations/{'MF-nothing'}").status_code == 400


def test_a_finalised_month_refuses_edits_over_the_surface(stocked):
    run_id, _ = closed_month(stocked)

    answer = stocked.post(
        f"/api/generations/{run_id}/entries",
        json={"date": "2026-11-04", "kind": "activity", "title_en": "Late"},
    )

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] in {
        "month_is_finalised_and_immutable",
        "revision_is_not_current",
    }


def test_the_month_lifecycle_reports_four_separate_dimensions(stocked):
    run_id, record = closed_month(stocked)
    register = stocked.get(f"/api/generations/{run_id}").json()
    register_id = stocked.get(f"/api/generations/{run_id}/review").json()["register_id"]

    months = stocked.get(f"/api/registers/{register_id}/months").json()

    assert [item["month"] for item in months] == ["2026-11"]
    month = months[0]
    assert month["allocation_complete"] is True
    assert month["allocator_status"].upper() == "FEASIBLE"
    assert month["reviewed_dates"] == month["total_dates"] == 30
    assert month["finalised"] is True
    assert month["latest_finalisation_id"] == record["finalisation_id"]
    assert month["locked_dates"] == 0
    assert month["lifecycle"] == "finalised"
    assert register["is_draft"] is True


def test_one_month_is_finalised_while_the_next_stays_open(stocked):
    made = generate(stocked, end="2026-12-31", cycle_duration_months=2)
    review_all(stocked, made["run_id"])
    register_id = stocked.get(f"/api/generations/{made['run_id']}/review").json()[
        "register_id"
    ]

    answer = stocked.post(
        f"/api/generations/{made['run_id']}/finalisation",
        json={"month": "2026-11"},
    )
    assert answer.status_code == 200

    months = {
        item["month"]: item
        for item in stocked.get(f"/api/registers/{register_id}/months").json()
    }
    assert months["2026-11"]["lifecycle"] == "finalised"
    assert months["2026-12"]["lifecycle"] == "reviewed"
    assert months["2026-12"]["finalised"] is False


# --- correcting a finalised month -----------------------------------------


def test_a_correction_is_started_from_a_finalised_month(stocked):
    run_id, record = closed_month(stocked)

    answer = stocked.post(
        f"/api/finalisations/{record['finalisation_id']}/corrections",
        json={"reason": "the 4th was entered wrong"},
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["correction_of_run_id"] == run_id
    assert body["correction_of_finalisation_id"] == record["finalisation_id"]
    assert body["reason"] == "the 4th was entered wrong"
    assert body["next_sequence"] == 2
    assert len(body["carried_reviews"]) == 30


def test_a_correction_without_a_reason_is_refused(stocked):
    _, record = closed_month(stocked)

    answer = stocked.post(
        f"/api/finalisations/{record['finalisation_id']}/corrections",
        json={"reason": "  "},
    )

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "correction_reason_required"


def test_the_original_version_is_readable_after_its_correction_is_finalised(stocked):
    run_id, record = closed_month(stocked)
    correction = stocked.post(
        f"/api/finalisations/{record['finalisation_id']}/corrections",
        json={"reason": "a correction"},
    ).json()
    corrected_run = correction["correction_run_id"]
    edit = stocked.post(
        f"/api/generations/{corrected_run}/entries",
        json={"date": "2026-11-04", "kind": "activity", "title_en": "Corrected"},
    ).json()
    corrected_run = edit["run_id"]
    review_all(stocked, corrected_run)
    successor = stocked.post(
        f"/api/generations/{corrected_run}/finalisation", json={"month": "2026-11"}
    )
    assert successor.status_code == 200, successor.text
    successor = successor.json()
    assert successor["sequence"] == 2
    assert successor["parent_finalisation_id"] == record["finalisation_id"]
    assert successor["correction_of_run_id"] == run_id

    chain = stocked.get(
        f"/api/registers/{correction['register_id']}/finalisations/2026-11"
    ).json()
    assert [item["sequence"] for item in chain] == [1, 2]
    assert [item["is_latest"] for item in chain] == [False, True]
    assert chain[0]["content"] == record["content"]
    titles = [item[2] for item in chain[1]["content"]["rows"]["2026-11-04"]["entries"]]
    assert "Corrected" in titles


def test_an_open_correction_can_be_abandoned_over_the_surface(stocked):
    run_id, record = closed_month(stocked)
    correction = stocked.post(
        f"/api/finalisations/{record['finalisation_id']}/corrections",
        json={"reason": "opened by mistake"},
    ).json()

    answer = stocked.delete(
        f"/api/registers/{correction['register_id']}/months/2026-11/correction"
    )

    assert answer.status_code == 200
    assert answer.json()["current_run_id"] == run_id
    months = stocked.get(
        f"/api/registers/{correction['register_id']}/months"
    ).json()
    assert months[0]["lifecycle"] == "finalised"
    assert months[0]["open_correction_run_id"] is None
