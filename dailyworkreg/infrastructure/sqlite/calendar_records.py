"""Repositories for calendar records: General Holidays, leave, events, overrides.

There is no Restricted Holiday repository and no table behind one. Requirements
§4.1 makes a Restricted Holiday an ordinary working day whose dates and names are
ignored entirely, so storing one would create exactly the reason to display it that
the requirement forbids.

Overrides are stored beside the facts they modify rather than instead of them. A
holiday that an override turns into a working day stays in ``general_holiday``, and
a leave record that coincides with one stays in ``leave_record``: the office can see
both the underlying reason and the effective classification, and the conflict is
available to be warned about rather than silently resolved.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime

from .database import transaction
from .masters import bump_version


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- General Holidays -----------------------------------------------------


@dataclass(frozen=True)
class HolidayRow:
    holiday_id: str
    holiday_date: date
    name: str
    description: str | None
    holiday_year: int
    source: str
    revision: int


def _holiday_from(row: sqlite3.Row) -> HolidayRow:
    return HolidayRow(
        holiday_id=row["holiday_id"],
        holiday_date=date.fromisoformat(row["holiday_date"]),
        name=row["name"],
        description=row["description"],
        holiday_year=int(row["holiday_year"]),
        source=row["source"],
        revision=int(row["revision"]),
    )


def list_holidays(
    connection: sqlite3.Connection, year: int | None = None
) -> tuple[HolidayRow, ...]:
    if year is None:
        rows = connection.execute(
            "SELECT * FROM general_holiday ORDER BY holiday_date, name"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM general_holiday WHERE holiday_year = ? ORDER BY holiday_date, name",
            (year,),
        ).fetchall()
    return tuple(_holiday_from(row) for row in rows)


def holidays_on(connection: sqlite3.Connection, day: date) -> tuple[HolidayRow, ...]:
    return tuple(
        _holiday_from(row)
        for row in connection.execute(
            "SELECT * FROM general_holiday WHERE holiday_date = ? ORDER BY name",
            (day.isoformat(),),
        ).fetchall()
    )


def upsert_holiday(
    connection: sqlite3.Connection,
    *,
    holiday_id: str,
    holiday_date: date,
    name: str,
    description: str | None = None,
    source: str = "operator",
) -> HolidayRow:
    with transaction(connection):
        version = bump_version(connection)
        existing = connection.execute(
            "SELECT revision FROM general_holiday WHERE holiday_id = ?", (holiday_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO general_holiday (holiday_id, holiday_date, name,
                                             description, holiday_year, source,
                                             revision, valid_from_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    holiday_id, holiday_date.isoformat(), name, description,
                    holiday_date.year, source, version, _now(),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE general_holiday SET holiday_date = ?, name = ?, description = ?,
                                           holiday_year = ?, source = ?,
                                           revision = revision + 1,
                                           valid_from_version = ?, updated_at = ?
                WHERE holiday_id = ?
                """,
                (
                    holiday_date.isoformat(), name, description, holiday_date.year,
                    source, version, _now(), holiday_id,
                ),
            )
    row = connection.execute(
        "SELECT * FROM general_holiday WHERE holiday_id = ?", (holiday_id,)
    ).fetchone()
    return _holiday_from(row)


def delete_holiday(connection: sqlite3.Connection, holiday_id: str) -> None:
    with transaction(connection):
        connection.execute(
            "DELETE FROM general_holiday WHERE holiday_id = ?", (holiday_id,)
        )
        bump_version(connection)


def replace_holiday_year(
    connection: sqlite3.Connection, year: int, *, source: str, merge: bool
) -> int:
    """Delete one year's holidays, so a supplied list can replace the stored one.

    Requirements §4.1 makes the user's list replace the stored list by default, with
    an explicit merge alternative. Deletion is scoped to one year and to records
    from the ``karnataka`` source only, so an operator's own entry for that year is
    not swept away by importing the state list. Returns the number deleted.
    """

    with transaction(connection):
        if merge:
            return 0
        deleted = connection.execute(
            "DELETE FROM general_holiday WHERE holiday_year = ? AND source = ?",
            (year, source),
        ).rowcount
        if deleted:
            bump_version(connection)
    return deleted or 0


# --- leave ----------------------------------------------------------------


@dataclass(frozen=True)
class LeaveTypeRow:
    leave_type_id: str
    name: str
    active: bool


@dataclass(frozen=True)
class LeaveRecordRow:
    leave_id: str
    leave_date: date
    leave_type_id: str
    leave_type_name: str
    note: str | None
    revision: int


def list_leave_types(connection: sqlite3.Connection) -> tuple[LeaveTypeRow, ...]:
    return tuple(
        LeaveTypeRow(
            leave_type_id=row["leave_type_id"], name=row["name"], active=bool(row["active"])
        )
        for row in connection.execute(
            "SELECT * FROM leave_type ORDER BY name"
        ).fetchall()
    )


