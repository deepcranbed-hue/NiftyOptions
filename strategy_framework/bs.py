"""
strategy_framework/bs.py
========================
Shared Black-Scholes / implied-vol utilities, used by both the feature store
(features/extractor.py) and the skew_rnd signal, so the IV math lives in one place.

`implied_vol` inverts an option's market price (LTP) to σ — this is how we get
implied vol when the feed stores IV=0 but does store prices. `iv_skew` builds the
volatility-trader skew block (ATM IV, 25Δ risk-reversal, butterfly, smile slope +
curvature) from those solved IVs.
"""
from __future__ import annotations
import math
import numpy as np


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_price(S, K, T, sigma, r, call):
    if sigma <= 0 or T <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if call:
        return S * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2)
    return K * math.exp(-r * T) * ncdf(-d2) - S * ncdf(-d1)


def bs_delta(S, K, T, sigma, r=0.0655, call=True):
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return ncdf(d1) if call else ncdf(d1) - 1.0


def bs_vega_per_volpt(S, K, T, sigma, r=0.0655):
    """Option vega in PRICE POINTS per 1 volatility POINT (i.e. per 0.01 of σ).
    Same for calls and puts. Used to size overnight IV-spike risk on a position."""
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return S * npdf(d1) * math.sqrt(T) * 0.01     # ×0.01 → per 1 vol point


def implied_vol(price, S, K, T, r=0.0655, call=True):
    """Back out implied vol from an option's market price by inverting BS. Newton
    with a bisection fallback. None if price < intrinsic or won't converge."""
    if price is None or price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(0.0, (S - K) if call else (K - S))
    if price < intrinsic - 1e-6:
        return None
    sqrtT = math.sqrt(T)
    sigma = 0.2
    for _ in range(40):
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
        vega = S * npdf(d1) * sqrtT
        diff = bs_price(S, K, T, sigma, r, call) - price
        if abs(diff) < 1e-4:
            return sigma if 1e-3 < sigma < 5 else None
        if vega < 1e-8:
            break
        sigma -= diff / vega
        if sigma <= 1e-4 or sigma > 5:
            break
    lo, hi = 1e-4, 5.0
    if (bs_price(S, K, T, lo, r, call) - price) * (bs_price(S, K, T, hi, r, call) - price) > 0:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, r, call) > price:
            hi = mid
        else:
            lo = mid
    sigma = 0.5 * (lo + hi)
    return sigma if 1e-3 < sigma < 5 else None


def _iv_at_delta(points, target):
    if len(points) < 2:
        return None
    pts = sorted(points, key=lambda x: x[0])
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if (d0 - target) * (d1 - target) <= 0 and d1 != d0:
            return v0 + (v1 - v0) * (target - d0) / (d1 - d0)
    return min(pts, key=lambda x: abs(x[0] - target))[1]


def strike_ivs(chain, dte_days):
    """Per-strike OTM implied vol: stored IV if present, else solved from LTP.
    Returns (call_iv_map for K>=spot, put_iv_map for K<=spot)."""
    spot = chain.spot
    T = max(dte_days / 365.0, 1e-5)

    def _stored(v):
        if not v or v <= 0:
            return None
        return v / 100.0 if v > 3 else v
    civ, piv = {}, {}
    for k in chain.strikes:
        if k >= spot:
            civ[k] = _stored(chain.call_iv.get(k)) or implied_vol(
                chain.call_ltp.get(k), spot, k, T, call=True)
        if k <= spot:
            piv[k] = _stored(chain.put_iv.get(k)) or implied_vol(
                chain.put_ltp.get(k), spot, k, T, call=False)
    return civ, piv


def iv_skew(chain, dte_days):
    """Vol-trader skew block from (solved) IV: atm_iv, 25Δ risk-reversal, butterfly,
    smile slope + curvature. {} if no IV could be obtained."""
    spot = chain.spot
    T = max(dte_days / 365.0, 1e-5)
    civ, piv = strike_ivs(chain, dte_days)
    if not any(v for v in civ.values()) and not any(v for v in piv.values()):
        return {}
    atm = chain.atm_strike()
    atm_iv = next((v for v in (civ.get(atm), piv.get(atm)) if v), None)

    call_pts, put_pts = [], []
    for k in chain.strikes:
        if k >= spot and civ.get(k):
            d = bs_delta(spot, k, T, civ[k], call=True)
            if d is not None:
                call_pts.append((d, civ[k]))
        if k <= spot and piv.get(k):
            d = bs_delta(spot, k, T, piv[k], call=False)
            if d is not None:
                put_pts.append((d, piv[k]))
    iv_25c = _iv_at_delta(call_pts, 0.25)
    iv_25p = _iv_at_delta(put_pts, -0.25)

    out = {"atm_iv_pct": round(atm_iv * 100, 2) if atm_iv else None}
    if iv_25c is not None and iv_25p is not None:
        out["rr_iv_pct"] = round((iv_25c - iv_25p) * 100, 3)
        if atm_iv:
            out["butterfly_pct"] = round(((iv_25c + iv_25p) / 2 - atm_iv) * 100, 3)
    xs, ys = [], []
    for k in chain.strikes:
        v = civ.get(k) if k >= spot else piv.get(k)
        if v:
            xs.append(math.log(k / spot)); ys.append(v)
    if len(xs) >= 3:
        a, b, _ = np.polyfit(np.array(xs), np.array(ys), 2)
        out["smile_slope"] = round(float(b), 4)
        out["smile_curvature"] = round(float(a), 4)
    return out
