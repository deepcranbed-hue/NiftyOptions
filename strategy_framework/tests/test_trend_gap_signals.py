"""
Behavioural tests for the trend-behaviour gap signals: adx (trend strength/direction)
and choppiness (regime trend↔chop). Synthetic NIFTY 1m bars.
"""
import os
import sqlite3
import numpy as np
from strategy_framework.signals.data_access import DataAccess
from strategy_framework.signals import adx, choppiness


def _bars_db(path_dir, closes):
    """DB with NIFTY 1m OHLC from a close path (H/L a couple ticks around close)."""
    os.makedirs(path_dir, exist_ok=True)
    db = os.path.join(str(path_dir), "b.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, ts TEXT, "
                "open REAL, high REAL, low REAL, close REAL, volume REAL)")
    ts = None
    for i, c in enumerate(closes):
        ts = f"2026-01-01T0{i // 60}:{i % 60:02d}:00Z"
        con.execute("INSERT INTO price_bars VALUES ('NSE','NIFTY','1m',?,?,?,?,?,100)",
                    (ts, c, c + 2, c - 2, c))
    con.commit(); con.close()
    return DataAccess(db)


def test_adx_stronger_on_trend_than_chop(tmp_path):
    now = "2026-01-01T00:49:00Z"
    trend = list(np.linspace(24000, 24300, 50))                      # straight up
    chop = [24000 + (30 if i % 2 else -30) for i in range(50)]       # oscillate, no net travel
    s_t = adx.compute(_bars_db(tmp_path / "t", trend), now, {})
    s_c = adx.compute(_bars_db(tmp_path / "c", chop), now, {})
    assert s_t.status == "OK" and s_c.status == "OK"
    assert s_t.score > 0.3 and s_t.detail["adx"] > 20                # clear up-trend, strong ADX
    assert s_c.detail["adx"] < s_t.detail["adx"]                     # chop → weaker trend


def test_choppiness_high_on_oscillation_low_on_trend(tmp_path):
    now = "2026-01-01T00:20:00Z"
    trend = list(np.linspace(24000, 24300, 20))
    chop = [24000 + (25 if i % 2 else -25) for i in range(20)]
    ci_t = choppiness.compute(_bars_db(tmp_path / "t", trend), now, {})
    ci_c = choppiness.compute(_bars_db(tmp_path / "ch", chop), now, {})
    assert ci_t.status == "OK" and ci_c.status == "OK"
    assert ci_c.detail["choppiness_index"] > ci_t.detail["choppiness_index"]   # chop > trend
    assert ci_c.detail["note"].startswith("regime")                 # non-directional
