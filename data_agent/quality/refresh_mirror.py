#!/usr/bin/env python3
"""
refresh_mirror.py — copy the Drive SQLite to the repo-local mirror, safely and in the one
direction that is correct.

WHY A SCRIPT AND NOT `cp`
------------------------
SQLite market data flows **Drive → local**: Drive is the source of truth, written by the
download pipeline, and `<repo>/option_chains.db` is a read-only mirror kept so tooling without
Drive access can read. A bare `cp` in a sync plan cannot check any of the four things that make
that copy safe, and three of them bit this repo in one day:

1. DIRECTION. If the mirror is NEWER than Drive, something wrote to the read-only copy — that
   is C37 — and its write is absent from the source of truth. Copying then DESTROYS data that
   exists nowhere else. A `cp` would do it silently. This refuses.

2. A HOT JOURNAL. `option_chains.db-journal` beside the Drive file means a transaction is
   mid-flight. Copying the database WITHOUT its journal yields a copy that is neither the
   before-state nor the after-state. This happened on 2026-08-17 when a failed commit left a
   242 KB journal behind, and a copy taken at that moment would have looked fine and been
   subtly wrong. This refuses.

3. ATOMICITY. The file is ~490 MB. A plain `cp` over a live path leaves a half-written database
   readable for the seconds it takes, and `backend/` may be reading it. This copies to a temp
   file in the same directory and then `os.replace()`, which is atomic on one filesystem — a
   reader sees the old file or the new one, never a partial one.

4. VERIFICATION. A copy that completed is not a copy that is usable. This runs
   `PRAGMA integrity_check` on the result and compares a row count against the source, because
   "the command exited 0" has been the wrong proof three times in this repo (C36, C37, the
   schema-only pg_dump guard).

    python3 data_agent/quality/refresh_mirror.py
    python3 data_agent/quality/refresh_mirror.py --check   # report only, copy nothing
    python3 data_agent/quality/refresh_mirror.py --force   # override the direction guard
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

COUNT_TABLE = "price_bars"      # present in every generation of this schema


def _mtime(p):
    return dt.datetime.fromtimestamp(os.path.getmtime(p)) if os.path.exists(p) else None


def _rows(path: str, table: str) -> int | None:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = con.execute(f"select count(*) from {table}").fetchone()[0]
        con.close()
        return n
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, copy nothing")
    ap.add_argument("--force", action="store_true",
                    help="copy even if the mirror is newer than Drive (destroys the "
                         "mirror-only write — be sure)")
    a = ap.parse_args()

    from db_config import resolve_writable_db_path
    try:
        drive = resolve_writable_db_path()
    except FileNotFoundError as exc:
        # Not a failure of this step. In a sandbox or when signed out of Drive there is
        # nothing to copy FROM, and the mirror is simply whatever it already was.
        print(f"SKIP  Drive not reachable, nothing to copy from.\n      {exc}")
        return 0

    mirror = os.path.join(ROOT, "option_chains.db")
    dm, mm = _mtime(drive), _mtime(mirror)
    print(f"source (Drive)  {drive}\n                {dm}  "
          f"{os.path.getsize(drive) / 1e6:.0f} MB")
    print(f"target (mirror) {mirror}\n                {mm or 'absent'}"
          + (f"  {os.path.getsize(mirror) / 1e6:.0f} MB" if mm else ""))

    # ---- guard 1: direction ------------------------------------------------------------
    if mm and mm > dm and not a.force:
        ahead = (mm - dm).total_seconds() / 3600
        print(f"\nREFUSED — the mirror is {ahead:.1f}h NEWER than Drive.")
        print("  SQLite flows Drive -> local, so this can only mean something WROTE to the")
        print("  read-only mirror (C37). That write is not in the source of truth, and")
        print("  copying now would destroy the only copy of it.")
        print("  Find the writer first:  python3 data_agent/quality/db_path_audit.py")
        print("  Then --force once you are certain the mirror holds nothing worth keeping.")
        return 1

    # ---- guard 2: a hot journal means Drive is mid-transaction --------------------------
    for sidecar in ("-journal", "-wal"):
        p = drive + sidecar
        if os.path.exists(p) and os.path.getsize(p) > 0:
            print(f"\nREFUSED — {os.path.basename(p)} exists ({os.path.getsize(p):,} bytes).")
            print("  The source is mid-transaction. A copy taken now is neither the before")
            print("  state nor the after state, and it would look perfectly healthy.")
            print("  Let the writer finish, or open the Drive database once so SQLite can")
            print("  roll the journal back, then re-run.")
            return 1

    src_rows = _rows(drive, COUNT_TABLE)
    print(f"\nsource {COUNT_TABLE}: {src_rows:,}" if src_rows is not None
          else f"\nsource {COUNT_TABLE}: unreadable")

    if a.check:
        if mm and dm and mm < dm:
            print(f"\n--check: mirror is behind by {(dm - mm)}. A refresh is due.")
        else:
            print("\n--check: nothing to do.")
        return 0

    # ---- guard 3: atomic swap ----------------------------------------------------------
    tmp = mirror + ".refresh.tmp"
    print(f"\ncopying -> {os.path.basename(tmp)} then atomic replace ...")
    try:
        shutil.copy2(drive, tmp)
        os.replace(tmp, mirror)          # atomic within one filesystem
    except Exception as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                print(f"      leftover temp file: {tmp}")
        return 1

    # ---- guard 4: verify the RESULT, not the exit code ---------------------------------
    ok = _rows(mirror, COUNT_TABLE)
    integ = None
    try:
        con = sqlite3.connect(f"file:{mirror}?mode=ro", uri=True)
        integ = con.execute("pragma integrity_check").fetchone()[0]
        con.close()
    except Exception as exc:
        integ = f"unreadable: {exc}"

    print(f"  integrity_check: {integ}")
    print(f"  {COUNT_TABLE}: {ok:,}" if ok is not None else f"  {COUNT_TABLE}: unreadable")
    if integ != "ok" or ok is None or (src_rows is not None and ok != src_rows):
        print("\nFAIL  the copy does not match the source — do NOT trust the mirror.")
        return 1
    print(f"\nOK    mirror refreshed and verified ({ok:,} {COUNT_TABLE} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
