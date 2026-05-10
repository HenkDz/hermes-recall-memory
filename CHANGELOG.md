# Changelog

## Unreleased

## 0.3.6 - 2026-05-10

### Fixed

- Align plugin metadata version with the provider/package version.

### Changed

- Document one-command GitHub installation in the README and install guide.

## 0.3.5 - 2026-05-10

### Fixed

- Make optional dashboard backend tests skip cleanly when standalone test environments do not install FastAPI. Core Recall remains dependency-free; dashboard API coverage still runs where FastAPI is available.

## 0.3.4 - 2026-05-10

### Added

- Add dashboard curation filters for fact-only rows, episode hiding, and minimum quality thresholds.
- Add explicit rejected-row promotion override (`allow_rejected=true`) so rejected archive rows cannot be promoted accidentally.
- Add 100k+ archive burn-in verification as an operator release check.

### Changed

- Dashboard curation is denser and safer for review queues, with filter state passed to the plugin API.

## 0.3.3 - 2026-05-10

### Added

- Add explicit `memory_promote_candidate` promotion into built-in Hermes `MEMORY.md` / `USER.md` with dry-run review, confirmation, low-quality blocking, redaction/safety scans, and audit events.
- Add `memory_recall_build_info` for runtime version/schema/capability verification.
- Add `memory_consolidation_apply` for reviewed duplicate rejection under a canonical Recall row.
- Add dashboard curation assets and installer coverage for overview, observation review/search/detail, marking, and promotion flows.

### Changed

- Update README/tool docs to reflect 0.3.3 promotion, build-info, consolidation-apply, and dashboard-curation behavior.

## 0.3.2 - 2026-05-10

### Changed

- Hide low-quality consolidation groups by default so noisy episode transcript labels like `User asked:` do not swamp useful fact/preference consolidation queues.
- Add opt-in low-quality consolidation inspection through `include_low_quality` / `--include-low-quality` and `min_quality_score` / `--min-quality-score`.

## 0.3.1 - 2026-05-10

### Added

- Add `scripts/recall_stress_probe.py` for deterministic isolated stress checks.
- Add deterministic local observation quality ranking via `memory_quality_rank` and `recall-cli rank`.
- Add same-subject consolidation suggestions via `memory_consolidation_suggest` and `recall-cli consolidate`.

### Changed

- Set SQLite WAL connections to `synchronous=NORMAL` for much faster archive writes on sync-expensive filesystems.
- Add observation indexes for current/search supersession filters under larger archives.
- Deduplicate exact built-in memory mirror writes and supersede older same-subject mirrors on replacement.
- Filter prefetch injection to avoid single broad-term archive noise while preserving unique marker hits.

## 0.3.0 - 2026-05-10

### Added

- `memory_archive_current` and `recall-cli current` for active, unexpired, non-superseded archive evidence.
- Installer `--dry-run` and `--check` modes for safer profile-specific installs and updates.
- Dogfood archive fixtures for superseded/current rows, expiry filtering, redaction, and export/import roundtrip.
- `docs/COMPATIBILITY.md` covering tested Hermes/Python/SQLite baselines, diagnose expectations, and plugin API drift handling.

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
