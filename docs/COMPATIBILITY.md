# Hermes compatibility matrix

Recall is an external Hermes memory provider plugin. This repository is the source of truth; any Hermes in-tree copy is only a compatibility snapshot or upstream proof.

Recall stays conservative: results are **lower-trust archive evidence**. Built-in Hermes `MEMORY.md` and `USER.md` remain authoritative.

## Tested Hermes Agent baseline

| Component | Status |
| --- | --- |
| Recall package | `0.2.0` plus current `Unreleased` changes |
| Hermes upstream main checked | `fef1a41248a9a584f7b945d0a46d57de46d15358` from `NousResearch/hermes-agent` `main` |
| Local Hermes compatibility checkout checked | `11c295b33` on `fix/acp-zed-tooling-polish` |
| Plugin install path | `$HERMES_HOME/plugins/recall/` |
| Provider kind | exclusive memory provider: `memory.provider recall` |

Compatibility is API-level, not a promise that every Hermes UI surface renders every tool identically. Zed/ACP rendering issues belong in Hermes ACP work, not in this standalone Recall plugin unless the Recall tool payload itself is invalid.

## Python and SQLite requirements

| Requirement | Supported / expected |
| --- | --- |
| Python | `>=3.10`; CI runs `3.10`, `3.11`, `3.12` |
| Local Python smoke check | `3.11.14` |
| SQLite smoke check | `3.50.4` |
| SQLite extension | SQLite FTS5 must be available |
| External services | none |
| Runtime dependencies | none beyond Python stdlib |

Check SQLite FTS5 directly:

```bash
python - <<'PY'
import sqlite3
sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')
print('SQLite FTS5 OK')
PY
```

If this fails, Recall cannot provide archive search on that Python build. Use a Python distribution with SQLite FTS5 enabled.

## Expected Hermes plugin API surface

Recall currently depends on the Hermes memory provider shape below:

- subclass/import compatibility for `agent.memory_provider.MemoryProvider`
- `initialize(session_id, **kwargs)` called with profile/session context
- optional `hermes_home`, `cwd`, or `project_path` in `initialize(...)` kwargs
- `shutdown()` called on provider teardown
- `system_prompt_block()` included as lower-trust context guidance
- `sync_turn(user_content, assistant_content, *, session_id="")` called after completed turns when auto-capture is enabled
- `prefetch_context(user_message)` called before turns when prefetch is enabled
- `get_tool_schemas()` used to expose Recall tools
- `handle_tool_call(name, arguments)` used to execute Recall tools

The expected tool names are:

- `memory_archive_search`
- `memory_archive_current`
- `memory_candidate_review`
- `memory_candidate_mark`
- `memory_archive_forget`
- `memory_archive_stats`
- `memory_archive_export`
- `memory_archive_import`
- `memory_archive_diagnose`
- `memory_audit_query`
- `memory_audit_verify`

## `recall-cli diagnose --json` expectations

Run diagnostics against the active archive DB:

```bash
recall-cli --db "${HERMES_HOME:-$HOME/.hermes}/recall_memory.sqlite" diagnose --json
```

A healthy result should have:

```json
{
  "ok": true,
  "checks": {
    "fts5_available": true,
    "db_exists": true,
    "db_writable": true,
    "fts_index_readable": true,
    "audit_chain_ok": true,
    "redaction_smoke_ok": true
  },
  "audit": {
    "ok": true
  },
  "stats": {
    "db_path": ".../recall_memory.sqlite",
    "observations_by_status": {},
    "observations_by_type": {},
    "episode_count": 0,
    "expired_observation_count": 0,
    "db_size_bytes": 0
  }
}
```

Counts may be non-zero on a real profile. Treat failed checks in this order:

1. `fts5_available`: Python/SQLite build problem.
2. `db_exists` / `db_writable`: path, permissions, or wrong `HERMES_HOME`.
3. `fts_index_readable`: schema/index damage; export what you can before repair.
4. `audit_chain_ok`: audit history mismatch; keep the archive but lower trust further until inspected.
5. `redaction_smoke_ok`: stop using the archive until redaction is fixed.

## If Hermes plugin API drift occurs

Do not evolve Recall inside the Hermes repo by default. Keep this external repo as source of truth.

Triage steps:

1. Confirm the active install path:

   ```bash
   hermes config get memory.provider
   hermes config get plugins.recall.db_path
   python recall_cli.py --db "${HERMES_HOME:-$HOME/.hermes}/recall_memory.sqlite" diagnose --json
   ```

2. Verify plugin files are installed from this checkout:

   ```bash
   ./scripts/install.sh --check
   ```

3. Check whether Hermes changed the memory provider API:

   ```bash
   python - <<'PY'
   import inspect
   from agent.memory_provider import MemoryProvider
   print(MemoryProvider)
   for name in ('initialize', 'shutdown', 'sync_turn', 'prefetch_context', 'get_tool_schemas', 'handle_tool_call'):
       attr = getattr(MemoryProvider, name, None)
       print(name, inspect.signature(attr) if attr else 'missing')
   PY
   ```

4. Patch the compatibility adapter in this repository first, with tests in `tests/test_recall_roadmap.py`.

5. Only sync into Hermes `feat/recall-memory-provider` when explicitly preparing compatibility testing or an upstream PR.

## Compatibility verification commands

From this repository:

```bash
python -m pytest tests/test_recall_roadmap.py -q
python -m py_compile __init__.py store.py schema.py audit.py redaction.py recall_cli.py
bash -n scripts/install.sh scripts/recall_dogfood.sh
./scripts/install.sh --dry-run
RECALL_DOGFOOD_DB=/tmp/recall-dogfood.sqlite ./scripts/recall_dogfood.sh --archive-fixtures-only
```

For a live Hermes profile with a working model:

```bash
RECALL_DOGFOOD_PROFILE=recall-test ./scripts/recall_dogfood.sh
```

The live dogfood should pass cross-session search, current archive view, supersedes/expiry filtering, redaction, and export/import roundtrip.
