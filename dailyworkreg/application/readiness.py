"""Whether the office's masters are good enough to generate a register.

Readiness is measured from the database, not assumed. An application that always
reported the same three missing masters would be lying in both directions: it would
claim a holiday baseline was absent after one had been imported, and it would stay
silent about an empty activity catalogue that nobody had noticed.

A missing master does not stop field-visit allocation, and it does not make the result
wrong. It stops the result from being an operational diary, and it is reported as
exactly that.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ..infrastructure.sqlite import calendar_records as cal
from ..infrastructure.sqlite import masters

#: The years the requirements baseline requires holiday coverage for (§4.1).
BASELINE_HOLIDAY_YEARS = (2025, 2026)


@dataclass(frozen=True)
class ReadinessItem:
    """One thing the application needs, and whether it has it."""

    key: str
    label: str
    satisfied: bool
    detail: str
    #: True when the item only affects whether the result is a complete operational
    #: diary, rather than whether a draft can be generated at all.
    blocks_draft: bool = False

    @property
    def blocks_generation(self) -> bool:
        return self.blocks_draft and not self.satisfied


@dataclass(frozen=True)
class Readiness:
    items: tuple[ReadinessItem, ...]
    years: tuple[int, ...]

    @property
    def blocking(self) -> tuple[ReadinessItem, ...]:
        return tuple(item for item in self.items if item.blocks_generation)

    @property
    def can_generate(self) -> bool:
        return not self.blocking

    @property
    def missing_content(self) -> tuple[str, ...]:
        """The masters that are absent, named from what the database actually holds."""

        return tuple(item.label for item in self.items if not item.satisfied)

    @property
    def is_operational_diary(self) -> bool:
        return all(item.satisfied for item in self.items)


def years_in(start: date, end: date) -> tuple[int, ...]:
    return tuple(range(start.year, end.year + 1))


def assess_readiness(
    connection: sqlite3.Connection, start: date, end: date
) -> Readiness:
    """What is present, what is absent, and what that means for a draft."""

    dtcs = [dtc for dtc in masters.list_dtcs(connection) if dtc.active]
    areas = [area for area in masters.list_areas(connection) if area.active]
    feeders = [feeder for feeder in masters.list_feeders(connection) if feeder.active]
    activities = [a for a in masters.list_activities(connection) if a.active]
    leave_types = [t for t in cal.list_leave_types(connection) if t.active]

    years = years_in(start, end)
    items: list[ReadinessItem] = [
        ReadinessItem(
            key="roster.dtc",
            label="DTC master",
            satisfied=bool(dtcs),
            detail=(
                f"{len(dtcs)} active DTC(s) are stored"
                if dtcs
                else (
                    "no DTC is stored, so there is nothing to inspect; import the "
                    "113-SCD source file"
                )
            ),
            blocks_draft=True,
        ),
        ReadinessItem(
            key="roster.area",
            label="Area master",
            satisfied=bool(areas),
            detail=f"{len(areas)} area(s) are stored" if areas else "no area is stored",
        ),
        ReadinessItem(
            key="roster.feeder",
            label="Feeder master",
            satisfied=bool(feeders),
            detail=(
                f"{len(feeders)} feeder(s) are stored"
                if feeders
                else "no feeder is stored"
            ),
        ),
        ReadinessItem(
            key="activities",
            label="activity catalogue",
            satisfied=bool(activities),
            detail=(
                f"{len(activities)} activity record(s) are stored"
                if activities
                else (
                    "no activity is stored, so regular activity entries cannot be "
                    "generated; the approved catalogue has not been imported"
                )
            ),
        ),
        ReadinessItem(
            key="leave_types",
            label="leave types",
            satisfied=bool(leave_types),
            detail=(
                f"{len(leave_types)} leave type(s) are stored"
                if leave_types
                else "no leave type is stored"
            ),
        ),
    ]

    for year in years:
        stored = cal.list_holidays(connection, year=year)
        items.append(
            ReadinessItem(
                key=f"holidays.{year}",
                label=f"official Karnataka General Holiday baseline for {year}",
                satisfied=bool(stored),
                detail=(
                    f"{len(stored)} General Holiday record(s) are stored for {year}"
                    if stored
                    else (
                        f"no General Holiday is stored for {year}, so the calendar "
                        "shows only weekly offs and leave"
                    )
                ),
            )
        )

    return Readiness(items=tuple(items), years=years)
