"""A DTC identifier is the office's own code, not the tool's.

One office files its transformers as `SCD-12`; the next files them as `SCE142`. Both are the
office's identifier, and the tool has no business dictating the form: the roster is the
office's record and the identifier is how it reports work. What an identifier must still be
is *a code* — a short letter prefix and a number — so that a cell holding a placeholder, a
bare number or a name dragged into the wrong column is refused with a reason rather than
imported as a transformer.

The letters in an identifier are never read as a class. Which records requirements §8
excludes (the five SCF and one SCE of the original source) is decided by the `Class` column
the office supplies, because the original source carried no class column at all and a prefix
is a filing convention rather than a classification.
"""

from __future__ import annotations

import pytest
from dailyworkreg.infrastructure.imports.datasets import (
    EXCLUDE,
    SEVERITY_ERROR,
    judge_dataset,
)
from dailyworkreg.infrastructure.imports.tabular import read_source_bytes


def judge(csv: str):
    source = read_source_bytes("roster.csv", csv.encode())
    return judge_dataset("dtc", source.headers, source.rows)


def one(dtc_id: str, *, source_class: str = "DEPT", name: str = "A DTC"):
    verdict = judge(f"DTC ID,DTC Name,Class\n{dtc_id},{name},{source_class}\n")
    return verdict.outcomes[0]


# --- the form is the office's own -----------------------------------------


@pytest.mark.parametrize(
    "dtc_id",
    (
        "SCD-12",  # the office this tool was built for
        "SCE142",  # the next one, letters running straight into the number
        "SCE1",
        "SCE1000",
        "SCD_5",  # another office's separator
        "SCD/5",
        "SCE 142",
        "DTC42",  # no meaning in the letters at all
        "T-1",
    ),
)
def test_an_office_identifier_of_any_of_these_forms_is_accepted(dtc_id: str) -> None:
    outcome = one(dtc_id)
    assert outcome.severity != SEVERITY_ERROR, outcome.messages
    assert outcome.payload["dtc_id"] == dtc_id
    assert outcome.payload["dtc_class"] == "DEPT"


def test_the_next_office_whole_roster_is_accepted_as_it_stands() -> None:
    csv = "DTC ID,DTC Name,Class,kVA,Feeder\n" + "".join(
        f"SCE{number},DTC {number},DEPT,100,MF12 RML Nagara\n" for number in range(1, 155)
    )
    verdict = judge(csv)
    assert len(verdict.outcomes) == 154
    assert all(outcome.is_importable for outcome in verdict.outcomes)
    assert verdict.can_accept is True
    assert [outcome.payload["dtc_id"] for outcome in verdict.outcomes[:3]] == [
        "SCE1",
        "SCE2",
        "SCE3",
    ]


# --- what is still refused ------------------------------------------------


@pytest.mark.parametrize(
    "dtc_id",
    (
        "#N/A",  # a placeholder, the office's own marker for "not known"
        "142",  # a bare number: which column did this come from?
        "MILAN CHOWTRY",  # a name in the identifier column
        "110/11KV MANDLI",  # a station dragged into the wrong column
        "SCE-142-3",  # not one prefix and one number
        "SCE",  # a prefix with no number
    ),
)
def test_a_cell_that_is_not_a_code_is_refused_and_named(dtc_id: str) -> None:
    outcome = one(dtc_id)
    assert outcome.severity == SEVERITY_ERROR
    assert outcome.payload is None
    assert dtc_id in outcome.messages[0]
    assert "is not a code" in outcome.messages[0]
    assert "SCD-12 or SCE142" in outcome.messages[0]


def test_a_refused_identifier_does_not_stop_the_rest_of_the_file() -> None:
    verdict = judge(
        "DTC ID,DTC Name,Class\n"
        "SCE1,Alpha,DEPT\n"
        "#N/A,Broken,DEPT\n"
        "SCE2,Beta,MSB\n"
    )
    assert [outcome.is_importable for outcome in verdict.outcomes] == [True, False, True]
    assert verdict.can_accept is False


# --- the letters are not a class ------------------------------------------


def test_the_class_column_decides_exclusion_and_the_prefix_never_does() -> None:
    verdict = judge(
        "DTC ID,DTC Name,Class\n"
        "SCE1,Transformer,DEPT\n"  # the office's own series, classed DEPT: imported
        "SCF-2,Section feeder,SCF\n"  # the excluded class, stated by the file: kept out
        "SCE-3,Section consumer,SCE\n"
    )
    by_id = {outcome.payload["dtc_id"] if outcome.payload else outcome.raw["DTC ID"]: outcome
             for outcome in verdict.outcomes}
    assert by_id["SCE1"].is_importable
    assert by_id["SCF-2"].decision == EXCLUDE
    assert by_id["SCF-2"].severity == "warning"
    assert "§8" in by_id["SCF-2"].messages[0]
    assert by_id["SCE-3"].decision == EXCLUDE


def test_an_office_that_separates_its_own_series_is_not_read_as_a_class() -> None:
    # A hyphen is a separator, not a statement of class: this is the office's code written
    # with one, and the file says the transformer is a DEPT one.
    outcome = one("SCE-142", source_class="DEPT")
    assert outcome.is_importable
    assert outcome.payload["source_class"] == "DEPT"


def test_a_row_the_file_calls_msb_is_imported_whatever_its_prefix() -> None:
    # The designed consequence of reading the class from the file: a row the office classifies
    # as MSB is a self-DTC record, and the tool does not overrule the office by looking at the
    # letters of its identifier.
    outcome = one("SCF-2", source_class="MSB")
    assert outcome.is_importable
    assert outcome.payload["dtc_class"] == "SELF_DTC"
    assert outcome.payload["source_class"] == "MSB"


def test_a_row_with_no_class_is_refused_rather_than_silently_excluded() -> None:
    verdict = judge("DTC ID,DTC Name,Class\nSCE1,Transformer,\n")
    outcome = verdict.outcomes[0]
    assert outcome.severity == SEVERITY_ERROR
    assert "has no class" in outcome.messages[0]
    assert outcome.decision != EXCLUDE
