"""SQLite connection handling, location resolution and transactions.

Operational data lives outside the Git working tree by default, because a
database inside the checkout is a database that a `git clean` can delete and that
a clone would ship somebody else's records. The location is resolved once, and
every caller receives a connection configured the same way.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: Environment variable that overrides the database location. Used by the tests,
#: the smoke harness and anyone who wants the file somewhere specific.
DB_PATH_ENV = "DAILYWORKREG_DB"

#: Subdirectory created under the platform data directory.
APP_DIR_NAME = "DailyDairyRegister"
DB_FILE_NAME = "dailyworkreg.db"


def default_data_dir() -> Path:
    """The platform's per-user application data directory.

    Chosen so that the database never lands inside a checkout, which is what
    "outside the Git working tree by default" requires.
    """

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / APP_DIR_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "dailyworkreg"
    return Path.home() / ".local" / "share" / "dailyworkreg"


def resolve_db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Where the database lives, in order: argument, environment, default."""

    if explicit is not None:
        return Path(explicit)
    from_env = os.environ.get(DB_PATH_ENV)
    if from_env:
        return Path(from_env)
    return default_data_dir() / DB_FILE_NAME


def is_inside(path: Path, directory: Path) -> bool:
    """Whether ``path`` lies within ``directory``, resolved."""

    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def connect(path: Path) -> sqlite3.Connection:
    """Open a connection configured for this application.

    Foreign keys are enforced per connection in SQLite and default to off, so
    they are switched on here rather than left to whoever remembers. Row access is
    by name because every repository returns mappings, and the isolation level is
    left explicit so a caller knows when a transaction has actually begun.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(path), isolation_level=None, detect_types=0, check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one write transaction, rolling back on any exception.

    Re-entrant. A caller composing a repository write inside a larger unit of work
    gets a savepoint rather than a second ``BEGIN``, because SQLite refuses the
    latter outright and a nested repository call is a normal thing to write.

    Kept short on purpose. A generation run reads its inputs, releases the
    connection, runs the allocator with no transaction open, and only then opens
    one of these to persist the outcome. Holding a write transaction across a
    multi-second allocation would block every other writer for no benefit.
    """

    outermost = not connection.in_transaction
    if outermost:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute("SAVEPOINT nested_write")
    try:
        yield connection
    except BaseException:
        if outermost:
            connection.execute("ROLLBACK")
        else:
            connection.execute("ROLLBACK TO nested_write")
            connection.execute("RELEASE nested_write")
        raise
    if outermost:
        connection.execute("COMMIT")
    else:
        connection.execute("RELEASE nested_write")


def open_database(path: Path | None = None) -> sqlite3.Connection:
    """Open the database and bring its schema up to date.

    Importing the schema module here rather than at module scope keeps the
    dependency one-directional: the schema needs nothing from this module except a
    connection, which it is handed.
    """

    from .schema import apply_migrations

    database_path = resolve_db_path(path)
    connection = connect(database_path)
    apply_migrations(connection)
    return connection
