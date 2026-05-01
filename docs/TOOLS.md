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
- `content` is redacted before returning.
- `matched_query_terms` explains why the result matched.

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
  "audit": {"ok": true, "count": 3, "head": "sha256..."},
  "oldest_observation_at": "ISO timestamp",
  "newest_observation_at": "ISO timestamp",
  "db_size_bytes": 123456
}
```

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
