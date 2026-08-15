#!/usr/bin/env bash
# sync_from_parent.sh — refresh the vendored engine from the parent newsindex/ project.
#
# NewsAgent/engine/ is a COPY (snapshot) of the parent market_scan.py + siblings. Run this
# whenever you've improved the parent engine (fixed a bug, tuned coefficients) or refreshed
# events.db (via build_events.py), so the vendored copy picks up those changes.
#
# Safe: only writes inside this engine/ folder; verifies each source exists first; reports
# exactly what it copied. Does NOT touch the parent.
#
# Usage:   bash NewsAgent/engine/sync_from_parent.sh            # auto-detect parent
#          PARENT=/path/to/newsindex bash .../sync_from_parent.sh   # explicit parent

set -euo pipefail

ENGINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# parent = two levels up (NewsAgent/engine -> NewsAgent -> newsindex), unless PARENT is set
PARENT="${PARENT:-$(cd "$ENGINE_DIR/../.." && pwd)}"

echo "engine dir : $ENGINE_DIR"
echo "parent     : $PARENT"
echo

if [[ ! -f "$PARENT/market_scan.py" ]]; then
  echo "✗ market_scan.py not found in parent ($PARENT)."
  echo "  Set PARENT=/path/to/newsindex and re-run."
  exit 1
fi

# source-in-parent  ->  name-in-engine
copy() {
  local src="$PARENT/$1" dst="$ENGINE_DIR/${2:-$1}"
  if [[ -f "$src" ]]; then
    cp "$src" "$dst"
    printf '  ✓ %-22s → %s\n' "$1" "$(basename "$dst")"
  else
    printf '  – %-22s (not in parent, skipped)\n' "$1"
  fi
}

echo "copying engine files:"
copy "market_scan.py"       "market_engine.py"   # renamed so NewsAgent imports 'market_engine'
copy "fetch_article.py"
copy "desk_note_examples.py"
copy "build_events.py"
copy "signals.json"
copy "events.db"                                 # refreshed calibration (linkage hit-rates)
copy "suggested_sensitivity.py"                  # fitted coefficients, if present

echo
echo "done. Re-run the pipeline to pick up the refreshed engine:"
echo "  python NewsAgent/agents/run.py --report --no-llm"
