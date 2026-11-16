"""The backup package: a versioned archive holding a manifest and one database payload.

A backup is one file, and everything needed to decide whether to trust it travels inside
it. The archive has exactly two entries with fixed names — a JSON manifest and the SQLite
payload — because an archive whose contents vary is an archive whose reader has to guess,
and guessing at a restore boundary is how the wrong file gets installed.

Nothing user-supplied becomes a path here. The entries read are looked up by their fixed
names, never extracted by their archived path, so a crafted archive cannot write outside the
staging directory however its entries are named. The payload is read through a hard byte
limit and hashed as it is written, so an archive cannot fill the disk or the memory, and a
payload that does not match the digest in its manifest is refused rather than restored.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path

#: Identifies this package format. A reader that does not recognise the string refuses the
#: file rather than interpreting it.
FORMAT_NAME = "dailyworkreg-backup"

#: The current package format. Bumped only if the archive layout changes, which is a
#: different question from the database schema version carried in the manifest.
FORMAT_VERSION = 1

MANIFEST_NAME = "manifest.json"
PAYLOAD_NAME = "database.sqlite"

#: Ceilings, configurable through the environment so a larger installation can raise them
#: without a code change. Sized for a register database rather than for a data warehouse.
DEFAULT_MAX_PACKAGE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024

PACKAGE_BYTES_ENV = "DAILYWORKREG_BACKUP_MAX_BYTES"
PAYLOAD_BYTES_ENV = "DAILYWORKREG_BACKUP_MAX_PAYLOAD_BYTES"

_CHUNK = 1024 * 1024
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


class PackageError(Exception):
    """A package this reader will not pass on for inspection.

    ``reason`` is a stable token; the application layer turns it into the constraint
    identifier an operator sees, so this module never invents operator wording.
    """

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Limits:
    """How much a package may cost before it is refused."""

    max_package_bytes: int = DEFAULT_MAX_PACKAGE_BYTES
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES

    @classmethod
    def from_environment(cls) -> Limits:
        def read(name: str, fallback: int) -> int:
            raw = os.environ.get(name)
            if not raw:
                return fallback
            try:
                value = int(raw)
            except ValueError:
                return fallback
            return value if value > 0 else fallback

        return cls(
            max_package_bytes=read(PACKAGE_BYTES_ENV, DEFAULT_MAX_PACKAGE_BYTES),
            max_payload_bytes=read(PAYLOAD_BYTES_ENV, DEFAULT_MAX_PAYLOAD_BYTES),
        )


@dataclass(frozen=True)
class Package:
    """A package that passed its structural checks, plus where its payload was written."""

    manifest: dict
    payload: Path
    payload_bytes: int
    payload_sha256: str
    package_bytes: int


def package_filename(created_at: datetime, *, schema_version: int) -> str:
    """A filename an operator can identify, built from facts rather than from input."""

    stamp = created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"daily-dairy-register-backup-{stamp}-schema-{int(schema_version)}.zip"


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_manifest(
    *,
    created_at: datetime,
    schema_version: int,
    payload_bytes: int,
    payload_sha256: str,
    counts: dict[str, int],
) -> dict:
    """The manifest this format version carries, and nothing identifying the machine."""

    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "created_at": created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "application": {
            "name": "DailyDairyRegister",
            "schema_version": int(schema_version),
        },
        "payload": {
            "name": PAYLOAD_NAME,
            "bytes": int(payload_bytes),
            "sha256": payload_sha256,
            "journal_mode": "delete",
        },
        "contents": {key: int(value) for key, value in counts.items()},
    }


def write_package(
    *,
    snapshot_path: Path,
    manifest: dict,
    target: Path,
    compression: int = zipfile.ZIP_DEFLATED,
) -> Path:
    """Write one archive with exactly the manifest and the payload, in that order."""

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", compression=compression) as archive:
        archive.writestr(
            MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )
        archive.write(snapshot_path, PAYLOAD_NAME)
    return target


def _reject_unsafe_name(name: str) -> None:
    if not _SAFE.match(name) or name in {".", ".."}:
        raise PackageError(
            "unsafe_entry", f"the archive holds an entry named {name!r}, which is not allowed"
        )


def _manifest_from(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict:
    if info.file_size > MAX_MANIFEST_BYTES:
        raise PackageError("manifest_invalid", "the manifest is far larger than a manifest can be")
    raw = archive.read(info)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise PackageError("manifest_invalid", f"the manifest is not readable JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise PackageError("manifest_invalid", "the manifest is not a JSON object")
    return manifest


def _validated_manifest(manifest: dict) -> dict:
    if manifest.get("format") != FORMAT_NAME:
        raise PackageError(
            "format_unknown",
            "this file is not a DailyDairyRegister backup package",
        )
    version = manifest.get("format_version")
    if not isinstance(version, int) or version != FORMAT_VERSION:
        raise PackageError(
            "format_unsupported",
            f"backup format version {version!r} is not one this tool reads",
        )
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str):
        raise PackageError("manifest_invalid", "the manifest carries no creation time")
    try:
        datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise PackageError("manifest_invalid", f"the creation time is not a timestamp: {error}") from error

    application = manifest.get("application")
    if not isinstance(application, dict) or not isinstance(
        application.get("schema_version"), int
    ):
        raise PackageError("manifest_invalid", "the manifest carries no schema version")
    payload = manifest.get("payload")
    if not isinstance(payload, dict):
        raise PackageError("manifest_invalid", "the manifest carries no payload description")
    if payload.get("name") != PAYLOAD_NAME:
        raise PackageError("manifest_invalid", "the manifest names a different payload entry")
    if not isinstance(payload.get("bytes"), int) or payload["bytes"] < 0:
        raise PackageError("manifest_invalid", "the manifest carries no payload length")
    digest = payload.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PackageError("manifest_invalid", "the manifest carries no checksum of the payload")
    return manifest


def _payload_entries(archive: zipfile.ZipFile) -> tuple[zipfile.ZipInfo, zipfile.ZipInfo]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    for name in names:
        _reject_unsafe_name(name)
    if len(names) != len(set(names)):
        raise PackageError("duplicate_entry", "the archive holds the same entry more than once")
    for info in infos:
        if info.is_dir():
            raise PackageError("entry_unexpected", f"the archive holds a directory: {info.filename!r}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise PackageError("entry_unexpected", "the archive holds a symbolic link")
    unexpected = set(names) - {MANIFEST_NAME, PAYLOAD_NAME}
    if unexpected:
        listed = ", ".join(sorted(unexpected))
        raise PackageError("entry_unexpected", f"the archive holds entries it should not: {listed}")
    missing = {MANIFEST_NAME, PAYLOAD_NAME} - set(names)
    if missing:
        listed = ", ".join(sorted(missing))
        raise PackageError("entry_missing", f"the archive is missing {listed}")
    by_name = {info.filename: info for info in infos}
    return by_name[MANIFEST_NAME], by_name[PAYLOAD_NAME]


def _stream_payload(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, limits: Limits
) -> tuple[int, str]:
    declared = int(info.file_size)
    if declared > limits.max_payload_bytes:
        raise PackageError(
            "too_large",
            f"the payload states {declared} bytes, above the {limits.max_payload_bytes} allowed",
        )
    hasher = hashlib.sha256()
    written = 0
    try:
        with archive.open(info) as reader, Path(target).open("wb") as writer:
            while True:
                chunk = reader.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limits.max_payload_bytes:
                    raise PackageError(
                        "too_large",
                        f"the payload is larger than the {limits.max_payload_bytes} bytes allowed",
                    )
                hasher.update(chunk)
                writer.write(chunk)
    except (zipfile.BadZipFile, EOFError, OSError) as error:
        raise PackageError(
            "package_unreadable", f"the archive could not be read: {error}"
        ) from error
    except Exception as error:  # zlib.error and its relatives
        raise PackageError(
            "package_unreadable", f"the archive could not be read: {error}"
        ) from error
    return written, hasher.hexdigest()


def read_package(source: Path, *, workdir: Path, limits: Limits | None = None) -> Package:
    """Open a package, check its shape and checksum, and write the payload to ``workdir``.

    The payload is written under a fixed name inside the caller's staging directory. No
    archived path is used, so nothing an archive claims can choose where a file lands.
    """

    source = Path(source)
    limits = limits or Limits.from_environment()
    try:
        package_bytes = source.stat().st_size
    except OSError as error:
        raise PackageError("package_unreadable", f"the package cannot be read: {error}") from error
    if package_bytes > limits.max_package_bytes:
        raise PackageError(
            "too_large",
            f"the package is {package_bytes} bytes, above the {limits.max_package_bytes} allowed",
        )

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    payload_path = workdir / PAYLOAD_NAME

    try:
        with zipfile.ZipFile(source) as archive:
            manifest_info, payload_info = _payload_entries(archive)
            manifest = _validated_manifest(_manifest_from(archive, manifest_info))
            written, digest = _stream_payload(archive, payload_info, payload_path, limits)
    except zipfile.BadZipFile as error:
        raise PackageError(
            "package_unreadable", f"the file is not a readable archive: {error}"
        ) from error
    except PackageError:
        raise
    except (OSError, ValueError) as error:
        raise PackageError("package_unreadable", f"the package could not be read: {error}") from error

    declared = manifest["payload"]
    if written != declared["bytes"]:
        raise PackageError(
            "checksum_mismatch",
            f"the payload is {written} bytes where the manifest says {declared['bytes']}",
        )
    if digest != declared["sha256"]:
        raise PackageError(
            "checksum_mismatch",
            "the payload does not match the checksum in the manifest",
        )
    return Package(
        manifest=manifest,
        payload=payload_path,
        payload_bytes=written,
        payload_sha256=digest,
        package_bytes=package_bytes,
    )


def manifest_summary(manifest: dict) -> dict:
    """The non-secret facts an operator needs to recognise a backup before restoring it."""

    application = manifest.get("application") or {}
    payload = manifest.get("payload") or {}
    return {
        "format": manifest.get("format"),
        "format_version": manifest.get("format_version"),
        "created_at": manifest.get("created_at"),
        "schema_version": application.get("schema_version"),
        "application": application.get("name"),
        "payload_bytes": payload.get("bytes"),
        "payload_sha256": payload.get("sha256"),
        "contents": dict(manifest.get("contents") or {}),
    }


def iterate_entries(path: Path) -> Iterator[str]:
    """The entry names in an archive, for diagnostics that must not extract anything."""

    with zipfile.ZipFile(Path(path)) as archive:
        for info in archive.infolist():
            yield info.filename
