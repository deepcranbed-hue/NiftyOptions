"""
strategy_framework/signals/breadth_quality.py
==============================================
Breadth QUALITY — % of constituents above their own short trend (EMA20), index-weighted.

Plain advance/decline (breadth_oi) counts up vs down. This asks a better question: how
many of the 50 are actually in an UPtrend, weighted by index weight? It's the Scenario
1 vs 2 distinction made into a signal — NIFTY +0.8% on three heavyweights (narrow) reads
very differently from +0.8% with 45/50 above trend (broad). Broad participation above
trend = a durable move; a narrow one = fragile. New INFORMATION from the constituents
you already capture (not another transform of the index price). Weight-0 candidate;
signal_class='position' (participation breadth).
"""
from __future__ import annotations
import numpy as np
from .base import Signal, clamp
from ..config import constituents as K


def _ema(x, span):
    a = 2.0 / (span + 1.0)
    e = x[0]
    for v in x[1:]:
        e = a * v + (1 - a) * e
    return e


def compute(da, now: str, ctx: dict, span: int = 20) -> Signal:
    syms = sorted((set(da.available_symbols("1m")) & set(K.symbols())) - {"NIFTY"})
    above_w = tot_w = 0.0
    n_above = n = 0
    for sym in syms:
        bars = da.bars(sym, "1m", end=now, limit=span + 6)
        if len(bars) < max(8, span // 2):
            continue
        c = np.array([b["close"] for b in bars], float)
        w = K.weight_of(sym)
        tot_w += w; n += 1
        if c[-1] > _ema(c, span):
            above_w += w; n_above += 1
    if n == 0 or tot_w <= 0:
        return Signal.no_data("breadth_quality", "no constituent bars as-of now")
    pct_w = above_w / tot_w                 # index-weighted % above trend
    pct_eq = n_above / n                     # equal-weighted % above trend
    score = clamp(2.0 * (pct_w - 0.5))       # >50% above trend → bullish participation
    confidence = clamp(0.30 + 0.5 * min(1.0, n / 30.0), 0.30, 0.85)
    return Signal("breadth_quality", float(score), float(confidence), "PRIOR",
                  detail={"pct_above_ema_weighted": round(pct_w * 100, 1),
                          "pct_above_ema_equal": round(pct_eq * 100, 1),
                          "n_constituents": int(n),
                          "narrow_vs_broad": round((pct_w - pct_eq) * 100, 1),
                          "read": "broad participation" if pct_eq > 0.6 else
                          "narrow / heavyweight-led" if pct_w - pct_eq > 0.12 else "mixed"})
