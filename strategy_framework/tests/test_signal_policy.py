"""
Tests for the constrained long-call/flat policy backtest
(strategy_framework/backtest/signal_policy.py).

The trading LOOP is validated through the two PURE decision functions
(_exit_decision, _can_enter) so we can pin every branch without standing up the
full signal bundle, plus the pure helpers (_nearest_call, _summarize). The
end-to-end integration is smoke-tested separately via the CLI on the chain DB.
"""
from strategy_framework.backtest.signal_policy import (
    _exit_decision, _can_enter, _nearest_call, _summarize, PolicyResult, Trade)


# ---- the whipsaw guard: the whole reason the profit gate exists ----------
def test_whipsaw_hold_when_profit_too_small():
    # bought, immediate bearish signal, only +2% — below the 8% gate → HOLD (None)
    assert _exit_decision(ret_pct=2.0, score=-0.6, exit_thr=0.35,
                          min_profit_pct=8.0, stop_pct=12.0, expired=False) is None


def test_sell_when_bearish_and_profit_gate_met():
    assert _exit_decision(10.0, -0.6, 0.35, 8.0, 12.0, False) == "signal"


def test_no_sell_without_bearish_signal_even_if_profitable():
    # up 20% but no sell signal (score positive) → keep holding, not "signal"
    assert _exit_decision(20.0, +0.5, 0.35, 8.0, 12.0, False) is None


def test_stop_loss_forces_exit_regardless_of_signal():
    assert _exit_decision(-15.0, +0.9, 0.35, 8.0, 12.0, False) == "stop"


def test_expiry_overrides_everything():
    assert _exit_decision(-50.0, -0.9, 0.35, 8.0, 12.0, expired=True) == "expiry"


def test_bearish_but_below_gate_holds():
    assert _exit_decision(5.0, -0.9, 0.35, 8.0, 12.0, False) is None


# ---- entry gate: regime, threshold, budget ------------------------------
def test_entry_requires_regime_threshold_and_budget():
    ok = dict(entry_thr=0.35, in_regime=True, prem=100.0, notional=6500.0,
              entry_cost=20.0, cash=200000.0)
    assert _can_enter(score=0.5, **ok) is True
    assert _can_enter(score=0.2, **ok) is False                 # below threshold
    assert _can_enter(score=0.5, **{**ok, "in_regime": False}) is False   # wrong regime
    assert _can_enter(score=0.5, **{**ok, "cash": 100.0}) is False        # budget too small
    assert _can_enter(score=None, **ok) is False                # no signal
    assert _can_enter(score=0.5, **{**ok, "prem": 0.0}) is False          # unpriceable


def test_nearest_call_falls_back_to_adjacent_strike():
    class C:
        strikes = [24000, 24100, 24200]
        call_ltp = {24000: 150.0, 24100: 0.0, 24200: 80.0}
    assert _nearest_call(C(), 24000) == 150.0        # exact
    assert _nearest_call(C(), 24100) == 80.0         # held strike empty → nearest priced
    assert _nearest_call(None, 24000) is None


def test_summarize_counts_wins_and_drawdown():
    def mk(net):
        return Trade(entry_ts="a", exit_ts="b", expiry="e", strike=1, entry_prem=100,
                     exit_prem=110, n_lots=1, lot_size=65, gross_pnl=net, cost=40,
                     net_pnl=net, ret_pct=1.0, hold_min=30, exit_reason="signal")
    res = PolicyResult(trades=[mk(500), mk(-200), mk(300)],
                       equity=[("t0", 100000), ("t1", 99000), ("t2", 100600)])
    s = _summarize(res, budget=100000)
    assert s["n_trades"] == 3
    assert s["win_rate"] == round(100 * 2 / 3, 1)
    assert s["net_pnl"] == 600
    assert s["max_drawdown"] == 1000            # 100000 -> 99000


def test_empty_result_is_honest():
    assert _summarize(PolicyResult(), budget=100000)["n_trades"] == 0
