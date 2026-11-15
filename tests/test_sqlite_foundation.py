"""SQLite foundation: schema, migrations, foreign keys and master repositories."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from dailyworkreg.infrastructure.sqlite import (
    MIGRATIONS,
    SCHEMA_VERSION,
    StorageError,
    applied_versions,
    apply_migrations,
    default_data_dir,
    is_inside,
    open_database,
    resolve_db_path,
)
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import DB_PATH_ENV
from dailyworkreg.infrastructure.sqlite.seed import (
    APPROVED_AREAS,
    APPROVED_FEEDERS,
    APPROVED_LEAVE_TYPES,
    seed_approved_identities,
)


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "test.db")
    yield connection
    connection.close()


# --- schema and migrations ------------------------------------------------


def test_a_fresh_database_records_every_migration(db):
    # Every declared migration is recorded, and the newest one matches the
    # declared schema version. Asserting a literal tuple would make adding a
    # migration look like a regression.
    assert applied_versions(db) == tuple(m.version for m in MIGRATIONS)
    assert applied_versions(db)[-1] == SCHEMA_VERSION


def test_migrations_are_idempotent(db):
    before = applied_versions(db)
    assert apply_migrations(db) == ()
    assert applied_versions(db) == before


def test_the_database_file_is_created_where_asked(tmp_path):
    target = tmp_path / "nested" / "dir" / "x.db"
    connection = open_database(target)
    connection.close()
    assert target.exists()


def test_foreign_keys_are_enforced(db):
    # An unassigned feeder is legitimate: not every DTC has a canonical one yet.
    db.execute(
        "INSERT INTO dtc (dtc_id, name, dtc_class, valid_from_version, updated_at) "
        "VALUES ('SCD-999', 'x', 'DEPT', 1, 'now')"
    )
    # A reference to a feeder that does not exist is not.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO dtc (dtc_id, name, dtc_class, canonical_feeder_id, "
            "valid_from_version, updated_at) "
            "VALUES ('SCD-998', 'x', 'DEPT', 'NO-SUCH', 1, 'now')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO area_alias (area_id, name_kn) VALUES ('NO-SUCH', 'x')"
        )


def test_a_missing_leave_type_is_refused(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO leave_record (leave_id, leave_date, leave_type_id, "
            "valid_from_version, updated_at) VALUES ('L1', '2026-11-02', 'NOPE', 1, 'now')"
        )


def test_schema_has_no_restricted_holiday_storage(db):
    names = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "general_holiday" in names
    assert not any("restricted" in name for name in names)
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info (general_holiday)")
    }
    assert not any("restricted" in column for column in columns)


def test_the_default_location_is_outside_a_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv(DB_PATH_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    path = resolve_db_path()
    assert is_inside(path, tmp_path) is True
    # And it is not under the repository the code lives in.
    repository_root = Path(__file__).resolve().parents[1]
    assert is_inside(path, repository_root) is False


def test_an_explicit_environment_path_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "from-env.db"))
    assert resolve_db_path() == tmp_path / "from-env.db"
    assert resolve_db_path(tmp_path / "arg.db") == tmp_path / "arg.db"


# --- master version -------------------------------------------------------


def test_every_master_write_advances_the_version(db):
    before = masters.current_version(db)
    masters.upsert_area(db, area_id="A-01", name_kn="ಗಾಂಧಿಬಜಾರ್")
    after_area = masters.current_version(db)
    assert after_area == before + 1
    masters.upsert_feeder(db, feeder_id="F-01", name="MRS F-1 OLDTOWN")
    assert masters.current_version(db) == after_area + 1
    masters.upsert_dtc(db, dtc_id="SCD-1", name="d", dtc_class="DEPT")
    assert masters.current_version(db) == after_area + 2


def test_an_update_advances_both_versions(db):
    first = masters.upsert_area(db, area_id="A-01", name_kn="ಒಂದು")
    second = masters.upsert_area(db, area_id="A-01", name_kn="ಎರಡು", name_en="Two")
    assert first.revision == 1
    assert second.revision == 2
    assert second.name_en == "Two"
    assert masters.current_version(db) == 2


def test_settings_round_trip(db):
    masters.set_setting(db, "server.port", "8000")
    assert masters.get_setting(db, "server.port") == "8000"
    assert masters.get_setting(db, "missing", "fallback") == "fallback"


# --- areas ----------------------------------------------------------------


def test_area_aliases_are_stored_and_replaced(db):
    masters.upsert_area(
        db, area_id="A-01", name_kn="ಗಾಂಧಿಬಜಾರ್", aliases=("ಗಾಂಧಿ ಬಜಾರ್",)
    )
    assert masters.get_area(db, "A-01").aliases == ("ಗಾಂಧಿ ಬಜಾರ್",)
    masters.upsert_area(db, area_id="A-01", name_kn="ಗಾಂಧಿಬಜಾರ್", aliases=())
    assert masters.get_area(db, "A-01").aliases == ()


def test_an_area_without_an_english_name_displays_its_stored_name(db):
    area = masters.upsert_area(db, area_id="A-01", name_kn="ಗಾಂಧಿಬಜಾರ್")
    assert area.name_en is None
    assert area.display_en == "ಗಾಂಧಿಬಜಾರ್"


# --- feeders --------------------------------------------------------------


def test_a_feeder_in_use_cannot_be_deleted(db):
    feeder = masters.upsert_feeder(db, feeder_id="F-01", name="MRS F-1 OLDTOWN")
    masters.upsert_dtc(
        db, dtc_id="SCD-1", name="d", dtc_class="DEPT",
        canonical_feeder_id=feeder.feeder_id,
    )
    with pytest.raises(StorageError) as caught:
        masters.delete_feeder(db, "F-01")
    assert caught.value.constraint == "feeder_referenced_by_dtc"
    assert masters.get_feeder(db, "F-01") is not None


def test_a_feeder_nobody_uses_can_be_deleted(db):
    masters.upsert_feeder(db, feeder_id="F-01", name="MRS F-1 OLDTOWN")
    masters.delete_feeder(db, "F-01")
    assert masters.get_feeder(db, "F-01") is None


# --- DTCs -----------------------------------------------------------------


def test_dtc_keeps_the_source_class_apart_from_the_scheduling_class(db):
    row = masters.upsert_dtc(
        db, dtc_id="SCD-1", name="d", dtc_class="SELF_DTC", source_class="MSB"
    )
    assert row.dtc_class == "SELF_DTC"
    assert row.source_class == "MSB"
    assert row.is_department is False


def test_dtc_kva_and_station_survive_as_null_when_absent(db):
    row = masters.upsert_dtc(db, dtc_id="SCD-2", name="d", dtc_class="DEPT")
    assert row.kva is None
    assert row.station is None


def test_an_unknown_dtc_class_is_refused_by_the_schema(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO dtc (dtc_id, name, dtc_class, valid_from_version, updated_at) "
            "VALUES ('SCD-3', 'd', 'OWN', 1, 'now')"
        )


# --- seed -----------------------------------------------------------------


def test_the_approved_identities_are_seeded_once(db):
    first = seed_approved_identities(db)
    assert first == {
        "areas": len(APPROVED_AREAS),
        "feeders": len(APPROVED_FEEDERS),
        "leave_types": len(APPROVED_LEAVE_TYPES),
    }
    second = seed_approved_identities(db)
    assert second == {"areas": 0, "feeders": 0, "leave_types": 0}
    assert len(masters.list_areas(db)) == 16
    assert len(masters.list_feeders(db)) == 4


def test_seeding_does_not_overwrite_an_operators_edit(db):
    seed_approved_identities(db)
    masters.upsert_area(db, area_id="A-01", name_kn="ಒಂದು", name_en="Gandhibazaar")
    seed_approved_identities(db, force=True)
    area = masters.get_area(db, "A-01")
    assert area.name_kn == "ಒಂದು"
    assert area.name_en == "Gandhibazaar"
    assert area.revision == 2


def test_no_activity_or_holiday_is_seeded(db):
    """No approved catalogue or official holiday list exists, so none is invented."""

    seed_approved_identities(db)
    assert masters.list_activities(db) == ()
    assert cal.list_holidays(db) == ()


def test_seeded_areas_keep_their_approved_aliases(db):
    seed_approved_identities(db)
    assert masters.get_area(db, "A-01").aliases == ("ಗಾಂಧಿ ಬಜಾರ್",)
    assert masters.get_area(db, "A-07").aliases == ("KR ಪುರಂ",)


# --- calendar records -----------------------------------------------------


def test_holiday_round_trip_and_year_filter(db):
    cal.upsert_holiday(
        db, holiday_id="H-1", holiday_date=date(2026, 1, 26), name="Republic Day",
        source="karnataka",
    )
    cal.upsert_holiday(
        db, holiday_id="H-2", holiday_date=date(2025, 8, 15), name="Independence Day",
        source="karnataka",
    )
    assert len(cal.list_holidays(db)) == 2
    assert len(cal.list_holidays(db, year=2026)) == 1
    assert cal.holidays_on(db, date(2026, 1, 26))[0].name == "Republic Day"


def test_replacing_a_year_keeps_operator_entries(db):
    cal.upsert_holiday(
        db, holiday_id="H-1", holiday_date=date(2026, 1, 26), name="Republic Day",
        source="karnataka",
    )
    cal.upsert_holiday(
        db, holiday_id="H-9", holiday_date=date(2026, 3, 1), name="Office event",
        source="operator",
    )
    deleted = cal.replace_holiday_year(db, 2026, source="karnataka", merge=False)
    assert deleted == 1
    remaining = cal.list_holidays(db, year=2026)
    assert [h.holiday_id for h in remaining] == ["H-9"]


def test_merge_mode_deletes_nothing(db):
    cal.upsert_holiday(
        db, holiday_id="H-1", holiday_date=date(2026, 1, 26), name="Republic Day",
        source="karnataka",
    )
    assert cal.replace_holiday_year(db, 2026, source="karnataka", merge=True) == 0
    assert len(cal.list_holidays(db, year=2026)) == 1


def test_leave_type_and_record_round_trip(db):
    cal.upsert_leave_type(db, leave_type_id="CL", name="Casual Leave")
    cal.upsert_leave_record(
        db, leave_id="L-1", leave_date=date(2026, 11, 18), leave_type_id="CL"
    )
    records = cal.list_leave_records(db)
    assert len(records) == 1
    assert records[0].leave_type_name == "Casual Leave"
    assert records[0].leave_date == date(2026, 11, 18)


def test_events_and_overrides_round_trip(db):
    cal.upsert_event(
        db, event_id="E-1", event_date=date(2026, 11, 10),
        title_en="Safety workshop", title_kn="ಸುರಕ್ಷತಾ ಕಾರ್ಯಾಗಾರ",
    )
    assert cal.list_events(db)[0].title_en == "Safety workshop"

    cal.upsert_override(
        db, override_id="O-1", override_date=date(2026, 11, 3),
        is_working_day=True, reason_en="Section work",
    )
    overrides = cal.list_overrides(db)
    assert overrides[0].is_working_day is True
    # Re-stating the same date updates it rather than adding a second row.
    cal.upsert_override(
        db, override_id="O-1", override_date=date(2026, 11, 3),
        is_working_day=False, reason_en="Holiday",
    )
    assert len(cal.list_overrides(db)) == 1
    assert cal.list_overrides(db)[0].is_working_day is False
    assert cal.list_overrides(db)[0].revision == 2


# --- source records -------------------------------------------------------


def test_source_records_keep_blank_and_na_values_verbatim(db):
    with db:
        db.execute(
            """
            INSERT INTO dtc_source_record (dtc_id, batch_id, row_number, raw_name,
                raw_feeder, raw_kva, raw_class)
            VALUES ('SCD-5', 'B1', 5, 'Some DTC', '', '#N/A', 'MSB')
            """
        )
    record = masters.list_dtc_source_records(db)[0]
    assert record.raw_feeder == ""
    assert record.raw_kva == "#N/A"
    assert record.raw_class == "MSB"
    assert record.dtc_id == "SCD-5"