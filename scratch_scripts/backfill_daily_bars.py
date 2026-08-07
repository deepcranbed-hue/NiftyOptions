"""backfill_daily_bars.py — one-off DEEP backfill of daily bars.

WHY THIS EXISTS
---------------
sync_nifty50_to_now.py starts a symbol with no watermark from a 365-day floor
(`one_year_ago = now_ist - timedelta(days=365)`), and from then on only ever moves
the watermark FORWARD. So any symbol added to the universe later receives exactly
one year of history and can never deepen it, no matter how often the sync runs.

That is why an audit found TATAMOTORS and ZOMATO holding 246 bars from 2025-08-06
while all 46 other constituents hold 2,126 bars from 2018 — both were re-added
after a corporate identity change (Tata Motors' Oct-2025 demerger; Zomato's rename
to Eternal). Their reaction statistics were computed on ~4 events and are
unusable until this is run.

This script deliberately BYPASSES the watermark and re-fetches a full range,
chunked by calendar year, writing through bar_store.save_bars — the canonical
writer (INSERT OR REPLACE on the natural key), so re-running is safe and simply
refreshes existing rows.

PRICES ARE STORED RAW
---------------------
Per the bar_store contract ("STORE ground truth only ... DERIVE at query time"),
bars are written exactly as the vendor returns them: NOT adjusted for splits,
bonuses or demergers. TATAMOTORS will therefore still show the 2025-10-14
demerger discontinuity after this runs, and that is CORRECT — adjustment belongs
to the consumer. The known ratios live in `_KNOWN_ACTIONS` /`_ACTIONS` in
data_agent/fundamentals/earnings_reaction_backfill.py.

USAGE  (must run where Breeze credentials + network exist — i.e. your machine)
-----------------------------------------------------------------------------
    # see current coverage without fetching anything
    ./scratch_scripts/breeze_env/bin/python scratch_scripts/backfill_daily_bars.py --verify-only

    # backfill the two broken symbols to 2018
    ./scratch_scripts/breeze_env/bin/python scratch_scripts/backfill_daily_bars.py \
        <session_token> --symbols TATAMOTORS,ZOMATO

    # backfill everything (idempotent; safe but slow)
    ./scratch_scripts/breeze_env/bin/python scratch_scripts/backfill_daily_bars.py \
        <session_token> --symbols ALL --from 2018-01-01
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.append(os.path.join(_HERE, "breeze_env", "lib", "python3.9", "site-packages"))
sys.path.append(_ROOT)

DEFAULT_FROM = "2018-01-01"
_MAP_JSON = os.path.join(_ROOT, "strategy_framework", "config", "breeze_symbol_map.json")
_CSV = os.path.join(_ROOT, "nifty-50-stock-list.csv")

# Real listing dates. Requesting bars before these returns nothing, which would
# otherwise look like a fetch failure — so we clamp instead of alarming.
LISTED_FROM = {
    "ZOMATO": "2021-07-23",      # Zomato IPO (renamed Eternal in 2025)
    "ETERNAL": "2021-07-23",
    "JIOFIN": "2023-08-21",      # demerged out of Reliance
    "MAXHEALTH": "2020-08-21",
    "HDFCLIFE": "2017-11-17",
    "SBILIFE": "2017-10-03",
}

# Vendor stock_code fallbacks. Renames/demergers are exactly where the NSE symbol
# and the Breeze code diverge, so each symbol may be tried under several codes;
# the first that returns rows wins. Extend rather than edit when NSE renames again.
CODE_FALLBACKS = {
    "ZOMATO": ["ZOMATO", "ETERNAL", "ZOMLIM"],
    "ETERNAL": ["ETERNAL", "ZOMATO", "ZOMLIM"],
    "TATAMOTORS": ["TATMOT", "TATAMOTORS", "TMPV", "TATMOTPV"],
    "TMPV": ["TMPV", "TATMOT", "TATAMOTORS"],
}


# Resolved once, printed loudly. See _resolve_db for why this is not a one-liner.
_DB = None


CANONICAL_DB = os.getenv("OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")


def _same_file(a, b):
    """True only if a and b are the SAME file on disk.

    Deliberately (st_dev, st_ino) and not size: the repo-root option_chains.db and
    the Drive one are both exactly 329,240,576 bytes yet have different inodes.
    A size comparison here reports 'identical' on precisely the case this guard
    exists to catch.
    """
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


MIRROR_DB = os.path.join(_ROOT, "option_chains.db")


def _resolve_db(explicit=None):
    """Pick the bars database. TWO COPIES EXIST, ON PURPOSE.

    Drive  — written by the sync/backfill jobs, read by backend/ and all of
             data_agent/fundamentals. Source of truth; this is where we WRITE.
    Mirror — <repo>/option_chains.db, a manual copy of Drive kept so agents and
             tooling without Drive access can read the data.

    Same size, different inode: a copy, not a link. So the mirror goes stale the
    moment this script writes to Drive, and stays stale until it is re-copied.
    Order: --db -> OPTION_CHAINS_DB -> Drive -> mirror. Never silent about which.
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


def _db_path():
    return _resolve_db()


def _coverage(symbols):
    """Current bar coverage per symbol — used by --verify-only and the after-report."""
    out = {}
    con = sqlite3.connect(_db_path())
    for s in symbols:
        r = con.execute(
            "select count(*), min(ts), max(ts) from price_bars "
            "where symbol=? and timeframe='1d'", (s,)).fetchone()
        out[s] = {"bars": r[0], "first": (r[1] or "")[:10], "last": (r[2] or "")[:10]}
    con.close()
    return out


