#!/usr/bin/env python3
"""download_india_indices.py — Indian index dailies, via the shared store.

Thin wrapper over daily_bars.sync_symbols(). It writes nothing itself.

WHAT THIS REPLACES, AND WHY IT MATTERED
---------------------------------------
This script used to open price_bars and INSERT directly, formatting its timestamps
as '...T00:00:00Z'. Every symbol it writes — NIFTY, NIFTYIT, INDIAVIX, BANKNIFTY,
USDINR — is ALSO written by sync_sectors_yf / sync_nifty50_bars_yf /
sync_commodities, which use the canonical '...T00:00:00'.

Since ts is part of the primary key, those are two rows for the same session. The
next /api/sync-all-data would have re-duplicated roughly 2,117 sessions per symbol,
undoing a repair that had just been completed. Nothing would have errored.

It was the sixth writer bypassing the store layer, and it was the easiest to miss
because it lives under macro/ rather than fetching/ — the coverage tool did not
know it existed.

Now it goes through daily_bars.write_daily() like every other daily sync, which
means it inherits the foreign-format purge, exchange resolution from what is already
stored, and the known vendor corrections. Two jobs writing one symbol is now merely
redundant rather than corrupting: both produce identical rows.

The redundancy itself is tracked in data_agent/symbols.json, which declares one
owner per (symbol, timeframe). Resolving it is a separate step; making it harmless
came first.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_FETCH = os.path.join(_ROOT, "data_agent", "fetching")
sys.path.insert(0, _FETCH)
sys.path.append(_ROOT)

from daily_bars import sync_symbols

# DB symbols only. The Yahoo tickers live in daily_bars.INDEX_TICKERS — one place,
# so swapping a source never touches this file. That separation is the point:
# identity here, sourcing there.
INDICES = ["NIFTY", "NIFTYIT", "INDIAVIX", "BANKNIFTY", "USDINR"]


def main():
    ap = argparse.ArgumentParser(
        description="Daily Indian index bars into price_bars, via the shared store.")
    ap.add_argument("--since", default="2018-01-02", help="floor for a full pull")
    ap.add_argument("--full", action="store_true",
                    help="re-pull from --since instead of incrementally")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    db = args.db
    if not db:
        from bar_store import DB_PATH
        db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)

    print(f"database: {db}")
    print(f"indices ({len(INDICES)}), {'FULL' if args.full else 'incremental'}:")
    res = sync_symbols(INDICES, db, full=args.full, from_date=args.since)
    dead = [s for s, (n, t) in res.items() if t is None]
    if dead:
        print(f"\nNO DATA for {', '.join(dead)} — check daily_bars.INDEX_TICKERS.")
    print(f"\nwrote {sum(n for n, _ in res.values())} bars")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
