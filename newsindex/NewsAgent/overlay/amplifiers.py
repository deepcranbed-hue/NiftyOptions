"""
amplifiers.py — generalize the oil level-amplifier idea to other level-sensitive drivers.

The insight (from the review): the SAME % move matters more at some absolute levels than
others. Oil already has this in the engine. Here we extend the concept — as PRIOR bands —
to USDINR and India VIX, which are equally level/regime sensitive:

  * USDINR: crossing 83 / 84 / 85 are psychological + policy (RBI intervention) thresholds.
  * India VIX: fear regimes — calm vs elevated vs stress vs panic.

These are amplifiers on the *interpretation*, tagged PRIOR. They do not overwrite any Core
number; they annotate how hard a given move bites at the current level.
"""
from __future__ import annotations


# The rupee re-bases over time (it was ~83 in 2024, ~95 in 2026), so absolute thresholds go
# stale and everything saturates in the top band. Instead the bands are RELATIVE to a single
# anchor — the current-regime "normal" level. Re-base = change ONE number (or wire it to a
# trailing average / RBI reference rate later).  As of the last review: USDINR ≈ 95.
USDINR_NORMAL = 95.0        # <-- update this one number as the rupee re-bases


def usdinr_level(price: float | None, normal: float | None = None) -> dict:
    """Rupee stress bands, RELATIVE to the current-regime normal (higher = weaker = more stress).
    Bands are offsets from `normal` so they never go stale on an absolute drift."""
    if price is None:
        return {"price": None, "multiplier": 1.0, "band": "unknown"}
    ref = normal if normal is not None else USDINR_NORMAL
    d = price - ref
    if d < -3:
        band, mult = "comfortable", 0.7
    elif d < 1:
        band, mult = "normal", 1.0
    elif d < 3:
        band, mult = "watch (RBI attentive)", 1.3
    elif d < 5:
        band, mult = "stress (intervention likely)", 1.6
    else:
        band, mult = "record-weak (policy risk)", 2.0
    return {"price": price, "multiplier": mult, "band": band, "vs_normal": round(d, 2), "normal": ref}


def vix_level(price: float | None) -> dict:
    """India VIX fear-regime bands."""
    if price is None:
        return {"price": None, "multiplier": 1.0, "band": "unknown"}
    if price < 13:
        band, mult = "calm / complacent", 0.7
    elif price < 16:
        band, mult = "normal", 1.0
    elif price < 20:
        band, mult = "elevated", 1.3
    elif price < 25:
        band, mult = "stress", 1.7
    else:
        band, mult = "fear / panic", 2.2
    return {"price": price, "multiplier": mult, "band": band}


def all_amplifiers(brent: float | None, usdinr: float | None,
                   vix: float | None, oil_level_fn) -> dict:
    """Bundle the three level amplifiers. oil_level_fn is core.oil_level (engine-consistent)."""
    return {
        "oil": oil_level_fn(brent),
        "usdinr": usdinr_level(usdinr),
        "vix": vix_level(vix),
    }
