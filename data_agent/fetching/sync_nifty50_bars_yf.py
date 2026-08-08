#!/usr/bin/env python3
"""sync_nifty50_bars_yf.py — the ONE writer of daily equity bars.

Run every day by sync_all_auxiliary.py, right after the Breeze sync finishes.

WHAT CHANGED AND WHY
--------------------
The previous version deleted every symbol's whole history and re-downloaded from
2018 on EVERY run. That was slow, and it silently reverted any correction held in
the database — a repaired series lasted exactly until the next morning.

It also had three defects that together caused the data problems we chased down:

  1. Its ticker map was INVERTED: {'ETERNAL': 'ZOMATO', 'TMPV': 'TATAMOTORS'}.
     ZOMATO.NS and TATAMOTORS.NS are the RETIRED tickers and return 0 bars; the
     live ones are ETERNAL.NS and TMPV.NS. So those two names never received Yahoo
     history at all, fell through to the Breeze path, and got stuck at ~1 year.
  2. It wrote them under the DB symbols ETERNAL / TMPV, while the constituents CSV,
     the Nifty 50 view, nifty50_drivers.json and earnings_reactions.json all key on
     ZOMATO / TATAMOTORS — so nothing ever read those rows.
  3. It used index.tz_localize(None), which keeps whatever wall-clock the index
     carries. It happens to work while yfinance returns IST for .NS, but the day
     that changes, every date shifts back one session and Mondays land on Sunday.

All three now live in ONE place — daily_bars.py — shared with the backfill tool.

INCREMENTAL, WITH A CORRECTNESS BACKSTOP
----------------------------------------
Default is incremental: fetch from a few sessions before the stored watermark, so
late vendor corrections are picked up without re-pulling eight years.

But incremental has a real failure mode worth naming. When a company does a split
or bonus, Yahoo re-adjusts its ENTIRE history at once. An incremental sync only
rewrites the recent window, so the old bars keep the pre-event scale and the series
develops a cliff at the join — which is exactly the shape of corruption that made
Trent's 1Y return read -42% instead of -13%.

So every run ends with a continuity check. Any symbol showing an unexplained gap
>15% is reported as NEEDS FULL REFRESH, and `--full <SYM,...>` (or `--full ALL`)
re-pulls it. Cheap by default, self-diagnosing when it matters.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)        # daily_bars sits beside this file
sys.path.append(REPO_ROOT)

from daily_bars import (LISTED_FROM, fetch_best, write_daily, duplicate_dates,
                        VENDOR_ADJUSTMENTS, KNOWN_REAL_GAPS)

DEFAULT_FROM = "2018-01-01"
# Re-fetch this many sessions behind the watermark so late vendor corrections land.
_OVERLAP_DAYS = 5
# A gap this large with no matching known action means the stored history is on a
# different price scale from the new bars — i.e. a corporate action we missed.
_GAP_FLAG = 0.15

_CSV = os.path.join(REPO_ROOT, "nifty-50-stock-list.csv")
# The index is the benchmark every relative-return figure is measured against, so
# it belongs in the same job as the constituents rather than a separate one.
_EXTRA = ["NIFTY"]


def _db():
    from bar_store import DB_PATH
    return os.environ.get("OPTION_CHAINS_DB", DB_PATH)


def _universe():
    """Symbols from the constituents CSV — the same names the app reads."""
    with open(_CSV, newline="") as f:
        return [r["Symbol"].strip() for r in csv.DictReader(f) if r.get("Symbol")]


def _watermark(con, symbol):
    r = con.execute("select max(ts) from price_bars where symbol=? and timeframe='1d'",
                    (symbol,)).fetchone()
    return r[0][:10] if r and r[0] else None


def _floor_for(symbol):
    f = DEFAULT_FROM
    if symbol.upper() in LISTED_FROM and LISTED_FROM[symbol.upper()] > f:
        f = LISTED_FROM[symbol.upper()]
    return datetime.strptime(f, "%Y-%m-%d")


def _continuity(con, symbol):
    """Largest unexplained single-day gap. Known actions are excluded."""
    known = {(a["symbol"], a["boundary"]) for a in VENDOR_ADJUSTMENTS}
    known |= set(KNOWN_REAL_GAPS)          # genuine, already-understood breaks
    rows = con.execute("select ts, open, close from price_bars where symbol=? "
                       "and timeframe='1d' order by ts", (symbol,)).fetchall()
    worst = None
    prev = None
    for ts, o, cl in rows:
        if prev and o and prev > 0:
            r = o / prev
            if (r < 1 - _GAP_FLAG or r > 1 + _GAP_FLAG) and (symbol, ts[:10]) not in known:
                if worst is None or abs(r - 1) > abs(worst[1] - 1):
                    worst = (ts[:10], r)
        prev = cl
    return worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", default="",
                    help="comma list (or ALL) to re-pull from scratch instead of "
                         "incrementally — use after a split/bonus")
    ap.add_argument("--symbols", default="", help="limit the run to these symbols")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = _db()
    print(f"database: {db}")
    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else _universe() + _EXTRA)
    full = set() if not args.full else (
        set(symbols) if args.full.upper() == "ALL"
        else {s.strip().upper() for s in args.full.split(",") if s.strip()})

    today = datetime.now()
    end = today + timedelta(days=1)
    con = sqlite3.connect(db)
    written = skipped = 0
    needs_full = []

    for sym in symbols:
        floor = _floor_for(sym)
        wm = None if sym in full else _watermark(con, sym)
        if wm:
            start = max(floor, datetime.strptime(wm, "%Y-%m-%d") - timedelta(days=_OVERLAP_DAYS))
            mode = f"incremental from {start.date()} (watermark {wm})"
        else:
            start = floor
            mode = f"FULL from {start.date()}"
        if start >= end:
            skipped += 1
            continue

        rows, ticker = fetch_best(sym, start, end, log=lambda *_: None)
        if not rows:
            print(f"  {sym:12} NO DATA — check TICKER_ALTS in daily_bars.py")
            continue
        if args.dry_run:
            print(f"  {sym:12} {mode}: would write {len(rows)} bars via {ticker}")
            continue

        n, _ = write_daily(rows, sym, db)          # INSERT OR REPLACE, never delete
        written += n
        print(f"  {sym:12} {mode}: {n} bars via {ticker}")

    if args.dry_run:
        con.close()
        print("\n--dry-run: nothing written.")
        return

    # Backstop: did an unnoticed corporate action leave a scale break behind?
    for sym in symbols:
        g = _continuity(con, sym)
        if g:
            needs_full.append((sym, g[0], g[1]))
    dupes = duplicate_dates(db)
    con.close()

    print(f"\nwrote {written} bars, {skipped} up to date")
    if dupes:
        print("DUPLICATE trading dates (a second writer is active):")
        for s, n in dupes:
            print(f"   {s:12}{n:>5} dates")
    if needs_full:
        print("\nNEEDS FULL REFRESH — unexplained price gap, probably a split/bonus")
        print("that re-adjusted history Yahoo-side while we only updated the tail:")
        for s, d, r in needs_full:
            print(f"   {s:12} {d}  ratio {r:.4f}")
        print(f"\n   python {os.path.basename(__file__)} --full "
              f"{','.join(s for s, _, _ in needs_full)}")
        print("   If the gap is REAL (a demerger, which Yahoo does not adjust), add it")
        print("   to VENDOR_ADJUSTMENTS or _KNOWN_ACTIONS instead so it stops being flagged.")
    else:
        print("continuity: no unexplained gaps.")


if __name__ == "__main__":
    main()
