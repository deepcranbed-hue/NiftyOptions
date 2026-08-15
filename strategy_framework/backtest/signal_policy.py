"""
strategy_framework/backtest/signal_policy.py
=============================================
Event-driven backtest of a CONSTRAINED signal-trading POLICY — the thing that
decides whether the research edge survives real execution rules.

The rules (as specified):

  * LONG / FLAT only. A bullish signal buys an ATM CALL; when flat, a bearish
    signal does nothing (we never buy puts / go short here).
  * ENTRY is REGIME-GATED: buy only when the capture is in the chosen regime AND
    the chosen signal's score ≥ +entry_thr AND the premium fits the budget.
    (No budget → skip the trade and stay flat; the opportunity is simply missed.)
  * EXIT: on a bearish signal (score ≤ −exit_thr) close ONLY if the position is up
    by ≥ min_profit_pct net of costs — otherwise KEEP HOLDING and wait for a better
    exit. This is the whipsaw guard: bought, immediate sell signal, tiny gap → after
    costs you'd lose, so you don't sell. A STOP-LOSS force-exits a loser so "keep
    holding" can't run unbounded.

Nothing is re-implemented that already exists (CLAUDE.md DRY):
  * per-capture signal scores  → api._eval_signals_series
  * per-capture regime label   → api._label_regimes (same source the study uses, so
                                  the policy trades exactly the regime it measured)
  * option prices              → DataAccess.chain_as_of (the ATM call, marked each bar)
  * costs / lot size           → CostModel.legs_cost_inr / exchange_config lot size

Honest limits: a long call also carries THETA and IV — both are captured naturally
because we mark the SAME held strike against the real chain snapshot at every
capture (not a model). If the chain lacks the held strike at some capture we hold
the last good mark. Positions are force-closed at the option's expiry and at the end
of the range (final mark-to-market), so there is no look-past-expiry leakage.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Trade:
    entry_ts: str
    exit_ts: str
    expiry: str
    strike: float
    entry_prem: float
    exit_prem: float
    n_lots: int
    lot_size: int
    gross_pnl: float          # ₹ before costs
    cost: float               # ₹ round-trip
    net_pnl: float            # ₹ after costs
    ret_pct: float            # net_pnl / entry notional, %
    hold_min: float
    exit_reason: str          # 'signal' | 'stop' | 'expiry' | 'eod'


@dataclass
class PolicyResult:
    trades: list = field(default_factory=list)
    equity: list = field(default_factory=list)     # [(ts, equity_₹)]
    params: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


def _exit_decision(ret_pct: float, score, exit_thr: float, min_profit_pct: float,
                   stop_pct: float, expired: bool):
    """PURE exit rule (no I/O) so it can be unit-tested exhaustively. Priority:
    expiry > stop-loss > (bearish signal AND profit gate met). Returns the exit
    reason or None (= keep holding). The whipsaw guard lives in the last clause:
    a bearish signal with too little profit returns None, i.e. we DON'T sell."""
    if expired:
        return "expiry"
    if ret_pct <= -stop_pct:
        return "stop"
    if score is not None and score <= -exit_thr and ret_pct >= min_profit_pct:
        return "signal"
    return None


def _can_enter(score, entry_thr: float, in_regime: bool, prem,
               notional: float, entry_cost: float, cash: float) -> bool:
    """PURE entry gate: bullish enough, in the chosen regime, priceable, and the
    premium+cost fits the budget. Any failure → stay flat (opportunity missed)."""
    return bool(score is not None and score >= entry_thr and in_regime
                and prem and prem > 0 and (notional + entry_cost) <= cash)


def _nearest_call(chain, strike):
    """Price of the held strike's call, or the nearest available strike's call as a
    fallback (illiquid snapshots can drop a strike). None if nothing usable."""
    if chain is None:
        return None
    p = chain.call_ltp.get(strike)
    if p and p > 0:
        return float(p)
    cands = [(abs(k - strike), chain.call_ltp.get(k, 0.0)) for k in chain.strikes]
    cands = [(d, v) for d, v in cands if v and v > 0]
    if not cands:
        return None
    cands.sort()
    return float(cands[0][1])


