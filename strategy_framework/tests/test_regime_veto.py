"""
Tests for the TREND-EXPANSION veto in regime.classify: a range signature must NOT
sell premium while the ATM straddle is expanding or the tape is trending — and must
still return RANGE when the regime sensors agree the tape is quiet.
"""
from types import SimpleNamespace
from strategy_framework.config.settings import FrameworkConfig
from strategy_framework.strategy import regime as regime_mod


def _sig(score=0.0, conf=0.6, status="OK", detail=None):
    return SimpleNamespace(score=score, confidence=conf, status=status, detail=detail or {})


class _FakeBundle:
    """Weak/offsetting directional signals (range signature) + controllable regime
    sensors. classify() reads: time_of_day, earnings_events, heavyweight_leadership,
    vrp, the blended roster, straddle_flow, choppiness, pin_pressure."""
    spot = 24000.0
    context = {"atm_straddle_pts": 120.0, "vix": 12.0, "dte_days": 2.0}

    def __init__(self, straddle_score=-0.2, chop_score=0.6, strad_detail=None, chop_detail=None):
        self._m = {
            "time_of_day": _sig(detail={"momentum_multiplier": 1.0, "pin_risk": False,
                                        "phase": "MIDDAY"}),
            "earnings_events": _sig(detail={"veto": False}),
            "heavyweight_leadership": _sig(0.05, detail={"breadth": 0.1, "concentration": 0.2,
                                                         "hv_vol_surge": 1.0}),
            "vrp": _sig(0.0, detail={"vrp_ratio": 1.2, "regime": "FAIR"}),
            "straddle_flow": _sig(straddle_score, detail=strad_detail or {"change_pct": 8.0}),
            "choppiness": _sig(chop_score, detail=chop_detail or {"choppiness_index": 62.0}),
            "pin_pressure": _sig(0.7, detail={"regime": "gamma pin"}),
        }

    def get(self, name):
        return self._m.get(name, _sig(0.05, 0.6))     # weak-but-live directional default


def _classify(**kw):
    cfg = FrameworkConfig()
    return regime_mod.classify(_FakeBundle(**kw), cfg.weights, cfg.gates)


def test_range_when_sensors_quiet():
    r = _classify(straddle_score=-0.2, chop_score=0.6)     # compressing + choppy = quiet
    assert r.label == "RANGE"
    assert r.family in ("iron_condor", "iron_butterfly")
    assert r.diagnostics.get("pin_regime") == "gamma pin"  # pin support surfaced


def test_veto_on_straddle_expansion():
    r = _classify(straddle_score=0.5, chop_score=0.6,      # straddle EXPANDING
                  strad_detail={"change_pct": 12.0})
    assert r.label == "NO_TRADE"
    assert any("TREND-EXPANSION veto" in x for x in r.reasons)


def test_veto_on_trending_tape():
    r = _classify(straddle_score=-0.2, chop_score=0.2,     # chop low = tape trending
                  chop_detail={"choppiness_index": 30.0})
    assert r.label == "NO_TRADE"
    assert any("TREND-EXPANSION veto" in x for x in r.reasons)
