"""Turning stored calendar facts into per-date classifications.

This is the one place a date's classification is decided, and it decides nothing the
office has not recorded: a weekly off comes from configured rules, a holiday from the
General Holiday master, leave from leave records and an effective override from the
override table. No Karnataka holiday is assumed, and nothing is inferred from a name.

The reason strings are kept because the register shows them, §4.1 requires both
reasons when a General Holiday falls on a weekly off, and because a date that was
turned into a working day by an override must still say what it would have been.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..domain.validator import Calendar
from ..infrastructure.sqlite import calendar_records as cal
from ..infrastructure.sqlite import masters
from .draft_generation import CalendarDay, CalendarDayKind, DayEntry, DayEntryKind

#: §4.2's default wording. Visible so the operator can see what will be printed, and
#: overridable per date by an entry of the same kind.
SAFETY_MEETING_WORDING = "Conducted safety meeting for employees"

SETTING_SUNDAYS = "calendar.sundays_off"
SETTING_SECOND_SATURDAY = "calendar.second_saturday_off"
SETTING_FOURTH_SATURDAY = "calendar.fourth_saturday_off"

MONDAY = 0


@dataclass(frozen=True)
class WeeklyOffRules:
    """Configurable weekly-off rules, per requirements §4."""

    sundays: bool = True
    second_saturday: bool = True
    fourth_saturday: bool = True

    def weekly_off(self, day: date) -> str | None:
        """The reason this date is a weekly off, or None."""

        if self.sundays and day.weekday() == 6:
            return "Sunday"
        if day.weekday() == 5:
            if self.second_saturday and 8 <= day.day <= 14:
                return "Second Saturday"
            if self.fourth_saturday and 22 <= day.day <= 28:
                return "Fourth Saturday"
        return None


def load_weekly_off_rules(connection: sqlite3.Connection) -> WeeklyOffRules:
    def flag(key: str) -> bool:
        value = masters.get_setting(connection, key)
        if value is None:
            return True
        return value.strip().lower() not in {"0", "false", "no", "off"}

    return WeeklyOffRules(
        sundays=flag(SETTING_SUNDAYS),
        second_saturday=flag(SETTING_SECOND_SATURDAY),
        fourth_saturday=flag(SETTING_FOURTH_SATURDAY),
    )


def save_weekly_off_rules(
    connection: sqlite3.Connection, rules: WeeklyOffRules
) -> None:
    masters.set_setting(connection, SETTING_SUNDAYS, "1" if rules.sundays else "0")
    masters.set_setting(
        connection, SETTING_SECOND_SATURDAY, "1" if rules.second_saturday else "0"
    )
    masters.set_setting(
        connection, SETTING_FOURTH_SATURDAY, "1" if rules.fourth_saturday else "0"
    )


def _dates(start: date, end: date) -> list[date]:
    span = (end - start).days + 1
    return [start + timedelta(days=offset) for offset in range(span)]


@dataclass(frozen=True)
class CalendarProjection:
    """Per-date classifications plus the conflicts the office must resolve."""

    days: tuple[CalendarDay, ...]
    entries: tuple[DayEntry, ...] = field(default=())
    conflicts: tuple[str, ...] = field(default=())

    @property
    def working_days(self) -> frozenset[date]:
        return frozenset(day.day for day in self.days if day.is_working_day)

    def calendar(self) -> Calendar:
        return Calendar(self.working_days)


def holiday_wording(name: str, description: str | None) -> str:
    """Register wording for a General Holiday, per §4.1.

    A supplied description wins. Otherwise the three named national days use the
    ``Celebrated [festival] in office`` pattern, a Jayanthi uses
    ``Celebrated [Person] Jayanthi in office`` and everything else is
    ``Holiday - [name]``.
    """

    if description:
        return description
    lowered = name.lower()
    for festival in ("republic day", "independence day", "gandhi jayanti"):
        if festival in lowered:
            return f"Celebrated {name} in office"
    if "jayanthi" in lowered or "jayanti" in lowered:
        return f"Celebrated {name} in office"
    return f"Holiday - {name}"


def project_calendar(
    connection: sqlite3.Connection,
    start: date,
    end: date,
    *,
    rules: WeeklyOffRules | None = None,
) -> CalendarProjection:
    """Classify every date in the range from stored facts alone."""

    rules = rules or load_weekly_off_rules(connection)
    holidays = {h.holiday_date: h for h in cal.list_holidays(connection)}
    leave_by_day: dict[date, list[cal.LeaveRecordRow]] = {}
    for record in cal.list_leave_records(connection):
        leave_by_day.setdefault(record.leave_date, []).append(record)
    overrides = {row.override_date: row for row in cal.list_overrides(connection)}

    days: list[CalendarDay] = []
    conflicts: list[str] = []

    for day in _dates(start, end):
        weekly_reason = rules.weekly_off(day)
        holiday = holidays.get(day)
        leaves = leave_by_day.get(day, [])
        override = overrides.get(day)

        reasons_en: list[str] = []
        reasons_kn: list[str] = []

        if leaves:
            # Leave supersedes every other fact about the date (owner decision 2026-11-21). The
            # office reads a leave date as one thing - the person was not there - so the register
            # says only that: the holiday, the weekly off or the working day it falls on is not
            # repeated beside it, and its overlap with them is not reported as a conflict,
            # because nothing is in conflict. The stored holiday and the standing weekly off are
            # untouched in their own records; this decides what the date reads as.
            for record in leaves:
                # The stored type name already reads as a statement ("Casual Leave"), so
                # appending a second "leave" would print "Casual Leave leave".
                reasons_en.append(record.leave_type_name)
                reasons_kn.append("ರಜೆ")
        else:
            if holiday is not None:
                reasons_en.append(holiday_wording(holiday.name, holiday.description))
            if weekly_reason is not None:
                reasons_en.append(weekly_reason)
                reasons_kn.append("ವಾರದ ರಜೆ")
            # Overlap between a holiday and a weekly off is preserved, never discarded: §4.1 and
            # §5.1 require a conflict to be surfaced rather than one fact silently winning.
            if holiday is not None and weekly_reason is not None:
                conflicts.append(
                    f"{day.isoformat()}: {holiday.name} falls on a {weekly_reason}"
                )

        non_working = bool(leaves) or holiday is not None or weekly_reason is not None

        if leaves:
            # A person's approved absence is not undone by a date-level override, so an override
            # that marks the date as worked is reported rather than applied - it is a genuine
            # contradiction between two records, and the office is told which one the register
            # follows instead of one of them disappearing.
            is_working_day = False
            if override is not None and override.is_working_day:
                conflicts.append(
                    f"{day.isoformat()}: an override marks this day as a working day, "
                    "but leave is recorded on it; the leave stands"
                )
        elif override is not None:
            is_working_day = override.is_working_day
            if override.reason_en:
                reasons_en.append(override.reason_en)
            if override.reason_kn:
                reasons_kn.append(override.reason_kn)
            if is_working_day and non_working:
                conflicts.append(

                        f"{day.isoformat()}: treated as a working day by an override, "
                        "while the underlying holiday or weekly off is preserved above"

                )
        else:
            is_working_day = not non_working

        if is_working_day:
            kind = CalendarDayKind.WORKING
        elif leaves:
            kind = CalendarDayKind.LEAVE
        elif holiday is not None:
            kind = CalendarDayKind.GENERAL_HOLIDAY
        elif weekly_reason == "Sunday":
            kind = CalendarDayKind.SUNDAY
        elif weekly_reason == "Second Saturday":
            kind = CalendarDayKind.SECOND_SATURDAY
        elif weekly_reason == "Fourth Saturday":
            kind = CalendarDayKind.FOURTH_SATURDAY
        elif weekly_reason is not None:
            kind = CalendarDayKind.OTHER_WEEKLY_OFF
        else:
            kind = CalendarDayKind.OTHER_NON_WORKING

        days.append(
            CalendarDay(
                day=day,
                kind=kind,
                is_working_day=is_working_day,
                reasons_en=tuple(reasons_en),
                reasons_kn=tuple(reasons_kn),
            )
        )

    leave_days = frozenset(leave_by_day)
    events = event_entries(connection, start, end)
    for entry in events:
        if entry.day in leave_days:
            # A dated event on a leave date is the office's own record, so it is not deleted.
            # It is simply not printed on the date: the register reads a leave date as one
            # thing (owner decision 2026-11-21), and the event stays on the record with this
            # line saying where it went.
            conflicts.append(
                f"{entry.day.isoformat()}: an event is recorded on a leave date; the "
                "register states the leave and keeps the event on the record"
            )

    entries = safety_meeting_entries(
        connection, start, end, leave_days=leave_days
    ) + tuple(entry for entry in events if entry.day not in leave_days)

    return CalendarProjection(
        days=tuple(days),
        entries=tuple(sorted(entries, key=lambda entry: (entry.day, entry.entry_id))),
        conflicts=tuple(conflicts),
    )


def safety_meeting_entries(
    connection: sqlite3.Connection,
    start: date,
    end: date,
    *,
    leave_days: frozenset[date] = frozenset(),
) -> tuple[DayEntry, ...]:
    """One safety meeting on every Monday, holidays included and leave days excluded.

    §4.2 is explicit that a Monday holiday does not suppress it, and that it consumes no part
    of the regular activity quota, so a holiday keeps its meeting. A leave date does not: the
    register reads such a date as one thing, and the office asked for nothing but the leave to
    appear on it, so no meeting is projected there. An operator entry of the same kind on a
    date replaces the default rather than adding a second meeting, and an operator's own
    meeting recorded on a leave date is treated like any other event - kept on the record, and
    not printed on the date.
    """

    operator: dict[date, list[cal.EventRow]] = {}
    for event in cal.list_events(connection):
        if event.event_kind != DayEntryKind.SAFETY_MEETING.value:
            continue
        operator.setdefault(event.event_date, []).append(event)

    entries: list[DayEntry] = []
    for day in _dates(start, end):
        if day.weekday() != MONDAY:
            continue
        if day in leave_days:
            continue
        recorded = operator.get(day)
        if recorded:
            # The operator's own wording replaces the default sentence. It replaces the
            # entry, not the meeting: a Monday with two safety meetings recorded is
            # still two entries, because the office entered two.
            entries.extend(
                DayEntry(
                    entry_id=event.event_id,
                    day=day,
                    kind=DayEntryKind.SAFETY_MEETING,
                    title_en=event.title_en,
                    title_kn=event.title_kn,
                )
                for event in recorded
            )
            continue
        entries.append(
            DayEntry(
                entry_id=f"safety-{day.isoformat()}",
                day=day,
                kind=DayEntryKind.SAFETY_MEETING,
                title_en=SAFETY_MEETING_WORDING,
            )
        )
    return tuple(entries)


def event_entries(
    connection: sqlite3.Connection, start: date, end: date
) -> tuple[DayEntry, ...]:
    """Operator-entered dated events inside the range, projected unchanged."""

    entries: list[DayEntry] = []
    for event in cal.list_events(connection):
        if event.event_date < start or event.event_date > end:
            continue
        if event.event_kind == DayEntryKind.SAFETY_MEETING.value:
            # Already placed by safety_meeting_entries, which sees the whole Monday.
            continue
        entries.append(
            DayEntry(
                entry_id=event.event_id,
                day=event.event_date,
                kind=DayEntryKind(event.event_kind)
                if event.event_kind in {k.value for k in DayEntryKind}
                else DayEntryKind.EVENT,
                title_en=event.title_en,
                title_kn=event.title_kn,
            )
        )
    return tuple(entries)
