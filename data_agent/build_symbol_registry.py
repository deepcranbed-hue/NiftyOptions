#!/usr/bin/env python3
"""build_symbol_registry.py — generate data_agent/symbols.json from what is TRUE.

Run once to bootstrap the registry, then hand-curate. Regenerating later is a
DIFF exercise, not a replace: the file is the contract, and this script only
reports what the database and the sync scripts currently say.

WHY A REGISTRY
--------------
Symbol identity must not move when a data source changes. Today the identity of a
symbol is scattered: its exchange lives in price_bars rows, its Yahoo ticker in
daily_bars.TICKER_ALTS, its Upstox key in sync_commodities.SYMBOLS_MAP, its
membership in one of seven hardcoded lists, and its currency nowhere at all. Swap
Yahoo for Upstox on one symbol and you have to find and agree all five.

The registry separates the two:

    IDENTITY  symbol, exchange, currency, instrument, unit   — stable, rarely changes
    SOURCING  per timeframe: source, ticker/key, owning job  — changes freely

Change a source by editing one SOURCING entry. Identity is untouched, so no new
symbol appears and no duplicate can be created.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_FETCH = os.path.join(_HERE, "fetching")
sys.path.insert(0, _FETCH)
sys.path.append(_ROOT)

OUT = os.path.join(_HERE, "symbols.json")

# Currency and unit are NOT derivable from the database — price_bars has no column
# for either. They are stated here once, which is the whole point of the registry.
CURRENCY = {"CRUDEOIL": "USD", "BRENT": "USD"}          # everything else INR
UNIT = {
    "GOLD": "INR per 10 g", "SILVER": "INR per kg", "COPPER": "INR per kg",
    "CRUDEOIL_MCX": "INR per barrel", "CRUDEOIL": "USD per barrel",
    "USDINR": "INR per USD",
}
INSTRUMENT = {}     # filled below from membership


def _list_literal(path, var):
    try:
        src = open(path).read()
    except OSError:
        return []
    m = re.search(rf"^{var}\s*=\s*\[(.*?)\]", src, re.S | re.M)
    if not m:
        return []
    return [t.strip().strip("'\"") for t in m.group(1).replace("\n", " ").split(",")
            if t.strip() and not t.strip().startswith("#")]


def _dict_keys(path, var):
    try:
        src = open(path).read()
    except OSError:
        return []
    m = re.search(rf"^{var}\s*=\s*\{{(.*?)^\}}", src, re.S | re.M)
    return re.findall(r'"([A-Z0-9_&\-]+)"\s*:', m.group(1)) if m else []


def owners_1d():
    """{symbol: [job, ...]} for daily bars, read from the scripts."""
    own = {}

    def add(syms, job):
        for s in syms:
            own.setdefault(s.upper(), []).append(job)

    with open(os.path.join(_ROOT, "nifty-50-stock-list.csv"), newline="") as f:
        add([r["Symbol"].strip() for r in csv.DictReader(f) if r.get("Symbol")],
            "sync_nifty50_bars_yf")
    add(["NIFTY"], "sync_nifty50_bars_yf")
    from daily_bars import INDEX_TICKERS
    add([s for s in INDEX_TICKERS if s.startswith("NIFTY") and s != "NIFTY"],
        "sync_sectors_yf")
    add(["BANKNIFTY", "INDIAVIX"], "sync_sectors_yf")
    add(_list_literal(os.path.join(_FETCH, "sync_bank_bars_yf.py"), "BANKS"),
        "sync_bank_bars_yf")
    add(_list_literal(os.path.join(_FETCH, "sync_it_bars_yf.py"), "IT_STOCKS"),
        "sync_it_bars_yf")
    add(_list_literal(os.path.join(_FETCH, "sync_finnifty_bars_yf.py"), "FINNIFTY"),
        "sync_finnifty_bars_yf")
    add(["CRUDEOIL"], "sync_crudeoil_yf")
    add(_dict_keys(os.path.join(_FETCH, "sync_commodities.py"), "SYMBOLS_MAP"),
        "sync_commodities")
    add(["NIFTY_FUT_1", "NIFTY_FUT_2"], "sync_nifty50_to_now")
    # macro/download_india_indices.py — the writer that hid under macro/
    try:
        src = open(os.path.join(_HERE, "macro", "download_india_indices.py")).read()
        m = re.search(r"symbol_map\s*=\s*\{(.*?)\}", src, re.S)
        if m:
            add(re.findall(r':\s*"([A-Z0-9_]+)"', m.group(1)),
                "macro/download_india_indices")
    except OSError:
        pass
    return own


def main():
    from bar_store import DB_PATH
    db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    con = sqlite3.connect(db)
    rows = con.execute(
        "select symbol, exchange, timeframe, count(*), min(ts), max(ts) "
        "from price_bars group by 1,2,3").fetchall()
    con.close()

    from daily_bars import TICKER_ALTS
    upstox = {}
    try:
        src = open(os.path.join(_FETCH, "sync_commodities.py")).read()
        m = re.search(r"^SYMBOLS_MAP\s*=\s*\{(.*?)^\}", src, re.S | re.M)
        if m:
            upstox = dict(re.findall(r'"([A-Z0-9_]+)":\s*\{"key":\s*"([^"]+)"', m.group(1)))
    except OSError:
        pass

    own = owners_1d()
    syms = {}
    for s, ex, tf, n, first, last in rows:
        e = syms.setdefault(s, {
            "exchange": ex,
            "currency": CURRENCY.get(s, "INR"),
            "unit": UNIT.get(s),
            "instrument": INSTRUMENT.get(s, "equity" if ex == "NSE" else "other"),
            "timeframes": {},
        })
        if e["exchange"] != ex:
            e["exchange_CONFLICT"] = sorted({e["exchange"], ex})
        src_name = ("upstox" if s in upstox else
                    "yahoo" if tf == "1d" and s in own and
                    any("_yf" in j for j in own[s]) else "breeze")
        e["timeframes"][tf] = {
            "source": src_name,
            "ticker": (TICKER_ALTS.get(s, [f"{s}.NS"])[0] if src_name == "yahoo"
                       else upstox.get(s)),
            "owner": own.get(s, ["UNOWNED"]) if tf == "1d" else ["(intraday path)"],
            "bars": n, "first": first[:10], "last": last[:10],
        }

    doc = {
        "version": 1,
        "generated_from": "live price_bars + the sync scripts, 2026-08-08",
        "contract": (
            "IDENTITY (symbol, exchange, currency, unit, instrument) is stable and "
            "must not change when a data source changes. SOURCING (source, ticker, "
            "owner) per timeframe may change freely. Exactly ONE owner per "
            "(symbol, timeframe). Adding a symbol or changing a source means "
            "editing THIS file first."),
        "symbols": dict(sorted(syms.items())),
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1)

    dupes = {s: v["timeframes"]["1d"]["owner"] for s, v in syms.items()
             if "1d" in v["timeframes"] and len(v["timeframes"]["1d"]["owner"]) > 1}
    unowned = [s for s, v in syms.items()
               if "1d" in v["timeframes"] and v["timeframes"]["1d"]["owner"] == ["UNOWNED"]]
    print(f"wrote {OUT}: {len(syms)} symbols")
    print(f"  multiple owners (1d): {len(dupes)}")
    for s, o in sorted(dupes.items()):
        print(f"     {s:14} {', '.join(o)}")
    print(f"  unowned (1d): {unowned or 'none'}")


if __name__ == "__main__":
    main()
