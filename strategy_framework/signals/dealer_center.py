"""
strategy_framework/signals/dealer_center.py
============================================
DEALER CENTER — the ΔOI-weighted strike centroid: where NEW risk is being added.

    center = Σ strike·(ΔOI_call⁺ + ΔOI_put⁺) / Σ (ΔOI_call⁺ + ΔOI_put⁺)

(positive additions only — we want where fresh positioning is being *built*, not the
noise of unwinds). This is deliberately DIFFERENT from oi_migration, which tracks the
center of STANDING OI — yesterday's inventory. The dealer center tracks today's flow:
when the market repriced 24300→24600, standing OI still pointed at the old range while
the ΔOI centroid migrated up with the move — the market saying "higher is the new base
case". Treating support/resistance as fixed levels is exactly what gets short-gamma
books run over; this is the dynamic version.

Score blends two reads (PRIOR weights, documented):
  * centroid vs spot  — new risk being built above spot = acceptance of higher prices
  * put-add vs call-add aggression — heavy fresh put writing at/below spot = bullish
    (writers underwriting the level), heavy call writing = capped.

Weight-0 studied candidate; likely a cousin of oi_migration/strike_role_change — the
audit decides.  ΔOI reconstructed from levels (option_oi.reconstruct_doi, one source).
"""
from __future__ import annotations
import numpy as np
from .base import Signal, clamp
from . import option_oi

_CENTER_SCALE = 50.0    # points of centroid-vs-spot offset for a firm reading (PRIOR)


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    if chain is None:
        return Signal.no_data("dealer_center", "no option chain as-of now")
    doi = option_oi.reconstruct_doi(da, chain, now)
    if not doi:
        return Signal("dealer_center", 0.0, 0.15, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"note": "no earlier snapshot to difference ΔOI against yet"})
    spot = chain.spot
    num = den = put_add = call_add = 0.0
    for k in chain.strikes:
        dc = max(0.0, doi["call_doi"].get(k, 0.0))
        dp = max(0.0, doi["put_doi"].get(k, 0.0))
        num += k * (dc + dp)
        den += dc + dp
        call_add += dc
        put_add += dp
    if den <= 0:
        return Signal("dealer_center", 0.0, 0.2, "PRIOR", status="OK",
                      detail={"read": "no fresh OI added in window — stale positioning"})
    center = num / den
    offset = center - spot
    tot_add = put_add + call_add
    aggression = (put_add - call_add) / tot_add if tot_add > 0 else 0.0   # + = put writers bolder
    score = clamp(0.5 * float(np.tanh(offset / _CENTER_SCALE)) + 0.5 * float(np.tanh(2.0 * aggression)))
    confidence = clamp(0.30 + 0.4 * min(1.0, den / (sum(
        (chain.call_oi.get(k, 0) or 0) + (chain.put_oi.get(k, 0) or 0) for k in chain.strikes) * 0.02 + 1e-9)),
        0.30, 0.8)
    return Signal("dealer_center", float(score), float(confidence), "PRIOR",
                  detail={"dealer_center": round(center, 1), "spot": round(spot, 1),
                          "offset_pts": round(offset, 1),
                          "put_add_share": round(put_add / tot_add, 2) if tot_add else None,
                          "fresh_oi_added": int(den),
                          "window": f"{doi['prior_ts'][11:16]}→{doi['cur_ts'][11:16]}",
                          "read": ("new risk centered ABOVE spot — higher prices being accepted"
                                   if offset > 15 else
                                   "new risk centered BELOW spot — lower prices being underwritten"
                                   if offset < -15 else "new risk centered at spot — pinning here")})
