"""
Multi-instrument valuation tests. No DB needed.
Run: python -m pytest strategy_framework/tests/test_valuation.py -q
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from strategy_framework.portfolio import valuation as V


def test_stock_pnl_and_delta():
    pos = [{"id": "1", "label": "RELIANCE", "kind": "stock",
            "payload": {"symbol": "RELIANCE", "entry_price": 1400.0, "qty": 50}}]
    out = V.value_book(pos, None, {"RELIANCE": 1450.0}, spot=24000.0)
    assert out["lines"][0]["pnl_rupees"] == 2500.0        # (1450-1400)*50
    assert out["total_pnl_rupees"] == 2500.0


def test_future_pnl_and_delta():
    pos = [{"id": "2", "label": "NIFTY fut", "kind": "future",
            "payload": {"symbol": "NIFTY", "entry_price": 24000.0, "qty": 1, "lot_size": 75}}]
    out = V.value_book(pos, None, {"NIFTY": 24100.0}, spot=24100.0)
    assert out["lines"][0]["pnl_rupees"] == 7500.0        # (24100-24000)*1*75
    assert out["net_delta_rupees_per_point"] == 75.0      # 1 lot tracks index 1:1


def test_option_strategy_pnl_marks_to_chain():
    legs = [("call", 24000.0, +1), ("call", 24100.0, -1)]   # bull call spread
    entry = {"call:24000.0": 120.0, "call:24100.0": 60.0}   # net debit 60
    pos = [{"id": "3", "label": "bcs", "kind": "option_strategy",
            "payload": {"family": "bull_call_spread", "legs": [list(l) for l in legs],
                        "entry_prices": entry, "lot_size": 75}}]
    chain = {"call_ltp": {24000.0: 150.0, 24100.0: 80.0}, "put_ltp": {}, "spot": 24080.0}
    out = V.value_book(pos, chain, {}, spot=24080.0)
    # long +30 (150-120), short -20 (-(80-60)) => +10 pts * 75 = 750
    assert out["lines"][0]["pnl_rupees"] == 750.0


def test_combined_book_sums():
    pos = [
        {"id": "1", "label": "s", "kind": "stock",
         "payload": {"symbol": "TCS", "entry_price": 3000.0, "qty": 10}},
        {"id": "2", "label": "f", "kind": "future",
         "payload": {"symbol": "NIFTY", "entry_price": 24000.0, "qty": 1, "lot_size": 75}},
    ]
    out = V.value_book(pos, None, {"TCS": 3010.0, "NIFTY": 24010.0}, spot=24010.0)
    assert out["total_pnl_rupees"] == 100.0 + 750.0        # stock 100 + fut 750
    assert len(out["lines"]) == 2


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
