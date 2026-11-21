"""Acceptance holds an activity batch to the contract in force now.

Act 17 stopped the importer inventing an identifier, a weight, a frequency or a repeat
gap for a row that omitted them. It guarded acceptance by asking whether those values
were absent, which covers a payload that says nothing. It does not cover a payload that
says something wrong: a weight of `-2.5`, a weight of `0`, a monthly minimum of `-1` or a
repeat gap of `-3` are all present, and the write path reads them straight through
`float()` and `int()`.

Those are exactly the values the earlier contract let through, so a batch staged under it
could reach the master without ever being judged by the rules that apply now. These tests
build that situation directly: a batch staged cleanly, then rewritten to the shape an
older database holds, and then offered for acceptance.

Every row here is synthetic (`LEG-###`), because this is a test of the tool, not a
catalogue.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application.readiness import assess_readiness
from dailyworkreg.infrastructure.imports.datasets import (
    judge_dataset,
    read_activity_fields,
)
from dailyworkreg.infrastructure.sqlite import StorageError, masters, open_database
from dailyworkreg.infrastructure.sqlite.imports import (
    C_ACTIVITY_CONTRACT_UNSATISFIED,
    accept_batch,
    get_batch,
    list_batches,
    reject_batch,
    stage_bytes,
    staging_rows,
)
from master_fixtures import seed_office_masters

HEADER = (
    "Activity ID,Category,Description,Kannada,Weight,Monthly Min,Monthly Max,"
    "Repeat Gap,Weekdays\n"
)


def valid_row(activity_id: str = "LEG-001", description: str = "Legacy rehearsal activity") -> str:
    return f'{activity_id},Rehearsal,"{description}",ಪರಿಶೀಲನೆ,1,1,4,2,""\n'


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "legacy.db")
    seed_office_masters(connection)
    yield connection
    connection.close()


def stage_valid(connection: sqlite3.Connection, rows: str | None = None):
    batch, verdict = stage_bytes(
        connection, "activity", "legacy.csv", (HEADER + (rows or valid_row())).encode()
    )
    assert verdict.error_count == 0, [outcome.messages for outcome in verdict.outcomes]
    return batch


def rewrite_payload(connection: sqlite3.Connection, batch_id: str, **changes) -> None:
    """Make a staged payload look like one an older contract wrote.

    The batch was staged by the current judge, so its payload is valid by construction.
    Overwriting the stored JSON is how a database that staged the same file earlier
    holds it: same batch, same rows, payload written under the older rules.
    """

    rows = connection.execute(
        "SELECT row_number, normalized_json FROM import_staging_row "
        "WHERE batch_id = ? ORDER BY row_number",
        (batch_id,),
    ).fetchall()
    targets = changes.pop("__all__", None)
    wanted = {int(number) for number in targets} if targets else {int(rows[0]["row_number"])}
    for row in rows:
        if int(row["row_number"]) not in wanted:
            continue
        payload = json.loads(row["normalized_json"])
        for key, value in changes.items():
            if value is _ABSENT:
                payload.pop(key, None)
            else:
                payload[key] = value
        connection.execute(
            "UPDATE import_staging_row SET normalized_json = ? WHERE batch_id = ? AND row_number = ?",
            (json.dumps(payload, ensure_ascii=False), batch_id, row["row_number"]),
        )
    connection.commit()


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<absent>"


_ABSENT = _Absent()


def master_snapshot(connection: sqlite3.Connection) -> tuple:
    return (tuple(masters.list_activities(connection)), masters.current_version(connection))


def refused(connection: sqlite3.Connection, batch_id: str) -> StorageError:
    with pytest.raises(StorageError) as refusal:
        accept_batch(connection, batch_id)
    return refusal.value


# --- the shared contract ---------------------------------------------------


def test_one_reading_of_a_valid_activity_payload():
    fields, problems = read_activity_fields(
        {
            "activity_id": "LEG-100",
            "category": "Rehearsal",
            "description_en": "Rehearsal activity",
            "description_kn": None,
            "weight": 1.0,
            "monthly_min": 1,
            "monthly_max": 4,
            "repeat_gap_days": 2,
            "weekday_mask": None,
        }
    )

    assert problems == ()
    assert (fields.weight, fields.monthly_min, fields.monthly_max) == (1.0, 1, 4)


@pytest.mark.parametrize(
    ("changes", "expected"),
    (
        ({"activity_id": None}, "missing activity id"),
        ({"category": None}, "missing category"),
        ({"description_en": None}, "missing English description"),
        ({"activity_id": "", "category": "  "}, "missing activity id, category"),
        ({"weight": None}, "no weight was supplied"),
        ({"monthly_min": None}, "no monthly minimum was supplied"),
        ({"monthly_max": None}, "no monthly maximum was supplied"),
        ({"repeat_gap_days": None}, "no repeat gap was supplied"),
        ({"weight": 0}, "is not greater than zero"),
        ({"weight": -2.5}, "is not greater than zero"),
        ({"weight": float("nan")}, "is not a finite number"),
        ({"weight": float("inf")}, "is not a finite number"),
        ({"monthly_min": -1}, "monthly minimum -1 is negative"),
        ({"monthly_max": -2}, "monthly maximum -2 is negative"),
        ({"repeat_gap_days": -3}, "repeat gap -3 is negative"),
        ({"monthly_min": 5, "monthly_max": 2}, "monthly minimum 5 is above maximum 2"),
        ({"weight": "heavy"}, "weight 'heavy' is not a number"),
        ({"weight": "nan"}, "is not a finite number"),
        ({"monthly_min": 2.5}, "is not a whole number"),
        ({"monthly_min": "#N/A"}, "is a placeholder"),
        ({"weight": True}, "is not a number"),
    ),
)
def test_the_contract_names_what_is_wrong(changes, expected):
    payload = {
        "activity_id": "LEG-101",
        "category": "Rehearsal",
        "description_en": "Rehearsal activity",
        "description_kn": "ಪರಿಶೀಲನೆ",
        "weight": 1,
        "monthly_min": 0,
        "monthly_max": 0,
        "repeat_gap_days": 0,
        "weekday_mask": None,
    } | changes

    _fields, problems = read_activity_fields(payload)

    assert problems
    assert expected in " ".join(problems)


def test_zero_minimum_and_zero_maximum_together_are_valid():
    """A row that may never occur is a decision, and the contract keeps it."""

    _fields, problems = read_activity_fields(
        {
            "activity_id": "LEG-102",
            "category": "Rehearsal",
            "description_en": "Rehearsal activity",
            "weight": 1,
            "monthly_min": 0,
            "monthly_max": 0,
            "repeat_gap_days": 0,
        }
    )

    assert problems == ()


def test_a_missing_kannada_description_is_not_a_contract_violation():
    _fields, problems = read_activity_fields(
        {
            "activity_id": "LEG-103",
            "category": "Rehearsal",
            "description_en": "English only",
            "description_kn": None,
            "weight": 1,
            "monthly_min": 1,
            "monthly_max": 4,
            "repeat_gap_days": 2,
        }
    )

    assert problems == ()


# --- the judge and acceptance apply the same rules -------------------------


def _as_stored(column: str, value: str) -> dict[str, object]:
    """The same supplied value in the shape a staged payload carries it."""

    if column == "Weight":
        return {"weight": None if value == "" else float(value)}
    key = {
        "Monthly Min": "monthly_min",
        "Monthly Max": "monthly_max",
        "Repeat Gap": "repeat_gap_days",
    }[column]
    return {key: None if value == "" else int(value)}


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    (
        ("Weight", "0", "is not greater than zero"),
        ("Weight", "-2.5", "is not greater than zero"),
        ("Monthly Min", "-1", "monthly minimum -1 is negative"),
        ("Monthly Max", "-2", "monthly maximum -2 is negative"),
        ("Repeat Gap", "-3", "repeat gap -3 is negative"),
        ("Monthly Min", "9", "monthly minimum 9 is above maximum 4"),
        ("Weight", "", "no weight was supplied"),
    ),
)
def test_a_source_row_and_a_stored_payload_fail_the_same_way(column, value, expected):
    """Same field, same complaint, whichever door the value arrives through."""

    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(HEADER.strip().split(","))
    supplied = ["LEG-200", "Rehearsal", "Same rules", "ಪರಿಶೀಲನೆ", "1", "1", "4", "2", ""]
    supplied[HEADER.strip().split(",").index(column)] = value
    writer.writerow(supplied)
    headers, rows = csv.DictReader(io.StringIO(stream.getvalue())).fieldnames, list(
        csv.DictReader(io.StringIO(stream.getvalue()))
    )
    judged = judge_dataset("activity", tuple(headers), tuple(rows))

    stored_hint = _as_stored(column, value)
    _fields, problems = read_activity_fields(
        {
            "activity_id": "LEG-200",
            "category": "Rehearsal",
            "description_en": "Same rules",
            "weight": 1,
            "monthly_min": 1,
            "monthly_max": 4,
            "repeat_gap_days": 2,
        }
        | stored_hint
    )

    assert judged.error_count == 1
    assert expected in " ".join(judged.outcomes[0].messages)
    assert expected in " ".join(problems)


# --- legacy batches offered for acceptance ---------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    (
        ({"activity_id": _ABSENT}, "missing activity id"),
        ({"activity_id": None}, "missing activity id"),
        ({"weight": _ABSENT}, "no weight was supplied"),
        ({"weight": None}, "no weight was supplied"),
        ({"weight": 0}, "is not greater than zero"),
        ({"weight": 0.0}, "weight 0 is not greater than zero"),
        ({"weight": -2.5}, "weight -2.5 is not greater than zero"),
        ({"weight": float("nan")}, "weight nan is not a finite number"),
        ({"weight": float("inf")}, "weight inf is not a finite number"),
        ({"monthly_min": -1}, "monthly minimum -1 is negative"),
        ({"monthly_max": -2}, "monthly maximum -2 is negative"),
        ({"repeat_gap_days": -3}, "repeat gap -3 is negative"),
        ({"monthly_min": 5, "monthly_max": 2}, "monthly minimum 5 is above maximum 2"),
        ({"monthly_min": _ABSENT}, "no monthly minimum was supplied"),
        ({"monthly_max": None}, "no monthly maximum was supplied"),
        ({"repeat_gap_days": _ABSENT}, "no repeat gap was supplied"),
        ({"weight": None, "monthly_min": None, "monthly_max": None, "repeat_gap_days": None},
         "no weight, monthly minimum, monthly maximum or repeat gap was supplied"),
    ),
)
def test_a_legacy_payload_that_breaks_the_contract_is_refused(db, changes, expected):
    batch = stage_valid(db)
    rewrite_payload(db, batch.batch_id, **changes)
    before = master_snapshot(db)

    refusal = refused(db, batch.batch_id)

    assert refusal.constraint == C_ACTIVITY_CONTRACT_UNSATISFIED
    assert expected in str(refusal)
    assert "Row 1:" in str(refusal)

    after = get_batch(db, batch.batch_id)
    assert after.status == "staged"
    assert after.accepted_at is None
    assert after.decision_note is None
    assert master_snapshot(db) == before
    assert masters.list_activities(db) == ()


def test_a_legacy_payload_with_an_identifier_the_master_already_holds_changes_nothing(db):
    """The refusal happens before the write, so not even an update reaches the master."""

    good = stage_valid(db)
    accept_batch(db, good.batch_id)
    before = master_snapshot(db)

    legacy = stage_valid(db, valid_row("LEG-001", "Rewritten wording"))
    rewrite_payload(db, legacy.batch_id, weight=-2.5)

    refusal = refused(db, legacy.batch_id)

    assert "weight -2.5 is not greater than zero" in str(refusal)
    assert master_snapshot(db) == before
    assert masters.get_activity(db, "LEG-001").description_en == "Legacy rehearsal activity"


def test_one_bad_row_refuses_the_whole_batch(db):
    rows = valid_row("LEG-301") + valid_row("LEG-302") + valid_row("LEG-303")
    batch = stage_valid(db, rows)
    rewrite_payload(db, batch.batch_id, __all__=[2], repeat_gap_days=-3)
    before = master_snapshot(db)

    refusal = refused(db, batch.batch_id)

    assert "Row 2:" in str(refusal)
    assert "repeat gap -3 is negative" in str(refusal)
    assert master_snapshot(db) == before
    assert masters.list_activities(db) == ()
    assert get_batch(db, batch.batch_id).status == "staged"


def test_every_bad_row_is_named_rather_than_only_the_first(db):
    rows = valid_row("LEG-311") + valid_row("LEG-312") + valid_row("LEG-313")
    batch = stage_valid(db, rows)
    rewrite_payload(db, batch.batch_id, __all__=[1], weight=0)
    rewrite_payload(db, batch.batch_id, __all__=[3], monthly_min=-1)

    refusal = refused(db, batch.batch_id)

    assert "2 rows in this batch do not meet" in str(refusal)
    assert "Row 1:" in str(refusal)
    assert "Row 3:" in str(refusal)


def test_the_refusal_reads_as_instructions_for_the_operator(db):
    batch = stage_valid(db)
    rewrite_payload(db, batch.batch_id, weight=-2.5)

    message = str(refused(db, batch.batch_id))

    assert "Nothing was imported and the batch is unchanged" in message
    assert "rejected and uploaded again from a corrected file" in message
    assert "HTTP" not in message
    assert "ValueError" not in message


# --- a valid legacy batch stays acceptable ---------------------------------


def test_a_complete_and_valid_legacy_payload_is_still_accepted(db):
    """Age is not a defect: a payload that meets the contract today is imported."""

    batch = stage_valid(db)
    # An older contract wrote the weight as an integer and omitted the weekday column.
    rewrite_payload(db, batch.batch_id, weight=2, weekday_mask=_ABSENT)

    result = accept_batch(db, batch.batch_id)

    assert (result.inserted, result.updated) == (1, 0)
    activity = masters.get_activity(db, "LEG-001")
    assert activity.weight == 2.0
    assert activity.weekday_mask is None
    assert get_batch(db, batch.batch_id).status == "accepted"


def test_a_legacy_payload_without_kannada_is_accepted_as_a_warning(db):
    batch = stage_valid(db, valid_row().replace("ಪರಿಶೀಲನೆ", ""))
    assert get_batch(db, batch.batch_id).warning_count == 1

    accept_batch(db, batch.batch_id)

    activity = masters.get_activity(db, "LEG-001")
    assert activity.description_kn is None
    assert activity.description_en == "Legacy rehearsal activity"


# --- the other import behaviours are untouched -----------------------------


def test_rejecting_a_legacy_batch_still_works(db):
    batch = stage_valid(db)
    rewrite_payload(db, batch.batch_id, weight=-2.5)

    reject_batch(db, batch.batch_id, note="superseded by a corrected file")

    assert get_batch(db, batch.batch_id).status == "rejected"
    assert masters.list_activities(db) == ()


def test_re_uploading_the_corrected_file_still_updates_in_place(db):
    first = stage_valid(db)
    accept_batch(db, first.batch_id)

    second, verdict = stage_bytes(
        db,
        "activity",
        "legacy.csv",
        (HEADER + valid_row(description="Corrected wording")).encode(),
    )
    assert verdict.error_count == 0
    result = accept_batch(db, second.batch_id)

    assert (result.inserted, result.updated) == (0, 1)
    assert len(masters.list_activities(db)) == 1
    assert masters.get_activity(db, "LEG-001").description_en == "Corrected wording"


def test_the_staged_payload_is_not_rewritten_by_a_refusal(db):
    batch = stage_valid(db)
    rewrite_payload(db, batch.batch_id, weight=0)

    refused(db, batch.batch_id)

    stored = staging_rows(db, batch.batch_id)[0]
    assert stored.payload["weight"] == 0, "the refusal must not repair the record"


def test_the_refusal_does_not_disturb_readiness(db):
    batch = stage_valid(db, valid_row("LEG-401") + valid_row("LEG-402"))
    good = stage_valid(db, valid_row("LEG-403"))
    accept_batch(db, good.batch_id)
    rewrite_payload(db, batch.batch_id, weight=-2.5)

    refused(db, batch.batch_id)

    report = assess_readiness(db, date(2026, 1, 1), date(2026, 1, 31))
    item = next(entry for entry in report.items if entry.key == "activities")
    assert item.detail == "1 activity record(s) are stored"


def test_the_batch_list_shows_the_legacy_batch_as_still_waiting(db):
    batch = stage_valid(db)
    rewrite_payload(db, batch.batch_id, weight=-2.5)

    refused(db, batch.batch_id)

    waiting = [row for row in list_batches(db) if row.batch_id == batch.batch_id]
    assert [row.status for row in waiting] == ["staged"]
