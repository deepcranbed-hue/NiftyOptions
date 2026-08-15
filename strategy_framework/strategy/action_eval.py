"""
strategy_framework/strategy/action_eval.py
===========================================
FORECAST-DRIVEN action evaluator — the proactive-management layer.

Instead of "which rule applies?", this asks "given my forecast, which action
produces the best risk-adjusted outcome?". For the current position it enumerates
candidate actions, builds each resulting structure, integrates its EXPIRY payoff
against the forecast terminal-spot distribution (drift + vol from the regime), and
scores every action by a tail-aware objective:

        score = E[P&L] − λ · |CVaR10|            (λ default 0.5)

so income and blow-up protection trade off explicitly. The prediction model is
unchanged — only the decision layer becomes an optimization instead of a tree.

Candidate actions (condor / strangle):
    HOLD          keep the current legs
    DEFEND_PUT    roll the put spread one step further OTM (down)
    DEFEND_CALL   roll the call spread one step further OTM (up)
    CLOSE         flatten — locks the current mark, zero future variance

Returns a ranked table (each with expected P&L, CVaR10, P-loss, cost) plus the
forecast read (drift, σ, touch probabilities) so the trace can show the reasoning.
"""
from __future__ import annotations
import numpy as np

from . import constructor
from . import risk_forecast as RF


def _infer_step(strikes):
    s = sorted(set(strikes))
    diffs = [b - a for a, b in zip(s, s[1:]) if b > a]
    return min(diffs) if diffs else 50.0


def _shorts(legs):
    """Return (put_short_k, call_short_k) from a condor/strangle leg set."""
    puts = [k for side, k, sign in legs if side == "put" and sign < 0]
    calls = [k for side, k, sign in legs if side == "call" and sign < 0]
    return (max(puts) if puts else None, min(calls) if calls else None)


def _is_vertical(legs):
    """A directional 2-leg spread: same option side, exactly one long (+1) and one
    short (−1). Covers bull/bear call & put spreads (debit or credit)."""
    if len(legs) != 2:
        return False
    sides = {s for s, _k, _sg in legs}
    signs = sorted(sg for _s, _k, sg in legs)
    return len(sides) == 1 and signs == [-1, 1]


def _vertical_legs(legs):
    """(side, long_k, short_k) for a vertical."""
    side = legs[0][0]
    long_k = next(k for s, k, sg in legs if sg > 0)
    short_k = next(k for s, k, sg in legs if sg < 0)
    return side, long_k, short_k


def _roll_to(legs, side, chain, target_short, step):
    """Roll the given side's spread so its SHORT strike sits at ~`target_short`
    (snapped to a real strike), preserving wing width. Only rolls OUTWARD (away
    from spot) — returns None if the target isn't further OTM than current."""
    shorts = _shorts(legs)
    cur = shorts[0] if side == "put" else shorts[1]
    if cur is None:
        return None
    longs = [k for s, k, sign in legs if s == side and sign > 0]
    width = abs(cur - longs[0]) if longs else 2 * step
    tgt = min(chain.strikes, key=lambda k: abs(k - target_short))
    # only defend by moving the short FURTHER from spot
    if side == "put" and tgt >= cur:
        return None
    if side == "call" and tgt <= cur:
        return None
    new_long = tgt - width if side == "put" else tgt + width
    new_long = min(chain.strikes, key=lambda k: abs(k - new_long))
    out = []
    for s, k, sign in legs:
        if s != side:
            out.append((s, k, sign))
        else:
            out.append((s, tgt if sign < 0 else new_long, sign))
    return out


def _cond_loss_inr(grid, payoff, spot, drift, sigma, barrier, side, lot):
    """Expected P&L (₹) conditional on the short strike being BREACHED at expiry —
    i.e. E[payoff | terminal spot beyond `barrier`], × lot size. Negative = a loss."""
    g = np.asarray(grid, float); pf = np.asarray(payoff, float)
    mu = spot + drift
    pdf = np.exp(-0.5 * ((g - mu) / sigma) ** 2)
    mask = (g < barrier) if side == "put" else (g > barrier)
    w = pdf[mask]
    if w.sum() <= 0:
        return None
    return round(float((pf[mask] * w).sum() / w.sum()) * lot, 0)


