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

import math
import re
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

#: Auto-excluded source classes, from requirements §8: the original source's SCD records
#: are included and its SCF and SCE records are not. The class the office states in its own
#: `Class` column decides this, not the letters in front of an identifier: one office files
#: its DTCs as `SCD-12` and includes them, another files them as `SCE142` and includes
#: those too, and the tool reads nothing into a prefix.
EXCLUDED_SOURCE_CLASSES = ("SCF", "SCE")

#: A DTC identifier is the office's own code: a short letter prefix and a number, with the
#: prefix and the number optionally separated by a hyphen, an underscore, a slash or a
#: space. Which letters an office prefixes its transformers with is its filing convention
#: (`SCD-12` in the office this tool was built for, `SCE142` in the next one) and the tool
#: has no business dictating it. What is still refused is a cell that is not a code at all:
#: a bare number, a placeholder such as `#N/A`, or a name dragged into the wrong column.
_DTC_ID_PATTERN = re.compile(r"[A-Za-z]{1,10}[-_/ ]?\d{1,10}")

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
        # The date the DTC was commissioned. The office's own heading is spelled
        # "Date of commision", so that spelling is an alias in its own right rather
        # than a correction: the column is read as supplied.
        FieldSpec(
            "commissioned_on",
            (
                "date of commision",
                "date of commissioning",
                "date of commission",
                "commissioned on",
                "commissioning date",
                "commission date",
                "commision date",
            ),
            False,
        ),
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

#: The same accepted orders as bare fields, so a value whose fields are all present
#: can be told apart from one that is not a date at all. `31-09-2026` reads against no
#: format, yet it is not gibberish: it names a day September does not have, and an
#: operator told it is "not a recognisable date" goes looking for a wrong format
#: instead of a wrong day.
_DATE_SHAPES: tuple[tuple[re.Pattern[str], bool], ...] = (
    (re.compile(r"^(\d{1,2})[-/.]?(\d{1,2})[-/.]?(\d{4})$"), True),
    (re.compile(r"^(\d{4})[-/.]?(\d{1,2})[-/.]?(\d{1,2})$"), False),
)

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)


def _impossible_date_reason(day: int, month: int, year: int) -> str:
    """Why a day, month and year that were all supplied cannot be a date."""

    if not 1 <= month <= 12:
        return f"there is no month {month}"
    days = monthrange(year, month)[1]
    if not 1 <= day <= days:
        name = _MONTH_NAMES[month - 1]
        return f"{name} {year} has {days} days, so there is no {day:02d}"
    return "the value cannot be a date"


