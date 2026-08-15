"""
strategy_framework/strategy/futures_action_eval.py
===================================================
FORECAST-DRIVEN action evaluator for a SINGLE LINEAR position (a NIFTY future).

This is the futures analogue of action_eval.py (which scores option structures).
The split is deliberate and mirrors "the model predicts, the optimizer chooses":

  * the PREDICTION model answers "what is likely to happen?" — expected move,
    probability up, expected volatility over the horizon (from the regime);
  * this OPTIMIZER answers "given that forecast and my current position, what
    should I do?" — it enumerates every valid action, integrates each resulting
    position's P&L against the forecast terminal-spot distribution, and picks the
    action with the best TAIL-AWARE score:

        score = E[P&L] − λ · |CVaR10|            (λ default 0.5)

A future's payoff is linear, so for a signed position of `q` lots and terminal
spot S_T the P&L (₹) is simply  q · (S_T − S₀) · lot_size. That makes each action
a one-line payoff curve integrated against N(S₀+drift, σ):

    HOLD     keep q
    EXIT     go flat (0) — locks the current mark, zero forward variance
    ADD      scale IN one lot in the position's direction   (|q| ≤ max_lots)
    REDUCE   scale OUT one lot toward flat
    REVERSE  flip to −q   (only if allow_reverse)

Nothing here reads the future: drift and σ come only from the as-of regime.
"""
from __future__ import annotations
import numpy as np

from . import risk_forecast as RF


def _sgn(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def evaluate_futures_actions(spot: float, position_lots: int, regime, cfg,
                             lam: float = 0.5, horizon_frac: float = 1.0,
                             max_lots: int = 2, allow_reverse: bool = True,
                             min_edge_inr: float = 0.0, risk_drift_frac: float = 1.0,
                             current_mark_inr: float | None = None) -> dict:
    """Score HOLD/EXIT/ADD/REDUCE/REVERSE for a signed futures position of
    `position_lots` lots (e.g. +1 long, −2 short). Returns a ranked table (each row
    with target lots, expected P&L, CVaR10, σ and a vs-HOLD delta) plus the forecast
    read, so a trace can show the reasoning. All P&L figures are in RUPEES.

    `horizon_frac` (0..1) scales the regime's full move/vol to the forecast window
    (e.g. 30 min of the remaining session). `risk_drift_frac` controls how much of
    the drift the TAIL sees: 1.0 = trend-centred tail; 0.0 = symmetric tail (prices
    the risk of a reversal), matching the options optimizer's knob."""
    lot = cfg.lot_size
    q = int(position_lots)

    # --- forecast: drift & vol over the horizon (points) -------------------
    base_sigma = max(float(getattr(regime, "expected_move_pts", 0.0)) or 0.0, 1.0)
    net_score = float(getattr(regime, "net_score", 0.0) or 0.0)
    net_conf = float(getattr(regime, "net_confidence", 0.0) or 0.0)
    base_drift = net_score * base_sigma
    drift, sigma = RF.scale_horizon(base_drift, base_sigma, horizon_frac)
    risk_drift = drift * risk_drift_frac
    prob_up = RF._phi(drift / sigma) if sigma > 0 else (1.0 if drift >= 0 else 0.0)

    forecast = {"expected_move_pts": round(drift, 1), "std_dev_pts": round(sigma, 1),
                "prob_up": round(prob_up, 3), "prob_down": round(1.0 - prob_up, 3),
                "net_score": round(net_score, 3), "confidence": round(net_conf, 3)}

    grid = np.linspace(spot - 5 * sigma, spot + 5 * sigma, 201)

    def _cost_inr(lots_traded: int) -> float:
        return cfg.costs.legs_cost_inr(abs(int(lots_traded)), lot)

    def _stats_for(qt: int):
        """Expected & tail stats (₹) of holding `qt` signed lots to the horizon."""
        if qt == 0:
            return {"expected": 0.0, "cvar10": 0.0, "std": 0.0, "p_loss": 0.0}
        payoff = qt * (grid - spot) * lot                    # linear P&L in ₹
        se = RF.pnl_under_forecast(grid, payoff, spot, drift, sigma)
        if risk_drift_frac == 1.0:
            return se
        sr = RF.pnl_under_forecast(grid, payoff, spot, risk_drift, sigma)
        return {"expected": se["expected"], "cvar10": sr["cvar10"],
                "std": sr.get("std"), "p_loss": se["p_loss"]}

    # --- enumerate candidate TARGET positions ------------------------------
    dir_ = _sgn(q) or _sgn(drift) or 1          # scaling direction (forecast if flat)
    cap = int(max_lots)
    cands: dict[str, int] = {"HOLD": q, "EXIT": 0}
    add_t = q + dir_
    if abs(add_t) <= cap and add_t != q:
        cands["ADD"] = add_t
    red_t = q - _sgn(q)                          # one lot toward flat
    if q != 0 and red_t != q:
        cands["REDUCE"] = red_t
    if allow_reverse and q != 0:
        cands["REVERSE"] = -q

    rows = []
    for name, qt in cands.items():
        traded = qt - q
        cost = 0.0 if name == "HOLD" else _cost_inr(traded)
        st = _stats_for(qt)
        row = {"action": name, "target_lots": qt, "traded_lots": traded,
               "expected": round(st["expected"] - cost, 0),
               "cvar10": round(st["cvar10"] - cost, 0),
               "std": round(st.get("std", 0.0) or 0.0, 0),
               "p_loss": st.get("p_loss", 0.0), "cost_inr": round(cost, 0)}
        if name == "EXIT":
            row["kind"] = "realized"            # flat forward — locks the current mark
            row["realized_inr"] = (round(current_mark_inr, 0)
                                   if current_mark_inr is not None else None)
        else:
            row["kind"] = "distribution"
        rows.append(row)

    # tail-aware absolute score, then delta vs HOLD (recommend if it beats HOLD by
    # at least min_edge_inr — the churn / transaction-cost guard).
    for r in rows:
        r["score_abs"] = round(r["expected"] - lam * abs(r["cvar10"]), 0)
    hold_abs = next((r["score_abs"] for r in rows if r["action"] == "HOLD"), 0.0)
    for r in rows:
        r["score"] = round(r["score_abs"] - hold_abs, 0)      # ₹ vs HOLD
    ranked = sorted(rows, key=lambda r: -r["score"])
    non_hold = [r for r in ranked if r["action"] != "HOLD"]
    best = (non_hold[0]["action"] if non_hold and non_hold[0]["score"] > min_edge_inr
            else "HOLD")

    return {
        "best": best, "position_lots": q, "lambda": lam, "min_edge_inr": min_edge_inr,
        "max_lots": cap, "allow_reverse": allow_reverse, "horizon_frac": round(horizon_frac, 3),
        "forecast": forecast, "table": ranked,
        "current_mark_inr": (round(current_mark_inr, 0) if current_mark_inr is not None else None),
        "score_label": "risk-adj EV vs HOLD (₹)",
        "note": ("each action: absolute E / CVaR10 / σ of its forward P&L (₹) under "
                 "N(spot+drift, σ); the (±) is risk-adj EV (E−λ|CVaR10|) MINUS HOLD's. "
                 "EXIT is 'realized' (flat, no forward variance)."),
    }
