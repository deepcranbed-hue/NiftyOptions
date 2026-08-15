"""
strategy_framework/signals/adx.py
=================================
ADX / DMI — trend STRENGTH and direction (Wilder). Fills the "is the trend
structured?" gap: technical_momentum says price is moving, ADX says whether that
move is a real directional trend or noise.

  +DI vs −DI  → direction (which way the directional movement dominates)
  ADX         → strength of the trend, regardless of direction

Score = (+DI − −DI)/(+DI + −DI) ∈ [−1,1] (direction). Confidence scales with ADX
(a trend read you don't trust in a rangebound tape). New MATH on price (directional
movement + ATR), distinct from EMA momentum and from Kaufman ER — passes the
'is the maths new?' test. Weight-0 studied candidate.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, clamp

_N = 14


def _wilder_sum(x, n):
    s = np.zeros(len(x))
    if len(x) < n:
        return s
    s[n - 1] = x[:n].sum()
    for i in range(n, len(x)):
        s[i] = s[i - 1] - s[i - 1] / n + x[i]
    return s


def _wilder_avg(x, n):
    a = np.zeros(len(x))
    if len(x) < n:
        return a
    a[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        a[i] = (a[i - 1] * (n - 1) + x[i]) / n
    return a


def compute(da, now: str, ctx: dict, n: int = _N) -> Signal:
    bars = da.bars("NIFTY", "1m", end=now, limit=3 * n + 10)
    if len(bars) < 2 * n + 2:
        return Signal("adx", 0.0, 0.15, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"n_bars": len(bars)})
    h = np.array([b["high"] for b in bars], float)
    l = np.array([b["low"] for b in bars], float)
    c = np.array([b["close"] for b in bars], float)
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    atr = _wilder_sum(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * _wilder_sum(plus_dm, n) / np.where(atr == 0, np.nan, atr)
        mdi = 100.0 * _wilder_sum(minus_dm, n) / np.where(atr == 0, np.nan, atr)
        dx = 100.0 * np.abs(pdi - mdi) / np.where((pdi + mdi) == 0, np.nan, pdi + mdi)
    adx = _wilder_avg(np.nan_to_num(dx), n)
    pdi_l = float(np.nan_to_num(pdi[-1])); mdi_l = float(np.nan_to_num(mdi[-1]))
    adx_l = float(adx[-1])
    direction = (pdi_l - mdi_l) / (pdi_l + mdi_l) if (pdi_l + mdi_l) > 0 else 0.0
    score = clamp(float(direction))
    confidence = clamp(0.25 + 0.55 * min(1.0, adx_l / 40.0), 0.20, 0.85)
    return Signal("adx", score, confidence, "PRIOR",
                  detail={"plus_di": round(pdi_l, 1), "minus_di": round(mdi_l, 1),
                          "adx": round(adx_l, 1), "n_bars": int(len(bars)),
                          "read": "strong trend" if adx_l > 25 else
                          "developing" if adx_l > 18 else "no trend / range"})
