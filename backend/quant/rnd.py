"""
rnd.py
------
Extract the risk-neutral density (RND) from the option chain and read
model-free probabilities off it -- the market's own probability estimate,
which is the BENCHMARK the sentiment tilt must beat (not an independent input).

Method (Breeden-Litzenberger):  f_Q(K) = e^{rT} * d2C/dK2
We invert call prices to IV, smooth the smile (IV is smoother than price),
reprice on a fine grid, then take the second derivative -> density. This keeps
the skew the chain actually shows, which a single-ATM-IV lognormal discards.

IMPORTANT
---------
* f_Q is RISK-NEUTRAL. It overstates downside vs realised frequencies by the
  variance/skew risk premium. Convert Q->P (q_to_p) before using as a real
  probability for EV, or premium-selling EV will look wrong by construction.
* The RND is the market's view -> use it as the baseline. Sentiment's job is to
  produce a P that DIFFERS from it; the edge is that difference, net of costs.
"""

from __future__ import annotations

import math
import numpy as np

# NumPy 2.0-safe integrator (BUG 3)
_trap = getattr(np, 'trapezoid', getattr(np, 'trapz', None))

# ---- single source for Black-Scholes IV inversion ---------------------------------
# The inverter lives in strategy_framework/bs.py. This module is an ADAPTER over that
# one implementation — NOT a second implementation (same rule as
# strategy_framework/signals/futures_oi.py over backend.quant.intraday_oi).
import os as _os, sys as _sys
_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
from strategy_framework.bs import implied_vol as _bs_implied_vol

_SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)   # 0.797885 — E|S-F| / sigma


# --------------------------------------------------------------------------- #
# Black-Scholes + IV inversion
# --------------------------------------------------------------------------- #
# _norm_cdf / bs_call lived here to serve a local bisection IV solver. That solver
# was replaced by a delegation to strategy_framework/bs.py (D-SC-03), leaving both
# helpers with zero call sites. The normal CDF now comes from bs.ncdf — one
# definition, not a third copy.
from strategy_framework.bs import ncdf as _norm_cdf   # noqa: F401 (kept: public-ish)


def implied_vol(price, S, K, T, r, lo=1e-4, hi=5.0):
    """Call IV. Returns nan if price is outside no-arb bounds.

    The inversion itself is strategy_framework.bs.implied_vol (single source). Kept
    HERE, deliberately:
      * the FORWARD-discounted no-arb gate  max(0, S - K*e^{-rT})  — the correct bound
        for a call, and stricter than bs.py's undiscounted one, so this gate stays;
      * the nan sentinel — extract_rnd() feeds these straight into a numpy grid, where
        None would raise. nan propagates; None does not.
    `lo`/`hi` are retained for signature compatibility and are no longer used.
    """
    intrinsic = max(0.0, S - K * math.exp(-r * T))
    if price <= intrinsic + 1e-6:
        return float("nan")
    sigma = _bs_implied_vol(price, S, K, T, r, True)
    return float("nan") if sigma is None else float(sigma)


# --------------------------------------------------------------------------- #
# RND extraction
# --------------------------------------------------------------------------- #
def extract_rnd(strikes, call_prices, S, T, r, grid_pts=1201, smooth=3,
                put_prices=None, min_price=3.0, band_pts=500):
    """Return (grid, density) for the risk-neutral terminal distribution.
    `smooth` = moving-average window on the IV smile before repricing.

    IMPORTANT: pass `put_prices` too. Breeden-Litzenberger needs OUT-OF-THE-MONEY
    options -- ITM prices are intrinsic-dominated and invert to garbage IV, which
    erases the put-skew left wing and flips the density's skew sign. When puts are
    given we use puts for K<S and calls for K>=S, converting puts to synthetic
    calls via put-call parity (C = P + S - K*e^{-rT}). Call-only input is kept for
    backward compatibility but is WRONG below spot."""
    K = np.asarray(strikes, float)
    C = np.asarray(call_prices, float)
    
    if put_prices is None:
        raise ValueError("put_prices is required for hardened RND extraction")
    P = np.asarray(put_prices, float)
    disc = math.exp(-r * T)
    
    # 1) OTM legs -> call-equivalent via put-call parity for K<spot
    Ceq = np.where(K < S, P + S - K * disc, C)
    
    # 2) raw density = discounted 2nd derivative wrt K
    dens = np.gradient(np.gradient(Ceq, K), K) / disc
    
    # 3) CLIP negatives (numerical noise can go below 0)
    dens = np.maximum(dens, 0.0)
    
    # 4) TRIM unreliable deep-OTM wings
    keep = (C >= min_price) & (P >= min_price) & (np.abs(K - S) <= band_pts)
    if keep.sum() < 5:
        keep = (np.abs(K - S) <= band_pts)
    Kk, dk = K[keep], dens[keep]
    
    # 5) optional light smoothing of the density to tame 2nd-derivative noise
    if smooth and len(dk) >= 5:
        kern = np.array([1, 2, 3, 2, 1], float)
        kern /= kern.sum()
        dk = np.convolve(dk, kern, mode="same")
        
    # 6) interpolate to a fine grid and RENORMALIZE
    grid = np.linspace(Kk.min(), Kk.max(), grid_pts)
    di = np.interp(grid, Kk, dk)
    area = _trap(di, grid)
    if area > 0:
        di = di / area
        
    return grid, di


# --------------------------------------------------------------------------- #
# Model-free reads off the RND
# --------------------------------------------------------------------------- #
def prob_in_range(grid, dens, low, high):
    m = (grid >= low) & (grid <= high)
    return float(_trap(dens[m], grid[m]))


