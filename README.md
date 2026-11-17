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

4. Start the application:

   ```powershell
   .\.venv\Scripts\dailyworkreg.exe
   ```

5. Open [http://127.0.0.1:8010](http://127.0.0.1:8010).

The first launch creates the default SQLite database when needed and applies pending schema migrations. The database is stored outside the Git checkout.

## Developer and operator guides

- [Local development, first-time setup, subsequent updates, application startup and database location](documentation/LOCAL_DEVELOPMENT.md)
- [Regression tests, focused export/UI tests and manual smoke checks](documentation/REGRESSION_TESTING.md)
- [Product requirements baseline](documentation/PRODUCT_REQUIREMENTS_BASELINE.md)
- [Change history](CHANGELOG.md)
- [Repository implementation contract](CLAUDE.md)

## Dependency management

`pyproject.toml` is the canonical Python dependency and packaging manifest. Runtime dependencies are installed by `pip install -e .`; the optional `test` extra adds pytest and HTTPX for regression testing. Do not maintain a second manually edited dependency list.

The declared version ranges are compatibility constraints, not an exact dependency lock. See the local development guide for installation and update instructions.
