"""The activity contract reads a whole number exactly, including above 2**53.

Act 18 held a legacy activity payload to the contract in force, but read whole numbers
through `float()` first. A float cannot hold every integer: above 2**53 two different
integers become the same float, so a monthly minimum of 9007199254740993 and a maximum of
9007199254740992 compared as equal, the inversion was never seen, and the acceptance guard
let the pair through to the master. A number beyond SQLite's signed 64-bit range was not
refused by the contract either; it failed later, at the INSERT, as a driver `OverflowError`.
A weight too large to be a float raised `OverflowError` out of the contract itself.

These tests pin the exact reading on the shared function, on the legacy acceptance path,
at the storage boundary, through text, and for the weight, so that the staging judge and
the acceptance guard keep answering the same question the same way.

Every value here is synthetic and every database is a throwaway file.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from dailyworkreg.infrastructure.imports.datasets import (
    SQLITE_INTEGER_MAX,
    SQLITE_INTEGER_MIN,
    judge_dataset,
    read_activity_fields,
)
from dailyworkreg.infrastructure.sqlite import StorageError, masters, open_database
from dailyworkreg.infrastructure.sqlite.imports import (
    C_ACTIVITY_CONTRACT_UNSATISFIED,
    accept_batch,
    get_batch,
    stage_bytes,
)
from master_fixtures import seed_office_masters

HEADER = (
    "Activity ID,Category,Description,Kannada,Weight,Monthly Min,Monthly Max,"
    "Repeat Gap,Weekdays\n"
)

ABOVE_THE_FLOAT_BOUNDARY = 2**53
UNREPRESENTABLE = ABOVE_THE_FLOAT_BOUNDARY + 1  # 9007199254740993


def payload(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "activity_id": "PRE-001",
        "category": "Rehearsal",
        "description_en": "Rehearsal activity",
        "description_kn": None,
        "weight": 1,
        "monthly_min": 1,
        "monthly_max": 4,
        "repeat_gap_days": 2,
        "weekday_mask": None,
    }
    return base | changes


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "precision.db")
    seed_office_masters(connection)
    yield connection
    connection.close()


def stage_one(connection: sqlite3.Connection, activity_id: str = "PRE-001"):
    batch, verdict = stage_bytes(
        connection,
        "activity",
        "precision.csv",
        (HEADER + f'{activity_id},Rehearsal,"Rehearsal activity",,1,1,4,2,""\n').encode(),
    )
    assert verdict.error_count == 0, [outcome.messages for outcome in verdict.outcomes]
    return batch


def rewrite(connection: sqlite3.Connection, batch_id: str, **changes: object) -> str:
    """Overwrite row 1's stored payload with the shape an older database holds."""

    row = connection.execute(
        "SELECT row_number, normalized_json FROM import_staging_row "
        "WHERE batch_id = ? ORDER BY row_number",
        (batch_id,),
    ).fetchone()
    stored = json.loads(row["normalized_json"])
    stored.update(changes)
    text = json.dumps(stored, ensure_ascii=False)
    connection.execute(
        "UPDATE import_staging_row SET normalized_json = ? WHERE batch_id = ? AND row_number = ?",
        (text, batch_id, row["row_number"]),
    )
    connection.commit()
    return text


def stored_text(connection: sqlite3.Connection, batch_id: str) -> str:
    return connection.execute(
        "SELECT normalized_json FROM import_staging_row WHERE batch_id = ? ORDER BY row_number",
        (batch_id,),
    ).fetchone()["normalized_json"]


# --- A. the comparison itself, at the boundary where a float stops being exact ---


def test_a_minimum_one_above_its_maximum_is_refused_even_though_a_float():
    """float(2**53 + 1) == float(2**53), so this pair used to look like equality."""

    assert float(UNREPRESENTABLE) == float(ABOVE_THE_FLOAT_BOUNDARY)

    _fields, problems = read_activity_fields(
        payload(monthly_min=UNREPRESENTABLE, monthly_max=ABOVE_THE_FLOAT_BOUNDARY)
    )

    assert problems == (
        "monthly minimum 9007199254740993 is above maximum 9007199254740992",
    )


