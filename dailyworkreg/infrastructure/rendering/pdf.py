"""Turning a printed register into a PDF on this computer, with no network.

The register is already a print-ready page: the template states A4 portrait, its own
margins, repeated column headings and where a row may not be split. What a PDF needs is
an engine that honours that page description, and the honest way to get one is the same
engine the operator's own browser would use. Chromium is installed locally, driven by
Playwright, and every render happens in this process on this machine: no document ever
leaves the computer, and no service is asked to lay anything out.

Two decisions keep the output trustworthy.

*   **The engine is a dependency, not an assumption.** If Playwright or its browser is
    missing, rendering raises :class:`RenderError` with
    :data:`C_PDF_RENDERER_UNAVAILABLE` rather than half-producing a file or quietly
    serving the HTML with a PDF filename. The operator is told which step installs it.
*   **One browser serves a whole download.** A bulk download renders several months, and
    starting an engine per document would be several seconds of work for no benefit. The
    browser is opened once per call and closed with a context manager, so a failed render
    cannot leave one behind.

The page description is the template's, so ``prefer_css_page_size`` is deliberate: the
paper size and margins printed here are the ones the operator sees in the browser, and a
second copy of them in Python would be a second thing to keep in step.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from typing import Any

C_PDF_RENDERER_UNAVAILABLE = "pdf_renderer_unavailable"
C_PDF_RENDERING_FAILED = "pdf_rendering_failed"

#: How long one document may take before the render is abandoned. Generous, because the
#: first document pays for the browser's own start-up.
RENDER_TIMEOUT_MS = 120_000

_MISSING = (
    "the PDF layout engine is not installed on this computer, so a PDF cannot be "
    "written; install it once with 'python -m playwright install chromium' and try again"
)


class RenderError(Exception):
    """A PDF that could not be written, in terms the operator can act on."""

    def __init__(self, constraint: str, detail: str) -> None:
        self.constraint = constraint
        self.detail = detail
        super().__init__(detail)


@contextlib.contextmanager
def browser() -> Iterator[Any]:
    """A locally installed Chromium, opened for the duration of one download."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as missing:  # pragma: no cover - depends on the installation
        raise RenderError(C_PDF_RENDERER_UNAVAILABLE, _MISSING) from missing

    with sync_playwright() as engine:
        try:
            instance = engine.chromium.launch()
        except PlaywrightError as failure:
            # A browser that was never downloaded reports itself here, and the sentence
            # has to name the step that fixes it rather than the file it looked for.
            raise RenderError(C_PDF_RENDERER_UNAVAILABLE, _MISSING) from failure
        try:
            yield instance
        finally:
            instance.close()


def _render(page: Any, html: str) -> bytes:
    from playwright.sync_api import Error as PlaywrightError

    try:
        page.set_content(html, wait_until="load")
        return page.pdf(prefer_css_page_size=True, print_background=True)
    except PlaywrightError as failure:
        raise RenderError(
            C_PDF_RENDERING_FAILED,
            (
                "this register could not be turned into a PDF on this computer, so "
                "nothing was downloaded; the printed page is still available and can be "
                "saved from the browser"
            ),
        ) from failure


def render_documents(documents: Mapping[str, str]) -> dict[str, bytes]:
    """Render each ``name -> html`` pair to a PDF, in the order given.

    One browser and one page serve the whole mapping. A document that fails to render
    aborts the whole call: a package holding four of five months would read as a complete
    set of five, which is worse than no package at all.
    """

    if not documents:
        return {}

    rendered: dict[str, bytes] = {}
    with browser() as instance:
        page = instance.new_page()
        try:
            page.set_default_timeout(RENDER_TIMEOUT_MS)
            for name, html in documents.items():
                rendered[name] = _render(page, html)
        finally:
            page.close()
    return rendered


def render_document(html: str) -> bytes:
    """Render one document to a PDF."""

    return render_documents({"document": html})["document"]
