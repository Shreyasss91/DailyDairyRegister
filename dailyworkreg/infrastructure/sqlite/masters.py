"""Repositories for the canonical asset masters: areas, feeders, DTCs, activities.

Every write bumps one monotonic master version inside the caller's transaction, so
a generation run can record the state it read. Writes never replace a record in
place without incrementing its own revision, which is what allows a later reader to
see that a record changed rather than silently reading a different value.

Nothing here decides anything: a repository stores the value it is given. Whether a
DTC is a cycle member, what its weight is and when it may be visited are domain
questions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from .database import transaction

#: Raised when a caller asks for something the schema cannot hold. Kept separate
#: from the domain's InputError because it describes a storage problem, not a
#: scheduling one.
C_DUPLICATE = "master_record_duplicate"
C_UNKNOWN = "master_record_unknown"
C_FEEDER_IN_USE = "feeder_referenced_by_dtc"


class StorageError(ValueError):
    """A statement could not be applied to the canonical store."""

    def __init__(self, constraint: str, detail: str, record_id: str | None = None):
        self.constraint = constraint
        self.detail = detail
        self.record_id = record_id
        super().__init__(f"{constraint}: {detail}")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso_date(value: date | str | None) -> str | None:
    """The stored form of a date: ISO text, or ``NULL`` when there is none.

    Accepts a date or the ISO text the import already parsed, so the caller never
    has to know how the column is stored. It never guesses at an unreadable value:
    parsing happened when the file was read, and a value that could not be read then
    is ``None`` here.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def current_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT version FROM master_version WHERE id = 1").fetchone()
    return int(row["version"]) if row is not None else 0


def bump_version(connection: sqlite3.Connection) -> int:
    """Increment the master version. Call inside an open write transaction."""

    connection.execute(
        "UPDATE master_version SET version = version + 1, updated_at = ? WHERE id = 1",
        (_now(),),
    )
    return current_version(connection)


# --- settings -------------------------------------------------------------


