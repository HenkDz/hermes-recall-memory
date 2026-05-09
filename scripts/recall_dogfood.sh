#!/usr/bin/env bash
set -euo pipefail

# Live Recall dogfood test.
# Requires a configured profile with the Recall memory provider enabled and a working model.
# Uses synthetic markers only; never put real secrets in this script output.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${RECALL_DOGFOOD_PROFILE:-recall-test}"
HERMES_BIN="${HERMES_BIN:-hermes}"
MARKER="${RECALL_DOGFOOD_MARKER:-RECALL_DOGFOOD_$(date +%s)_$$}"
TIMEOUT_SECONDS="${RECALL_DOGFOOD_TIMEOUT:-180}"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
DOGFOOD_DB="${RECALL_DOGFOOD_DB:-$HERMES_HOME_DIR/recall_memory.sqlite}"
MODE="live"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--archive-fixtures-only]

Runs real Recall dogfood checks against a configured Hermes profile.

Environment:
  RECALL_DOGFOOD_PROFILE   Hermes profile to use (default: recall-test)
  HERMES_BIN               Hermes executable (default: hermes)
  RECALL_DOGFOOD_MARKER    Synthetic marker override
  RECALL_DOGFOOD_TIMEOUT   Per-Hermes-call timeout seconds (default: 180)
  RECALL_DOGFOOD_DB        Recall SQLite DB path (default: \$HERMES_HOME/recall_memory.sqlite)
  HERMES_HOME              Hermes home used for default DB resolution

Options:
  --archive-fixtures-only  Seed and verify deterministic archive fixtures without invoking Hermes.
  -h, --help               Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --archive-fixtures-only) MODE="archive-fixtures-only" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

require_timeout() {
  if ! command -v timeout >/dev/null 2>&1; then
    echo "FAIL: GNU timeout is required" >&2
    exit 2
  fi
}

run_hermes() {
  local prompt="$1"
  timeout "$TIMEOUT_SECONDS" "$HERMES_BIN" -p "$PROFILE" chat -q "$prompt"
}

