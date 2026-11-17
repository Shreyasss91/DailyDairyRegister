# Changelog

All notable repository changes are recorded here, including small fixes, documentation-only edits, configuration changes, tests and repository-governance changes. Entries describe repository facts and do not imply that planned application capabilities have been implemented.

The repository policy in `CLAUDE.md` requires every change, however small, to have a corresponding entry in this file **in the same commit**. This includes changes to `CLAUDE.md` and to this changelog.

## [Unreleased]

### Added
- **2026-11-18 — Add a standard Python project manifest and Windows application command:** Added `pyproject.toml` as the single canonical declaration of runtime dependencies, optional regression-test dependencies, Python 3.11 minimum, setuptools package discovery, packaged HTML/CSS/JavaScript assets and the `dailyworkreg` console command. This enables a repeatable editable install with `python -m pip install -e ".[test]"` and launches the existing loopback-only server through the declared entry point. Added `.venv/`, build outputs and package metadata to `.gitignore` so a developer environment and packaging artifacts are not staged as application data. No application route, database schema, scheduling policy, operational data, export behavior or runtime host binding changed. The manifest and entry-point declarations were inspected against the current imports and server launcher; package installation, wheel construction and tests are not claimed as executed in the GitHub-only environment.
