# Architecture

Recall is deliberately boring: SQLite, FTS5, redaction, and a small MemoryProvider implementation.

## Data model

SQLite tables:

- `episodes` — redacted user/assistant turn traces.
- `observations` — searchable archive facts/traces with trust, confidence, importance, status, source session, and project path.
- `observations_fts` — FTS5 virtual table over observation content.
- `audit_events` — append-only hash-chained audit log.
- `schema_meta` — schema version.

## Provider lifecycle

`RecallMemoryProvider` implements Hermes' `MemoryProvider` interface:

- `initialize()` opens the profile-scoped SQLite store.
- `sync_turn()` captures completed turns when `auto_capture` is enabled.
- `prefetch()` searches the archive and injects conservative context.
- `on_memory_write()` mirrors explicit built-in memory writes as high-trust archive observations.
- `on_delegation()` captures delegation summaries.
- `get_tool_schemas()` exposes Recall tools.
- `handle_tool_call()` routes tool invocations.

## Retrieval

Search uses SQLite FTS5/BM25.

User queries are normalized by `_query_terms()`:

- lowercases,
- tokenizes safely,
- drops common low-signal stopwords,
- preserves useful tokens such as paths, codenames, ports, config keys, and model names.

The FTS query is built as quoted `OR` terms to avoid syntax errors from paths like `E:\Projects` or `/mnt/e/...`.

Search excludes rejected/deleted observations and observations whose `expires_at` timestamp is in the past. This keeps old conventions from silently winning retrieval after they are time-bounded.

## Export/import and diagnostics

`export_archive()` emits a versioned JSON payload containing `episodes`, `observations`, and `audit_events`. `import_archive()` currently supports merge-only restore: it upserts episodes/observations by ID and inserts audit events that do not already exist.

`diagnose()` checks FTS5 availability, DB existence/writeability, FTS index readability, redaction smoke behavior, and audit-chain health. It is exposed both as a Hermes tool and through `recall-cli diagnose`.

## Trust model

Archive observations are lower-trust unless they mirror built-in memory writes.

Recall-prefetched context is labelled with source session, trust, and confidence. It should be treated as background evidence, not instruction.

## Curation statuses

Supported statuses:

- `candidate` — useful-looking, needs review.
- `active` — searchable and usable.
- `rejected` — excluded from search.
- `promoted` — marked useful inside Recall only.

No status automatically writes to `MEMORY.md` or `USER.md`.

## Audit chain

Audit event hashes include:

- sequence number,
- event id,
- phase,
- operation,
- target,
- redacted preview,
- previous hash,
- timestamp,
- metadata JSON.

`memory_audit_verify` recomputes the chain and reports the first mismatch.
