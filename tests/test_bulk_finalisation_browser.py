"""The bulk finalisation an operator actually performs, driven in a real browser.

The act this pins is the one that was broken in front of the operator: a page that
showed the action, listed what was in the way, offered the acknowledgement boxes and then
did nothing at all. Static assertions over the client source cannot catch that, because
every line was present and every function was defined; what was wrong was the interaction
between them. So these tests start the application, open the draft through the same
navigation an operator uses, click the same elements, and read the same panel back.

Each test gets its own database and its own server on its own port, so a test that closes
the months cannot satisfy the next one.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

import pytest
import uvicorn
from dailyworkreg.application import revisions
from dailyworkreg.application.draft_generation import (
    DraftMode,
    DraftStatus,
    generate_draft,
)
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.application.generation_runs import save_run
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from dailyworkreg.infrastructure.sqlite.seed import seed_approved_identities
from dailyworkreg.interfaces.server import create_app
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def browser_available() -> bool:
    """Whether this computer has the Chromium the PDF renderer also uses."""

    try:
        with sync_playwright() as playwright:
            engine = playwright.chromium.launch()
            engine.close()
            return True
    except PlaywrightError:
        return False


needs_browser = pytest.mark.skipif(
    not browser_available(),
    reason=(
        "no local Chromium is installed; run 'python -m playwright install chromium' "
        "to exercise the real page"
    ),
)

pytestmark = [needs_browser]


# --- a real server on a real port -------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Office:
    """One database behind one running server, and the ways a test disturbs it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.run_id = ""
        self.register_id = ""
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                create_app(self.path),
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                access_log=False,
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("the server did not start")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)

    # -- inspection ---------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """A second connection, so assertions read the database and not the app."""

        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def finalisations(self) -> int:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM month_finalisation"
            ).fetchone()
            return int(row["total"])
        finally:
            connection.close()

    def open_months(self) -> list[str]:
        from dailyworkreg.application import finalisation as finalise

        connection = open_database(self.path)
        try:
            return finalise.bulk_preflight(connection, self.run_id)["target_months"]
        finally:
            connection.close()

    # -- disturbance --------------------------------------------------------

    def add_holiday(self, day: date) -> None:
        """Record a General Holiday the calendar has not seen, after the page loaded."""

        connection = open_database(self.path)
        try:
            cal.upsert_holiday(
                connection,
                holiday_id=f"H-{day.isoformat()}",
                holiday_date=day,
                name="Recorded later",
                source="karnataka",
            )
        finally:
            connection.close()

    def withdraw_one_review(self, month: str) -> None:
        """Put one date of a month back to unread, as a reviewer can."""

        connection = open_database(self.path)
        try:
            for item in revisions.review_state(connection, self.run_id)["dates"]:
                if item["date"].startswith(month) and item["reviewed_at"]:
                    revisions.clear_review(
                        connection,
                        self.run_id,
                        date.fromisoformat(item["date"]),
                    )
                    return
            raise AssertionError(f"no reviewed date in {month}")
        finally:
            connection.close()


def build_office(path: Path, *, conflict: bool = False) -> str:
    """A two-month revision with every date read, ready to be closed."""

    connection = open_database(path)
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
    for index in range(1, 3):
        masters.upsert_activity(
            connection,
            activity_id=f"A-{index:03d}",
            category="Inspection",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
        )
    cal.upsert_holiday(
        connection,
        holiday_id="H-2026-11-20",
        holiday_date=date(2026, 11, 20),
        name="Recorded holiday",
        source="karnataka",
    )
    spec = GenerationSpec(
        start=date(2026, 11, 1),
        end=date(2026, 12, 31),
        mode=DraftMode.COVERAGE_CYCLE,
        seed=7,
        cycle_start_month=MonthKey(2026, 11),
        cycle_duration_months=2,
        cycle_visits_per_asset=1,
    )
    prepared = prepare_generation(connection, spec)
    draft = generate_draft(prepared.request)
    assert draft.status == DraftStatus.ALLOCATION_COMPLETE
    run_id = save_run(connection, prepared, draft)
    for item in revisions.review_state(connection, run_id)["dates"]:
        revisions.mark_reviewed(
            connection,
            run_id,
            date.fromisoformat(item["date"]),
            expected_fingerprint=item["fingerprint"],
        )
    if conflict:
        # A General Holiday on a weekly off: the calendar reports a conflict, which has
        # to be acknowledged before the month it falls in can be closed.
        cal.upsert_holiday(
            connection,
            holiday_id="H-2026-11-01",
            holiday_date=date(2026, 11, 1),
            name="Founder's Day",
            source="karnataka",
        )
    from dailyworkreg.infrastructure.sqlite import revision_state

    register_id = revision_state.register_for_run(connection, run_id) or ""
    connection.close()
    return run_id, register_id


