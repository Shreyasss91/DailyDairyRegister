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

## 3. Focused tests for monthly and bulk exports, and for handing the tool to a new office

Run the directly relevant export and UI tests first:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_export.py tests/test_pdf_export.py tests/test_register_ui.py tests/test_bulk_finalisation.py tests/test_activity_allocation.py tests/test_register_wording.py tests/test_daily_counts.py tests/test_allocation_performance.py tests/test_generation_progress.py tests/test_generation_browser.py tests/test_per_kind_caps.py tests/test_commissioned_on.py tests/test_print_pdfs.py tests/test_print_document.py tests/test_reset.py tests/test_protection_triggers.py tests/test_dtc_identifier.py
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
- The print command's page arithmetic, derived destination and output name, refusal of an empty folder, and the export it reads being left unchanged.
- One PDF holding every issued month in month order with each month's identification page left out, named after the months it holds, one month named as itself rather than as a range, the identification page still present in the single-month download, and a register with nothing issued refused.
- Showing the register-level `Download all issued months` action only when at least one month is issued.
- Keeping downloaded combined HTML self-contained while retaining the online single-month return link.
- One PDF per issued month in its own package, with a README naming each month, version and finalisation, and the months omitted for having no issued finalisation.
- One workbook per issued month in its own package, using the same `Register` and `Metadata` sheets as the single-month download.
- Refusing a PDF request with a stable setup message when the local layout engine is missing, and refusing a failed render without sending a partial package.
- The bulk close: the target set derived from the revision, the preflight writing nothing, one blocked month refusing the whole act, a forced failure rolling back the months already written, and the page refusing a second submission.
- The allocator's cost: the indexed revisit-gap test answering exactly what the original scan answers, a later visit in the period still closing the earlier dates, a whole month allocating to the same visits when the reference scan does the work, and the gap question being asked once per viable check rather than once per placed visit, so a speed-up cannot arrive by choosing a different month.
- Register wording: the three languages and that the choice is recorded with the generation and frozen into the closed month, an absent choice read as the platform default rather than a remembered one, an unknown language refused at both the request and the domain boundary, a Kannada register keeping English where no Kannada is stored, a weekly off, holiday or leave date classified in words beside its stored reason instead of a bare `Calendar:` prefix, a reason carried twice printed once, a visit reading as the work done at the installation with the sentence chosen by the asset kind the roster recorded, a record that does not say which kind it visited reading a neutral sentence, and the language and asset kinds staying outside the month fingerprint so a month closed before they existed is still recognised as itself.
- The progress of a long generation: the allocator's attempt hook and the cycle runner's
  per-month hook reporting in order, the same month and the same three-month plan produced
  whether the run is watched or silent, the percentage rising through reading, placing,
  activities and saving to exactly 100, a finished run keeping its result for a late poll, a
  refusal ending as a failed run with the office's own sentence, one run at a time with the
  run in progress named, a report arriving after the end being ignored, the oldest records
  dropped, the watched route answering exactly what the direct route answers, and the page
  drawing the server's percentage into a bar that is still there for a second run.
- The daily numbers a working day is built from: the activity range's floor met on
  every eligible day and its ceiling never exceeded, the day-to-day spread the draw
  produces, an equal-ended range reproducing the flat count an earlier request asked for,
  a reversed or negative range refused at both the domain and the request boundary, a
  ceiling wider than the catalogue drawn under rather than demanded, an outstanding
  monthly minimum borrowing a day's headroom but never passing the ceiling, each visit
  kind's own ceiling reaching the request and the snapshot, an unstated ceiling keeping the
  platform default and no whole-date total being imposed, a negative ceiling and three
  ceilings of zero refused, a month that was refused generating once its kind's ceiling is
  raised, a capacity refusal naming the kind and the ceiling that would fit the month, the
  range and the ceilings reaching the route and being echoed in its answer, and the generate
  form offering both ends of the range and a ceiling for each kind while the client sends
  them.
- One ceiling per kind, and nothing else counted against them: a date holding each kind up to
  its own ceiling, the day capacity being their sum, a stated total acting as one further
  ceiling over all kinds, the day-level and aggregate refusals of each kind naming that kind,
  a committed date judged against its kind's ceiling, a Monday carrying both its safety
  meeting and its full day of visits, the meeting not consuming an activity slot, the meeting
  giving no credit towards the activity minimum, and a snapshot written before the kinds were
  separated reading its total as a whole-date ceiling with the unnamed kinds at their
  defaults.
