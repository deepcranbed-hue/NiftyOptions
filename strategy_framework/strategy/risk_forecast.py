"""
strategy_framework/strategy/risk_forecast.py
============================================
Forecast-driven risk primitives — the foundation for PROACTIVE position
management. Turns the framework's existing forecast (direction, net_score,
expected move) into forward-looking probabilities and P&L distributions, so the
defense layer can ask "how likely is spot to reach my strike before I can adjust
again?" instead of "has spot reached my strike?".

Model: over a horizon, spot follows an arithmetic Brownian motion
    X_t = μ t + σ √t · Z          (μ = forecast drift in points, σ = 1σ move)
This is the standard intraday approximation (drift + vol in points). Barrier /
touch probabilities are the closed-form first-passage results (reflection
principle) — no Monte Carlo needed for the trigger.

Everything is pure math (no numpy dependency beyond optional arrays). Nothing
here reads the future; μ and σ come only from data available as-of now.

Functions
---------
  scale_horizon(drift, sigma, frac)         -> (drift_h, sigma_h) for a sub-window
  touch_prob(spot, barrier, drift, sigma)   -> P(spot touches barrier within horizon)
  expiry_breach_prob(spot, barrier, ...)    -> P(spot beyond barrier AT horizon end)
  pnl_under_forecast(grid, payoff, ...)     -> {expected, cvar10, p_loss} of a payoff
  risk_score(...)                            -> composite 0..1 defend-urgency
"""
from __future__ import annotations
import math
import numpy as np
from dataclasses import dataclass