def coerce_date(
    value: str | None, *, field_name: str | None = None
) -> tuple[date | None, list[str]]:
    """Read a date, or say why it could not be read.

    Only formats whose day, month and year cannot be confused are accepted. An
    ambiguous value such as ``03/04/2026`` is refused rather than guessed, because a
    wrong reading here moves a visit.
    """

    text = _text(value)
    if text is None:
        return None, []
    if _marker(value):
        label = field_name or "the date"
        return None, [f"{label} is a placeholder ({text}), so no date was read"]
    for pattern in _DATE_FORMATS:
        try:
            from datetime import datetime

            return datetime.strptime(text, pattern).date(), []
        except ValueError:
            continue
    label = field_name or "the date"
    for shape, day_first in _DATE_SHAPES:
        match = shape.match(text)
        if match is None:
            continue
        first, second, third = (int(part) for part in match.groups())
        day, month, year = (first, second, third) if day_first else (third, second, first)
        return None, [
            f"{label} '{text}' is not a real calendar date: "
            f"{_impossible_date_reason(day, month, year)}"
        ]
    if field_name is not None:
        return None, [f"{field_name} '{text}' is not a recognisable date"]
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
    if not _DTC_ID_PATTERN.fullmatch(dtc_id):
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_ERROR,
            (
                f"DTC id '{dtc_id}' is not a code: an identifier is this office's own prefix "
                "and a number, such as SCD-12 or SCE142",
            ),
        )

    # The office's `Class` column decides what is excluded, and the letters in the
    # identifier are never read as a class: an office that files its transformers as
    # `SCE<number>` is naming its own code, not declaring the SCE class §8 excludes.
    upper_class = (raw_class or "").upper()
    if upper_class in EXCLUDED_SOURCE_CLASSES:
        return RowOutcome(
            row_number, dict(row), None, SEVERITY_WARNING,
            (
                (
                    f"{dtc_id} has source class {upper_class}; requirements §8 includes the SCD "
                    "records and excludes these, so the row is kept for review and not imported"
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

    # A DTC commissioned after a month cannot be visited in it, so the date is read
    # here rather than left in the raw record. A blank or unreadable cell is carried
    # as no date, which the schedule reads as "no restriction": refusing every DTC
    # whose date is missing would make an older master unusable.
    commissioned, commissioned_messages = coerce_date(
        get("commissioned_on"), field_name="the commissioning date"
    )
    messages.extend(commissioned_messages)

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
        # ISO text, or None when the master recorded no readable date.
        "commissioned_on": commissioned.isoformat() if commissioned else None,
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
            "commissioned_on": get("commissioned_on") or "",
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


# --- the activity contract ------------------------------------------------


@dataclass(frozen=True)
class ActivityFields:
    """An activity payload read into the shapes the contract talks about.

    A field the source left blank, marked as a placeholder, or wrote in terms that
    cannot be read as its type is `None` here, and the accompanying problems say
    which it was. Nothing is defaulted and no identifier is manufactured.
    """

    activity_id: str | None
    category: str | None
    description_en: str | None
    description_kn: str | None
    weight: float | None
    monthly_min: int | None
    monthly_max: int | None
    repeat_gap_days: int | None
    weekday_mask: str | None


ACTIVITY_TEXT_FIELDS = (
    ("activity_id", "activity id"),
    ("category", "category"),
    ("description_en", "English description"),
)

ACTIVITY_NUMBER_FIELDS = (
    ("weight", "weight", False),
    ("monthly_min", "monthly minimum", True),
    ("monthly_max", "monthly maximum", True),
    ("repeat_gap_days", "repeat gap", True),
)

#: The range this register can store a whole number in. It is the database's signed
#: 64-bit INTEGER range, not a scheduling limit: the contract judges what the catalogue
#: may say, and this refuses only a value that could not be written at all. It is
#: checked here rather than left to the INSERT, because a refusal from the contract
#: names the row and the field while a driver error arrives after the batch was opened.
SQLITE_INTEGER_MIN = -(2**63)
SQLITE_INTEGER_MAX = 2**63 - 1

#: A whole number written as text, read as an exact decimal rather than as a float.
#: `float` is the wrong parser for this field twice over: it rounds a long integer to
#: the nearest double, and it rounds a fractional value *towards* a whole one.


def _is_whole_decimal(number: Decimal) -> bool:
    """Whether the digits the source wrote sit exactly on an integer.

    Read from the decimal's own digits and exponent, never from `to_integral_value()`,
    which rounds and therefore cannot answer whether rounding was needed. A float has
    no such digits left to inspect; that is why the text is parsed again here instead
    of being read through one.
    """

    _sign, digits, exponent = number.as_tuple()
    if exponent >= 0:
        return True
    fraction = -exponent
    if fraction >= len(digits):
        return not any(digits)
    return not any(digits[-fraction:])


def _contract_text_whole(text: str, *, field_name: str) -> tuple[int | None, list[str]]:
    """A whole number written as text, read exactly, decimal and exponent forms too.

    `Decimal(text)` keeps the digits the source wrote. Nothing here is arithmetic, so
    the active decimal context cannot round what is read: the only operations are
    `as_tuple()` over the digits the parser stored and comparisons against the storage
    range, both exact. `"9007199254740993.0"` is that integer, `"2.5"` is not whole,
    and `"2.000...0001"` stays fractional instead of arriving as two.
    """

    if _marker(text):
        return None, [f"{field_name} is a placeholder ({text}), so no number was read"]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None, [f"{field_name} '{text}' is not a number"]
    if not number.is_finite() or not _is_whole_decimal(number):
        return None, [f"{field_name} '{text}' is not a whole number"]
    # The range is compared exactly and *before* any conversion. `int(Decimal("1e1000000000"))`
    # would try to build a number with a billion digits, which is not a refusal; a
    # comparison against the boundary costs nothing and answers the same question.
    if number < SQLITE_INTEGER_MIN or number > SQLITE_INTEGER_MAX:
        return None, [
            f"{field_name} {_given(text)} is outside the range this register stores whole "
            f"numbers in, {SQLITE_INTEGER_MIN} to {SQLITE_INTEGER_MAX}"
        ]
    return int(number), []


def _shown(value: object) -> str:
    """A supplied value as a message should quote it back."""

    return f"'{value}'" if isinstance(value, str) else repr(value)


def _given(value: object) -> str:
    """A number as a message quotes it, without printing four hundred digits."""

    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= 24 else f"{text[:20]}..."


def _contract_text(value: object) -> str | None:
    """Text as stored, or None. A number in a text column keeps its digits."""

    if value is None:
        return None
    if isinstance(value, str):
        return _text(value)
    return str(value).strip() or None


def _within_storage(value: int, field_name: str) -> tuple[int | None, list[str]]:
    """A whole number, refused when this database could not store it."""

    if SQLITE_INTEGER_MIN <= value <= SQLITE_INTEGER_MAX:
        return value, []
    return None, [
        f"{field_name} {_given(value)} is outside the range this register stores whole "
        f"numbers in, {SQLITE_INTEGER_MIN} to {SQLITE_INTEGER_MAX}"
    ]


def _contract_whole(value: object, *, field_name: str) -> tuple[int | None, list[str]]:
    """A whole number, read exactly, never through a float on the way.

    Converting an integer to a float to ask whether it is whole answers two questions
    badly. It answers the wrong one, because a float cannot hold every integer: above
    2**53 neighbours collapse onto the same value, so a minimum of 9007199254740993 and
    a maximum of 9007199254740992 compare as equal and the inversion goes unnoticed. And
    it answers it by changing the number, because the value returned is the rounded one.

    So an integer is taken as it stands. A float is a float the source really wrote, and
    is accepted only when it is finite and already whole - nothing here claims to recover
    precision the float lost before it arrived. Text is read as an exact decimal,
    whichever form the catalogue wrote it in: digits, a decimal point, or an exponent.
    """

    if isinstance(value, int):
        return _within_storage(value, field_name)
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None, [f"{field_name} {_shown(value)} is not a whole number"]
        return _within_storage(int(value), field_name)
    if isinstance(value, str):
        text = _text(value)
        if text is None:
            return None, []
        return _contract_text_whole(text, field_name=field_name)
    return None, [f"{field_name} {_shown(value)} is not a number"]


def _contract_share(value: object, *, field_name: str) -> tuple[float | None, list[str]]:
    """A share, read as a float, with an integer too large to be one refused here.

    `float(huge_int)` raises `OverflowError`, which is not a contract failure and would
    have escaped the judge and the acceptance guard as an unhandled error. A weight has
    to be a finite number regardless; this only makes sure that a value which cannot
    even be represented says so in the same vocabulary as the rest of the contract.
    """

    if isinstance(value, (int, float)):
        try:
            return float(value), []
        except OverflowError:
            return None, [
                f"{field_name} {_given(value)} is too large to be stored as a number"
            ]
    if isinstance(value, str):
        return coerce_float(value, field_name=field_name)
    return None, [f"{field_name} {_shown(value)} is not a number"]


def _contract_number(
    value: object, *, field_name: str, whole: bool
) -> tuple[float | int | None, list[str]]:
    """One number, read from a source cell or from an already-stored payload.

    The same rules apply to both: a blank stays blank, a placeholder is named as a
    placeholder, unreadable text is refused, and a value of the wrong shape is refused
    rather than coerced into something plausible. A boolean is refused by name, because
    `True` is an `int` in Python and would otherwise pass as a whole number one.
    """

    if value is None:
        return None, []
    if isinstance(value, bool):
        return None, [f"{field_name} {_shown(value)} is not a number"]
    if whole:
        return _contract_whole(value, field_name=field_name)
    return _contract_share(value, field_name=field_name)


def read_activity_fields(payload: Mapping[str, object]) -> tuple[ActivityFields, tuple[str, ...]]:
    """Read one activity payload and report every way it breaks the contract.

    This is the only place the activity contract is written down. The staging judge
    calls it on the values a source row supplies, and acceptance calls it on the
    values an already-staged row carries, so a batch staged under an earlier contract
    is held to exactly the rules a new upload is held to and the two cannot drift.

    The contract, in full: an activity identifier, a category and an English
    description; a finite weight greater than zero; a monthly minimum and maximum of
    zero or more, the minimum not above the maximum; and a repeat gap of zero or more
    days. A Kannada description is wanted but not required here, because its absence
    is a warning rather than a refusal and this function judges only what blocks an
    import.

    The function is pure. It reads no database, invents no value, translates nothing
    and repairs nothing it cannot read.
    """

    problems: list[str] = []

    missing = [
        label
        for name, label in ACTIVITY_TEXT_FIELDS
        if _contract_text(payload.get(name)) is None
    ]
    if missing:
        problems.append(f"missing {', '.join(missing)}")

    numbers: dict[str, float | int | None] = {}
    blank: list[str] = []
    for name, label, whole in ACTIVITY_NUMBER_FIELDS:
        number, messages = _contract_number(payload.get(name), field_name=label, whole=whole)
        numbers[name] = number
        problems.extend(messages)
        if number is None and not messages:
            blank.append(label)
    if blank:
        listed = blank[0] if len(blank) == 1 else f"{', '.join(blank[:-1])} or {blank[-1]}"
        problems.append(
            f"no {listed} was supplied; supply the approved catalogue value, because "
            "the tool does not choose a weight, a frequency or a repeat gap for an activity"
        )

    weight = numbers["weight"]
    if weight is not None and not math.isfinite(weight):
        problems.append(f"weight {_shown(payload.get('weight'))} is not a finite number")
    elif weight is not None and weight <= 0:
        problems.append(
            f"weight {weight:g} is not greater than zero, so it cannot express a share"
        )
    for name, label, _whole in ACTIVITY_NUMBER_FIELDS[1:]:
        value = numbers[name]
        if value is not None and value < 0:
            problems.append(f"{label} {value} is negative")
    minimum, maximum = numbers["monthly_min"], numbers["monthly_max"]
    if minimum is not None and maximum is not None and minimum > maximum:
        problems.append(f"monthly minimum {minimum} is above maximum {maximum}")

    fields = ActivityFields(
        activity_id=_contract_text(payload.get("activity_id")),
        category=_contract_text(payload.get("category")),
        description_en=_contract_text(payload.get("description_en")),
        description_kn=_contract_text(payload.get("description_kn")),
        weight=numbers["weight"],
        monthly_min=numbers["monthly_min"],
        monthly_max=numbers["monthly_max"],
        repeat_gap_days=numbers["repeat_gap_days"],
        weekday_mask=_contract_text(payload.get("weekday_mask")),
    )
    return fields, tuple(problems)


def judge_activity(row: dict[str, str], mapping: dict[str, str], row_number: int) -> RowOutcome:
    """Judge one catalogue row against the contract in `read_activity_fields`.

    The rules themselves live in one function, because acceptance has to apply the
    same ones to rows staged before this judge existed. What belongs here is only the
    mapping from this file's columns onto those fields, and the one thing the
    contract calls a warning: a missing Kannada description. Nothing is translated,
    no identifier is manufactured, and no number is completed.
    """

    def get(field_name: str) -> str | None:
        column = mapping.get(field_name)
        return row.get(column) if column else None

    supplied = {
        "activity_id": get("activity_id"),
        "category": get("category"),
        "description_en": get("description_en"),
        "description_kn": get("description_kn"),
        "weight": get("weight"),
        "monthly_min": get("monthly_min"),
        "monthly_max": get("monthly_max"),
        "repeat_gap_days": get("repeat_gap_days"),
        "weekday_mask": get("weekday_mask"),
    }
    fields, problems = read_activity_fields(supplied)
    if problems:
        return RowOutcome(row_number, dict(row), None, SEVERITY_ERROR, problems)

    warnings: list[str] = []
    if fields.description_kn is None:
        warnings.append(
            "no Kannada description was supplied; the activity is imported with its "
            "English text alone and nothing is translated here"
        )
    payload = {
        "activity_id": fields.activity_id,
        "category": fields.category,
        "description_en": fields.description_en,
        "description_kn": fields.description_kn,
        "weight": fields.weight,
        "monthly_min": fields.monthly_min,
        "monthly_max": fields.monthly_max,
        "repeat_gap_days": fields.repeat_gap_days,
        "weekday_mask": fields.weekday_mask,
    }
    severity = SEVERITY_WARNING if warnings else SEVERITY_OK
    return RowOutcome(row_number, dict(row), payload, severity, tuple(warnings))


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
