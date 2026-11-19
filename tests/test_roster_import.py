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
from dailyworkreg.infrastructure.imports.tabular import read_source_file
from dailyworkreg.infrastructure.sqlite import masters, open_database
from dailyworkreg.infrastructure.sqlite.imports import (
    accept_batch,
    get_batch,
    reject_batch,
    stage_bytes,
)
from dailyworkreg.infrastructure.sqlite.seed import (
    APPROVED_FEEDER_OVERRIDES,
    seed_approved_identities,
)

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

    # Of the six owner-confirmed assignments, four agree with the supplied feeder
    # text and two (SCD-107, SCD-108) disagree with it. That is the shape of the
    # office file, and it is what makes the override visible rather than a no-op.
    for dtc_id, feeder_name in APPROVED_FEEDER_OVERRIDES.items():
        index = int(dtc_id.split("-")[1]) - 1
        dtc_id, name, source_class, _, capacity, station = rows[index]
        supplied = MANDLI_22 if dtc_id in ("SCD-107", "SCD-108") else feeder_name
        rows[index] = (dtc_id, name, source_class, supplied, capacity, station)
    return rows


def _csv(rows: list[tuple[str, str, str, str, str, str]]) -> bytes:
    body = "".join(",".join(row) + "\n" for row in rows)
    return (HEADER + body).encode("utf-8")


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "roster.db")
    seed_approved_identities(connection)
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

    # Every row whose canonical feeder differs from what the supplied text resolves
    # to says so before acceptance, so the override is visible during review.
    notices = {
        outcome.payload["dtc_id"]: outcome.messages
        for outcome in included
        if any("owner-confirmed assignment" in m for m in outcome.messages)
    }
    assert set(notices) == {"SCD-107", "SCD-108"}


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


# --- the owner-confirmed feeder assignments -------------------------------


def test_the_six_settled_assignments_win_over_conflicting_source_text(
    db: sqlite3.Connection,
) -> None:
    """SCD-107 and SCD-108 are the two the source text disagrees about.

    Their supplied feeder is Mandli F-22, and the settled assignment is MRS F-1.
    The import must keep the supplied text and set the canonical feeder apart from
    it, so both facts survive.
    """

    batch, _ = _stage_roster(db)
    result = accept_batch(db, batch.batch_id)

    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    for dtc_id, feeder_name in APPROVED_FEEDER_OVERRIDES.items():
        wanted = masters.find_feeder_by_name(db, feeder_name)
        assert stored[dtc_id].canonical_feeder_id == wanted.feeder_id, dtc_id

    assert stored["SCD-107"].canonical_feeder_id == "F-01"
    kept = db.execute(
        "SELECT raw_feeder FROM dtc_source_record WHERE batch_id = ? AND dtc_id = ?",
        (batch.batch_id, "SCD-107"),
    ).fetchone()
    assert kept[0] == MANDLI_22
    # The other four source rows already agree with their assignment, so only the
    # two that disagree are rewritten.
    assert result.feeder_overrides_applied == 2


def test_an_unmapped_feeder_stays_a_warning_and_assigns_nothing(
    db: sqlite3.Connection,
) -> None:
    rows = _roster_rows()
    rows[0] = (rows[0][0], rows[0][1], rows[0][2], "A FEEDER NOBODY HAS RECORDED", "250", "S")
    batch, verdict = _stage_roster(db, rows)

    mentioned = [
        outcome
        for outcome in verdict.outcomes
        if any("not in the feeder master" in message for message in outcome.messages)
    ]
    assert [outcome.payload["dtc_id"] for outcome in mentioned] == ["SCD-1"]
    assert batch.error_count == 0
    spoken = [outcome for outcome in verdict.outcomes if outcome.messages]
    assert batch.warning_count == len(spoken)
    assert any(
        "owner-confirmed assignment" in " ".join(o.messages) for o in verdict.outcomes
    )

    accept_batch(db, batch.batch_id)
    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    assert stored["SCD-1"].canonical_feeder_id is None
    kept = db.execute(
        "SELECT raw_feeder FROM dtc_source_record WHERE dtc_id = ?", ("SCD-1",)
    ).fetchone()
    assert kept[0] == "A FEEDER NOBODY HAS RECORDED"


def test_a_roster_cannot_be_accepted_before_its_feeder_master(tmp_path: Path) -> None:
    """Without the feeders, six assignments cannot be made, so acceptance is refused.

    Accepting anyway would store 113 DTCs with no canonical feeder and leave every
    feeder's capacity short, which is the failure the assignment exists to prevent.
    """

    connection = open_database(tmp_path / "bare.db")
    try:
        batch, verdict = _stage_roster(connection)
        refused = [
            outcome.payload["dtc_id"]
            for outcome in verdict.outcomes
            if outcome.severity == "error"
        ]
        assert sorted(refused) == sorted(APPROVED_FEEDER_OVERRIDES)
        assert batch.error_count == 6

        with pytest.raises(Exception) as raised:
            accept_batch(connection, batch.batch_id)
        assert "cannot be imported" in str(raised.value)
        assert connection.execute("SELECT COUNT(*) FROM dtc").fetchone()[0] == 0
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
    notices = [
        outcome.payload["dtc_id"]
        for outcome in included
        if any("owner-confirmed assignment" in m for m in outcome.messages)
    ]
    assert notices == ["SCD-107", "SCD-108"]
    stored = {row.dtc_id: row for row in masters.list_dtcs(db)}
    for dtc_id, feeder_name in APPROVED_FEEDER_OVERRIDES.items():
        wanted = masters.find_feeder_by_name(db, feeder_name)
        assert stored[dtc_id].canonical_feeder_id == wanted.feeder_id, dtc_id
    assert all(row.canonical_feeder_id for row in stored.values())
    assert all(row.kva is not None for row in stored.values())
