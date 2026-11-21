"""The monthly leave sheet: what it writes, what it refuses, and what a person can undo.

A leave record is a date the office did not work. The sheet that says so is written by a person
and covers ranges, so the importer reads a range and writes one record per date, refuses a leave
type the office has not configured instead of inventing it, leaves a rejected leave out, and
replaces only the dates the file names. Deleting one record is then the whole correction.
"""

from __future__ import annotations

from datetime import date

import pytest
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import database
from dailyworkreg.infrastructure.sqlite.imports import accept_batch, stage_bytes
from dailyworkreg.infrastructure.sqlite.masters import StorageError
from dailyworkreg.infrastructure.sqlite.seed import seed_leave_types
from dailyworkreg.interfaces.api import create_app
from fastapi.testclient import TestClient

HEADER = (
    "Employee ID,Employee Name,Leave Type,From Date,To Date,"
    "Full Day (Y/N),Approval Status,Approved By,Approval Reference,Notes\n"
)


@pytest.fixture()
def office(tmp_path):
    connection = database.open_database(tmp_path / "office.db")
    seed_leave_types(connection)
    yield connection
    connection.close()


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(tmp_path / "office.db"))


def _sheet(*rows: str) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode("utf-8")


def _stage(office, *rows: str):
    return stage_bytes(office, "leave", "leave.csv", _sheet(*rows))


def _stored(office) -> list[tuple[str, str, str | None]]:
    return [
        (record.leave_date.isoformat(), record.leave_type_name, record.note)
        for record in cal.list_leave_records(office)
    ]


def test_a_range_becomes_one_record_for_each_of_its_dates(office):
    batch, _ = _stage(
        office,
        "1,A Person,Casual Leave,05-11-2026,07-11-2026,Y,Approved,AE,X1,Family function",
    )
    result = accept_batch(office, batch.batch_id)

    assert (result.inserted, result.updated) == (3, 0)
    assert [day for day, _, _ in _stored(office)] == [
        "2026-11-05",
        "2026-11-06",
        "2026-11-07",
    ]


def test_the_leave_type_is_matched_by_its_code_or_its_name(office):
    _stage(
        office,
        "1,A,CL,05-11-2026,05-11-2026,Y,Approved,AE,X1,",
        "2,B,Casual Leave,06-11-2026,06-11-2026,Y,Approved,AE,X2,",
    )
    batch, _ = _stage(
        office,
        "1,A,CL,05-11-2026,05-11-2026,Y,Approved,AE,X1,",
        "2,B,Casual Leave,06-11-2026,06-11-2026,Y,Approved,AE,X2,",
    )
    assert accept_batch(office, batch.batch_id).inserted == 2
    assert {name for _, name, _ in _stored(office)} == {"Casual Leave"}, (
        "both rows name the same stored leave type, so both records carry its name"
    )


def test_a_leave_type_the_office_has_not_configured_is_a_row_error(office):
    batch, verdict = _stage(
        office, "1,A,Earned Leave,05-11-2026,05-11-2026,Y,Approved,AE,X1,"
    )

    assert batch.error_count == 1
    message = verdict.outcomes[0].messages[0]
    assert "leave type 'Earned Leave' is not one of the stored leave types" in message
    assert "CL (Casual Leave)" in message
    assert "add it on the Calendar tab first" in message
    assert len(cal.list_leave_types(office)) == 2, "no leave type was invented"


def test_a_rejected_leave_writes_nothing_and_keeps_its_reason(office):
    batch, verdict = _stage(
        office, "3,C,CL,12-11-2026,12-11-2026,Y,Rejected,AE,X3,"
    )
    assert batch.error_count == 0
    assert verdict.outcomes[0].decision == "exclude"
    assert "recorded this leave as 'Rejected'" in verdict.outcomes[0].messages[0]
    assert "not a leave day and nothing is written" in verdict.outcomes[0].messages[0]

    # A batch whose every row was left out has nothing to write, and saying so is better than
    # accepting it: the operator is told the file changed nothing rather than believing it did.
    with pytest.raises(StorageError) as refusal:
        accept_batch(office, batch.batch_id)
    assert refusal.value.constraint == "import_batch_nothing_to_import"
    assert _stored(office) == []


def test_a_cancelled_or_withdrawn_leave_is_left_out_too(office):
    batch, verdict = _stage(
        office,
        "3,C,CL,12-11-2026,12-11-2026,Y,Cancelled,AE,X3,",
        "4,D,CL,13-11-2026,13-11-2026,Y,Withdrawn,AE,X4,",
    )
    assert batch.error_count == 0
    assert [outcome.decision for outcome in verdict.outcomes] == ["exclude", "exclude"]


def test_a_half_day_is_kept_as_leave_and_the_office_is_told(office):
    batch, verdict = _stage(
        office, "2,B,CL,10-11-2026,10-11-2026,N,Approved,AE,X2,"
    )
    message = verdict.outcomes[0].messages[0]
    assert "less than a full day" in message
    assert "the whole date is taken as leave" in message
    assert verdict.outcomes[0].decision == "include"

    accept_batch(office, batch.batch_id)
    assert [day for day, _, _ in _stored(office)] == ["2026-11-10"], (
        "a half day is still a day the office did not work; the calendar has no half day"
    )


def test_a_missing_end_date_is_a_single_day(office):
    batch, verdict = _stage(office, "2,B,CL,10-11-2026,,Y,Approved,AE,X2,")
    assert verdict.outcomes[0].severity == "ok"
    assert accept_batch(office, batch.batch_id).inserted == 1
    assert [day for day, _, _ in _stored(office)] == ["2026-11-10"]