def _harvest_wing(legs, chain, spot, step):
    """Roll the OVER-SAFE wing (the one farther from spot) one step TOWARD spot —
    the premium-harvest move. Collects fresh premium but narrows the condor and
    sells away far-wing 'insurance'. Returns (new_legs, side) or None."""
    put_s, call_s = _shorts(legs)
    if put_s is None or call_s is None:
        return None
    side = "call" if (call_s - spot) >= (spot - put_s) else "put"   # over-safe = farther
    d = -step if side == "call" else step                           # toward spot
    out = [(s, k + d, sign) if s == side else (s, k, sign) for s, k, sign in legs]
    return out, side


def evaluate_actions(family, legs, chain, regime, cfg, lam: float = 0.5,
                     horizon_frac: float = 1.0, min_edge: float = 5.0,
                     current_mark_inr: float | None = None,
                     risk_drift_frac: float = 1.0,
                     harvest_debt_pts: float = 0.0, n_harvests: int = 0,
                     harvest_debt_lambda: float = 0.03,
                     max_harvest_debt: float | None = None,
                     max_harvests: int | None = None,
                     min_wing_buffer: float | None = None,
                     min_width: float = 200.0) -> dict:
    """Score every candidate action under the forecast — ABSOLUTE expected P&L and
    CVaR for each (including HOLD), plus a vs-HOLD delta. `min_edge` is the points an
    action must beat HOLD by to be recommended (churn guard). `horizon_frac` (0..1)
    is the fraction of time-to-expiry used for the touch read."""
    spot = float(chain.spot)
    step = _infer_step(chain.strikes)
    sigma = max(float(getattr(regime, "expected_move_pts", 0.0)) or 0.0, step)
    net_score = float(getattr(regime, "net_score", 0.0) or 0.0)
    net_conf = float(getattr(regime, "net_confidence", 0.0) or 0.0)
    drift = net_score * sigma                    # net_score carries direction & conviction

    put_s, call_s = _shorts(legs)
    lot = cfg.lot_size

    def _cost_pts(n_legs_touched):
        return cfg.costs.legs_cost_inr(n_legs_touched, lot) / lot

    # risk read FIRST (closed-form, cheap) — used both for the output and the early-out
    rr = RF.risk_score(spot, put_s or spot - 5 * sigma, call_s or spot + 5 * sigma,
                       drift=drift, sigma=sigma, horizon_frac=horizon_frac)
    risk = {"touch_put": rr.touch_put, "touch_call": rr.touch_call,
            "breach_put": rr.breach_put, "breach_call": rr.breach_call,
            "threatened_side": rr.threatened_side, "worst_touch": rr.worst_touch}
    forecast = {"expected_move_pts": round(drift, 1), "std_dev_pts": round(sigma, 1),
                "net_score": round(net_score, 3),
                "confidence": round(net_conf, 3)}

    # a coarse grid (±5σ, 201 pts) is plenty for the integral and ~4× faster
    grid = np.linspace(spot - 5 * sigma, spot + 5 * sigma, 201)

    def _price(cl):
        prem = {}
        for side, k, sign in cl:
            book = chain.put_ltp if side == "put" else chain.call_ltp
            p = float(book.get(k, 0.0) or 0.0)
            if p <= 0:
                return None
            prem[(side, k)] = p
        return prem

    # EXPECTED return uses the forecast drift; RISK (CVaR/σ) uses only `risk_drift_frac`
    # of it — so at 0 the tail is measured SYMMETRICALLY (as if the trend could
    # reverse), which is what values the far wing as insurance and exposes
    # over-harvesting. At 1.0 it's the old drift-centred behaviour.
    risk_drift = drift * risk_drift_frac

    def _stats(payoff):
        se = RF.pnl_under_forecast(grid, payoff, spot, drift, sigma)
        if risk_drift_frac == 1.0:
            return {"expected": se["expected"], "cvar10": se["cvar10"],
                    "std": se.get("std"), "p_loss": se["p_loss"]}
        sr = RF.pnl_under_forecast(grid, payoff, spot, risk_drift, sigma)
        return {"expected": se["expected"], "cvar10": sr["cvar10"],
                "std": sr.get("std"), "p_loss": se["p_loss"]}

    # ---- DIRECTIONAL VERTICAL (bull/bear call/put spread) --------------------
    # A vertical has no two-sided short "band" to defend/harvest. Its levers are:
    # roll the whole spread with the trend, widen/narrow the strike gap (bounded by
    # a minimum width — never make it a razor-thin lottery), or close. Each is scored
    # the SAME tail-aware way against the forecast distribution.
    if _is_vertical(legs):
        return _evaluate_vertical(family, legs, chain, spot, step, sigma, drift,
                                  risk_drift_frac, grid, lam, min_edge, cfg, lot,
                                  current_mark_inr, forecast, _price, _stats, _cost_pts,
                                  min_width=min_width)

    # HOLD's absolute stats + expected-loss-if-breached (always computed — HOLD is
    # scored on its own merit, not treated as a zero baseline).
    hold_prem = _price(legs)
    hold_stats = {"expected": 0.0, "cvar10": 0.0, "std": 0.0, "p_loss": 0.0}
    if hold_prem is not None:
        hold_payoff = constructor.expiry_payoff(legs, hold_prem, grid)
        hold_stats = _stats(hold_payoff)
        thr_side = rr.threatened_side
        thr_barrier = put_s if thr_side == "put" else call_s
        if thr_barrier is not None:
            risk["expected_loss_if_breach_inr"] = _cond_loss_inr(
                grid, hold_payoff, spot, drift, sigma, thr_barrier, thr_side, lot)

    # EARLY-OUT: both shorts safe → HOLD trivially best, but still report HOLD's
    # ABSOLUTE expected value (not a bare 0) so the log is honest.
    SAFE_TOUCH = 0.06
    if rr.worst_touch < SAFE_TOUCH:
        return {"best": "HOLD", "lambda": lam, "min_edge": min_edge,
                "forecast": forecast, "risk": risk,
                "table": [{"action": "HOLD", "score": 0.0,
                           "expected": hold_stats["expected"], "cvar10": hold_stats["cvar10"],
                           "cost_pts": 0.0, "priceable": True}],
                "skipped": True,
                "note": f"HOLD (both shorts safe: worst touch {rr.worst_touch} < {SAFE_TOUCH})"}

    # defend by rolling the threatened wing out to ~1.3σ from the drift-adjusted centre
    centre = spot + drift
    cands = {"HOLD": (legs, 0.0)}
    if put_s is not None:
        rp = _roll_to(legs, "put", chain, centre - 1.3 * sigma, step)
        if rp:
            cands["DEFEND_PUT"] = (rp, _cost_pts(4))
    if call_s is not None:
        rc = _roll_to(legs, "call", chain, centre + 1.3 * sigma, step)
        if rc:
            cands["DEFEND_CALL"] = (rc, _cost_pts(4))
    # HARVEST the over-safe wing — scored the SAME way, but now STATE-AWARE: the
    # cumulative harvest debt (points of protection already sold away today) is
    # carried in, so the optimizer can see the path a one-step score can't.
    harvest_blocked, harvest_block_why = False, None
    hw = _harvest_wing(legs, chain, spot, step)
    if hw:
        new_debt = harvest_debt_pts + step
        buf_after = min(spot - (put_s or spot), (call_s or spot) - spot) - step
        if max_harvests is not None and n_harvests >= max_harvests:
            harvest_blocked, harvest_block_why = True, f"harvest budget: already {n_harvests}/{max_harvests} today"
        elif max_harvest_debt is not None and new_debt > max_harvest_debt:
            harvest_blocked, harvest_block_why = True, f"harvest budget: debt {new_debt:.0f} > {max_harvest_debt:.0f} pts"
        elif min_wing_buffer is not None and buf_after < min_wing_buffer:
            harvest_blocked, harvest_block_why = True, f"harvest budget: wing buffer would drop to {buf_after:.0f} < {min_wing_buffer:.0f} pts"
        if not harvest_blocked:
            cands["HARVEST_WING"] = (hw[0], _cost_pts(4))
    cands["CLOSE"] = (None, _cost_pts(len(legs)))

    rows = []
    for name, (cl, cost) in cands.items():
        if name == "HOLD":                               # absolute stats already computed
            rows.append({"action": "HOLD", "expected": hold_stats["expected"],
                         "cvar10": hold_stats["cvar10"], "std": hold_stats.get("std"),
                         "p_loss": hold_stats["p_loss"], "kind": "distribution",
                         "cost_pts": 0.0, "priceable": hold_prem is not None})
            continue
        if cl is None:                                   # CLOSE — flat future (no distribution)
            rows.append({"action": "CLOSE", "expected": round(-cost, 2),
                         "cvar10": round(-cost, 2), "std": 0.0, "p_loss": 0.0,
                         "cost_pts": round(cost, 2), "priceable": True,
                         "kind": "realized",           # NOT an estimate — flat, incremental from here
                         "realized_inr": round(current_mark_inr, 0) if current_mark_inr is not None else None})
            continue
        prem = _price(cl)
        if prem is None:
            rows.append({"action": name, "priceable": False})
            continue
        payoff = constructor.expiry_payoff(cl, prem, grid)
        stats = _stats(payoff)
        rows.append({"action": name, "expected": round(stats["expected"] - cost, 2),
                     "cvar10": round(stats["cvar10"] - cost, 2), "std": stats.get("std"),
                     "p_loss": stats["p_loss"], "kind": "distribution",
                     "cost_pts": round(cost, 2), "priceable": True,
                     "shorts": _shorts(cl)})

    # Each action carries its OWN absolute tail-aware score = E − λ|CVaR10|; the
    # vs-HOLD delta drives the recommendation, and an action must beat HOLD by
    # `min_edge` points to be chosen (user-defined churn guard).
    for r in rows:
        if r.get("priceable"):
            r["score_abs"] = round(r["expected"] - lam * abs(r["cvar10"]), 2)
            # STATE-AWARE harvest penalty: charge for the protection sold away so far
            # PLUS this harvest's step — so the 4th harvest is scored far worse than
            # the 1st, even though each looks fine in isolation.
            if r["action"] == "HARVEST_WING":
                r["debt_penalty"] = round(harvest_debt_lambda * (harvest_debt_pts + step), 2)
                r["score_abs"] = round(r["score_abs"] - r["debt_penalty"], 2)
    hold_abs = next((r["score_abs"] for r in rows if r["action"] == "HOLD"), 0.0)
    for r in rows:
        if r.get("priceable"):
            r["score"] = round(r["score_abs"] - hold_abs, 2)      # delta vs HOLD
    ranked = sorted([r for r in rows if r.get("priceable")], key=lambda r: -r["score"])
    non_hold = [r for r in ranked if r["action"] != "HOLD"]
    best = (non_hold[0]["action"] if non_hold and non_hold[0]["score"] > min_edge else "HOLD")

    return {
        "best": best, "lambda": lam, "min_edge": min_edge, "forecast": forecast, "risk": risk,
        "table": ranked, "skipped": False,
        "current_mark_inr": round(current_mark_inr, 0) if current_mark_inr is not None else None,
        "harvest_state": {"debt_pts": round(harvest_debt_pts, 0), "n_harvests": n_harvests,
                          "blocked": harvest_blocked, "block_why": harvest_block_why},
        "score_label": "risk-adj EV vs HOLD",   # what the (±) delta means
        "note": ("each action: absolute E / CVaR10 / σ of its forward P&L distribution; "
                 "the (±) is risk-adj EV (E−λ|CVaR10|) MINUS HOLD's; recommend if ≥ min_edge. "
                 "CLOSE is 'realized' (flat, no distribution) — it locks the current mark."),
    }


