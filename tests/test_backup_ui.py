"""The backup panel asks and shows; it decides nothing.

These are reading tests over the two files the browser actually loads, in the same spirit
as the register's own UI tests: they pin that the panel exists, that it talks to the
routes that exist, that the destructive step is guarded by the server's own word rather
than a word the page invented, and that no schema, capacity or scheduling rule has been
copied into the client.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "dailyworkreg" / "interfaces"
PAGE = STATIC / "templates" / "index.html"
SCRIPT = STATIC / "static" / "app.js"


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def backup_script() -> str:
    """Only the panel's own code: the rest of the page may legitimately name columns."""

    source = script()
    start = source.index("// --- backup and restore")
    return source[start: source.index("// --- start ---")]


def panel() -> str:
    markup = page()
    start = markup.index('id="view-backup"')
    end = markup.index("</section>", start)
    return markup[start:end]


def test_the_backup_panel_is_reachable_from_the_tabs():
    assert 'data-view="backup"' in page()
    assert 'id="view-backup"' in page()
    assert "backup: loadBackup," in script()


def test_the_panel_offers_the_three_steps_and_no_others():
    markup = panel()
    for control in (
        'id="backup-create"',
        'id="restore-file"',
        'id="restore-validate"',
        'id="restore-word"',
        'id="restore-run"',
    ):
        assert control in markup, control
    # Nothing about generating, masters or finalising belongs on this page.
    assert "generate" not in markup.lower()
    assert "finalis" not in markup.lower()


def test_the_panel_talks_to_the_routes_that_exist():
    source = script()
    assert "api('/api/backups', { method: 'POST' })" in source
    assert "'/api/backup-validation'" in source
    assert "api('/api/restore', {" in source
    assert "restore_token: restoreState.token" in source
    assert "confirmation: word" in source


def test_the_replacement_is_not_offered_until_the_server_accepts_the_file():
    source = script()
    validate = source[source.index("async function validateRestore"):]
    validate = validate[: validate.index("async function runRestore")]
    assert "hide(preview)" in validate and "hide(confirm)" in validate
    assert "restoreState.token = null;" in validate
    # The confirmation block is only revealed after a successful answer.
    assert validate.index("await api('/api/backup-validation'") < validate.index(
        "confirm.hidden = false"
    )


def test_the_word_to_type_comes_from_the_server_not_from_the_page():
    source = script()
    assert "pending.confirmation_word" in source
    assert "REPLACE" not in source
    assert "REPLACE" not in page()


def test_the_client_holds_no_schema_or_scheduling_rule():
    source = backup_script()
    for forbidden in (
        "schema_version >",  # no version comparison of its own
        "PRAGMA",
        "sqlite",
        "integrity_check(",
        "sequence >=",
        "daily_cap",
        "revisit",
        "allocator",
        "prorate",
    ):
        assert forbidden not in source.lower(), forbidden


def test_the_panel_shows_what_the_server_reported_and_nothing_it_invented():
    source = script()
    for reported in (
        "held.verified",
        "pending.verified",
        "answer.verified",
        "held.contents",
        "pending.contents",
        "pending.warnings",
        "pending.migrations_applied",
    ):
        assert reported in source, reported


def test_a_refused_restore_is_reported_as_refused():
    source = script()
    run = source[source.index("async function runRestore"):]
    run = run[: run.index("document.getElementById('backup-create')")]
    assert "The database was not replaced." in run
    assert "restore_in_progress" in run
    assert re.search(r"showBanner\(banner, 'error'", run)
