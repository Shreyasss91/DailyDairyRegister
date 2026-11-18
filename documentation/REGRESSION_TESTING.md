# Regression testing

This guide defines the repository's local regression-test workflow, including the monthly and bulk document-export capability.

## 1. Install the test environment

From the repository root in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

The `test` optional dependency group in `pyproject.toml` installs pytest and HTTPX. Runtime dependencies are installed by the same editable project installation.

Use the virtual-environment interpreter explicitly so the test command cannot accidentally run against a different Python installation:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pytest --version
```

## 2. Run the full regression suite

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The repository's `pytest.ini` identifies `tests/` as the test directory and adds the repository root to the Python import path. Run pytest from the repository root.

A non-zero exit code means the validation gate failed. Inspect the failing test and its fixture/evidence; do not weaken, skip or delete a contract test solely to obtain a green run.

## 3. Focused tests for monthly and bulk exports

Run the directly relevant export and UI tests first:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_export.py tests/test_pdf_export.py tests/test_register_ui.py tests/test_bulk_finalisation.py tests/test_activity_allocation.py tests/test_register_wording.py
```

These modules cover the export/document, PDF, bulk-finalisation and frontend contracts, including:

- XLSX content and issued-version identification.
- Print-ready monthly document content and navigation.
- Exporting a specific immutable finalisation rather than a mutable draft or a different/latest version.
- Keeping earlier issued versions exportable after a correction.
- Refusing unknown finalisation IDs and unfinalised revisions.
- Ensuring export reads do not alter the stored finalisation or operational tables.
- A ZIP containing one workbook per included issued month, a combined print-ready HTML document and a README.
- Omitting months without an issued finalisation and refusing a register with no issued months.
- Showing direct monthly export links only when the month has a latest issued finalisation.
- Showing the register-level `Download all issued months` action only when at least one month is issued.
- Keeping downloaded combined HTML self-contained while retaining the online single-month return link.
- One PDF per issued month in its own package, with a README naming each month, version and finalisation, and the months omitted for having no issued finalisation.
- One workbook per issued month in its own package, using the same `Register` and `Metadata` sheets as the single-month download.
- Refusing a PDF request with a stable setup message when the local layout engine is missing, and refusing a failed render without sending a partial package.
- The bulk close: the target set derived from the revision, the preflight writing nothing, one blocked month refusing the whole act, a forced failure rolling back the months already written, and the page refusing a second submission.
- Register wording: the three languages and that the choice is recorded with the generation and frozen into the closed month, an absent choice read as the platform default rather than a remembered one, an unknown language refused at both the request and the domain boundary, a Kannada register keeping English where no Kannada is stored, a weekly off, holiday or leave date classified in words beside its stored reason instead of a bare `Calendar:` prefix, a reason carried twice printed once, a visit reading as the work done at the installation with the sentence chosen by the asset kind the roster recorded, a record that does not say which kind it visited reading a neutral sentence, and the language and asset kinds staying outside the month fingerprint so a month closed before they existed is still recognised as itself.
- Regular activities on working days: the configured count on every eligible day and none on a Sunday or a recorded holiday, the catalogue's monthly maximum, monthly minimum, repeat gap (across a month boundary) and weekday restriction as hard constraints, a weight unable to override a maximum, a catalogue that cannot fill a day or a month refused with the constraint and date named, determinism from the seed, the count and the catalogue surviving the request snapshot, a schema-1 snapshot still reading as the visits-only request it recorded, and the placed activities reaching the issued page and workbook with their Kannada text.

The PDF module runs its packaging, refusal and read-only tests against a stand-in renderer, so they pass on a machine without Chromium. The two tests that assert a real `%PDF` file are skipped unless a local browser is installed; run `python -m playwright install chromium` before treating PDF rendering as verified.

The focused tests use the application's test client and test database fixtures. They do not require a separately running application server.

## 4. Suggested validation sequence for an export change

From the repository root:

```powershell
# 1. Focused API/document, PDF, bulk-finalisation and UI coverage.
.\.venv\Scripts\python.exe -m pytest tests/test_export.py tests/test_pdf_export.py tests/test_register_ui.py tests/test_bulk_finalisation.py tests/test_activity_allocation.py tests/test_register_wording.py

# 2. Full regression suite.
.\.venv\Scripts\python.exe -m pytest
```

Both commands must exit successfully before claiming the export change has passed these gates. Record the actual command, exit status, test count and failures (if any) in the act report and commit message. Do not report a test as passed if it was not executed.

## 5. Manual local smoke check

Automated tests are the primary regression gate. A manual smoke check can additionally confirm that the installed console entry point starts the current checkout.

In PowerShell window 1:

```powershell
.\.venv\Scripts\dailyworkreg.exe
```

In PowerShell window 2:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/health"
```

Confirm the health response reports `ok = True`, then open `http://127.0.0.1:8010` in the browser. Stop the server with `Ctrl+C` in window 1.

For a manual export workflow check, use a disposable/test database selected with `--db`; do not create synthetic test records in the operator's normal operational database. Create and review a test draft, issue the month through the normal finalisation process, then verify the monthly actions and bulk ZIP contents. A draft or merely reviewed month must not appear as an issued export.

## 6. Validation and data-safety rules

- Do not run manual test scenarios against the operator's normal database.
- Back up an existing operational database before testing a new application revision against it.
- Do not commit database files, uploaded office data, generated registers, backups, credentials or environment files.
- Keep the server on loopback only.
- Preserve the distinction between generated drafts, reviewed dates, locked dates and issued finalisations.
- Treat an export as a read of the stored immutable issued snapshot; do not introduce generation, recalculation or master-data lookups into an export path.
- Do not modify expected results merely to make the test pass. A changed contract requires explicit owner authorization and corresponding implementation/tests/documentation.

## 7. Current verification boundary

This repository has no continuous-integration runner, so the only verification gate is the local suite. Run it on the checkout you are about to commit: the counts quoted in the changelog were produced by the commands above on Windows with the project's own virtual environment.

Two boundaries are worth stating plainly rather than discovering later:

- The PDF tests that assert a real PDF file need a locally installed Chromium. Without it they skip, and a green run then says nothing about PDF rendering. The packaging and refusal tests always run.
- XLSX bytes are not guaranteed identical across a long interval, because the archive records a modification time. Compare workbooks cell by cell rather than as bytes.
