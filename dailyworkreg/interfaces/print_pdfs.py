"""Prepare a month-per-file export for the office as one printable document.

Purpose
-------
Every issued month exports as its own PDF, and each of those files ends with an
*Issued version identification* page: the issued version, its standing, the
revision closed, the finalisation id, the content fingerprint and the evidence.
That page belongs in the archive and it does not belong in the file that goes to
the press, because nineteen of them turn a register into an appendix of hashes.

This tool does the two things that turn such a folder into one printable
document:

1. it drops the **last page** of every PDF in the folder, which is that
   identification page, and
2. it concatenates what is left, in file-name order, into one PDF.

File-name order is chronological order for the names this repository exports -
``daily-dairy-register-YYYY-MM-vN.pdf`` - so April 2025 leads and October 2026
follows without anybody sorting anything by hand.

The source files are only ever read. Nothing is renamed, moved or rewritten in
the folder they came from, so the tool can be run again over the same export
after a month is reissued.

Usage
-----
The source folder is the only input. Everything else is derived from it.

    python -m dailyworkreg.interfaces.print_pdfs "C:\\Users\\me\\Downloads\\print\\before"

or, once the package is installed, through the declared console command:

    dailyworkreg-print "C:\\Users\\me\\Downloads\\print\\before"

The destination is derived, never asked for: a folder named ``after`` beside the
source folder, created when it does not exist. The example above writes

    C:\\Users\\me\\Downloads\\print\\after\\daily-dairy-register-2025-04-to-2026-10.pdf

Point it at a folder already named ``after`` - which is what re-running over the
previous output looks like - and the document goes to ``after-merged`` beside it
instead, so a folder is never both what is read and what is written.

The output name is read from the first and the last file it merged, so the range
is visible in the file name without opening it. A folder whose names do not
carry a month falls back to ``merged-registers.pdf``.

What it reports, and what it refuses
------------------------------------
One line per source file with its page count before and after, then a summary of
files read, pages dropped, pages merged and the file written.

A PDF of a single page is **skipped and named**, never merged as an empty file:
dropping its last page would leave nothing of that month, and a silently missing
month reads as a month that has no register. A folder with no PDF in it is
refused with a sentence saying so. The command exits 0 when at least one PDF was
merged, 1 when none was and 2 for a missing or unreadable folder.

Why the page count is trusted rather than parsed
------------------------------------------------
The identification page is not recognised by reading its text. A month whose
wording differs would then be merged with its hashes still attached, and the
mistake would be discovered at the press. It is the last page by construction -
``application.export`` starts it on a fresh page after the final date row - so
position is the contract this tool relies on. The two page operations themselves
live in ``infrastructure/rendering/pdf.py``, shared with the print document the
application serves from ``/api/registers/{id}/exports/print.pdf``, so the command
and the button cannot disagree about which page is left out.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..infrastructure import rendering
from ..infrastructure.rendering.pdf import RenderError

# The exported names carry the month, and that is the only ordering information
# this tool has. It is also what makes the output file name self-describing.
_MONTH_IN_NAME = re.compile(r"(\d{4}-\d{2})")

# Where the printable document goes: one level down from the folder it read, so
# an operator who exported into ...\print\before finds it in ...\print\after.
DESTINATION_FOLDER_NAME = "after"

# Used instead when the folder being read is itself named "after", which is what
# a re-run over the previous output looks like. Reading and writing one folder
# would merge the document into itself, so the output moves beside it instead.
ALTERNATE_DESTINATION_FOLDER_NAME = "after-merged"

FALLBACK_OUTPUT_NAME = "merged-registers.pdf"

MISSING_ENGINE_SENTENCE = (
    "Writing and joining PDFs needs the 'pypdf' package, which this installation does not "
    "have. Install it with: python -m pip install pypdf"
)
@dataclass
class SkippedFile:
    """A source PDF that could not contribute a page, and why."""

    name: str
    reason: str


@dataclass
class PrintResult:
    """What one run did, so a caller can report it without reading stdout."""

    output_path: Path | None = None
    pages_before: int = 0
    pages_merged: int = 0
    files_merged: int = 0
    skipped: list[SkippedFile] = field(default_factory=list)

    @property
    def pages_dropped(self) -> int:
        """Identification pages left out, one per merged file by construction."""
        return self.pages_before - self.pages_merged


def destination_for(source: Path) -> Path:
    """The folder the printable document is written to.

    Args:
        source: The folder the PDFs were read from.

    Returns:
        The ``after`` folder beside it. Derived rather than asked for, so the
        one input the operator gives is enough. A source that is itself named
        ``after`` gets :data:`ALTERNATE_DESTINATION_FOLDER_NAME`, so a folder is
        never both the input and the output.
    """
    destination = source.parent / DESTINATION_FOLDER_NAME
    if destination == source:
        return source.parent / ALTERNATE_DESTINATION_FOLDER_NAME
    return destination


def output_name_for(sources: list[Path]) -> str:
    """Name the printable document after the months it holds.

    Args:
        sources: The files to be merged, already in order.

    Returns:
        ``daily-dairy-register-<first>-to-<last>.pdf`` when the names carry
        months, and :data:`FALLBACK_OUTPUT_NAME` otherwise. A single month reads
        as itself rather than as a range of one.
    """
    months = [match.group(1) for match in (_MONTH_IN_NAME.search(f.name) for f in sources) if match]
    if not months:
        return FALLBACK_OUTPUT_NAME
    if months[0] == months[-1]:
        return f"daily-dairy-register-{months[0]}.pdf"
    return f"daily-dairy-register-{months[0]}-to-{months[-1]}.pdf"


def strip_and_merge(source: Path, report=print) -> PrintResult:
    """Drop the last page of every PDF in ``source`` and merge the rest.

    Args:
        source: The folder holding one PDF per month.
        report: The sink for progress lines. ``print`` by default; a test passes
            a collector so it can assert on the messages.

    Returns:
        A :class:`PrintResult` naming the file written and the pages counted.

    Raises:
        FileNotFoundError: The folder does not exist.
        NotADirectoryError: The path given is a file.
        RenderError: ``pypdf`` is not installed, or a file holds no page besides
            its identification page.
    """
    if not source.exists():
        raise FileNotFoundError(f"There is no folder at {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"{source} is a file, not a folder")

    destination = destination_for(source)

    # destination_for() already keeps the output out of the folder being read,
    # so no file written by an earlier run can be mistaken for a month.
    sources = sorted(source.glob("*.pdf"))
    if not sources:
        raise FileNotFoundError(f"There is no PDF to merge in {source}")

    result = PrintResult()
    kept: list[bytes] = []

    for path in sources:
        body = path.read_bytes()
        page_count = rendering.count_pages(body)

        # Answered from the page count, not from a failed call: a file whose only
        # page is its identification page would print as nothing of that month, so
        # it is named here instead of being merged as an empty contribution.
        if page_count <= rendering.identification_page_count():
            reason = "only its identification page, so nothing of that month would print"
            result.skipped.append(SkippedFile(name=path.name, reason=reason))
            report(f"SKIP  {path.name}: {reason}")
            continue

        result.pages_before += page_count
        kept.append(rendering.without_identification_page(body))
        result.files_merged += 1
        report(f"      {path.name}: {page_count} -> {page_count - 1} page(s)")

    if not kept:
        raise FileNotFoundError(
            f"No PDF in {source} has a page left once its last one is dropped"
        )

    destination.mkdir(parents=True, exist_ok=True)
    result.output_path = destination / output_name_for(sources)
    joined = rendering.join_pdfs(kept)
    result.pages_merged = rendering.count_pages(joined)

    result.output_path.write_bytes(joined)

    return result


def main(argv: list[str] | None = None) -> int:
    """Run the tool from the command line.

    Args:
        argv: Arguments without the program name. ``None`` reads ``sys.argv``.

    Returns:
        The process exit code: 0 when a document was written, 1 when nothing
        could be merged, 2 when the folder is missing or is not a folder.
    """
    parser = argparse.ArgumentParser(
        prog="dailyworkreg-print",
        description=(
            "Join the month PDFs of one export into a single printable document, "
            "leaving out each file's last page, which is its issued-version "
            "identification page."
        ),
        epilog=(
            "The source folder is the only input. The document is written to an "
            "'after' folder beside it, named after the months it holds."
        ),
    )
    parser.add_argument(
        "source",
        help="folder holding one PDF per issued month, for example ...\\print\\before",
    )
    arguments = parser.parse_args(argv)

    source = Path(arguments.source).expanduser()
    try:
        result = strip_and_merge(source)
    except (FileNotFoundError, NotADirectoryError) as error:
        print(f"Nothing was written. {error}", file=sys.stderr)
        return 2
    except RenderError as error:
        print(f"Nothing was written. {error}", file=sys.stderr)
        return 1

    print(
        f"\nMerged {result.files_merged} file(s): "
        f"{result.pages_before} page(s) read, "
        f"{result.pages_dropped} identification page(s) left out, "
        f"{result.pages_merged} page(s) written."
    )
    if result.skipped:
        print(
            f"{len(result.skipped)} file(s) were skipped and are named above. "
            "This document does not hold them."
        )
    print(f"Written to {result.output_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
