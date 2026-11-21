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