def test_the_reverse_order_above_the_float_boundary_is_accepted_and_kept_exact():
    fields, problems = read_activity_fields(
        payload(monthly_min=ABOVE_THE_FLOAT_BOUNDARY, monthly_max=UNREPRESENTABLE)
    )

    assert problems == ()
    assert fields.monthly_min == ABOVE_THE_FLOAT_BOUNDARY
    assert fields.monthly_max == UNREPRESENTABLE
    assert str(fields.monthly_max) == "9007199254740993"


def test_an_equal_pair_above_the_float_boundary_is_accepted_and_not_rounded():
    """The value itself survives, not merely the comparison against its neighbour."""

    fields, problems = read_activity_fields(
        payload(monthly_min=UNREPRESENTABLE, monthly_max=UNREPRESENTABLE)
    )

    assert problems == ()
    assert fields.monthly_min == UNREPRESENTABLE
    assert fields.monthly_max == UNREPRESENTABLE
    assert int(float(UNREPRESENTABLE)) != UNREPRESENTABLE


# --- C. the storage boundary, which is the database's and not a policy choice ---


def test_the_largest_number_this_register_stores_is_accepted():
    fields, problems = read_activity_fields(payload(monthly_max=SQLITE_INTEGER_MAX))

    assert problems == ()
    assert fields.monthly_max == SQLITE_INTEGER_MAX


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("monthly_max", SQLITE_INTEGER_MAX + 1),
        ("monthly_min", SQLITE_INTEGER_MIN - 1),
        ("repeat_gap_days", SQLITE_INTEGER_MAX + 1),
    ),
)
def test_a_number_beyond_what_this_register_stores_is_refused_and_named(field, value):
    _fields, problems = read_activity_fields(payload(**{field: value}))

    assert problems
    assert "is outside the range this register stores whole numbers in" in problems[0]
    assert str(value) in problems[0]
    assert str(SQLITE_INTEGER_MAX) in problems[0]


def test_no_scheduling_limit_is_invented_beyond_the_storage_boundary():
    """A large but storable frequency is the office's decision, not this tool's."""

    fields, problems = read_activity_fields(payload(repeat_gap_days=10_000_000))

    assert problems == ()
    assert fields.repeat_gap_days == 10_000_000


# --- D. what a float in an integer field means ---


@pytest.mark.parametrize(
    ("value", "accepted", "expected"),
    (
        (2.0, True, 2),
        (0.0, True, 0),
        (2.5, False, "is not a whole number"),
        (float("nan"), False, "is not a whole number"),
        (float("inf"), False, "is not a whole number"),
        (float("-inf"), False, "is not a whole number"),
        (True, False, "is not a number"),
        (False, False, "is not a number"),
    ),
)
def test_a_float_in_a_whole_field_is_accepted_only_when_it_is_already_whole(
    value, accepted, expected
):
    fields, problems = read_activity_fields(payload(monthly_min=value))

    if accepted:
        assert problems == ()
        assert fields.monthly_min == expected
        return
    assert problems
    assert expected in " ".join(problems)


@pytest.mark.parametrize(
    ("written", "accepted", "expected"),
    (
        ("3", True, 3),
        (" 3 ", True, 3),
        ("+3", True, 3),
        ("3.0", True, 3),
        ("2.5", False, "is not a whole number"),
        ("heavy", False, "is not a number"),
    ),
)
def test_text_in_a_whole_field_is_read_exactly_rather_than_through_a_float(
    written, accepted, expected
):
    field_name = "monthly_min"
    supplied = {"repeat_gap_days": 2, field_name: written}
    fields, problems = read_activity_fields(payload(**supplied))

    if accepted:
        assert problems == ()
        assert getattr(fields, field_name) == expected
        return
    assert problems
    assert expected in " ".join(problems)


def test_a_long_catalogue_cell_is_read_digit_by_digit_and_not_rounded():
    """`int(float(text))` would turn this into 9007199254740992 before judging it."""

    fields, problems = read_activity_fields(payload(repeat_gap_days="9007199254740993"))

    assert problems == ()
    assert fields.repeat_gap_days == 9007199254740993
    assert int(float("9007199254740993")) != 9007199254740993


