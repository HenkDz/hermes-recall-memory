# Updating Hermes Recall Memory

## From 0.3.3 to 0.3.4

0.3.4 is backward-compatible with the 0.3.x SQLite schema. No data migration is required.

What changed operationally:
- Dashboard review queues now support fact/type filters, episode hiding, recommended-action filters, and minimum quality thresholds so noisy archive traces are easier to avoid.
- `memory_promote_candidate` blocks rejected observations by default; pass `allow_rejected=true` only when deliberately reversing a prior rejection after review.
- The release includes a documented 100k-observation isolated burn-in in `docs/BURNIN.md`.

Recommended smoke check after updating:
```bash
python -m pytest tests/test_recall_roadmap.py -q
python -m py_compile __init__.py store.py schema.py audit.py redaction.py recall_cli.py dashboard/plugin_api.py
node --check dashboard/dist/index.js
./scripts/install.sh --check
```

## From 0.3.1 to 0.3.2

0.3.2 is backward-compatible with the 0.3.x SQLite schema. No data migration is required.

What changed operationally:
- `memory_consolidation_suggest` and `recall-cli consolidate` now hide low-quality groups by default, especially noisy episode transcript groups like `User asked:`.
- Operators can opt into those noisy groups with `include_low_quality=true` or `recall-cli consolidate --include-low-quality`.
- `min_quality_score` / `--min-quality-score` controls the default canonical quality threshold.

## From 0.3.0 to 0.3.1

0.3.1 is backward-compatible with the 0.3.0 SQLite schema. No data migration is required.

What changed operationally:
- `memory_quality_rank` and `recall-cli rank` score observations for curation using local deterministic quality signals.
- `memory_consolidation_suggest` and `recall-cli consolidate` propose same-subject rows to supersede/consolidate without mutating the archive.
- The stress probe now checks quality ranking and consolidation paths.

## From 0.2.x to 0.3.x

0.3.x is backward-compatible with the 0.2.x SQLite schema. No data migration is required.

What changed operationally:
- `memory_archive_current` and `recall-cli current` now hide expired, rejected/deleted, and superseded observations from normal current views.
- Export/import remains merge-only and preserves history/audit rows.
- Import now redacts secret-shaped content before storage.
- `scripts/recall_dogfood.sh --archive-fixtures-only` is the fastest deterministic post-update smoke test.

Recommended update flow:
```bash
git pull
python -m pytest tests/test_recall_roadmap.py -q
RECALL_DOGFOOD_DB=/tmp/recall-dogfood.sqlite ./scripts/recall_dogfood.sh --archive-fixtures-only
./scripts/install.sh --dry-run
./scripts/install.sh
./scripts/install.sh --check
hermes config set memory.provider recall
recall-cli --db ~/.hermes/recall_memory.sqlite diagnose --json
```

For profile-specific or non-default Hermes homes, set `HERMES_HOME` on install, check, and diagnose:

```bash
HERMES_HOME=/path/to/hermes-home ./scripts/install.sh --dry-run
HERMES_HOME=/path/to/hermes-home ./scripts/install.sh
HERMES_HOME=/path/to/hermes-home ./scripts/install.sh --check
HERMES_HOME=/path/to/hermes-home recall-cli --db /path/to/hermes-home/recall_memory.sqlite diagnose --json
```

Restart any running Hermes process after installation.

## From 0.1.x to 0.2.x

0.2.0 is backward-compatible with the 0.1.x SQLite schema. No migration is required.

Recommended update flow:
```bash
git pull
python -m pytest tests/test_recall_roadmap.py -q
./scripts/install.sh --dry-run
./scripts/install.sh
./scripts/install.sh --check
hermes config set memory.provider recall
```

For profile-specific or non-default Hermes homes, set `HERMES_HOME` on both install and check:

```bash
HERMES_HOME=/path/to/hermes-home ./scripts/install.sh
HERMES_HOME=/path/to/hermes-home ./scripts/install.sh --check
```

Restart any running Hermes process after installation.

## Backup before major changes

Use the new export command before risky upgrades or profile moves:

```bash
recall-cli --db ~/.hermes/recall_memory.sqlite export > recall-backup.json
```

Restore/merge into another profile:

```bash
recall-cli --db ~/.hermes/recall_memory.sqlite import recall-backup.json --json
```

Import is currently merge-only: it upserts episodes and observations by ID and preserves existing local rows.

## Health check

After updating:

```bash
recall-cli --db ~/.hermes/recall_memory.sqlite diagnose --json
```

The top-level `ok` field should be `true`. If it is false, inspect `checks` first, then `audit`.
