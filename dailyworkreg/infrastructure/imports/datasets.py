"""Per-dataset column mapping, coercion and reconciliation rules.

Each dataset describes which source columns it reads, which of them it cannot work
without, and what makes a row unacceptable. The rules are deliberately noisy: a row
that cannot be used is recorded with its reason rather than dropped, and a value
that cannot be coerced is reported instead of defaulted.

Column names are matched leniently, because the utility's sheet headings are not
ours to dictate. Aliases are listed per field; when a required field matches no
column the row is refused with the missing field named, which is more useful than a
guess about which column was meant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

#: Auto-excluded source classes, from requirements §8: the 113 SCD records are
#: included and the SCF and SCE records are not.
EXCLUDED_SOURCE_CLASSES = ("SCF", "SCE")

INCLUDE = "include"
EXCLUDE = "exclude"

SEVERITY_OK = "ok"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"


def normalise_header(header: str) -> str:
    """Fold a column heading to letters and digits for alias matching."""

    return re.sub(r"[^a-z0-9]+", "", header.strip().lower())


@dataclass(frozen=True)
class FieldSpec:
    field: str
    aliases: tuple[str, ...]
    required: bool = False


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    label: str
    fields: tuple[FieldSpec, ...]

    def header_map(self, headers: tuple[str, ...]) -> dict[str, str]:
        """Map our field names onto this file's actual column headings."""

        folded = {normalise_header(header): header for header in headers if header}
        mapping: dict[str, str] = {}
        for spec in self.fields:
            for alias in spec.aliases:
                found = folded.get(normalise_header(alias))
                if found is not None:
                    mapping[spec.field] = found
                    break
        return mapping

    def missing_required(self, headers: tuple[str, ...]) -> tuple[str, ...]:
        mapping = self.header_map(headers)
        return tuple(
            spec.field
            for spec in self.fields
            if spec.required and spec.field not in mapping
        )


@dataclass(frozen=True)
class RowOutcome:
    """One source row, judged. Never discarded: an excluded row keeps its reason."""

    row_number: int
    raw: dict[str, str]
    payload: dict[str, object] | None
    severity: str
    messages: tuple[str, ...] = ()
    decision: str = INCLUDE

    @property
    def is_importable(self) -> bool:
        return self.severity != SEVERITY_ERROR and self.decision == INCLUDE


DTC_SPEC = DatasetSpec(
    name="dtc",
    label="DTC master",
    fields=(
        FieldSpec("dtc_id", ("dtc id", "dtcid", "scd", "scd no", "dtc code", "id"), True),
        FieldSpec("name", ("dtc name", "dtcname", "name", "dtc"), True),
        FieldSpec("source_class", ("class", "category", "dtc class", "type"), True),
        FieldSpec("feeder", ("feeder", "feeder name", "feeder desc"), False),
        FieldSpec("feeder_code", ("feeder code", "feedercode", "fdr code"), False),
        FieldSpec("mdm_code", ("mdm code", "mdmcode", "mdm"), False),
        FieldSpec("kva", ("kva", "capacity", "dtc kva"), False),
        FieldSpec("station", ("station", "station name", "location", "subdivision"), False),
    ),
)

AREA_SPEC = DatasetSpec(
    name="area",
    label="Area master",
    fields=(
        FieldSpec("area_id", ("area id", "areaid", "code", "id"), False),
        FieldSpec("name_kn", ("kannada name", "name kn", "name kannada", "kannada"), True),
        FieldSpec("name_en", ("english name", "name en", "name english", "area name"), False),
        FieldSpec("aliases", ("aliases", "alias", "alternate names"), False),
        FieldSpec("weight", ("weight",), False),
        FieldSpec("monthly_min", ("monthly min", "min visits", "minimum"), False),
        FieldSpec("monthly_max", ("monthly max", "max visits", "maximum"), False),
        FieldSpec("revisit_gap_days", ("revisit gap", "gap days", "gap"), False),
    ),
)

FEEDER_SPEC = DatasetSpec(
    name="feeder",
    label="Feeder master",
    fields=(
        FieldSpec("feeder_id", ("feeder id", "feederid", "code", "id"), False),
        FieldSpec("name", ("feeder name", "name", "feeder"), True),
        FieldSpec("manual_multiplier", ("manual multiplier", "multiplier"), False),
        FieldSpec("monthly_min", ("monthly min", "minimum"), False),
        FieldSpec("monthly_max", ("monthly max", "maximum"), False),
        FieldSpec("revisit_gap_days", ("revisit gap", "gap days", "gap"), False),
    ),
)