# --- E. the weight, which is a float, and must fail rather than raise ---


@pytest.mark.parametrize(
    ("value", "accepted", "expected"),
    (
        (2, True, 2.0),
        (1.5, True, 1.5),
        (0.25, True, 0.25),
        (float("nan"), False, "is not a finite number"),
        (float("inf"), False, "is not a finite number"),
        (10**400, False, "is too large to be stored as a number"),
    ),
)
def test_a_weight_is_read_without_an_overflow_escaping_the_contract(
    value, accepted, expected
):
    fields, problems = read_activity_fields(payload(weight=value))

    if accepted:
        assert problems == ()
        assert fields.weight == pytest.approx(expected)
        return
    assert problems
    assert expected in " ".join(problems)


def test_a_weight_too_large_to_be_a_float_does_not_raise():
    """`float(10**400)` raises, and a raise is not a contract answer."""

    with pytest.raises(OverflowError):
        float(10**400)

    _fields, problems = read_activity_fields(payload(weight=10**400))

    assert problems == ("weight 10000000000000000000... is too large to be stored as a number",)


# --- B. the legacy acceptance path, which is the one that could write ---


def test_a_legacy_pair_above_the_float_boundary_is_refused_before_any_write(db):
    batch = stage_one(db)
    before_master = (tuple(masters.list_activities(db)), masters.current_version(db))
    untouched = rewrite(db, batch.batch_id, monthly_min=UNREPRESENTABLE, monthly_max=ABOVE_THE_FLOAT_BOUNDARY)

    with pytest.raises(StorageError) as refusal:
        accept_batch(db, batch.batch_id)

    assert refusal.value.constraint == C_ACTIVITY_CONTRACT_UNSATISFIED
    assert "monthly minimum 9007199254740993 is above maximum 9007199254740992" in (
        refusal.value.detail
    )
    assert get_batch(db, batch.batch_id).status == "staged"
    row = db.execute(
        "SELECT accepted_at, decision_note FROM import_batch WHERE batch_id = ?",
        (batch.batch_id,),
    ).fetchone()
    assert row["accepted_at"] is None
    assert row["decision_note"] is None
    assert (tuple(masters.list_activities(db)), masters.current_version(db)) == before_master
    assert masters.list_activities(db) == ()
    assert stored_text(db, batch.batch_id) == untouched


def test_the_largest_storable_legacy_pair_is_accepted_and_stored_exactly(db):
    batch = stage_one(db)
    rewrite(db, batch.batch_id, monthly_min=SQLITE_INTEGER_MAX - 1, monthly_max=SQLITE_INTEGER_MAX)

    accept_batch(db, batch.batch_id)

    stored = masters.get_activity(db, "PRE-001")
    assert (stored.monthly_min, stored.monthly_max) == (
        SQLITE_INTEGER_MAX - 1,
        SQLITE_INTEGER_MAX,
    )


def test_a_legacy_number_beyond_the_storage_range_writes_nothing(db):
    batch = stage_one(db)
    before_master = (tuple(masters.list_activities(db)), masters.current_version(db))
    rewrite(db, batch.batch_id, monthly_max=SQLITE_INTEGER_MAX + 1)

    with pytest.raises(StorageError) as refusal:
        accept_batch(db, batch.batch_id)

    assert refusal.value.constraint == C_ACTIVITY_CONTRACT_UNSATISFIED
    assert "is outside the range this register stores whole numbers in" in refusal.value.detail
    assert get_batch(db, batch.batch_id).status == "staged"
    assert (tuple(masters.list_activities(db)), masters.current_version(db)) == before_master
    assert masters.list_activities(db) == ()


def test_a_legacy_weight_that_cannot_be_a_float_writes_nothing(db):
    batch = stage_one(db)
    before_master = (tuple(masters.list_activities(db)), masters.current_version(db))
    rewrite(db, batch.batch_id, weight=10**400)

    with pytest.raises(StorageError) as refusal:
        accept_batch(db, batch.batch_id)

    assert refusal.value.constraint == C_ACTIVITY_CONTRACT_UNSATISFIED
    assert "is too large to be stored as a number" in refusal.value.detail
    assert get_batch(db, batch.batch_id).status == "staged"
    assert (tuple(masters.list_activities(db)), masters.current_version(db)) == before_master


