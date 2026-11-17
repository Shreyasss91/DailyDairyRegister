"""Tabular source readers for the import adapters.

The utility's source files are spreadsheets, so this module reads CSV, TSV and
XLSX. It does one thing: turn a file into ordered header-to-text mappings, keeping
every value exactly as supplied.

Keeping values verbatim is the point. A blank feeder, a ``#N/A`` kVA and a stray
trailing space are all facts about what was sent, and a reader that trimmed,
coerced or defaulted them would destroy the discrepancy the office is being asked
to reconcile. Coercion happens later, deliberately, and its failures are reported
rather than silently repaired.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

#: Recognised by extension. Anything else is refused rather than guessed at.
SUPPORTED_SUFFIXES = (".csv", ".tsv", ".xlsx", ".xlsm")


class SourceFormatError(ValueError):
    """The file cannot be read as a table of named columns."""

    def __init__(self, constraint: str, detail: str):
        self.constraint = constraint
        self.detail = detail
        super().__init__(f"{constraint}: {detail}")


C_UNSUPPORTED_FORMAT = "source_format_unsupported"
C_EMPTY_SOURCE = "source_file_empty"
C_NO_HEADER = "source_header_missing"
C_DUPLICATE_HEADER = "source_header_duplicate"
C_UNMAPPED_SOURCE = "source_file_unmapped"


@dataclass(frozen=True)
class TabularSource:
    """A source file read into headers and rows, with its provenance."""

    filename: str
    sha256: str
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _headers_from(first_row: list[str]) -> tuple[str, ...]:
    headers = [header.strip() for header in first_row]
    if not any(headers):
        raise SourceFormatError(C_NO_HEADER, "the first row carries no column names")
    seen: set[str] = set()
    duplicates: list[str] = []
    for header in headers:
        if header and header in seen:
            duplicates.append(header)
        seen.add(header)
    if duplicates:
        raise SourceFormatError(
            C_DUPLICATE_HEADER,
            "the source names these columns more than once: "
            + ", ".join(sorted(set(duplicates))),
        )
    return tuple(headers)


def _read_delimited(text: str, delimiter: str) -> tuple[tuple[str, ...], list[list[str]]]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        first = next(reader)
    except StopIteration:
        raise SourceFormatError(C_EMPTY_SOURCE, "the file has no rows") from None
    headers = _headers_from(list(first))
    body = list(reader)
    return headers, body


def _read_xlsx(payload: bytes) -> tuple[tuple[str, ...], list[list[str]]]:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - openpyxl is present in this environment
        raise SourceFormatError(
            C_UNSUPPORTED_FORMAT,
            (
                "reading a .xlsx source needs the openpyxl package, which is not "
                "installed; save the sheet as CSV instead or install openpyxl"
            ),
        ) from None

    workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    iterator = sheet.iter_rows(values_only=True)
    try:
        first = next(iterator)
    except StopIteration:
        raise SourceFormatError(C_EMPTY_SOURCE, "the workbook's first sheet is empty") from None
    headers = _headers_from(["" if cell is None else str(cell) for cell in first])
    body: list[list[str]] = []
    for row in iterator:
        body.append(["" if cell is None else str(cell) for cell in row])
    return headers, body


def _rows_from(headers: tuple[str, ...], body: list[list[str]]) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for raw in body:
        if not any((cell or "").strip() for cell in raw):
            # A wholly blank line is not a record. It carries no values to
            # preserve, so skipping it loses nothing.
            continue
        record: dict[str, str] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            record[header] = raw[index] if index < len(raw) else ""
        rows.append(record)
    return tuple(rows)


def read_source_bytes(filename: str, payload: bytes) -> TabularSource:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise SourceFormatError(
            C_UNSUPPORTED_FORMAT,
            (
                f"{suffix or 'a file with no extension'} is not a supported source "
                f"format; supported formats are {', '.join(SUPPORTED_SUFFIXES)}"
            ),
        )
    if suffix in (".xlsx", ".xlsm"):
        headers, body = _read_xlsx(payload)
    else:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = payload.decode("cp1252", errors="replace")
        headers, body = _read_delimited(text, "\t" if suffix == ".tsv" else ",")
    if not body:
        raise SourceFormatError(
            C_EMPTY_SOURCE, "the file has column names but no records under them"
        )
    return TabularSource(
        filename=Path(filename).name,
        sha256=_digest(payload),
        headers=headers,
        rows=_rows_from(headers, body),
    )


def read_source_file(path: Path) -> TabularSource:
    return read_source_bytes(path.name, path.read_bytes())
