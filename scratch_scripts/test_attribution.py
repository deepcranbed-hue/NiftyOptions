import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_compare import strategy_pnl_comparison

def test_attribution():
    # 07-Jul Iron Condor
    # Short 24100 CE, Short 23800 PE, Long 24300 CE, Long 23600 PE
    legs = [
        ("CE", 24100, -1),
        ("PE", 23800, -1),
        ("CE", 24300, 1),
        ("PE", 23600, 1)
    ]

    cap_a = {
        "spot": 23961,
        "strikes": [23600, 23800, 24000, 24100, 24300],
        # Entry Prices: Net credit received (entry_value < 0)
        # E.g. Short 24100 CE at 50, Short 23800 PE at 60 (total collected 110)
        # Long 24300 CE at 10, Long 23600 PE at 15 (total paid 25)
        # Net collected = 85 points credit.
        "call_ltp": [0, 0, 0, 50, 10], 
        "put_ltp": [15, 60, 0, 0, 0], 
        "call_iv": [13.2, 13.2, 13.2, 13.2, 13.2],
        "put_iv": [13.2, 13.2, 13.2, 13.2, 13.2],
        "captured_at": "2026-07-01T10:00:00"
    }

    cap_b = {
        "spot": 24340,
        "strikes": [23600, 23800, 24000, 24100, 24300],
        # Exit Prices: 
        # Spot is at 24340, so 24100 CE is deep ITM (worth > 240)
        # E.g. 24100 CE at 260. Long 24300 CE at 70.
        # Short 23800 PE at 2, Long 23600 PE at 1.
        "call_ltp": [0, 0, 0, 260, 70],
        "put_ltp": [1, 2, 0, 0, 0],
        "call_iv": [9.6, 9.6, 9.6, 9.6, 9.6],
        "put_iv": [9.6, 9.6, 9.6, 9.6, 9.6],
        "captured_at": "2026-07-03T10:00:00"
    }

    res = strategy_pnl_comparison(legs, cap_a, cap_b, expiry_date="2026-07-07", db=None)
    
    assert res["attribution"]["net_short_premium"] is True, "Should be net short premium"
    
    reads = "\n".join(res["read"])
    print("READS:")
    print(reads)

    assert "HELPS" in reads and "IV drop" in reads, "Should say IV drop helps"
    assert "HELPS" in reads and "theta" in reads, "Should say theta helps"
    assert "BREACHED the call side" in reads, "Should detect call side breach"
    
    print("\nSUCCESS: All attribution assertions passed!")

if __name__ == "__main__":
    test_attribution()