def test_a_legacy_whole_field_written_as_text_is_stored_as_the_number_it_states(db):
    """The payload's text is not re-converted with `int()`, which would raise here."""

    batch = stage_one(db)
    rewrite(db, batch.batch_id, monthly_min="3.0", weight="1.5")

    accept_batch(db, batch.batch_id)

    stored = masters.get_activity(db, "PRE-001")
    assert stored.monthly_min == 3
    assert stored.weight == pytest.approx(1.5)


def test_a_batch_with_one_imprecise_row_writes_none_of_its_rows(db):
    rows = (
        'PRE-001,Rehearsal,"First",,1,1,4,2,""\n'
        'PRE-002,Rehearsal,"Second",,1,1,4,2,""\n'
        'PRE-003,Rehearsal,"Third",,1,1,4,2,""\n'
    )
    batch, verdict = stage_bytes(db, "activity", "precision.csv", (HEADER + rows).encode())
    assert verdict.error_count == 0
    db.execute(
        "UPDATE import_staging_row SET normalized_json = ? WHERE batch_id = ? AND row_number = 3",
        (
            json.dumps(
                payload(activity_id="PRE-003", monthly_min=UNREPRESENTABLE, monthly_max=ABOVE_THE_FLOAT_BOUNDARY),
                ensure_ascii=False,
            ),
            batch.batch_id,
        ),
    )
    db.commit()

    with pytest.raises(StorageError) as refusal:
        accept_batch(db, batch.batch_id)

    assert "Row 3" in refusal.value.detail
    assert masters.list_activities(db) == ()
    assert db.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 0


# --- F. the two doors still agree, and the contract's other rules are untouched ---


def test_the_judge_and_the_stored_payload_refuse_the_same_precise_pair():
    """A catalogue cell and a stored integer are read by one function, so one answer."""

    headers = HEADER.strip().split(",")
    row = dict(
        zip(
            headers,
            [
                "PRE-010",
                "Rehearsal",
                "Rehearsal activity",
                "",
                "1",
                str(UNREPRESENTABLE),
                str(ABOVE_THE_FLOAT_BOUNDARY),
                "2",
                "",
            ],
            strict=True,
        )
    )
    judged = judge_dataset("activity", headers, [row])

    _fields, problems = read_activity_fields(
        payload(
            activity_id="PRE-010",
            monthly_min=UNREPRESENTABLE,
            monthly_max=ABOVE_THE_FLOAT_BOUNDARY,
        )
    )

    expected = "monthly minimum 9007199254740993 is above maximum 9007199254740992"
    assert judged.error_count == 1
    assert expected in " ".join(judged.outcomes[0].messages)
    assert expected in " ".join(problems)


def test_a_catalogue_cell_above_the_float_boundary_is_no_longer_rounded_when_staged():
    headers = HEADER.strip().split(",")
    row = dict(
        zip(
            headers,
            ["PRE-011", "Rehearsal", "Rehearsal activity", "", "1", "2", str(UNREPRESENTABLE), "2", ""],
            strict=True,
        )
    )

    judged = judge_dataset("activity", headers, [row])

    assert judged.error_count == 0, [outcome.messages for outcome in judged.outcomes]
    assert judged.outcomes[0].payload["monthly_max"] == UNREPRESENTABLE


# --- Act 20: text in decimal and exponent form, read as an exact decimal ---------

#: Decimal and exponent forms of one integer, each of which `float` turns into
#: 9007199254740992 before anything can ask whether it is whole.
EXACT_FORMS = ("9007199254740993", "9007199254740993.0", "9007199254740993e0",
               "9.007199254740993e15")


@pytest.mark.parametrize("written", EXACT_FORMS)
def test_every_textual_form_of_a_long_integer_returns_that_integer(written):
    fields, problems = read_activity_fields(payload(repeat_gap_days=written))

    assert problems == ()
    assert fields.repeat_gap_days == UNREPRESENTABLE


