#!/usr/bin/env python3
"""fix_usdinr_scale.py — repair the USDINR bars written at 1/10 scale from 2026-08-16.

WHAT HAPPENED
-------------
`sync_commodities.py` carried `SCALE = {"USDINR": 10.0}` and divided every USDINR candle
by ten, because Upstox quoted the GLOBAL_INDICATOR|USDINR feed 10x scaled. On 2026-08-16
Upstox stopped scaling it. Our divisor did not change — it had been there since the
original version of the script, unchanged by the 15-Aug refactor — so the compensation
became the defect and 95.5 was stored as 9.55.

The boundary is not a judgement call:

    2026-08-14T20:00:00Z   95.415      last 1m close before
    2026-08-16T14:13:00Z    9.5415     first 1m close after      ratio 10.0000

The same price, one decimal point apart, on a rate that moves ~0.05% a day.

WHY SIX DAILY BARS AND NOT ONE
------------------------------
`_resume_from(db, symbol, "1d", DAILY_FROM, 5)` re-fetches the last five days on every
run and `write_daily` upserts them. So the first run after the flip did not merely append
a bad bar — it reached back and REPLACED five good ones. 2026-08-09, 11, 12, 13, 14 and 16
are all wrong; 2026-08-07 and earlier are untouched and correct. The overlap that exists
to pick up vendor revisions propagated a vendor defect backwards instead.

WHY A SCRIPT AND NOT A RE-FETCH
-------------------------------
A `--full` re-fetch fixes 1d, because write_daily upserts across the whole window. It does
NOT fix 1m: the minute resume starts from the last stored timestamp, so every bad minute
bar from 16-Aug onward is already behind the watermark and would never be revisited. And a
re-fetch depends on the vendor still serving that history for a GLOBAL_INDICATOR key,
which is not something to find out during a repair. Multiplying by ten is exact — it is
the arithmetic inverse of the only operation that was applied.

SELECTION
---------
`close < 20`. USDINR has never printed below 63.26 in this series (2018 low), so there is
no date range to get wrong and no boundary to reason about: a USDINR bar under 20 is a
scaled bar and nothing else. The script prints what it matched before it touches anything.

    python3 data_agent/quality/fix_usdinr_scale.py            # dry run, writes nothing
    python3 data_agent/quality/fix_usdinr_scale.py --apply    # repair Drive

Writes through db_config.resolve_writable_db_path(), so it repairs the SOURCE OF TRUTH on
Drive and not the read-only mirror (C37). Refresh the mirror afterwards:

    python3 data_agent/quality/refresh_mirror.py
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SYMBOL = "USDINR"
CEILING = 20.0          # nothing legitimate is below this; the 2018 low is 63.26
FACTOR = 10.0
COLS = ("open", "high", "low", "close")


def _report(con) -> list:
    rows = con.execute(
        f"select timeframe, count(*), min(ts), max(ts), min(close), max(close) "
        f"from price_bars where symbol=? and close < ? group by timeframe order by 1",
        (SYMBOL, CEILING)).fetchall()
    if not rows:
        print("Nothing matches — no USDINR bar is below the ceiling. Already repaired,")
        print("or this database was never affected.")
        return []
    print(f"AFFECTED (symbol={SYMBOL}, close < {CEILING:g})\n")
    total = 0
    for tf, n, lo_ts, hi_ts, lo, hi in rows:
        total += n
        print(f"  {tf:<4} {n:>6,} bars   {str(lo_ts)[:19]} .. {str(hi_ts)[:19]}   "
              f"close {lo:.4f} .. {hi:.4f}   -> {lo * FACTOR:.4f} .. {hi * FACTOR:.4f}")
    print(f"\n  {total:,} rows would be multiplied by {FACTOR:g} across "
          f"{', '.join(COLS)}\n")

    print("  daily bars in full:")
    for ts, o, h, l, c in con.execute(
            "select ts, open, high, low, close from price_bars where symbol=? and "
            "timeframe='1d' and close < ? order by ts", (SYMBOL, CEILING)):
        print(f"    {str(ts)[:10]}  {o:>8.4f} {h:>8.4f} {l:>8.4f} {c:>8.4f}"
              f"   ->  {c * FACTOR:>8.3f}")
    return rows


def _continuity(con) -> None:
    """The test that says the repair worked: the series has no step at the boundary.

    A count of updated rows proves the UPDATE ran, which is the proof that failed in C36.
    What matters is that the last bar before the flip and the first bar after it are once
    again a plausible tick apart instead of a factor of ten.
    """
    q = ("select ts, close from price_bars where symbol=? and timeframe='1m' "
         "and ts {op} ? order by ts {dir} limit 1")
    before = con.execute(q.format(op="<", dir="desc"),
                         (SYMBOL, "2026-08-15")).fetchone()
    after = con.execute(q.format(op=">=", dir="asc"),
                        (SYMBOL, "2026-08-15")).fetchone()
    if not before or not after:
        print("  continuity: one side of the boundary is missing; cannot check")
        return
    ratio = before[1] / after[1] if after[1] else float("inf")
    gap = abs(after[1] / before[1] - 1) * 100
    print(f"  continuity  {str(before[0])[:19]} {before[1]:.4f}  ->  "
          f"{str(after[0])[:19]} {after[1]:.4f}")
    print(f"              ratio {ratio:.4f}, step {gap:.3f}%", end="  ")
    if gap < 2.0:
        print("OK — a normal weekend gap")
    else:
        print("STILL BROKEN — the step is not a market move")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="perform the update; without it nothing is written")
    a = ap.parse_args()

    from db_config import resolve_writable_db_path
    try:
        db = resolve_writable_db_path()
    except FileNotFoundError as exc:
        print(f"Cannot reach the writable database — refusing to fall back to the "
              f"mirror.\n{exc}")
        return 1
    print(f"target  {db}\n")

    con = sqlite3.connect(db)
    try:
        rows = _report(con)
        if not rows:
            _continuity(con)
            return 0

        if not a.apply:
            print("DRY RUN — nothing written. Re-run with --apply to repair.")
            return 0

        sets = ", ".join(f"{c} = {c} * {FACTOR:g}" for c in COLS)
        cur = con.execute(
            f"update price_bars set {sets} where symbol=? and close < ?",
            (SYMBOL, CEILING))
        n = cur.rowcount
        con.commit()
        print(f"updated {n:,} rows")

        left = con.execute("select count(*) from price_bars where symbol=? and close < ?",
                           (SYMBOL, CEILING)).fetchone()[0]
        print(f"  remaining below {CEILING:g}: {left}")
        _continuity(con)
        if left:
            print("\nFAIL — rows are still below the ceiling.")
            return 1
        print("\nOK — repaired. Now refresh the mirror:")
        print("  python3 data_agent/quality/refresh_mirror.py")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
