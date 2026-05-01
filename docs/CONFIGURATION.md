# Configuration

Recall reads plugin settings from `plugins.recall` in Hermes config.

## Minimal config

```yaml
memory:
  provider: recall
```

With no extra config, Recall uses:

```yaml
plugins:
  recall:
    db_path: $HERMES_HOME/recall_memory.sqlite
    auto_capture: true
    prefetch_enabled: true
    max_prefetch_results: 3
    audit_enabled: true
```

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `plugins.recall.db_path` | `$HERMES_HOME/recall_memory.sqlite` | SQLite database path. `$HERMES_HOME` and `${HERMES_HOME}` are expanded. |
| `plugins.recall.auto_capture` | `true` | Store completed user/assistant turns as low-trust archive observations. |
| `plugins.recall.prefetch_enabled` | `true` | Search the archive before turns and inject conservative recall context. |
| `plugins.recall.max_prefetch_results` | `3` | Max recalled items injected before a turn. |
| `plugins.recall.audit_enabled` | `true` | Append hash-chained audit events for session/memory/archive actions. |

## Profile isolation

The default DB path is profile-aware because it resolves under the active `HERMES_HOME`.

That means each Hermes profile can have its own Recall archive:

```text
~/.hermes/recall_memory.sqlite
~/.hermes/profiles/recall-test/recall_memory.sqlite
```

Exact profile paths depend on your Hermes profile setup.

## Recommended production posture

Keep these defaults unless you know why you are changing them:

```yaml
auto_capture: true
prefetch_enabled: true
max_prefetch_results: 3
audit_enabled: true
```

Do not point `db_path` at a shared network filesystem unless SQLite locking semantics are understood.

## Secret handling

Recall redacts common secret-shaped values before storing episode/observation payloads and audit previews.

The redactor is best-effort, not a security boundary. Do not intentionally paste secrets into chat just because Recall redacts common patterns.