def rnd_stats(grid, dens, spot, strikes=None, call_ltp=None, put_ltp=None):
    mean = float(_trap(grid * dens, grid))
    var = float(_trap((grid - mean) ** 2 * dens, grid))
    sd = math.sqrt(var) if var > 0 else 0.0
    skew = float(_trap(((grid - mean) / sd) ** 3 * dens, grid)) if sd else 0.0
    p_down = prob_in_range(grid, dens, grid.min(), spot)
    
    res = {"mean": mean, "sd": sd, "skew": skew,
           "p_below_spot": p_down, "p_above_spot": 1 - p_down}
           
    # skew sanity guard (independent of calibration)
    res["skew_ok"] = abs(skew) <= 1.0
    if not res["skew_ok"]:
        res["skew_warning"] = (f"⚠ |skew| {skew:.2f} > 1.0 — density CONTAMINATED "
                               f"(deep-OTM tail noise). Do not trust this RND.")
           
    if strikes is not None and call_ltp is not None and put_ltp is not None:
        strikes_arr = np.asarray(strikes, float)
        call_arr = np.asarray(call_ltp, float)
        put_arr = np.asarray(put_ltp, float)
        
        atm_c = float(np.interp(spot, strikes_arr, call_arr))
        atm_p = float(np.interp(spot, strikes_arr, put_arr))
        straddle = atm_c + atm_p
        
        # The straddle prices the MEAN ABSOLUTE move, E|S_T - F| = sigma*sqrt(2/pi)
        # ~= 0.7979*sigma. It is NOT a 1-sigma move. `sd` here IS a 1 sigma, so convert
        # the straddle up before comparing: sigma = straddle / 0.7979 = 1.2533*straddle.
        # (Verified numerically: a normal density of width sigma prices an ATM straddle
        # at exactly 0.7979*sigma, for every sigma.) With this, `ratio` is 1.00 for a
        # correct RND and the [0.7, 1.4] band means what it says. Previously the raw
        # straddle was used, putting a correct RND at 1.25 and leaving the band
        # mis-centred. NOTE: scratch_scripts/update_rnd.py multiplied by 0.8 instead of
        # dividing — that put a correct RND at 1.57 and FAILED it. Both were wrong.
        straddle_move = straddle / _SQRT_2_OVER_PI          # straddle -> 1 sigma
        ratio = sd / straddle_move if straddle_move > 0 else float("nan")
        calibrated = (0.7 <= ratio <= 1.4) and res["skew_ok"]
        
        res.update({
            "straddle_pts": round(straddle, 1),
            "straddle_1sigma_pts": round(straddle_move, 1),   # = 1.2533 * straddle_pts
            "calibration_ratio": round(ratio, 2),
            "calibrated": bool(calibrated),
            "provenance": "PRIMARY" if calibrated else "FALLBACK",
            "warning": ("" if calibrated else
                f"⚠ RND move {sd:.0f} vs straddle-implied 1σ {straddle_move:.0f} "
                f"(straddle {straddle:.0f}, ratio {ratio:.2f})" +
                (f", skew {skew:.2f}" if not res["skew_ok"] else "") +
                " — do NOT trust the ranking off this RND.")
        })
                          
    return res


def q_to_p(prob_q, variance_premium=0.85):
    """Crude Q->P de-risking: shrink risk-neutral tail mass toward the centre by
    the variance risk premium. variance_premium<1 because realised vol tends to
    be below implied. CALIBRATE this against your realised distribution; it is
    the single number that decides whether premium-selling EV is +ve."""
    # toy monotone shrink of a tail probability toward 0.5
    return 0.5 + (prob_q - 0.5) * variance_premium


# --------------------------------------------------------------------------- #
# Demo: the screenshot chain (call LTPs), spot 24200
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    strikes = list(range(23750, 24850, 50))
    call_ltp = [478.60, 430.00, 383.85, 340.05, 295.50, 252.25, 212.40, 175.20,
                141.65, 112.70, 86.90, 65.35, 48.40, 35.70, 26.30, 19.35, 14.00,
                10.75, 8.30, 6.30, 5.20, 3.95]
    S, T, r = 24_200.0, 7 / 365, 0.0655

    grid, dens = extract_rnd(strikes, call_ltp, S, T, r)
    st = rnd_stats(grid, dens, S)

    print("RISK-NEUTRAL DISTRIBUTION (from the chain):")
    print(f"  mean {st['mean']:.0f} vs spot {S:.0f}  (drift {st['mean']-S:+.0f})")
    print(f"  sd {st['sd']:.0f} pts | skew {st['skew']:+.2f} "
          f"({'put-skewed / downside-fat' if st['skew'] < 0 else 'call-skewed'})")
    print(f"  P(below spot) {st['p_below_spot']:.1%} | "
          f"P(above spot) {st['p_above_spot']:.1%}")

    p_stay_q = prob_in_range(grid, dens, 24_100, 24_350)
    # lognormal-ATM number from strategy_probability.py for the same band:
    p_stay_lognormal = 0.311
    print(f"\nP(stay 24100-24350):")
    print(f"  RND (real chain, with skew) : {p_stay_q:.1%}")
    print(f"  lognormal ATM-IV (no skew)  : {p_stay_lognormal:.1%}")
    print(f"  difference from skew        : {p_stay_q - p_stay_lognormal:+.1%}")

    print(f"\nQ->P de-risked P(stay)        : {q_to_p(p_stay_q):.1%}  "
          f"(harvest the variance premium; CALIBRATE the factor)")
    print("\nUse RND as the benchmark; the trade edge is your sentiment-tilted P "
          "minus this, net of costs.")
