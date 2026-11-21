"""Reading a DTC roster before the masters it names it against.

A roster says which feeder each transformer sits on and which streets the office works. Both
of those are other files, and a feeder name only becomes a stored record by way of the feeder
master. Read first, the roster is accepted into a database where no transformer has a
canonical feeder: every feeder's capacity is short, the readiness screen carries the count, and
the only cure is to read the roster again.

So the roster is refused at the moment it is read - before a batch exists - and the refusal
names what is missing. This is an order of work rather than a fault in the file: the roster
itself may be perfect, and the area and feeder masters are not.

The alternative was measured on the office's own files in Act 34: a roster staged ahead of its
feeder master was accepted with 154 warnings and 154 transformers holding no canonical feeder.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from dailyworkreg.infrastructure.imports.tabular import SourceFormatError
from dailyworkreg.infrastructure.sqlite import masters, open_database
from dailyworkreg.infrastructure.sqlite.imports import (
    C_IMPORT_ORDER_UNMET,
    accept_batch,
    stage_bytes,
)
from fastapi.testclient import TestClient
from master_fixtures import seed_office_masters

from dailyworkreg.interfaces.api import create_app

HEADER = "DTC ID,DTC Name,Class,Feeder,kVA,Station\n"

ROSTER = (
    "SCD-1,Alpha,DEPT,MRS F-1 OLDTOWN,250,110/11KV MRS\n"
    "SCD-2,Beta,MSB,Mandli F-13 MANDLI BYEPASS,63,110/11KV MANDLI\n"
    "SCD-3,Gamma,OWN,,100,110/11KV MRS\n"
)

AREA_CSV = "Area ID,Kannada Name,English Name\nA-01,ಗಾಂಧಿಬಜಾರ್,Gandhibazaar\n"
FEEDER_CSV = "Feeder ID,Feeder Name\nF-01,MRS F-1 OLDTOWN\nF-02,Mandli F-13 MANDLI BYEPASS\n"


def _roster(extra: str = "") -> bytes:
    return (HEADER + ROSTER + extra).encode("utf-8")


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "order.db")
    yield connection
    connection.close()


def _masters(connection: sqlite3.Connection, *, areas: int = 1, feeders: int = 2) -> None:
    """Store the masters the roster meets, the way their own files would."""

    if areas:
        masters.upsert_area(connection, area_id="A-01", name_kn="ಗಾಂಧಿಬಜಾರ್")
    if feeders >= 1:
        masters.upsert_feeder(
            connection, feeder_id="F-01", name="MRS F-1 OLDTOWN"
        )
    if feeders >= 2:
        masters.upsert_feeder(
            connection, feeder_id="F-02", name="Mandli F-13 MANDLI BYEPASS"
        )


# --- the refusals ---------------------------------------------------------


def test_a_roster_with_no_area_stored_is_refused(db: sqlite3.Connection) -> None:
    masters.upsert_feeder(db, feeder_id="F-01", name="MRS F-1 OLDTOWN")
    masters.upsert_feeder(db, feeder_id="F-02", name="Mandli F-13 MANDLI BYEPASS")

    with pytest.raises(SourceFormatError) as caught:
        stage_bytes(db, "dtc", "roster.csv", _roster())

    assert caught.value.constraint == C_IMPORT_ORDER_UNMET
    assert "no area is stored" in caught.value.detail
    assert "04_area_master.csv" in caught.value.detail
    assert "then read this roster again" in caught.value.detail


def test_a_roster_with_no_feeder_stored_is_refused(db: sqlite3.Connection) -> None:
    masters.upsert_area(db, area_id="A-01", name_kn="ಗಾಂಧಿಬಜಾರ್")

    with pytest.raises(SourceFormatError) as caught:
        stage_bytes(db, "dtc", "roster.csv", _roster())

    assert caught.value.constraint == C_IMPORT_ORDER_UNMET
    assert "no feeder is stored" in caught.value.detail
    assert "05_feeder_master.csv" in caught.value.detail


def test_a_name_the_feeder_master_does_not_carry_is_refused_and_named(
    db: sqlite3.Connection,
) -> None:
    _masters(db)
    extra = "SCD-4,Delta,DEPT,CHOWTRY FEEDER 9,63,110/11KV MRS\n"

    with pytest.raises(SourceFormatError) as caught:
        stage_bytes(db, "dtc", "roster.csv", _roster(extra))

    assert caught.value.constraint == C_IMPORT_ORDER_UNMET
    assert "CHOWTRY FEEDER 9" in caught.value.detail
    assert "does not carry 1 name" in caught.value.detail


def test_every_unknown_name_is_counted_and_five_are_shown(db: sqlite3.Connection) -> None:
    _masters(db)
    extra = "".join(
        f"SCD-{number},Delta,DEPT,UNKNOWN FEEDER {number},63,110/11KV MRS\n"
        for number in range(4, 11)
    )

    with pytest.raises(SourceFormatError) as caught:
        stage_bytes(db, "dtc", "roster.csv", _roster(extra))

    detail = caught.value.detail
    assert "does not carry 7 name(s)" in detail
    assert "UNKNOWN FEEDER 4" in detail
    assert "UNKNOWN FEEDER 8" in detail
    # Five are named and the rest are counted rather than listed, so a long file says
    # something an operator can read.
    assert "UNKNOWN FEEDER 9" not in detail
    assert "and 2 more" in detail


def test_an_unknown_name_matching_another_case_is_refused(db: sqlite3.Connection) -> None:
    """Matching is forgiving because the office's own sheets differ in case."""

    _masters(db)
    extra = "SCD-4,Delta,DEPT,mrs f-1 oldtown,63,110/11KV MRS\n"
    batch, _ = stage_bytes(db, "dtc", "roster.csv", _roster(extra))
    assert batch.error_count == 0


