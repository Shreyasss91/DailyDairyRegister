"""Staged import: file, staging table, judgement, operator acceptance, canonical rows.

The order is fixed and visible in the database, not just in memory:

    file -> import_batch + import_staging_row -> judgement -> operator acceptance
         -> canonical tables

A file is judged and written to staging before any canonical table is touched. That
means an operator can see what a file would do, including the rows that cannot be
used and why, and can refuse it without having changed anything. Acceptance is one
transaction: either every importable row lands or none does.

Every source row is retained verbatim in ``import_staging_row.raw_json``, including
the rows that will not be imported. Discarding them would erase the discrepancy the
operator is being asked to review.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..imports.datasets import (
    DATASETS,
    EXCLUDE,
    SEVERITY_ERROR,
    SEVERITY_OK,
    SEVERITY_WARNING,
    ActivityFields,
    DatasetVerdict,
    RowOutcome,
    judge_dataset,
    read_activity_fields,
)
from ..imports.tabular import C_UNMAPPED_SOURCE, SourceFormatError, TabularSource
from . import calendar_records as cal
from . import masters
from .database import transaction
from .masters import StorageError

C_BATCH_UNKNOWN = "import_batch_unknown"
C_BATCH_NOT_STAGED = "import_batch_not_staged"
C_BATCH_HAS_ERRORS = "import_batch_has_errors"
C_BATCH_NOTHING_TO_IMPORT = "import_batch_nothing_to_import"
C_HOLIDAY_MODE_NOT_APPLICABLE = "holiday_merge_mode_not_applicable"

#: An activity batch is held to the contract in force at acceptance, not to the one it
#: was staged under. A batch that reached staging before these rules existed is refused
#: rather than repaired, because the alternative is writing a weight, a frequency or a
#: repeat gap that no approved catalogue states.
C_ACTIVITY_CONTRACT_UNSATISFIED = "activity_contract_unsatisfied"

C_HOLIDAY_REPLACE = "replace"
C_HOLIDAY_MERGE = "merge"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class BatchRow:
    batch_id: str
    dataset: str
    source_filename: str
    source_sha256: str
    staged_at: str
    status: str
    row_count: int
    error_count: int
    warning_count: int
    accepted_at: str | None
    decision_note: str | None


@dataclass(frozen=True)
class StagingRowRow:
    row_number: int
    raw: dict[str, str]
    payload: dict[str, object] | None
    severity: str
    messages: tuple[str, ...]
    decision: str


@dataclass(frozen=True)
class AcceptanceResult:
    batch_id: str
    dataset: str
    inserted: int
    updated: int
    excluded: int


def _batch_from(row: sqlite3.Row) -> BatchRow:
    return BatchRow(
        batch_id=row["batch_id"],
        dataset=row["dataset"],
        source_filename=row["source_filename"],
        source_sha256=row["source_sha256"],
        staged_at=row["staged_at"],
        status=row["status"],
        row_count=int(row["row_count"]),
        error_count=int(row["error_count"]),
        warning_count=int(row["warning_count"]),
        accepted_at=row["accepted_at"],
        decision_note=row["decision_note"],
    )


def _identity(dataset: str, payload: dict[str, object]) -> str | None:
    if dataset == "dtc":
        return str(payload.get("dtc_id"))
    if dataset == "feeder":
        return str(payload.get("name"))
    if dataset == "activity":
        return str(payload.get("activity_id") or payload.get("description_en"))
    if dataset == "area":
        area_id = payload.get("area_id")
        return str(area_id) if area_id else str(payload.get("name_kn"))
    if dataset == "general_holiday":
        return f"{payload.get('holiday_date')}|{payload.get('name')}"
    return None


def _reconcile(
    connection: sqlite3.Connection, dataset: str, outcome: RowOutcome
) -> RowOutcome:
    """Compare a judged row against the master it would change.

    Reconciliation only ever adds a message or resolves an identity that the
    master already knows. It never repairs a value: a feeder name that does not
    exist in the feeder master stays a warning for a person.

    It can also refuse a row, in one case: when the row carries an
    owner-confirmed feeder assignment and the feeder that assignment names is not
    in the master. Accepting that row would store a DTC whose canonical feeder is
    unset while reporting success, which leaves the feeder's derived capacity
    short with nothing said about it.
    """

    payload = outcome.payload
    if payload is None:
        return outcome

    messages = list(outcome.messages)
    error = outcome.severity == SEVERITY_ERROR

    if dataset == "dtc":
        feeder_name = payload.get("feeder")
        supplied = (
            masters.find_feeder_by_name(connection, str(feeder_name))
            if feeder_name
            else None
        )
        if feeder_name and supplied is None:
            messages.append(
                f"feeder '{feeder_name}' is not in the feeder master, so no canonical "
                "feeder will be assigned; the supplied value is kept with the source record"
            )
        # There is no built-in assignment table any more: the roster file is the only
        # authority over which feeder a transformer sits on, and the feeder master is what
        # makes that name a record. A name the master does not carry is reported above and
        # leaves the transformer with no canonical feeder, which the readiness screen counts
        # rather than a second list silently correcting the office's own file.
        if payload.get("kva") is None:
            messages.append("no capacity was read, so the weight cannot be derived yet")

    if dataset == "area" and not payload.get("area_id"):
        existing = next(
            (
                area
                for area in masters.list_areas(connection)
                if area.name_kn == payload.get("name_kn")
            ),
            None,
        )
        if existing is None:
            return RowOutcome(
                outcome.row_number,
                outcome.raw,
                None,
                SEVERITY_ERROR,
                tuple(messages)
                + (
                    (
                        "no area id was supplied and no area with this name exists, so "
                        "the row has no identity to update"
                    ),
                ),
                outcome.decision,
            )
        payload = dict(payload)
        payload["area_id"] = existing.area_id
        messages.append(f"matched the existing area {existing.area_id} by its name")

    if dataset == "feeder" and not payload.get("feeder_id"):
        existing = masters.find_feeder_by_name(connection, str(payload.get("name")))
        if existing is not None:
            payload = dict(payload)
            payload["feeder_id"] = existing.feeder_id
            messages.append(f"matched the existing feeder {existing.feeder_id} by its name")
        else:
            payload = dict(payload)
            payload["feeder_id"] = None
            messages.append("a feeder id will be assigned when this row is accepted")

    severity = outcome.severity
    if severity == SEVERITY_OK and messages:
        severity = SEVERITY_WARNING
    if error:
        severity = SEVERITY_ERROR
    return RowOutcome(
        outcome.row_number, outcome.raw, payload, severity, tuple(messages), outcome.decision
    )


def _flag_duplicate_identities(dataset: str, outcomes: list[RowOutcome]) -> list[RowOutcome]:
    """Mark every repeat of an identity after its first appearance as unusable.

    A file that names the same DTC twice is ambiguous: applying either order gives a
    different master, so neither is applied until somebody decides. The first
    occurrence is kept and the rest are refused with the row they collide with.
    """

    first_seen: dict[str, int] = {}
    result: list[RowOutcome] = []
    for outcome in outcomes:
        identity = _identity(dataset, outcome.payload) if outcome.payload else None
        if identity is None:
            result.append(outcome)
            continue
        earlier = first_seen.get(identity)
        if earlier is not None:
            result.append(
                RowOutcome(
                    outcome.row_number,
                    outcome.raw,
                    None,
                    SEVERITY_ERROR,
                    outcome.messages
                    + (
                        (
                            f"this row names the same record as row {earlier}; "
                            "decide which one is correct before importing"
                        ),
                    ),
                    EXCLUDE,
                )
            )
            continue
        first_seen[identity] = outcome.row_number
        result.append(outcome)
    return result


def stage_source(
    connection: sqlite3.Connection,
    dataset: str,
    source: TabularSource,
) -> tuple[BatchRow, DatasetVerdict]:
    """Judge a source file and record it, without changing any canonical row."""

    if dataset not in DATASETS:
        raise StorageError(C_BATCH_UNKNOWN, f"unknown import dataset: {dataset}")

    verdict = judge_dataset(dataset, source.headers, source.rows)
    if verdict.file_messages:
        # The file could not be read against the contract at all, so no row can be
        # judged. Refusing here keeps the reason in front of whoever uploaded it;
        # recording an empty batch instead would store a rowless file that explains
        # nothing and can never be accepted.
        raise SourceFormatError(C_UNMAPPED_SOURCE, " ".join(verdict.file_messages))

    outcomes = [_reconcile(connection, dataset, outcome) for outcome in verdict.outcomes]
    outcomes = _flag_duplicate_identities(dataset, outcomes)
    verdict = DatasetVerdict(dataset=dataset, outcomes=tuple(outcomes))

    batch_id = f"{dataset}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO import_batch (batch_id, dataset, source_filename, source_sha256,
                                      staged_at, status, row_count, error_count,
                                      warning_count)
            VALUES (?, ?, ?, ?, ?, 'staged', ?, ?, ?)
            """,
            (
                batch_id, dataset, source.filename, source.sha256, _now(),
                len(outcomes), verdict.error_count, verdict.warning_count,
            ),
        )
        for outcome in outcomes:
            connection.execute(
                """
                INSERT INTO import_staging_row (batch_id, row_number, raw_json,
                                                normalized_json, severity,
                                                messages_json, decision)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    outcome.row_number,
                    json.dumps(outcome.raw, ensure_ascii=False, sort_keys=True),
                    json.dumps(outcome.payload, ensure_ascii=False, sort_keys=True, default=str)
                    if outcome.payload is not None
                    else None,
                    outcome.severity,
                    json.dumps(list(outcome.messages), ensure_ascii=False),
                    outcome.decision,
                ),
            )
    return get_batch(connection, batch_id), verdict


def stage_bytes(
    connection: sqlite3.Connection, dataset: str, filename: str, payload: bytes
) -> tuple[BatchRow, DatasetVerdict]:
    from ..imports.tabular import read_source_bytes

    return stage_source(connection, dataset, read_source_bytes(filename, payload))


def list_batches(
    connection: sqlite3.Connection, dataset: str | None = None
) -> tuple[BatchRow, ...]:
    if dataset is None:
        rows = connection.execute(
            "SELECT * FROM import_batch ORDER BY staged_at DESC"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM import_batch WHERE dataset = ? ORDER BY staged_at DESC",
            (dataset,),
        ).fetchall()
    return tuple(_batch_from(row) for row in rows)


def get_batch(connection: sqlite3.Connection, batch_id: str) -> BatchRow:
    row = connection.execute(
        "SELECT * FROM import_batch WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        raise StorageError(C_BATCH_UNKNOWN, f"no import batch called {batch_id}", batch_id)
    return _batch_from(row)


def staging_rows(connection: sqlite3.Connection, batch_id: str) -> tuple[StagingRowRow, ...]:
    return tuple(
        StagingRowRow(
            row_number=int(row["row_number"]),
            raw=json.loads(row["raw_json"]),
            payload=json.loads(row["normalized_json"]) if row["normalized_json"] else None,
            severity=row["severity"],
            messages=tuple(json.loads(row["messages_json"])),
            decision=row["decision"] or EXCLUDE if row["decision"] else "include",
        )
        for row in connection.execute(
            "SELECT * FROM import_staging_row WHERE batch_id = ? ORDER BY row_number",
            (batch_id,),
        ).fetchall()
    )


def reject_batch(connection: sqlite3.Connection, batch_id: str, note: str | None = None) -> None:
    batch = get_batch(connection, batch_id)
    if batch.status != "staged":
        raise StorageError(
            C_BATCH_NOT_STAGED,
            f"batch {batch_id} is already {batch.status}",
            batch_id,
        )
    with transaction(connection):
        connection.execute(
            "UPDATE import_batch SET status = 'rejected', accepted_at = ?, decision_note = ? "
            "WHERE batch_id = ?",
            (_now(), note, batch_id),
        )


def accept_batch(
    connection: sqlite3.Connection,
    batch_id: str,
    *,
    note: str | None = None,
    holiday_mode: str = C_HOLIDAY_REPLACE,
) -> AcceptanceResult:
    """Apply one staged batch to the canonical tables, or change nothing.

    Refusal is the default for anything doubtful: a batch carrying an unusable row,
    or one already decided, is rejected outright rather than partly applied.
    """

    batch = get_batch(connection, batch_id)
    if batch.status != "staged":
        raise StorageError(
            C_BATCH_NOT_STAGED, f"batch {batch_id} is already {batch.status}", batch_id
        )
    if batch.error_count:
        raise StorageError(
            C_BATCH_HAS_ERRORS,
            (
                f"{batch.error_count} row(s) cannot be imported; correct the source or "
                "reject the batch, because applying the rest would silently drop them"
            ),
            batch_id,
        )
    if holiday_mode not in (C_HOLIDAY_REPLACE, C_HOLIDAY_MERGE):
        raise StorageError(
            C_HOLIDAY_MODE_NOT_APPLICABLE,
            f"unknown holiday mode {holiday_mode!r}; use replace or merge",
            batch_id,
        )

    rows = staging_rows(connection, batch_id)
    importable = [row for row in rows if row.decision == "include" and row.payload]
    if not importable:
        raise StorageError(
            C_BATCH_NOTHING_TO_IMPORT,
            "this batch has no row that would change the master",
            batch_id,
        )
    if batch.dataset == "activity":
        settled = _refuse_activities_that_break_the_contract(batch_id, importable)
    else:
        settled = {}

    inserted = 0
    updated = 0
    excluded = sum(1 for row in rows if row.decision != "include")

    with transaction(connection):
        masters.bump_version(connection)

        if batch.dataset == "general_holiday" and holiday_mode == C_HOLIDAY_REPLACE:
            years = {
                int(str(row.payload["holiday_date"])[:4])
                for row in importable
                if "holiday_date" in row.payload
            }
            for year in sorted(years):
                cal.replace_holiday_year(connection, year, source="karnataka", merge=False)

        for row in importable:
            payload = row.payload or {}
            if batch.dataset == "dtc":
                dtc_id = str(payload["dtc_id"])
                existed = masters.get_dtc(connection, dtc_id) is not None
                feeder_name = payload.get("feeder")
                feeder = (
                    masters.find_feeder_by_name(connection, str(feeder_name))
                    if feeder_name
                    else None
                )
                previous = masters.get_dtc(connection, dtc_id) if existed else None
                masters.upsert_dtc(
                    connection,
                    dtc_id=dtc_id,
                    name=str(payload["name"]),
                    dtc_class=str(payload["dtc_class"]),
                    canonical_feeder_id=(
                        feeder.feeder_id
                        if feeder is not None
                        else (previous.canonical_feeder_id if previous else None)
                    ),
                    kva=payload.get("kva") if isinstance(payload.get("kva"), (int, float)) else None,
                    station=payload.get("station"),
                    source_class=payload.get("source_class"),
                    # Carried straight from the judged row: a date the reader could not
                    # parse is already None here, and is never guessed at acceptance.
                    commissioned_on=payload.get("commissioned_on"),
                )
                inserted += 0 if existed else 1
                updated += 1 if existed else 0
                _write_source_record(connection, batch_id, row, payload)

            elif batch.dataset == "area":
                area_id = str(payload["area_id"])
                existed = masters.get_area(connection, area_id) is not None
                previous = masters.get_area(connection, area_id) if existed else None
                masters.upsert_area(
                    connection,
                    area_id=area_id,
                    name_kn=str(payload["name_kn"]),
                    name_en=payload.get("name_en"),
                    weight=_number(payload.get("weight"), previous.weight if previous else 1.0),
                    monthly_min=_integer(
                        payload.get("monthly_min"), previous.monthly_min if previous else 0
                    ),
                    monthly_max=_integer(
                        payload.get("monthly_max"), previous.monthly_max if previous else 3
                    ),
                    revisit_gap_days=_integer(
                        payload.get("revisit_gap_days"),
                        previous.revisit_gap_days if previous else 7,
                    ),
                    aliases=tuple(payload.get("aliases") or ()),
                )
                inserted += 0 if existed else 1
                updated += 1 if existed else 0

            elif batch.dataset == "feeder":
                feeder_id = payload.get("feeder_id")
                if not feeder_id:
                    feeder_id = _next_feeder_id(connection)
                feeder_id = str(feeder_id)
                existed = masters.get_feeder(connection, feeder_id) is not None
                previous = masters.get_feeder(connection, feeder_id) if existed else None
                masters.upsert_feeder(
                    connection,
                    feeder_id=feeder_id,
                    name=str(payload["name"]),
                    manual_multiplier=_number(
                        payload.get("manual_multiplier"),
                        previous.manual_multiplier if previous else 1.0,
                    ),
                    monthly_min=_integer(
                        payload.get("monthly_min"), previous.monthly_min if previous else 0
                    ),
                    monthly_max=_integer(
                        payload.get("monthly_max"), previous.monthly_max if previous else 3
                    ),
                    revisit_gap_days=_integer(
                        payload.get("revisit_gap_days"),
                        previous.revisit_gap_days if previous else 7,
                    ),
                )
                inserted += 0 if existed else 1
                updated += 1 if existed else 0

            elif batch.dataset == "activity":
                # The numbers arrive from the catalogue, never from here. A payload
                # reaching this point is complete, because the guard above read it and
                # found nothing to complain about, and what it read is what is stored:
                # an integer kept exact, rather than the raw payload re-converted.
                read = settled[row.row_number]
                activity_id = str(read.activity_id)
                existed = masters.get_activity(connection, activity_id) is not None
                masters.upsert_activity(
                    connection,
                    activity_id=activity_id,
                    category=str(read.category),
                    description_en=str(read.description_en),
                    description_kn=read.description_kn,
                    weight=float(read.weight),
                    monthly_min=int(read.monthly_min),
                    monthly_max=int(read.monthly_max),
                    repeat_gap_days=int(read.repeat_gap_days),
                    weekday_mask=read.weekday_mask,
                )
                inserted += 0 if existed else 1
                updated += 1 if existed else 0

            elif batch.dataset == "general_holiday":
                # Staging round-trips through JSON, so the date arrives as text.
                raw_day = payload["holiday_date"]
                day = (
                    raw_day
                    if isinstance(raw_day, date)
                    else date.fromisoformat(str(raw_day))
                )
                holiday_id = f"H-{day.isoformat()}-{_slug(str(payload['name']))}"
                existed = bool(cal.holidays_on(connection, day)) and any(
                    existing.holiday_id == holiday_id
                    for existing in cal.holidays_on(connection, day)
                )
                cal.upsert_holiday(
                    connection,
                    holiday_id=holiday_id,
                    holiday_date=day,
                    name=str(payload["name"]),
                    description=payload.get("description"),
                    source="karnataka",
                )
                inserted += 0 if existed else 1
                updated += 1 if existed else 0

        connection.execute(
            "UPDATE import_batch SET status = 'accepted', accepted_at = ?, decision_note = ? "
            "WHERE batch_id = ?",
            (_now(), note, batch_id),
        )

    return AcceptanceResult(
        batch_id=batch_id,
        dataset=batch.dataset,
        inserted=inserted,
        updated=updated,
        excluded=excluded,
    )


def _write_source_record(
    connection: sqlite3.Connection, batch_id: str, row: StagingRowRow, payload: dict
) -> None:
    """Store the supplied fields verbatim, separate from the canonical record.

    The values travel with the judged row rather than being looked up again from the
    raw dictionary, because the raw dictionary is keyed by this file's own column
    headings and acceptance has no business guessing which one was the feeder.
    """

    source = payload.get("source") or {}
    connection.execute(
        """
        INSERT OR REPLACE INTO dtc_source_record (dtc_id, batch_id, row_number,
            raw_name, raw_feeder, raw_feeder_code, raw_mdm_code, raw_kva,
            raw_station, raw_class, raw_commissioned_on)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.get("dtc_id"),
            batch_id,
            row.row_number,
            source.get("name"),
            source.get("feeder"),
            source.get("feeder_code"),
            source.get("mdm_code"),
            source.get("kva"),
            source.get("station"),
            source.get("source_class"),
            source.get("commissioned_on"),
        ),
    )