ACTIVITY_SPEC = DatasetSpec(
    name="activity",
    label="Activity master",
    fields=(
        FieldSpec("activity_id", ("activity id", "activityid", "code", "id"), False),
        FieldSpec("category", ("category", "group", "type"), True),
        FieldSpec("description_en", ("description", "english", "description en", "activity"), True),
        FieldSpec("description_kn", ("kannada", "description kn", "kannada description"), False),
        FieldSpec("weight", ("weight",), False),
        FieldSpec("monthly_min", ("monthly min", "minimum"), False),
        FieldSpec("monthly_max", ("monthly max", "maximum"), False),
        FieldSpec("repeat_gap_days", ("repeat gap", "gap days", "gap"), False),
        FieldSpec("weekday_mask", ("weekdays", "weekday restriction", "days"), False),
    ),
)

GENERAL_HOLIDAY_SPEC = DatasetSpec(
    name="general_holiday",
    label="General Holidays",
    fields=(
        FieldSpec("holiday_date", ("date", "holiday date"), True),
        FieldSpec("name", ("holiday", "name", "holiday name", "festival"), True),
        FieldSpec("description", ("description", "remarks", "wording"), False),
    ),
)

DATASETS: dict[str, DatasetSpec] = {
    spec.name: spec
    for spec in (DTC_SPEC, AREA_SPEC, FEEDER_SPEC, ACTIVITY_SPEC, GENERAL_HOLIDAY_SPEC)
}


# --- coercion -------------------------------------------------------------


def _text(value: str | None) -> str | None:
    """A supplied value, kept as text. Blank stays None, never a default."""

    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _marker(value: str | None) -> bool:
    """Whether a cell holds a placeholder rather than a value."""

    return _text(value) in {"#N/A", "#NA", "N/A", "#REF!", "#VALUE!", "-"}


def coerce_int(value: str | None, *, field_name: str) -> tuple[int | None, list[str]]:
    text = _text(value)
    if text is None:
        return None, []
    if _marker(value):
        return None, [f"{field_name} is a placeholder ({text}), so no number was read"]
    try:
        return int(float(text)), []
    except ValueError:
        return None, [f"{field_name} '{text}' is not a whole number"]


def coerce_float(value: str | None, *, field_name: str) -> tuple[float | None, list[str]]:
    text = _text(value)
    if text is None:
        return None, []
    if _marker(value):
        return None, [f"{field_name} is a placeholder ({text}), so no number was read"]
    try:
        return float(text), []
    except ValueError:
        return None, [f"{field_name} '{text}' is not a number"]


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y")


def coerce_date(value: str | None) -> tuple[date | None, list[str]]:
    text = _text(value)
    if text is None:
        return None, []
    for pattern in _DATE_FORMATS:
        try:
            from datetime import datetime

            return datetime.strptime(text, pattern).date(), []
        except ValueError:
            continue
    return None, [f"'{text}' is not a recognisable date"]


def coerce_bool(value: str | None, *, default: bool = True) -> bool:
    text = _text(value)
    if text is None:
        return default
    return text.lower() not in {"no", "false", "0", "inactive", "n"}


_CLASS_MAP = {
    "dept": ("DEPT", "DEPT"),
    "department": ("DEPT", "DEPT"),
    "msb": ("SELF_DTC", "MSB"),
    "own": ("SELF_DTC", "OWN"),
}


# --- per-row judges -------------------------------------------------------