# --- what the refusal leaves behind --------------------------------------


def test_a_refused_roster_creates_no_batch_and_no_row(db: sqlite3.Connection) -> None:
    with pytest.raises(SourceFormatError):
        stage_bytes(db, "dtc", "roster.csv", _roster())

    assert db.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM import_staging_row").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM dtc").fetchone()[0] == 0


def test_a_refused_roster_cannot_be_retried_into_acceptance(
    db: sqlite3.Connection,
) -> None:
    """Nothing half-staged survives to be accepted by its identifier."""

    with pytest.raises(SourceFormatError) as caught:
        stage_bytes(db, "dtc", "roster.csv", _roster())
    assert caught.value.constraint == C_IMPORT_ORDER_UNMET
    assert db.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0


# --- the order, end to end ------------------------------------------------


def test_the_masters_first_then_the_roster_is_accepted(db: sqlite3.Connection) -> None:
    """The same file is refused, then accepted once its masters are stored."""

    with pytest.raises(SourceFormatError):
        stage_bytes(db, "dtc", "roster.csv", _roster())

    _masters(db)
    batch, verdict = stage_bytes(db, "dtc", "roster.csv", _roster())

    assert batch.error_count == 0
    assert verdict.error_count == 0
    result = accept_batch(db, batch.batch_id)
    assert result.inserted == 3
    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    assert stored["SCD-1"].canonical_feeder_id == "F-01"
    assert stored["SCD-2"].canonical_feeder_id == "F-02"
    # A blank cell is accepted with a warning rather than refused.
    assert stored["SCD-3"].canonical_feeder_id is None


def test_the_area_and_feeder_files_are_never_held_to_the_order(
    db: sqlite3.Connection,
) -> None:
    """The two files the order asks for must themselves be readable from an empty database."""

    area_batch, area_verdict = stage_bytes(db, "area", "areas.csv", AREA_CSV.encode())
    assert area_batch.error_count == 0
    assert area_verdict.error_count == 0

    feeder_batch, feeder_verdict = stage_bytes(db, "feeder", "feeders.csv", FEEDER_CSV.encode())
    assert feeder_batch.error_count == 0
    assert feeder_verdict.error_count == 0

    accept_batch(db, area_batch.batch_id)
    accept_batch(db, feeder_batch.batch_id)
    assert len(masters.list_areas(db)) == 1
    assert len(masters.list_feeders(db)) == 2


