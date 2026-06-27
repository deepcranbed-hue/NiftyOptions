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


# --------------------------------------------------------------------------- #
# Black-Scholes + IV inversion
# --------------------------------------------------------------------------- #
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, r, sigma):
    if sigma <= 0 or T <= 0:
        return max(0.0, S - K * math.exp(-r * T))
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def implied_vol(price, S, K, T, r, lo=1e-4, hi=5.0):
    """Bisection IV solver. Returns nan if price is outside no-arb bounds."""
    intrinsic = max(0.0, S - K * math.exp(-r * T))
    if price <= intrinsic + 1e-6:
        return float("nan")
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if bs_call(S, K, T, r, mid) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------- #
# RND extraction
# --------------------------------------------------------------------------- #
def extract_rnd(strikes, call_prices, S, T, r, grid_pts=1201, smooth=3,
                put_prices=None):
    """Return (grid, density) for the risk-neutral terminal distribution.
    `smooth` = moving-average window on the IV smile before repricing.

    IMPORTANT: pass `put_prices` too. Breeden-Litzenberger needs OUT-OF-THE-MONEY
    options -- ITM prices are intrinsic-dominated and invert to garbage IV, which
    erases the put-skew left wing and flips the density's skew sign. When puts are
    given we use puts for K<S and calls for K>=S, converting puts to synthetic
    calls via put-call parity (C = P + S - K*e^{-rT}). Call-only input is kept for
    backward compatibility but is WRONG below spot."""
    strikes = np.asarray(strikes, float)
    call_prices = np.asarray(call_prices, float)

    # 0. build an OTM call-price curve (synthetic calls from OTM puts below spot)
    if put_prices is not None:
        put_prices = np.asarray(put_prices, float)
        disc = math.exp(-r * T)
        call_prices = np.where(strikes < S,
                               put_prices + S - strikes * disc,   # parity
                               call_prices)

    # 1. invert to IV
    ivs = np.array([implied_vol(c, S, k, T, r) for c, k in zip(call_prices, strikes)])
    good = ~np.isnan(ivs)
    strikes, ivs = strikes[good], ivs[good]

    # 2. smooth the smile (IV is far smoother than price)
    if smooth > 1 and len(ivs) >= smooth:
        kernel = np.ones(smooth) / smooth
        ivs = np.convolve(ivs, kernel, mode="same")

    # 3. fine grid, interpolate IV, reprice (extrapolate IV flat at the wings)
    grid = np.linspace(strikes.min(), strikes.max(), grid_pts)
    iv_grid = np.interp(grid, strikes, ivs)
    C = np.array([bs_call(S, k, T, r, sig) for k, sig in zip(grid, iv_grid)])

    # 4. Breeden-Litzenberger: density = e^{rT} * C''(K)
    dK = grid[1] - grid[0]
    dens = np.gradient(np.gradient(C, dK), dK) * math.exp(r * T)
    dens = np.clip(dens, 0, None)                 # kill tiny negative noise
    area = np.trapezoid(dens, grid)
    dens = dens / area if area > 0 else dens
    return grid, dens


# --------------------------------------------------------------------------- #
# Model-free reads off the RND
# --------------------------------------------------------------------------- #
def prob_in_range(grid, dens, low, high):
    m = (grid >= low) & (grid <= high)
    return float(np.trapezoid(dens[m], grid[m]))


def rnd_stats(grid, dens, spot):
    mean = float(np.trapezoid(grid * dens, grid))
    var = float(np.trapezoid((grid - mean) ** 2 * dens, grid))
    sd = math.sqrt(var)
    skew = float(np.trapezoid(((grid - mean) / sd) ** 3 * dens, grid)) if sd else 0.0
    p_down = prob_in_range(grid, dens, grid.min(), spot)
    return {"mean": mean, "sd": sd, "skew": skew,
            "p_below_spot": p_down, "p_above_spot": 1 - p_down}


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
