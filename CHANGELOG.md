# Changelog

## Unreleased

### Added

- `memory_archive_current` and `recall-cli current` for active, unexpired, non-superseded archive evidence.
- Installer `--dry-run` and `--check` modes for safer profile-specific installs and updates.
- Dogfood archive fixtures for superseded/current rows, expiry filtering, redaction, and export/import roundtrip.

### Changed

- Normal archive search/current views hide superseded observations while export/audit/history preserve them.
- Import path redacts secret-shaped episode, observation, and audit preview content before storage/search.

## 0.2.0 - 2026-05-02

### Added

- Expired observations are excluded from archive search.
- Portable JSON archive export/import in safe merge mode.
- `memory_archive_export`, `memory_archive_import`, and `memory_archive_diagnose` tools.
- Standalone `recall-cli` with `stats`, `search`, `verify`, `diagnose`, `export`, and `import` commands.
- `pyproject.toml` packaging metadata and `recall-cli` entry point.
- GitHub Actions CI for Python 3.10, 3.11, and 3.12.

### Changed

- Archive stats now include `expired_observation_count`.
- Plugin modules support both Hermes package import and standalone CLI/test import.

## 0.1.0 - 2026-05-01

- Initial standalone Hermes Recall memory provider.
- SQLite + FTS5 archive storage.
- Redaction-first observation/episode capture.
- Candidate curation lifecycle.
- Hash-chain audit events and verification.
- Dogfood script and install docs.
