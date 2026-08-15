"""
Payoff-math correctness tests. Pure numpy, no DB needed.
Run: python -m pytest strategy_framework/tests/test_payoff.py -q
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from strategy_framework.strategy import constructor as C
from strategy_framework.config.settings import FrameworkConfig


class _Chain:
    """Minimal chain stub with the attributes constructor.build reads."""
    def __init__(self, spot, strikes, call_ltp, put_ltp):
        self.spot = spot
        self.strikes = strikes
        self.call_ltp = call_ltp
        self.put_ltp = put_ltp
    def atm_strike(self):
        return min(self.strikes, key=lambda k: abs(k - self.spot))


def _synthetic_chain(spot=24250.0):
    strikes = [24000, 24050, 24100, 24150, 24200, 24250, 24300, 24350, 24400, 24450, 24500]
    strikes = [float(s) for s in strikes]
    # crude but monotone premiums: calls decrease with strike, puts increase
    call_ltp = {k: max(spot - k, 0) + 60 + max(0, (24500 - k)) * 0.02 for k in strikes}
    put_ltp = {k: max(k - spot, 0) + 60 + max(0, (k - 24000)) * 0.02 for k in strikes}
    return _Chain(spot, strikes, call_ltp, put_ltp)


def test_bull_call_spread_bounds():
    cfg = FrameworkConfig()
    ch = _synthetic_chain()
    st = C.build("bull_call_spread", ch, cfg)
    assert st is not None
    # debit spread: you pay -> net_debit > 0, defined risk = debit, capped profit
    assert st.net_debit > 0
    assert st.max_loss > 0
    assert st.max_profit > 0
    # max loss of a debit vertical cannot exceed the debit paid (within rounding)
    assert st.max_loss <= st.net_debit + 1e-6
    # profit far above short strike equals width - debit
    width = abs(st.legs[0][1] - st.legs[1][1])
    assert abs(st.max_profit - (width - st.net_debit)) < 1.0


def test_bull_put_spread_is_credit():
    cfg = FrameworkConfig()
    ch = _synthetic_chain()
    st = C.build("bull_put_spread", ch, cfg)
    assert st is not None
    # credit spread: you receive premium -> net_debit < 0
    assert st.net_debit < 0
    # max profit of a credit spread == net credit received
    assert abs(st.max_profit - (-st.net_debit)) < 1.0


def test_payoff_at_matches_curve_endpoints():
    cfg = FrameworkConfig()
    ch = _synthetic_chain()
    st = C.build("long_call", ch, cfg)
    assert st is not None
    k = st.legs[0][1]
    # deep OTM at expiry -> lose the full premium
    lost = C.payoff_at(st, k - 1000)
    assert abs(lost + st.premiums[("call", k)]) < 1e-6
    # deep ITM -> gain intrinsic minus premium
    up = C.payoff_at(st, k + 1000)
    assert up > 0


def test_short_leg_sign_convention():
    # a lone short call: gains premium if underlying expires below strike
    ch = _synthetic_chain()
    prem = ch.call_ltp[24300.0]
    # emulate structure with one short leg via payoff_at
    from strategy_framework.strategy.constructor import Structure
    st = Structure("x", [("call", 24300.0, -1)], {("call", 24300.0): prem},
                   -prem, prem, 0, [], ch.spot, 75)
    assert abs(C.payoff_at(st, 24000.0) - prem) < 1e-6      # expires worthless -> keep premium
    assert C.payoff_at(st, 25000.0) < 0                      # deep ITM -> loss


def test_iron_condor_is_defined_risk_credit():
    cfg = FrameworkConfig()
    ch = _synthetic_chain()
    # give the condor OI so _anchor_short finds walls
    ch.put_oi = {k: 100000 for k in ch.strikes}
    ch.call_oi = {k: 100000 for k in ch.strikes}
    ch.put_oi_chg = {k: 0 for k in ch.strikes}
    ch.call_oi_chg = {k: 0 for k in ch.strikes}
    st = C.build("iron_condor", ch, cfg)
    assert st is not None
    assert len(st.legs) == 4
    assert st.net_debit < 0                 # a condor is a net credit
    assert st.max_loss > 0 and st.max_profit > 0
    # max profit of a condor == net credit received
    assert abs(st.max_profit - (-st.net_debit)) < 1.0
    # defined risk: loss is bounded (wing width - credit), not infinite
    assert st.max_loss < 1e6


def test_iron_butterfly_four_legs_credit():
    cfg = FrameworkConfig()
    ch = _synthetic_chain()
    ch.put_oi = {k: 1 for k in ch.strikes}; ch.call_oi = {k: 1 for k in ch.strikes}
    ch.put_oi_chg = {k: 0 for k in ch.strikes}; ch.call_oi_chg = {k: 0 for k in ch.strikes}
    st = C.build("iron_butterfly", ch, cfg)
    assert st is not None
    assert len(st.legs) == 4
    assert st.net_debit < 0                 # short straddle dominates -> credit
    # peak profit sits at the ATM body
    body = [k for s, k, sign in st.legs if sign == -1][0]
    assert C.payoff_at(st, body) > 0


def test_degenerate_spread_rejected():
    """Spot outside the strike band must not yield a same-strike spread."""
    cfg = FrameworkConfig()
    strikes = [24000.0, 24050.0, 24100.0]        # narrow band
    ch = _Chain(23800.0, strikes,               # spot below all strikes
                {k: 10.0 for k in strikes}, {k: 200.0 for k in strikes})
    assert C.build("bear_put_spread", ch, cfg) is None
    assert C.build("bull_put_spread", ch, cfg) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
