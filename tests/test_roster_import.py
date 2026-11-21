"""The office's own DTC roster, imported at its real size.

The roster is operational data and is therefore not in this repository. These
tests build a roster of the same shape as the office file — 113 SCD rows with the
settled class split, beside the five SCF and one SCE rows the source carries — so
the reconciliation, the class mapping, the owner-confirmed feeder assignments and a
whole coverage cycle are exercised at the size they are actually used at.

`test_the_office_copy_reconciles_when_it_is_present` runs against the real file
when this checkout has one and skips otherwise, which is the only test here that
needs data the repository does not carry.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application.draft_generation import DraftMode, generate_draft
from dailyworkreg.application.draft_requests import (
    GenerationSpec,
    _cycle_weight_model,
    prepare_generation,
)
from dailyworkreg.application.generation_runs import save_run
from dailyworkreg.application.readiness import assess_readiness
from dailyworkreg.domain.cycle import DtcClass, MonthKey
from dailyworkreg.domain.validator import Asset, AssetType
from dailyworkreg.infrastructure.imports.tabular import SourceFormatError, read_source_file
from dailyworkreg.infrastructure.sqlite import masters, open_database
from dailyworkreg.infrastructure.sqlite.imports import (
    C_IMPORT_ORDER_UNMET,
    accept_batch,
    get_batch,
    reject_batch,
    stage_bytes,
)
from master_fixtures import seed_office_masters

HEADER = "DTC ID,DTC Name,Class,Feeder,kVA,Station\n"

#: The office file these tests stand in for. It is git-ignored: the roster names
#: the office's own installations.
OFFICE_ROSTER = (
    Path(__file__).resolve().parents[1] / "data" / "record_A" / "01_dtc_master_roster.csv"
)

#: The feeders the seeded master already carries, by name.
MRS = "MRS F-1 OLDTOWN"
MANDLI_13 = "Mandli F-13 MANDLI BYEPASS"
MANDLI_22 = "Mandli F-22 GANDHIBAZAAR"

#: Rows this fixture gives a feeder its own number would not have chosen, so a file whose
#: text drifts from the row's station is exercised. Nothing in the application prefers one.
CONFLICTING_SOURCE = {"SCD-109": MANDLI_22, "SCD-110": MANDLI_22}
NT_ROAD = "Mandli F-3 NT ROAD"


def _roster_rows() -> list[tuple[str, str, str, str, str, str]]:
    """113 SCD rows: 68 DEPT, 42 MSB and 3 OWN, plus the six excluded rows.

    The six excluded rows are kept because the source carries them and the
    importer, not whoever prepared the file, decides they are excluded.
    """

    rows: list[tuple[str, str, str, str, str, str]] = []
    for number in range(1, 114):
        source_class = "DEPT" if number <= 68 else ("MSB" if number <= 110 else "OWN")
        capacity = "250" if source_class == "DEPT" else "63"
        feeder = MRS if number % 2 else MANDLI_13
        station = "110/11KV MRS" if feeder == MRS else "110/11KV MANDLI"
        rows.append(
            (f"SCD-{number}", f"INSTALLATION {number}", source_class, feeder, capacity, station)
        )
    rows.append(("SCF-3", "SECTION FEEDER 3", "SCF", NT_ROAD, "100", "110/11KV MRS"))
    rows.append(("SCF-5", "SECTION FEEDER 5", "SCF", NT_ROAD, "100", "110/11KV MRS"))
    rows.append(("SCF-94", "SECTION FEEDER 94", "SCF", NT_ROAD, "100", "110/11KV MRS"))
    rows.append(("SCF-112", "SECTION FEEDER 112", "SCF", NT_ROAD, "100", "110/11KV MRS"))
    rows.append(("SCF-140", "SECTION FEEDER 140", "SCF", NT_ROAD, "100", "110/11KV MRS"))
    rows.append(("SCE-3", "SECTION CONSUMER 3", "SCE", NT_ROAD, "100", "110/11KV MRS"))

    # Two rows are deliberately given a feeder the master carries under a different name,
    # so the warning that names an unmatched feeder is exercised rather than quietly dead.
    # The feeder each row names is otherwise the one its own number assigned above: there is
    # no assignment table in the application any more, only the file and the master.
    for dtc_id, supplied in CONFLICTING_SOURCE.items():
        index = int(dtc_id.split("-")[1]) - 1
        dtc_id, name, source_class, _, capacity, station = rows[index]
        rows[index] = (dtc_id, name, source_class, supplied, capacity, station)
    return rows


def _csv(rows: list[tuple[str, str, str, str, str, str]]) -> bytes:
    body = "".join(",".join(row) + "\n" for row in rows)
    return (HEADER + body).encode("utf-8")


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "roster.db")
    seed_office_masters(connection)
    yield connection
    connection.close()


def _stage_roster(connection: sqlite3.Connection, rows=None):
    return stage_bytes(connection, "dtc", "roster.csv", _csv(rows or _roster_rows()))


# --- reconciliation -------------------------------------------------------


def test_the_whole_roster_reconciles_to_the_settled_counts(db: sqlite3.Connection) -> None:
    batch, verdict = _stage_roster(db)

    included = [outcome for outcome in verdict.outcomes if outcome.decision == "include"]
    excluded = [outcome for outcome in verdict.outcomes if outcome.decision != "include"]
    classes = {}
    for outcome in included:
        classes[outcome.payload["source_class"]] = (
            classes.get(outcome.payload["source_class"], 0) + 1
        )
    canonical = {}
    for outcome in included:
        canonical[outcome.payload["dtc_class"]] = (
            canonical.get(outcome.payload["dtc_class"], 0) + 1
        )

    assert batch.row_count == 119
    assert batch.error_count == 0
    assert len(included) == 113
    assert classes == {"DEPT": 68, "MSB": 42, "OWN": 3}
    assert canonical == {"DEPT": 68, "SELF_DTC": 45}
    assert len(excluded) == 6
    # An excluded row keeps its supplied values but has no proposed record, so the
    # class is read from the source row rather than from a payload that does not exist.
    assert {o.raw["Class"] for o in excluded} == {"SCF", "SCE"}
    assert all("not imported" in " ".join(o.messages) for o in excluded)

    # No row is reported against an assignment table, because the application has none: the
    # canonical feeder is the master's record of the name the row supplies, and only an
    # unmatched name is worth a word before acceptance.
    assert not any(
        "owner-confirmed assignment" in " ".join(outcome.messages) for outcome in included
    )


def test_staging_the_roster_changes_no_canonical_row(db: sqlite3.Connection) -> None:
    _stage_roster(db)

    assert db.execute("SELECT COUNT(*) FROM dtc").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM dtc_source_record").fetchone()[0] == 0


def test_acceptance_stores_the_settled_split_and_every_source_field(
    db: sqlite3.Connection,
) -> None:
    rows = _roster_rows()
    batch, _ = _stage_roster(db, rows)
    result = accept_batch(db, batch.batch_id)

    assert result.inserted == 113
    assert result.updated == 0
    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    assert len(stored) == 113

    canonical = {}
    source = {}
    for row in stored.values():
        canonical[row.dtc_class] = canonical.get(row.dtc_class, 0) + 1
        source[row.source_class] = source.get(row.source_class, 0) + 1
    assert canonical == {"DEPT": 68, "SELF_DTC": 45}
    assert source == {"DEPT": 68, "MSB": 42, "OWN": 3}

    supplied = {row[0]: row for row in rows}
    for dtc_id, row in stored.items():
        original = supplied[dtc_id]
        assert row.name == original[1]
        assert row.kva == float(original[4])
        assert row.station == original[5]
        assert row.canonical_feeder_id is not None

    kept = db.execute(
        "SELECT dtc_id, raw_name, raw_feeder, raw_kva, raw_station, raw_class "
        "FROM dtc_source_record WHERE batch_id = ?",
        (batch.batch_id,),
    ).fetchall()
    assert len(kept) == 113
    for dtc_id, name, feeder, kva, station, source_class in kept:
        original = supplied[dtc_id]
        assert (name, feeder, kva, station, source_class) == (
            original[1],
            original[3],
            original[4],
            original[5],
            original[2],
        )


def test_the_batch_names_the_file_it_came_from(db: sqlite3.Connection) -> None:
    batch, _ = _stage_roster(db)

    stored = get_batch(db, batch.batch_id)
    assert stored.source_filename == "roster.csv"
    assert stored.source_sha256 == batch.source_sha256
    assert len(stored.source_sha256) == 64


# --- the file decides which feeder a transformer sits on ------------------


def test_the_file_decides_and_nothing_second_guesses_it(
    db: sqlite3.Connection,
) -> None:
    """Every transformer keeps the canonical feeder its own row named.

    The application carried six owner-confirmed assignments and applied them on acceptance
    until 2026-11-21, when the owner withdrew the mechanism: the roster is the office's own
    record, and a second list beside it applies one office's judgement to the next office's
    transformers. So a row naming the master's `MRS F-1 OLDTOWN` lands on F-01 and a row
    naming `Mandli F-22 GANDHIBAZAAR` lands on F-03, with the supplied text kept beside it.
    """

    batch, verdict = _stage_roster(db)
    result = accept_batch(db, batch.batch_id)

    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    for dtc_id, feeder_name in CONFLICTING_SOURCE.items():
        wanted = masters.find_feeder_by_name(db, feeder_name)
        assert stored[dtc_id].canonical_feeder_id == wanted.feeder_id, dtc_id
        kept = db.execute(
            "SELECT raw_feeder FROM dtc_source_record WHERE batch_id = ? AND dtc_id = ?",
            (batch.batch_id, dtc_id),
        ).fetchone()
        assert kept[0] == feeder_name

    # The rows the fixture left alone keep the feeder their own number assigned.
    for number in (1, 2, 3, 4, 5, 6, 7):
        assert stored[f"SCD-{number}"].canonical_feeder_id is not None

    # No row is reported as reassigned, because nothing reassigns anything.
    assert not any(
        "owner-confirmed assignment" in " ".join(outcome.messages)
        for outcome in verdict.outcomes
    )
    assert result.inserted == 113


def test_an_application_has_no_assignment_count_to_report(db: sqlite3.Connection) -> None:
    """The acceptance result no longer carries a count of overrides applied.

    The count is not merely always zero: the field is gone, so a caller cannot read a number
    that implies a second authority existed.
    """

    batch, _ = _stage_roster(db)
    result = accept_batch(db, batch.batch_id)

    assert not hasattr(result, "feeder_overrides_applied")


def test_a_blank_feeder_cell_is_a_warning_and_assigns_nothing(
    db: sqlite3.Connection,
) -> None:
    """A gap in the office's own sheet is accepted and said out loud.

    A blank cell is not a name that fails to resolve, so it does not stop the file: the row is
    accepted, the transformer is left with no canonical feeder and is not reached through any
    feeder's capacity, and the row says so. A name the master does not carry is refused at read
    time instead, which `tests/test_import_order.py` pins.
    """

    rows = _roster_rows()
    rows[0] = (rows[0][0], rows[0][1], rows[0][2], "", "250", "S")
    batch, verdict = _stage_roster(db, rows)

    silent = [
        outcome
        for outcome in verdict.outcomes
        if any("no feeder is named" in message for message in outcome.messages)
    ]
    assert [outcome.payload["dtc_id"] for outcome in silent] == ["SCD-1"]
    assert batch.error_count == 0
    assert batch.warning_count == len([o for o in verdict.outcomes if o.messages])

    accept_batch(db, batch.batch_id)
    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    assert stored["SCD-1"].canonical_feeder_id is None
    kept = db.execute(
        "SELECT raw_feeder FROM dtc_source_record WHERE dtc_id = ?", ("SCD-1",)
    ).fetchone()
    assert kept[0] == ""


def test_a_roster_read_before_its_feeder_master_is_refused(
    tmp_path: Path,
) -> None:
    """A roster with no masters to meet is refused at the moment it is read.

    Earlier commits in this act withdrew the assignment table and then made the order of work
    a rule: a transformer resolves to a feeder only through the feeder master, so a roster
    read first would leave every transformer unassigned, every feeder's capacity short and the
    operator reading it all again. `tests/test_import_order.py` states the rule and its
    messages; this pins that the real office roster is refused the same way, before any batch
    or canonical row exists.
    """

    connection = open_database(tmp_path / "bare.db")
    try:
        with pytest.raises(SourceFormatError) as caught:
            _stage_roster(connection)
        assert caught.value.constraint == C_IMPORT_ORDER_UNMET
        assert connection.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM dtc").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM import_staging_row").fetchone()[0] == 0
        )
    finally:
        connection.close()


# --- the same file twice, and a refusal ----------------------------------


def test_re_uploading_the_roster_updates_rather_than_duplicates(
    db: sqlite3.Connection,
) -> None:
    first, _ = _stage_roster(db)
    assert accept_batch(db, first.batch_id).inserted == 113

    second, _ = _stage_roster(db)
    result = accept_batch(db, second.batch_id)
    assert (result.inserted, result.updated) == (0, 113)
    assert db.execute("SELECT COUNT(*) FROM dtc").fetchone()[0] == 113


def test_rejecting_the_roster_leaves_the_master_as_it_was(db: sqlite3.Connection) -> None:
    batch, _ = _stage_roster(db)
    reject_batch(db, batch.batch_id, note="wrong extract")

    assert db.execute("SELECT COUNT(*) FROM dtc").fetchone()[0] == 0
    assert get_batch(db, batch.batch_id).status == "rejected"


# --- what the roster is good for downstream -------------------------------


def test_the_imported_roster_carries_its_classes_into_the_weight_model(
    db: sqlite3.Connection,
) -> None:
    batch, _ = _stage_roster(db)
    accept_batch(db, batch.batch_id)

    weights = _cycle_weight_model(db)
    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    department = stored["SCD-1"]
    self_dtc = stored["SCD-69"]

    assert department.dtc_class == "DEPT"
    assert self_dtc.dtc_class == "SELF_DTC"
    assert weights.class_of(Asset(department.dtc_id, AssetType.DTC)) is DtcClass.DEPARTMENT
    assert weights.class_of(Asset(self_dtc.dtc_id, AssetType.DTC)) is DtcClass.SELF_DTC
    assert weights.weight_for(Asset(department.dtc_id, AssetType.DTC)) == pytest.approx(
        250.0
    )
    assert weights.weight_for(Asset(self_dtc.dtc_id, AssetType.DTC)) == pytest.approx(
        63.0 * 0.20
    )


def test_a_two_month_cycle_over_the_whole_roster_allocates_completely(
    db: sqlite3.Connection,
) -> None:
    """One month cannot hold 113 DTCs, so the cycle spreads them over two."""

    batch, _ = _stage_roster(db)
    accept_batch(db, batch.batch_id)
    roster = {row.dtc_id for row in masters.list_dtcs(db)}

    spec = GenerationSpec(
        start=date(2026, 1, 1),
        end=date(2026, 2, 28),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=7,
        cycle_start_month=MonthKey(2026, 1),
        cycle_duration_months=2,
        cycle_visits_per_asset=1,
        # Three transformers a working day: 45 working dates over January and
        # February hold the 113 duties, and the January dates alone do not.
        dtc_daily_cap=3,
    )
    prepared = prepare_generation(db, spec)
    draft = generate_draft(prepared.request)
    save_run(db, prepared, draft)

    assert draft.status.value == "allocation_complete"
    assert len(draft.rows) == 59
    visits = [visit for month in draft.months for visit in month.visits]
    assert {visit.asset_id for visit in visits if visit.asset_id in roster} == roster
    assert len([visit for visit in visits if visit.asset_id in roster]) == 113
    assert not [row for row in draft.rows if not row.is_working_day and row.visits]


def test_readiness_counts_the_imported_roster(db: sqlite3.Connection) -> None:
    batch, _ = _stage_roster(db)
    accept_batch(db, batch.batch_id)

    report = assess_readiness(db, date(2026, 1, 1), date(2026, 1, 31))
    dtc_item = next(item for item in report.items if item.key == "roster.dtc")
    assert dtc_item.satisfied is True
    assert "113" in dtc_item.detail


# --- the office's own copy, when this checkout has one --------------------


@pytest.mark.skipif(
    not OFFICE_ROSTER.exists(),
    reason="the office roster is local data and is not part of the repository",
)
def test_the_office_copy_reconciles_when_it_is_present(db: sqlite3.Connection) -> None:
    source = read_source_file(OFFICE_ROSTER)
    batch, verdict = stage_bytes(db, "dtc", source.filename, OFFICE_ROSTER.read_bytes())

    assert batch.error_count == 0
    included = [outcome for outcome in verdict.outcomes if outcome.decision == "include"]
    assert len(included) == 113
    classes = {}
    for outcome in included:
        classes[outcome.payload["source_class"]] = (
            classes.get(outcome.payload["source_class"], 0) + 1
        )
    assert classes == {"DEPT": 68, "MSB": 42, "OWN": 3}

    result = accept_batch(db, batch.batch_id)
    assert result.inserted == 113
    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    # Every transformer holds the feeder its own row names, resolved against the master.
    assert all(row.canonical_feeder_id for row in stored.values())
    for outcome in included:
        raw = outcome.payload.get("feeder")
        if not raw:
            continue
        wanted = masters.find_feeder_by_name(db, raw)
        assert stored[outcome.payload["dtc_id"]].canonical_feeder_id == (
            wanted.feeder_id if wanted else None
        )
    assert all(row.kva is not None for row in stored.values())
    # The office's own file names Mandli F-22 for these two, and the owner settled
    # that the file is what the office goes by.
    assert stored["SCD-107"].canonical_feeder_id == "F-03"
    assert stored["SCD-108"].canonical_feeder_id == "F-03"
