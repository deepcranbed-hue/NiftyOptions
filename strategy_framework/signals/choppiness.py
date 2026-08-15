"""
strategy_framework/signals/choppiness.py
=========================================
Choppiness Index — a REGIME signal: is the tape trending or a random-walk chop?

    CI = 100 · log10( Σ TR(n) / (maxHigh(n) − minLow(n)) ) / log10(n)

High CI (~≥61.8) = lots of back-and-forth inside a range (choppy); low CI (~≤38.2) =
directional travel (trending). Non-directional — it says NOTHING about which way, only
whether direction is worth trusting. Complements Kaufman ER (which you already have)
with a different, range-based construction, so the regime engine gets a second,
independent read of trend-vs-chop. Emits CHOP ∈ [0,1] (1 = maximally choppy).
kind='gate', signal_class='regime'. Weight-0 candidate.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, clamp

_N = 14


def compute(da, now: str, ctx: dict, n: int = _N) -> Signal:
    bars = da.bars("NIFTY", "1m", end=now, limit=n + 5)
    if len(bars) < n + 1:
        return Signal("choppiness", 0.0, 0.15, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"n_bars": len(bars)})
    h = np.array([b["high"] for b in bars[-(n + 1):]], float)
    l = np.array([b["low"] for b in bars[-(n + 1):]], float)
    c = np.array([b["close"] for b in bars[-(n + 1):]], float)
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    rng = float(h[1:].max() - l[1:].min())
    if rng <= 0 or tr.sum() <= 0:
        return Signal.no_data("choppiness", "degenerate range")
    ci = 100.0 * np.log10(tr.sum() / rng) / np.log10(n)
    ci = float(min(100.0, max(0.0, ci)))
    chop = clamp(ci / 100.0)           # 0..1, 1 = maximally choppy
    confidence = clamp(0.3 + 0.4 * abs(ci - 50.0) / 50.0)   # more confident away from the middle
    regime = "choppy / range" if ci >= 61.8 else "trending" if ci <= 38.2 else "transitional"
    return Signal("choppiness", float(chop), float(confidence), "PRIOR",
                  detail={"choppiness_index": round(ci, 1), "chop": round(chop, 3),
                          "regime": regime, "n_bars": int(len(bars)),
                          "note": "regime (trend↔chop), NOT a direction"})