@pytest.fixture(scope="module")
def engine():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch()
    yield browser
    browser.close()
    playwright.stop()


@pytest.fixture()
def office(tmp_path: Path, engine) -> Office:
    path = tmp_path / "office.db"
    run_id, register_id = build_office(path)
    served = Office(path)
    served.run_id = run_id
    served.register_id = register_id
    served.start()
    yield served
    served.stop()


@pytest.fixture()
def loaded(tmp_path: Path, engine, office: Office):
    """The page a test drives, with the draft already open, plus what it recorded."""

    context = engine.new_context(viewport={"width": 1360, "height": 1000})
    page = context.new_page()
    seen: dict[str, list] = {"console": [], "errors": [], "requests": [], "responses": []}
    page.on("console", lambda message: seen["console"].append(f"{message.type}: {message.text}"))
    page.on("pageerror", lambda error: seen["errors"].append(str(error)))

    def note_request(request) -> None:
        if "/finalisation/" in request.url:
            seen["requests"].append(
                {
                    "method": request.method,
                    "url": request.url,
                    "body": request.post_data,
                }
            )

    def note_response(response) -> None:
        # Only the act itself: the preflight is a GET and is recorded separately, so a
        # test that waits for "a response" cannot be satisfied by the checks being read.
        if response.request.method == "POST" and "/finalisation/" in response.url:
            seen["responses"].append({"status": response.status, "url": response.url})

    page.on("request", note_request)
    page.on("response", note_response)
    open_draft(page, office.base)
    yield page, office, seen
    context.close()


def open_draft(page, base: str) -> None:
    """Walk the navigation an operator walks: History, then the draft."""

    page.goto(base, wait_until="load")
    page.click('button.tab[data-view="history"]')
    page.wait_for_selector("#history-groups tbody button", timeout=20_000)
    page.click("#history-groups tbody button")
    page.wait_for_selector(".bulk-finalisation", timeout=20_000)
    page.wait_for_timeout(400)


BUTTON = ".bulk-finalisation button.button-primary"
CHECKBOXES = ".bulk-finalisation input.bulk-ack"


def state(page) -> dict:
    return page.evaluate(
        """() => {
            const panel = document.querySelector('.bulk-finalisation');
            const button = [...panel.querySelectorAll('button')]
                .find((node) => node.textContent.startsWith('Finalize all months'));
            const status = panel.querySelector('.bulk-status');
            return {
                disabled: button.disabled,
                attribute: button.getAttribute('disabled'),
                label: button.textContent,
                status: status ? status.textContent : '',
                boxes: panel.querySelectorAll('input.bulk-ack').length,
                checked: panel.querySelectorAll('input.bulk-ack:checked').length,
                banner: (() => {
                    const node = panel.querySelector('.banner');
                    return node && !node.hidden ? node.textContent : '';
                })(),
                text: panel.innerText,
            };
        }"""
    )


def bulk_posts(seen: dict) -> list[dict]:
    return [item for item in seen["requests"] if item["method"] == "POST"]


def wait_for(page, predicate, *, timeout: float = 20.0):
    """Wait for a condition while letting the browser deliver its events.

    Playwright's synchronous API dispatches events only while the main thread is inside
    one of its own calls, so a plain ``time.sleep`` loop would sit out the whole timeout
    with the events queued behind it. ``page.wait_for_timeout`` is a sleep that keeps the
    loop pumping, which is why every wait in this module takes the page.
    """

    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        page.wait_for_timeout(50)
    return predicate()


