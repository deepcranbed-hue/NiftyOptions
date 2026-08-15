"""
strategy_framework/signals/technical_momentum.py
================================================
Intraday price + volume momentum on the NIFTY index itself.

Directional core signal. Blends three views of the 1-minute tape, all computed
strictly on bars with ts <= now (no lookahead):

  * Trend       : sign & slope of short EMA vs long EMA (normalised by ATR).
  * Thrust      : cumulative return over a lookback window vs its own vol.
  * Volume build: is the recent move happening on rising volume (participation)
                  or fading volume (exhaustion)? Amplifies / damps confidence.

Score is + when price is trending up on participation, - when trending down.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, squash, clamp


def _ema(x: np.ndarray, span: int) -> float:
    if len(x) == 0:
        return float("nan")
    a = 2.0 / (span + 1.0)
    e = x[0]
    for v in x[1:]:
        e = a * v + (1 - a) * e
    return float(e)


def compute(da, now: str, ctx: dict,
            fast: int = 9, slow: int = 21, thrust_lookback: int = 30) -> Signal:
    bars = da.bars("NIFTY", "1m", end=now, limit=max(slow * 4, 120))
    if len(bars) < slow + 5:
        return Signal("technical_momentum", 0.0, 0.15, "PRIOR",
                      status="INSUFFICIENT_HISTORY",
                      detail={"n_bars": len(bars), "need": slow + 5})

    close = np.array([b["close"] for b in bars], float)
    vol = np.array([b["volume"] or 0.0 for b in bars], float)
    high = np.array([b["high"] for b in bars], float)
    low = np.array([b["low"] for b in bars], float)

    # The NIFTY index bar carries no volume of its own. If so, ESTIMATE per-minute
    # index volume from constituents (index-weighted), aligned to these bars, so the
    # volume-build / participation arm below is live instead of silently inert.
    vol_source = "nifty_bar"
    if vol.sum() <= 0:
        try:
            from .index_volume import per_bar_index_volume
            est, _uw, n_used = per_bar_index_volume(da, [b["ts"] for b in bars])
            if est.sum() > 0:
                vol = est
                vol_source = f"constituents({n_used})"
        except Exception:
            pass                                    # keep zeros; participation → neutral

    # --- trend: EMA gap normalised by ATR (a unit-free momentum) -----------
    ema_fast, ema_slow = _ema(close[-slow * 3:], fast), _ema(close[-slow * 3:], slow)
    tr = np.maximum(high - low, np.abs(np.diff(np.concatenate([[close[0]], close]))))
    atr = float(np.mean(tr[-slow:])) or 1e-6
    trend_z = (ema_fast - ema_slow) / atr
    trend = squash(trend_z, scale=1.0)

    # --- thrust: windowed return vs realized 1m vol ------------------------
    n = min(thrust_lookback, len(close) - 1)
    rets = np.diff(np.log(close[-n - 1:]))
    thrust_z = (rets.sum()) / (rets.std() * np.sqrt(n) + 1e-9)
    thrust = squash(thrust_z, scale=1.5)

    # --- volume build: recent vs baseline participation --------------------
    if vol.sum() > 0 and len(vol) >= 2 * n:
        recent_v = vol[-n:].mean()
        base_v = vol[-2 * n:-n].mean() + 1e-9
        vol_ratio = recent_v / base_v
    else:
        vol_ratio = 1.0
    # rising volume (>1) corroborates the move -> boosts confidence & slightly
    # the score magnitude; falling volume (<1) signals exhaustion -> damps.
    participation = clamp((vol_ratio - 1.0), -0.5, 0.5)

    raw = 0.6 * trend + 0.4 * thrust
    score = clamp(raw * (1.0 + 0.4 * participation))

    # confidence: agreement between trend & thrust, scaled by data sufficiency
    agree = 1.0 - abs(trend - thrust) / 2.0
    data_suff = min(1.0, len(bars) / (slow * 4))
    vol_conf = 0.5 + 0.5 * clamp(participation * 2, -1, 1) if vol.sum() > 0 else 0.5
    confidence = clamp(0.5 * agree + 0.3 * data_suff + 0.2 * vol_conf, 0.0, 1.0)

    return Signal("technical_momentum", score, confidence, "PRIOR",
                  detail={"trend_z": round(trend_z, 3), "thrust_z": round(thrust_z, 3),
                          "vol_ratio": round(vol_ratio, 3), "vol_source": vol_source,
                          "ema_fast": round(ema_fast, 1), "ema_slow": round(ema_slow, 1),
                          "atr_1m": round(atr, 2), "n_bars": len(bars)})
