"""An issued month as a downloadable workbook.

The workbook is a rendering of :class:`~dailyworkreg.application.export.RegisterDocument`,
which is itself only the stored finalisation. Nothing here reads the database, consults a
master, or calculates a figure: a cell either holds something the record stored or it is a
heading.

Two sheets, because the record has two kinds of thing in it and neither belongs on the
other's page:

*   **Register** — the office's own register. One row per stored calendar date, in the
    stored order, in exactly three columns: the serial number, the date, and one Activity
    cell carrying everything recorded against that date, comma-separated, exactly as the
    printed page shows it. The cell text comes from ``DocumentDay.activity_text``, which is
    the same value the print page renders, so the two documents cannot disagree.
*   **Metadata** — how this document identifies itself and what was recorded alongside the
    dates: the issued version and the revision it closed, when, the content fingerprint,
    the master version it was measured against, the operator's note, the locked dates, the
    findings that were acknowledged, the master readiness measured at finalisation, the
    month's recorded obligations, the stored evidence, and how many of its visits were
    committed before generation rather than allocated.

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

REGISTER_HEADERS = ("Sl No.", "Date", "Activity")

#: Widths, in the same order as the headers above. The Activity column takes the room the
#: old ten-column register spent on facts that now live on the Metadata sheet.
REGISTER_WIDTHS = (8, 14, 104)

#: The row the column headings sit on, and the first row of data.
REGISTER_HEADER_ROW = 4
REGISTER_FIRST_ROW = REGISTER_HEADER_ROW + 1

#: The sheet names, in the order they appear. The register is the operator's document and
#: comes first; the identification sheet is secondary and never mixed into it.
REGISTER_SHEET = "Register"
METADATA_SHEET = "Metadata"


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


def pdf_filename(document: RegisterDocument) -> str:
    """The same safe name for the printed version of one issued month."""

    return xlsx_filename(document).removesuffix(".xlsx") + ".pdf"


def print_range_filename(documents: list[RegisterDocument]) -> str:
    """The name for one document holding several issued months in reading order.

    Named after the months it holds rather than after the register, so a file that
    arrives in a downloads folder says which run of registers it is without being
    opened. One month reads as itself rather than as a range of one.

    Args:
        documents: The months the document holds, in the order they are printed.

    Returns:
        A filename safe on Windows: no character outside a month's digits and
        hyphens reaches it, for the same reason :func:`xlsx_filename` is guarded.
    """

    months = [_safe_month(document) for document in documents]
    if len(months) == 1:
        return f"daily-dairy-register-{months[0]}.pdf"
    return f"daily-dairy-register-{months[0]}-to-{months[-1]}.pdf"


def _safe_month(document: RegisterDocument) -> str:
    """One document's month reduced to the characters a filename may carry."""

    return "".join(
        character
        for character in document.month
        if character.isdigit() or character == "-"
    )


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
    register.title = REGISTER_SHEET
    thin = Side(style="thin", color="d0d0cb")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_font = Font(bold=True)

    _title(register, "DAILY DAIRY REGISTER", document.month_label)

    header_row = REGISTER_HEADER_ROW
    for index, name in enumerate(REGISTER_HEADERS, start=1):
        cell = register.cell(row=header_row, column=index, value=name)
        cell.font = head_font
        cell.border = border
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        register.column_dimensions[cell.column_letter].width = REGISTER_WIDTHS[index - 1]

    row_index = header_row
    for serial, day in enumerate(document.days, start=1):
        row_index += 1
        values: tuple[Any, ...] = (
            serial,
            _date_cell(day.day),
            day.activity_text,
        )
        for index, value in enumerate(values, start=1):
            cell = register.cell(row=row_index, column=index)
            _put(cell, value)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if index == 1:
                cell.alignment = Alignment(vertical="top", horizontal="right")
            if isinstance(value, date):
                # Displayed exactly as the stored day reads, while staying a date cell so
                # the column sorts and filters as dates rather than as text.
                cell.number_format = "yyyy-mm-dd"
    if row_index > header_row:
        register.freeze_panes = register.cell(row=header_row + 1, column=1).coordinate
    register.print_title_rows = f"{header_row}:{header_row}"
    register.page_setup.orientation = "portrait"
    register.page_setup.paperSize = register.PAPERSIZE_A4
    register.sheet_properties.pageSetUpPr.fitToPage = True
    register.page_setup.fitToWidth = 1
    register.page_setup.fitToHeight = 0

    metadata = workbook.create_sheet(METADATA_SHEET)
    _metadata_sheet(metadata, document, head_font, border)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _metadata_sheet(sheet, document: RegisterDocument, head_font, border) -> None:
    """Everything about this issued version that is not a date of the register."""

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
        "Field visits recorded",
        ("Provenance", "Visits"),
        [(label, count) for label, count in document.recorded_provenance],
        head_font,
        border,
        empty="The month recorded no field visits.",
    )
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


def _title(sheet, line_one: str, month_label: str) -> None:
    """The heading the register is filed under, centred like a headed office page."""

    from openpyxl.styles import Alignment, Font

    cell = sheet.cell(row=1, column=1, value=line_one.upper())
    cell.font = Font(bold=True, size=15)
    cell.alignment = Alignment(horizontal="center")
    month = sheet.cell(row=2, column=1, value=month_label.upper())
    month.font = Font(bold=True, size=12)
    month.alignment = Alignment(horizontal="center")
    # The issued version names itself on the Metadata sheet rather than under the heading:
    # the operator's page carries the register, not the audit trail.

    # The heading spans the three columns so it sits in the middle of the page rather than
    # in the first column only.
    for row in (1, 2):
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)


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
