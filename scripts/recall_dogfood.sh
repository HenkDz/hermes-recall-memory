#!/usr/bin/env bash
set -euo pipefail

# Live Recall dogfood test.
# Requires a configured profile with the Recall memory provider enabled and a working model.
# Uses a synthetic marker only; never put real secrets in this script.

PROFILE="${RECALL_DOGFOOD_PROFILE:-recall-test}"
HERMES_BIN="${HERMES_BIN:-./hermes}"
MARKER="${RECALL_DOGFOOD_MARKER:-RECALL_DOGFOOD_$(date +%s)_$$}"
TIMEOUT_SECONDS="${RECALL_DOGFOOD_TIMEOUT:-180}"

if ! command -v timeout >/dev/null 2>&1; then
  echo "FAIL: GNU timeout is required" >&2
  exit 2
fi

run_hermes() {
  local prompt="$1"
  timeout "$TIMEOUT_SECONDS" "$HERMES_BIN" -p "$PROFILE" chat -q "$prompt"
}

echo "Recall dogfood profile: $PROFILE"
echo "Synthetic marker: $MARKER"

echo "[1/2] Seeding marker through Hermes..."
seed_output="$(run_hermes "Synthetic Recall dogfood seed. The marker is $MARKER. Reply exactly: seeded $MARKER")"
printf '%s\n' "$seed_output"

if ! grep -Fq "$MARKER" <<<"$seed_output"; then
  echo "FAIL: seed run did not echo marker; profile/model may be misconfigured" >&2
  exit 1
fi

echo "[2/3] Searching marker from a fresh Hermes run via memory_archive_search..."
search_prompt="Use the memory_archive_search tool with query '$MARKER'. If the marker appears in the tool result, reply exactly DOGFOOD_FOUND $MARKER. Otherwise reply exactly DOGFOOD_MISSING $MARKER."
search_output="$(run_hermes "$search_prompt")"
printf '%s\n' "$search_output"

if ! grep -Fq "DOGFOOD_FOUND $MARKER" <<<"$search_output"; then
  echo "FAIL: Recall did not find $MARKER across Hermes runs" >&2
  exit 1
fi

echo "[3/3] Checking current archive view stays conservative..."
current_prompt="Use memory_archive_current with limit 20. If any result contains '$MARKER', reply exactly DOGFOOD_CURRENT $MARKER. If not, reply exactly DOGFOOD_CURRENT_MISSING $MARKER. Do not claim durable memory truth."
current_output="$(run_hermes "$current_prompt")"
printf '%s\n' "$current_output"

if grep -Fq "DOGFOOD_CURRENT $MARKER" <<<"$current_output"; then
  echo "PASS: Recall found $MARKER across Hermes runs and current archive view"
  exit 0
fi

echo "FAIL: Recall search worked but memory_archive_current did not show $MARKER" >&2
exit 1
