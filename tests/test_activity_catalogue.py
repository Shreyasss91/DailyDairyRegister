"""The activity catalogue contract the importer enforces.

No approved MESCOM/BESCOM catalogue exists yet, so these tests do not carry one.
Every row here is a **rehearsal row**: a synthetic record whose only job is to
exercise the importer, named `REH-###` in a category called `Rehearsal`, so that no
one can mistake it for office data or copy it into the blank import template. What
they pin is what the tool does with a catalogue-shaped file: which values it
requires, which it refuses, which it preserves verbatim, and that it invents none
of them.

The reason this matters more than usual: a missing weight or frequency used to be
completed with 1.0, 0, 10 and 2 at acceptance, so an incomplete catalogue entered
the master looking complete and approved.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application.readiness import assess_readiness
from dailyworkreg.infrastructure.imports.datasets import judge_dataset
from dailyworkreg.infrastructure.imports.tabular import read_source_file
from dailyworkreg.infrastructure.sqlite import StorageError, masters, open_database
from dailyworkreg.infrastructure.sqlite.imports import (
    C_ACTIVITY_CONTRACT_UNSATISFIED,
    accept_batch,
    get_batch,
    reject_batch,
    stage_bytes,
    staging_rows,
)
from master_fixtures import seed_office_masters

HEADER = "Activity ID,Category,Description,Kannada,Weight,Monthly Min,Monthly Max,Repeat Gap,Weekdays\n"

KANNADA_INSPECTION = "ಪರಿಶೀಲನೆ"


def row(
    activity_id: str = "REH-001",
    *,
    category: str = "Rehearsal",
    description: str = "Rehearsal activity",
    kannada: str = KANNADA_INSPECTION,
    weight: str = "1",
    minimum: str = "1",
    maximum: str = "4",
    gap: str = "2",
    weekdays: str = "",
) -> str:
    quoted = f'"{weekdays}"'
    return (
        f'{activity_id},{category},"{description}",{kannada},{weight},'
        f'{minimum},{maximum},{gap},{quoted}' + "\n"
    )


def catalogue(count: int = 46) -> bytes:
    """A catalogue-shaped file: `count` distinct, complete rehearsal rows."""

    body = "".join(
        row(
            f"REH-{index:03d}",
            category=f"Rehearsal block {1 + index // 10}",
            description=f"Rehearsal activity number {index}",
            weight=f"{1 + index % 3}",
            minimum=str(index % 2),
            maximum=str(2 + index % 4),
            gap=str(1 + index % 3),
        )
        for index in range(1, count + 1)
    )
    return (HEADER + body).encode()


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "catalogue.db")
    seed_office_masters(connection)
    yield connection
    connection.close()


def stage(connection: sqlite3.Connection, body: str, name: str = "catalogue.csv"):
    return stage_bytes(connection, "activity", name, (HEADER + body).encode())


# --- a complete catalogue -------------------------------------------------


def test_a_complete_catalogue_stages_and_accepts_cleanly(db):
    batch, verdict = stage_bytes(db, "activity", "catalogue.csv", catalogue())

    assert batch.row_count == 46
    assert verdict.error_count == 0
    assert batch.warning_count == 0

    result = accept_batch(db, batch.batch_id)
    assert (result.inserted, result.updated, result.excluded) == (46, 0, 0)

    stored = masters.list_activities(db)
    assert len(stored) == 46
    assert {activity.category for activity in stored} == {
        f"Rehearsal block {block}" for block in range(1, 6)
    }


def test_every_supplied_number_and_the_kannada_text_are_stored_as_supplied(db):
    body = row(
        "REH-900",
        description="Rehearsal inspection with a Kannada title",
        kannada="ನಿರ್ವಹಣೆ\u200cಪರಿಶೀಲನೆ",
        weight="2.5",
        minimum="2",
        maximum="6",
        gap="4",
        weekdays="Tuesday,Friday",
    )
    batch, _ = stage(db, body)
    accept_batch(db, batch.batch_id)

    activity = masters.get_activity(db, "REH-900")
    assert activity is not None
    assert activity.weight == 2.5
    assert activity.monthly_min == 2
    assert activity.monthly_max == 6
    assert activity.repeat_gap_days == 4
    # A zero-width non-joiner is part of Kannada orthography, not padding: it survives.
    assert activity.description_kn == "ನಿರ್ವಹಣೆ\u200cಪರಿಶೀಲನೆ"
    # The weekday column is stored exactly as supplied; nothing interprets it yet.
    assert activity.weekday_mask == "Tuesday,Friday"


def test_an_activity_without_a_kannada_description_is_a_warning_not_a_refusal(db):
    batch, verdict = stage(db, row("REH-901", kannada=""))

    assert verdict.error_count == 0
    assert batch.warning_count == 1
    assert "no Kannada description was supplied" in verdict.outcomes[0].messages[0]

    accept_batch(db, batch.batch_id)
    activity = masters.get_activity(db, "REH-901")
    assert activity is not None
    assert activity.description_kn is None
    assert activity.description_en == "Rehearsal activity"


# --- the numbers are never invented -------------------------------------


def test_a_blank_weight_is_refused_rather_than_filled_in(db):
    batch, verdict = stage(db, row("REH-902", weight=""))

    assert verdict.error_count == 1
    message = verdict.outcomes[0].messages[0]
    assert "no weight was supplied" in message
    assert "does not choose" in message
    with pytest.raises(StorageError):
        accept_batch(db, batch.batch_id)
    assert masters.list_activities(db) == ()


def test_blank_frequencies_and_gap_are_each_refused(db):
    for blank_field in ("minimum", "maximum", "gap"):
        values = {"minimum": "1", "maximum": "4", "gap": "2", blank_field: ""}
        batch, verdict = stage(db, row("REH-903", **values))
        assert verdict.error_count == 1, blank_field
        assert "was supplied" in verdict.outcomes[0].messages[0], blank_field
        assert masters.list_activities(db) == ()


def test_all_four_numbers_blank_are_named_together(db):
    batch, verdict = stage(db, row("REH-904", weight="", minimum="", maximum="", gap=""))

    assert verdict.error_count == 1
    message = verdict.outcomes[0].messages[0]
    assert message.startswith(
        "no weight, monthly minimum, monthly maximum or repeat gap was supplied"
    )


def test_a_weight_that_cannot_be_read_is_refused(db):
    batch, verdict = stage(db, row("REH-905", weight="about two"))

    assert verdict.error_count == 1
    assert "weight 'about two' is not a number" in verdict.outcomes[0].messages[0]
    assert masters.list_activities(db) == ()


def test_a_placeholder_in_a_number_column_is_refused(db):
    batch, verdict = stage(db, row("REH-906", minimum="#N/A"))

    assert verdict.error_count == 1
    assert "placeholder" in verdict.outcomes[0].messages[0]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    (
        ("weight", "-1", "is not greater than zero"),
        ("weight", "0", "is not greater than zero"),
        ("weight", "nan", "is not a finite number"),
        ("minimum", "-1", "monthly minimum -1 is negative"),
        ("maximum", "-2", "monthly maximum -2 is negative"),
        ("gap", "-3", "repeat gap -3 is negative"),
    ),
)
def test_impossible_numbers_are_refused(db, field, value, expected):
    batch, verdict = stage(db, row("REH-907", **{field: value}))

    assert verdict.error_count == 1
    assert expected in " ".join(verdict.outcomes[0].messages)
    assert masters.list_activities(db) == ()


def test_a_minimum_above_its_maximum_is_refused(db):
    batch, verdict = stage(db, row("REH-908", minimum="5", maximum="2"))

    assert verdict.error_count == 1
    assert "monthly minimum 5 is above maximum 2" in verdict.outcomes[0].messages[0]


def test_a_zero_maximum_with_a_zero_minimum_is_allowed(db):
    """A row that may never occur is a decision, not a typo, and it is kept."""

    batch, verdict = stage(db, row("REH-909", minimum="0", maximum="0"))

    assert verdict.error_count == 0
    accept_batch(db, batch.batch_id)
    activity = masters.get_activity(db, "REH-909")
    assert (activity.monthly_min, activity.monthly_max) == (0, 0)


# --- identity, re-upload and rejection ----------------------------------


def test_a_row_without_an_activity_id_is_refused(db):
    body = ",Rehearsal,Rehearsal activity,," + "1,1,4,2,\n"
    batch, verdict = stage(db, body)

    assert verdict.error_count == 1
    assert "missing activity id" in verdict.outcomes[0].messages[0]


def test_a_re_upload_updates_instead_of_adding_a_second_copy(db):
    first, _ = stage_bytes(db, "activity", "catalogue.csv", catalogue(10))
    accept_batch(db, first.batch_id)

    second, _ = stage_bytes(db, "activity", "catalogue.csv", catalogue(10))
    result = accept_batch(db, second.batch_id)

    assert (result.inserted, result.updated) == (0, 10)
    assert len(masters.list_activities(db)) == 10
    assert len({activity.activity_id for activity in masters.list_activities(db)}) == 10


def test_two_rows_naming_one_record_are_refused(db):
    body = row("REH-910", description="First wording") + row(
        "REH-910", category="Rehearsal other", description="Second wording"
    )
    batch, verdict = stage(db, body)

    assert verdict.error_count == 1
    duplicate = [outcome for outcome in verdict.outcomes if outcome.severity == "error"]
    assert len(duplicate) == 1
    assert "the same record as row 1" in duplicate[0].messages[-1]


def test_a_rejected_batch_leaves_the_master_untouched(db):
    batch, _ = stage_bytes(db, "activity", "catalogue.csv", catalogue(5))
    reject_batch(db, batch.batch_id, note="wrong version of the catalogue")

    assert get_batch(db, batch.batch_id).status == "rejected"
    assert masters.list_activities(db) == ()


def test_a_batch_staged_before_the_rule_existed_cannot_be_accepted(db):
    """A batch already in a database may carry rows staged with blanks.

    The guard refuses it rather than completing the numbers, which is what would have
    happened before: weight 1.0, minimum 0, maximum 10, gap 2.
    """

    batch, _ = stage(db, row("REH-911"))
    assert get_batch(db, batch.batch_id).row_count == 1

    # Rewrite the staged payload the way the earlier contract allowed.
    connection = db
    connection.execute(
        """
        UPDATE import_staging_row
        SET normalized_json = json_remove(
            json_remove(json_set(normalized_json, '$.weight', json('null')), '$.monthly_min'),
            '$.monthly_max'
        )
        WHERE batch_id = ? AND row_number = 1
        """,
        (batch.batch_id,),
    )
    connection.commit()

    with pytest.raises(StorageError) as refusal:
        accept_batch(db, batch.batch_id)

    assert refusal.value.constraint == C_ACTIVITY_CONTRACT_UNSATISFIED
    assert "no weight" in str(refusal.value)
    assert masters.list_activities(db) == ()
    assert get_batch(db, batch.batch_id).status == "staged"


# --- the importer as a whole -------------------------------------------


def test_the_staged_payload_carries_the_supplied_numbers(db):
    batch, _ = stage(db, row("REH-912", weight="3", minimum="2", maximum="5", gap="3"))
    staged = staging_rows(db, batch.batch_id)[0]

    assert staged.payload["weight"] == 3.0
    assert staged.payload["monthly_min"] == 2
    assert staged.payload["monthly_max"] == 5
    assert staged.payload["repeat_gap_days"] == 3
    assert staged.raw["Weight"] == "3"


def test_the_judge_is_the_place_that_refuses_a_blank_weight():
    verdict = judge_dataset(
        dataset="activity",
        headers=["Activity ID", "Category", "Description", "Weight"],
        rows=[{"Activity ID": "REH-913", "Category": "Rehearsal", "Description": "d", "Weight": ""}],
    )

    assert verdict.error_count == 1
    assert "supplied" in verdict.outcomes[0].messages[0]


def test_readiness_measures_the_imported_catalogue(db):
    batch, _ = stage_bytes(db, "activity", "catalogue.csv", catalogue(46))
    accept_batch(db, batch.batch_id)

    report = assess_readiness(db, date(2026, 1, 1), date(2026, 1, 31))
    item = next(entry for entry in report.items if entry.key == "activities")
    assert item.satisfied is True
    assert item.detail == "46 activity record(s) are stored"


def test_the_catalogue_path_holds_no_activity_selection(db):
    """Importing a catalogue must not start choosing which activity falls on a date."""

    import inspect

    from dailyworkreg.infrastructure.imports import datasets
    from dailyworkreg.infrastructure.sqlite import imports as store

    for module in (datasets, store):
        source = inspect.getsource(module)
        for forbidden in ("schedule_activity", "select_activities", "weighted_choice"):
            assert forbidden not in source, forbidden


def test_the_repository_carries_no_approved_catalogue_yet():
    """The blank template is blank and the proposed file is not importable data.

    This is the state Act 17 must not change by inventing content: the blank template
    holds its headings and no row, and the proposed examples say what they are.
    """

    repo = Path(__file__).resolve().parents[1]
    blank = (repo / "data" / "templates" / "03_activity_catalogue_blank_import.csv").read_text(
        encoding="utf-8-sig"
    )
    assert blank.strip().count("\n") == 0, "the blank template must stay blank"

    proposed = (repo / "data" / "templates" / "03_activity_proposed_examples_DO_NOT_IMPORT.csv").read_text(
        encoding="utf-8-sig"
    )
    assert "PROPOSED" in proposed
    read = read_source_file(
        repo / "data" / "templates" / "03_activity_proposed_examples_DO_NOT_IMPORT.csv"
    )
    assert len(read.rows) == 51
