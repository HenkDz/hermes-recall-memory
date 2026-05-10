#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_HOME_DIR/plugins/recall"
FILES=(__init__.py store.py schema.py audit.py redaction.py recall_cli.py plugin.yaml README.md after-install.md dashboard/manifest.json dashboard/plugin_api.py dashboard/dist/index.js)
MODE="install"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--dry-run|--check]

Installs the Hermes Recall memory provider into:
  \${HERMES_HOME:-$HOME/.hermes}/plugins/recall

Options:
  --dry-run   Print planned actions and config commands without writing files.
  --check     Verify installed files exist and match this checkout.
  -h, --help  Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --check) MODE="check" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

print_config_commands() {
  cat <<'EOF'
Enable it with:
  hermes config set memory.provider recall
  hermes config set plugins.recall.db_path '$HERMES_HOME/recall_memory.sqlite'
  hermes config set plugins.recall.auto_capture true
  hermes config set plugins.recall.prefetch_enabled true
  hermes config set plugins.recall.max_prefetch_results 3
  hermes config set plugins.recall.audit_enabled true

Restart Hermes after changing memory provider config.
EOF
}

check_install() {
  local missing=0
  local changed=0
  for file in "${FILES[@]}"; do
    if [[ ! -f "$DEST/$file" ]]; then
      echo "missing: $DEST/$file"
      missing=1
      continue
    fi
    if ! cmp -s "$ROOT_DIR/$file" "$DEST/$file"; then
      echo "changed: $DEST/$file differs from $ROOT_DIR/$file"
      changed=1
    fi
  done

  if [[ "$missing" -eq 0 && "$changed" -eq 0 ]]; then
    echo "Install check OK: Hermes Recall files match $ROOT_DIR"
    echo "Destination: $DEST"
    return 0
  fi

  echo "Install check failed: run $(basename "$0") to update $DEST"
  return 1
}

case "$MODE" in
  dry-run)
    echo "DRY RUN: would install Hermes Recall memory provider to: $DEST"
    echo "Files: ${FILES[*]}"
    echo
    print_config_commands
    ;;
  check)
    check_install
    ;;
  install)
    mkdir -p "$DEST"
    for file in "${FILES[@]}"; do
      mkdir -p "$DEST/$(dirname "$file")"
      install -m 0644 "$ROOT_DIR/$file" "$DEST/$file"
    done
    echo "Installed Hermes Recall memory provider to: $DEST"
    echo
    print_config_commands
    ;;
esac
