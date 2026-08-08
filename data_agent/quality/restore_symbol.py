#!/usr/bin/env python3
"""restore_symbol.py — copy one symbol's bars back from a backup database.

WHY THIS EXISTS
---------------
Some repairs cannot be re-fetched. GOLD is the case that motivated this: its MCX
contract expired, the vendor now returns 400 for that instrument key, and a sync
that DELETEs before writing had already replaced 249 good bars with 12 bars of an
option contract. There is no source left to re-download from — only a backup.

Surgical on purpose. It touches exactly one symbol and one timeframe, so a restore
cannot undo unrelated work done since the backup was taken. That matters when the
backup predates a day of other fixes, which is the normal case.

USAGE
    python data_agent/quality/restore_symbol.py --from ~/option_chains.db.bak-20260808 \\
        --symbols GOLD                       # dry run: compares both sides
    python ... --symbols GOLD --apply
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.append(_ROOT)


def _describe(con, sym, tf):
    r = con.execute(
        "select count(*), min(ts), max(ts), min(close), max(close) from price_bars "
        "where symbol=? and timeframe=?", (sym, tf)).fetchone()
    return r if r and r[0] else None


def restore(src_db, dst_db, symbols, timeframe="1d", dry=True):
    if not os.path.exists(src_db):
        sys.exit(f"backup not found: {src_db}")
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_db)

    for sym in symbols:
        s = _describe(src, sym, timeframe)
        d = _describe(dst, sym, timeframe)
        print(f"\n{sym} ({timeframe})")
        if not s:
            print("   backup holds NO rows for this symbol — nothing to restore")
            continue
        print(f"   backup : {s[0]:>6} bars  {s[1][:10]}..{s[2][:10]}  "
              f"close {s[3]:.0f}..{s[4]:.0f}")
        if d:
            print(f"   current: {d[0]:>6} bars  {d[1][:10]}..{d[2][:10]}  "
                  f"close {d[3]:.0f}..{d[4]:.0f}")
            if d[0] > s[0]:
                print("   NOTE: current has MORE bars than the backup. Restoring would "
                      "lose data. Check this is really what you want.")
        else:
            print("   current: no rows")

        rows = src.execute(
            "select exchange, symbol, timeframe, ts, open, high, low, close, volume, "
            "open_interest from price_bars where symbol=? and timeframe=?",
            (sym, timeframe)).fetchall()
        if dry:
            print(f"   would replace current rows with {len(rows)} from the backup")
            continue
        dst.execute("delete from price_bars where symbol=? and timeframe=?",
                    (sym, timeframe))
        dst.executemany(
            "insert or replace into price_bars(exchange, symbol, timeframe, ts, open, "
            "high, low, close, volume, open_interest) values (?,?,?,?,?,?,?,?,?,?)", rows)
        dst.commit()
        a = _describe(dst, sym, timeframe)
        print(f"   restored: {a[0]:>6} bars  {a[1][:10]}..{a[2][:10]}  "
              f"close {a[3]:.0f}..{a[4]:.0f}")
    src.close()
    dst.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="backup database path")
    ap.add_argument("--db", default=None, help="target; defaults to the canonical DB")
    ap.add_argument("--symbols", required=True, help="comma list")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--apply", action="store_true", help="write; default is dry-run")
    args = ap.parse_args()

    dst = args.db
    if not dst:
        from bar_store import DB_PATH
        dst = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    src = os.path.expanduser(args.src)
    print(f"from: {src}\nto  : {dst}")
    restore(src, dst, [s.strip().upper() for s in args.symbols.split(",") if s.strip()],
            args.timeframe, dry=not args.apply)
    if not args.apply:
        print("\n--dry-run (default). Re-run with --apply to write.")
    else:
        print("\nRe-copy the mirror, then run daily_bar_audit.py.")


if __name__ == "__main__":
    main()