def get_setting(connection: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = connection.execute("SELECT value FROM app_setting WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value,
                                            updated_at = excluded.updated_at
            """,
            (key, value, _now()),
        )


# --- areas ----------------------------------------------------------------


@dataclass(frozen=True)
class AreaRow:
    area_id: str
    name_kn: str
    name_en: str | None
    weight: float
    monthly_min: int
    monthly_max: int
    revisit_gap_days: int
    active: bool
    revision: int
    aliases: tuple[str, ...] = field(default=())

    @property
    def display_en(self) -> str:
        """An English name for display, falling back to the retained Kannada name.

        The fallback is the stored name rather than an invented transliteration:
        this layer transliterates nothing.
        """

        return self.name_en or self.name_kn


def _area_from(row: sqlite3.Row, aliases: tuple[str, ...]) -> AreaRow:
    return AreaRow(
        area_id=row["area_id"],
        name_kn=row["name_kn"],
        name_en=row["name_en"],
        weight=float(row["weight"]),
        monthly_min=int(row["monthly_min"]),
        monthly_max=int(row["monthly_max"]),
        revisit_gap_days=int(row["revisit_gap_days"]),
        active=bool(row["active"]),
        revision=int(row["revision"]),
        aliases=aliases,
    )


def list_areas(connection: sqlite3.Connection) -> tuple[AreaRow, ...]:
    rows = connection.execute("SELECT * FROM area ORDER BY area_id").fetchall()
    alias_rows = connection.execute(
        "SELECT area_id, name_kn FROM area_alias ORDER BY name_kn"
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for alias in alias_rows:
        grouped.setdefault(alias["area_id"], []).append(alias["name_kn"])
    return tuple(_area_from(row, tuple(grouped.get(row["area_id"], ()))) for row in rows)


def get_area(connection: sqlite3.Connection, area_id: str) -> AreaRow | None:
    row = connection.execute("SELECT * FROM area WHERE area_id = ?", (area_id,)).fetchone()
    if row is None:
        return None
    aliases = tuple(
        alias["name_kn"]
        for alias in connection.execute(
            "SELECT name_kn FROM area_alias WHERE area_id = ? ORDER BY name_kn", (area_id,)
        ).fetchall()
    )
    return _area_from(row, aliases)


def upsert_area(
    connection: sqlite3.Connection,
    *,
    area_id: str,
    name_kn: str,
    name_en: str | None = None,
    weight: float = 1.0,
    monthly_min: int = 0,
    monthly_max: int = 3,
    revisit_gap_days: int = 7,
    active: bool = True,
    aliases: tuple[str, ...] = (),
) -> AreaRow:
    """Create or update one area, bumping its revision and the master version.

    An update preserves the record rather than replacing it, so a draft generated
    earlier still describes the state it was built from: its own snapshot is
    untouched while the master moves on.
    """

    with transaction(connection):
        version = bump_version(connection)
        existing = connection.execute(
            "SELECT revision FROM area WHERE area_id = ?", (area_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO area (area_id, name_kn, name_en, weight, monthly_min,
                                  monthly_max, revisit_gap_days, active, revision,
                                  valid_from_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    area_id, name_kn, name_en, weight, monthly_min, monthly_max,
                    revisit_gap_days, int(active), version, _now(),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE area SET name_kn = ?, name_en = ?, weight = ?, monthly_min = ?,
                                monthly_max = ?, revisit_gap_days = ?, active = ?,
                                revision = revision + 1, valid_from_version = ?,
                                updated_at = ?
                WHERE area_id = ?
                """,
                (
                    name_kn, name_en, weight, monthly_min, monthly_max,
                    revisit_gap_days, int(active), version, _now(), area_id,
                ),
            )
        connection.execute("DELETE FROM area_alias WHERE area_id = ?", (area_id,))
        for alias in aliases:
            connection.execute(
                "INSERT OR IGNORE INTO area_alias (area_id, name_kn) VALUES (?, ?)",
                (area_id, alias),
            )
    result = get_area(connection, area_id)
    assert result is not None
    return result


def delete_area(connection: sqlite3.Connection, area_id: str) -> None:
    with transaction(connection):
        connection.execute("DELETE FROM area WHERE area_id = ?", (area_id,))
        bump_version(connection)


# --- feeders --------------------------------------------------------------


@dataclass(frozen=True)
class FeederRow:
    feeder_id: str
    name: str
    manual_multiplier: float
    monthly_min: int
    monthly_max: int
    revisit_gap_days: int
    active: bool
    revision: int


def _feeder_from(row: sqlite3.Row) -> FeederRow:
    return FeederRow(
        feeder_id=row["feeder_id"],
        name=row["name"],
        manual_multiplier=float(row["manual_multiplier"]),
        monthly_min=int(row["monthly_min"]),
        monthly_max=int(row["monthly_max"]),
        revisit_gap_days=int(row["revisit_gap_days"]),
        active=bool(row["active"]),
        revision=int(row["revision"]),
    )


def list_feeders(connection: sqlite3.Connection) -> tuple[FeederRow, ...]:
    return tuple(
        _feeder_from(row)
        for row in connection.execute("SELECT * FROM feeder ORDER BY name").fetchall()
    )


def get_feeder(connection: sqlite3.Connection, feeder_id: str) -> FeederRow | None:
    row = connection.execute(
        "SELECT * FROM feeder WHERE feeder_id = ?", (feeder_id,)
    ).fetchone()
    return _feeder_from(row) if row is not None else None


def find_feeder_by_name(connection: sqlite3.Connection, name: str) -> FeederRow | None:
    row = connection.execute("SELECT * FROM feeder WHERE name = ?", (name,)).fetchone()
    return _feeder_from(row) if row is not None else None


def upsert_feeder(
    connection: sqlite3.Connection,
    *,
    feeder_id: str,
    name: str,
    manual_multiplier: float = 1.0,
    monthly_min: int = 0,
    monthly_max: int = 3,
    revisit_gap_days: int = 7,
    active: bool = True,
) -> FeederRow:
    with transaction(connection):
        version = bump_version(connection)
        existing = connection.execute(
            "SELECT revision FROM feeder WHERE feeder_id = ?", (feeder_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO feeder (feeder_id, name, manual_multiplier, monthly_min,
                                    monthly_max, revisit_gap_days, active, revision,
                                    valid_from_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    feeder_id, name, manual_multiplier, monthly_min, monthly_max,
                    revisit_gap_days, int(active), version, _now(),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE feeder SET name = ?, manual_multiplier = ?, monthly_min = ?,
                                  monthly_max = ?, revisit_gap_days = ?, active = ?,
                                  revision = revision + 1, valid_from_version = ?,
                                  updated_at = ?
                WHERE feeder_id = ?
                """,
                (
                    name, manual_multiplier, monthly_min, monthly_max,
                    revisit_gap_days, int(active), version, _now(), feeder_id,
                ),
            )
    result = get_feeder(connection, feeder_id)
    assert result is not None
    return result


def delete_feeder(connection: sqlite3.Connection, feeder_id: str) -> None:
    in_use = connection.execute(
        "SELECT COUNT(*) AS n FROM dtc WHERE canonical_feeder_id = ?", (feeder_id,)
    ).fetchone()["n"]
    if in_use:
        raise StorageError(
            C_FEEDER_IN_USE,
            (
                f"{in_use} DTC(s) are assigned to this feeder; reassign them before "
                "removing it"
            ),
            feeder_id,
        )
    with transaction(connection):
        connection.execute("DELETE FROM feeder WHERE feeder_id = ?", (feeder_id,))
        bump_version(connection)


# --- DTCs -----------------------------------------------------------------


@dataclass(frozen=True)
class DtcRow:
    dtc_id: str
    name: str
    canonical_feeder_id: str | None
    kva: float | None
    station: str | None
    dtc_class: str
    source_class: str | None
    individual_adjustment: float
    monthly_min: int
    monthly_max: int
    revisit_gap_days: int
    #: The date the asset was commissioned, or ``None`` when the master recorded no
    #: date. Read as "no restriction" rather than as "not yet commissioned".
    commissioned_on: date | None
    active: bool
    revision: int

    @property
    def is_department(self) -> bool:
        return self.dtc_class == "DEPT"


def _dtc_from(row: sqlite3.Row) -> DtcRow:
    return DtcRow(
        dtc_id=row["dtc_id"],
        name=row["name"],
        canonical_feeder_id=row["canonical_feeder_id"],
        kva=float(row["kva"]) if row["kva"] is not None else None,
        station=row["station"],
        dtc_class=row["dtc_class"],
        source_class=row["source_class"],
        individual_adjustment=float(row["individual_adjustment"]),
        monthly_min=int(row["monthly_min"]),
        monthly_max=int(row["monthly_max"]),
        revisit_gap_days=int(row["revisit_gap_days"]),
        commissioned_on=(
            date.fromisoformat(row["commissioned_on"])
            if row["commissioned_on"]
            else None
        ),
        active=bool(row["active"]),
        revision=int(row["revision"]),
    )


def list_dtcs(connection: sqlite3.Connection) -> tuple[DtcRow, ...]:
    return tuple(
        _dtc_from(row)
        for row in connection.execute("SELECT * FROM dtc ORDER BY dtc_id").fetchall()
    )


def get_dtc(connection: sqlite3.Connection, dtc_id: str) -> DtcRow | None:
    row = connection.execute("SELECT * FROM dtc WHERE dtc_id = ?", (dtc_id,)).fetchone()
    return _dtc_from(row) if row is not None else None


def upsert_dtc(
    connection: sqlite3.Connection,
    *,
    dtc_id: str,
    name: str,
    dtc_class: str,
    canonical_feeder_id: str | None = None,
    kva: float | None = None,
    station: str | None = None,
    source_class: str | None = None,
    individual_adjustment: float = 1.0,
    monthly_min: int = 0,
    monthly_max: int = 3,
    revisit_gap_days: int = 7,
    commissioned_on: date | str | None = None,
    active: bool = True,
) -> DtcRow:
    stored_commissioned = _iso_date(commissioned_on)
    with transaction(connection):
        version = bump_version(connection)
        existing = connection.execute(
            "SELECT revision FROM dtc WHERE dtc_id = ?", (dtc_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO dtc (dtc_id, name, canonical_feeder_id, kva, station,
                                 dtc_class, source_class, individual_adjustment,
                                 monthly_min, monthly_max, revisit_gap_days,
                                 commissioned_on, active, revision,
                                 valid_from_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    dtc_id, name, canonical_feeder_id, kva, station, dtc_class,
                    source_class, individual_adjustment, monthly_min, monthly_max,
                    revisit_gap_days, stored_commissioned, int(active), version,
                    _now(),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE dtc SET name = ?, canonical_feeder_id = ?, kva = ?, station = ?,
                               dtc_class = ?, source_class = ?, individual_adjustment = ?,
                               monthly_min = ?, monthly_max = ?, revisit_gap_days = ?,
                               commissioned_on = ?, active = ?, revision = revision + 1,
                               valid_from_version = ?, updated_at = ?
                WHERE dtc_id = ?
                """,
                (
                    name, canonical_feeder_id, kva, station, dtc_class, source_class,
                    individual_adjustment, monthly_min, monthly_max, revisit_gap_days,
                    stored_commissioned, int(active), version, _now(), dtc_id,
                ),
            )
    result = get_dtc(connection, dtc_id)
    assert result is not None
    return result


def delete_dtc(connection: sqlite3.Connection, dtc_id: str) -> None:
    with transaction(connection):
        connection.execute("DELETE FROM dtc WHERE dtc_id = ?", (dtc_id,))
        bump_version(connection)


# --- activities -----------------------------------------------------------


@dataclass(frozen=True)
class ActivityRow:
    activity_id: str
    category: str
    description_en: str
    description_kn: str | None
    weight: float
    monthly_min: int
    monthly_max: int
    repeat_gap_days: int
    weekday_mask: str | None
    active: bool
    revision: int


def _activity_from(row: sqlite3.Row) -> ActivityRow:
    return ActivityRow(
        activity_id=row["activity_id"],
        category=row["category"],
        description_en=row["description_en"],
        description_kn=row["description_kn"],
        weight=float(row["weight"]),
        monthly_min=int(row["monthly_min"]),
        monthly_max=int(row["monthly_max"]),
        repeat_gap_days=int(row["repeat_gap_days"]),
        weekday_mask=row["weekday_mask"],
        active=bool(row["active"]),
        revision=int(row["revision"]),
    )


def list_activities(connection: sqlite3.Connection) -> tuple[ActivityRow, ...]:
    return tuple(
        _activity_from(row)
        for row in connection.execute(
            "SELECT * FROM activity ORDER BY category, activity_id"
        ).fetchall()
    )


def get_activity(connection: sqlite3.Connection, activity_id: str) -> ActivityRow | None:
    row = connection.execute(
        "SELECT * FROM activity WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    return _activity_from(row) if row is not None else None


def upsert_activity(
    connection: sqlite3.Connection,
    *,
    activity_id: str,
    category: str,
    description_en: str,
    description_kn: str | None = None,
    weight: float = 1.0,
    monthly_min: int = 0,
    monthly_max: int = 10,
    repeat_gap_days: int = 2,
    weekday_mask: str | None = None,
    active: bool = True,
) -> ActivityRow:
    with transaction(connection):
        version = bump_version(connection)
        existing = connection.execute(
            "SELECT revision FROM activity WHERE activity_id = ?", (activity_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO activity (activity_id, category, description_en,
                                      description_kn, weight, monthly_min, monthly_max,
                                      repeat_gap_days, weekday_mask, active, revision,
                                      valid_from_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    activity_id, category, description_en, description_kn, weight,
                    monthly_min, monthly_max, repeat_gap_days, weekday_mask,
                    int(active), version, _now(),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE activity SET category = ?, description_en = ?,
                                    description_kn = ?, weight = ?, monthly_min = ?,
                                    monthly_max = ?, repeat_gap_days = ?,
                                    weekday_mask = ?, active = ?,
                                    revision = revision + 1, valid_from_version = ?,
                                    updated_at = ?
                WHERE activity_id = ?
                """,
                (
                    category, description_en, description_kn, weight, monthly_min,
                    monthly_max, repeat_gap_days, weekday_mask, int(active), version,
                    _now(), activity_id,
                ),
            )
    result = get_activity(connection, activity_id)
    assert result is not None
    return result


def delete_activity(connection: sqlite3.Connection, activity_id: str) -> None:
    with transaction(connection):
        connection.execute("DELETE FROM activity WHERE activity_id = ?", (activity_id,))
        bump_version(connection)


# --- stored source records ------------------------------------------------


@dataclass(frozen=True)
class DtcSourceRow:
    row_number: int
    dtc_id: str | None
    raw_name: str | None
    raw_feeder: str | None
    raw_feeder_code: str | None
    raw_mdm_code: str | None
    raw_kva: str | None
    raw_station: str | None
    raw_class: str | None


def list_dtc_source_records(connection: sqlite3.Connection) -> tuple[DtcSourceRow, ...]:
    return tuple(
        DtcSourceRow(
            row_number=int(row["row_number"]),
            dtc_id=row["dtc_id"],
            raw_name=row["raw_name"],
            raw_feeder=row["raw_feeder"],
            raw_feeder_code=row["raw_feeder_code"],
            raw_mdm_code=row["raw_mdm_code"],
            raw_kva=row["raw_kva"],
            raw_station=row["raw_station"],
            raw_class=row["raw_class"],
        )
        for row in connection.execute(
            "SELECT * FROM dtc_source_record ORDER BY batch_id, row_number"
        ).fetchall()
    )
