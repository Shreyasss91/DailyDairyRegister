"""One-click downloads: a real PDF for each issued month, and a workbook for each.

Two properties are worth more than the shape of the ZIP. The PDF has to be a PDF — laid
out by an engine on this computer, not an HTML file with a .pdf name — and the missing
engine has to be a sentence an operator can act on rather than a file that will not open.
And the download has to be a reading: nothing in the register may move because somebody
fetched a file.

The rendering tests are split deliberately. The packaging, the refusal and the read-only
guarantees are exercised against a stand-in renderer, so they run anywhere. The tests that
need a real layout engine are skipped when Chromium is not installed on the machine, and
they are the only ones that assert the bytes really are a PDF.
"""

from __future__ import annotations

import sqlite3
import sys
import types
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

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
from openpyxl import load_workbook


@pytest.fixture()
def database(tmp_path: Path) -> Path:
    path = tmp_path / "packages.db"
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
            holiday_date=date_of("2026-11-10"),
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


def date_of(text: str):
    from datetime import date as _date

    return _date.fromisoformat(text)


@pytest.fixture()
def db(database: Path) -> sqlite3.Connection:
    connection = open_database(database)
    yield connection
    connection.close()


@pytest.fixture()
def client(database: Path) -> TestClient:
    return TestClient(create_app(database))


def spec(**kwargs) -> GenerationSpec:
    start = kwargs.get("start", date_of("2026-11-01"))
    end = kwargs.get("end", date_of("2026-12-31"))
    return GenerationSpec(
        start=start,
        end=end,
        mode=DraftMode.COVERAGE_CYCLE,
        seed=7,
        cycle_start_month=MonthKey(start.year, start.month),
        cycle_duration_months=kwargs.get("cycle_months", 2),
        cycle_visits_per_asset=1,
    )


def stored(db: sqlite3.Connection) -> str:
    prepared = prepare_generation(db, spec())
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    return generations.save_run(db, prepared, draft)


