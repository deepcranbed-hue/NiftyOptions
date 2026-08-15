"""
strategy_framework/signals/pin_pressure.py
===========================================
Pin Pressure → a REGIME signal (pin STRENGTH), NOT a directional vote.

Pin Pressure Index PPI = (CallOI_ATM + PutOI_ATM) / ATM straddle answers "how hard is
it for price to ESCAPE this strike?", not "which way will it go". Those are different
questions (thanks to the design review). So this emits pin STRENGTH in [0,1] — a
non-directional regime read — and never votes bullish/bearish. Direction, when a pin
exists, comes from the Position/Confirmation signals; the regime controller uses this
strength to modulate how much to trust them (strong pin → expect reversion/range, so
damp directional conviction).

Strength blends the straddle-normalised PPI with the OI CONCENTRATION at the pin strike
(unit-free), so it doesn't depend on the raw OI scale. Registered kind='gate',
signal_class='regime'.
"""
from __future__ import annotations
from .base import Signal, clamp
from . import option_oi

_PPI_REF = 8.0     # reference PPI for a 'notable' pin (PRIOR; calibratable, only scales strength)


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    if chain is None:
        return Signal.no_data("pin_pressure", "no option chain as-of now")
    spot = chain.spot
    S, k_atm = option_oi.atm_straddle(chain)
    pin_k, pin_oi, pin_share = option_oi.pin_strike(chain)
    ppi = ((chain.call_oi.get(k_atm, 0) or 0) + (chain.put_oi.get(k_atm, 0) or 0)) / S if S > 0 else 0.0

    # pin STRENGTH ∈ [0,1]: how strong/concentrated the pin is. NON-directional.
    strength = clamp(0.5 * min(1.0, ppi / _PPI_REF) + 0.5 * min(1.0, pin_share / 0.25), 0.0, 1.0)
    regime = "gamma pin" if strength > 0.6 else "soft pin" if strength > 0.35 else "no pin"

    return Signal("pin_pressure", float(strength), float(strength), "PRIOR",
                  detail={"ppi": round(ppi, 2), "pin_strike": pin_k, "spot": round(spot, 1),
                          "dist_to_pin": round(pin_k - spot, 1), "pin_oi": int(pin_oi),
                          "pin_share": round(pin_share, 3), "atm_straddle": round(S, 1),
                          "pin_strength": round(strength, 3), "regime": regime,
                          "note": "regime (pin strength 0..1), NOT a direction — modulates trust"})
