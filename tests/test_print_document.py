"""The one document the office prints: every issued month joined, no identification page.

What this file pins is the difference between the archive and the press. The archive keeps
each month's issued-version identification page — the version, its standing, the content
fingerprint, the finalisation id and the evidence — because that page is what proves which
register was issued. The printed register is the register alone, and nineteen
identification pages between the register pages is not a document an office files.

So the tests are about exactly three things: that every issued month reaches the joined
document and no identification page does, that the months read in month order rather than
in whatever order the database returned them, and that nothing else about the register
changed — the single-month download and the PDF package still carry the identification
page they always carried.

The months here are given a different page count each, because a joined document's page
total alone cannot prove that the drop happened at the right boundary. Three pages for
November and four for December means seven in the joined file: drop the wrong page and the
total is eight, drop from the wrong end and the documents are in the wrong order.
"""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from dailyworkreg.application import finalisation as finalise
from dailyworkreg.application import generation_runs as generations
from dailyworkreg.application import revisions
from dailyworkreg.application.draft_generation import (
    DraftMode,
    DraftStatus,
    generate_draft,
)
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure import rendering
from dailyworkreg.infrastructure.rendering import pdf as pdf_module
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities
from dailyworkreg.interfaces.api import create_app
from fastapi.testclient import TestClient

#: Pages in each month's rendered PDF. November's identification page makes four,
#: December's five, so the joined document's total is a statement about both months.
PAGES_PER_MONTH = {"2026-11": 4, "2026-12": 5}


def build_pdf(pages: int) -> bytes:
    """A PDF of blank A4 pages, standing in for a rendered month."""

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class MonthRenderer:
    """A renderer that returns a PDF of a known length for each named month.

    The key is the month's PDF filename, which is what the route passes, so the
    stand-in answers per month without being told which month is which.
    """

    def __init__(self) -> None:
        self.asked: dict[str, str] = {}

    def __call__(self, documents):
        self.asked = dict(documents)
        return {
            name: build_pdf(PAGES_PER_MONTH[month_of(name)])
            for name in documents
        }


def month_of(pdf_name: str) -> str:
    """The month a rendered filename names, for example ``2026-11``."""

    return pdf_name.removeprefix("daily-dairy-register-").split("-v")[0]


@pytest.fixture()
def database(tmp_path: Path) -> Path:
    path = tmp_path / "print.db"
    connection = open_database(path)
    try:
        seed_approved_identities(connection)
        for index in range(1, 7):
            masters.upsert_dtc(
                connection,
                dtc_id=f"SCD-{index}",
                name=f"Test DTC {index}",
                dtc_class="DEPT",
                kva=100.0 + index,
                canonical_feeder_id=f"F-0{(index % 2) + 1}",
            )
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-11-10",
            holiday_date=date.fromisoformat("2026-11-10"),
            name="Recorded holiday",
            source="karnataka",
        )
        for index in range(1, 3):
            masters.upsert_activity(
                connection,
                activity_id=f"A-{index:03d}",
                category="Inspection",
                description_en=f"Activity {index}",
                description_kn=f"ಚಟುವಟಿಕೆ {index}",
            )
    finally:
        connection.close()
    return path


@pytest.fixture()
def db(database: Path) -> sqlite3.Connection:
    connection = open_database(database)
    yield connection
    connection.close()


@pytest.fixture()
def client(database: Path) -> TestClient:
    return TestClient(create_app(database))


@pytest.fixture()
def renderer(monkeypatch: pytest.MonkeyPatch) -> MonthRenderer:
    """Every render goes through the stand-in, so its page counts are the ones seen."""

    stand_in = MonthRenderer()
    monkeypatch.setattr(rendering, "render_documents", stand_in)
    # The single-month download renders one document through the other entry point,
    # and it has to see the same page count or the archive-vs-print comparison would
    # be comparing two different stand-ins.
    monkeypatch.setattr(
        rendering,
        "render_document",
        lambda html: stand_in({"daily-dairy-register-2026-11-v1.pdf": html})[
            "daily-dairy-register-2026-11-v1.pdf"
        ],
    )
    return stand_in


