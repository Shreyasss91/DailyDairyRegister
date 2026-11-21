# DailyDairyRegister

DailyDairyRegister is a local-first application for preparing, reviewing and issuing the office Daily Dairy Register. It uses a Python/FastAPI backend, a vanilla HTML/CSS/JavaScript interface and SQLite for local persistence.

The application is designed for one operator on the local machine. The server binds to `127.0.0.1` and must not be exposed to a LAN, public interface, reverse proxy or tunnel.

## Get started on Windows

1. Install Python 3.11 or newer and Git.
2. Clone this repository and open PowerShell in the repository root.
3. Create the virtual environment and install the application plus test dependencies:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -e ".[test]"
   ```

4. Install the local PDF layout engine once. The application writes PDFs itself, using a
   Chromium that stays on this computer; nothing is sent anywhere:

   ```powershell
   .\.venv\Scripts\python.exe -m playwright install chromium
   ```

5. Start the application:

   ```powershell
   .\.venv\Scripts\dailyworkreg.exe
   ```

6. Open [http://127.0.0.1:8010](http://127.0.0.1:8010).

The first launch creates the default SQLite database when needed and applies pending schema migrations. The database is stored outside the Git checkout.

## Operator guide

- [The DailyDairyRegister wiki](documentation/USER_WIKI.md) - installing it, starting it, what
  every screen does, the order the work is done in, what each message means, and how to back the
  records up.

## Developer and operator guides

- [Local development, first-time setup, subsequent updates, application startup and database location](documentation/LOCAL_DEVELOPMENT.md)
- [Regression tests, focused export/UI tests and manual smoke checks](documentation/REGRESSION_TESTING.md)
- [Product requirements baseline](documentation/PRODUCT_REQUIREMENTS_BASELINE.md)
- [Change history](CHANGELOG.md)
- [Repository implementation contract](CLAUDE.md)

## Starting a new office

One installation holds one office. Handing the computer to a different office is done from
the **Backup** tab, under `Start a new office`, and there are two scopes:

- **Clear everything** leaves the tool exactly as it was on its first start.
- **Clear everything, but keep the calendar** keeps the Karnataka General Holiday list, the
  leave types and the weekly-off rules, because any office in the state works from the same
  ones, and removes every other record.

Each scope has its own button and its own confirmation: type `RESET` in the box beside the
one you mean. Before anything is removed, the page counts what the selected scope would take
and a backup of the database as it stood is taken and offered for download; after the reset a
recovery copy of the previous database is also left beside the live one, and its name is
shown. Export files already written to disk are not touched. The next start does not put the
previous office's areas, feeders or transformers back: a fresh start holds no office-specific
master data at all, and only the records you import for the new office appear.

## Dependency management

`pyproject.toml` is the canonical Python dependency and packaging manifest. Runtime dependencies are installed by `pip install -e .`; the optional `test` extra adds pytest and HTTPX for regression testing. Do not maintain a second manually edited dependency list.

`playwright` is a runtime dependency because the application writes its own PDF files. The Python package is installed with everything else; the Chromium it drives is a separate download that stays outside the virtual environment and outside this repository, and it is installed once per computer with `python -m playwright install chromium`. Skip that step and every other feature still works: the PDF downloads refuse with a sentence naming the step, and the printed page remains available in the browser.

The declared version ranges are compatibility constraints, not an exact dependency lock. See the local development guide for installation and update instructions.

## Preparing the printed registers

An issued month downloads as a PDF that ends with an issued-version
identification page. That page is the archive's proof of what was issued and it
stays in the per-month download and in the PDF package, but it is not part of the
register the office prints, so there are two ways to get a document without it.

The month panel's `Single PDF with no metadata` writes one PDF holding every
issued month, in month order, with each month's identification page left out. It
needs no folder, no command and no second step - one download, straight from the
browser.

To prepare months that were already exported, put those PDFs in one folder and
point the print command at that folder. It drops each file's last page and joins
the rest in file-name order:

```powershell
.\.venv\Scripts\dailyworkreg-print.exe "C:\path\to\print\before"
```

The folder is the only input. The document is written to an `after` folder beside
it, named after the months it holds - `daily-dairy-register-2025-04-to-2026-10.pdf`
for example - and the folder that was read is left exactly as it was. A PDF of a
single page is named rather than merged, because dropping its last page would
leave nothing of that month. `python -m dailyworkreg.interfaces.print_pdfs <folder>`
runs the same command without the launcher.

Both leave out the identification page by the same rule, which lives in one place
(`dailyworkreg/infrastructure/rendering/pdf.py`), so a month that is pressed from
the button and the same month pressed from the folder produce the same register.
