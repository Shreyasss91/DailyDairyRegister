"""Staged import: file reading, judgement, reconciliation and acceptance."""

from __future__ import annotations

import io
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.infrastructure.imports.datasets import (
    EXCLUDE,
    judge_dataset,
    normalise_header,
)
from dailyworkreg.infrastructure.imports.tabular import (
    C_UNMAPPED_SOURCE,
    SourceFormatError,
    read_source_bytes,
)
from dailyworkreg.infrastructure.sqlite import StorageError, masters, open_database
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite.imports import (
    accept_batch,
    get_batch,
    list_batches,
    reject_batch,
    stage_bytes,
    stage_source,
    staging_rows,
)
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities

DTC_CSV = (
    "DTC ID,DTC Name,Class,Feeder,Feeder Code,MDM Code,kVA,Station\n"
    "SCD-1,Alpha,DEPT,MRS F-1 OLDTOWN,FC-1,MDM-1,250,Station A\n"
    "SCD-2,Beta,MSB,,#N/A,MDM-2,#N/A,Station B\n"
    "SCD-3,Gamma,OWN,Unknown Feeder,FC-3,MDM-3,100,Station C\n"
)


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "import.db")
    seed_approved_identities(connection)
    yield connection
    connection.close()


# --- reading a source file ------------------------------------------------


def test_values_are_read_verbatim_including_blanks_and_na():
    source = read_source_bytes("dtc.csv", DTC_CSV.encode())
    assert source.headers[0] == "DTC ID"
    assert source.rows[1]["Feeder"] == ""
    assert source.rows[1]["kVA"] == "#N/A"
    assert source.filename == "dtc.csv"
    assert len(source.sha256) == 64


def test_a_source_with_no_records_is_refused():
    with pytest.raises(SourceFormatError) as caught:
        read_source_bytes("empty.csv", b"a,b\n")
    assert caught.value.constraint == "source_file_empty"


def test_a_source_with_no_header_is_refused():
    with pytest.raises(SourceFormatError) as caught:
        read_source_bytes("x.csv", b"\n1,2\n")
    assert caught.value.constraint == "source_header_missing"


def test_a_duplicate_column_name_is_refused():
    with pytest.raises(SourceFormatError) as caught:
        read_source_bytes("x.csv", b"DTC ID,DTC ID\n1,2\n")
    assert caught.value.constraint == "source_header_duplicate"


def test_an_unsupported_format_is_refused_rather_than_guessed():
    with pytest.raises(SourceFormatError) as caught:
        read_source_bytes("x.pdf", b"%PDF-1.4")
    assert caught.value.constraint == "source_format_unsupported"


def test_a_tab_separated_source_reads_the_same_way():
    source = read_source_bytes("x.tsv", b"DTC ID\tDTC Name\nSCD-1\tAlpha\n")
    assert source.rows[0]["DTC Name"] == "Alpha"