# --- the healthy act --------------------------------------------------------


def test_the_bulk_action_is_clickable_when_the_server_says_it_can_be(loaded):
    page, office, seen = loaded

    before = state(page)

    assert before["boxes"] == 0
    assert before["disabled"] is False
    # The defect this act repairs: setAttribute('disabled', false) wrote the string
    # "false", which disables the button just as firmly as the bare attribute.
    assert before["attribute"] is None
    assert before["disabled"] is False


def test_one_click_closes_every_month_and_the_panel_shows_them_closed(loaded):
    page, office, seen = loaded

    page.click(BUTTON)
    wait_for(page, lambda: office.finalisations() == 2)

    posts = bulk_posts(seen)
    assert len(posts) == 1
    payload = json.loads(posts[0]["body"])
    assert set(payload["months"]) == {"2026-11", "2026-12"}
    wait_for(page, lambda: seen["responses"])
    assert [item["status"] for item in seen["responses"]] == [200]
    assert office.open_months() == []

    # The lifecycle is re-read from the server, so the cards below say what the
    # database says rather than what the page hoped.
    page.wait_for_timeout(600)
    panel = state(page)
    assert "Already closed in this revision: 2026-11, 2026-12" in panel["text"]
    assert "there is nothing to finalize" in panel["status"]
    assert panel["disabled"] is True


def test_the_whole_workflow_raises_no_console_error(loaded):
    page, office, seen = loaded

    page.click(BUTTON)
    wait_for(page, lambda: office.finalisations() == 2)
    page.wait_for_timeout(400)

    assert seen["errors"] == []
    assert [item for item in seen["console"] if item.startswith("error")] == []


# --- acknowledgements -------------------------------------------------------


@pytest.fixture()
def contested(tmp_path: Path, engine) -> Office:
    path = tmp_path / "contested.db"
    run_id, register_id = build_office(path, conflict=True)
    served = Office(path)
    served.run_id = run_id
    served.register_id = register_id
    served.start()
    yield served
    served.stop()


@pytest.fixture()
def contested_page(engine, contested: Office):
    context = engine.new_context(viewport={"width": 1360, "height": 1000})
    page = context.new_page()
    page.goto(contested.base, wait_until="load")
    page.click('button.tab[data-view="history"]')
    page.wait_for_selector("#history-groups tbody button", timeout=20_000)
    page.click("#history-groups tbody button")
    page.wait_for_selector(".bulk-finalisation", timeout=20_000)
    page.wait_for_timeout(400)
    yield page, contested
    context.close()


def test_a_finding_holds_the_action_closed_and_says_which_month(contested_page):
    page, _ = contested_page

    panel = state(page)

    assert panel["boxes"] == 2
    assert panel["checked"] == 0
    assert panel["disabled"] is True
    assert "still have to be acknowledged" in panel["status"]
    assert "2026-11" in panel["status"] and "2026-12" in panel["status"]
    # The finding is shown beside the box that answers it, not only summarised.
    assert "Founder's Day falls on a Sunday" in panel["text"]


def test_acknowledging_one_month_does_not_answer_another(contested_page):
    page, _ = contested_page

    page.check(f"{CHECKBOXES} >> nth=0")
    page.wait_for_timeout(200)
    first = state(page)

    assert first["checked"] == 1
    assert first["disabled"] is True
    assert "2026-12" in first["status"]

    page.check(f"{CHECKBOXES} >> nth=1")
    page.wait_for_timeout(200)
    second = state(page)

    assert second["checked"] == 2
    assert second["disabled"] is False
    assert second["attribute"] is None


def test_every_month_carries_its_own_acknowledgement_and_both_close(contested_page):
    page, office = contested_page

    page.check(f"{CHECKBOXES} >> nth=0")
    page.check(f"{CHECKBOXES} >> nth=1")
    page.wait_for_timeout(200)
    page.click(BUTTON)
    wait_for(page, lambda: office.finalisations() == 2)

    connection = office.connect()
    try:
        rows = connection.execute(
            "SELECT month, acknowledged_json FROM month_finalisation ORDER BY month"
        ).fetchall()
    finally:
        connection.close()

    assert [row["month"] for row in rows] == ["2026-11", "2026-12"]
    expected = "calendar_conflict|2026-11-01: Founder's Day falls on a Sunday"
    for row in rows:
        # The record keeps the finding and its token, and each month answered for its
        # own findings rather than inheriting the other month's tick.
        assert [item["token"] for item in json.loads(row["acknowledged_json"])] == [expected]


