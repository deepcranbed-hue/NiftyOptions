import os
import sys
from datetime import datetime

# Import strategy desk modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from strategy_framework.signals import data_access
from strategy_framework.signals import derisk_preopen

def test_preopen(date_str: str):
    # e.g., 2026-07-09
    now_ts = f"{date_str}T03:40:00Z" # 09:10 IST
    from bar_store import DB_PATH
    da = data_access.DataAccess(DB_PATH)
    print(f"\nEvaluating pre-open signal at: {now_ts}")
    
    # 1. Check if there are bars at all
    for sym in derisk_preopen.OVERNIGHT:
        bars = da.bars(sym, "1m", end=now_ts, limit=5)
        print(f"  {sym} as-of bars count: {len(bars)}")
        if bars:
            print(f"    Latest bar: {bars[-1]['ts']}, close: {bars[-1]['close']}")
            
    # 2. Compute signal
    sig = derisk_preopen.compute(da, now_ts, {})
    print(f"Signal score: {sig.score}, confidence: {sig.confidence}")
    print("Signal details:")
    import pprint
    pprint.pprint(sig.detail)

def main():
    print("=== Testing pre-open calculation for July 9th ===")
    test_preopen("2026-07-09")
    
    print("\n=== Testing pre-open calculation for July 10th ===")
    test_preopen("2026-07-10")

if __name__ == "__main__":
    main()