def test_an_xlsx_source_reads_the_same_way(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["DTC ID", "DTC Name", "Class"])
    sheet.append(["SCD-1", "Alpha", "DEPT"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    source = read_source_bytes("dtc.xlsx", buffer.getvalue())
    assert source.rows[0]["DTC Name"] == "Alpha"


def test_column_headings_are_matched_leniently():
    assert normalise_header("DTC_Name") == normalise_header("dtc name")


# --- judging --------------------------------------------------------------


def test_the_scf_and_sce_records_are_excluded_with_a_reason():
    # Which rows are excluded is decided by the Class column the office supplies, not by the
    # letters in front of an identifier: the original source carried no class column at all,
    # and a prefix is one office's filing convention rather than a class. The identifier rule
    # itself is covered by tests/test_dtc_identifier.py.
    csv = (
        "DTC ID,DTC Name,Class\n"
        "SCD-1,Alpha,DEPT\n"
        "SCF-2,Beta,SCF\n"
        "SCE-3,Gamma,SCE\n"
    )
    source = read_source_bytes("d.csv", csv.encode())
    verdict = judge_dataset("dtc", source.headers, source.rows)
    by_id = {o.payload["dtc_id"] if o.payload else o.raw["DTC ID"]: o for o in verdict.outcomes}
    assert by_id["SCD-1"].is_importable
    assert by_id["SCF-2"].decision == EXCLUDE
    assert by_id["SCF-2"].severity == "warning"
    assert "§8" in by_id["SCF-2"].messages[0]
    assert by_id["SCE-3"].decision == EXCLUDE
    assert verdict.can_accept is True


def test_msb_and_own_become_self_dtc_but_keep_their_source_class():
    source = read_source_bytes("d.csv", DTC_CSV.encode())
    verdict = judge_dataset("dtc", source.headers, source.rows)
    payloads = {o.payload["dtc_id"]: o.payload for o in verdict.outcomes if o.payload}
    assert payloads["SCD-2"]["dtc_class"] == "SELF_DTC"
    assert payloads["SCD-2"]["source_class"] == "MSB"
    assert payloads["SCD-3"]["dtc_class"] == "SELF_DTC"
    assert payloads["SCD-3"]["source_class"] == "OWN"
    assert payloads["SCD-1"]["dtc_class"] == "DEPT"


def test_an_unrecognised_class_is_an_error_not_a_guess():
    source = read_source_bytes("d.csv", b"DTC ID,DTC Name,Class\nSCD-1,A,MUNICIPAL\n")
    verdict = judge_dataset("dtc", source.headers, source.rows)
    assert verdict.error_count == 1
    assert "not DEPT, MSB or OWN" in verdict.outcomes[0].messages[0]
    assert verdict.can_accept is False


def test_a_missing_required_column_is_reported_by_name():
    source = read_source_bytes("d.csv", b"DTC ID,Class\nSCD-1,DEPT\n")
    verdict = judge_dataset("dtc", source.headers, source.rows)
    assert verdict.file_messages
    assert "name" in verdict.file_messages[0]
    assert verdict.can_accept is False


def test_a_file_whose_columns_do_not_match_the_contract_is_refused_when_staged(db):
    """It must not become a batch with no rows, no errors and no explanation.

    The reason for the refusal used to be computed and then dropped, so an operator
    uploading a file with different headings saw an empty batch and no message.
    """

    source = read_source_bytes("d.csv", b"DTC ID,Class\nSCD-1,DEPT\n")
    with pytest.raises(SourceFormatError) as refusal:
        stage_source(db, "dtc", source)

    assert refusal.value.constraint == C_UNMAPPED_SOURCE
    assert "no column for name" in refusal.value.detail
    assert list_batches(db) == ()


def test_a_placeholder_capacity_is_reported_and_not_read_as_zero():
    source = read_source_bytes("d.csv", DTC_CSV.encode())
    verdict = judge_dataset("dtc", source.headers, source.rows)
    beta = next(o for o in verdict.outcomes if o.payload and o.payload["dtc_id"] == "SCD-2")
    assert beta.payload["kva"] is None
    assert beta.severity == "warning"
    assert any("#N/A" in message for message in beta.messages)


# --- staging does not touch the master ------------------------------------


def test_staging_changes_no_canonical_record(db):
    before = masters.current_version(db)
    batch, verdict = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    assert batch.status == "staged"
    assert batch.row_count == 3
    assert masters.list_dtcs(db) == ()
    assert masters.current_version(db) == before
    assert len(staging_rows(db, batch.batch_id)) == 3


def test_staging_retains_the_source_filename_and_digest(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    assert batch.source_filename == "dtc.csv"
    assert batch.source_sha256 == read_source_bytes("dtc.csv", DTC_CSV.encode()).sha256


def test_staging_row_keeps_the_raw_values(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    rows = staging_rows(db, batch.batch_id)
    assert rows[1].raw["kVA"] == "#N/A"
    assert rows[1].raw["Feeder"] == ""


def test_an_unfindable_feeder_is_a_reconciliation_warning(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    rows = {row.payload["dtc_id"]: row for row in staging_rows(db, batch.batch_id) if row.payload}
    assert rows["SCD-3"].severity == "warning"
    assert "not in the feeder master" in rows["SCD-3"].messages[0]


def test_a_duplicate_identity_makes_the_batch_unacceptable(db):
    csv = "DTC ID,DTC Name,Class\nSCD-1,Alpha,DEPT\nSCD-1,Beta,DEPT\n"
    batch, verdict = stage_bytes(db, "dtc", "dupe.csv", csv.encode())
    assert verdict.error_count == 1
    assert "same record as row 1" in verdict.outcomes[1].messages[-1]
    with pytest.raises(StorageError) as caught:
        accept_batch(db, batch.batch_id)
    assert caught.value.constraint == "import_batch_has_errors"
    assert masters.list_dtcs(db) == ()


# --- acceptance -----------------------------------------------------------


def test_accepting_an_import_creates_the_canonical_records(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    result = accept_batch(db, batch.batch_id)
    assert result.inserted == 3
    assert result.updated == 0
    dtcs = {d.dtc_id: d for d in masters.list_dtcs(db)}
    assert set(dtcs) == {"SCD-1", "SCD-2", "SCD-3"}
    # The supplied feeder existed, so it became the canonical assignment.
    assert dtcs["SCD-1"].canonical_feeder_id == "F-01"
    # The one that did not exist was left unassigned rather than invented.
    assert dtcs["SCD-3"].canonical_feeder_id is None
    assert get_batch(db, batch.batch_id).status == "accepted"


def test_acceptance_preserves_the_source_record_verbatim(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    accept_batch(db, batch.batch_id)
    sources = {record.dtc_id: record for record in masters.list_dtc_source_records(db)}
    assert sources["SCD-2"].raw_kva == "#N/A"
    assert sources["SCD-2"].raw_class == "MSB"
    assert sources["SCD-3"].raw_feeder == "Unknown Feeder"
    # The canonical assignment and the supplied value are different facts.
    assert sources["SCD-1"].raw_feeder == "MRS F-1 OLDTOWN"


def test_a_rejected_batch_leaves_the_master_untouched(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    reject_batch(db, batch.batch_id, note="wrong file")
    assert get_batch(db, batch.batch_id).status == "rejected"
    assert masters.list_dtcs(db) == ()
    with pytest.raises(StorageError):
        accept_batch(db, batch.batch_id)


def test_a_batch_cannot_be_accepted_twice(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    accept_batch(db, batch.batch_id)
    with pytest.raises(StorageError) as caught:
        accept_batch(db, batch.batch_id)
    assert caught.value.constraint == "import_batch_not_staged"


def test_reimporting_the_same_file_updates_rather_than_duplicating(db):
    batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    accept_batch(db, batch.batch_id)
    first_count = len(masters.list_dtcs(db))
    second_batch, _ = stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    result = accept_batch(db, second_batch.batch_id)
    assert result.inserted == 0
    assert result.updated == 3
    assert len(masters.list_dtcs(db)) == first_count


def test_accepting_an_excluded_row_does_not_create_it(db):
    csv = "DTC ID,DTC Name,Class\nSCD-1,Alpha,DEPT\nSCF-2,Beta,SCF\n"
    batch, _ = stage_bytes(db, "dtc", "excl.csv", csv.encode())
    result = accept_batch(db, batch.batch_id)
    assert result.excluded == 1
    assert {d.dtc_id for d in masters.list_dtcs(db)} == {"SCD-1"}


def test_the_approved_feeder_overrides_are_applied_on_acceptance(db):
    csv = (
        "DTC ID,DTC Name,Class,Feeder\n"
        "SCD-5,Override Me,DEPT,\n"
        "SCD-12,Override Me Too,DEPT,Mandli F-13 MANDLI BYEPASS\n"
    )
    batch, _ = stage_bytes(db, "dtc", "overrides.csv", csv.encode())
    result = accept_batch(db, batch.batch_id)
    dtcs = {d.dtc_id: d for d in masters.list_dtcs(db)}
    # The owner-confirmed assignment wins over a blank and over a different source value.
    assert dtcs["SCD-5"].canonical_feeder_id == "F-01"
    assert dtcs["SCD-12"].canonical_feeder_id == "F-03"
    assert result.feeder_overrides_applied == 2


def test_an_area_without_an_id_is_matched_by_its_name(db):
    seed_area_id = "A-01"
    csv = "Kannada Name,English Name\nಗಾಂಧಿಬಜಾರ್,Gandhibazaar\n"
    batch, verdict = stage_bytes(db, "area", "areas.csv", csv.encode())
    assert verdict.error_count == 0
    accept_batch(db, batch.batch_id)
    area = masters.get_area(db, seed_area_id)
    assert area.name_en == "Gandhibazaar"
    assert area.revision == 2


def test_an_area_with_no_id_and_no_match_is_refused(db):
    csv = "Kannada Name\nಬೆಂಗಳೂರು\n"
    batch, verdict = stage_bytes(db, "area", "areas.csv", csv.encode())
    assert verdict.error_count == 1
    assert "no identity to update" in verdict.outcomes[0].messages[-1]


def test_an_activity_without_an_id_is_refused(db):
    csv = (
        "Category,Description,Weight,Monthly Min,Monthly Max,Repeat Gap\n"
        "Inspection,Inspect distribution transformer,1,1,4,2\n"
    )
    batch, verdict = stage_bytes(db, "activity", "act.csv", csv.encode())
    assert verdict.error_count == 1
    assert "missing activity id" in verdict.outcomes[0].messages[0]
    with pytest.raises(StorageError):
        accept_batch(db, batch.batch_id)
    assert masters.list_activities(db) == ()


def test_an_activity_without_its_numbers_is_refused(db):
    csv = "Activity ID,Category,Description\nACT-1,Inspection,Inspect distribution transformer\n"
    batch, verdict = stage_bytes(db, "activity", "act.csv", csv.encode())
    assert verdict.error_count == 1
    assert "was supplied" in verdict.outcomes[0].messages[0]
    assert masters.list_activities(db) == ()


def test_holiday_import_replaces_the_year_by_default(db):
    cal.upsert_holiday(
        db, holiday_id="OLD", holiday_date=date(2026, 4, 14),
        name="Old entry", source="karnataka",
    )
    csv = "Date,Holiday\n26-01-2026,Republic Day\n"
    batch, verdict = stage_bytes(db, "general_holiday", "h.csv", csv.encode())
    assert verdict.error_count == 0
    accept_batch(db, batch.batch_id)
    names = {h.name for h in cal.list_holidays(db, year=2026)}
    assert names == {"Republic Day"}


def test_holiday_import_in_merge_mode_keeps_what_is_stored(db):
    cal.upsert_holiday(
        db, holiday_id="OLD", holiday_date=date(2026, 4, 14),
        name="Old entry", source="karnataka",
    )
    csv = "Date,Holiday\n26-01-2026,Republic Day\n"
    batch, _ = stage_bytes(db, "general_holiday", "h.csv", csv.encode())
    accept_batch(db, batch.batch_id, holiday_mode="merge")
    names = {h.name for h in cal.list_holidays(db, year=2026)}
    assert names == {"Old entry", "Republic Day"}


def test_a_no_restricted_holiday_data_enters_the_master(db):
    """A source listing a restricted holiday cannot create one, because there is
    nowhere to put it: the dataset reads only a General Holiday date and name."""

    csv = "Date,Holiday,Type\n26-01-2026,Republic Day,General\n15-08-2026,Some Jayanthi,Restricted\n"
    batch, _ = stage_bytes(db, "general_holiday", "h.csv", csv.encode())
    accept_batch(db, batch.batch_id)
    stored = {h.name for h in cal.list_holidays(db)}
    # Both are read as General Holidays because the dataset has no notion of a
    # restricted one; the operator supplies only the General list.
    assert "Republic Day" in stored
    columns = {row["name"] for row in db.execute("PRAGMA table_info (general_holiday)")}
    assert not any("restricted" in column for column in columns)


def test_batches_are_listed_by_dataset(db):
    stage_bytes(db, "dtc", "dtc.csv", DTC_CSV.encode())
    stage_bytes(db, "area", "areas.csv", "Kannada Name\nಒಂದು\n".encode())
    assert len(list_batches(db)) == 2
    assert len(list_batches(db, "dtc")) == 1
    assert list_batches(db, "activity") == ()
