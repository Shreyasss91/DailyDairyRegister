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

#: (leave_type_id, name) from requirements §5.1. Additional types are configurable
#: and are not seeded, because the baseline names only these two.
APPROVED_LEAVE_TYPES: tuple[tuple[str, str], ...] = (
    ("CL", "Casual Leave"),
    ("FPL", "Full Pay Leave"),
)

#: Owner-confirmed canonical feeder assignments from requirements §8. Applied when
#: the DTC exists, so an import that supplies the records can then be reconciled
#: against the approved overrides rather than the reverse.
#:
#: SCD-107 and SCD-108 were confirmed to MRS F-1 OLDTOWN, and on 2026-11-19 the owner
#: corrected that to Mandli F-22 GANDHIBAZAAR - the feeder the roster itself names -
#: with the instruction that the imported file is what the office goes by. They are
#: therefore recorded as the file states them. All six now agree with the supplied
#: text, so an ordinary roster import reassigns nothing; the table is kept because a
#: later file that contradicts it must be reported before it is accepted, not
#: discovered afterwards.
APPROVED_FEEDER_OVERRIDES: dict[str, str] = {
    "SCD-5": "MRS F-1 OLDTOWN",
    "SCD-12": "Mandli F-22 GANDHIBAZAAR",
    "SCD-107": "Mandli F-22 GANDHIBAZAAR",
    "SCD-108": "Mandli F-22 GANDHIBAZAAR",
    "SCD-109": "MRS F-1 OLDTOWN",
    "SCD-110": "MRS F-1 OLDTOWN",
}

SEED_PROVENANCE = (
    "requirements baseline §5.1: the leave types the baseline names. No office's areas or "
    "feeders are seeded; those arrive with that office's own master files."
)


def seed_leave_types(connection: sqlite3.Connection, *, force: bool = False) -> dict[str, int]:
    """Insert the leave types the baseline names, if they are absent.

    An office's areas and feeders are **not** seeded. They are that office's own records,
    they arrive from its own `04_area_master.csv` and `05_feeder_master.csv`, and a database
    that invented sixteen areas and four feeders for whoever opened it would be handing one
    office's names to the next one - which is exactly what happened before this was removed.
    The leave types stay because they are policy rather than a place: the baseline names CL
    and FPL and says additional types are configurable.

    Returns a count per category so a caller can report what happened rather than guess. An
    existing record with the same identity is left exactly as it is, because the office may
    have edited it and a seed is not an authority over an operator.
    """

    row = connection.execute(
        "SELECT value FROM app_setting WHERE key = ?", (SEED_SETTING_KEY,)
    ).fetchone()
    if row is not None and not force:
        return {"leave_types": 0}

    counts = {"leave_types": 0}
    with transaction(connection):
        version = bump_version(connection)
        now = SEED_PROVENANCE
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
