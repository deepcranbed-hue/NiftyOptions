"""
strategy_framework/signals/oi_dispersion.py
============================================
OI DISPERSION — a REGIME signal complementing the center of gravity.

COG says WHERE the OI mass sits; dispersion says how TIGHT it is around there. Same
total OI can be crowded on one strike (a pin) or smeared across many (loose
positioning) — completely different markets. Dispersion = OI-weighted standard
deviation of strikes (in points, via option_oi.oi_concentration). We emit a
non-directional TIGHTNESS score ∈ [0,1] (1 = crowded/pinned, 0 = diffuse).

kind='gate', signal_class='regime' — it never votes direction; it tells the controller
whether positioning is concentrated enough for pin dynamics to dominate.
"""
from __future__ import annotations
from .base import Signal, clamp
from . import option_oi

_STD_SCALE = 120.0     # a std of ~120 strike-pts ≈ half-tight (PRIOR; only scales the reading)


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    if chain is None:
        return Signal.no_data("oi_dispersion", "no option chain as-of now")
    c = option_oi.oi_concentration(chain)
    if c is None:
        return Signal.no_data("oi_dispersion", "no OI to form a distribution")
    tightness = clamp(1.0 / (1.0 + c["std"] / _STD_SCALE), 0.0, 1.0)   # small std → tight (→1)
    regime = "tight / pinned" if tightness > 0.6 else "loose" if tightness < 0.4 else "moderate"
    return Signal("oi_dispersion", float(tightness), float(tightness), "PRIOR",
                  detail={"oi_std_pts": round(c["std"], 1), "cog": round(c["cog"], 1),
                          "tightness": round(tightness, 3), "regime": regime,
                          "note": "regime (OI concentration 0..1), NOT a direction"})
