"""The progress bar an operator actually watches, driven in a real browser.

A bar that is drawn from a poll cannot be tested by reading the client source: the thing
being asserted is that the page keeps asking while the server keeps working, and that the
answers it shows only ever move forward. So this module starts the application, presses the
same button an operator presses, records every state the status line held, and then reads the
draft the run produced through the same navigation.

The percentage is the server's own, so this test does not compute what the bar should say; it
checks that something was said, that it moved, and that it ended at the finished state with
the draft open behind it.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from datetime import date
from pathlib import Path

import pytest
import uvicorn
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


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Office:
    """One seeded database behind one running server."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._seed()

    def _seed(self) -> None:
        connection = open_database(self.path)
        try:
            seed_approved_identities(connection)
            for index in range(1, 13):
                masters.upsert_dtc(
                    connection,
                    dtc_id=f"SCD-{index}",
                    name=f"Test DTC {index}",
                    dtc_class="DEPT",
                    kva=100.0 + index,
                )
            for index in range(1, 4):
                masters.upsert_area(
                    connection,
                    area_id=f"A-{index:02d}",
                    name_kn=f"ಪ್ರದೇಶ {index}",
                    monthly_min=0,
                    monthly_max=4,
                    revisit_gap_days=7,
                )
            for index in range(1, 5):
                masters.upsert_feeder(
                    connection,
                    feeder_id=f"F-{index:02d}",
                    name=f"Feeder {index}",
                    monthly_min=2,
                    monthly_max=3,
                    revisit_gap_days=5,
                )
            cal.upsert_holiday(
                connection,
                holiday_id="H-2026-11-10",
                holiday_date=date(2026, 11, 10),
                name="Recorded holiday",
                source="karnataka",
            )
            for index in range(1, 13):
                masters.upsert_activity(
                    connection,
                    activity_id=f"ACT-{index:03d}",
                    category="System operation",
                    description_en=f"Activity {index}",
                    description_kn=f"ಚಟುವಟಿಕೆ {index}",
                    weight=1.0,
                    monthly_min=0,
                    monthly_max=20,
                    repeat_gap_days=1,
                )
        finally:
            connection.close()

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


@pytest.fixture()
def office(tmp_path: Path):
    desk = Office(tmp_path / "browser.db")
    desk.start()
    try:
        yield desk
    finally:
        desk.stop()


#: Records every message the status line holds, so the test can read the bar's whole life
#: rather than whatever it happens to look like when the test gets round to asking. It is
#: installed before the button is pressed.
RECORD_STATES = """
window.__genStates = [];
const line = document.getElementById('gen-progress-text');
const fill = document.getElementById('gen-progress-fill');
const note = () => window.__genStates.push({
  text: line.textContent,
  width: fill.style.width,
  hidden: document.getElementById('gen-progress').hidden,
});
new MutationObserver(note).observe(line, { childList: true, characterData: true, subtree: true });
note();
"""


def press_generate(page) -> None:
    """Fill the form the way an operator fills it and press the button once."""

    page.click('button.tab[data-view="generate"]')
    page.fill("#gen-start", "2026-11-01")
    page.fill("#gen-end", "2027-01-31")
    page.fill("#gen-cycle-start", "2026-11")
    page.fill("#gen-cycle-months", "3")
    page.fill("#gen-visits", "1")
    page.click("#gen-run")


def read_states(page) -> list[dict]:
    return page.evaluate("() => window.__genStates")


def percentages(states: list[dict]) -> list[int]:
    found = []
    for state in states:
        match = re.search(r"(\d+)%", state["text"] or "")
        if match:
            found.append(int(match.group(1)))
    return found


def test_the_bar_moves_while_the_register_is_being_generated(office: Office):
    with sync_playwright() as playwright:
        engine = playwright.chromium.launch()
        page = engine.new_page()
        page.goto(office.base, wait_until="load")
        page.evaluate(RECORD_STATES)

        press_generate(page)
        page.wait_for_function(
            "() => document.getElementById('gen-progress-text').textContent.includes('100%')",
            timeout=60_000,
        )
        states = read_states(page)
        # The bar reaching the end and the draft opening are two steps: the page reads the
        # draft it was just given before it shows it. So the draft is waited for, rather
        # than read in whatever state it happens to be in a moment after 100%.
        page.wait_for_selector("#view-draft", state="visible", timeout=30_000)
        assert page.is_visible("#draft-rows tbody"), "the draft opened with no rows in it"
        engine.close()

    assert states, "the status line never said anything"
    assert any(state["hidden"] is False for state in states), "the bar stayed hidden"

    shown = percentages(states)
    assert shown, f"no percentage was ever shown: {states}"
    assert shown == sorted(shown), f"the bar went backwards: {shown}"
    assert shown[-1] == 100
    assert any(state["width"] == "100%" for state in states)
    assert all("elapsed" in (state["text"] or "") for state in states if state["text"])


def test_the_bar_works_again_for_a_second_run_in_the_same_page(office: Office):
    """A bar that is emptied when it is reset cannot be drawn into a second time."""

    with sync_playwright() as playwright:
        engine = playwright.chromium.launch()
        page = engine.new_page()
        page.goto(office.base, wait_until="load")
        page.evaluate(RECORD_STATES)
        press_generate(page)
        page.wait_for_selector("#view-draft", state="visible", timeout=60_000)

        # Same page, same bar element, a second run.
        page.evaluate("() => { window.__genStates = []; }")
        press_generate(page)
        page.wait_for_function(
            "() => document.getElementById('gen-progress-text').textContent.includes('100%')",
            timeout=60_000,
        )
        second = percentages(read_states(page))
        engine.close()

    assert second, "the bar said nothing the second time"
    assert second[-1] == 100
