"""Master data the tests need, which the application no longer invents.

The application seeds only the leave types: an office's areas and feeders are its own
records and arrive from its own `04_area_master.csv` and `05_feeder_master.csv`. The tests
still need a handful of masters to point a fixture at, so they are here instead — clearly
fictional to production, and identical to the ones the old seed wrote so the fixtures that
name `A-01` or `F-01` keep meaning what they meant.

Nothing in this module ships. It is imported by the test modules only.
"""

from __future__ import annotations

import sqlite3

from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import transaction
from dailyworkreg.infrastructure.sqlite.seed import seed_leave_types

#: (area_id, Kannada name, aliases), the fixture's own areas.
AREAS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("A-01", "ಗಾಂಧಿಬಜಾರ್", ("ಗಾಂಧಿ ಬಜಾರ್",)),
    ("A-02", "ಲಷ್ಕರ್ ಮೊಹಲ್ಲಾ", ()),
    ("A-03", "ಸಾವರ್ಕರ್ ನಗರ", ()),
    ("A-04", "ಬಿ.ಎಚ್. ರಸ್ತೆ", ()),
    ("A-05", "ತಿರುಪಳಯ್ಯನ ಕೇರಿ", ("ತಿರುಪಲ್ಯ ಕೇರಿ",)),
    ("A-06", "ಸೊಪ್ಪಿನ ಮಾರ್ಕೆಟ್", ()),
    ("A-07", "ಕೆ. ಆರ್. ಪುರಂ", ("KR ಪುರಂ",)),
    ("A-08", "ಭರಮಪ್ಪ ನಗರ", ("ಭರ್ಮಪ್ಪ ನಗರ",)),
    ("A-09", "ಎಂ.ಕೆ.ಕೆ. ಕೆ ರಸ್ತೆ", ("MKK ರಸ್ತೆ",)),
    ("A-10", "ನಾಗಪ್ಪ ಕೇರಿ", ()),
    ("A-11", "OT ರಸ್ತೆ", ()),
    ("A-12", "ಎಲ್ಲೆರೆವನ ಕೇರಿ", ()),
    ("A-13", "ಕುಂಬಾರ ಗುಂಡಿ", ()),
    ("A-14", "ಸಿದ್ದಯ್ಯ ರಸ್ತೆ ತಿಮ್ಮಪ್ಪನಕೊಪ್ಪಲು", ()),
    ("A-15", "ಉಪ್ಪಾರ ಕೇರಿ", ()),
    ("A-16", "ಕಸ್ತೂರ್ಬಾ ರಸ್ತೆ", ()),
)

#: (feeder_id, name), the fixture's own feeders.
FEEDERS: tuple[tuple[str, str], ...] = (
    ("F-01", "MRS F-1 OLDTOWN"),
    ("F-02", "Mandli F-13 MANDLI BYEPASS"),
    ("F-03", "Mandli F-22 GANDHIBAZAAR"),
    ("F-04", "Mandli F-3 NT ROAD"),
)


def seed_office_masters(connection: sqlite3.Connection) -> dict[str, int]:
    """Bring a database up the way a first start does, plus the fixture's own places.

    The leave types come from the application's own seed, because a real first start writes
    them; the areas and feeders are this fixture's, because the application no longer seeds
    any office's places. Skipping anything that already exists keeps it idempotent, which is
    what the seed it replaced was.
    """

    seed_leave_types(connection)
    counts = {"areas": 0, "feeders": 0}
    with transaction(connection):
        for area_id, name_kn, aliases in AREAS:
            if masters.get_area(connection, area_id) is not None:
                continue
            masters.upsert_area(connection, area_id=area_id, name_kn=name_kn)
            for alias in aliases:
                connection.execute(
                    "INSERT OR IGNORE INTO area_alias (area_id, name_kn) VALUES (?, ?)",
                    (area_id, alias),
                )
            counts["areas"] += 1
        for feeder_id, name in FEEDERS:
            if masters.get_feeder(connection, feeder_id) is not None:
                continue
            masters.upsert_feeder(connection, feeder_id=feeder_id, name=name)
            counts["feeders"] += 1
    return counts
