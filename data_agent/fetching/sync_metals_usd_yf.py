#!/usr/bin/env python3
"""sync_metals_usd_yf.py — USD continuous metals from Yahoo, via the shared store.

    GOLD_USD    GC=F   USD per troy ounce, COMEX
    SILVER_USD  SI=F   USD per troy ounce, COMEX
    COPPER_USD  HG=F   USD per pound,      COMEX

WHY THESE EXIST ALONGSIDE THE MCX SERIES
----------------------------------------
MCX per-contract history cannot be rebuilt: Upstox delists a contract at expiry and
the numeric token needed to fetch it afterwards was never recorded before
2026-07-28. So the Indian series starts in 2025 and its early history is an
unlabelled splice across contracts.

Yahoo already solves that problem for the international contract — it rolls and
back-adjusts continuously, back to 2018. probe_continuous_commodities.py checked
each of these against landed parity (spot x USDINR x unit x duty x GST) and they
reconciled, so they track the same metal as the MCX contract.

    GOLD      MCX, INR per 10g, per contract   -> algo, basis, landed parity
    GOLD_USD  COMEX, USD/oz, continuous        -> long history, analysis

THE TWO ARE NOT INTERCHANGEABLE AND MUST NEVER BE CONVERTED INTO EACH OTHER.
The India basis is duty plus GST plus a physical premium — around 18.45% on silver
alone — and it moves. Converting one into the other measures the tax code.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.append(REPO_ROOT)

from daily_bars import sync_symbols

METALS = ["GOLD_USD", "SILVER_USD", "COPPER_USD"]


def main():
    from bar_store import DB_PATH
    db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    full = "--full" in sys.argv
    print(f"database: {db}")
    print(f"USD continuous metals, {'FULL' if full else 'incremental'}:")
    res = sync_symbols(METALS, db, full=full, from_date="2018-01-01")
    dead = [s for s, (n, t) in res.items() if t is None]
    if dead:
        print(f"\nNO DATA for {', '.join(dead)} — check daily_bars.COMMODITY_TICKERS.")
    print(f"\nwrote {sum(n for n, _ in res.values())} bars")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