run_archive_fixtures() {
  PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  RECALL_DOGFOOD_DB="$DOGFOOD_DB" \
  RECALL_DOGFOOD_MARKER="$MARKER" \
  python - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from store import RecallStore

marker = os.environ["RECALL_DOGFOOD_MARKER"]
db_path = Path(os.environ["RECALL_DOGFOOD_DB"]).expanduser()
project_path = "/mnt/e/Projects/AI/hermes-recall-memory"
raw_secret = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"

def iso(delta_days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

store = RecallStore(db_path)
try:
    stale_id = store.add_observation(
        content=f"Dogfood stale repo path {marker}_STALE should be superseded.",
        type="fact",
        scope="project",
        status="active",
        project_path=project_path,
    )
    current_id = store.add_observation(
        content=f"Dogfood current repo path {marker}_CURRENT is authoritative only as Recall evidence.",
        type="fact",
        scope="project",
        status="active",
        project_path=project_path,
        supersedes=stale_id,
    )
    expired_id = store.add_observation(
        content=f"Dogfood expired value {marker}_EXPIRED must not appear in normal views.",
        type="fact",
        scope="project",
        status="active",
        project_path=project_path,
        expires_at=iso(-1),
    )
    redacted_id = store.add_observation(
        content=f"Dogfood redaction marker {marker}_REDACT with OPENAI_API_KEY={raw_secret}",
        type="fact",
        scope="project",
        status="active",
        project_path=project_path,
    )

    search_current = store.search_observations(f"{marker}_CURRENT", limit=10)
    current = store.current_observations(limit=100, project_path=project_path)
    current_ids = {item["id"] for item in current}
    if current_id not in current_ids or stale_id in current_ids:
        raise SystemExit("current fixture failed: superseded row leaked or current row missing")
    if not search_current or search_current[0].get("supersedes") != stale_id:
        raise SystemExit("search fixture failed: supersedes metadata missing")
    print(f"DOGFOOD_CURRENT_OK {marker}")

    expired_search = store.search_observations(f"{marker}_EXPIRED", limit=10)
    current_after_expiry = store.current_observations(limit=100, project_path=project_path)
    if expired_id in {item["id"] for item in expired_search} or expired_id in {item["id"] for item in current_after_expiry}:
        raise SystemExit("expiry fixture failed: expired row appeared in normal views")
    print(f"DOGFOOD_EXPIRED_OK {marker}")

    redacted_row = store.get_observation(redacted_id)
    redacted_search = store.search_observations(f"{marker}_REDACT", limit=10)
    redacted_text = json.dumps({"row": redacted_row, "search": redacted_search}, ensure_ascii=False)
    if raw_secret in redacted_text or "OPENAI_API_KEY=[REDACTED]" not in redacted_text:
        raise SystemExit("redaction fixture failed: raw secret leaked or redaction marker missing")
    print(f"DOGFOOD_REDACTION_OK {marker}")

    archive = store.export_archive()
finally:
    store.close()

with tempfile.TemporaryDirectory(prefix="recall-dogfood-import-") as tmp:
    target = RecallStore(Path(tmp) / "roundtrip.sqlite")
    try:
        summary = target.import_archive(archive)
        roundtrip_current = target.current_observations(limit=100, project_path=project_path)
        roundtrip_search = target.search_observations(f"{marker}_REDACT", limit=10)
        roundtrip_text = json.dumps({"summary": summary, "current": roundtrip_current, "search": roundtrip_search}, ensure_ascii=False)
        if current_id not in {item["id"] for item in roundtrip_current}:
            raise SystemExit("roundtrip fixture failed: current row missing after import")
        if raw_secret in roundtrip_text:
            raise SystemExit("roundtrip fixture failed: raw secret leaked after import")
        if not roundtrip_search:
            raise SystemExit("roundtrip fixture failed: redacted marker missing after import")
    finally:
        target.close()
print(f"DOGFOOD_ROUNDTRIP_OK {marker}")
print(f"DOGFOOD_ARCHIVE_FIXTURES_OK {marker}")
PY
}

if [[ "$MODE" == "archive-fixtures-only" ]]; then
  echo "Recall dogfood DB: $DOGFOOD_DB"
  echo "Synthetic marker: $MARKER"
  run_archive_fixtures
  exit 0
fi

require_timeout

echo "Recall dogfood profile: $PROFILE"
echo "Recall dogfood DB: $DOGFOOD_DB"
echo "Synthetic marker: $MARKER"

echo "[1/5] Seeding marker through Hermes for cross-session recall..."
seed_output="$(run_hermes "Synthetic Recall dogfood seed. The marker is $MARKER. Reply exactly: seeded $MARKER")"
printf '%s\n' "$seed_output"

if ! grep -Fq "$MARKER" <<<"$seed_output"; then
  echo "FAIL: seed run did not echo marker; profile/model may be misconfigured" >&2
  exit 1
fi

echo "[2/5] Searching marker from a fresh Hermes run via memory_archive_search..."
search_prompt="Use the memory_archive_search tool with query '$MARKER'. If the marker appears in the tool result, reply exactly DOGFOOD_FOUND $MARKER. Otherwise reply exactly DOGFOOD_MISSING $MARKER."
search_output="$(run_hermes "$search_prompt")"
printf '%s\n' "$search_output"

if ! grep -Fq "DOGFOOD_FOUND $MARKER" <<<"$search_output"; then
  echo "FAIL: Recall did not find $MARKER across Hermes runs" >&2
  exit 1
fi

echo "[3/5] Checking current archive view stays conservative..."
current_prompt="Use memory_archive_current with limit 20. If any result contains '$MARKER', reply exactly DOGFOOD_CURRENT $MARKER. If not, reply exactly DOGFOOD_CURRENT_MISSING $MARKER. Do not claim durable memory truth."
current_output="$(run_hermes "$current_prompt")"
printf '%s\n' "$current_output"

if ! grep -Fq "DOGFOOD_CURRENT $MARKER" <<<"$current_output"; then
  echo "FAIL: Recall search worked but memory_archive_current did not show $MARKER" >&2
  exit 1
fi

echo "[4/5] Seeding deterministic archive fixtures for supersedes, expiry, redaction, and roundtrip..."
run_archive_fixtures

echo "[5/5] Verifying fixture behavior through Hermes tools..."
fixture_prompt="Use memory_archive_search for '$MARKER'_CURRENT and memory_archive_current with limit 100. If the current marker appears and the stale/expired markers do not appear in normal current results, reply exactly DOGFOOD_FIXTURES_VISIBLE $MARKER. Treat Recall as lower-trust archive evidence, not durable truth."
fixture_output="$(run_hermes "$fixture_prompt")"
printf '%s\n' "$fixture_output"

if ! grep -Fq "DOGFOOD_FIXTURES_VISIBLE $MARKER" <<<"$fixture_output"; then
  echo "FAIL: Hermes did not verify dogfood archive fixtures" >&2
  exit 1
fi

echo "PASS: Recall found $MARKER across Hermes runs and passed current/supersedes/expiry/redaction/export-import dogfood"