def judge_dtc(row: dict[str, str], mapping: dict[str, str], row_number: int) -> RowOutcome:
    def get(field_name: str) -> str | None:
        column = mapping.get(field_name)
        return row.get(column) if column else None

    messages: list[str] = []
    dtc_id = _text(get("dtc_id"))
    name = _text(get("name"))
    raw_class = _text(get("source_class"))

    if dtc_id is None:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR, ("no DTC id was supplied",)
        )
    if not re.fullmatch(r"(SCD|SCF|SCE)-\d+", dtc_id, flags=re.IGNORECASE):
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            (f"DTC id '{dtc_id}' is not of the form SCD-<number>",),
        )

    prefix = dtc_id.split("-", 1)[0].upper()
    upper_class = (raw_class or "").upper()
    if prefix in EXCLUDED_SOURCE_CLASSES or upper_class in EXCLUDED_SOURCE_CLASSES:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_WARNING,
            (
                (
                    f"{dtc_id} is a {prefix if prefix in EXCLUDED_SOURCE_CLASSES else upper_class} "
                    "record; requirements §8 includes the SCD records and excludes these, so "
                    "the row is kept for review and not imported"
                ),
            ),
            decision=EXCLUDE,
        )

    if name is None:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            (f"{dtc_id} has no DTC name",),
        )
    if raw_class is None:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            (f"{dtc_id} has no class, so it cannot be placed in DEPT or SELF_DTC",),
        )
    # The map is keyed in lower case; the supplied value was upper-cased for the
    # message and for the exclusion test, so it is folded back for the lookup.
    mapped = _CLASS_MAP.get(upper_class.lower())
    if mapped is None:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            (
                f"{dtc_id} has class '{upper_class}', which is not DEPT, MSB or OWN; "
                "assigning one here would be a guess",
            ),
        )
    dtc_class, source_class = mapped

    kva, kva_messages = coerce_float(get("kva"), field_name="kVA")
    messages.extend(kva_messages)
    if _marker(get("kva")):
        messages.append("the source capacity is a placeholder and was not treated as zero")

    payload = {
        "dtc_id": dtc_id,
        "name": name,
        "dtc_class": dtc_class,
        "source_class": source_class,
        "kva": kva,
        "station": _text(get("station")),
        "feeder": _text(get("feeder")),
        "feeder_code": _text(get("feeder_code")),
        "mdm_code": _text(get("mdm_code")),
        # The values exactly as supplied, kept with the row so acceptance can write
        # the source record without knowing this file's column headings. The
        # canonical payload above is our reading; this is the evidence.
        "source": {
            "name": get("name") or "",
            "feeder": get("feeder") or "",
            "feeder_code": get("feeder_code") or "",
            "mdm_code": get("mdm_code") or "",
            "kva": get("kva") or "",
            "station": get("station") or "",
            "source_class": get("source_class") or "",
        },
    }
    severity = SEVERITY_WARNING if messages else SEVERITY_OK
    return RowOutcome(row_number, dict(row), payload, severity, tuple(messages))


def judge_area(row: dict[str, str], mapping: dict[str, str], row_number: int) -> RowOutcome:
    def get(field_name: str) -> str | None:
        column = mapping.get(field_name)
        return row.get(column) if column else None

    name_kn = _text(get("name_kn"))
    if name_kn is None:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR, ("no area name was supplied",)
        )
    messages: list[str] = []
    weight, weight_messages = coerce_float(get("weight"), field_name="weight")
    messages.extend(weight_messages)
    monthly_min, min_messages = coerce_int(get("monthly_min"), field_name="monthly minimum")
    messages.extend(min_messages)
    monthly_max, max_messages = coerce_int(get("monthly_max"), field_name="monthly maximum")
    messages.extend(max_messages)
    if monthly_min is not None and monthly_max is not None and monthly_min > monthly_max:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            (f"monthly minimum {monthly_min} is above maximum {monthly_max}",),
        )
    gap, gap_messages = coerce_int(get("revisit_gap_days"), field_name="revisit gap")
    messages.extend(gap_messages)
    aliases_text = _text(get("aliases"))
    payload = {
        "area_id": _text(get("area_id")),
        "name_kn": name_kn,
        "name_en": _text(get("name_en")),
        "aliases": tuple(
            alias.strip() for alias in (aliases_text or "").split(";") if alias.strip()
        ),
        "weight": weight,
        "monthly_min": monthly_min,
        "monthly_max": monthly_max,
        "revisit_gap_days": gap,
    }
    severity = SEVERITY_WARNING if messages else SEVERITY_OK
    return RowOutcome(row_number, dict(row), payload, severity, tuple(messages))


def judge_feeder(row: dict[str, str], mapping: dict[str, str], row_number: int) -> RowOutcome:
    def get(field_name: str) -> str | None:
        column = mapping.get(field_name)
        return row.get(column) if column else None

    name = _text(get("name"))
    if name is None:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR, ("no feeder name was supplied",)
        )
    messages: list[str] = []
    multiplier, multiplier_messages = coerce_float(
        get("manual_multiplier"), field_name="manual multiplier"
    )
    messages.extend(multiplier_messages)
    monthly_min, min_messages = coerce_int(get("monthly_min"), field_name="monthly minimum")
    messages.extend(min_messages)
    monthly_max, max_messages = coerce_int(get("monthly_max"), field_name="monthly maximum")
    messages.extend(max_messages)
    gap, gap_messages = coerce_int(get("revisit_gap_days"), field_name="revisit gap")
    messages.extend(gap_messages)
    payload = {
        "feeder_id": _text(get("feeder_id")),
        "name": name,
        "manual_multiplier": multiplier,
        "monthly_min": monthly_min,
        "monthly_max": monthly_max,
        "revisit_gap_days": gap,
    }
    severity = SEVERITY_WARNING if messages else SEVERITY_OK
    return RowOutcome(row_number, dict(row), payload, severity, tuple(messages))


