#!/usr/bin/env python3
"""sync_sectors_yf.py — daily bars for the NSE sector indices.

Thin wrapper over daily_bars.sync_symbols(). All fetch/write/verify logic lives
there so this file cannot drift from the Nifty 50 sync the way it used to.

WHAT THIS REPLACES
------------------
The previous version DELETEd each symbol's whole history and re-downloaded from
2018 on every run, used index.tz_localize(None) (which silently shifts every date
back a day whenever yfinance returns a UTC index), and wrote 'CNXIT' and 'NIFTYIT'
from the SAME Yahoo ticker — one series stored under two names.

It also probably was not working at all. Every sector row in price_bars carries a
'Z' timestamp, which is the Breeze orchestrator's format, not the format this
script writes — so the ^CNX* tickers were most likely dead and the failures went
unnoticed. Hence the candidate lists in daily_bars.INDEX_TICKERS: whichever ticker
returns bars wins, and the run PRINTS which one, so a dead symbol is visible.

WHY THIS MATTERS BEYOND TIDINESS
--------------------------------
earnings_reaction_backfill measures every stock's results-day move against its own
sector index. Only 11 of 13 sector indices held more than one year of history, so
just 323 of 1,633 events (20%) had a sector-relative number at all. Deep history
here is what unlocks the other 80%.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.append(REPO_ROOT)

from daily_bars import sync_symbols, INDEX_TICKERS

# CNXIT / NSEBANK are deliberately NOT here. They duplicate NIFTYIT / BANKNIFTY
# from the same Yahoo ticker; download_india_indices.py already treats
# ^CNXIT -> NIFTYIT and ^NSEBANK -> BANKNIFTY as canonical. Writing both is what
# created the 257-bar shadow copies.
SECTORS = [s for s in INDEX_TICKERS if s.startswith("NIFTY") and s != "NIFTY"]
# INDIAVIX belongs here too: it was left to a Breeze path that no longer writes
# daily bars, so it sat on the old Z format with nothing to refresh it.
SECTORS += ["BANKNIFTY", "INDIAVIX"]


def main():
    from bar_store import DB_PATH
    db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    full = "--full" in sys.argv
    print(f"database: {db}\nsector indices ({len(SECTORS)}), {'FULL' if full else 'incremental'}:")
    res = sync_symbols(SECTORS, db, full=full)
    dead = [s for s, (n, t) in res.items() if t is None]
    if dead:
        print(f"\nNO DATA for {', '.join(dead)} — the Yahoo ticker is probably retired.")
        print("Add a working candidate to INDEX_TICKERS in daily_bars.py.")
    print(f"\nwrote {sum(n for n, _ in res.values())} bars")


if __name__ == "__main__":
    main()