def _universe():
    with open(_CSV) as f:
        return [row["Symbol"].strip() for row in csv.DictReader(f) if row.get("Symbol")]


def _breeze_map():
    try:
        with open(_MAP_JSON) as f:
            return json.load(f)
    except Exception:
        return {}


def _codes_for(symbol, bmap):
    """Candidate vendor codes, most likely first, de-duplicated."""
    cands = list(CODE_FALLBACKS.get(symbol.upper(), []))
    mapped = bmap.get(symbol.upper())
    if mapped:
        cands.insert(0, mapped)
    cands.append(symbol)
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _fetch_year(breeze, code, y_from, y_to):
    res = breeze.get_historical_data_v2(
        interval="1day",
        from_date=y_from.strftime("%Y-%m-%dT00:00:00.000Z"),
        to_date=y_to.strftime("%Y-%m-%dT23:59:59.000Z"),
        stock_code=code, exchange_code="NSE", product_type="cash")
    rows = []
    for item in (res or {}).get("Success", []) or []:
        ds = item.get("datetime")
        if not ds:
            continue
        try:
            ts = datetime.strptime(ds[:10], "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00Z")
        except Exception:
            ts = ds
        rows.append((ts, float(item.get("open", 0.0)), float(item.get("high", 0.0)),
                     float(item.get("low", 0.0)), float(item.get("close", 0.0)),
                     float(item.get("volume", 0.0))))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session_token", nargs="?", help="Breeze session token")
    ap.add_argument("--symbols", default="TATAMOTORS,ZOMATO",
                    help="comma list, or ALL for every constituent")
    ap.add_argument("--from", dest="from_date", default=DEFAULT_FROM)
    ap.add_argument("--verify-only", action="store_true",
                    help="report current coverage and exit — fetches nothing")
    ap.add_argument("--db", default=None,
                    help="explicit bars database path (see _resolve_db — this repo has "
                         "two candidate option_chains.db files)")
    args = ap.parse_args()
    _resolve_db(args.db)

    symbols = _universe() if args.symbols.upper() == "ALL" else \
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    before = _coverage(symbols)
    print(f"{'symbol':14}{'bars':>7}{'first':>13}{'last':>13}")
    for s in symbols:
        b = before[s]
        print(f"{s:14}{b['bars']:>7}{b['first']:>13}{b['last']:>13}")
    if args.verify_only:
        print("\n--verify-only: nothing fetched.")
        return
    if not args.session_token:
        sys.exit("\nERROR: session_token required to fetch (or pass --verify-only).")

    from bar_store import save_bars
    from breeze_connect import BreezeConnect

    # Same idiom as data_agent/fundamentals/*: load .env if present, env still wins.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_ROOT, ".env"))
    except Exception:
        pass

    api_key = os.getenv("BREEZE_API_KEY")
    api_secret = os.getenv("BREEZE_API_SECRET")
    if not (api_key and api_secret):
        sys.exit(
            "ERROR: BREEZE_API_KEY / BREEZE_API_SECRET not set.\n"
            "  They are currently hardcoded in scratch_scripts/sync_nifty50_to_now.py\n"
            "  (~line 122). Copy them into .env (already gitignored) as:\n"
            "      BREEZE_API_KEY=...\n"
            "      BREEZE_API_SECRET=...\n"
            "  or export them for this shell.")
    breeze = BreezeConnect(api_key=api_key)
    breeze.generate_session(api_secret=api_secret, session_token=args.session_token)

    bmap = _breeze_map()
    start_floor = datetime.strptime(args.from_date, "%Y-%m-%d")
    today = datetime.now()

    for sym in symbols:
        floor = start_floor
        if sym in LISTED_FROM:
            listed = datetime.strptime(LISTED_FROM[sym], "%Y-%m-%d")
            if listed > floor:
                floor = listed
                print(f"\n{sym}: clamped to listing date {LISTED_FROM[sym]}")
        print(f"\n{sym}: backfilling {floor.date()} -> {today.date()}")

        total, used_code = 0, None
        for code in _codes_for(sym, bmap):
            got_any = False
            for year in range(floor.year, today.year + 1):
                y_from = max(floor, datetime(year, 1, 1))
                y_to = min(today, datetime(year, 12, 31))
                if y_from > y_to:
                    continue
                try:
                    rows = _fetch_year(breeze, code, y_from, y_to)
                except Exception as e:
                    print(f"   {year} [{code}] error: {str(e)[:70]}")
                    continue
                if rows:
                    # Stored under the DB symbol, not the vendor code, so the
                    # existing series stays continuous.
                    save_bars(rows, exchange="NSE", symbol=sym, timeframe="1d",
                              db=_db_path())
                    total += len(rows)
                    got_any = True
                    print(f"   {year} [{code}] {len(rows):>4} bars")
                time.sleep(0.35)          # stay inside the vendor rate limit
            if got_any:
                used_code = code
                break
            print(f"   [{code}] returned nothing — trying next code")
        print(f"   -> {total} bars written for {sym}"
              f"{f' via vendor code {used_code}' if used_code else ' (NO DATA — check the code)'}")

    print("\n" + "=" * 52 + "\nAFTER\n" + "=" * 52)
    after = _coverage(symbols)
    print(f"{'symbol':14}{'bars':>7}{'first':>13}{'last':>13}   change")
    for s in symbols:
        a, b = after[s], before[s]
        print(f"{s:14}{a['bars']:>7}{a['first']:>13}{a['last']:>13}   {a['bars'] - b['bars']:+d}")
    _mirror_reminder()
    print("\nNext: re-run data_agent/fundamentals/earnings_reaction_backfill.py "
          "so the reaction record picks up the deeper history.")


if __name__ == "__main__":
    main()
