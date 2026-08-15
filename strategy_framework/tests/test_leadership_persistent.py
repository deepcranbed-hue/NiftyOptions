"""
Tests for heavyweight_leadership_persistent — the SNR/t-stat leadership signal.
The point of the signal is that CHOPPY leadership reads neutral (no flip) while
SUSTAINED leadership reads decisive; these tests pin that behaviour.
"""
import numpy as np
from strategy_framework.signals.base import squash, clamp


def _score(series):
    a = np.array(series, float); m = a.mean(); v = a.std()
    z = m / (v / np.sqrt(len(a))) if v > 1e-12 else 0.0
    return clamp(squash(z, 2.0)), z


def test_sustained_leadership_is_decisive():
    steady_up = [0.02, 0.03, 0.01, 0.02, 0.03, 0.02, 0.02, 0.03, 0.01, 0.02]
    score, z = _score(steady_up)
    assert score > 0.9 and z > 3          # steady, low-noise up → decisive buy


def test_choppy_leadership_is_neutral():
    choppy = [0.10, -0.09, 0.11, -0.08, 0.09, -0.10, 0.08, -0.11, 0.10, -0.09]
    score, z = _score(choppy)
    assert abs(score) < 0.1 and abs(z) < 0.5   # big wiggles, ~0 mean → NEUTRAL (no flip)


def test_signal_registered_as_directional_candidate():
    from strategy_framework.signals import registry as R
    spec = R.BY_NAME.get("heavyweight_leadership_persistent")
    assert spec is not None
    assert spec.kind == "directional" and spec.default_weight == 0.0   # candidate
    assert R.validate()["directional"] == 22


def test_no_data_when_no_constituents(tmp_path):
    import sqlite3
    from strategy_framework.signals.data_access import DataAccess
    from strategy_framework.signals import heavyweight_leadership_persistent as P
    db = str(tmp_path / "d.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, "
                "ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    # only NIFTY, no constituents
    for i in range(40):
        con.execute("INSERT INTO price_bars VALUES ('NSE','NIFTY','1m',?,?,?,?,?,0)",
                    (f"2026-01-01T0{i//60}:{i%60:02d}:00Z", 100, 100, 100, 100 + i))
    con.commit(); con.close()
    s = P.compute(DataAccess(db), "2026-01-01T00:39:00Z", {})
    assert s.status == "NO_DATA"
