#!/usr/bin/env python3
"""sync_crudeoil_yf.py — WTI crude in USD, the long macro series.

Writes ONE symbol: CRUDEOIL = WTI, USD/bbl, NYMEX, from Yahoo CL=F.

The INR MCX contract is a DIFFERENT series and lives under CRUDEOIL_MCX, written by
sync_commodities.py. They used to share this symbol, which produced an 84x "price
move" on 2026-02-20 that was purely a change of currency, plus a 6.5-month hole
where neither feed wrote. impact_monitor.py reads CRUDEOIL against a 4% threshold
and oil is the top-ranked macro factor in the Nifty view, so that was a live false
signal, not a cosmetic problem. See daily_bars.NATIVE_CCY.

Same commodity on both sides — CL=F is WTI and MCX crude is WTI-linked; the -37.63
close on 2020-04-20 is the WTI negative settlement, which Brent never had. Only the
currency and venue differ, so no reconciliation is needed, just separate symbols.

Thin wrapper over daily_bars.sync_symbols(): incremental, correct IST dates, one ts
convention. Replaces a version that DELETEd and re-downloaded the whole history
every run.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.append(REPO_ROOT)

from daily_bars import sync_symbols


def main():
    from bar_store import DB_PATH
    db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    full = "--full" in sys.argv
    print(f"database: {db}\nCRUDEOIL (WTI, USD, CL=F), {'FULL' if full else 'incremental'}:")
    res = sync_symbols(["CRUDEOIL"], db, full=full)
    n, ticker = res.get("CRUDEOIL", (0, None))
    if ticker is None:
        print("NO DATA — CL=F returned nothing.")
    print(f"\nwrote {n} bars")


if __name__ == "__main__":
    main()
