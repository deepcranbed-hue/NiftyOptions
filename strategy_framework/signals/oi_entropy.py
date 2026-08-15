"""
strategy_framework/signals/oi_entropy.py
========================================
OI ENTROPY — a REGIME signal: how CROWDED vs distributed is the open interest?

Shannon entropy of the normalised OI distribution, H = −Σ pᵢ log pᵢ, divided by log(N)
so it's in [0,1] (via option_oi.oi_concentration). Low entropy = everyone crowded into
one strike (pin); high entropy = inventory spread across the chain. We emit CROWDING =
1 − normalised entropy (1 = crowded/pin, 0 = uniform). Non-directional.

Entropy and dispersion are cousins (both measure concentration) and will likely
correlate — that's fine; they enter as weight-0 candidates and the redundancy audit
decides whether both earn a place or one represents the pair. Your point exactly:
'pin strength should really be PPI + Entropy + Dispersion' — so let the study learn
that combination rather than hardcoding it. kind='gate', signal_class='regime'.
"""
from __future__ import annotations
from .base import Signal, clamp
from . import option_oi


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    if chain is None:
        return Signal.no_data("oi_entropy", "no option chain as-of now")
    c = option_oi.oi_concentration(chain)
    if c is None:
        return Signal.no_data("oi_entropy", "no OI to form a distribution")
    crowding = clamp(1.0 - c["entropy_norm"], 0.0, 1.0)      # low entropy → crowded (→1)
    regime = "crowded / pinned" if crowding > 0.5 else "distributed" if crowding < 0.25 else "mixed"
    return Signal("oi_entropy", float(crowding), float(crowding), "PRIOR",
                  detail={"entropy_norm": round(c["entropy_norm"], 3),
                          "crowding": round(crowding, 3), "n_strikes": c["n"], "regime": regime,
                          "note": "regime (OI crowding 0..1), NOT a direction"})
