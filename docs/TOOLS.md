# Recall Tools

These tools are exposed when `memory.provider` is set to `recall`.

## `memory_archive_search`

Search lower-trust archive observations.

Arguments:

```json
{
  "query": "string, required",
  "limit": 5,
  "scope": "optional string",
  "project_path": "optional string"
}
```

Result shape:

```json
{
  "results": [
    {
      "id": "observation id",
      "type": "fact | preference | episode | delegation | ...",
      "scope": "session | project | profile | user",
      "trust_level": "archive | builtin-mirror",
      "confidence": 0.35,
      "importance": 0.25,
      "status": "active",
      "content": "redacted content",
      "redacted_content": "redacted content",
      "source_session_id": "session id",
      "project_path": "/path",
      "created_at": "ISO timestamp",
      "score": -1.23,
      "matched_query_terms": ["recall", "marker"]
    }
  ]
}
```

Notes:

- `rejected` and `deleted` observations are excluded from search.
- Observations with `expires_at` in the past are excluded from search.
- Observations superseded by a non-expired non-rejected row are excluded from search.
- `content` is redacted before returning.
- `matched_query_terms` explains why the result matched.
- Results that supersede another row include `supersedes` and redacted `supersedes_content` metadata when available.

## `memory_archive_current`

List current lower-trust archive observations: active, unexpired, not superseded, and not rejected/deleted.

Arguments:

```json
{
  "limit": 50,
  "scope": "optional string",
  "project_path": "optional string"
}
```

Returns:

```json
{
  "trust": "lower-trust archive evidence; built-in MEMORY.md/USER.md remain authoritative",
  "results": []
}
```

Use this for operator inspection of active archive evidence. Do not treat it as durable truth; built-in Hermes memory remains authoritative.

## `memory_candidate_review`

List observations for curation.

Arguments:

```json
{
  "status": "candidate",
  "type": "optional string",
  "scope": "optional string",
  "limit": 20
}
```

Use this to inspect candidates before marking them.

## `memory_candidate_mark`

Change an observation's Recall status.

Arguments:

```json
{
  "id": "observation id",
  "status": "candidate | active | rejected | promoted",
  "reason": "optional human reason"
}
```

Important: `promoted` means “marked as promoted in Recall” only. It does not write to Hermes built-in durable memory.

## `memory_archive_forget`

Soft-forget an observation by marking it `rejected`.

Arguments:

```json
{
  "id": "observation id",
  "reason": "optional reason"
}
```

This preserves the audit trail. It does not hard-delete rows.

## `memory_archive_stats`

Show archive health.

Returns:

```json
{
  "db_path": "/path/to/recall_memory.sqlite",
  "observations_by_status": {"active": 10, "candidate": 2},
  "observations_by_type": {"episode": 8, "fact": 4},
  "episode_count": 12,
  "expired_observation_count": 1,
  "audit": {"ok": true, "count": 3, "head": "sha256..."},
  "oldest_observation_at": "ISO timestamp",
  "newest_observation_at": "ISO timestamp",
  "db_size_bytes": 123456
}
```

## `memory_archive_export`

Export the archive as portable JSON.

Arguments: none.

Returns:

```json
{
  "version": 1,
  "schema_version": "1",
  "exported_at": "ISO timestamp",
  "episodes": [],
  "observations": [],
  "audit_events": []
}
```

Use this before risky upgrades, profile migration, or manual DB changes.

## `memory_archive_import`

Import a Recall archive JSON payload in safe merge mode.

Arguments:

```json
{
  "payload": {"version": 1, "episodes": [], "observations": [], "audit_events": []},
  "json": "optional JSON string if payload is not provided",
  "mode": "merge"
}
```

Returns:

```json
{
  "mode": "merge",
  "episodes_imported": 1,
  "observations_imported": 3,
  "audit_events_imported": 2
}
```

Import redacts episode text, observation content, and audit previews again as a defensive measure. `merge` is the only supported mode.

## `memory_archive_diagnose`

Run operator diagnostics.

Returns:

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
  "audit": {"ok": true, "count": 3, "head": "sha256..."},
  "stats": {}
}
```

## `memory_quality_rank`

Rank observations by deterministic local curation quality. This is offline and does not call an LLM, embedding model, or network service.

Arguments:

```json
{
  "limit": 20,
  "include_statuses": ["candidate", "active"],
  "scope": "optional string",
  "project_path": "optional string"
}
```

Returns ranked observations with extra fields:

```json
{
  "trust": "local deterministic curation ranking; review before promotion to built-in memory",
  "results": [
    {
      "id": "observation id",
      "quality_score": 0.91,
      "quality_reasons": ["trusted mirror", "specific markers", "stable subject label"],
      "recommended_action": "promote | keep | review | reject",
      "subject_key": "label:recall memory"
    }
  ]
}
```

Quality signals include confidence, importance, trust level, fact/preference shape, stable labels, path/hash/marker specificity, transcript-summary penalties, repetition penalties, and status penalties. The score is a curation heuristic, not truth.

## `memory_consolidation_suggest`

Suggest same-subject rows that could be consolidated by superseding weaker duplicates with the best canonical row. This tool does not mutate the archive.

Arguments:

```json
{
  "limit": 20,
  "scope": "optional string",
  "project_path": "optional string",
  "include_low_quality": false,
  "min_quality_score": 0.45
}
```

Returns:

```json
{
  "trust": "suggestions only; no archive rows were mutated",
  "filters": {"include_low_quality": false, "min_quality_score": 0.45},
  "results": [
    {
      "subject_key": "label:poti",
      "canonical_id": "best observation id",
      "canonical_quality_score": 0.94,
      "duplicate_ids": ["older observation id"],
      "duplicate_count": 1,
      "recommended_action": "supersede_duplicates",
      "suggested_content": "redacted canonical content"
    }
  ]
}
```

Use the suggestion as an operator queue. Actual state changes still require explicit `memory_candidate_mark`, `memory_archive_forget`, or a future supersession mutation path.

By default, consolidation suggestions hide low-quality groups where the best canonical row scores below `min_quality_score` or is recommended for rejection. This keeps noisy episode transcript groups such as `label:user asked` out of the main operator queue. Set `include_low_quality: true` only when deliberately auditing noise/backlog groups.

## `memory_audit_query`

List recent audit events.

Arguments:

```json
{"limit": 20}
```

## `memory_audit_verify`

Verify the audit hash chain.

Returns:

```json
{"ok": true, "count": 10, "head": "sha256..."}
```

If tampering is detected:

```json
{"ok": false, "failed_seq": 1, "reason": "event_hash mismatch"}
```