@pytest.mark.parametrize("written", EXACT_FORMS[1:])
def test_the_forms_float_alone_would_round_are_the_ones_being_tested(written):
    """So the test above cannot pass because a float happened to be exact anyway."""

    assert float(written) != UNREPRESENTABLE


def test_a_decimal_text_pair_above_the_float_boundary_is_compared_exactly():
    fields, problems = read_activity_fields(
        payload(monthly_min="9007199254740993.0", monthly_max="9007199254740992.0")
    )

    assert problems == (
        "monthly minimum 9007199254740993 is above maximum 9007199254740992",
    )
    assert fields.monthly_min == UNREPRESENTABLE
    assert fields.monthly_max == ABOVE_THE_FLOAT_BOUNDARY


def test_an_equal_decimal_text_pair_above_the_float_boundary_is_accepted_exactly():
    fields, problems = read_activity_fields(
        payload(monthly_min="9007199254740993.0", monthly_max="9007199254740993.0")
    )

    assert problems == ()
    assert fields.monthly_min == UNREPRESENTABLE
    assert fields.monthly_max == UNREPRESENTABLE


@pytest.mark.parametrize(
    "written",
    (
        "2.0000000000000000000000000000000000000000001",
        "1.9999999999999999999999999999999999999999999",
        "9007199254740993.0000000001",
    ),
)
def test_a_fraction_that_a_float_would_hide_is_still_refused(written):
    """Ordinary float conversion says each of these is a whole number."""

    assert float(written).is_integer()

    fields, problems = read_activity_fields(payload(repeat_gap_days=written))

    assert fields.repeat_gap_days is None
    assert problems == (f"repeat gap '{written}' is not a whole number",)


def test_a_trailing_zero_run_is_still_the_integer_it_states():
    fields, problems = read_activity_fields(
        payload(repeat_gap_days="2.0000000000000000000000000000000000000000000")
    )

    assert problems == ()
    assert fields.repeat_gap_days == 2


@pytest.mark.parametrize(
    ("written", "expected"),
    (
        ("2.0", 2),
        ("2.000", 2),
        ("2e0", 2),
        ("+3.0", 3),
        ("-0.0", 0),
        ("0.000", 0),
        ("1E+3", 1000),
    ),
)
def test_legitimate_decimal_forms_keep_their_meaning(written, expected):
    fields, problems = read_activity_fields(payload(repeat_gap_days=written))

    assert problems == ()
    assert fields.repeat_gap_days == expected


def test_negative_zero_is_zero_and_not_a_negative_minimum():
    """Mathematically zero is not negative, so the field rule must not fire."""

    fields, problems = read_activity_fields(
        payload(monthly_min="-0.0", monthly_max="-0.000")
    )

    assert problems == ()
    assert fields.monthly_min == 0
    assert fields.monthly_max == 0
    assert fields.monthly_min < 1  # a plain int zero, never -0


@pytest.mark.parametrize(
    "written",
    (
        "",
        "   ",
        "1..2",
        "1e",
        "1e+",
        "e5",
        "0x10",
        "--1",
        "1 2",
        "two",
        "NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        "inf",
    ),
)
def test_no_decimal_exception_escapes_and_every_bad_form_is_named(written):
    fields, problems = read_activity_fields(payload(repeat_gap_days=written))

    if not written.strip():
        # blank and whitespace are "not supplied", which the caller words as such
        assert fields.repeat_gap_days is None
        assert problems
        assert "no repeat gap was supplied" in " ".join(problems)
        return
    assert fields.repeat_gap_days is None
    assert problems
    assert problems[0].startswith("repeat gap")
    assert "is not a whole number" in problems[0] or "is not a number" in problems[0]


