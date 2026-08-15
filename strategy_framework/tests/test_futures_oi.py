"""
Tests for the futures-OI positioning read.

The RULE lives in backend.quant.intraday_oi._label (single source, also behind the
Macro Shock view). strategy_framework/signals/futures_oi.py is a thin adapter. We
pin the user's hand-verified 03-Jul labels to the CANONICAL engine, and check the
adapter delegates rather than re-deriving.
"""
from backend.quant.intraday_oi import _label
from strategy_framework.signals.futures_oi import classify_positioning as C, reliability


# user's 03-Jul phases, as (price %, OI %) — the values shown in the Macro Shock panel
_USER_03JUL = [
    (0.6, -0.79, "short_covering"),    # morning:   price up,  OI down
    (-0.11, -1.32, "long_unwinding"),  # midday:    price down, OI down
    (-0.13, 1.46, "short_buildup"),    # afternoon: price down, OI up
    (0.35, -0.67, "short_covering"),   # full day:  price up,  OI down
]


def test_canonical_engine_reproduces_user_labels():
    for dp, doi, expect in _USER_03JUL:
        assert _label(dp, doi)[0] == expect, (dp, doi)


def test_adapter_matches_canonical():
    # the adapter must return exactly the canonical kind — no divergent rule
    for dp, doi, expect in _USER_03JUL:
        r = C(dp, doi)
        assert r["kind"] == expect
        assert r["regime"] == expect.replace("_", " ")


def test_adapter_lean_and_conviction():
    assert C(1.0, 1.0)["kind"] == "long_buildup" and C(1.0, 1.0)["conviction"] is True
    assert C(1.0, -1.0)["kind"] == "short_covering" and C(1.0, -1.0)["conviction"] is False
    assert C(-1.0, 1.0)["lean"] == "bear" and C(-1.0, 1.0)["conviction"] is True
    assert C(-1.0, -1.0)["lean"] == "bear" and C(-1.0, -1.0)["conviction"] is False


def test_churn_and_flat():
    assert C(1.0, 0.2)["kind"] == "churn"         # price moving, OI ~flat → leveraged churn
    assert C(0.01, 0.2)["kind"] == "churn"        # flat price AND flat OI → genuine noise


def test_coiled_flat_price_heavy_oi():
    # flat price but OI building heavily = two-sided positioning, NOT churn. This is the
    # 29-Jun case (+40% OI, flat price) the old rule wrongly folded into churn.
    r = C(0.01, 2.0)
    assert r["kind"] == "coiled"
    assert r["lean"] == "neutral" and r["conviction"] is True   # real positioning, no direction
    from strategy_framework.signals.futures_oi import regime_score, reliability
    s, cf = regime_score("coiled")
    assert s == 0.0                                   # no directional vote
    assert cf > regime_score("churn")[1]              # but more confident than noise
    assert reliability("coiled") < reliability("short_covering")  # distrust directional reads while coiled


def test_coiled_volume_proxy():
    from backend.quant.intraday_oi import _label_vol
    assert _label_vol(0.01, 1.5)[0] == "coiled"       # flat price, heavy volume
    assert _label_vol(0.01, 1.0)[0] == "churn"        # flat price, average volume


def test_reliability_orders_conviction_above_hollow():
    assert reliability("short_buildup") > reliability("short_covering")
    assert reliability("long_buildup") > reliability("long_unwinding")
    assert reliability("churn") < reliability("short_covering")


def test_regime_score_signs_and_conviction():
    from strategy_framework.signals.futures_oi import regime_score
    # buildup: trade WITH, high confidence
    assert regime_score("long_buildup") == (0.70, 0.70)     # bullish
    assert regime_score("short_buildup") == (-0.70, 0.70)   # bearish
    # covering/unwinding: FADE the move (opposite of price direction), low confidence
    assert regime_score("short_covering")[0] < 0 and regime_score("short_covering")[1] < 0.5
    assert regime_score("long_unwinding")[0] > 0 and regime_score("long_unwinding")[1] < 0.5
    assert regime_score("churn")[0] == 0.0


def test_signal_no_data_without_oi(tmp_path):
    """The OI regime signal returns NO_DATA (never a fake vote) when bars carry no OI."""
    import sqlite3
    from strategy_framework.signals.data_access import DataAccess
    from strategy_framework.signals import futures_oi_regime as F
    db = str(tmp_path / "d.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, "
                "ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    for i in range(70):
        con.execute("INSERT INTO price_bars VALUES ('NFO','NIFTY_FUT_1','1m',?,?,?,?,?,100)",
                    (f"2026-01-01T0{i//60}:{i%60:02d}:00Z", 100, 100, 100, 100 + i))
    con.commit(); con.close()
    s = F.compute(DataAccess(db), "2026-01-01T01:00:00Z", {})
    assert s.status == "NO_DATA"          # no open_interest column → honest NO_DATA


def test_volume_proxy_labels_and_index_guard():
    """Volume proxy classifies stock legs and refuses an index (no volume)."""
    from backend.quant.intraday_oi import _label_vol
    assert _label_vol(0.5, 1.4)[0] == "long_buildup"     # up, heavy vol
    assert _label_vol(-0.5, 1.4)[0] == "short_buildup"   # down, heavy vol
    assert _label_vol(0.5, 0.6)[0] == "short_covering"   # up, light vol (hollow)
    assert _label_vol(-0.5, 0.6)[0] == "long_unwinding"  # down, light vol (hollow)
    assert _label_vol(0.5, 1.0)[0] == "churn"            # average vol → no edge
