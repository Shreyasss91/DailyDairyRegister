"""Backup and restore mechanics that sit below the application's decisions.

``package`` owns the archive format and its limits; ``replace`` owns putting a validated
database in place of the live one, which is the only place in this project that replaces a
file the application is running from.
"""

from .package import (
    FORMAT_NAME,
    FORMAT_VERSION,
    MANIFEST_NAME,
    PAYLOAD_NAME,
    Limits,
    Package,
    PackageError,
    build_manifest,
    manifest_summary,
    package_filename,
    read_package,
    sha256_file,
    write_package,
)
from .replace import (
    ReplacementError,
    RollbackCopy,
    clear_sidecars,
    install_database,
    put_back,
    rollback_copy,
    staged_path,
    staged_pattern,
)

__all__ = [
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "MANIFEST_NAME",
    "PAYLOAD_NAME",
    "Limits",
    "Package",
    "PackageError",
    "ReplacementError",
    "RollbackCopy",
    "build_manifest",
    "clear_sidecars",
    "install_database",
    "manifest_summary",
    "package_filename",
    "put_back",
    "read_package",
    "rollback_copy",
    "sha256_file",
    "staged_path",
    "staged_pattern",
    "write_package",
]
