#!/usr/bin/env python3
"""sync_bank_bars_yf — daily bars for BANKNIFTY constituents.

Thin wrapper over daily_bars.sync_symbols(); all fetch/write/verify logic lives
there, so this file cannot drift from the Nifty 50 sync the way it used to.

Replaces a version that DELETEd each symbol's full history and re-downloaded from
2018 every run — slow, and it silently reverted any correction held in the
database — and that used index.tz_localize(None), which shifts every date back a
day whenever yfinance hands back a UTC index. Both bugs cost real analysis time.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.append(REPO_ROOT)

from daily_bars import sync_symbols

BANKS = [
    'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK',
    'INDUSINDBK', 'BANKBARODA', 'PNB', 'AUBANK', 'IDFCFIRSTB',
    'FEDERALBNK', 'BANDHANBNK',
]


def main():
    from bar_store import DB_PATH
    db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    full = "--full" in sys.argv
    print(f"database: {db}\nBANKNIFTY constituents ({len(BANKS)}), {'FULL' if full else 'incremental'}:")
    res = sync_symbols(BANKS, db, full=full)
    dead = [s for s, (n, t) in res.items() if t is None]
    if dead:
        print(f"\nNO DATA for {', '.join(dead)} — check the ticker in daily_bars.TICKER_ALTS.")
    print(f"\nwrote {sum(n for n, _ in res.values())} bars")


if __name__ == "__main__":
    main()
