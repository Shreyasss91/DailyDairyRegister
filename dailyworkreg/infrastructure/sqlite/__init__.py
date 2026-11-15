"""SQLite access for the canonical master data and generation history."""

from .database import (
    DB_PATH_ENV,
    connect,
    default_data_dir,
    is_inside,
    open_database,
    resolve_db_path,
    transaction,
)
from .generation_store import (
    GenerationStorageError,
    RunSummary,
    insert_run,
    list_runs,
    read_run,
    runs_by_outcome,
)
from .masters import StorageError
from .schema import MIGRATIONS, SCHEMA_VERSION, applied_versions, apply_migrations

__all__ = [
    "DB_PATH_ENV",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "GenerationStorageError",
    "RunSummary",
    "StorageError",
    "applied_versions",
    "apply_migrations",
    "connect",
    "default_data_dir",
    "insert_run",
    "is_inside",
    "list_runs",
    "read_run",
    "runs_by_outcome",
    "open_database",
    "resolve_db_path",
    "transaction",
]