- The date a DTC was commissioned: the supplied `Date of commision` column read
  into a canonical date with the source text kept beside it, a blank cell read as no
  restriction rather than as "not yet commissioned", a `#N/A` placeholder and an
  unreadable value reported and carried without a date, a day-first slash date read the
  way every other date in the platform is read, the date surviving acceptance, a
  re-import, the source record and the schema migration, an existing row keeping no date
  and therefore no restriction, an asset visitable only strictly after its date, the
  commissioning day itself refused as a visit date, the following day accepted, a
  committed visit before the date reported rather than dropped, a month ending before the
  date proved infeasible with the date named, a date late in the month leaving too few
  dates for the visits owed, the allocator never offering such a date, a coverage cycle
  dealing an asset to a month it exists in and excluding one commissioned after the
  cycle with its reason, the distribution staying exactly even when no asset carries a
  date, a generated draft never recording a visit before the transformer existed, a
  transformer the range could not cover at all being reported beside the draft, and the
  request snapshot keeping each roster date while a snapshot written before the field
  existed reads without one.
- The roster file deciding what goes into the masters: a whole 119-row file staged and judged
  with 113 rows included and 6 excluded by the class column, a stage that changes no canonical
  row, acceptance storing the supplied fields verbatim beside the canonical record, a row whose
  feeder name the master does not carry accepted with a warning and **no** canonical feeder
  rather than refused, a batch staged before the feeder master accepted unassigned so the gap
  is visible on the readiness screen, the supplied feeder text kept beside the accepted record
  whichever way it resolves, re-uploading the same file updating rather than duplicating, and
  the acceptance result carrying no count of overrides applied because no assignment table
  exists any more.
- Regular activities on working days: the configured count on every eligible day and none on a Sunday or a recorded holiday, the catalogue's monthly maximum, monthly minimum, repeat gap (across a month boundary) and weekday restriction as hard constraints, a weight unable to override a maximum, a catalogue that cannot fill a day or a month refused with the constraint and date named, determinism from the seed, the count and the catalogue surviving the request snapshot, a schema-1 snapshot still reading as the visits-only request it recorded, and the placed activities reaching the issued page and workbook with their Kannada text.

- Handing the installation to a new office: the preview counting what a reset would remove
  and changing nothing itself, a fresh installation already holding the seeded identities it
  would clear, a wrong or missing confirmation word refused with the database and the folder
  untouched, the word accepted in any case and with spaces, an unknown scope refused by name,
  the full scope emptying every data table including the calendar and the weekly-off rules,
  the calendar scope keeping the holiday rows and the leave types whole while still removing
  the office's own leave records, dated events and overrides, a changed weekly-off rule
  surviving that scope, the seed not running again after the next start because the marker
  records the operator's decision, a forced seed still writing nothing but leave types, a backup of what was removed that still holds
  the previous office when re-validated, a recovery copy of the database as it stood, no
  staging file or sidecar left behind, the replacement verified for integrity and references,
  the exclusive barrier actually entered and a reset refused while a replacement holds it, a
  poll arriving afterwards not answered with a draft no table holds, the export files already
  written being left alone, and the page offering each scope as its own confirmation and its
  own button.

- A DTC identifier is the office's own code: any letter prefix and number accepted,
  including `SCE142` and a hyphenated series; a placeholder, a bare number or a name in the
  identifier column refused and named; and the file's own `Class` column - never the letters
  of a prefix - deciding which records requirements §8 excludes.
- The guards that keep a closed month closed: a database that lost the three
  `generation_entry` protections refused a backup by name and repaired when it opens, the
  repair touching no row, recorded once, replacing a trigger left behind by older text
  rather than trusting its name, the restored guard actually refusing an insertion, an
  update and a deletion of a closed month while a date outside it stays writable, a
  connection that never repaired still meeting the refusal, and migration 6 installing the
  guards and keeping every row across its own table rebuild.

The PDF module runs its packaging, refusal and read-only tests against a stand-in renderer, so they pass on a machine without Chromium. The two tests that assert a real `%PDF` file are skipped unless a local browser is installed; run `python -m playwright install chromium` before treating PDF rendering as verified.

The focused tests use the application's test client and test database fixtures. They do not require a separately running application server.

The masters the tests point at come from `tests/master_fixtures.py`, not from the application: an office's areas and feeders are its own records and arrive from its own master files, so the application seeds none of them. `seed_office_masters()` writes the fixture's sixteen areas and four feeders (bringing up the application's own leave types with them, as a first start does), and it is idempotent so a test may call it twice. A test that needs a place must therefore create it, which is also what the import workflow makes an operator do.

## 4. Suggested validation sequence for an export change

From the repository root:

```powershell
# 1. Focused API/document, PDF, bulk-finalisation and UI coverage.
.\.venv\Scripts\python.exe -m pytest tests/test_export.py tests/test_pdf_export.py tests/test_register_ui.py tests/test_bulk_finalisation.py tests/test_activity_allocation.py tests/test_register_wording.py tests/test_daily_counts.py tests/test_allocation_performance.py tests/test_generation_progress.py tests/test_generation_browser.py tests/test_per_kind_caps.py tests/test_commissioned_on.py tests/test_print_pdfs.py tests/test_reset.py tests/test_protection_triggers.py tests/test_dtc_identifier.py

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
