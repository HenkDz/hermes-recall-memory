# Updating Hermes Recall Memory

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