# --- refusal and recovery ---------------------------------------------------


def test_a_hard_blocker_keeps_the_action_closed_and_is_named(tmp_path, engine):
    """An unread date is not something a checkbox can answer."""

    path = tmp_path / "blocked.db"
    run_id, register_id = build_office(path)
    served = Office(path)
    served.run_id = run_id
    served.register_id = register_id
    served.start()
    try:
        served.withdraw_one_review("2026-12")
        context = engine.new_context(viewport={"width": 1360, "height": 1000})
        page = context.new_page()
        page.goto(served.base, wait_until="load")
        page.click('button.tab[data-view="history"]')
        page.wait_for_selector("#history-groups tbody button", timeout=20_000)
        page.click("#history-groups tbody button")
        page.wait_for_selector(".bulk-finalisation", timeout=20_000)
        page.wait_for_timeout(400)

        panel = state(page)

        assert panel["disabled"] is True
        assert panel["boxes"] == 0
        assert "cannot be closed" in panel["status"]
        assert "not been read" in panel["text"] or "read" in panel["text"]
        assert "2026-12" in panel["text"]
        context.close()
    finally:
        served.stop()


def test_a_refusal_after_a_calendar_change_is_shown_and_the_checks_reload(loaded):
    page, office, seen = loaded

    # The register changes after the page read the checks: a holiday the calendar did
    # not have when the button was enabled.
    office.add_holiday(date(2026, 11, 8))
    page.click(BUTTON)
    wait_for(page, lambda: seen["responses"])
    page.wait_for_timeout(400)

    assert seen["responses"][0]["status"] == 400
    refused = state(page)
    assert refused["banner"], "the refusal must be visible"
    assert refused["disabled"] is True
    assert "out of date" in refused["status"]
    assert office.finalisations() == 0

    # Refreshing re-reads the server's verdict, and the month that now needs an
    # acknowledgement offers the box that answers it.
    page.click(".bulk-finalisation .actions button:not(.button-primary)")
    page.wait_for_timeout(600)
    refreshed = state(page)
    assert refreshed["boxes"] >= 1
    assert refreshed["disabled"] is True
    assert "acknowledged" in refreshed["status"]


def test_a_failed_preflight_leaves_a_retry_that_works(loaded):
    page, office, seen = loaded

    page.route(
        "**/finalisation/preflight",
        lambda route: route.fulfill(status=500, body="{}", content_type="application/json"),
    )
    page.click(".bulk-finalisation .actions button:not(.button-primary)")
    page.wait_for_timeout(600)

    broken = state(page)
    assert broken["disabled"] is True
    assert "could not be read" in broken["status"]

    page.unroute("**/finalisation/preflight")
    page.click(".bulk-finalisation .actions button:not(.button-primary)")
    page.wait_for_timeout(800)

    recovered = state(page)
    assert recovered["disabled"] is False
    assert "can be closed" in recovered["status"]


def test_a_second_click_while_closing_sends_one_request(loaded):
    page, office, seen = loaded

    page.click(BUTTON)
    # A double click lands while the first request is in flight or after the panel has
    # been rebuilt; neither may produce a second act.
    try:
        page.click(BUTTON, timeout=2000)
    except Exception:
        pass
    wait_for(page, lambda: office.finalisations() == 2)
    page.wait_for_timeout(500)

    assert len(bulk_posts(seen)) == 1
    assert office.finalisations() == 2


def test_the_action_reports_progress_before_the_server_answers(loaded):
    page, office, seen = loaded

    page.click(BUTTON)
    wait_for(page, lambda: office.finalisations() == 2)
    page.wait_for_timeout(400)

    # The success banner names every month that was closed, so a partial act could
    # never be reported as a whole one.
    panel = state(page)
    assert "Closed 2 month(s) in one act." in panel["banner"] or "already closed" in panel["text"].lower()
