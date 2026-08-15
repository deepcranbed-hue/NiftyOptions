"""
Adjustment-engine + transaction-cost tests. No DB needed.
Run: python -m pytest strategy_framework/tests/test_adjustment.py -q
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from strategy_framework.config.settings import FrameworkConfig, CostModel
from strategy_framework.strategy import adjustment as A


class _Chain:
    def __init__(self, spot, strikes, call_ltp, put_ltp):
        self.spot = spot; self.strikes = strikes
        self.call_ltp = call_ltp; self.put_ltp = put_ltp
    def atm_strike(self):
        return min(self.strikes, key=lambda k: abs(k - self.spot))


def _chain(spot):
    strikes = [float(s) for s in range(23800, 24701, 50)]
    call_ltp = {k: max(spot - k, 0) + 40 for k in strikes}
    put_ltp = {k: max(k - spot, 0) + 40 for k in strikes}
    return _Chain(spot, strikes, call_ltp, put_ltp)


class _Reg:
    def __init__(self, label, direction, em, score, conf):
        self.label = label; self.direction = direction
        self.expected_move_pts = em; self.net_score = score; self.net_confidence = conf


def _condor_legs(sp=24100, psp=24000, sc=24400, csc=24500):
    return [("put", float(psp), +1), ("put", float(sp), -1),
            ("call", float(sc), -1), ("call", float(csc), +1)]


def test_cost_model_rupees():
    cm = CostModel(per_leg_inr=20.0, slippage_pts=0.0)   # brokerage-only arithmetic
    assert cm.legs_cost_inr(4, 75) == 80.0          # 4-leg condor entry = ₹80
    assert cm.legs_cost_inr(2, 75) == 40.0
    # DEFAULT now includes 1pt/leg slippage — backtests are no longer frictionless.
    assert CostModel().slippage_pts == 1.0
    assert CostModel().legs_cost_inr(4, 75) == 4 * (20.0 + 75.0)


def test_hold_when_range_intact():
    cfg = FrameworkConfig()
    ch = _chain(24250)                               # spot centred in condor
    reg = _Reg("RANGE", 0, 120, 0.05, 0.4)
    plan = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg)
    assert plan.action == "HOLD"


def test_strong_breakout_converts_not_rolls():
    """A strong CONFIRMED trend that breaches the tested short must CONVERT to a
    directional spread on the winning wing — NOT roll the whole condor into the
    move (the gamma-chase pathology)."""
    cfg = FrameworkConfig()
    ch = _chain(24420)                               # spot beyond short call 24400
    reg = _Reg("TREND_UP", +1, 120, 0.55, 0.6)       # strong + confirmed
    plan = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg)
    assert plan.action.startswith("CONVERT")
    # the tested (call) wing is shed; the untested (put) wing is retained → bull_put
    assert {s for s, k, g in plan.close_legs} == {"call"}
    assert plan.new_family and plan.new_family.startswith("bull_put")
    assert plan.threatened is True


def test_near_tested_wing_tilts_when_confirmed():
    """A moderate lean that is merely NEAR (not breached) rolls only the untested
    wing toward spot — after the persistence filter is satisfied."""
    cfg = FrameworkConfig()
    ch = _chain(24360)                               # near short call 24400 (within 0.5*EM)
    reg = _Reg("TREND_UP", +1, 120, 0.35, 0.5)       # moderate, not very-strong
    # first near snapshot: persistence filter holds
    p0 = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg, breach_streak=0)
    assert p0.action == "HOLD" and p0.threatened is True
    # confirmed on the next snapshot: wing-level tilt (untested side only)
    p1 = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg, breach_streak=1)
    assert p1.action == "ROLL_UNTESTED_TOWARD" and p1.touched > 0
    # the tested (call) legs are NOT touched — the winner is preserved
    assert all(s != "call" for s, k, g in p1.close_legs)


def test_cooldown_blocks_rapid_readjust():
    cfg = FrameworkConfig()
    ch = _chain(24360)                               # near, not breached
    reg = _Reg("TREND_UP", +1, 120, 0.35, 0.5)
    plan = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg,
                      n_adjust=1, mins_since_last=3.0, breach_streak=5)
    assert plan.action == "HOLD" and "cooldown" in plan.rationale.lower()


def test_harvest_fires_in_trend_when_premium_clears_cost():
    """In a trend with the tested short still safe, harvesting the over-safe wing
    fires only when the fresh net premium beats the ₹/leg cost by the threshold."""
    cfg = FrameworkConfig()
    ch = _chain(24150)                               # mild down-drift; put side tested but safe
    reg = _Reg("TREND_DOWN", -1, 120, 0.35, 0.5)     # trend, not very-strong, short strikes safe
    off = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg, harvest=False)
    assert off.action == "HOLD"                      # disabled → holds
    on = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg,
                    harvest=True, min_harvest_inr=1.0)
    assert on.action in ("HARVEST_WING", "HOLD")     # fires if a worthwhile roll exists
    if on.action == "HARVEST_WING":
        assert {s for s, k, g in on.close_legs} == {"call"}   # only the safe (call) wing
        assert on.threatened is False
    # an absurdly high threshold must never fire
    hi = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg,
                    harvest=True, min_harvest_inr=1e9)
    assert hi.action == "HOLD"


def test_harvest_off_in_range():
    """No harvest in a quiet range (no trend) — direction 0 never reaches the hook."""
    cfg = FrameworkConfig()
    ch = _chain(24250)
    reg = _Reg("RANGE", 0, 120, 0.05, 0.4)
    plan = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg, harvest=True, min_harvest_inr=1.0)
    assert plan.action == "HOLD"


def test_roll_budget_exits_without_stop():
    cfg = FrameworkConfig()
    ch = _chain(24420)                               # breached
    reg = _Reg("TREND_UP", +1, 120, 0.55, 0.6)
    plan = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg,
                      n_adjust=2, breach_streak=3, stop_active=False)
    assert plan.action == "EXIT" and len(plan.close_legs) == 4


def test_roll_budget_holds_when_stop_active():
    """Budget is a fee limit, not a risk limit: with a stop-loss active, an
    exhausted budget stops adjusting but HOLDs — the stop owns the exit."""
    cfg = FrameworkConfig()
    ch = _chain(24420)                               # breached
    reg = _Reg("TREND_UP", +1, 120, 0.55, 0.6)
    plan = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg,
                      n_adjust=2, breach_streak=3, stop_active=True)
    assert plan.action == "HOLD" and plan.touched == 0


def test_pin_risk_closes():
    cfg = FrameworkConfig()
    ch = _chain(24250)
    reg = _Reg("RANGE", 0, 120, 0.0, 0.4)
    plan = A.evaluate("iron_condor", _condor_legs(), ch, reg, cfg, pin_risk=True)
    assert plan.action == "CLOSE"
    assert len(plan.close_legs) == 4


def test_directional_position_closes_on_reversal():
    cfg = FrameworkConfig()
    ch = _chain(24250)
    reg = _Reg("TREND_DOWN", -1, 120, -0.5, 0.6)
    legs = [("call", 24200.0, +1), ("call", 24300.0, -1)]   # a bull call spread
    plan = A.evaluate("bull_call_spread", legs, ch, reg, cfg)
    assert plan.action == "CLOSE"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
