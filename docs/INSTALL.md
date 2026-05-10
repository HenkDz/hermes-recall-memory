# Install Hermes Recall Memory

Recall is a Hermes **memory provider** plugin. Install it into the active Hermes profile's plugin directory, then set `memory.provider: recall`. For tested Hermes/Python/SQLite compatibility and plugin API drift handling, see [`COMPATIBILITY.md`](COMPATIBILITY.md).

## 1. Choose the Hermes profile

By default Hermes uses `~/.hermes`.

For a named profile, first check where its home lives:

```bash
hermes profile show recall-test
```

If you use a wrapper command/profile, run install commands under that same profile context.

## 2. Install

Preferred Hermes plugin installer flow:

```bash
hermes plugins install HenkDz/hermes-recall-memory --no-enable
hermes memory setup   # select "recall"
```

One-command install from GitHub, without piping remote code into a shell:

```bash
tmp="$(mktemp -d)" && git clone --depth 1 https://github.com/HenkDz/hermes-recall-memory.git "$tmp" && "$tmp/scripts/install.sh"
```

Or install from a local clone:

```bash
git clone https://github.com/HenkDz/hermes-recall-memory.git
cd hermes-recall-memory
./scripts/install.sh
```

The installer copies the plugin files to:

```text
${HERMES_HOME:-~/.hermes}/plugins/recall/
```

It does not modify your config automatically. It is safe to re-run for updates: files are overwritten from the current checkout.

Useful installer modes:

```bash
./scripts/install.sh --dry-run   # print destination, files, and config commands without writing
./scripts/install.sh --check     # verify installed files exist and match this checkout
```

For a non-default Hermes home, set `HERMES_HOME` explicitly:

```bash
HERMES_HOME=/path/to/hermes-home ./scripts/install.sh
HERMES_HOME=/path/to/hermes-home ./scripts/install.sh --check
```

## 3. Enable Recall

Recommended CLI config commands:

```bash
hermes config set memory.provider recall
hermes config set plugins.recall.db_path '$HERMES_HOME/recall_memory.sqlite'
hermes config set plugins.recall.auto_capture true
hermes config set plugins.recall.prefetch_enabled true
hermes config set plugins.recall.max_prefetch_results 3
hermes config set plugins.recall.audit_enabled true
```

Equivalent YAML:

```yaml
memory:
  provider: recall

plugins:
  recall:
    db_path: $HERMES_HOME/recall_memory.sqlite
    auto_capture: true
    prefetch_enabled: true
    max_prefetch_results: 3
    audit_enabled: true
```

## 4. Restart Hermes

Memory provider config is read when the Hermes process starts. Restart any running CLI/gateway process.

For a user gateway service:

```bash
systemctl --user restart hermes-gateway
```

For CLI, just start a new `hermes` process.

## 5. Verify

Ask Hermes to call the stats tool:

```bash
hermes chat -q "Use memory_archive_stats and summarize the Recall archive health."
```

You should see a DB path ending in `recall_memory.sqlite` and audit status.

Run local diagnostics directly with the standalone CLI:

```bash
python recall_cli.py --db "${HERMES_HOME:-$HOME/.hermes}/recall_memory.sqlite" diagnose --json
```

If installed through packaging, the equivalent command is:

```bash
recall-cli --db "${HERMES_HOME:-$HOME/.hermes}/recall_memory.sqlite" diagnose --json
```

You can also run the live dogfood script after setting up a profile named `recall-test`:

```bash
RECALL_DOGFOOD_PROFILE=recall-test ./scripts/recall_dogfood.sh
```

For installer/archive verification without a live Hermes model call:

```bash
RECALL_DOGFOOD_DB=/tmp/recall-dogfood.sqlite ./scripts/recall_dogfood.sh --archive-fixtures-only
```

## Manual install without the script

```bash
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/plugins/recall"
cp __init__.py store.py schema.py audit.py redaction.py recall_cli.py plugin.yaml \
  "${HERMES_HOME:-$HOME/.hermes}/plugins/recall/"
```

Then enable the config as shown above.

## Uninstall

```bash
rm -rf "${HERMES_HOME:-$HOME/.hermes}/plugins/recall"
hermes config set memory.provider ""
```

If you want to delete the archive DB too:

```bash
rm -f "${HERMES_HOME:-$HOME/.hermes}/recall_memory.sqlite"*
```

The `*` also removes SQLite WAL/SHM sidecar files.
