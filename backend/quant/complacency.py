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
(range / expansion) that market_view can consume instead of guessing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _clip01(x):
    return max(0.0, min(1.0, x))


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
    if c.put_oi_chg_pct_atm == 0.0:
        warnings.append("put_oi_chg_pct_atm is 0.0; field may be unpopulated or referencing wrong column")
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

    return {
        "score": score,
        "label": label,
        "vol_state_hint": vol_state,      # market_view consumes this
        "components": {
            "iv_cheapness": round(iv_cheap, 2),
            "put_writing": round(put_write, 2),
            "pcr_lean": round(pcr_lean, 2),
            "skew_flatness": round(skew_flat, 2),
            "vix_complacency": round(vix_comp, 2) if vix_comp is not None else None,
        },
        "warnings": warnings,
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
    # the calm relief-rally chain (spot ~24050): low IV ~9.3%, fresh put-writing
    # at/above ATM, mildly flat-ish skew, VIX ~12.9 falling.
    calm = ChainComplacencyInputs(
        atm_iv=0.093, iv_percentile=0.18,
        put_oi_chg_pct_atm=130.0, put_call_oi_ratio=0.92,
        skew=-0.29, vix=12.9, vix_chg_pct=-3.4)
    import json
    print("CALM relief-rally chain:")
    print(json.dumps(complacency_score(calm), indent=2))

    # a stressed chain for contrast: high IV, put-writers fleeing, steep skew
    stressed = ChainComplacencyInputs(
        atm_iv=0.19, iv_percentile=0.82,
        put_oi_chg_pct_atm=-40.0, put_call_oi_ratio=1.5,
        skew=-0.9, vix=21.0, vix_chg_pct=18.0)
    print("\nSTRESSED chain:")
    print(json.dumps(complacency_score(stressed), indent=2))
