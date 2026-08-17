#!/bin/bash
# run_expectation_snapshot.sh — weekly consensus capture, with a loud failure mode.
#
# WHY A WRAPPER AND NOT A BARE CRON LINE
# --------------------------------------
# A cron entry that fails silently is the failure this whole channel exists to avoid.
# expectation_snapshots.json is APPEND-ONLY and irreplaceable: consensus estimates are
# revised continuously and vendors serve the current number, not last Tuesday's. A week
# that does not capture is a week that cannot be recovered — and the way you would find
# out, with a bare cron line, is by opening the file in three months and counting.
#
# WHAT THE FIRST VERSION GOT WRONG (correction C36)
# -------------------------------------------------
# It proved success by checking the snapshot COUNT went up. On 2026-08-17 two instances
# started in the same second, each read the file before the other wrote, and the count
# went 2 -> 4. Both instances logged "OK snapshots 2 -> 4". Snapshot #4 was byte-identical
# to #3 — same captured_at, same md5 over rows. A duplicate write grows the count exactly
# like a real capture does, so the count was never the proof it was taken for.
#
# Two changes, because the bug had two halves:
#   * A LOCK, so two instances cannot overlap. This is the cause.
#   * A BETTER TEST — grew by exactly 1, and the newest capture is dated today. This is
#     the detection, and it stands on its own if the lock is ever bypassed.
# The duplicate guard itself lives in expectation_snapshot.py, which refuses to append a
# same-day identical payload and exits 2. Exit 2 is reported DUP, not FAIL: nothing was
# lost, so it must not read like a missed week.
#
# It also runs freshness.py afterwards, so the one job that IS automated becomes the
# reminder for the ones that are not.
#
# INSTALL
#   chmod +x data_agent/fundamentals/run_expectation_snapshot.sh
#   crontab -e
# then add — Mondays 09:00 local, before the week's results start landing:
#
#   0 9 * * 1 /Users/deepak/antigravity/NiftyOptions/data_agent/fundamentals/run_expectation_snapshot.sh
#
# ONE line. Check with `crontab -l | grep -c run_expectation_snapshot` — it must print 1.
# Two identical cron lines is the most likely cause of the same-second double run above.
#
# CHECK IT IS ALIVE
#   tail -5 .state/expectation_snapshot.status
#   tail -40 .state/expectation_snapshot.log
#
# macOS note: cron needs Full Disk Access for /usr/sbin/cron under System Settings ->
# Privacy & Security, or it will fail on protected paths. If you would rather not grant
# that, run it manually each Monday — the point is the cadence, not the automation.

set -uo pipefail

REPO="/Users/deepak/antigravity/NiftyOptions"
PY="$REPO/breeze_env/bin/python"
SCRIPT="$REPO/data_agent/fundamentals/expectation_snapshot.py"
SNAP="$REPO/expectation_snapshots.json"
LOG="$REPO/.state/expectation_snapshot.log"
STATUS="$REPO/.state/expectation_snapshot.status"
LOCK="$REPO/.state/expectation_snapshot.lock"

mkdir -p "$REPO/.state"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

# mkdir is atomic on every filesystem this will ever run on, and unlike flock it needs no
# GNU coreutils. If the directory exists another instance is mid-run: exit quietly rather
# than racing it. A stale lock older than 2 hours is broken — the job takes under a minute.
if [ -d "$LOCK" ]; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    echo "[$TS] breaking stale lock $LOCK" >> "$LOG"
    rmdir "$LOCK" 2>/dev/null
  else
    echo "[$TS] SKIP another instance holds $LOCK" | tee -a "$LOG" >> "$STATUS"
    exit 0
  fi
fi
mkdir "$LOCK" 2>/dev/null || { echo "[$TS] SKIP lost the lock race" >> "$LOG"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

count() {
  [ -f "$SNAP" ] || { echo 0; return; }
  "$PY" -c "import json,sys
try:
    print(len(json.load(open('$SNAP')).get('snapshots', [])))
except Exception:
    print(-1)" 2>/dev/null || echo -1
}

newest() {
  [ -f "$SNAP" ] || { echo "none"; return; }
  "$PY" -c "import json
try:
    s = json.load(open('$SNAP')).get('snapshots', [])
    print(max((x.get('captured_at','') for x in s), default='none')[:10])
except Exception:
    print('none')" 2>/dev/null || echo none
}

BEFORE="$(count)"

{
  echo "=========================================================="
  echo "[$TS] starting — snapshots held before run: $BEFORE"
} >> "$LOG"

cd "$REPO" || { echo "[$TS] FAIL cannot cd to $REPO" | tee -a "$LOG" >> "$STATUS"; exit 1; }

"$PY" "$SCRIPT" >> "$LOG" 2>&1
RC=$?

AFTER="$(count)"
NEWEST="$(newest)"
TODAY="$(date '+%Y-%m-%d')"
GREW=$(( AFTER - BEFORE ))

# The exit code alone is not proof, and neither is the count going up. Growing by exactly
# one, with the newest entry dated today, is.
if [ "$RC" -eq 2 ]; then
  MSG="DUP  identical to today's capture, nothing appended (snapshots=$AFTER) — not a miss"
  RC=0
elif [ "$RC" -ne 0 ]; then
  MSG="FAIL exit=$RC snapshots=$AFTER (was $BEFORE) — see $LOG"
elif [ "$GREW" -eq 0 ]; then
  MSG="FAIL exit=0 but snapshots did NOT grow: $BEFORE -> $AFTER — nothing captured"
  RC=1
elif [ "$GREW" -gt 1 ]; then
  MSG="FAIL snapshots grew by $GREW ($BEFORE -> $AFTER) — concurrent writes, see C36"
  RC=1
elif [ "$NEWEST" != "$TODAY" ]; then
  MSG="FAIL count grew to $AFTER but newest capture is dated $NEWEST, not $TODAY"
  RC=1
else
  MSG="OK   snapshots $BEFORE -> $AFTER, newest $NEWEST"
fi

echo "[$TS] $MSG" | tee -a "$LOG" >> "$STATUS"

# ---------------------------------------------------------------------------
# Freshness. Several inputs refresh manually or on an event, and "remember to run it" is
# not a control. This is the only job on a clock, so it is where the reminder belongs.
# Its exit code is NOT allowed to mask the capture's: a stale input is a to-do list, not
# a failed capture.
if [ -f "$REPO/data_agent/freshness.py" ]; then
  {
    echo "---- freshness ----"
    "$PY" "$REPO/data_agent/freshness.py"
  } >> "$LOG" 2>&1
  DUE="$("$PY" "$REPO/data_agent/freshness.py" --quiet 2>/dev/null | grep -c 'DUE ')"
  if [ "${DUE:-0}" -gt 0 ]; then
    echo "[$TS] $DUE input(s) overdue — see the freshness block in $LOG" \
      | tee -a "$LOG" >> "$STATUS"
  fi
fi

# Keep the log from growing without bound; keep the status file forever, it is small
# and it is the audit trail.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 2000000 ]; then
  tail -c 500000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
fi

exit $RC