def judge_activity(row: dict[str, str], mapping: dict[str, str], row_number: int) -> RowOutcome:
    def get(field_name: str) -> str | None:
        column = mapping.get(field_name)
        return row.get(column) if column else None

    category = _text(get("category"))
    description_en = _text(get("description_en"))
    if category is None or description_en is None:
        missing = [
            name
            for name, value in (("category", category), ("English description", description_en))
            if value is None
        ]
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            (f"missing {', '.join(missing)}",),
        )
    messages: list[str] = []
    weight, weight_messages = coerce_float(get("weight"), field_name="weight")
    messages.extend(weight_messages)
    monthly_min, min_messages = coerce_int(get("monthly_min"), field_name="monthly minimum")
    messages.extend(min_messages)
    monthly_max, max_messages = coerce_int(get("monthly_max"), field_name="monthly maximum")
    messages.extend(max_messages)
    if monthly_min is not None and monthly_max is not None and monthly_min > monthly_max:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            (f"monthly minimum {monthly_min} is above maximum {monthly_max}",),
        )
    gap, gap_messages = coerce_int(get("repeat_gap_days"), field_name="repeat gap")
    messages.extend(gap_messages)
    payload = {
        "activity_id": _text(get("activity_id")),
        "category": category,
        "description_en": description_en,
        "description_kn": _text(get("description_kn")),
        "weight": weight,
        "monthly_min": monthly_min,
        "monthly_max": monthly_max,
        "repeat_gap_days": gap,
        "weekday_mask": _text(get("weekday_mask")),
    }
    severity = SEVERITY_WARNING if messages else SEVERITY_OK
    return RowOutcome(row_number, dict(row), payload, severity, tuple(messages))


def judge_general_holiday(
    row: dict[str, str], mapping: dict[str, str], row_number: int
) -> RowOutcome:
    def get(field_name: str) -> str | None:
        column = mapping.get(field_name)
        return row.get(column) if column else None

    parsed, date_messages = coerce_date(get("holiday_date"))
    name = _text(get("name"))
    if parsed is None:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            tuple(date_messages) or ("no holiday date was supplied",),
        )
    if name is None:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR, ("no holiday name was supplied",)
        )
    payload = {
        "holiday_date": parsed,
        "name": name,
        "description": _text(get("description")),
    }
    return RowOutcome(row_number, dict(row), payload, SEVERITY_OK, ())


JUDGES = {
    "dtc": judge_dtc,
    "area": judge_area,
    "feeder": judge_feeder,
    "activity": judge_activity,
    "general_holiday": judge_general_holiday,
}


@dataclass(frozen=True)
class DatasetVerdict:
    """The whole file, judged, before anything is written to canonical tables."""

    dataset: str
    outcomes: tuple[RowOutcome, ...]
    file_messages: tuple[str, ...] = field(default=())

    @property
    def error_count(self) -> int:
        return sum(1 for o in self.outcomes if o.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for o in self.outcomes if o.severity == SEVERITY_WARNING)

    @property
    def importable(self) -> tuple[RowOutcome, ...]:
        return tuple(o for o in self.outcomes if o.is_importable)

    @property
    def can_accept(self) -> bool:
        """A file is acceptable when no row is unusable and nothing blocks the file.

        Warnings do not block acceptance: a placeholder capacity or an unfindable
        feeder is a discrepancy for a person to review, not a reason to refuse the
        import. An error does block, because applying the rest while silently
        skipping a row is exactly the silent discard the requirements forbid.
        """

        return self.error_count == 0 and not self.file_messages and bool(self.importable)


def judge_dataset(
    dataset: str, headers: tuple[str, ...], rows: tuple[dict[str, str], ...]
) -> DatasetVerdict:
    spec = DATASETS.get(dataset)
    if spec is None:
        raise ValueError(f"unknown import dataset: {dataset}")

    missing = spec.missing_required(headers)
    if missing:
        return DatasetVerdict(
            dataset=dataset,
            outcomes=(),
            file_messages=(
                (
                    "the file has no column for "
                    + ", ".join(missing)
                    + f"; the {spec.label} import needs "
                    + ", ".join(
                        " or ".join(field.aliases[:3])
                        for field in spec.fields
                        if field.required
                    )
                ),
            ),
        )

    mapping = spec.header_map(headers)
    judge = JUDGES[dataset]
    outcomes = tuple(
        judge(row, mapping, index + 1) for index, row in enumerate(rows)
    )
    return DatasetVerdict(dataset=dataset, outcomes=outcomes)