def test_the_activity_and_holiday_files_are_not_held_to_the_order(
    db: sqlite3.Connection,
) -> None:
    """Only the roster names a feeder, so only the roster is refused."""

    activity_csv = (
        "Activity ID,Category,Description,Kannada,Weight,Monthly Min,Monthly Max,Repeat Gap\n"
        "ACT-1,Inspection,Inspected the DTC,ಪರಿಶೀಲನೆ,1,1,4,2\n"
    )
    batch, verdict = stage_bytes(
        db, "activity", "activities.csv", activity_csv.encode()
    )
    assert batch.error_count == 0
    assert verdict.error_count == 0

    holiday_csv = (
        "Holiday ID,Date,Name\nH-1,2026-01-26,Republic Day\n"
    )
    holiday_batch, holiday_verdict = stage_bytes(
        db, "general_holiday", "holidays.csv", holiday_csv.encode()
    )
    assert holiday_batch.error_count == 0
    assert holiday_verdict.error_count == 0


def test_a_roster_read_with_its_masters_stores_what_the_file_said(
    db: sqlite3.Connection,
) -> None:
    _masters(db)
    batch, _ = stage_bytes(db, "dtc", "roster.csv", _roster())
    accept_batch(db, batch.batch_id)

    records = {record.dtc_id: record for record in masters.list_dtc_source_records(db)}
    assert records["SCD-1"].raw_feeder == "MRS F-1 OLDTOWN"
    assert records["SCD-3"].raw_feeder == ""


# --- over the surface -----------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "order-api.db"))


def _upload(client: TestClient, dataset: str, filename: str, payload: bytes):
    return client.post(
        f"/api/imports?dataset={dataset}",
        files={"file": (filename, payload, "text/csv")},
    )


def test_the_surface_refuses_the_roster_in_the_same_words(client: TestClient) -> None:
    answer = _upload(client, "dtc", "roster.csv", _roster())

    assert answer.status_code == 400
    body = answer.json()
    assert body["error"]["constraint"] == C_IMPORT_ORDER_UNMET
    assert "no area is stored" in body["error"]["detail"]
    assert client.get("/api/imports").json() == []


def test_the_surface_walks_the_order_and_then_accepts(client: TestClient) -> None:
    """Staging the two masters is not enough: they have to be accepted to be stored."""

    for dataset, filename, payload in (
        ("area", "areas.csv", AREA_CSV.encode()),
        ("feeder", "feeders.csv", FEEDER_CSV.encode()),
    ):
        staged = _upload(client, dataset, filename, payload)
        assert staged.status_code == 200
        accepted = client.post(f"/api/imports/{staged.json()['batch_id']}/accept", json={})
        assert accepted.json()["accepted"] is True

    # A roster staged but not accepted still leaves the masters unstored, so the order is
    # about stored records rather than about which file was chosen last.
    answer = _upload(client, "dtc", "roster.csv", _roster())
    assert answer.status_code == 200
    batch = answer.json()
    assert batch["can_accept"] is True

    accepted = client.post(f"/api/imports/{batch['batch_id']}/accept", json={}).json()
    assert accepted["accepted"] is True
    assert accepted["inserted"] == 3


def test_the_page_states_the_order_before_the_file_is_chosen(client: TestClient) -> None:
    page = client.get("/").text

    # The sentence an operator reads before uploading anything.
    assert "Read and accept the areas and the feeders before the DTC roster" in page
    assert "refused until both are stored" in page
    assert "feeder master does not carry" in page


def test_a_holiday_import_still_works_with_nothing_stored(client: TestClient) -> None:
    """The order is about one file, and the other four keep working from an empty database."""

    holiday_csv = "Holiday ID,Date,Name\nH-1,2026-01-26,Republic Day\n"
    staged = _upload(client, "general_holiday", "holidays.csv", holiday_csv.encode())
    assert staged.status_code == 200
    accepted = client.post(f"/api/imports/{staged.json()['batch_id']}/accept", json={})
    assert accepted.json()["accepted"] is True
