"""
strategy_framework/signals/index_volume.py
==========================================
THE single source of truth for reconstructing the NIFTY index's per-minute
traded volume from its constituents.

The index itself carries NO volume (its price_bars rows have volume 0), so any
participation-based read must reconstruct it. NIFTY is free-float cap-weighted, so
a heavyweight's volume must count more:

    index_volumeₜ = Σ_constituents ( index_weightᵢ × volumeᵢ,ₜ )

Every signal that needs per-minute index volume (technical_momentum, vwap,
rel_volume) imports `per_bar_index_volume` from here instead of rolling its own
constituent loop — see HARD RULE 9 in SKILL.md (single source of truth).

Note: vol_index does NOT use this — it weights per-constituent *returns* by
weight×volume (needs each stock's volume separately), which is a different
computation than a summed index-volume series.
"""
from __future__ import annotations
import numpy as np
from ..config import constituents as K


def per_bar_index_volume(da, ts_list: list[str]):
    """Per-minute index volume aligned to `ts_list` (the NIFTY 1m bar timestamps),
    estimated from constituent 1m volume.

    Returns (weighted, unweighted, n_used):
      * weighted   – Σ(index_weightᵢ × volumeᵢ,ₜ)  (cap-weighted; the usual one)
      * unweighted – Σ(volumeᵢ,ₜ)                   (equal-volume; for comparison)
      * n_used     – how many constituents contributed any volume

    Absolute scale is arbitrary (weight units); callers use it only as a ratio or
    to weight prices, both scale-invariant.
    """
    if not ts_list:
        return np.zeros(0), np.zeros(0), 0
    start, end = ts_list[0], ts_list[-1]
    want = set(ts_list)
    vw = {t: 0.0 for t in ts_list}
    vu = {t: 0.0 for t in ts_list}
    syms = sorted((set(da.available_symbols("1m")) & set(K.symbols())) - {"NIFTY"})
    used = 0
    for s in syms:
        w = K.weight_of(s)
        bars = da.bars(s, "1m", end=end, start=start, limit=len(ts_list) + 5)
        if not bars:
            continue
        got = False
        for bar in bars:
            t = bar.get("ts")
            if t in want:
                vol = bar.get("volume") or 0.0
                if vol > 0:
                    vw[t] += w * vol
                    vu[t] += vol
                    got = True
        used += 1 if got else 0
    return (np.array([vw[t] for t in ts_list], float),
            np.array([vu[t] for t in ts_list], float), used)