def run_policy(date_from: Optional[str] = None, date_to: Optional[str] = None,
               signal: str = "rel_volume", regime_by: str = "oi", regime: str = "hollow",
               entry_thr: float = 0.35, exit_thr: float = 0.35,
               min_profit_pct: float = 8.0, stop_pct: float = 12.0,
               budget: float = 200000.0, n_lots: int = 1) -> PolicyResult:
    """Backtest the long-call/flat policy. Profit/stop percentages are on the OPTION
    premium (an option moves far more than the index, so 8%/12% on premium ≈ a small
    index move). Returns trades, an equity curve, and summary stats."""
    from ..api import (_CFG, _eval_signals_series, _label_regimes, _attach_front_expiry)
    from ..signals.data_access import DataAccess
    from datetime import datetime

    lot = _CFG.lot_size
    costs = _CFG.costs
    da = DataAccess(_CFG.db_path)
    all_caps = da.list_captures()
    if not all_caps:
        return PolicyResult(params={"error": "no captures in DB"})
    _attach_front_expiry(da, all_caps)
    lo, hi = (date_from or "0000"), (date_to or "9999")
    caps = [c for c in all_caps if lo <= c["captured_at"][:10] <= hi]
    if len(caps) < 3:
        return PolicyResult(params={"error": "not enough captures in range"})

    dir_specs, records = _eval_signals_series(da, caps)
    names = {s.name for s in dir_specs}
    if signal not in names:
        return PolicyResult(params={"error": f"unknown signal '{signal}'",
                                    "available": sorted(names)})
    spots = [r["spot"] for r in records]
    regimes, reg_names, _ = _label_regimes(records, spots, caps, regime_by)
    if regime not in reg_names:
        return PolicyResult(params={"error": f"regime '{regime}' not in {reg_names}"})

    params = {"mode": "signal", "signal": signal, "regime_by": regime_by, "regime": regime,
              "entry_thr": entry_thr, "exit_thr": exit_thr, "min_profit_pct": min_profit_pct,
              "stop_pct": stop_pct, "budget": budget, "n_lots": n_lots, "lot_size": lot,
              "date_from": caps[0]["captured_at"][:10], "date_to": caps[-1]["captured_at"][:10],
              "n_captures": len(records)}
    return _simulate(da, caps, records, lot, costs,
                     score_of=lambda i: records[i]["vals"].get(signal, (None,))[0],
                     gate_of=lambda i: regimes[i] == regime,
                     entry_thr=entry_thr, exit_thr=exit_thr, min_profit_pct=min_profit_pct,
                     stop_pct=stop_pct, budget=budget, n_lots=n_lots, params=params)


def run_belief_policy(date_from=None, date_to=None,
                      entry_factor: str = "trend_direction", entry_thr: float = 0.3,
                      quality_factor: str = "trend_quality", quality_thr: float = 0.0,
                      exit_thr: float = 0.3, min_profit_pct: float = 8.0, stop_pct: float = 12.0,
                      budget: float = 200000.0, n_lots: int = 1) -> PolicyResult:
    """BELIEF-DRIVEN policy: the trade decision consumes BELIEFS, never raw signals.
    Entry when the `entry_factor` belief (e.g. trend_direction) is bullish ≥ entry_thr
    AND the `quality_factor` belief (e.g. trend_quality) ≥ quality_thr — i.e. 'the trend
    hypothesis explains today's market AND participation says it's a high-quality trend'.
    Exit trigger = the entry factor turning bearish (with the same profit gate + stop).
    This is the A/B counterpart to run_policy for the ultimate test: do beliefs improve
    trading decisions over direct signal use? Strategies stay insulated from sensors —
    swap a signal in the factor map and this policy never changes."""
    from types import SimpleNamespace
    from ..api import (_CFG, _eval_signals_series, _attach_front_expiry)
    from ..signals.data_access import DataAccess
    from ..factors import evaluate_factors, MAP_VERSION
    from .. import knowledge as KB

    lot = _CFG.lot_size
    costs = _CFG.costs
    da = DataAccess(_CFG.db_path)
    all_caps = da.list_captures()
    if not all_caps:
        return PolicyResult(params={"error": "no captures in DB"})
    _attach_front_expiry(da, all_caps)
    lo, hi = (date_from or "0000"), (date_to or "9999")
    caps = [c for c in all_caps if lo <= c["captured_at"][:10] <= hi]
    if len(caps) < 3:
        return PolicyResult(params={"error": "not enough captures in range"})

    _, records = _eval_signals_series(da, caps)

    class _VBundle:                      # adapter: per-capture vals → bundle interface
        def __init__(self, vals): self._v = vals
        def get(self, name):
            t = self._v.get(name)
            if not t or t[0] is None:
                return None
            return SimpleNamespace(score=t[0], confidence=t[1], status="OK")

    ev = KB.load_evidence()              # load once, not per capture
    beliefs = []
    for r in records:
        f = {x["name"]: x for x in evaluate_factors(_VBundle(r["vals"]), evidence=ev)}
        beliefs.append(f)

    def _score(i):
        b = beliefs[i].get(entry_factor)
        return b["estimate"] if b else None

    def _gate(i):
        if not quality_factor:
            return True
        q = beliefs[i].get(quality_factor)
        return bool(q and q["estimate"] is not None and q["estimate"] >= quality_thr)

    params = {"mode": "belief", "entry_factor": entry_factor, "quality_factor": quality_factor,
              "quality_thr": quality_thr, "factor_map_version": MAP_VERSION,
              "entry_thr": entry_thr, "exit_thr": exit_thr, "min_profit_pct": min_profit_pct,
              "stop_pct": stop_pct, "budget": budget, "n_lots": n_lots, "lot_size": lot,
              "date_from": caps[0]["captured_at"][:10], "date_to": caps[-1]["captured_at"][:10],
              "n_captures": len(records)}
    return _simulate(da, caps, records, lot, costs, score_of=_score, gate_of=_gate,
                     entry_thr=entry_thr, exit_thr=exit_thr, min_profit_pct=min_profit_pct,
                     stop_pct=stop_pct, budget=budget, n_lots=n_lots, params=params)