def upsert_leave_type(
    connection: sqlite3.Connection, *, leave_type_id: str, name: str, active: bool = True
) -> None:
    with transaction(connection):
        version = bump_version(connection)
        existing = connection.execute(
            "SELECT revision FROM leave_type WHERE leave_type_id = ?", (leave_type_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO leave_type (leave_type_id, name, active, revision,
                                        valid_from_version, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (leave_type_id, name, int(active), version, _now()),
            )
        else:
            connection.execute(
                """
                UPDATE leave_type SET name = ?, active = ?, revision = revision + 1,
                                      valid_from_version = ?, updated_at = ?
                WHERE leave_type_id = ?
                """,
                (name, int(active), version, _now(), leave_type_id),
            )


def list_leave_records(connection: sqlite3.Connection) -> tuple[LeaveRecordRow, ...]:
    return tuple(
        LeaveRecordRow(
            leave_id=row["leave_id"],
            leave_date=date.fromisoformat(row["leave_date"]),
            leave_type_id=row["leave_type_id"],
            leave_type_name=row["leave_type_name"],
            note=row["note"],
            revision=int(row["revision"]),
        )
        for row in connection.execute(
            """
            SELECT r.*, t.name AS leave_type_name
            FROM leave_record r JOIN leave_type t ON t.leave_type_id = r.leave_type_id
            ORDER BY r.leave_date
            """
        ).fetchall()
    )


def upsert_leave_record(
    connection: sqlite3.Connection,
    *,
    leave_id: str,
    leave_date: date,
    leave_type_id: str,
    note: str | None = None,
) -> None:
    with transaction(connection):
        version = bump_version(connection)
        existing = connection.execute(
            "SELECT revision FROM leave_record WHERE leave_id = ?", (leave_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO leave_record (leave_id, leave_date, leave_type_id, note,
                                          revision, valid_from_version, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (leave_id, leave_date.isoformat(), leave_type_id, note, version, _now()),
            )
        else:
            connection.execute(
                """
                UPDATE leave_record SET leave_date = ?, leave_type_id = ?, note = ?,
                                        revision = revision + 1,
                                        valid_from_version = ?, updated_at = ?
                WHERE leave_id = ?
                """,
                (
                    leave_date.isoformat(), leave_type_id, note, version, _now(),
                    leave_id,
                ),
            )


def delete_leave_record(connection: sqlite3.Connection, leave_id: str) -> None:
    with transaction(connection):
        connection.execute("DELETE FROM leave_record WHERE leave_id = ?", (leave_id,))
        bump_version(connection)


# --- dated events ---------------------------------------------------------


@dataclass(frozen=True)
class EventRow:
    event_id: str
    event_date: date
    title_en: str
    title_kn: str | None
    event_kind: str
    note: str | None
    revision: int


def list_events(connection: sqlite3.Connection) -> tuple[EventRow, ...]:
    return tuple(
        EventRow(
            event_id=row["event_id"],
            event_date=date.fromisoformat(row["event_date"]),
            title_en=row["title_en"],
            title_kn=row["title_kn"],
            event_kind=row["event_kind"],
            note=row["note"],
            revision=int(row["revision"]),
        )
        for row in connection.execute(
            "SELECT * FROM dated_event ORDER BY event_date, event_id"
        ).fetchall()
    )


def upsert_event(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    event_date: date,
    title_en: str,
    title_kn: str | None = None,
    event_kind: str = "event",
    note: str | None = None,
) -> None:
    with transaction(connection):
        version = bump_version(connection)
        existing = connection.execute(
            "SELECT revision FROM dated_event WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO dated_event (event_id, event_date, title_en, title_kn,
                                         event_kind, note, revision,
                                         valid_from_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    event_id, event_date.isoformat(), title_en, title_kn, event_kind,
                    note, version, _now(),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE dated_event SET event_date = ?, title_en = ?, title_kn = ?,
                                       event_kind = ?, note = ?,
                                       revision = revision + 1, valid_from_version = ?,
                                       updated_at = ?
                WHERE event_id = ?
                """,
                (
                    event_date.isoformat(), title_en, title_kn, event_kind, note,
                    version, _now(), event_id,
                ),
            )


def delete_event(connection: sqlite3.Connection, event_id: str) -> None:
    with transaction(connection):
        connection.execute("DELETE FROM dated_event WHERE event_id = ?", (event_id,))
        bump_version(connection)


# --- explicit date overrides ---------------------------------------------


@dataclass(frozen=True)
class OverrideRow:
    override_id: str
    override_date: date
    is_working_day: bool
    reason_en: str | None
    reason_kn: str | None
    note: str | None
    revision: int


def list_overrides(connection: sqlite3.Connection) -> tuple[OverrideRow, ...]:
    return tuple(
        OverrideRow(
            override_id=row["override_id"],
            override_date=date.fromisoformat(row["override_date"]),
            is_working_day=bool(row["is_working_day"]),
            reason_en=row["reason_en"],
            reason_kn=row["reason_kn"],
            note=row["note"],
            revision=int(row["revision"]),
        )
        for row in connection.execute(
            "SELECT * FROM date_override ORDER BY override_date"
        ).fetchall()
    )


def upsert_override(
    connection: sqlite3.Connection,
    *,
    override_id: str,
    override_date: date,
    is_working_day: bool,
    reason_en: str | None = None,
    reason_kn: str | None = None,
    note: str | None = None,
) -> None:
    with transaction(connection):
        version = bump_version(connection)
        connection.execute(
            """
            INSERT INTO date_override (override_id, override_date, is_working_day,
                                       reason_en, reason_kn, note, revision,
                                       valid_from_version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT (override_date) DO UPDATE SET
                override_id = excluded.override_id,
                is_working_day = excluded.is_working_day,
                reason_en = excluded.reason_en,
                reason_kn = excluded.reason_kn,
                note = excluded.note,
                revision = date_override.revision + 1,
                valid_from_version = excluded.valid_from_version,
                updated_at = excluded.updated_at
            """,
            (
                override_id, override_date.isoformat(), int(is_working_day),
                reason_en, reason_kn, note, version, _now(),
            ),
        )


def delete_override(connection: sqlite3.Connection, override_id: str) -> None:
    with transaction(connection):
        connection.execute(
            "DELETE FROM date_override WHERE override_id = ?", (override_id,)
        )
        bump_version(connection)
