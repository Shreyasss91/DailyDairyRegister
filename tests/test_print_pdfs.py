"""Preparing one printable document from a folder of month PDFs.

The tool exists so the office does not print nineteen issued-version
identification pages along with the register. So the contracts pinned here are
the four that decide whether the file it writes is the register or an archive of
hashes: every month reaches the document in name order, each month's last page
is the only page left out, nothing is written back into the folder that was read,
and a month that cannot contribute a page is named instead of being silently
missing from the document.

The fixture PDFs are built here rather than committed: a PDF is a container, and
a container in the repository goes stale the day the export's page layout
changes. What matters is the page count and the order, and both are stated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dailyworkreg.interfaces import print_pdfs
from pypdf import PdfReader, PdfWriter

MONTHS = ["2025-04", "2025-05", "2025-06"]


def write_pdf(path: Path, pages: int) -> None:
    """Write a PDF of ``pages`` blank A4 pages, standing in for an exported month."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as handle:
        writer.write(handle)


def make_export(folder: Path, months: list[str], pages: int = 4) -> list[Path]:
    """Write one ``daily-dairy-register-<month>-v1.pdf`` per month."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for month in months:
        path = folder / f"daily-dairy-register-{month}-v1.pdf"
        write_pdf(path, pages)
        written.append(path)
    return written


def test_every_month_reaches_the_document_less_its_last_page(tmp_path):
    source = tmp_path / "before"
    make_export(source, MONTHS)

    result = print_pdfs.strip_and_merge(source, report=lambda _line: None)

    assert result.files_merged == 3
    assert result.pages_before == 12
    assert result.pages_dropped == 3
    assert result.pages_merged == 9
    assert len(PdfReader(result.output_path).pages) == 9


def test_the_document_is_written_to_the_after_folder_beside_the_export(tmp_path):
    source = tmp_path / "print" / "before"
    make_export(source, MONTHS)

    result = print_pdfs.strip_and_merge(source, report=lambda _line: None)

    assert result.output_path == source.parent / "after" / "daily-dairy-register-2025-04-to-2025-06.pdf"
    assert result.output_path.is_file()


def test_the_output_name_states_the_range_it_holds(tmp_path):
    source = tmp_path / "before"
    make_export(source, MONTHS)

    assert print_pdfs.output_name_for(make_export(source, MONTHS)) == (
        "daily-dairy-register-2025-04-to-2025-06.pdf"
    )


def test_a_single_month_reads_as_itself_rather_than_as_a_range(tmp_path):
    source = tmp_path / "before"
    written = make_export(source, ["2026-01"])

    assert print_pdfs.output_name_for(written) == "daily-dairy-register-2026-01.pdf"


def test_a_names_without_a_month_falls_back_to_a_plain_name(tmp_path):
    source = tmp_path / "before"
    source.mkdir()
    write_pdf(source / "part one.pdf", 3)
    write_pdf(source / "part two.pdf", 3)

    assert print_pdfs.output_name_for(sorted(source.glob("*.pdf"))) == print_pdfs.FALLBACK_OUTPUT_NAME


def test_a_month_whose_last_page_is_its_only_page_is_named_not_dropped(tmp_path):
    source = tmp_path / "before"
    make_export(source, MONTHS)
    write_pdf(source / "daily-dairy-register-2026-02-v1.pdf", 1)
    lines: list[str] = []

    result = print_pdfs.strip_and_merge(source, report=lines.append)

    assert result.files_merged == 3
    assert [skip.name for skip in result.skipped] == ["daily-dairy-register-2026-02-v1.pdf"]
    assert any("SKIP" in line and "2026-02" in line for line in lines)
    assert len(PdfReader(result.output_path).pages) == 9


def test_an_empty_folder_is_refused_rather_than_written_empty(tmp_path):
    source = tmp_path / "before"
    source.mkdir()

    with pytest.raises(FileNotFoundError):
        print_pdfs.strip_and_merge(source, report=lambda _line: None)

    assert not (tmp_path / "after").exists()


def test_a_folder_holding_only_single_page_pdfs_writes_nothing(tmp_path):
    source = tmp_path / "before"
    source.mkdir()
    write_pdf(source / "daily-dairy-register-2025-04-v1.pdf", 1)

    with pytest.raises(FileNotFoundError):
        print_pdfs.strip_and_merge(source, report=lambda _line: None)

    assert not (tmp_path / "after").exists()


def test_a_missing_folder_says_so_and_writes_nothing(tmp_path):
    source = tmp_path / "nowhere"

    with pytest.raises(FileNotFoundError, match="no folder"):
        print_pdfs.strip_and_merge(source, report=lambda _line: None)


def test_a_file_given_instead_of_a_folder_is_refused(tmp_path):
    source = tmp_path / "before"
    make_export(source, ["2025-04"])

    with pytest.raises(NotADirectoryError):
        print_pdfs.strip_and_merge(source / "daily-dairy-register-2025-04-v1.pdf", report=lambda _line: None)


def test_the_export_it_reads_is_left_exactly_as_it_was(tmp_path):
    source = tmp_path / "before"
    written = make_export(source, MONTHS)
    before = {path.name: path.read_bytes() for path in written}

    print_pdfs.strip_and_merge(source, report=lambda _line: None)

    assert {path.name: path.read_bytes() for path in written} == before
    assert sorted(path.name for path in source.glob("*.pdf")) == sorted(before)


def test_reading_a_folder_named_after_writes_beside_it_not_into_it(tmp_path):
    source = tmp_path / "after"
    make_export(source, MONTHS)

    result = print_pdfs.strip_and_merge(source, report=lambda _line: None)

    assert result.output_path.parent == tmp_path / "after-merged"
    # The point of the rule: the document is not among the files it read, so a
    # second run cannot merge its own output into itself.
    assert result.output_path.parent != source


def test_a_second_run_over_one_export_writes_the_same_document(tmp_path):
    source = tmp_path / "before"
    make_export(source, MONTHS)

    first = print_pdfs.strip_and_merge(source, report=lambda _line: None)
    second = print_pdfs.strip_and_merge(source, report=lambda _line: None)

    assert first.output_path == second.output_path
    assert len(PdfReader(second.output_path).pages) == first.pages_merged


def test_the_command_takes_the_source_folder_and_writes_the_document(tmp_path, capsys):
    source = tmp_path / "before"
    make_export(source, MONTHS)

    assert print_pdfs.main([str(source)]) == 0

    printed = capsys.readouterr().out
    assert "Merged 3 file(s)" in printed
    assert "9 page(s) written" in printed
    assert str(tmp_path / "after") in printed


def test_the_command_reports_a_missing_folder_without_a_traceback(tmp_path, capsys):
    assert print_pdfs.main([str(tmp_path / "nowhere")]) == 2

    assert "Nothing was written" in capsys.readouterr().err