def _simulate(da, caps, records, lot, costs, score_of, gate_of,
              entry_thr, exit_thr, min_profit_pct, stop_pct, budget, n_lots,
              params) -> PolicyResult:
    """THE shared long-call/flat simulator. Decision inputs are injected (score_of /
    gate_of per capture index) so the signal-driven and belief-driven policies run
    through EXACTLY the same execution, cost, and accounting code — the A/B compares
    decisions, not implementations."""
    from datetime import datetime

    def _tmin(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() / 60.0

    cash = float(budget)
    pos = None                       # dict when long, else None
    res = PolicyResult(params=params)

    def _close(cur_prem, ts, reason):
        nonlocal cash, pos
        entry_notional = pos["entry_prem"] * lot * n_lots
        gross = (cur_prem - pos["entry_prem"]) * lot * n_lots
        exit_cost = costs.legs_cost_inr(n_lots, lot)          # sell leg(s)
        net = gross - pos["entry_cost"] - exit_cost
        cash += entry_notional + net                          # release premium + P&L net
        res.trades.append(Trade(
            entry_ts=pos["ts"], exit_ts=ts, expiry=pos["expiry"], strike=pos["strike"],
            entry_prem=round(pos["entry_prem"], 2), exit_prem=round(cur_prem, 2),
            n_lots=n_lots, lot_size=lot, gross_pnl=round(gross, 0),
            cost=round(pos["entry_cost"] + exit_cost, 0), net_pnl=round(net, 0),
            ret_pct=round(100.0 * net / entry_notional, 2) if entry_notional else 0.0,
            hold_min=round(_tmin(ts) - _tmin(pos["ts"]), 1), exit_reason=reason))
        pos = None

    for i, c in enumerate(caps):
        ts = c["captured_at"]
        exp = c.get("expiry")
        score = score_of(i)
        chain = da.chain_as_of(ts, pos["expiry"] if pos else exp)

        if pos is not None:
            # force-close at (or past) the held option's expiry — no look-past-expiry.
            expired = ts[:10] > pos["expiry"]
            cur = _nearest_call(chain, pos["strike"]) or pos["last_mark"]
            pos["last_mark"] = cur
            ret = (cur - pos["entry_prem"]) / pos["entry_prem"] * 100.0 if pos["entry_prem"] else 0.0
            reason = _exit_decision(ret, score, exit_thr, min_profit_pct, stop_pct, expired)
            if reason:
                _close(cur, ts, reason)
            # else: keep holding (whipsaw guard / waiting for a decent exit)

        elif chain is not None:
            k = chain.atm_strike()
            prem = _nearest_call(chain, k)
            notional = (prem or 0.0) * lot * n_lots
            entry_cost = costs.legs_cost_inr(n_lots, lot)
            if _can_enter(score, entry_thr, gate_of(i), prem,
                          notional, entry_cost, cash):
                cash -= (notional + entry_cost)
                pos = {"ts": ts, "expiry": exp, "strike": k, "entry_prem": prem,
                       "entry_cost": entry_cost, "last_mark": prem}

        # equity = cash + open position marked-to-market
        mark = 0.0
        if pos is not None:
            mark = pos["last_mark"] * lot * n_lots
        res.equity.append((ts, round(cash + mark, 0)))

    # end of range: close any open position at its last mark (mark-to-market)
    if pos is not None:
        _close(pos["last_mark"], caps[-1]["captured_at"], "eod")

    res.stats = _summarize(res, budget)
    return res


def _summarize(res: "PolicyResult", budget: float) -> dict:
    tr = res.trades
    n = len(tr)
    if n == 0:
        return {"n_trades": 0, "note": "policy never fired (regime/threshold/budget too tight)"}
    nets = [t.net_pnl for t in tr]
    wins = [t for t in tr if t.net_pnl > 0]
    total = sum(nets)
    eq = [e for _, e in res.equity]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = -sum(t.net_pnl for t in tr if t.net_pnl <= 0)
    return {
        "n_trades": n,
        "win_rate": round(100.0 * len(wins) / n, 1),
        "net_pnl": round(total, 0),
        "return_on_budget_pct": round(100.0 * total / budget, 2),
        "avg_net_per_trade": round(total / n, 0),
        "avg_hold_min": round(sum(t.hold_min for t in tr) / n, 1),
        "max_drawdown": round(mdd, 0),
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0 else None),
        "exit_reasons": {r: sum(1 for t in tr if t.exit_reason == r)
                         for r in ("signal", "stop", "expiry", "eod")},
        "total_cost": round(sum(t.cost for t in tr), 0),
    }