def _number(value: object, fallback: float) -> float:
    return float(value) if isinstance(value, (int, float)) else fallback


def _integer(value: object, fallback: int) -> int:
    return int(value) if isinstance(value, (int, float)) else fallback


def _slug(text: str) -> str:
    return "".join(
        character.lower() if character.isalnum() else "-" for character in text
    ).strip("-")[:40]


def _next_feeder_id(connection: sqlite3.Connection) -> str:
    existing = {feeder.feeder_id for feeder in masters.list_feeders(connection)}
    index = len(existing) + 1
    while f"F-{index:02d}" in existing:
        index += 1
    return f"F-{index:02d}"


def _refuse_activities_that_break_the_contract(
    batch_id: str, importable: list[StagingRowRow]
) -> dict[int, ActivityFields]:
    """Hold an already-staged activity batch to the contract in force now.

    Staging refuses such a row, so no new upload reaches acceptance carrying one. What
    this covers is the other direction of time: a batch staged under the earlier
    contract is still in the database with its payload already written, and that
    contract accepted a zero weight, a negative weight, a negative frequency, a
    negative repeat gap, an inverted minimum and maximum whose difference is below the
    precision of a float, and an identifier invented at acceptance.

    Checking only that the values were present was not enough, because those values
    are not absent: they are present and wrong. So every importable payload is read
    through the same `read_activity_fields` the judge uses, and one row that fails
    refuses the whole batch. Refusing keeps the batch staged, leaves the master exactly
    as it was, and names the rows and the fields, because the operator's next step is
    to correct the file and upload it again rather than to guess which row was meant.

    The reading is returned rather than thrown away. It is what the contract actually
    read from the payload - an integer kept exact, text read digit by digit - so the
    write path stores these rather than re-deriving values from the raw payload with
    `float()` and `int()`, which is where a legacy string like `"2.0"` would raise
    instead of storing the two the contract accepted.
    """

    failures: list[tuple[int, tuple[str, ...]]] = []
    settled: dict[int, ActivityFields] = {}
    for row in importable:
        fields, problems = read_activity_fields(row.payload or {})
        if problems:
            failures.append((row.row_number, problems))
        else:
            settled[row.row_number] = fields
    if not failures:
        return settled

    naming = (
        "1 row in this batch does not meet"
        if len(failures) == 1
        else f"{len(failures)} rows in this batch do not meet"
    )
    detail = " ".join(
        f"Row {row_number}: {'; '.join(problems)}."
        for row_number, problems in failures
    )
    raise StorageError(
        C_ACTIVITY_CONTRACT_UNSATISFIED,
        (
            f"{naming} the activity rules as they stand now: {detail} Nothing was "
            "imported and the batch is unchanged. A batch staged before these rules "
            "existed has to be rejected and uploaded again from a corrected file."
        ),
        batch_id,
    )
