"""
strategy_framework/signals/vol_index.py
=======================================
Constituent volume + index-weighted momentum — "where the (heavyweight) money moves".

NIFTY is free-float market-cap weighted, so a move in a 10%-weight heavyweight swings
the index far more than the same move in a 0.4% name. So to estimate the INDEX
direction from constituents you must weight by index weight, not treat all 50 equally.

This signal reports three constituent-return aggregates over a short window, all
as-of `now` (backward):

    index-weighted   iw_ret = Σ(wᵢ·retᵢ) / Σ wᵢ            (reconstructs the cap-weighted move)
    volume-weighted  vw_ret = Σ(volᵢ·retᵢ) / Σ volᵢ        (where the volume is, index-blind)
    weight×volume    wv_ret = Σ(wᵢ·volᵢ·retᵢ) / Σ(wᵢ·volᵢ)  (heavyweights trading heavily) ← score

The score uses `wv_ret` (heavyweights *and* volume). The divergence between the
weighted and unweighted views tells you when volume disagrees with the cap-weighted
index. + = bullish NIFTY. NO_DATA-safe when constituent bars are absent.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, squash, clamp
from ..config import constituents as K


def compute(da, now: str, ctx: dict, lookback: int | None = None) -> Signal:
    # Shared momentum window from ctx; scale = base×√(n/ref). `base_scale` is set so
    # that at the historical 30-bar window this reproduces the old scale of 0.15.
    mom = ctx.get("momentum")
    lookback = int(lookback if lookback is not None else (ctx.get("lookback_bars") or 30))
    scale = (mom.scale_for("vol_index", lookback) if mom
             else 0.15 * (lookback / 30.0) ** 0.5)

    # NOTE: this signal does NOT use signals/index_volume.per_bar_index_volume —
    # it weights each constituent's RETURN by weight×volume, so it needs per-stock
    # volume, not a summed index-volume series. Different computation, not a dup.
    syms = sorted((set(da.available_symbols("1m")) & set(K.symbols())) - {"NIFTY"})
    rets, vols, wts = [], [], []
    for s in syms:
        b = da.bars(s, "1m", end=now, limit=lookback + 2)
        if len(b) < 3:
            continue
        c0, c1 = b[0]["close"], b[-1]["close"]
        if not c0:
            continue
        rets.append((c1 / c0 - 1.0) * 100.0)
        vols.append(float(np.mean([bar["volume"] or 0.0 for bar in b])))
        wts.append(K.weight_of(s))
    if len(rets) < 3:
        return Signal.no_data("vol_index", "not enough constituent bars for a weighted read")

    r = np.array(rets, float); vol = np.array(vols, float); w = np.array(wts, float)
    vol_ok = vol.sum() > 0
    w_ok = w.sum() > 0

    def _wavg(weight):
        s = weight.sum()
        return float((r * weight).sum() / s) if s > 0 else float(r.mean())

    iw_ret = _wavg(w) if w_ok else float(r.mean())                 # cap-weighted index move
    vw_ret = _wavg(vol) if vol_ok else float(r.mean())             # volume only (index-blind)
    wv = w * vol
    wv_ret = _wavg(wv) if wv.sum() > 0 else (iw_ret if w_ok else vw_ret)   # weight×volume ← score

    score = squash(wv_ret, scale=scale)
    n = len(rets)
    confidence = clamp((0.35 + min(0.30, abs(wv_ret) * 0.8))
                       * (1.0 if (vol_ok and w_ok) else 0.6)
                       * (0.5 + 0.5 * min(1.0, n / 25.0)), 0.0, 0.85)

    return Signal("vol_index", score, confidence, "PRIOR",
                  detail={"wv_return_pct": round(wv_ret, 3),        # index-weight × volume (score)
                          "index_weighted_return_pct": round(iw_ret, 3),
                          "volume_weighted_return_pct": round(vw_ret, 3),   # unweighted by index
                          "weight_vs_volume_divergence_pct": round(wv_ret - vw_ret, 3),
                          "vol_weighted": bool(vol_ok), "index_weighted": bool(w_ok), "n_constituents": int(n),
                          "convention": "index-weight × volume up = bullish"})