def _snap(k, strikes):
    return min(strikes, key=lambda x: abs(x - k))


_VERTICALS = ("bull_call_spread", "bear_call_spread", "bull_put_spread", "bear_put_spread")


def _enforce_min_width(legs, strikes, step, min_width):
    """Ensure a vertical's long↔short gap is ≥ min_width by pushing the SHORT strike
    further out (never tighter than the floor)."""
    side, long_k, short_k = _vertical_legs(legs)
    sd = 1 if short_k > long_k else -1
    while abs(short_k - long_k) < min_width:
        short_k = _snap(short_k + sd * step, strikes)
        if short_k in (long_k,):        # ran out of strikes
            break
    return [(side, _snap(long_k, strikes), 1), (side, _snap(short_k, strikes), -1)]


def _evaluate_vertical(family, legs, chain, spot, step, sigma, drift, risk_drift_frac,
                       grid, lam, min_edge, cfg, lot, current_mark_inr, forecast,
                       _price, _stats, _cost_pts, min_width=200.0):
    """Score a DIRECTIONAL vertical (bull/bear call/put spread) under the forecast.

    Actions:
      HOLD        keep the spread
      ROLL_UP     shift BOTH strikes up one step  (ride an uptrend / reset max-profit up)
      ROLL_DOWN   shift BOTH strikes down one step (ride a downtrend)
      WIDEN       move the SHORT strike one step further from the long → higher max
                  profit, more debit / wider risk
      NARROW      move the SHORT strike one step toward the long → cheaper, lower risk,
                  but NEVER below `min_width` (200 pts default) — below that it's a
                  razor-thin lottery, so the option is withheld (prefer CLOSE / ROLL)
      CLOSE       flatten — locks the current mark
    """
    side, long_k, short_k = _vertical_legs(legs)
    strikes = chain.strikes
    sd = 1 if short_k > long_k else -1               # side the short sits vs the long
    width = abs(short_k - long_k)

    def _mk(new_long, new_short):
        return [(side, _snap(new_long, strikes), 1), (side, _snap(new_short, strikes), -1)]

    cands = {"HOLD": (legs, 0.0)}
    cands["ROLL_UP"] = (_mk(long_k + step, short_k + step), _cost_pts(4))
    cands["ROLL_DOWN"] = (_mk(long_k - step, short_k - step), _cost_pts(4))
    cands["WIDEN"] = (_mk(long_k, short_k + sd * step), _cost_pts(2))       # short moves out
    narrow_width = width - step
    narrow_blocked = narrow_width < min_width
    if not narrow_blocked:
        cands["NARROW"] = (_mk(long_k, short_k - sd * step), _cost_pts(2))  # short moves in
    # CONVERT — re-establish as a DIFFERENT vertical (bull↔bear, call↔put) when the
    # forecast favours it. e.g. a bull call spread whose signal turned bearish scores
    # a bear call/put spread higher. Each fresh spread is placed by the constructor
    # (per config) then floored to min_width; scored fresh at current premiums − cost.
    for tgt in _VERTICALS:
        if tgt == family:
            continue
        try:
            st = constructor.build(tgt, chain, cfg)
        except Exception:
            st = None
        nl = [(s, k, sg) for s, k, sg in getattr(st, "legs", [])] if st else []
        if len(nl) == 2 and _is_vertical(nl):
            nl = _enforce_min_width(nl, strikes, step, min_width)
            cands[f"CONVERT_{tgt.replace('_spread', '').upper()}"] = (nl, _cost_pts(4))
    cands["CLOSE"] = (None, _cost_pts(len(legs)))

    rows = []
    for name, (cl, cost) in cands.items():
        if cl is None:                                # CLOSE — realized, flat forward
            rows.append({"action": "CLOSE", "expected": round(-cost, 2),
                         "cvar10": round(-cost, 2), "std": 0.0, "p_loss": 0.0,
                         "cost_pts": round(cost, 2), "priceable": True, "kind": "realized",
                         "realized_inr": round(current_mark_inr, 0) if current_mark_inr is not None else None})
            continue
        prem = _price(cl)
        if prem is None:
            rows.append({"action": name, "priceable": False}); continue
        payoff = constructor.expiry_payoff(cl, prem, grid)
        st = _stats(payoff)
        rows.append({"action": name, "expected": round(st["expected"] - cost, 2),
                     "cvar10": round(st["cvar10"] - cost, 2), "std": st.get("std"),
                     "p_loss": st["p_loss"], "cost_pts": round(cost, 2),
                     "priceable": True, "kind": "distribution",
                     "strikes": [int(_snap(long_k if name == "HOLD" else cl[0][1], strikes)),
                                 int(cl[1][1])] if name != "HOLD" else [int(long_k), int(short_k)]})

    for r in rows:
        if r.get("priceable"):
            r["score_abs"] = round(r["expected"] - lam * abs(r["cvar10"]), 2)
    hold_abs = next((r["score_abs"] for r in rows if r["action"] == "HOLD"), 0.0)
    for r in rows:
        if r.get("priceable"):
            r["score"] = round(r["score_abs"] - hold_abs, 2)
    ranked = sorted([r for r in rows if r.get("priceable")], key=lambda r: -r["score"])
    non_hold = [r for r in ranked if r["action"] != "HOLD"]
    best = (non_hold[0]["action"] if non_hold and non_hold[0]["score"] > min_edge else "HOLD")

    return {
        "best": best, "lambda": lam, "min_edge": min_edge, "forecast": forecast,
        "structure": "vertical", "family": family,
        "vertical_state": {"side": side, "long_k": int(long_k), "short_k": int(short_k),
                           "width": int(width), "min_width": int(min_width),
                           "narrow_blocked": narrow_blocked},
        "risk": {"note": "directional spread — profit zone set by the long/short strikes, no two-sided band"},
        "table": ranked, "skipped": False,
        "current_mark_inr": round(current_mark_inr, 0) if current_mark_inr is not None else None,
        "score_label": "risk-adj EV vs HOLD",
        "note": ("vertical actions: roll the whole spread with the trend, widen/narrow the "
                 f"gap (never below {int(min_width)} pts — else CLOSE/ROLL), or CLOSE. Each "
                 "scored E−λ|CVaR10| vs HOLD; recommend if ≥ min_edge."),
    }
