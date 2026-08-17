#!/usr/bin/env python3
"""backfill_us_indices -- US risk factors into price_bars, so the AI-capex thesis
                          becomes testable for the first time.

WHY
The repo's daily table holds Indian instruments, commodities, USDINR and GIFT Nifty --
and no US equity history at all. `global_cues` has 18 rows, a live snapshot with no time
dimension. So "AI capex drives US tech, which drives Indian IT" has never been checkable
here. This loads ^GSPC / ^IXIC / ^NDX / ^SOX / ^VIX / ^DJI daily to 2018, alongside the
existing 2,100-session Indian history.

===========================================================================
THE ONE THING THAT MATTERS MORE THAN THE DOWNLOAD: SESSION ALIGNMENT
===========================================================================
The US close on date D is printed at 16:00 ET, which is roughly 01:30-02:30 IST on
date D+1. Sequence for a single calendar day D:

    09:15-15:30 IST   NIFTY trades and closes for day D
    19:00 IST         US market OPENS for day D
    ~01:30 IST (D+1)  US market CLOSES for day D

So the S&P's day-D close happens AFTER Nifty's day-D close. Two consequences, and
getting either wrong invalidates every result built on this data:

 1. corr( SP500(D), NIFTY(D) ) is NOT a predictive relationship. The US print did not
    exist when Nifty closed. That correlation measures two markets reacting to the same
    earlier global news, and reading it as "US leads India" is circular.

 2. The correct predictive join is  US(D) -> NIFTY(D+1).  The newest US close available
    to an Indian trader at the open on day T is the last US session STRICTLY BEFORE T.

Holidays make this worse, not simpler: NSE and NYSE keep different calendars, so "D+1"
is not a fixed offset and a naive .shift(1) silently mis-aligns around Diwali,
Thanksgiving, Good Friday and every other one-sided holiday. Forward-filling across a US
holiday is the other classic error -- it re-presents a stale close as a fresh
observation and manufactures autocorrelation.

This file therefore ships `us_asof_indian_session()`, which does the alignment properly
with a strict backward as-of merge. USE IT. The stored `ts` is always the TRUE US trade
date -- no dates are shifted on disk, because fabricating timestamps to make a join
convenient is how a database stops being trustworthy.

===========================================================================
TWO REPO-SPECIFIC HAZARDS, both already documented in retired_scratch/
===========================================================================
 * TIMESTAMP FORMAT. `ts` is part of the primary key (exchange, symbol, timeframe, ts).
   Yahoo-sourced rows in this DB use '2018-01-02T00:00:00' with NO trailing 'Z'; Breeze
   rows use 'Z'. Writing one format over the other does not replace -- it DUPLICATES,
   and every downstream query silently double-counts. This script writes the Yahoo
   format and offers --replace to clear a symbol first.
 * PRICE BASIS. auto_adjust=True to match the existing Yahoo rows. For price indices
   this is a no-op (an index level has no splits or dividends to adjust), but it is set
   explicitly so the convention is visible rather than assumed.

USAGE  (must run where Yahoo is reachable -- your machine, not a sandbox)
    python -m data_agent.macro.backfill_us_indices --probe
    python -m data_agent.macro.backfill_us_indices --dry-run
    python -m data_agent.macro.backfill_us_indices --from 2018-01-01
    python -m data_agent.macro.backfill_us_indices --verify     # after loading
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime

try:
    import pandas as pd
except ImportError:
    sys.exit("pandas required")

# The Drive copy is the source of truth; <repo>/option_chains.db is a read-only mirror.
# This used to default to the MIRROR and rely on the operator exporting $SQLITE_DB_PATH on
# every invocation — which DATA_AGENT_DAILY_CHECKLIST.md duly did, twice. A manual export
# standing in for a control is the same defect class as C37: forget it once and the write
# lands in a copy while the run reports success. Resolved AFTER argument parsing so --help
# and --probe still work where Drive is unreachable.
def _resolve_db(explicit: str | None) -> str:
    if explicit:
        return explicit
    from db_config import resolve_writable_db_path
    return resolve_writable_db_path()


# local symbol -> (yahoo ticker, fallbacks, description)
# Fallbacks matter: Yahoo intermittently stops serving ^SOX and ^NDX to unauthenticated
# clients. SOXX/SMH and QQQ are ETFs tracking the same exposure -- not identical (fees,
# tracking error, and they are dividend-adjusted), but far better than a missing factor.
SYMBOLS = {
    "SP500":   ("^GSPC", [],              "S&P 500"),
    "NASDAQ":  ("^IXIC", [],              "Nasdaq Composite"),
    "NDX100":  ("^NDX",  ["QQQ"],         "Nasdaq 100 (AI/mega-cap tech)"),
    "SOX":     ("^SOX",  ["SOXX", "SMH"], "PHLX Semiconductor -- the AI-capex proxy"),
    "VIX_US":  ("^VIX",  [],              "CBOE VIX (distinct from INDIAVIX)"),
    "DJIA":    ("^DJI",  [],              "Dow Jones Industrial Average"),
}
# Synthetic venue code: these are calculated index levels, not exchange-traded contracts,
# so no real venue applies. Kept distinct from NSE/NYMEX/COMEX/CDS so US rows are always
# separable with a single WHERE clause.
_EXCHANGE = "IDX"
_TS = "%Y-%m-%dT00:00:00"          # NO trailing Z -- see hazard note above


def us_asof_indian_session(us: pd.DataFrame, indian_dates) -> pd.DataFrame:
    """Latest US close KNOWN at the start of each Indian session.

    For Indian trading date T, returns the last US row with date STRICTLY BEFORE T.
    Strictness is the whole point: the US close stamped T lands after T's Indian close,
    so including it would leak information backwards by one session.

    Different holiday calendars mean the gap is 1 session on most days, 2 over a weekend,
    and more around one-sided holidays -- so `age_days` is returned alongside. Filter on
    it: a US close 5 days stale is not the same observation as yesterday's, and treating
    the two identically is how forward-filling quietly fabricates signal.
    """
    us = us.sort_index()
    left = pd.DataFrame({"ind": pd.to_datetime(pd.Index(indian_dates))}).sort_values("ind")
    right = us.reset_index().rename(columns={us.index.name or "index": "us_date"})
    right["us_date"] = pd.to_datetime(right["us_date"])
    # allow_exact_matches=False implements the strict inequality
    out = pd.merge_asof(left, right, left_on="ind", right_on="us_date",
                        direction="backward", allow_exact_matches=False)
    out["age_days"] = (out["ind"] - out["us_date"]).dt.days
    return out.set_index("ind")


def _flatten(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance returns MultiIndex columns for multi-ticker AND (recent versions) for
    single-ticker downloads. Normalise both shapes rather than assuming one."""
    if isinstance(df.columns, pd.MultiIndex):
        lv0 = df.columns.get_level_values(0)
        df = df.xs(ticker, axis=1, level=1) if ticker in df.columns.get_level_values(1) \
            else df.droplevel(1, axis=1)
        df.columns = [str(c) for c in df.columns] if not isinstance(df.columns, pd.MultiIndex) \
            else [str(c) for c in lv0]
    df.columns = [str(c).strip().title().replace(" ", "") for c in df.columns]
    return df


