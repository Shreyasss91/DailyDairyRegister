"""The operator pages for review, correction and regeneration.

The pages may render what the server decided and send what the operator entered. They
may not decide anything themselves, so these tests pin two properties: the controls
exist for every act this capability adds, and no scheduling rule or diagnostic parsing
lives in the browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from dailyworkreg.interfaces.api import create_app
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "dailyworkreg" / "interfaces"
APP_JS = (STATIC / "static" / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "templates" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "ui.db"))


def test_the_page_offers_a_place_for_review_correction_and_regeneration(client):
    answer = client.get("/")

    assert answer.status_code == 200
    for marker in (
        'id="draft-banner"',
        'id="draft-rows"',
        'id="draft-regenerate"',
        'id="candidate-panel"',
        'id="draft-revisions"',
    ):
        assert marker in answer.text


def test_the_page_sends_every_act_of_this_capability_to_the_server():
    for path in (
        "/review/",
        "/locks/",
        "/entries",
        "/visits/move",
        "/candidates",
        "/compare/",
        "/accept",
        "/reject",
    ):
        assert path in APP_JS, path


def test_the_client_reads_no_diagnostic_message_to_decide_anything():
    for pattern in ("detail.match", "detail.split", "detail.indexOf", "detail.includes"):
        assert pattern not in APP_JS, pattern


def test_the_client_decides_no_constraint_of_its_own():
    # The identifiers of the domain's constraints and of the allocator's obligations
    # have no business in a rendering layer: if one appears here, a rule has been
    # copied into the browser and will disagree with the server within a release.
    lowered = APP_JS.lower()
    for word in (
        "area_daily_cap",
        "combined_daily_cap",
        "asset_monthly_max",
        "revisit_gap_violated",
        "prorate",
        "obligation",
        "validate_schedule",
    ):
        assert word not in lowered, word


def test_the_client_does_not_filter_or_reorder_the_rows_it_is_given():
    # Display-only date use exists for defaulting a form; deciding which date or
    # asset is eligible does not.
    assert "rows.filter(" not in APP_JS
    assert "rows.sort(" not in APP_JS
    assert "visits.filter(" not in APP_JS


def test_review_and_lock_are_displayed_as_separate_dimensions():
    assert "tag-reviewed" in APP_JS
    assert "tag-locked" in APP_JS
    assert "pending review" in APP_JS
    assert "reviewed" in APP_JS and "locked" in APP_JS
