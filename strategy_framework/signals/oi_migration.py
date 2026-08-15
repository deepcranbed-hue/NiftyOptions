"""
strategy_framework/signals/oi_migration.py
===========================================
OI center-of-gravity MIGRATION — where the option market's mass is shifting.

Instead of one strike, use the whole chain: the OI-weighted mean strike (center of
gravity) for calls and for puts. Its MOVEMENT over the last window is a stronger read
than any single strike's OI, because it captures the market re-pricing its whole
distribution:

  * both COGs drifting UP  → support & resistance rising  → bullish
  * both drifting DOWN     → the mass is sliding lower     → bearish

Score = the average COG migration (calls & puts) in strike-points, squashed. ΔCOG is
measured against the same prior snapshot the ΔOI reconstruction uses (no lookahead).
Confidence rises when the two COGs AGREE (both moving the same way = a coherent shift).

Weight-0 studied candidate. Likely a cousin of breadth_oi / strike_role_change (all
read the OI walls) — the audit will say whether it earns distinct edge.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, clamp
from . import option_oi

_MIG_SCALE = 15.0      # strike-points of COG drift for a firm reading (PRIOR, calibratable)


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    if chain is None:
        return Signal.no_data("oi_migration", "no option chain as-of now")
    prev = option_oi.prior_chain(da, chain, now)
    if prev is None:
        return Signal("oi_migration", 0.0, 0.15, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"note": "no earlier snapshot to measure COG migration against"})

    cog_c, cog_p = option_oi.oi_cog(chain, "call"), option_oi.oi_cog(chain, "put")
    cog_c0, cog_p0 = option_oi.oi_cog(prev, "call"), option_oi.oi_cog(prev, "put")
    if None in (cog_c, cog_p, cog_c0, cog_p0):
        return Signal.no_data("oi_migration", "insufficient OI to form a center of gravity")

    d_call = cog_c - cog_c0        # resistance-mass migration (points)
    d_put = cog_p - cog_p0         # support-mass migration (points)
    mig = 0.5 * (d_call + d_put)
    score = clamp(float(np.tanh(mig / _MIG_SCALE)))

    # confidence: the two sides agreeing = a coherent shift; disagreement = churn.
    agree = 1.0 if (d_call >= 0) == (d_put >= 0) else 0.0
    confidence = clamp(0.30 + 0.35 * agree + 0.15 * min(1.0, abs(mig) / _MIG_SCALE), 0.30, 0.85)

    return Signal("oi_migration", float(score), float(confidence), "PRIOR",
                  detail={"cog_call": round(cog_c, 1), "cog_put": round(cog_p, 1),
                          "d_cog_call": round(d_call, 1), "d_cog_put": round(d_put, 1),
                          "migration_pts": round(mig, 1), "sides_agree": bool(agree),
                          "read": ("mass rising → bullish" if mig > 2 else
                                   "mass falling → bearish" if mig < -2 else "mass stable")})
