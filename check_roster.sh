#!/usr/bin/env bash
# check_roster.sh — is the browser actually being served the new code?
# Run from the repo root:  bash check_roster.sh
#
# Checks the three links in the chain independently, so we learn WHICH one is broken
# instead of guessing: (1) source on disk, (2) backend API, (3) vite dev server.

API="${API:-http://127.0.0.1:8000}"
VITE="${VITE:-http://127.0.0.1:5173}"
SRC="src/components/SignalBacktestView.tsx"

echo "=============================================="
echo "1. SOURCE ON DISK"
echo "=============================================="
if [ ! -f "$SRC" ]; then
  echo "  [FAIL] $SRC not found — are you in the repo root?"; exit 1
fi
ctl=$(grep -c "Return window" "$SRC")
line=$(grep -n "Return window</span>" "$SRC" | head -1 | cut -d: -f1)
gate=$(grep -n "mode === 'all' || mode === 'single'" "$SRC" | head -1 | cut -d: -f1)
echo "  'Return window' occurrences : $ctl"
echo "  control rendered at line    : ${line:-NOT FOUND}"
echo "  mode gate at line           : ${gate:-none}"
if [ -n "$line" ] && [ -n "$gate" ] && [ "$line" -lt "$gate" ]; then
  echo "  [OK] control is ABOVE the mode gate -> shows in every mode"
else
  echo "  [WARN] control may be inside a mode-conditional block"
fi
echo "  file modified               : $(date -r "$SRC" '+%Y-%m-%d %H:%M:%S')"
echo "  now                         : $(date '+%Y-%m-%d %H:%M:%S')"

echo
echo "=============================================="
echo "2. BACKEND  ($API)"
echo "=============================================="
code=$(curl -s -o /tmp/_cfg -w '%{http_code}' "$API/api/strategy/config" 2>/dev/null)
if [ "$code" = "000" ]; then
  echo "  [DOWN] no response — uvicorn not running on this port"
else
  echo "  [$code] /api/strategy/config"
  python3 - <<'PY'
import json
try:
    d = json.load(open('/tmp/_cfg'))
except Exception:
    print("  (non-JSON body)"); raise SystemExit
mw = d.get('momentum_window')
print(f"  signals served      : {len(d.get('signals', []))}  (expect 19)")
if mw:
    print(f"  momentum_window     : {mw.get('lookback_min')} min, options {mw.get('options')}")
    print("  [OK] backend serves the return-window setting")
else:
    print("  [FAIL] no momentum_window key -> uvicorn is running OLD code. Restart it.")
PY
fi

echo
echo "=============================================="
echo "3. VITE DEV SERVER  ($VITE)"
echo "=============================================="
vcode=$(curl -s -o /tmp/_vite -w '%{http_code}' "$VITE/src/components/SignalBacktestView.tsx" 2>/dev/null)
if [ "$vcode" = "000" ]; then
  echo "  [DOWN] nothing on $VITE — the dev server is NOT running."
  echo "         start it:   npm run dev"
else
  hits=$(grep -c "Return window" /tmp/_vite 2>/dev/null || echo 0)
  echo "  [$vcode] served module, 'Return window' occurrences: $hits"
  if [ "$hits" -gt 0 ]; then
    echo "  [OK] vite IS serving the new code."
    echo "       -> the browser is showing a CACHED bundle."
    echo "       -> hard-reload: Cmd-Shift-R, or DevTools > Network > Disable cache, or"
    echo "          open in a private window to rule out cache entirely."
  else
    echo "  [FAIL] vite is serving an OLD version of this file."
    echo "       -> restart the dev server so it re-reads from disk:"
    echo "          pkill -f vite ; npm run dev"
  fi
fi

echo
echo "=============================================="
echo "VERDICT"
echo "=============================================="
echo "  All three OK + still nothing on screen -> open DevTools Console and look for"
echo "  a red error or a [signalRoster] line, and paste it. A render error in this"
echo "  component would blank the controls without any other symptom."