def _phi(x: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def scale_horizon(drift: float, sigma: float, frac: float) -> tuple[float, float]:
    """Scale a full-horizon drift/vol to a sub-horizon fraction (0..1).
    Drift scales linearly with time, vol with √time."""
    frac = max(0.0, min(1.0, frac))
    return drift * frac, sigma * math.sqrt(frac)


def touch_prob(spot: float, barrier: float, drift: float, sigma: float) -> float:
    """P(spot TOUCHES `barrier` at any point over the horizon), under ABM with
    `drift` (points, signed) and `sigma` (1σ points over the horizon).

    Closed-form first-passage (reflection principle). Works for a barrier above
    (call side) or below (put side) the current spot.
    """
    a = barrier - spot                       # signed distance to barrier
    if a == 0:
        return 1.0
    if sigma <= 0:                           # no vol: touch only if drift carries there
        return 1.0 if (a < 0 and drift <= a) or (a > 0 and drift >= a) else 0.0
    expo = math.exp(_safe_exp(2 * a * drift / (sigma * sigma)))
    if a < 0:                                # lower barrier (put side)
        p = _phi((a - drift) / sigma) + expo * _phi((a + drift) / sigma)
    else:                                    # upper barrier (call side)
        p = _phi((drift - a) / sigma) + expo * _phi((-a - drift) / sigma)
    return max(0.0, min(1.0, p))


def _safe_exp(x: float) -> float:
    """Clamp the exponent so exp() can't overflow; the paired Φ term is ~0 there."""
    return max(-50.0, min(50.0, x))


def expiry_breach_prob(spot: float, barrier: float, drift: float, sigma: float) -> float:
    """P(spot is BEYOND `barrier` at the END of the horizon) — terminal, not touch.
    Always ≤ touch_prob (you can breach intraday and come back)."""
    if sigma <= 0:
        return 1.0 if ((barrier < spot and spot + drift <= barrier) or
                       (barrier > spot and spot + drift >= barrier)) else 0.0
    a = barrier - spot
    if a < 0:                                # below a lower barrier
        return max(0.0, min(1.0, _phi((a - drift) / sigma)))
    return max(0.0, min(1.0, _phi((drift - a) / sigma)))   # above an upper barrier


def pnl_under_forecast(grid, payoff, spot: float, drift: float, sigma: float,
                       cvar_q: float = 0.10) -> dict:
    """Integrate an expiry `payoff` curve (aligned to underlying `grid`) against the
    forecast TERMINAL-spot distribution N(spot+drift, sigma). Returns expected P&L,
    CVaR at `cvar_q` (mean of the worst q tail), and P(loss). Units follow payoff
    (points or ₹ — caller decides). Deterministic (probability-weighted integral)."""
    g = np.asarray(grid, dtype=float)
    pf = np.asarray(payoff, dtype=float)
    if sigma <= 0 or g.size < 2:
        return {"expected": 0.0, "cvar10": 0.0, "p_loss": 0.0}
    mu = spot + drift
    pdf = np.exp(-0.5 * ((g - mu) / sigma) ** 2) / (sigma * math.sqrt(2 * math.pi))
    # cell widths from midpoint edges (vectorised trapezoid mass)
    edges = np.empty(g.size + 1)
    edges[1:-1] = 0.5 * (g[:-1] + g[1:]); edges[0] = g[0]; edges[-1] = g[-1]
    w = pdf * np.clip(np.diff(edges), 0.0, None)
    tot = w.sum()
    if tot <= 0:
        return {"expected": 0.0, "cvar10": 0.0, "p_loss": 0.0}
    w /= tot
    expected = float((pf * w).sum())
    std = float(math.sqrt(max(0.0, ((pf - expected) ** 2 * w).sum())))
    p_loss = float(w[pf < 0].sum())
    # CVaR: sort by payoff, take the worst `cvar_q` of probability mass
    order = np.argsort(pf)
    pf_s, w_s = pf[order], w[order]
    cw = np.cumsum(w_s)
    full = cw <= cvar_q
    tail_w = float(w_s[full].sum())
    tail_pnl = float((pf_s[full] * w_s[full]).sum())
    i = int(full.sum())
    if i < w_s.size and tail_w < cvar_q:                # partial cell at the boundary
        take = cvar_q - tail_w
        tail_pnl += float(pf_s[i]) * take; tail_w += take
    cvar = (tail_pnl / tail_w) if tail_w > 0 else expected
    return {"expected": round(expected, 2), "cvar10": round(cvar, 2),
            "std": round(std, 2), "p_loss": round(p_loss, 4)}


@dataclass
class RiskRead:
    touch_put: float
    touch_call: float
    breach_put: float
    breach_call: float
    worst_touch: float
    score: float
    threatened_side: str      # "put" | "call" | "none"


def risk_score(spot: float, put_short: float, call_short: float,
               drift: float, sigma: float, horizon_frac: float = 1.0,
               expected_loss_pts: float = 0.0, loss_ref_pts: float = 400.0) -> RiskRead:
    """Composite forward-looking defend-urgency for a short-strangle/condor.

    Computes touch & expiry-breach probabilities for BOTH short strikes over the
    horizon, then a 0..1 score = worst-side touch prob × loss-severity factor.
    Direction enters ONLY as drift — it never fires a defense on its own; a low
    touch probability keeps the score low even in a strong trend. The caller pairs
    this with a cost/benefit check before acting (see the action evaluator)."""
    d, s = scale_horizon(drift, sigma, horizon_frac)
    tp = touch_prob(spot, put_short, d, s)
    tc = touch_prob(spot, call_short, d, s)
    bp = expiry_breach_prob(spot, put_short, d, s)
    bc = expiry_breach_prob(spot, call_short, d, s)
    worst = max(tp, tc)
    side = "put" if tp >= tc else "call"
    sev = min(1.0, abs(expected_loss_pts) / loss_ref_pts) if loss_ref_pts else 1.0
    score = max(0.0, min(1.0, worst * (0.5 + 0.5 * sev)))
    return RiskRead(touch_put=round(tp, 3), touch_call=round(tc, 3),
                    breach_put=round(bp, 3), breach_call=round(bc, 3),
                    worst_touch=round(worst, 3), score=round(score, 3),
                    threatened_side=side if worst > 0 else "none")