def _fetch(ticker: str, start: str, end: str):
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end, auto_adjust=True,
                     progress=False, threads=False)
    if df is None or df.empty:
        return None
    df = _flatten(df, ticker)
    need = {"Open", "High", "Low", "Close"}
    if not need.issubset(set(df.columns)):
        return None
    return df.dropna(subset=["Close"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=None,
                    help="override the resolved primary; normally omit")
    ap.add_argument("--from", dest="start", default="2018-01-01")
    ap.add_argument("--to", dest="end", default=date.today().isoformat())
    ap.add_argument("--only", nargs="*", help="subset of local symbol names")
    ap.add_argument("--replace", action="store_true",
                    help="delete existing rows for each symbol first (use if a prior run "
                         "wrote a different ts format -- see the hazard note)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="fetch 10 days per ticker, report which resolve, write nothing")
    ap.add_argument("--verify", action="store_true",
                    help="after loading: demonstrate why the session alignment matters")
    args = ap.parse_args()
    args.db = _resolve_db(args.db)
    want = {k: v for k, v in SYMBOLS.items() if not args.only or k in args.only}

    if args.probe:
        print("%-9s %-8s %7s  %s" % ("local", "ticker", "rows", "resolved"))
        print("-" * 58)
        for name, (tk, fbs, desc) in want.items():
            hit = None
            for cand in [tk] + fbs:
                try:
                    d = _fetch(cand, "2026-01-02", "2026-01-20")
                except Exception as e:
                    print("%-9s %-8s %7s  ERROR %s" % (name, cand, "-", type(e).__name__))
                    continue
                if d is not None and len(d):
                    hit = (cand, len(d)); break
            print("%-9s %-8s %7s  %s" % (name, hit[0] if hit else tk,
                                         hit[1] if hit else 0,
                                         "OK" if hit else "FAILED -- try a fallback"))
        return 0

    if not os.path.exists(args.db):
        return print(f"db not found: {args.db}") or 1
    conn = sqlite3.connect(args.db)

    if args.verify:
        return _verify(conn)

    total = 0
    for name, (tk, fbs, desc) in want.items():
        got = None
        for cand in [tk] + fbs:
            try:
                got = _fetch(cand, args.start, args.end)
            except Exception as e:
                print(f"{name}: {cand} error {type(e).__name__}: {e}")
                got = None
            if got is not None and len(got):
                if cand != tk:
                    print(f"  NOTE {name}: '{tk}' unavailable, fell back to '{cand}' "
                          f"-- an ETF proxy, not the index; dividend-adjusted and "
                          f"carrying tracking error. Record this.")
                break
        if got is None or not len(got):
            print(f"{name:9s} FAILED -- no data from {[tk] + fbs}")
            continue
        rows = [(_EXCHANGE, name, "1d", d.strftime(_TS),
                 float(r.Open), float(r.High), float(r.Low), float(r.Close),
                 float(getattr(r, "Volume", 0) or 0), 0.0)
                for d, r in got.iterrows()]
        print("%-9s %-6s %5d bars  %s .. %s  last close %.2f   %s"
              % (name, tk, len(rows), rows[0][3][:10], rows[-1][3][:10], rows[-1][7], desc))
        if args.dry_run:
            continue
        if args.replace:
            conn.execute("DELETE FROM price_bars WHERE symbol=? AND timeframe='1d'", (name,))
        conn.executemany(
            "INSERT OR REPLACE INTO price_bars (exchange,symbol,timeframe,ts,open,high,"
            "low,close,volume,open_interest) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        total += len(rows)

    if args.dry_run:
        print("\ndry run -- nothing written")
        return 0
    print(f"\nwrote {total} bars")
    print("\nREAD THIS BEFORE ANALYSING: the stored ts is the TRUE US trade date. The US "
          "close\nstamped D lands ~01:30 IST on D+1, AFTER Nifty's D close. Join with "
          "us_asof_indian_session()\nor with US(D) -> NIFTY(D+1). A same-day join is not "
          "a predictive relationship.")
    print("Run --verify to see the size of the difference on your own data.")
    conn.close()
    return 0


def _verify(conn) -> int:
    """Show, on the loaded data, why the alignment rule is not pedantry."""
    import numpy as np
    q = ("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol "
         "IN ('SOX','NASDAQ','SP500','NIFTY','NIFTYIT')")
    df = pd.read_sql(q, conn)
    df["d"] = pd.to_datetime(df.ts.str[:10])
    P = df.pivot_table(index="d", columns="symbol", values="close").sort_index()
    have = [s for s in ("SOX", "NASDAQ", "SP500") if s in P and P[s].notna().sum() > 100]
    if not have or "NIFTYIT" not in P:
        print("need US symbols and NIFTYIT loaded first")
        return 1
    print("%-9s %-9s %12s %12s %10s" % ("US factor", "target", "SAME-DAY r", "NEXT-DAY r", "n"))
    print("-" * 58)
    for us in have:
        ur = (P[us].dropna().pct_change() * 100).rename("us")
        for tgt in ("NIFTY", "NIFTYIT"):
            if tgt not in P: continue
            ir = (P[tgt].dropna().pct_change() * 100).rename("ind")
            j = pd.concat([ur, ir], axis=1).dropna()
            same = float(np.corrcoef(j.us, j.ind)[0, 1])
            k = pd.concat([ur, ir.shift(-1).rename("nxt")], axis=1).dropna()
            nxt = float(np.corrcoef(k.us, k.nxt)[0, 1])
            print("%-9s %-9s %12.3f %12.3f %10d" % (us, tgt, same, nxt, len(j)))
    print("\nSAME-DAY is the misleading column: that US close printed AFTER the Indian "
          "close,\nso it cannot have caused the Indian move and cannot be traded on. "
          "NEXT-DAY is the\nonly one that answers 'does US tech lead Indian IT?'. If "
          "same-day is large and\nnext-day is near zero, the lead is an illusion of "
          "shared news, not causation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
