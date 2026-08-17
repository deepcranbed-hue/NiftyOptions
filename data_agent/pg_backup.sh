#!/bin/bash
# pg_backup.sh — dump localhost/niftyoptions to Google Drive, and prove the dump has content.
#
# WHY THIS EXISTS
# ---------------
# Two stores, split by domain: SQLite market data lives on Google Drive and syncs off-machine
# by itself; PostgreSQL localhost/niftyoptions holds macro + fundamentals and had NO backup
# machinery at all — no pg_dump anywhere in the repo. The repo's JSON artifacts are covered by
# git, but git cannot hold a live database, so Postgres was the one store with nothing between
# it and a disk failure.
#
# WHY IT VERIFIES CONTENT AND NOT JUST EXIT CODE
# ----------------------------------------------
# C36: run_expectation_snapshot.sh proved success by checking a COUNT went up, and a duplicate
# write satisfied that test. The same trap is here in a different shape — pg_dump exits 0 for a
# schema-only dump of an empty database, and a 2 KB file that "exists" restores nothing. So this
# checks the dump is plausibly sized AND contains COPY/INSERT data lines, then reports the row
# counts it captured. An unverified backup is worse than none, because it is trusted.
#
# ROTATION
# --------
# Keeps the last 7 daily dumps. Postgres here is a derived store — rebuildable from the
# downloaders — so deep history is not the point; recovering yesterday is.
#
#   data_agent/pg_backup.sh                 # dump to Drive
#   PG_BACKUP_DIR=/somewhere data_agent/pg_backup.sh
#
# Verify with:  python3 data_agent/quality/backup_audit.py

set -uo pipefail

REPO="/Users/deepak/antigravity/NiftyOptions"
DRIVE_DEFAULT="$HOME/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/niftyoptions_pg"
OUTDIR="${PG_BACKUP_DIR:-$DRIVE_DEFAULT}"
DSN="${DATABASE_URL:-${NIFTY_PG_DSN:-postgresql://localhost/niftyoptions}}"
KEEP=7

TS="$(date '+%Y-%m-%d_%H%M%S')"
LOG="$REPO/.state/pg_backup.log"
mkdir -p "$REPO/.state"

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

command -v pg_dump >/dev/null 2>&1 || { say "FAIL pg_dump not on PATH"; exit 1; }

if ! mkdir -p "$OUTDIR" 2>/dev/null; then
  say "FAIL cannot create $OUTDIR — is Drive mounted and signed in?"
  exit 1
fi

OUT="$OUTDIR/niftyoptions_$TS.sql.gz"
say "dumping $DSN -> $OUT"

# --no-owner/--no-privileges so the dump restores into any local role, which is what a
# recovery on a fresh machine actually needs.
if ! pg_dump --no-owner --no-privileges --dbname="$DSN" 2>>"$LOG" | gzip > "$OUT"; then
  say "FAIL pg_dump errored — see $LOG"
  rm -f "$OUT" 2>/dev/null
  exit 1
fi

SIZE=$(wc -c < "$OUT" | tr -d ' ')
if [ "${SIZE:-0}" -lt 100000 ]; then
  say "FAIL dump is only ${SIZE} bytes — that is a schema with no rows, not a backup"
  exit 1
fi

# Exit code and size are still circumstantial. Look for actual data statements.
DATALINES=$(gzip -dc "$OUT" | grep -cE '^(COPY |INSERT INTO )' || true)
if [ "${DATALINES:-0}" -lt 1 ]; then
  say "FAIL dump contains no COPY/INSERT statements — schema only"
  exit 1
fi

TABLES=$(gzip -dc "$OUT" | grep -cE '^CREATE TABLE ' || true)
say "OK   ${SIZE} bytes, ${TABLES} tables, ${DATALINES} data statements"

# What was actually captured, so a glance at the log answers "did it get the fundamentals".
if command -v psql >/dev/null 2>&1; then
  psql "$DSN" -At -c "
    SELECT relname || '=' || n_live_tup
      FROM pg_stat_user_tables
     WHERE n_live_tup > 0
     ORDER BY n_live_tup DESC LIMIT 12;" 2>/dev/null \
    | tr '\n' ' ' | sed 's/^/  rows: /' | tee -a "$LOG"
  echo | tee -a "$LOG"
fi

# Rotation, oldest first, and only ever inside OUTDIR.
COUNT=$(ls -1 "$OUTDIR"/niftyoptions_*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
if [ "${COUNT:-0}" -gt "$KEEP" ]; then
  ls -1t "$OUTDIR"/niftyoptions_*.sql.gz | tail -n +$((KEEP + 1)) | while read -r old; do
    say "rotating out $(basename "$old")"
    rm -f "$old"
  done
fi

say "done — $(ls -1 "$OUTDIR"/niftyoptions_*.sql.gz 2>/dev/null | wc -l | tr -d ' ') dump(s) held"
