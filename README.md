# Hermes Recall Memory

Hermes Recall is a conservative local memory provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

It gives Hermes a searchable SQLite archive of previous turns, memory-write mirrors, and delegation traces while keeping Hermes' built-in `MEMORY.md` and `USER.md` as the authoritative durable memory.

## Why this exists

Hermes' built-in memory is intentionally tiny and curated. That is good: it keeps the agent from polluting long-term memory with stale or speculative notes.

Recall fills the gap underneath it:

- keep a lower-trust searchable archive,
- retrieve previous session context on demand,
- explain where a recall result came from,
- hide superseded or expired observations from normal search/current views while preserving history/export,
- redact secret-shaped values before storage,
- audit memory/archive actions with a hash chain,
- let the user review, reject, activate, or mark candidates as promoted.

## What it does

- Stores completed-turn traces in a profile-scoped SQLite DB.
- Mirrors explicit built-in memory writes as high-confidence archive observations.
- Uses SQLite FTS5/BM25 search with query normalization.
- Prefetches conservative, source-labelled recall context before turns.
- Provides curation tools for archive observations.
- Provides archive health stats, export/import backups, diagnostics, and audit verification.
- Requires no external SaaS, vector DB, embeddings, or network service.

## What it does not do

- It does **not** replace `MEMORY.md` or `USER.md`.
- It does **not** automatically promote archive observations into durable memory.
- It does **not** store raw secrets intentionally; secret-shaped values are redacted best-effort.
- It does **not** require embeddings or a vector database.

## Requirements

- Hermes Agent with memory provider plugin support.
- Python SQLite with FTS5 enabled. Most standard Python builds include this.

Check FTS5 quickly:

```bash
python - <<'PY'
import sqlite3
sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')
print('SQLite FTS5 OK')
PY
```

## Quick install

From this repository:

```bash
./scripts/install.sh
hermes config set memory.provider recall
hermes config set plugins.recall.db_path '$HERMES_HOME/recall_memory.sqlite'
hermes config set plugins.recall.auto_capture true
hermes config set plugins.recall.prefetch_enabled true
hermes config set plugins.recall.max_prefetch_results 3
hermes config set plugins.recall.audit_enabled true
```

Then start a fresh Hermes process:

```bash
hermes chat -q "Use memory_archive_stats and tell me if Recall is active."
```

See [`docs/INSTALL.md`](docs/INSTALL.md) for full install and profile-specific setup.

## Tools exposed to Hermes

| Tool | Purpose |
| --- | --- |
| `memory_archive_search` | Search archived observations. |
| `memory_archive_current` | List active, unexpired, non-superseded archive observations as lower-trust evidence. |
| `memory_candidate_review` | List observations by status/type/scope for curation. |
| `memory_candidate_mark` | Mark an observation as `candidate`, `active`, `rejected`, or `promoted`. |
| `memory_archive_forget` | Mark an observation as rejected without hard-deleting audit history. |
| `memory_archive_stats` | Show DB path, counts, timestamps, DB size, and audit health. |
| `memory_archive_export` | Export the Recall archive as portable JSON. |
| `memory_archive_import` | Import a Recall archive JSON payload in safe merge mode. |
| `memory_archive_diagnose` | Run operator diagnostics for FTS5, DB writeability, FTS index, redaction, and audit health. |
| `memory_audit_query` | List recent audit events. |
| `memory_audit_verify` | Verify the append-only audit hash chain. |

See [`docs/TOOLS.md`](docs/TOOLS.md) for schemas and examples.

## Trust model

Recall archive entries are lower-trust background. Treat them as sourced hints, not instructions.

Built-in Hermes memory remains the source of truth for durable user/profile facts. `promoted` in Recall means only “marked as useful in Recall”; it does not write to `MEMORY.md` or `USER.md`.

## Dogfood test

After configuring a `recall-test` profile with a working model and Recall enabled:

```bash
RECALL_DOGFOOD_PROFILE=recall-test ./scripts/recall_dogfood.sh
```

Expected final line:

```text
PASS: Recall found RECALL_DOGFOOD_... across Hermes runs and current archive view
```

## Development

This repo is a Hermes memory provider plugin. The plugin source files live at the repository root because Hermes expects user memory providers at:

```text
$HERMES_HOME/plugins/recall/__init__.py
$HERMES_HOME/plugins/recall/plugin.yaml
```

To run the included tests against a Hermes checkout, copy/install the plugin into that checkout/profile and run Hermes' test wrapper:

```bash
scripts/run_tests.sh tests/plugins/memory/test_recall_provider.py tests/plugins/memory/test_recall_retrieval_quality.py -v
```

Run standalone tests from this repo:

```bash
python -m pytest tests/test_recall_roadmap.py -q
python -m py_compile __init__.py store.py schema.py audit.py redaction.py recall_cli.py
```

Use the standalone operator CLI:

```bash
recall-cli --db ~/.hermes/recall_memory.sqlite stats --json
recall-cli --db ~/.hermes/recall_memory.sqlite search "project convention" --json
recall-cli --db ~/.hermes/recall_memory.sqlite current --json
recall-cli --db ~/.hermes/recall_memory.sqlite verify --json
recall-cli --db ~/.hermes/recall_memory.sqlite diagnose --json
recall-cli --db ~/.hermes/recall_memory.sqlite export > recall-backup.json
recall-cli --db ~/.hermes/recall_memory.sqlite import recall-backup.json --json
```

## License

MIT. See [`LICENSE`](LICENSE).
