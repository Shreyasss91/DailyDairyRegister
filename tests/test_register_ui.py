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
        'id="draft-review-actions"',
        'id="draft-rows"',
        'id="draft-regenerate"',
        'id="candidate-panel"',
        'id="draft-revisions"',
    ):
        assert marker in answer.text




def test_bulk_review_uses_date_states_and_a_dedicated_visible_container():
    assert 'id="draft-review-actions"' in INDEX
    assert "const pendingDates = review.dates.filter((day) => day.state === 'pending_review')" in APP_JS
    assert "reviewActions.hidden = false" in APP_JS
    assert "reviewActions.append(bulkReview)" in APP_JS
    assert "review.total_dates - review.reviewed_dates" not in APP_JS


def test_the_page_revalidates_html_and_uses_a_versioned_application_script(client):
    response = client.get("/")
    assert response.headers["cache-control"] == "no-cache"
    assert '/static/app.js?v=review-bulk-ui-2' in response.text




def test_the_draft_offers_one_download_package_for_each_format():
    """Three downloads, each naming what it holds, shown only when a month is issued."""

    assert "const issuedCount = months.filter((month) => month.latest_finalisation_id).length" in APP_JS
    assert "`/api/registers/${registerId}/exports.zip`" in APP_JS
    assert "`/api/registers/${registerId}/exports/pdf.zip`" in APP_JS
    assert "`/api/registers/${registerId}/exports/excel.zip`" in APP_JS
    assert "Download PDFs — all months" in APP_JS
    assert "Download Excel — all months" in APP_JS
    assert "Download everything issued" in APP_JS
    assert "one combined print-ready page" in APP_JS


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



def test_failed_generation_attempts_render_evidence_without_revision_review():
    guard = APP_JS.index("if (!register.is_draft)")
    review_lookup = APP_JS.index("const review = await api(`/api/generations/${runId}/review`)")
    assert guard < review_lookup
    assert "No accepted revision exists for this attempt" in APP_JS
    assert "Month evidence" in APP_JS


def test_the_draft_offers_bulk_review_and_retains_individual_review_controls():
    assert "Mark all days reviewed" in APP_JS
    assert "`/api/generations/${runId}/review`" in APP_JS
    assert "text: day.state === 'reviewed' ? 'withdraw' : 'mark reviewed'" in APP_JS




def test_each_finalised_month_card_offers_direct_excel_and_print_actions():
    assert "if (month.latest_finalisation_id)" in APP_JS
    assert "`/api/finalisations/${month.latest_finalisation_id}/export.xlsx`" in APP_JS
    assert "`/finalisations/${month.latest_finalisation_id}/print`" in APP_JS
    assert "Download Excel (.xlsx)" in APP_JS
    assert "Print / Save as PDF" in APP_JS
    # The action is conditional on an issued version, not merely a reviewed draft.


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


# --- closing a month, and correcting a closed one -------------------------


def test_the_page_offers_a_place_to_close_a_month(client):
    answer = client.get("/")

    assert answer.status_code == 200
    assert 'id="draft-months"' in answer.text


def test_the_page_sends_finalisation_and_correction_to_the_server():
    for path in ("/finalisation", "/finalisations/", "/corrections", "/months/"):
        assert path in APP_JS, path


def test_allocation_review_locks_and_finalisation_are_shown_separately():
    # Four dimensions, four labels. Collapsing them into one status is what lets an
    # operator believe a reviewed month is a closed one.
    for label in ("'Allocation'", "'Review'", "'Locks'", "'Finalisation'"):
        assert label in APP_JS, label


def test_the_client_decides_no_finalisation_eligibility():
    lowered = APP_JS.lower()
    for word in (
        "dates_not_reviewed",
        "month_already_finalised",
        "over_coverage_not_acknowledged",
        "calendar_conflict_not_acknowledged",
        "operational_masters_missing",
        "correction_reason_required",
        "month_is_finalised_and_immutable",
    ):
        assert word not in lowered, word
    # No eligibility rule, no fingerprint comparison: the server answers both.
    assert not re.search(r"eligible\s*=", APP_JS)
    assert "fingerprint ===" not in APP_JS


def test_an_acknowledgement_is_sent_back_exactly_as_it_arrived():
    assert "value: item.token" in APP_JS
    assert "node.value" in APP_JS


def test_an_issued_version_offers_its_own_exports():
    # Each link names the version on screen, so viewing an older version downloads and
    # prints that version rather than whatever is in force today.
    assert "/api/finalisations/${record.finalisation_id}/export.xlsx" in APP_JS
    assert "/finalisations/${record.finalisation_id}/print" in APP_JS
    assert "Download Excel (.xlsx)" in APP_JS
    assert "Print / Save as PDF" in APP_JS
    # Exporting is a read: nothing about it writes, corrects or reopens anything.
    lowered = APP_JS.lower()
    assert "export" in lowered and "/corrections" not in APP_JS.split("export-actions")[1][:800]
