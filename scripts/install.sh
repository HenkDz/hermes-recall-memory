#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_HOME_DIR/plugins/recall"

mkdir -p "$DEST"

for file in __init__.py store.py schema.py audit.py redaction.py plugin.yaml README.md; do
  install -m 0644 "$ROOT_DIR/$file" "$DEST/$file"
done

echo "Installed Hermes Recall memory provider to: $DEST"
echo
echo "Enable it with:"
echo "  hermes config set memory.provider recall"
echo "  hermes config set plugins.recall.db_path '\$HERMES_HOME/recall_memory.sqlite'"
echo "  hermes config set plugins.recall.auto_capture true"
echo "  hermes config set plugins.recall.prefetch_enabled true"
echo "  hermes config set plugins.recall.max_prefetch_results 3"
echo "  hermes config set plugins.recall.audit_enabled true"
echo
echo "Restart Hermes after changing memory provider config."
