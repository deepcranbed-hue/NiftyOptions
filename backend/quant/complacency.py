"""
complacency.py
--------------
A complacency gauge for the option chain — the measured replacement for the
driver-label guess that currently sets vol_state in market_view.

Complacency = the market pricing little fear while sellers confidently
underwrite downside. High complacency means premium is thin and the tape is
crowded short-vol -> a poor time to SELL premium and a setup where a shock bites.

Inputs (all already in your chain / feed):
  * ATM IV and (optional) IV percentile/rank over a lookback
  * put-writer OI: absolute put OI and its day change near/at ATM
  * skew (from rnd.py) — a flat/compressing skew is itself complacent
  * (optional) India VIX level + change

Output: a 0–100 score + components + a label, and a vol_state hint
(range / expansion) that market_view can consume instead of guessing. Also a
PCR×VIX interaction read (`pcr_vix`) — a CONTRARIAN vol-regime overlay, NOT a
directional momentum vote: it carries a small advisory `reversal_hint` and a
`tail_risk` flag, and refines `vol_state_hint` at the interaction extremes. See
`pcr_vix_quadrant` below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _clip01(x):
    return max(0.0, min(1.0, x))


# ── PCR × VIX interaction bands (PRIOR — India NIFTY context, uncalibrated) ────
# The complacency score below blends PCR and VIX LINEARLY, and a linear blend
# structurally cannot represent "PCR-high AND VIX-high together" as its own state
# (the two just sum to a middling number). This classifier adds that missing
# interaction. It is a vol-regime / CONTRARIAN read, never a momentum vote.
# Thresholds ship tagged PRIOR until ≥60 sessions calibrate them.
PCR_HIGH = 1.30   # total put/call OI ratio — puts piling on (fear / protection)
PCR_LOW = 0.70    # calls dominating (greed / thin downside demand)
VIX_HIGH = 16.0   # India VIX — elevated fear
VIX_LOW = 12.0    # India VIX — calm


def pcr_vix_quadrant(
    put_call_oi_ratio: float | None,
    vix: float | None,
    vix_chg_pct: float | None = None,
    pcr_high: float = PCR_HIGH,
    pcr_low: float = PCR_LOW,
    vix_high: float = VIX_HIGH,
    vix_low: float = VIX_LOW,
) -> dict:
    """Classify the PCR×VIX interaction into one of four contrarian vol states.

    This is the SINGLE home for the PCR×VIX interaction (DRY): the complacency
    score consumes it, and any Desk overlay should READ this rather than recompute
    PCR/VIX. It is a vol-regime / contrarian modulator, NOT a directional vote.

    Returns a dict:
      * quadrant      : CAPITULATION | COMPLACENCY | HEDGING | CONFLICTING |
                        NEUTRAL | UNKNOWN
      * vol_state     : "range" | "expansion" | None — refines the complacency
                        vol read; None = quadrant is not decisive on the vol axis
                        (defer to the score).
      * reversal_hint : float in [-0.3, 0.3] — a SMALL contrarian nudge
                        (fade the extreme), non-zero ONLY at the diagonal corners.
                        Advisory; never a momentum vote; sign is contrarian.
      * tail_risk     : True when the chain prices little fear into a one-sided
                        book (the complacency / pin corner) — arm tail-hedge and
                        block naked premium-selling.
      * reading       : one-line human explanation.
      * tag           : always "PRIOR" (thresholds uncalibrated).
    """
    if vix is None or put_call_oi_ratio is None:
        return {
            "quadrant": "UNKNOWN", "vol_state": None, "reversal_hint": 0.0,
            "tail_risk": False, "tag": "PRIOR",
            "pcr": put_call_oi_ratio, "vix": vix,
            "reading": "PCR or VIX missing — interaction not evaluated.",
        }

    pcr_hi = put_call_oi_ratio >= pcr_high
    pcr_lo = put_call_oi_ratio <= pcr_low
    vix_hi = vix >= vix_high
    vix_lo = vix <= vix_low

    if pcr_hi and vix_hi:
        # Puts bid AND fear elevated → washout / capitulation → contrarian bullish.
        quad = {
            "quadrant": "CAPITULATION", "vol_state": "expansion",
            "reversal_hint": 0.25, "tail_risk": False,
            "reading": ("High PCR + high VIX: capitulation / washout — a contrarian "
                        "bullish-reversal tell. Favour long-vol or defined-risk longs "
                        "over fresh shorts."),
        }
    elif pcr_lo and vix_lo:
        # Calls dominate AND no fear priced → complacency / pin & black-swan risk.
        quad = {
            "quadrant": "COMPLACENCY", "vol_state": "range",
            "reversal_hint": -0.20, "tail_risk": True,
            "reading": ("Low PCR + low VIX: complacency — thin downside demand into a "
                        "call-heavy book. Pin / black-swan risk; arm the tail hedge and "
                        "avoid naked premium-selling."),
        }
    elif pcr_hi and vix_lo:
        # Puts bid but VIX calm → protective HEDGING, not fear. The off-diagonal a
        # linear blend loses (high-PCR fear vs low-VIX calm cancel out). Constructive.
        quad = {
            "quadrant": "HEDGING", "vol_state": "range",
            "reversal_hint": 0.0, "tail_risk": False,
            "reading": ("High PCR + low VIX: hedging demand — longs are being protected, "
                        "not panic. Treat as constructive / neutral, NOT complacent."),
        }
    elif pcr_lo and vix_hi:
        # Calls dominate but VIX elevated → conflicting; demand confirmation.
        quad = {
            "quadrant": "CONFLICTING", "vol_state": None,
            "reversal_hint": 0.0, "tail_risk": False,
            "reading": ("Low PCR + high VIX: conflicting positioning vs fear — no clean "
                        "read; require confirmation before acting."),
        }
    else:
        quad = {
            "quadrant": "NEUTRAL", "vol_state": None,
            "reversal_hint": 0.0, "tail_risk": False,
            "reading": "PCR / VIX not at an interaction extreme; no vol-regime override.",
        }

    quad.update({"tag": "PRIOR", "pcr": round(put_call_oi_ratio, 3), "vix": round(vix, 2)})
    return quad


@dataclass
class ChainComplacencyInputs:
    atm_iv: float                      # e.g. 0.093 (9.3%)
    iv_percentile: float | None = None # 0..1 over lookback; None -> derive from level
    put_oi_chg_pct_atm: float = 0.0    # avg % OI change on near-ATM PUTS (fresh writing>0)
    put_call_oi_ratio: float = 1.0     # total put OI / call OI
    skew: float = -0.4                 # rnd skew; ~0 = complacent, very negative = fearful
    vix: float | None = None           # India VIX level (e.g. 12.9)
    vix_chg_pct: float | None = None   # VIX day change %, negative = fear falling


def complacency_score(c: ChainComplacencyInputs) -> dict:
    """0 (max fear) .. 100 (max complacency). Continuous, weighted components."""
    warnings = []

    # Unit audit
    if c.atm_iv > 1.0:
        warnings.append(f"atm_iv={c.atm_iv} > 1.0; expected decimal fraction (e.g. 0.095)")
    if c.iv_percentile is not None and c.iv_percentile > 1.0:
        warnings.append(f"iv_percentile={c.iv_percentile} > 1.0; expected 0.0-1.0 fraction")
    provenance = "FULL"
    if c.put_oi_chg_pct_atm == 0.0:
        warnings.append("put_oi_chg_pct_atm is 0.0; field may be unpopulated or referencing wrong column")
        provenance = "PARTIAL"
    if c.put_call_oi_ratio > 10.0 or c.put_call_oi_ratio < 0.1:
        warnings.append(f"put_call_oi_ratio={c.put_call_oi_ratio}; expected raw ratio (e.g. 0.9)")

    # 1) IV cheapness — low IV / low percentile = complacent
    if c.iv_percentile is not None:
        iv_cheap = 1.0 - _clip01(c.iv_percentile)
    else:
        # fallback: map level vs a rough NIFTY band [9%, 22%] to cheapness
        iv_cheap = _clip01((0.22 - c.atm_iv) / (0.22 - 0.09))

    # 2) Put-writer aggression — fresh put writing near ATM = sellers confident
    #    (put OI building while IV is low is the core complacency tell)
    put_write = _clip01(c.put_oi_chg_pct_atm / 150.0)     # +150% OI -> saturated
    pcr_lean = _clip01((c.put_call_oi_ratio - 0.8) / 0.8) # >0.8 puts piling on

    # 3) Skew compression — a flat skew means downside isn't being bid (complacent);
    #    a very negative skew means hedges ARE bid (fearful)
    skew_flat = _clip01((c.skew + 0.6) / 0.6)             # skew -0.6 -> 0, 0.0 -> 1

    # 4) VIX — low and falling = complacent (optional)
    if c.vix is not None:
        vix_low = _clip01((16.0 - c.vix) / (16.0 - 10.0))
        vix_fall = _clip01((-(c.vix_chg_pct or 0.0)) / 5.0)
        vix_comp = 0.6 * vix_low + 0.4 * vix_fall
        w = {"iv": 0.30, "put": 0.25, "pcr": 0.10, "skew": 0.15, "vix": 0.20}
        score01 = (w["iv"]*iv_cheap + w["put"]*put_write + w["pcr"]*pcr_lean
                   + w["skew"]*skew_flat + w["vix"]*vix_comp)
    else:
        w = {"iv": 0.40, "put": 0.30, "pcr": 0.12, "skew": 0.18}
        score01 = (w["iv"]*iv_cheap + w["put"]*put_write + w["pcr"]*pcr_lean
                   + w["skew"]*skew_flat)
        vix_comp = None

    score = round(score01 * 100, 1)
    if score >= 70:
        label, vol_state = "COMPLACENT", "range"
    elif score >= 45:
        label, vol_state = "NEUTRAL", "range"
    else:
        label, vol_state = "FEARFUL / STRESSED", "expansion"

    # ── PCR × VIX interaction overlay (the missing non-linear read) ──────────────
    # A CONTRARIAN vol-regime modulator, not a momentum vote. It refines the
    # score-derived vol_state ONLY when the quadrant is decisive on the vol axis
    # (CAPITULATION → expansion, COMPLACENCY / HEDGING → range); the off-diagonal
    # CONFLICTING / NEUTRAL states leave the score's read untouched. The quadrant's
    # `reading` also disambiguates HEDGING (protective, constructive) from naive
    # COMPLACENT, which the linear label alone cannot tell apart.
    quad = pcr_vix_quadrant(c.put_call_oi_ratio, c.vix, c.vix_chg_pct)
    vol_state_source = "score"
    if quad["vol_state"] is not None and quad["vol_state"] != vol_state:
        vol_state = quad["vol_state"]
        vol_state_source = "pcr_vix_quadrant"

    return {
        "score": score,
        "label": label,
        "vol_state_hint": vol_state,      # market_view consumes this
        "vol_state_source": vol_state_source,
        "pcr_vix": quad,                  # contrarian vol-regime overlay (PRIOR)
        "reversal_hint": quad["reversal_hint"],  # small contrarian nudge, advisory
        "tail_risk": quad["tail_risk"],   # arm tail-hedge / block naked premium-sell
        "components": {
            "iv": round(iv_cheap, 2),
            "put": round(put_write, 2),
            "pcr": round(pcr_lean, 2),
            "skew": round(skew_flat, 2),
            "vix": round(vix_comp, 2) if vix_comp is not None else 0.0,
        },
        "weights": w,
        "warnings": warnings,
        "provenance": provenance,
        "reading": _reading(score, iv_cheap, put_write),
    }


def _reading(score, iv_cheap, put_write):
    if score >= 70:
        return ("High complacency: cheap protection + confident put-writing. "
                "Premium is thin and the tape is short-vol — a poor moment to add "
                "premium-selling; a shock would be amplified. Favor long-vol or "
                "stand aside.")
    if score < 45:
        return ("Elevated fear: IV bid and/or hedges in demand. Premium is rich — "
                "premium-selling is better compensated, but realised moves are larger.")
    return "Neutral: neither complacent nor stressed; no strong vol-axis tilt."


if __name__ == "__main__":
    import json

    # the calm relief-rally chain (spot ~24050): low IV ~9.3%, fresh put-writing
    # at/above ATM, mildly flat-ish skew, VIX ~12.9 falling.
    calm = ChainComplacencyInputs(
        atm_iv=0.093, iv_percentile=0.18,
        put_oi_chg_pct_atm=130.0, put_call_oi_ratio=0.92,
        skew=-0.29, vix=12.9, vix_chg_pct=-3.4)
    print("CALM relief-rally chain:")
    print(json.dumps(complacency_score(calm), indent=2))

    # a stressed chain for contrast: high IV, put-writers fleeing, steep skew
    stressed = ChainComplacencyInputs(
        atm_iv=0.19, iv_percentile=0.82,
        put_oi_chg_pct_atm=-40.0, put_call_oi_ratio=1.5,
        skew=-0.9, vix=21.0, vix_chg_pct=18.0)
    print("\nSTRESSED chain:")
    print(json.dumps(complacency_score(stressed), indent=2))

    # ── PCR×VIX interaction corners (each returns a distinct quadrant) ──
    print("\nPCR×VIX quadrants:")
    for name, pcr, vix, chg in [
        ("capitulation", 1.6, 19.0, 15.0),
        ("complacency", 0.6, 11.0, -2.0),
        ("hedging", 1.5, 11.5, -1.0),
        ("conflicting", 0.6, 18.0, 10.0),
        ("neutral", 1.0, 14.0, 0.0),
    ]:
        q = pcr_vix_quadrant(pcr, vix, chg)
        print(f"  {name:13s} -> {q['quadrant']:12s} "
              f"vol={str(q['vol_state']):9s} rev={q['reversal_hint']:+.2f} tail={q['tail_risk']}")
