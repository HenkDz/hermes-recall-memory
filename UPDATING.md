# Updating Hermes Recall Memory

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