@pytest.mark.parametrize(
    ("field", "written", "expected"),
    (
        ("repeat_gap_days", str(SQLITE_INTEGER_MAX), "accepted"),
        ("monthly_min", str(SQLITE_INTEGER_MIN), "is negative"),
        ("monthly_min", str(SQLITE_INTEGER_MAX + 1), "outside the range"),
        ("monthly_min", str(SQLITE_INTEGER_MIN - 1), "outside the range"),
        ("repeat_gap_days", "1e1000000000", "outside the range"),
        ("repeat_gap_days", "1e-1000000000", "is not a whole number"),
    ),
)
def test_the_storage_boundary_is_applied_to_decimal_text_before_conversion(
    field, written, expected
):
    """`2**63` is refused before `int()` would be asked to build a huge number."""

    fields, problems = read_activity_fields(payload(**{field: written}))

    if expected == "accepted":
        assert problems == ()
        assert fields.repeat_gap_days == SQLITE_INTEGER_MAX
        return
    assert problems
    assert expected in " ".join(problems)


def test_a_large_exponent_is_refused_without_building_a_huge_integer():
    import time

    started = time.monotonic()
    _fields, problems = read_activity_fields(payload(repeat_gap_days="1e1000000000"))
    elapsed = time.monotonic() - started

    assert problems
    assert "outside the range" in problems[0]
    assert elapsed < 1.0


# --- Act 20: the legacy path and the two doors, for decimal text ---------------


def test_a_legacy_decimal_text_pair_is_refused_with_both_exact_values_named(db):
    batch = stage_one(db)
    before = (tuple(masters.list_activities(db)), masters.current_version(db))
    untouched = rewrite(
        db, batch.batch_id, monthly_min="9007199254740993.0", monthly_max="9007199254740992.0"
    )

    with pytest.raises(StorageError) as refusal:
        accept_batch(db, batch.batch_id)

    assert refusal.value.constraint == C_ACTIVITY_CONTRACT_UNSATISFIED
    assert "monthly minimum 9007199254740993 is above maximum 9007199254740992" in (
        refusal.value.detail
    )
    assert get_batch(db, batch.batch_id).status == "staged"
    row = db.execute(
        "SELECT accepted_at, decision_note FROM import_batch WHERE batch_id = ?",
        (batch.batch_id,),
    ).fetchone()
    assert row["accepted_at"] is None
    assert row["decision_note"] is None
    assert (tuple(masters.list_activities(db)), masters.current_version(db)) == before
    assert masters.list_activities(db) == ()
    assert stored_text(db, batch.batch_id) == untouched


def test_a_valid_legacy_decimal_text_payload_is_stored_as_its_exact_value(db):
    batch = stage_one(db)
    rewrite(
        db,
        batch.batch_id,
        monthly_min="9007199254740993.0",
        monthly_max="9007199254740994.0",
        repeat_gap_days="2.0",
    )

    accept_batch(db, batch.batch_id)

    stored = masters.get_activity(db, "PRE-001")
    assert stored.monthly_min == UNREPRESENTABLE
    assert stored.monthly_max == UNREPRESENTABLE + 1
    assert stored.repeat_gap_days == 2


def test_a_catalogue_cell_in_decimal_form_and_the_same_stored_text_agree():
    headers = HEADER.strip().split(",")
    row = dict(
        zip(
            headers,
            ["PRE-020", "Rehearsal", "Rehearsal activity", "", "1.0",
             "9007199254740993.0", "9007199254740992.0", "2.0", ""],
            strict=True,
        )
    )

    judged = judge_dataset("activity", headers, [row])
    _fields, problems = read_activity_fields(
        payload(
            activity_id="PRE-020",
            monthly_min="9007199254740993.0",
            monthly_max="9007199254740992.0",
        )
    )

    expected = "monthly minimum 9007199254740993 is above maximum 9007199254740992"
    assert judged.error_count == 1
    assert expected in " ".join(judged.outcomes[0].messages)
    assert expected in " ".join(problems)


def test_a_catalogue_cell_in_decimal_form_is_staged_at_its_exact_value():
    headers = HEADER.strip().split(",")
    row = dict(
        zip(
            headers,
            ["PRE-021", "Rehearsal", "Rehearsal activity", "", "1",
             "0", "9007199254740993.0", "2", ""],
            strict=True,
        )
    )

    judged = judge_dataset("activity", headers, [row])

    assert judged.error_count == 0, [outcome.messages for outcome in judged.outcomes]
    assert judged.outcomes[0].payload["monthly_max"] == UNREPRESENTABLE
    assert judged.outcomes[0].payload["weight"] == 1.0
