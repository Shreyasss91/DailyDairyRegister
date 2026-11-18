# Local development and application operation

This guide describes the supported Windows PowerShell workflow for cloning, installing, starting and updating DailyDairyRegister. The application remains a local, single-operator tool; it is not a network service.

## 1. Prerequisites

- Windows 10 or Windows 11.
- Git for Windows.
- Python **3.11 or newer**, available through the Python Launcher (`py`) or as `python`.
- PowerShell.

Check the available Python installations:

```powershell
py -0p
py -3.11 --version
git --version
```

The Python 3.11 minimum is required by the current source (including use of `datetime.UTC`). The repository's `pyproject.toml` declares this minimum.

## 2. First-time checkout and installation

Choose a parent folder for the checkout. The following example uses `E:\Projects`; replace it with the folder you use.

```powershell
New-Item -ItemType Directory -Force E:\Projects | Out-Null
Set-Location E:\Projects

git clone https://github.com/Shreyasss91/DailyDairyRegister.git
Set-Location .\DailyDairyRegister
```

Create a project-local virtual environment and install the project with its regression-test dependencies:

```powershell
py -3.11 -m venv .venv

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

The editable install registers the `dailyworkreg` command in the virtual environment. The `.venv/` directory is ignored by Git; it is local tooling, not application source or operational data.

For an application-only installation without test tools, run this from the repository root instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
``` Install the `test` extra before running pytest.

Install the local PDF layout engine once per computer. The application writes PDF files itself with a Chromium that stays on this machine:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

The browser is downloaded to the user's own profile folder, not into the checkout or the virtual environment, so a `git pull` never removes it. This step is required for the PDF downloads only. Everything else works without it, and a PDF request refuses with a sentence naming this command rather than producing a file that will not open. Reinstalling or upgrading Python does not require repeating it; deleting the profile folder does.

## 3. Start the application — first and subsequent runs

From the repository root, start the local server:

```powershell
.\.venv\Scripts\dailyworkreg.exe
```

The default address is:

`http://127.0.0.1:8010`

Keep that PowerShell window open while using the application. Stop the server with `Ctrl+C`.

Check the health endpoint from a second PowerShell window:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/health"
```

The application binds to the loopback address `127.0.0.1` by design. It has no authentication because it is intended to be reachable only from the same machine. Do not change this boundary, expose the port to the LAN, or place the app behind a public tunnel or reverse proxy.

### Subsequent starts without a code update

Open PowerShell, change to the existing checkout, and run the launcher:

```powershell
Set-Location E:\Projects\DailyDairyRegister
.\.venv\Scripts\dailyworkreg.exe
```

You do not need to recreate the virtual environment or database on every start.

### Pull the latest code and then start

First inspect the working tree. Do not overwrite local modifications or discard work simply to pull:

```powershell
Set-Location E:\Projects\DailyDairyRegister
git status --short
```

When the working tree is clean and you intend to update the `main` checkout:

```powershell
git switch main
git pull --ff-only origin main

.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\dailyworkreg.exe
```

Re-running the editable install checks the current manifest and installs any newly required or missing dependencies. It does not delete the virtual environment or application database. If Git reports local modifications or the fast-forward pull is refused, stop and reconcile the branch/worktree rather than using `reset --hard`, force-pull or clean commands.

If a pull changes `pyproject.toml`, the editable-install step is required before launching. Running it after each pull is a safe, straightforward update procedure.

## 4. Application options

The launcher supports these options:

```powershell
# Use the default database and port.
.\.venv\Scripts\dailyworkreg.exe

# Print the database path that would be used, then exit.
.\.venv\Scripts\dailyworkreg.exe --print-db

# Use another local port.
.\.venv\Scripts\dailyworkreg.exe --port 8011

# Use an explicitly selected SQLite database.
.\.venv\Scripts\dailyworkreg.exe --db "D:\DailyDairyRegisterData\dailyworkreg.db"
```

The server remains bound to `127.0.0.1`; the port option does not expose it to other machines.

## 5. SQLite database location and selection

The database is deliberately stored outside the Git working tree. On a normal Windows installation, the default is:

```text
%LOCALAPPDATA%\DailyDairyRegister\dailyworkreg.db
```

For example, if `LOCALAPPDATA` is `C:\Users\<user>\AppData\Local`, the database is:

```text
C:\Users\<user>\AppData\Local\DailyDairyRegister\dailyworkreg.db
```

The application resolves the database path in this order:

1. The explicit `--db` command-line argument.
2. The `DAILYWORKREG_DB` environment variable.
3. The platform default data directory.

On Windows the platform default uses `LOCALAPPDATA`. If that variable is unavailable, the code falls back to `XDG_DATA_HOME\dailyworkreg` when configured, otherwise `~/.local/share/dailyworkreg`.

Print the effective path without starting the web server:

```powershell
.\.venv\Scripts\dailyworkreg.exe --print-db
```

Select a database through an environment variable for the current PowerShell session:

```powershell
$env:DAILYWORKREG_DB = "D:\DailyDairyRegisterData\dailyworkreg.db"
.\.venv\Scripts\dailyworkreg.exe
```

An explicit `--db` argument takes precedence over that environment variable:

```powershell
.\.venv\Scripts\dailyworkreg.exe --db "D:\DailyDairyRegisterData\dailyworkreg.db"
```

### First database creation and migrations

When the application first opens its database, it creates the parent directory and SQLite file if needed, applies pending schema migrations, and seeds approved baseline identities idempotently. Subsequent starts reuse the same database and migration history. The default database is not recreated just because the code is pulled or the virtual environment is rebuilt.

Before running a new application revision against an existing operational database, create and verify a backup using the application's backup workflow. The database uses SQLite WAL journaling; do not copy only the `.db` file while the application is running. Stop the application before any manual file-level copy, and keep database files and backups outside this public Git repository.

## 6. Dependency management

`pyproject.toml` is the single source of truth for runtime dependencies and the optional `test` extra.

- Runtime only: `python -m pip install -e .`
- Runtime plus regression tooling: `python -m pip install -e ".[test]"`

There is intentionally no second hand-maintained `requirements.txt` list. The manifest uses compatible version ranges; it is not an exact lock file. A future lockfile/tooling decision can be made separately if bit-for-bit dependency resolution is required.

## 7. Regression testing

The test environment is installed by the `.[test]` extra. Run tests with the interpreter from the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

For focused export and user-interface regression tests, see [Regression testing](REGRESSION_TESTING.md).

## 8. Troubleshooting

- **`dailyworkreg.exe` is missing:** Run `.\.venv\Scripts\python.exe -m pip install -e ".[test]"` from the repository root and check that `.venv\Scripts\dailyworkreg.exe` exists.
- **Python version is too old:** Check `py -0p` and create the environment with Python 3.11 or newer. Do not try to fix this by editing the project's `requires-python` declaration.
- **Port 8010 is already in use:** Start with `--port 8011` and open `http://127.0.0.1:8011`.
- **The application appears to use a different database:** Run `dailyworkreg.exe --print-db` and inspect `DAILYWORKREG_DB` and any `--db` argument.
- **A PDF download says the layout engine is not installed:** Run `.\.venv\Scripts\python.exe -m playwright install chromium` once, then reload the page. The printed page remains available in the meantime and can be saved from the browser's own print dialog.
- **Git shows local source changes after an update:** Stop before pulling further. Review and preserve those changes; do not use destructive reset/clean commands as a routine update method.