def review_every_date(db: sqlite3.Connection, run_id: str) -> None:
    for item in revisions.review_state(db, run_id)["dates"]:
        revisions.mark_reviewed(
            db,
            run_id,
            date_of(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )


def two_issued_months(db: sqlite3.Connection) -> list[dict]:
    """November and December closed, so a package has two documents to hold."""

    run_id = stored(db)
    review_every_date(db, run_id)
    return [
        finalise.finalise_month(db, run_id, month)
        for month in ("2026-11", "2026-12")
    ]


class Recorder:
    """A stand-in renderer that keeps what it was asked to render."""

    def __init__(self) -> None:
        self.documents: dict[str, str] = {}

    def __call__(self, documents):
        self.documents = dict(documents)
        return {name: b"%PDF-1.7 stand-in" for name in documents}


@pytest.fixture()
def rendered(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Every render, single or packaged, goes through this stand-in."""

    recorder = Recorder()
    monkeypatch.setattr(rendering, "render_documents", recorder)
    monkeypatch.setattr(
        rendering, "render_document", lambda html: recorder({"document": html})["document"]
    )
    return recorder


def register_id_of(db: sqlite3.Connection, run_id: str) -> str:
    from dailyworkreg.infrastructure.sqlite import revision_state

    return revision_state.register_for_run(db, run_id) or ""


# --- the PDF package --------------------------------------------------------


def test_the_pdf_package_holds_one_genuine_pdf_for_each_issued_month(
    db, client: TestClient, rendered: Recorder
):
    records = two_issued_months(db)
    answer = client.get(
        f"/api/registers/{register_id_of(db, records[0]['run_id'])}/exports/pdf.zip"
    )

    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(answer.content)) as package:
        names = set(package.namelist())
        assert names == {
            "pdf/daily-dairy-register-2026-11-v1.pdf",
            "pdf/daily-dairy-register-2026-12-v1.pdf",
            "README.txt",
        }
        assert package.read("pdf/daily-dairy-register-2026-11-v1.pdf").startswith(b"%PDF-")
        instructions = package.read("README.txt").decode("utf-8")
    # The README names what is in the package and which record each file is.
    for record in records:
        assert record["month"] in instructions
        assert record["finalisation_id"] in instructions
        assert f"issued version {record['sequence']}" in instructions
    assert "DailyDairyRegister — issued monthly PDFs" in instructions


def test_the_pdf_package_renders_the_register_page_not_something_else(
    db, client: TestClient, rendered: Recorder
):
    records = two_issued_months(db)
    client.get(f"/api/registers/{register_id_of(db, records[0]['run_id'])}/exports/pdf.zip")

    assert len(rendered.documents) == 2
    for markup in rendered.documents.values():
        assert "DAILY DAIRY REGISTER" in markup
        assert "Sl No." in markup and "Activity" in markup
        # Printing marks are not content, so they never reach a rendered page.
        assert "no-print" in markup


def test_the_pdf_package_lists_the_months_it_omitted(
    db, client: TestClient, rendered: Recorder
):
    run_id = stored(db)
    review_every_date(db, run_id)
    finalise.finalise_month(db, run_id, "2026-11")

    answer = client.get(
        f"/api/registers/{register_id_of(db, run_id)}/exports/pdf.zip"
    )

    with ZipFile(BytesIO(answer.content)) as package:
        instructions = package.read("README.txt").decode("utf-8")
    assert "2026-12" in instructions
    assert "Months omitted because they have no issued finalisation" in instructions


def test_a_register_with_no_issued_month_offers_no_package(
    db, client: TestClient, rendered: Recorder
):
    run_id = stored(db)

    answer = client.get(
        f"/api/registers/{register_id_of(db, run_id)}/exports/pdf.zip"
    )

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "register_has_no_issued_months"
    assert rendered.documents == {}


def test_a_missing_layout_engine_is_told_to_the_operator_as_a_setup_step(
    db, client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    records = two_issued_months(db)

    def unavailable(_documents):
        raise rendering.RenderError(
            rendering.C_PDF_RENDERER_UNAVAILABLE,
            "the PDF layout engine is not installed on this computer",
        )

    monkeypatch.setattr(rendering, "render_documents", unavailable)

    answer = client.get(
        f"/api/registers/{register_id_of(db, records[0]['run_id'])}/exports/pdf.zip"
    )

    # A missing engine is a fact about this computer, not a bad request.
    assert answer.status_code == 503
    assert answer.json()["error"]["constraint"] == "pdf_renderer_unavailable"


def test_the_renderer_reports_a_missing_browser_as_a_setup_step(
    monkeypatch: pytest.MonkeyPatch
):
    class Failure(Exception):
        pass

    class Launcher:
        def launch(self):
            raise Failure("Executable doesn't exist at ...")

    class Engine:
        chromium = Launcher()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    fake = types.ModuleType("playwright.sync_api")
    fake.Error = Failure
    fake.sync_playwright = lambda: Engine()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)

    with pytest.raises(rendering.RenderError) as failure:
        with pdf_module.browser():
            pass

    assert failure.value.constraint == rendering.C_PDF_RENDERER_UNAVAILABLE
    # The sentence names the step that fixes it, not the file that was looked for.
    assert "playwright install chromium" in failure.value.detail


def test_a_failed_render_downloads_nothing(
    db, client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    records = two_issued_months(db)

    def broken(_documents):
        raise rendering.RenderError(
            rendering.C_PDF_RENDERING_FAILED,
            "this register could not be turned into a PDF on this computer",
        )

    monkeypatch.setattr(rendering, "render_documents", broken)

    answer = client.get(
        f"/api/registers/{register_id_of(db, records[0]['run_id'])}/exports/pdf.zip"
    )

    # Half a package would read as a complete one, so no package is sent at all.
    assert answer.status_code == 503
    assert answer.json()["error"]["constraint"] == "pdf_rendering_failed"


# --- the Excel package ------------------------------------------------------


def test_the_excel_package_holds_one_workbook_for_each_issued_month(
    db, client: TestClient
):
    records = two_issued_months(db)

    answer = client.get(
        f"/api/registers/{register_id_of(db, records[0]['run_id'])}/exports/excel.zip"
    )

    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(answer.content)) as package:
        names = set(package.namelist())
        assert names == {
            "excel/daily-dairy-register-2026-11-v1.xlsx",
            "excel/daily-dairy-register-2026-12-v1.xlsx",
            "README.txt",
        }
        for name in sorted(names - {"README.txt"}):
            book = load_workbook(BytesIO(package.read(name)), read_only=True)
            assert book.sheetnames == ["Register", "Metadata"]
            book.close()
        instructions = package.read("README.txt").decode("utf-8")
    assert "DailyDairyRegister — issued monthly workbooks" in instructions
    for record in records:
        assert record["finalisation_id"] in instructions


def test_the_excel_package_does_not_need_a_layout_engine(
    db, client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    records = two_issued_months(db)

    def unavailable(_documents):  # pragma: no cover - must not be reached
        raise AssertionError("the workbook package must not render a page")

    monkeypatch.setattr(rendering, "render_documents", unavailable)

    answer = client.get(
        f"/api/registers/{register_id_of(db, records[0]['run_id'])}/exports/excel.zip"
    )

    assert answer.status_code == 200


# --- one month on its own ---------------------------------------------------


def test_one_issued_month_downloads_as_its_own_pdf(
    db, client: TestClient, rendered: Recorder
):
    records = two_issued_months(db)
    record = records[0]

    answer = client.get(f"/api/finalisations/{record['finalisation_id']}/export.pdf")

    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "application/pdf"
    assert (
        f'daily-dairy-register-{record["month"]}-v{record["sequence"]}.pdf'
        in answer.headers["content-disposition"]
    )
    assert list(rendered.documents.values())[0].count("2026-11") >= 1


def test_an_unknown_issued_version_downloads_nothing(client: TestClient):
    answer = client.get("/api/finalisations/MF-does-not-exist/export.pdf")

    assert answer.status_code == 400
    assert answer.json()["error"]["constraint"] == "finalisation_unknown"


# --- exporting is a reading -------------------------------------------------


def test_a_package_download_changes_nothing_in_the_register(
    db, client: TestClient, rendered: Recorder
):
    records = two_issued_months(db)
    register_id = register_id_of(db, records[0]["run_id"])

    def snapshot() -> list[tuple]:
        return [
            db.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()[0]
            for table in ("month_finalisation", "month_state", "generation_row")
        ]

    before = snapshot()
    client.get(f"/api/registers/{register_id}/exports/pdf.zip")
    client.get(f"/api/registers/{register_id}/exports/excel.zip")
    client.get(f"/api/finalisations/{records[0]['finalisation_id']}/export.pdf")
    client.get(f"/api/registers/{register_id}/exports.zip")

    assert snapshot() == before


# --- with a real layout engine ---------------------------------------------


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
def test_the_downloaded_pdf_is_a_real_pdf_with_pages(db, client: TestClient):
    records = two_issued_months(db)

    answer = client.get(f"/api/finalisations/{records[0]['finalisation_id']}/export.pdf")

    assert answer.status_code == 200, answer.text
    body = answer.content
    assert body.startswith(b"%PDF-")
    assert body.rstrip().endswith(b"%%EOF")
    assert len(body) > 3_000
    # Two register pages and an identification page, at the very least the table and the
    # metadata page this month needs.
    assert body.count(b"/Type /Page") >= 2


@needs_engine
def test_the_whole_package_renders_on_one_browser(
    db, client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    records = two_issued_months(db)
    opened: list[int] = []
    original = pdf_module.browser

    import contextlib as _contextlib

    @_contextlib.contextmanager
    def counting():
        opened.append(1)
        with original() as instance:
            yield instance

    monkeypatch.setattr(pdf_module, "browser", counting)
    monkeypatch.setattr(rendering, "render_documents", pdf_module.render_documents)

    answer = client.get(
        f"/api/registers/{register_id_of(db, records[0]['run_id'])}/exports/pdf.zip"
    )

    assert answer.status_code == 200, answer.text
    assert opened == [1]
    with ZipFile(BytesIO(answer.content)) as package:
        for name in package.namelist():
            if name.endswith(".pdf"):
                assert package.read(name).startswith(b"%PDF-")

# --- the page ---------------------------------------------------------------


def test_the_page_offers_the_pdf_and_excel_packages_side_by_side():
    script = (
        Path(__file__).resolve().parent.parent
        / "dailyworkreg/interfaces/static/app.js"
    ).read_text(encoding="utf-8")

    assert "exports/pdf.zip" in script
    assert "exports/excel.zip" in script
    assert "exports/print.pdf" in script
    assert "Single PDF with no metadata" in script
    assert "Download PDFs — all months" in script
    assert "Download Excel — all months" in script
    # The mixed package stays, so an older bookmark is not broken by the new buttons.
    assert "exports.zip" in script


def test_the_page_offers_a_pdf_for_the_version_being_read():
    script = (
        Path(__file__).resolve().parent.parent
        / "dailyworkreg/interfaces/static/app.js"
    ).read_text(encoding="utf-8")

    assert "export.pdf" in script
    assert "Download PDF (.pdf)" in script
