"""
Tests for the daily market-health gauge — primitives, honesty, and assembly.
Pure-Python synthetic series so they run offline with no DB dependency, plus a
couple of live-DB smoke checks that skip when the daily table is empty.
"""
from __future__ import annotations
import math
import pytest

from strategy_framework.market_health import daily_bars as D
from strategy_framework.market_health import trend as T


# ── primitives ────────────────────────────────────────────────────────────────
def test_sma_and_insufficient_history():
    xs = list(range(1, 11))                    # 1..10
    assert D.sma(xs, 5) == sum(range(6, 11)) / 5
    assert D.sma(xs, 20) is None               # not enough → None, not partial


def test_rsi_bounds_and_none():
    up = [100 + i for i in range(30)]          # strictly rising
    r = D.rsi(up, 14)
    assert r == 100.0                          # no losses → 100
    assert D.rsi([1, 2, 3], 14) is None        # too short


def test_slope_needs_period_plus_look():
    xs = [float(i) for i in range(219)]        # 219 < 200 + 20
    assert D.slope_pct(xs, 200, look=20) is None
    xs = [float(i) for i in range(260)]        # rising line → positive slope
    assert D.slope_pct(xs, 200, look=20) > 0


def test_macd_none_when_short():
    assert D.macd(list(range(10))) is None


# ── the honesty contract: no fabricated MA on thin data ───────────────────────
def test_thin_history_reports_pending_not_a_number(tmp_path):
    """A 200-DMA sub-score on < 200 sessions must be data_ready=False with a null
    score — never a partial value dressed as a verdict."""
    import sqlite3
    db = str(tmp_path / "d.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, "
                "ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    # only 30 daily bars — enough for RSI/MACD, NOT for a 200-DMA
    for i in range(30):
        con.execute("INSERT INTO price_bars VALUES ('NSE','NIFTY','1d',?,?,?,?,?,0)",
                    (f"2026-01-{i+1:02d}T00:00:00Z", 100 + i, 101 + i, 99 + i, 100 + i))
    con.commit(); con.close()

    it = T.index_trend(db, "NIFTY")
    assert it["sub"]["px_vs_200dma"]["data_ready"] is False
    assert it["sub"]["px_vs_200dma"]["score01"] is None      # NOT a partial number
    # RSI-based momentum has enough history, so it IS scored
    assert it["sub"]["momentum"]["data_ready"] is True

    rep = T.market_health(db)
    assert rep["coverage_pct"] < 100                          # coverage honest
    assert rep["prior"] is True


def test_score_normalised_over_available_points(tmp_path):
    """With only the index available, the headline is scaled over available points,
    and the breadth layer reports pending rather than contributing zero silently."""
    import sqlite3
    db = str(tmp_path / "d.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, "
                "ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    # a clean rising series, 260 sessions → all index sub-scores computable
    for i in range(260):
        px = 100 + i * 0.5
        con.execute("INSERT INTO price_bars VALUES ('NSE','NIFTY','1d',?,?,?,?,?,0)",
                    (f"2026-{1+i//28:02d}-{1+i%28:02d}T00:00:00Z", px, px, px, px))
    con.commit(); con.close()

    rep = T.market_health(db)
    assert rep["score"] is not None
    assert 0 <= rep["score"] <= 100
    assert rep["layers"]["index_trend"]["data_ready"] is True
    assert rep["layers"]["trend_breadth"]["data_ready"] is False
    # a steadily rising line should read bullish
    assert rep["score"] >= 65


def test_band_mapping_monotonic():
    assert T.band(90) == "Strong uptrend"
    assert T.band(70) == "Healthy uptrend"
    assert T.band(55) == "Neutral / consolidation"
    assert T.band(40) == "Weakening"
    assert T.band(10) == "Defensive / downtrend"


def test_points_budget_is_single_source():
    # every scored key across all four layers must have a point budget in the one dict
    from strategy_framework.market_health.trend import (
        POINTS, _INDEX_KEYS, _BREADTH_KEYS, _SECTOR_KEYS, _LEADERSHIP_KEYS)
    for k in (*_INDEX_KEYS, *_BREADTH_KEYS, *_SECTOR_KEYS, *_LEADERSHIP_KEYS):
        assert k in POINTS


def test_all_four_internals_layers_activate_with_constituents(tmp_path):
    """With a full synthetic constituent set, breadth + sector rotation + leadership
    all light up and coverage reaches 100%."""
    import sqlite3, random
    from strategy_framework.config import constituents as K
    db = str(tmp_path / "d.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, "
                "ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    random.seed(3)

    def seed(sym, base, drift):
        px = base
        for i in range(260):
            px *= (1 + drift + random.uniform(-0.008, 0.008))
            con.execute("INSERT INTO price_bars VALUES ('NSE',?,'1d',?,?,?,?,?,1000)",
                        (sym, f"2025-{1+i//28:02d}-{1+i%28:02d}T00:00:00Z", px, px, px, px))
    seed("NIFTY", 20000, 0.0006)
    for j, s in enumerate(sorted(set(K.symbols()) - {"NIFTY"})):
        seed(s, 500 + j * 5, 0.0008 if j % 5 < 3 else -0.0004)
    con.commit(); con.close()

    rep = T.market_health(db)
    assert rep["coverage_pct"] == 100
    for layer in ("index_trend", "trend_breadth", "sector_rotation", "leadership_quality"):
        assert rep["layers"][layer]["data_ready"], f"{layer} should be active"
    # sector rotation must expose the cyclical/defensive read
    sr = rep["components"]["sector_rotation"]
    assert "leaning" in sr and sr["cyclical_strength"] is not None


# ── live-DB smoke (skips if the daily table is empty) ─────────────────────────
def test_live_nifty_daily_smoke():
    from strategy_framework.config.settings import FrameworkConfig
    db = FrameworkConfig().db_path
    cov = D.coverage(db, "NIFTY")
    if cov["sessions"] < 50:
        pytest.skip("no daily NIFTY history in this DB")
    rep = T.market_health(db)
    assert rep["score"] is None or 0 <= rep["score"] <= 100
    assert rep["disclaimer"]
