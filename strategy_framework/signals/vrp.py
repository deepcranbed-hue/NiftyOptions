"""
strategy_framework/signals/vrp.py
=================================
Variance Risk Premium — is implied vol rich or cheap vs realized?

For a *directional* framework the VRP is mostly a *structure* modulator, not a
direction picker: when option premium is rich (IV >> RV) you'd rather express a
bullish view with a bull-put credit spread (sell rich premium) than by buying a
call; when premium is cheap you can afford long options. So this signal's
`score` is near-neutral by design, and its real payload lives in
`detail["vrp_ratio"]` and `detail["regime"]`, which the strategy constructor
reads to choose debit vs credit structures.

Realized vol comes from NIFTY 1m bars (annualised close-to-close). Implied comes
from ATM option IV if present, else from the captured India VIX. When historical
IV is absent (0.0 in backfill rows) we fall back to VIX so the signal still runs.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, clamp

_ANN = np.sqrt(252 * 375)     # annualisation for 1-minute NIFTY returns


def _realized_vol_1m(da, now: str, lookback: int = 120) -> float | None:
    bars = da.bars("NIFTY", "1m", end=now, limit=lookback + 5)
    if len(bars) < 20:
        return None
    c = np.array([b["close"] for b in bars], float)
    r = np.diff(np.log(c))
    return float(r.std() * _ANN * 100.0)          # annualised %


def _atm_iv(chain) -> float | None:
    if chain is None:
        return None
    atm = chain.atm_strike()
    civ, piv = chain.call_iv.get(atm, 0.0), chain.put_iv.get(atm, 0.0)
    ivs = [x for x in (civ, piv) if x and x > 0]
    if not ivs:
        return None
    iv = float(np.mean(ivs))
    return iv * 100.0 if iv < 3 else iv           # normalise frac vs pct


def compute(da, now: str, ctx: dict) -> Signal:
    chain = ctx.get("chain")
    rv = _realized_vol_1m(da, now)
    iv = _atm_iv(chain)
    vix = chain.vix if (chain and chain.vix) else da.latest_vix(now)
    implied = iv if iv is not None else (vix if vix else None)
    src = "atm_iv" if iv is not None else ("vix" if vix else None)

    if rv is None or implied is None:
        return Signal("vrp", 0.0, 0.2, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"rv": rv, "implied": implied, "note": "need RV and implied"})

    ratio = implied / (rv + 1e-9)                 # >1 rich, <1 cheap (D-MA-03)
    if ratio >= 1.15:
        regime = "RICH"
    elif ratio <= 0.95:
        regime = "CHEAP"
    else:
        regime = "FAIR"

    # Directional score stays small: rich premium slightly favours mean-reversion
    # (fade extremes) but we keep magnitude low so it never dominates direction.
    score = clamp(-0.15 * np.log(ratio), -0.3, 0.3)
    confidence = 0.4 if src == "vix" else 0.55

    return Signal("vrp", float(score), confidence, "PRIOR",
                  detail={"rv_ann_pct": round(rv, 2), "implied_pct": round(implied, 2),
                          "vrp_ratio": round(ratio, 3), "regime": regime,
                          "implied_source": src})
