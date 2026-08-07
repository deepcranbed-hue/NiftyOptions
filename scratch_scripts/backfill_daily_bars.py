"""backfill_daily_bars.py — one-off DEEP backfill of daily bars, from YAHOO.

WHY THIS EXISTS
---------------
An audit found TATAMOTORS and ZOMATO holding 246 daily bars from 2025-08-06 while
all 46 other constituents hold 2,126 bars from 2018-01-01.

The cause is not the sync watermark alone — those two symbols were loaded from a
DIFFERENT VENDOR. Breeze serves only ~1 year of daily history, which is exactly
what they have. Everything else came from Yahoo, which serves the full range.

Two fingerprints in price_bars confirm it:

    RELIANCE   2018-01-01T00:00:00    405.70936311758567   <- Yahoo, no 'Z'
    TATAMOTORS 2025-08-06T00:00:00Z   655.2                <- Breeze, trailing 'Z'

So this script fetches from Yahoo, and has to reconcile two incompatibilities the
Breeze rows introduced.

1. TIMESTAMP FORMAT — `ts` is part of the primary key
    (exchange, symbol, timeframe, ts). The Breeze rows end in 'Z'; the Yahoo rows
    do not. Writing Yahoo-format rows on top of Breeze-format rows therefore does
    NOT replace them — it creates a SECOND row for every date in the overlap, and
    every downstream query silently double-counts the last year. This script
    writes the Yahoo format and, with --replace, deletes the symbol's existing
    rows first. Do not skip that.

2. PRICE BASIS — Yahoo rows here were fetched with auto_adjust=True: they are
    back-adjusted for splits AND dividends, which is why RELIANCE opens 2018 at
    ~405.71 with irrational decimals rather than its ~918 traded price. The Breeze
    rows are raw traded prices at 2 decimals. This script uses auto_adjust=True to
    match the 46, so the backfilled symbols end up on the same basis as the rest.

    Note this contradicts the "STORE ground truth, DERIVE at query time" comment in
    bar_store: in practice the stored series is already adjusted. Matching the
    majority is the lesser evil — a mixed-basis table is worse than a consistently
    adjusted one — but it is worth deciding deliberately rather than by accident.

USAGE  (must run where Yahoo is reachable — i.e. your machine)
--------------------------------------------------------------
    # see current coverage and per-symbol ts format; fetches nothing
    ./scratch_scripts/breeze_env/bin/python scratch_scripts/backfill_daily_bars.py --verify-only

    # fix the two broken symbols (--replace drops their Breeze rows first)
    ./scratch_scripts/breeze_env/bin/python scratch_scripts/backfill_daily_bars.py \
        --symbols TATAMOTORS,ZOMATO --replace

    # see what would happen, without writing
    ./scratch_scripts/breeze_env/bin/python scratch_scripts/backfill_daily_bars.py \
        --symbols TATAMOTORS,ZOMATO --replace --dry-run
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.append(os.path.join(_HERE, "breeze_env", "lib", "python3.9", "site-packages"))
sys.path.append(_ROOT)

DEFAULT_FROM = "2018-01-01"
_CSV = os.path.join(_ROOT, "nifty-50-stock-list.csv")

# The format the Yahoo-loaded majority uses. No trailing 'Z' — see docstring.
_TS_FMT = "%Y-%m-%dT00:00:00"

# Real listing dates. Requesting bars before these returns nothing, which would
# otherwise look like a fetch failure — so we clamp instead of alarming.
LISTED_FROM = {
    "ZOMATO": "2021-07-23",      # Zomato IPO (renamed Eternal in 2025)
    "ETERNAL": "2021-07-23",
    "JIOFIN": "2023-08-21",      # demerged out of Reliance
    "MAXHEALTH": "2020-08-21",
}

# Yahoo tickers. Renames and demergers are exactly where the NSE symbol and the
# Yahoo ticker diverge, so each symbol may be tried under several; the one
# returning the most rows wins. Extend rather than edit when NSE renames again.
TICKER_ALTS = {
    "TATAMOTORS": ["TATAMOTORS.NS", "TMPV.NS"],   # Oct-2025 demerger + rename
    "ZOMATO": ["ETERNAL.NS", "ZOMATO.NS"],        # renamed Eternal, 2025
    "M&M": ["M&M.NS"],
    "BAJAJ-AUTO": ["BAJAJ-AUTO.NS"],
}

CANONICAL_DB = os.getenv("OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")
MIRROR_DB = os.path.join(_ROOT, "option_chains.db")

_DB = None


def _same_file(a, b):
    """True only if a and b are the SAME file on disk.

    Deliberately (st_dev, st_ino) and not size: the repo-root option_chains.db and
    the Drive one are both exactly 329,240,576 bytes yet have different inodes.
    A size comparison reports 'identical' on precisely the case worth catching.
    """
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def _resolve_db(explicit=None):
    """Pick the bars database. TWO COPIES EXIST, ON PURPOSE.

    Drive  — written by the sync/backfill jobs, read by backend/ and all of
             data_agent/fundamentals. Source of truth; this is where we WRITE.
    Mirror — <repo>/option_chains.db, a manual copy of Drive kept so agents and
             tooling without Drive access can read the data.

    A copy, not a link — so the mirror goes stale the moment this script writes,
    and stays stale until re-copied. Order: --db -> OPTION_CHAINS_DB -> Drive ->
    mirror. Never silent about which.
    """
    global _DB
    if _DB:
        return _DB
    if explicit:
        _DB = explicit
    elif os.path.exists(CANONICAL_DB):
        _DB = CANONICAL_DB
    elif os.path.exists(MIRROR_DB):
        _DB = MIRROR_DB
        print(f"NOTE: Drive not reachable here; using the repo-root mirror "
              f"{MIRROR_DB}.\n      It is only as fresh as the last manual copy.\n")
    else:
        sys.exit("ERROR: no option_chains.db found — pass --db <path> or set "
                 "OPTION_CHAINS_DB.")
    print(f"database: {_DB}\n")
    return _DB


def _mirror_reminder():
    """Printed after a write: the mirror is now behind and must be re-copied."""
    if _DB and os.path.exists(MIRROR_DB) and not _same_file(MIRROR_DB, _DB):
        print("\nMIRROR IS NOW STALE — re-copy it, or agent-side analysis will still")
        print("read the old bars (equal file SIZES do not mean equal content):")
        print(f"   cp '{_DB}' '{MIRROR_DB}'")


def _coverage(symbols):
    """Coverage per symbol, INCLUDING ts format — the format is the whole story here."""
    out = {}
    con = sqlite3.connect(_resolve_db())
    for s in symbols:
        r = con.execute(
            "select count(*), min(ts), max(ts) from price_bars "
            "where symbol=? and timeframe='1d'", (s,)).fetchone()
        fmts = [x[0] for x in con.execute(
            "select distinct substr(ts,11) from price_bars "
            "where symbol=? and timeframe='1d'", (s,)).fetchall()]
        out[s] = {"bars": r[0], "first": (r[1] or "")[:10], "last": (r[2] or "")[:10],
                  "fmt": ",".join(sorted(fmts)) or "-"}
    con.close()
    return out


def _print_coverage(symbols, cov, title=None):
    if title:
        print(f"\n{'=' * 64}\n{title}\n{'=' * 64}")
    print(f"{'symbol':14}{'bars':>7}{'first':>13}{'last':>13}  {'ts format':<14}")
    for s in symbols:
        c = cov[s]
        flag = "  <- Breeze" if "Z" in c["fmt"] else ""
        print(f"{s:14}{c['bars']:>7}{c['first']:>13}{c['last']:>13}  {c['fmt']:<14}{flag}")


def _universe():
    with open(_CSV) as f:
        return [row["Symbol"].strip() for row in csv.DictReader(f) if row.get("Symbol")]


def _tickers_for(symbol):
    if symbol.upper() in TICKER_ALTS:
        return TICKER_ALTS[symbol.upper()]
    return [f"{symbol}.NS"]


def _fetch(ticker, start, end):
    """Full daily history from Yahoo, on the same basis as the existing 46 symbols."""
    import yfinance as yf
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), interval="1d",
                     auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return []
    # yfinance returns MultiIndex columns for some versions even with one ticker.
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df = df.droplevel(1, axis=1)

    # TIMEZONE — the reason the first version of this shifted every bar back a day.
    # yfinance hands back a tz-aware index for .NS tickers. Midnight IST is 18:30 UTC
    # on the PREVIOUS day, so formatting a UTC-expressed timestamp as a date yields
    # 2017-12-31 for what is really the 2018-01-01 session — a Sunday, when NSE is
    # shut. Convert to IST before taking the calendar date, never after.
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        try:
            idx = idx.tz_convert("Asia/Kolkata")
        except Exception:
            idx = idx.tz_localize(None)

    rows = []
    for when, (_, r) in zip(idx, df.iterrows()):
        try:
            ts = when.strftime(_TS_FMT)
            rows.append((ts, float(r["Open"]), float(r["High"]), float(r["Low"]),
                         float(r["Close"]), float(r["Volume"])))
        except Exception:
            continue
    return rows


def _calendar_check(rows, symbol, db, reference="RELIANCE"):
    """Would have caught the timezone shift immediately, so it now runs every time.

    Two independent tests, because each catches what the other misses:
      weekends — a shifted series lands sessions on Sat/Sun, which no exchange has;
      reference — compare dates against a symbol known to be correct. A uniform
                  shift produces near-total mismatch even though the bar COUNT
                  looks perfect, which is exactly how the first run looked right.
    """
    dates = {r[0][:10] for r in rows}
    weekend = sorted(d for d in dates
                     if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5)
    # A HANDFUL of weekend sessions are real: NSE runs a Muhurat session on Diwali,
    # e.g. Sunday 2019-10-27. RELIANCE has exactly 3 across 2018-2026. Hundreds are
    # not — the shifted run put 427 of TATAMOTORS' 2,126 bars on a weekend.
    if len(weekend) > 10:
        print(f"   WARNING: {len(weekend)} bars fall on a weekend "
              f"(e.g. {', '.join(weekend[:3])}) — dates look shifted.")
    elif weekend:
        print(f"   note: {len(weekend)} weekend sessions ({', '.join(weekend[:3])}) "
              f"— plausible Muhurat trading, not a shift.")

    con = sqlite3.connect(db)
    ref = {r[0][:10] for r in con.execute(
        "select ts from price_bars where symbol=? and timeframe='1d'", (reference,))}
    con.close()
    if not ref:
        return
    lo, hi = min(dates), max(dates)
    ref_win = {d for d in ref if lo <= d <= hi}
    missing = ref_win - dates          # sessions the reference has and we do not
    extra = dates - ref               # dates the reference has never traded
    if ref_win:
        overlap = 100.0 * len(dates & ref_win) / len(ref_win)
        verdict = "OK" if overlap >= 98 else "MISALIGNED"
        print(f"   calendar vs {reference}: {overlap:.1f}% of sessions match  [{verdict}]")
        if verdict == "MISALIGNED":
            print(f"      {len(missing)} sessions missing, {len(extra)} dates not in "
                  f"the {reference} calendar — suspect a date shift, not a data gap.")


def _purge(symbol, db):
    con = sqlite3.connect(db)
    n = con.execute("delete from price_bars where symbol=? and timeframe='1d'",
                    (symbol,)).rowcount
    con.commit()
    con.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="TATAMOTORS,ZOMATO",
                    help="comma list, or ALL for every constituent")
    ap.add_argument("--from", dest="from_date", default=DEFAULT_FROM)
    ap.add_argument("--verify-only", action="store_true",
                    help="report coverage and ts format, then exit — fetches nothing")
    ap.add_argument("--replace", action="store_true",
                    help="DELETE the symbol's existing 1d rows before writing. Required "
                         "for TATAMOTORS/ZOMATO: their Breeze rows use a 'Z' ts suffix, "
                         "so without this you get TWO rows per overlapping date.")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, but write nothing")
    ap.add_argument("--db", default=None, help="explicit bars database path")
    args = ap.parse_args()
    db = _resolve_db(args.db)

    symbols = _universe() if args.symbols.upper() == "ALL" else \
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    before = _coverage(symbols)
    _print_coverage(symbols, before, "BEFORE")
    if args.verify_only:
        print("\n--verify-only: nothing fetched.")
        return

    # Any existing rows whose ts will not collide with the incoming ones must be
    # deleted, or the write ADDS a parallel series instead of refreshing it. Two
    # ways that happens, both seen in this table:
    #   'Z' suffix      — Breeze rows; different string, same date.
    #   weekend dates   — rows written by the tz-shifted version of this script.
    con = sqlite3.connect(db)
    needs = {}
    for s in symbols:
        if "Z" in before[s]["fmt"]:
            needs[s] = "Breeze-format rows (ts ends 'Z')"
            continue
        wk = [d[0][:10] for d in con.execute(
            "select ts from price_bars where symbol=? and timeframe='1d'", (s,))
            if datetime.strptime(d[0][:10], "%Y-%m-%d").weekday() >= 5]
        if wk:
            needs[s] = f"{len(wk)} weekend-dated rows (e.g. {wk[0]}) — date-shifted"
    con.close()
    if needs and not args.replace:
        print("\nREFUSING TO WRITE — these would gain a duplicate parallel series:")
        for s, why in needs.items():
            print(f"   {s}: {why}")
        sys.exit("Re-run with --replace to purge them first.")

    from bar_store import save_bars

    start_floor = datetime.strptime(args.from_date, "%Y-%m-%d")
    today = datetime.now()

    for sym in symbols:
        floor = start_floor
        if sym in LISTED_FROM:
            listed = datetime.strptime(LISTED_FROM[sym], "%Y-%m-%d")
            if listed > floor:
                floor = listed
                print(f"\n{sym}: clamped to listing date {LISTED_FROM[sym]}")
        print(f"\n{sym}: fetching {floor.date()} -> {today.date()}")

        best, best_ticker = [], None
        for tk in _tickers_for(sym):
            try:
                rows = _fetch(tk, floor, today)
            except Exception as e:
                print(f"   [{tk}] error: {str(e)[:70]}")
                continue
            print(f"   [{tk}] {len(rows)} bars")
            if len(rows) > len(best):
                best, best_ticker = rows, tk

        if not best:
            print(f"   -> NO DATA for {sym} — check the ticker in TICKER_ALTS")
            continue
        print(f"   -> using {best_ticker}: {len(best)} bars "
              f"({best[0][0][:10]} -> {best[-1][0][:10]})")
        _calendar_check(best, sym, db)

        if args.dry_run:
            print("   (--dry-run: not written)")
            continue
        if args.replace:
            print(f"   purged {_purge(sym, db)} existing rows")
        save_bars(best, exchange="NSE", symbol=sym, timeframe="1d", db=db)
        time.sleep(0.5)          # be polite to Yahoo

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    _print_coverage(symbols, _coverage(symbols), "AFTER")
    _mirror_reminder()
    print("\nNext: re-run data_agent/fundamentals/earnings_reaction_backfill.py "
          "so the reaction record picks up the deeper history.")


if __name__ == "__main__":
    main()
