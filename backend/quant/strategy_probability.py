"""
strategy_probability.py
-----------------------
Turns the option chain into probabilities, prices the EV of candidate
structures, and shows how the regime classifier (market_regime.py +
index_attribution.py) tilts those probabilities -- the bridge from
"what regime are we in" to "which strategy is positive-EV, and by how much".

Key idea
--------
The chain is the BASELINE risk-neutral distribution (market-implied). The regime
layer's only legitimate output is a *calibrated adjustment* to that baseline:
    sigma_mult : realised vol vs implied  (>1 in vol-expansion regimes)
    mu_shift   : small directional drift  (distrust; size small)

A strategy is only worth doing when the regime-adjusted EV is positive by MORE
than costs AND the adjustment is itself validated against realised outcomes
(Brier / CRPS). Otherwise you are just paying spreads to express a dashboard.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


# --------------------------------------------------------------------------- #
# 1. Market-implied distribution from the chain
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def implied_move(spot: float, atm_iv: float, days: float) -> float:
    """1-sigma move in points. atm_iv is annualised (e.g. 0.093 for 9.3%).
    Approximately equals the ATM straddle price."""
    return spot * atm_iv * math.sqrt(days / 365.0)


def prob_below(strike: float, spot: float, atm_iv: float, days: float,
               mu: float = 0.0) -> float:
    """Risk-neutral P(S_T < strike) under lognormal terminal distribution.
    mu is an annualised drift (0 for the risk-neutral baseline; regime can
    inject a small tilt)."""
    T = days / 365.0
    sig = atm_iv * math.sqrt(T)
    if sig <= 0:
        return 1.0 if spot < strike else 0.0
    d2 = (math.log(strike / spot) - (mu * T - 0.5 * sig * sig)) / sig
    return _norm_cdf(d2)


def prob_in_range(low: float, high: float, spot: float, atm_iv: float,
                  days: float, mu: float = 0.0) -> float:
    return prob_below(high, spot, atm_iv, days, mu) - prob_below(low, spot, atm_iv, days, mu)


# --------------------------------------------------------------------------- #
# 2. Regime -> distribution adjustment (the classifier's only job here)
# --------------------------------------------------------------------------- #
@dataclass
class RegimeAdjustment:
    sigma_mult: float = 1.0     # realised vol vs implied; >1 = vol expansion
    mu_shift: float = 0.0       # annualised directional drift; keep small

# Map a regime (from market_regime.Driver + a vol-expansion flag) to an
# adjustment. These are PRIORS to be calibrated against realised outcomes.
def regime_to_adjustment(driver: str, conviction: float,
                         vol_expansion: bool) -> RegimeAdjustment:
    if vol_expansion:
        # e.g. AI_SEMI selloff confirmed by KOSPI/SOX -> mark realised vol up,
        # small bearish drift scaled by conviction.
        return RegimeAdjustment(sigma_mult=1.0 + 0.6 * conviction,
                                mu_shift=-0.25 * conviction)
    # quiet/range regime: realised vol slightly below implied (vol risk premium)
    return RegimeAdjustment(sigma_mult=1.0 - 0.10 * conviction, mu_shift=0.0)


# --------------------------------------------------------------------------- #
# 3. Structure EV via Monte Carlo on the (adjusted) terminal distribution
# --------------------------------------------------------------------------- #
def _terminal_samples(spot, atm_iv, days, adj: RegimeAdjustment, n=200_000, seed=7):
    T = days / 365.0
    sig = atm_iv * adj.sigma_mult * math.sqrt(T)
    drift = (adj.mu_shift * T) - 0.5 * sig * sig
    rng = np.random.default_rng(seed)
    return spot * np.exp(drift + sig * rng.standard_normal(n))


def iron_condor_ev(spot, atm_iv, days, *, put_short, put_long, call_short,
                   call_long, net_credit, adj: RegimeAdjustment | None = None,
                   n=200_000):
    """EV (in points) of a SHORT iron condor at expiry, under the terminal
    distribution implied by atm_iv and the regime adjustment."""
    adj = adj or RegimeAdjustment()
    S = _terminal_samples(spot, atm_iv, days, adj, n)
    payoff = (net_credit
              - np.maximum(0.0, put_short - S) + np.maximum(0.0, put_long - S)
              - np.maximum(0.0, S - call_short) + np.maximum(0.0, S - call_long))
    p_stay = float(np.mean((S > put_short) & (S < call_short)))
    width = max(put_short - put_long, call_long - call_short)
    max_loss = width - net_credit
    return {
        "ev_points": float(np.mean(payoff)),
        "p_max_profit": p_stay,            # P(expire between short strikes)
        "p_loss": float(np.mean(payoff < 0)),
        "max_profit": net_credit,
        "max_loss": max_loss,
        "ev_over_maxloss": float(np.mean(payoff)) / max_loss if max_loss else float("nan"),
        "win_rate_needed": max_loss / (max_loss + net_credit),  # breakeven P(stay)
    }


# --------------------------------------------------------------------------- #
# 4. Demo: the chain from the screenshot (spot 24,200), condor 24100/24000 -
#    24350/24450, priced market-implied vs regime-adjusted.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    spot, atm_iv, days = 24_200.0, 0.093, 7.0   # ATM IV ~9.3%, weekly expiry

    # net credit from the screenshot LTPs:
    #   sell 24100P @61.65 / buy 24000P @39.75 -> +21.90
    #   sell 24350C @48.40 / buy 24450C @26.30 -> +22.10
    net_credit = 44.0
    strikes = dict(put_short=24_100, put_long=24_000,
                   call_short=24_350, call_long=24_450)

    print(f"Spot {spot:.0f} | ATM IV {atm_iv:.1%} | {days:.0f}d to expiry")
    print(f"1-sigma implied move: +/- {implied_move(spot, atm_iv, days):.0f} pts "
          f"(~ ATM straddle)\n")

    # --- BASELINE: market-implied (no regime view) ---
    base = iron_condor_ev(spot, atm_iv, days, net_credit=net_credit, **strikes)
    print("MARKET-IMPLIED (baseline):")
    print(f"  P(stay in 24100-24350)   : {base['p_max_profit']:.1%}")
    print(f"  Breakeven win-rate needed: {base['win_rate_needed']:.1%}")
    print(f"  EV                        : {base['ev_points']:+.1f} pts  "
          f"(EV/maxloss {base['ev_over_maxloss']:+.1%})")
    edge = base['p_max_profit'] - base['win_rate_needed']
    print(f"  Edge vs breakeven         : {edge:+.1%}  "
          f"-> {'sellable' if edge > 0 else 'no edge'} before costs\n")

    # --- REGIME-ADJUSTED: current AI_SEMI vol-expansion, conviction 0.73 ---
    adj = regime_to_adjustment("ai_semiconductor", conviction=0.73,
                               vol_expansion=True)
    reg = iron_condor_ev(spot, atm_iv, days, net_credit=net_credit, adj=adj, **strikes)
    print(f"REGIME-ADJUSTED (sigma x{adj.sigma_mult:.2f}, drift {adj.mu_shift:+.2%}):")
    print(f"  P(stay in 24100-24350)   : {reg['p_max_profit']:.1%}")
    print(f"  EV                        : {reg['ev_points']:+.1f} pts  "
          f"(EV/maxloss {reg['ev_over_maxloss']:+.1%})")
    print(f"  P(loss)                   : {reg['p_loss']:.1%}")
    print()
    print(f"  -> Regime moves P(stay) by {reg['p_max_profit']-base['p_max_profit']:+.1%} "
          f"and EV by {reg['ev_points']-base['ev_points']:+.1f} pts.")
    verdict = ("vol-expansion regime kills the condor's edge -> prefer LONG vol"
               if reg['ev_points'] < 0 <= base['ev_points']
               else "regime preserves the structure's sign")
    print(f"  VERDICT: {verdict}.")