def test_a_range_that_ends_before_it_begins_is_refused(office):
    batch, verdict = _stage(
        office, "1,A,CL,10-11-2026,09-11-2026,Y,Approved,AE,X1,"
    )
    assert batch.error_count == 1
    assert "so the range is empty" in verdict.outcomes[0].messages[0]


def test_a_date_that_cannot_exist_is_refused_with_the_day_named(office):
    batch, verdict = _stage(office, "1,A,CL,31-09-2026,31-09-2026,Y,Approved,AE,X1,")
    assert batch.error_count == 1
    message = verdict.outcomes[0].messages[0]
    assert "September" in message or "31" in message


def test_a_row_with_no_leave_type_is_refused(office):
    batch, verdict = _stage(office, "1,A,,10-11-2026,10-11-2026,Y,Approved,AE,X1,")
    assert batch.error_count == 1
    assert verdict.outcomes[0].messages[0] == "no leave type was supplied"


def test_reading_the_same_sheet_again_replaces_rather_than_duplicates(office):
    rows = ("1,A,CL,05-11-2026,06-11-2026,Y,Approved,AE,X1,",)
    first, _ = _stage(office, *rows)
    accept_batch(office, first.batch_id)

    second, _ = _stage(office, *rows)
    result = accept_batch(office, second.batch_id)

    assert (result.inserted, result.updated) == (0, 2)
    assert len(_stored(office)) == 2


def test_a_sheet_replaces_only_the_dates_it_names(office):
    earlier, _ = _stage(office, "1,A,CL,05-11-2026,07-11-2026,Y,Approved,AE,X1,")
    accept_batch(office, earlier.batch_id)

    corrected, _ = _stage(office, "1,A,CL,06-11-2026,06-11-2026,Y,Approved,AE,X1,")
    accept_batch(office, corrected.batch_id)

    assert [day for day, _, _ in _stored(office)] == [
        "2026-11-05",
        "2026-11-06",
        "2026-11-07",
    ], "a sheet for one range says nothing about the other dates stored"


def test_the_employee_named_on_the_sheet_travels_with_the_record(office):
    batch, _ = _stage(
        office, "7,R Shetty,CL,05-11-2026,05-11-2026,Y,Approved,AE,X1,Family function"
    )
    accept_batch(office, batch.batch_id)
    note = _stored(office)[0][2]
    assert note == "7 R Shetty: Family function", (
        "the calendar holds no employee, so the sheet's own evidence is kept beside the date"
    )


def test_acceptance_reports_records_not_file_rows(office):
    batch, _ = _stage(office, "1,A,CL,05-11-2026,07-11-2026,Y,Approved,AE,X1,")
    result = accept_batch(office, batch.batch_id)
    assert batch.row_count == 1
    assert result.inserted == 3, "one row covered three dates, and three records were written"


def test_leave_can_be_read_from_an_empty_calendar(office):
    assert cal.list_leave_records(office) == ()
    batch, _ = _stage(office, "1,A,CL,05-11-2026,05-11-2026,Y,Approved,AE,X1,")
    assert batch.error_count == 0


def test_a_record_can_be_deleted_over_the_surface(client, office):
    batch, _ = _stage(office, "1,A,CL,05-11-2026,06-11-2026,Y,Approved,AE,X1,")
    accept_batch(office, batch.batch_id)

    listed = client.get("/api/calendar/leave-records").json()
    assert [row["leave_date"] for row in listed] == ["2026-11-05", "2026-11-06"]
    assert listed[0]["leave_type_name"] == "Casual Leave"

    response = client.delete(f"/api/calendar/leave-records/{listed[0]['leave_id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "leave_id": listed[0]["leave_id"]}
    assert [row["leave_date"] for row in client.get("/api/calendar/leave-records").json()] == [
        "2026-11-06"
    ]


def test_deleting_a_leave_that_is_not_stored_says_so(client):
    response = client.delete("/api/calendar/leave-records/L-2026-11-05")
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["constraint"] == "leave_record_unknown"
    assert "there is nothing to remove" in body["detail"]


def test_a_stored_leave_day_reaches_the_calendar_projection(client, office):
    batch, _ = _stage(office, "1,A,CL,05-11-2026,05-11-2026,Y,Approved,AE,X1,")
    accept_batch(office, batch.batch_id)

    preview = client.get(
        "/api/calendar/preview", params={"start": "2026-11-01", "end": "2026-11-08"}
    ).json()
    day = next(item for item in preview["days"] if item["day"] == "2026-11-05")
    assert day["is_working_day"] is False
    assert "Casual Leave" in day["reasons_en"], day


def test_the_imports_tab_offers_the_leave_sheet(client):
    page = client.get("/").text
    assert '<option value="leave">Monthly leave records</option>' in page
    assert "A leave record is one date and one leave type" in page
    assert 'id="leave-table"' in page


def test_the_leave_list_is_loaded_with_the_calendar(client):
    from dailyworkreg.interfaces.api import STATIC

    client_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "async function loadLeave()" in client_js
    assert "/api/calendar/leave-records/" in client_js
    assert "await loadLeave();" in client_js


def test_a_leave_date_does_not_become_a_working_day(office):
    batch, _ = _stage(office, "1,A,CL,05-11-2026,05-11-2026,Y,Approved,AE,X1,")
    accept_batch(office, batch.batch_id)
    stored = cal.list_leave_records(office)
    assert stored[0].leave_date == date(2026, 11, 5)
    assert stored[0].leave_type_id == "CL"
