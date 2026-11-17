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
.\.venv\Scripts\python.exe -m pytest tests/test_export.py tests/test_register_ui.py
```

These modules cover the export/document and frontend contracts, including:

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

The focused tests use the application's test client and test database fixtures. They do not require a separately running application server.

## 4. Suggested validation sequence for an export change

From the repository root:

```powershell
# 1. Focused API/document and UI coverage.
.\.venv\Scripts\python.exe -m pytest tests/test_export.py tests/test_register_ui.py

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

The GitHub commit reports for the monthly export, ZIP export and combined-print correction state that source/tree checks and regression assertions were added, but pytest was not executed in the GitHub-only editing environment. The repository status checks queried for the latest export correction returned no workflow runs or status checks. Run the commands above on the local checkout before treating runtime regression validation as complete.
