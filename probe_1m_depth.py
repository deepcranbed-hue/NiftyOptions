"""probe_1m_depth.py — how far back does Upstox actually serve 1-minute candles?

INTRADAY_FROM is set to 2026-06-29 in sync_commodities.py. Nobody recorded whether
that is a vendor limit or a number somebody picked, and the difference matters: one
is a wall, the other is a setting.

Two limits could bind and they are not the same:
  * the API's own retention for the 1minute interval
  * the contract's listing date — a contract has no minutes before it existed

Probes month-long windows backwards until two consecutive windows come back empty.
Read-only.
"""
import os, sys
from datetime import date, timedelta
sys.path.insert(0, "data_agent/fetching"); sys.path.insert(0, "scratch_scripts")
sys.path.insert(0, ".")
from sync_commodities import fetch_1m, SYMBOLS_MAP          # noqa: E402

TARGETS = [
    ("CRUDEOIL_MCX_2026-08-19", "MCX_FO|560977"),   # front crude, expires in 10 days
    ("CRUDEOIL_MCX_2026-09-21", "MCX_FO|565899"),   # next crude
]

for label, key in TARGETS:
    print(f"=== {label}  ({key}) ===")
    end = date(2026, 8, 8)
    empties = 0
    earliest = None
    for _ in range(30):                       # up to ~30 months back
        start = end - timedelta(days=30)
        try:
            rows = fetch_1m(key, start.isoformat(), end.isoformat())
        except Exception as e:                # noqa: BLE001
            print(f"   {start} .. {end}   ERROR {str(e)[:70]}")
            break
        n = len(rows or [])
        print(f"   {start} .. {end}   {n:>6,} candles")
        if n:
            earliest, empties = start, 0
        else:
            empties += 1
            if empties >= 2:
                print("   two empty windows in a row — stopping")
                break
        end = start - timedelta(days=1)
    print(f"   -> earliest window with data: {earliest}\n")
print("If data reaches well before 2026-06-29, INTRADAY_FROM is just a setting.")
