"""The approved identities that ship with a fresh database.

These lists are transcribed from the requirements baseline, which names them
explicitly: the 16 canonical areas in §7 and the four feeder identities in §9, plus
the two initial leave types in §5.1. Transcribing an approved list is not
fabrication; inventing one would be.

What is deliberately **not** seeded, because no approved list exists:

* the activity catalogue (§6 requires 40-60 owner-approved activities that have
  not been supplied), and
* the General Holiday baseline for 2025 and 2026 (§4.1 requires the official
  Karnataka data).

Seeding is idempotent and never overwrites an operator's edit: every insert is
skipped when the identity already exists. A record the office has renamed stays
renamed.
"""

from __future__ import annotations

import sqlite3

from .database import transaction
from .masters import bump_version

#: Recorded in ``app_setting`` so a later start does not re-offer the seed. The
#: value is the provenance a reader sees, not a silent insertion.
SEED_SETTING_KEY = "seed.approved_identities"

#: (area_id, Kannada name, aliases) from requirements §7. Numbers follow the
#: baseline's order so a reader can check one against the other.
APPROVED_AREAS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
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

#: (feeder_id, name) from requirements §9.
APPROVED_FEEDERS: tuple[tuple[str, str], ...] = (
    ("F-01", "MRS F-1 OLDTOWN"),
    ("F-02", "Mandli F-13 MANDLI BYEPASS"),
    ("F-03", "Mandli F-22 GANDHIBAZAAR"),
    ("F-04", "Mandli F-3 NT ROAD"),
)

#: (leave_type_id, name) from requirements §5.1. Additional types are configurable
#: and are not seeded, because the baseline names only these two.
APPROVED_LEAVE_TYPES: tuple[tuple[str, str], ...] = (
    ("CL", "Casual Leave"),
    ("FPL", "Full Pay Leave"),
)

#: Owner-confirmed canonical feeder assignments from requirements §8. Applied when
#: the DTC exists, so an import that supplies the records can then be reconciled
#: against the approved overrides rather than the reverse.
APPROVED_FEEDER_OVERRIDES: dict[str, str] = {
    "SCD-5": "MRS F-1 OLDTOWN",
    "SCD-12": "Mandli F-22 GANDHIBAZAAR",
    "SCD-107": "MRS F-1 OLDTOWN",
    "SCD-108": "MRS F-1 OLDTOWN",
    "SCD-109": "MRS F-1 OLDTOWN",
    "SCD-110": "MRS F-1 OLDTOWN",
}

SEED_PROVENANCE = "requirements baseline §7, §9 and §5.1"


def seed_approved_identities(connection: sqlite3.Connection, *, force: bool = False) -> dict[str, int]:
    """Insert the approved areas, feeders and leave types if they are absent.

    Returns a count per category so a caller can report what happened rather than
    guess. An existing record with the same identity is left exactly as it is,
    because the office may have edited it and a seed is not an authority over an
    operator.
    """

    row = connection.execute(
        "SELECT value FROM app_setting WHERE key = ?", (SEED_SETTING_KEY,)
    ).fetchone()
    if row is not None and not force:
        return {"areas": 0, "feeders": 0, "leave_types": 0}

    counts = {"areas": 0, "feeders": 0, "leave_types": 0}
    with transaction(connection):
        version = bump_version(connection)
        now = SEED_PROVENANCE
        for area_id, name_kn, aliases in APPROVED_AREAS:
            exists = connection.execute(
                "SELECT 1 FROM area WHERE area_id = ?", (area_id,)
            ).fetchone()
            if exists is not None:
                continue
            connection.execute(
                """
                INSERT INTO area (area_id, name_kn, name_en, weight, monthly_min,
                                  monthly_max, revisit_gap_days, active, revision,
                                  valid_from_version, updated_at)
                VALUES (?, ?, NULL, 1.0, 0, 3, 7, 1, 1, ?, ?)
                """,
                (area_id, name_kn, version, now),
            )
            for alias in aliases:
                connection.execute(
                    "INSERT OR IGNORE INTO area_alias (area_id, name_kn) VALUES (?, ?)",
                    (area_id, alias),
                )
            counts["areas"] += 1

        for feeder_id, name in APPROVED_FEEDERS:
            exists = connection.execute(
                "SELECT 1 FROM feeder WHERE feeder_id = ?", (feeder_id,)
            ).fetchone()
            if exists is not None:
                continue
            connection.execute(
                """
                INSERT INTO feeder (feeder_id, name, manual_multiplier, monthly_min,
                                    monthly_max, revisit_gap_days, active, revision,
                                    valid_from_version, updated_at)
                VALUES (?, ?, 1.0, 0, 3, 7, 1, 1, ?, ?)
                """,
                (feeder_id, name, version, now),
            )
            counts["feeders"] += 1

        for leave_type_id, name in APPROVED_LEAVE_TYPES:
            exists = connection.execute(
                "SELECT 1 FROM leave_type WHERE leave_type_id = ?", (leave_type_id,)
            ).fetchone()
            if exists is not None:
                continue
            connection.execute(
                """
                INSERT INTO leave_type (leave_type_id, name, active, revision,
                                        valid_from_version, updated_at)
                VALUES (?, ?, 1, 1, ?, ?)
                """,
                (leave_type_id, name, version, now),
            )
            counts["leave_types"] += 1

        connection.execute(
            """
            INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value,
                                            updated_at = excluded.updated_at
            """,
            (SEED_SETTING_KEY, SEED_PROVENANCE, now),
        )
    return counts


def apply_approved_feeder_overrides(connection: sqlite3.Connection) -> int:
    """Set the owner-confirmed canonical feeders on the DTCs that exist.

    Only the canonical column is written. The imported source feeder stays in
    ``dtc_source_record`` untouched, so the override and the evidence for it remain
    two separate facts. Returns how many assignments changed.
    """

    changed = 0
    with transaction(connection):
        version = bump_version(connection)
        for dtc_id, feeder_name in APPROVED_FEEDER_OVERRIDES.items():
            feeder = connection.execute(
                "SELECT feeder_id FROM feeder WHERE name = ?", (feeder_name,)
            ).fetchone()
            if feeder is None:
                continue
            updated = connection.execute(
                """
                UPDATE dtc SET canonical_feeder_id = ?, revision = revision + 1,
                               valid_from_version = ?, updated_at = ?
                WHERE dtc_id = ? AND (canonical_feeder_id IS NULL
                                      OR canonical_feeder_id <> ?)
                """,
                (feeder["feeder_id"], version, SEED_PROVENANCE, dtc_id,
                 feeder["feeder_id"]),
            )
            changed += updated.rowcount or 0
    return changed
