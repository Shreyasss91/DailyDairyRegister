"""An issued month as a downloadable workbook.

The workbook is a rendering of :class:`~dailyworkreg.application.export.RegisterDocument`,
which is itself only the stored finalisation. Nothing here reads the database, consults a
master, or calculates a figure: a cell either holds something the record stored or it is a
heading.

Two sheets, because the record has two kinds of thing in it and neither belongs on the
other's page:

*   **Register** — one row per stored calendar date, in the stored order, with that date's
    classification, its reasons, its visits, its activities and its warnings. Multiple
    visits or activities on one date are kept, one per line inside the cell.
*   **Issued version** — how this document identifies itself and what was recorded
    alongside the dates: the issued version and the revision it closed, when, the content
    fingerprint, the master version it was measured against, the operator's note, the
    locked dates, the findings that were acknowledged, the master readiness measured at
    finalisation, and the month's recorded obligations.

Splitting the record this way keeps every recorded value without printing any of them
twice: the dates appear only on the first sheet, and the month's own figures only on the
second.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

from ..application.export import RegisterDocument

#: openpyxl is imported where it is needed rather than at module scope, so a build without
#: it still serves the pages and the print view.

class ExportUnavailable(RuntimeError):
    """A workbook could not be written because the writer is not installed."""


XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

#: Characters that make a spreadsheet read a cell as a formula. A name imported from a
#: source file, or a note an operator typed, is data and must stay data.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")

REGISTER_HEADERS = (
    "Date",
    "Weekday",
    "Day kind",
    "Working day",
    "Reasons (English)",
    "Reasons (Kannada)",
    "Visits",
    "Activities and entries",
    "Warnings",
    "Locked",
)

#: Widths, in the same order as the headers above.
REGISTER_WIDTHS = (12, 11, 18, 12, 26, 26, 34, 44, 34, 8)


def xlsx_filename(document: RegisterDocument) -> str:
    """A safe, version-specific filename.

    Only ASCII letters, digits and hyphens reach the header, so nothing in a register or a
    month can steer the path. The sequence makes two versions of one month distinct.
    """

    month = "".join(
        character
        for character in document.month
        if character.isdigit() or character == "-"
    )
    return f"daily-dairy-register-{month}-v{document.sequence}.xlsx"


def workbook_bytes(document: RegisterDocument) -> bytes:
    """The workbook for one issued version, as bytes.

    ``openpyxl`` is imported here rather than at module scope so that a build without it
    still serves the pages and the print view, and says plainly what is missing instead of
    failing to import the whole application.
    """

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError as error:  # pragma: no cover - depends on the installation
        raise ExportUnavailable(
            "writing a spreadsheet needs the openpyxl package, which is not installed;"
            " the print view can still be used to save the register as a PDF"
        ) from error

    workbook = Workbook()
    # The document's own timestamps, taken from the record rather than from the clock, so
    # the same issued version produces the same file every time it is downloaded.
    workbook.properties.creator = "DailyDairyRegister"
    workbook.properties.title = f"Daily Dairy Register — {document.month_label}"
    workbook.properties.subject = document.version_label
    workbook.properties.description = (
        f"Issued version {document.sequence} of {document.month} for register "
        f"{document.register_id}, as approved. Content fingerprint {document.fingerprint}."
    )
    issued_at = _timestamp(document.finalised_at)
    if issued_at is not None:
        workbook.properties.created = issued_at
        workbook.properties.modified = issued_at
    register = workbook.active
    register.title = "Register"
    thin = Side(style="thin", color="d0d0cb")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_font = Font(bold=True)

    _title(register, "Daily Dairy Register", document.month_label, document.version_label)
    _subtitle(
        register,
        f"Register {document.register_id}  ·  revision {document.revision_number}"
        f"  ·  closed {document.finalised_at}  ·  {document.standing_label}"
        f"  ·  fingerprint {document.fingerprint}",
    )

    header_row = 5
    for index, name in enumerate(REGISTER_HEADERS, start=1):
        cell = register.cell(row=header_row, column=index, value=name)
        cell.font = head_font
        cell.border = border
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        register.column_dimensions[cell.column_letter].width = REGISTER_WIDTHS[index - 1]

    row_index = header_row
    for day in document.days:
        row_index += 1
        values: tuple[Any, ...] = (
            _date_cell(day.day),
            day.weekday,
            day.kind_label,
            "Yes" if day.working else "No",
            "\n".join(day.reasons_en),
            "\n".join(day.reasons_kn),
            "\n".join(
                f"{visit.name_en} ({visit.asset_id}, {visit.provenance_label})".strip()
                for visit in day.visits
            ),
            "\n".join(
                f"{entry.kind_label}: {entry.title_en}".strip()
                for entry in day.entries
            ),
            "\n".join(f"{constraint}: {detail}" for constraint, detail in day.warnings),
            "Yes" if day.locked else "",
        )
        for index, value in enumerate(values, start=1):
            cell = register.cell(row=row_index, column=index)
            _put(cell, value)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(value, date):
                # Displayed exactly as the stored day reads, while staying a date cell so
                # the column sorts and filters as dates rather than as text.
                cell.number_format = "yyyy-mm-dd"
    if row_index > header_row:
        register.freeze_panes = register.cell(row=header_row + 1, column=1).coordinate
    register.print_title_rows = f"{header_row}:{header_row}"
    register.page_setup.orientation = "landscape"
    register.page_setup.paperSize = register.PAPERSIZE_A4
    register.sheet_properties.pageSetUpPr.fitToPage = True
    register.page_setup.fitToWidth = 1
    register.page_setup.fitToHeight = 0

    issued = workbook.create_sheet("Issued version")
    _issued_sheet(issued, document, head_font, border)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _issued_sheet(sheet, document: RegisterDocument, head_font, border) -> None:
    from openpyxl.styles import Alignment, Font

    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions["B"].width = 74
    sheet.column_dimensions["C"].width = 30
    sheet.column_dimensions["D"].width = 30

    sheet.cell(row=1, column=1, value="Issued version identification").font = Font(
        bold=True, size=14
    )
    rows: tuple[tuple[str, Any], ...] = (
        ("Month", document.month_label),
        ("Month key", document.month),
        ("Register", document.register_id),
        ("Issued version", document.sequence),
        ("Revision closed", document.revision_number),
        ("Finalisation id", document.finalisation_id),
        ("Closed at", document.finalised_at),
        ("Standing", document.standing_label),
        ("Content fingerprint", document.fingerprint),
        ("Master version at finalisation", document.master_version),
        ("Corrects revision", document.correction_of_run_id or ""),
        ("Correction of", document.parent_finalisation_id or ""),
        ("Month status", document.status),
        ("Allocator status", document.allocator_status),
        ("Locked dates", ", ".join(document.locked_dates)),
        ("Note", document.note or ""),
    )
    row = 3
    for label, value in rows:
        sheet.cell(row=row, column=1, value=label).font = head_font
        cell = sheet.cell(row=row, column=2)
        _put(cell, value)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        row += 1

    row += 1
    row = _block(
        sheet,
        row,
        "Acknowledged findings",
        ("Constraint", "Detail"),
        [(item.get("constraint") or item.get("kind") or "", item.get("detail") or "")
         for item in document.acknowledged],
        head_font,
        border,
        empty="Nothing was acknowledged when this version was issued.",
    )
    row = _block(
        sheet,
        row,
        "Master readiness measured at finalisation",
        ("Key", "Label", "Detail", "Satisfied"),
        [
            (
                item.get("key") or "",
                item.get("label") or "",
                item.get("detail") or "",
                "Yes" if item.get("satisfied") else "No",
            )
            for item in document.readiness
        ],
        head_font,
        border,
        empty="No readiness was recorded.",
    )
    row = _block(
        sheet,
        row,
        "Recorded obligations",
        ("Asset", "Visits recorded as owed"),
        [(asset_id, count) for asset_id, count in document.obligations],
        head_font,
        border,
        empty="The month recorded no obligations.",
    )
    row = _block(
        sheet,
        row,
        "Declared obligations",
        ("Asset", "Visits declared"),
        [(asset_id, count) for asset_id, count in document.declared_obligations],
        head_font,
        border,
        empty="The month declared no obligations.",
    )
    _block(
        sheet,
        row,
        "Stored evidence",
        ("Finding",),
        [(item,) for item in document.evidence],
        head_font,
        border,
        empty="No evidence was recorded.",
    )


def _block(
    sheet,
    row: int,
    title: str,
    headers: tuple[str, ...],
    rows: list[tuple[Any, ...]],
    head_font,
    border,
    *,
    empty: str,
) -> int:
    from openpyxl.styles import Alignment, Font

    sheet.cell(row=row, column=1, value=title).font = Font(bold=True)
    row += 1
    for index, name in enumerate(headers, start=1):
        cell = sheet.cell(row=row, column=index, value=name)
        cell.font = head_font
        cell.border = border
    row += 1
    if not rows:
        cell = sheet.cell(row=row, column=1, value=empty)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        return row + 2
    for values in rows:
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=index)
            _put(cell, value)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        row += 1
    return row + 1


def _title(sheet, line_one: str, month_label: str, version_label: str) -> None:
    from openpyxl.styles import Font

    cell = sheet.cell(row=1, column=1, value=f"{line_one} — {month_label}")
    cell.font = Font(bold=True, size=15)


def _subtitle(sheet, text: str) -> None:
    from openpyxl.styles import Alignment

    cell = sheet.cell(row=2, column=1, value=text)
    cell.alignment = Alignment(vertical="top", wrap_text=True)


def _timestamp(text: str):
    """The record's own timestamp, or None if it cannot be read as one."""

    from datetime import datetime

    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _date_cell(day: str) -> Any:
    """A stored date as a real date cell, or as the stored text if it is not one."""

    try:
        return date.fromisoformat(day)
    except ValueError:
        return day


def _put(cell, value: Any) -> None:
    """Write a value so that stored text can never become a formula.

    ``openpyxl`` marks a string beginning with ``=`` as a formula, and a spreadsheet client
    may treat a leading ``+``, ``-`` or ``@`` the same way. Every string that could be read
    as one is written as text instead, so an activity title or a holiday name imported from
    a source file stays a name.
    """

    cell.value = value
    if isinstance(value, str) and value.startswith(_FORMULA_LEADS):
        cell.data_type = "s"