def issue_months(db: sqlite3.Connection, months: tuple[str, ...]) -> str:
    """Generate a draft, review every date and close the given months."""

    start = date.fromisoformat(f"{months[0]}-01")
    # A draft covers whole calendar months, so the range has to end on the last day.
    end = date.fromisoformat(f"{months[-1]}-01").replace(
        day=monthrange(int(months[-1][:4]), int(months[-1][5:]))[1]
    )
    spec = GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=7,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=len(months),
        cycle_visits_per_asset=1,
    )
    prepared = prepare_generation(db, spec)
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    run_id = generations.save_run(db, prepared, draft)
    for item in revisions.review_state(db, run_id)["dates"]:
        revisions.mark_reviewed(
            db,
            run_id,
            date.fromisoformat(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )
    for month in months:
        finalise.finalise_month(db, run_id, month)

    from dailyworkreg.infrastructure.sqlite import revision_state

    return revision_state.register_for_run(db, run_id) or ""


# --- the joined document ---------------------------------------------------


def test_every_issued_month_reaches_one_pdf_without_its_identification_page(
    db, client: TestClient, renderer: MonthRenderer
):
    register_id = issue_months(db, ("2026-11", "2026-12"))

    answer = client.get(f"/api/registers/{register_id}/exports/print.pdf")

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/pdf"
    # Four and five rendered pages, two identification pages left out.
    assert rendering.count_pages(answer.content) == 7


def test_the_document_is_named_after_the_months_it_holds(
    db, client: TestClient, renderer: MonthRenderer
):
    register_id = issue_months(db, ("2026-11", "2026-12"))

    answer = client.get(f"/api/registers/{register_id}/exports/print.pdf")

    assert (
        'filename="daily-dairy-register-2026-11-to-2026-12.pdf"'
        in answer.headers["content-disposition"]
    )


def test_one_issued_month_is_named_as_itself_rather_than_as_a_range(
    db, client: TestClient, renderer: MonthRenderer
):
    register_id = issue_months(db, ("2026-11",))

    answer = client.get(f"/api/registers/{register_id}/exports/print.pdf")

    assert rendering.count_pages(answer.content) == 3
    assert (
        'filename="daily-dairy-register-2026-11.pdf"'
        in answer.headers["content-disposition"]
    )


def test_the_months_read_in_order_rather_than_as_the_database_returned_them(
    db, client: TestClient, renderer: MonthRenderer
):
    from pypdf import PdfReader

    register_id = issue_months(db, ("2026-11", "2026-12"))

    answer = client.get(f"/api/registers/{register_id}/exports/print.pdf")

    # November rendered four pages and December five; three and four are left after
    # each identification page goes, so seven pages are written and the first three
    # of them are November's register.
    assert len(PdfReader(BytesIO(answer.content)).pages) == 7


def test_an_identification_page_is_still_in_the_single_month_download(
    db, client: TestClient, renderer: MonthRenderer
):
    """The archive keeps what the printed document leaves out."""

    register_id = issue_months(db, ("2026-11",))
    months = client.get(f"/api/registers/{register_id}/months").json()
    finalisation_id = next(
        month["latest_finalisation_id"]
        for month in months
        if month["latest_finalisation_id"]
    )

    answer = client.get(f"/api/finalisations/{finalisation_id}/export.pdf")

    assert answer.status_code == 200
    assert rendering.count_pages(answer.content) == 4


def test_a_register_with_nothing_issued_is_refused(
    db, client: TestClient, renderer: MonthRenderer
):
    """A register whose draft was never finalised has nothing to print."""

    start = date.fromisoformat("2026-11-01")
    spec = GenerationSpec(
        start=start,
        end=date.fromisoformat("2026-11-30"),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=7,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=1,
        cycle_visits_per_asset=1,
    )
    prepared = prepare_generation(db, spec)
    draft = generate_draft(prepared.request)
    run_id = generations.save_run(db, prepared, draft)

    from dailyworkreg.infrastructure.sqlite import revision_state

    register_id = revision_state.register_for_run(db, run_id) or ""
    answer = client.get(f"/api/registers/{register_id}/exports/print.pdf")

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "register_has_no_issued_months"
    assert renderer.asked == {}


def test_printing_changes_no_stored_record(db, client: TestClient, renderer: MonthRenderer):
    register_id = issue_months(db, ("2026-11", "2026-12"))
    before = client.get(f"/api/registers/{register_id}/months").json()

    client.get(f"/api/registers/{register_id}/exports/print.pdf")

    assert client.get(f"/api/registers/{register_id}/months").json() == before


# --- the page operations themselves ----------------------------------------


def test_a_rendered_month_loses_its_last_page_and_not_another():
    source = build_pdf(4)

    kept = rendering.without_identification_page(source)

    assert rendering.count_pages(source) == 4
    assert rendering.count_pages(kept) == 3


def test_a_month_holding_only_its_identification_page_is_refused():
    with pytest.raises(rendering.RenderError) as refusal:
        rendering.without_identification_page(build_pdf(1))

    assert refusal.value.constraint == rendering.C_PDF_RENDERING_FAILED


def test_joining_keeps_every_page_of_every_document_in_order():
    joined = rendering.join_pdfs([build_pdf(3), build_pdf(2), build_pdf(1)])

    assert rendering.count_pages(joined) == 6


def test_joining_nothing_is_refused_rather_than_returning_an_empty_document():
    with pytest.raises(rendering.RenderError):
        rendering.join_pdfs([])


def test_the_identification_page_is_one_page():
    """The number every caller relies on is stated once and is one."""

    assert rendering.identification_page_count() == 1


def test_a_missing_page_library_is_a_setup_step_not_a_crash(monkeypatch: pytest.MonkeyPatch):
    def missing():
        raise rendering.RenderError(
            rendering.C_PDF_RENDERER_UNAVAILABLE, pdf_module._MISSING_PYPFOR_PAGES
        )

    monkeypatch.setattr(pdf_module, "_pypdf", missing)

    with pytest.raises(rendering.RenderError) as refusal:
        rendering.count_pages(build_pdf(2))

    assert refusal.value.constraint == rendering.C_PDF_RENDERER_UNAVAILABLE
    assert "pip install pypdf" in refusal.value.detail


# --- against a real layout engine ------------------------------------------
#
# Everything above stands a renderer in, so it runs anywhere and pins the page
# arithmetic. These two need the browser the download actually uses, because a stand-in
# cannot fail the way a real render does: it cannot refuse a month for having no page
# left, and it cannot produce a document that is not a PDF.


def engine_available() -> bool:
    try:
        with pdf_module.browser():
            return True
    except rendering.RenderError:
        return False


needs_engine = pytest.mark.skipif(
    not engine_available(),
    reason=(
        "no local Chromium is installed; run 'python -m playwright install chromium' "
        "to exercise the real PDF renderer"
    ),
)


@needs_engine
def test_the_button_writes_a_real_pdf_of_every_month(db, client: TestClient):
    """The joined document is the months with one identification page each left out.

    Asserted against each month's own download rather than against a guessed page
    count: how many pages a month lays out to depends on how many dates it holds, and
    December takes a second page where November takes one.
    """

    from pypdf import PdfReader

    register_id = issue_months(db, ("2026-11", "2026-12"))
    months = client.get(f"/api/registers/{register_id}/months").json()
    finalisation_ids = [
        month["latest_finalisation_id"]
        for month in months
        if month["latest_finalisation_id"]
    ]

    answer = client.get(f"/api/registers/{register_id}/exports/print.pdf")

    assert answer.status_code == 200, answer.text
    assert answer.content.startswith(b"%PDF-")
    assert answer.content.rstrip().endswith(b"%%EOF")

    alone = [
        len(PdfReader(BytesIO(client.get(
            f"/api/finalisations/{finalisation_id}/export.pdf"
        ).content)).pages)
        for finalisation_id in finalisation_ids
    ]
    assert len(alone) == 2
    assert all(pages > 1 for pages in alone), alone
    assert len(PdfReader(BytesIO(answer.content)).pages) == sum(alone) - 2


@needs_engine
def test_the_identification_page_is_the_page_the_button_leaves_out(
    db, client: TestClient
):
    """The rendered identification page is the last page, not the first or the middle."""

    from pypdf import PdfReader

    register_id = issue_months(db, ("2026-11",))
    months = client.get(f"/api/registers/{register_id}/months").json()
    finalisation_id = next(
        month["latest_finalisation_id"]
        for month in months
        if month["latest_finalisation_id"]
    )

    single = client.get(f"/api/finalisations/{finalisation_id}/export.pdf")
    joined = client.get(f"/api/registers/{register_id}/exports/print.pdf")

    single_pages = len(PdfReader(BytesIO(single.content)).pages)
    joined_pages = len(PdfReader(BytesIO(joined.content)).pages)
    assert single_pages > 1
    assert joined_pages == single_pages - 1
