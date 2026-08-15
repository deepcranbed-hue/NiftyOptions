"""
strategy_framework/signals/skew_rnd.py
======================================
Skew and Risk-Neutral Distribution (RND) directional tilt.

Two option-surface reads:
  * Skew / risk-reversal : if OTM puts are much richer than OTM calls the market
                           is paying up for downside protection (bearish tilt);
                           the reverse is a bullish tilt. Measured from IV when
                           available, else proxied from OTM put vs call premium
                           per unit distance.
  * RND drift            : the risk-neutral mean vs spot. RND mean above spot =>
                           market-implied bullish drift; below => bearish.

This module tries to use the project's calibrated engines
(backend/quant/rnd.py, backend/quant/skew/adapter.py) when importable; if they
or scipy are unavailable it falls back to a lightweight premium-based proxy so
the framework still produces a (lower-confidence) signal.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, squash, clamp


def _rnd_drift_via_engine(chain, dte_days: float):
    """Use backend/quant/rnd.py if scipy + the module are available."""
    try:
        import sys, os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from backend.quant.rnd import extract_rnd, rnd_stats  # noqa
    except Exception:
        return None
    try:
        ks = np.array(chain.strikes, float)
        calls = np.array([chain.call_ltp.get(k, 0.0) for k in chain.strikes], float)
        puts = np.array([chain.put_ltp.get(k, 0.0) for k in chain.strikes], float)
        T = max(dte_days / 365.0, 1e-5)
        grid, dens = extract_rnd(ks, calls, chain.spot, T, 0.0655, put_prices=puts)
        st = rnd_stats(grid, dens, chain.spot, strikes=ks, call_ltp=calls, put_ltp=puts)
        if st.get("provenance") == "FALLBACK":
            return None                       # calibration guard failed
        return st
    except Exception:
        return None


def _skew_proxy(chain):
    """Premium-based risk-reversal proxy when IV is missing.

    Compare a ~5%-OTM put vs a ~5%-OTM call (matched distance). Richer put =>
    negative (bearish) tilt.
    """
    spot = chain.spot
    target = 0.03 * spot
    puts = [(k, chain.put_ltp.get(k, 0)) for k in chain.strikes if k < spot and chain.put_ltp.get(k, 0) > 0]
    calls = [(k, chain.call_ltp.get(k, 0)) for k in chain.strikes if k > spot and chain.call_ltp.get(k, 0) > 0]
    if not puts or not calls:
        return None
    pk, pp = min(puts, key=lambda x: abs((spot - x[0]) - target))
    ck, cp = min(calls, key=lambda x: abs((x[0] - spot) - target))
    # normalise by distance so it's a per-point richness
    p_rich = pp / max(spot - pk, 1e-6)
    c_rich = cp / max(ck - spot, 1e-6)
    rr = (c_rich - p_rich) / (c_rich + p_rich + 1e-9)   # + = calls richer = bullish
    return {"rr_proxy": round(rr, 4), "put_k": pk, "call_k": ck,
            "put_ltp": pp, "call_ltp": cp}


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    if chain is None:
        return Signal.no_data("skew_rnd", "no chain snapshot")
    dte = ctx.get("dte_days", 1.0)
    spot = chain.spot

    # ---- QUALITY GATE (sometimes not trading is the best prediction) --------
    # On/near expiry the RND is degenerate (little time value), opening-auction
    # noise dominates, and a few points of drift on near-worthless options
    # produce spurious scores. Flag these as low-quality and heavily damp them.
    quality = 1.0
    q_notes = []
    if dte <= 1.0:
        quality *= 0.15; q_notes.append(f"DTE {dte:.2f} ≤ 1 — RND degenerate")
    elif dte <= 2.0:
        quality *= 0.5; q_notes.append(f"DTE {dte:.2f} — expiry regime")

    parts, detail = [], {}

    st = _rnd_drift_via_engine(chain, dte)
    if st is not None:
        mean = st.get("mean")
        sd = st.get("sd") or 0.0
        # RND width sanity: too narrow => not enough spread to trust the drift.
        if sd and sd / spot < 0.003:
            quality *= 0.4; q_notes.append(f"RND width {sd:.0f} very narrow")
        if mean and sd > 0:
            # σ-NORMALISED drift (z-score): how many implied std devs is the RND
            # mean from spot? Comparable across vol regimes, unlike a fixed %.
            z = (mean - spot) / sd
            drift = squash(z, scale=1.0)                 # z already in std units
            parts.append((drift, 0.6))
            detail["rnd"] = {"mean": round(mean, 1), "sd": round(sd, 1),
                             "drift_z": round(z, 3),
                             "skew": round(st.get("skew", 0), 3),
                             "p_above_spot": st.get("p_above_spot")}
        if st.get("skew") is not None:
            parts.append((squash(st["skew"], 1.0), 0.2))

    # 25Δ risk-reversal from IV backed out of the LTPs (vol-trader skew). Negative
    # rr (puts richer than calls) => downside skew => bearish tilt.
    rr_used = False
    try:
        from .. import bs
        ivsk = bs.iv_skew(chain, dte)
        rr = ivsk.get("rr_iv_pct")
        if rr is not None:
            parts.append((squash(rr / 2.0, scale=1.0), 0.3 if st else 0.8))
            detail["iv_skew"] = ivsk
            rr_used = True
    except Exception:
        pass

    if not rr_used:                                   # fallback: premium proxy
        prox = _skew_proxy(chain)
        if prox is not None:
            parts.append((squash(prox["rr_proxy"] * 5, scale=1.0), 0.3 if st else 0.8))
            detail["skew_proxy"] = prox

    if not parts:
        return Signal("skew_rnd", 0.0, 0.15, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"note": "no IV, no priced OTM wings"})

    w = sum(p[1] for p in parts)
    score = clamp(sum(s * wt for s, wt in parts) / w)
    base_conf = 0.55 if st is not None else 0.30
    confidence = clamp(base_conf * quality, 0.0, 1.0)   # quality damps confidence
    status = "LOW_QUALITY" if quality < 0.4 else "OK"
    return Signal("skew_rnd", score, confidence, "PRIOR", status=status,
                  detail={**detail, "engine": "rnd" if st is not None else "proxy",
                          "quality": round(quality, 2), "quality_notes": q_notes})
