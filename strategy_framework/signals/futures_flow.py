"""
strategy_framework/signals/futures_flow.py
===========================================
NIFTY futures own price + REAL-volume momentum.

Why this is NOT a duplicate of technical_momentum / rel_volume:
  * The NIFTY *index* carries no volume, so `rel_volume` / `vol_index` have to
    ESTIMATE participation from the 50 constituents. `NIFTY_FUT_1` has ACTUAL
    traded volume — so this signal gives *true* volume confirmation of an index
    move, not a reconstruction. That's the new information the user spotted.

Logic (mirrors rel_volume, but on the future's own OHLCV):
    recent_ret  = NIFTY_FUT_1 % change over ~n bars           (direction / thrust)
    rel_vol     = mean(fut volume, last n) / mean(fut volume, prior n)  (REAL)
    score       = squash(recent_ret) × participation_boost(rel_vol)

so a futures rally on 1.5× normal futures volume reads strongly bullish; the same
rally on 0.6× volume reads weak (a thin-volume move that tends to fade). Flags a
price/volume divergence (move on shrinking volume) in detail.

Sign convention: score in [-1, +1], + = up on real participation. NO_DATA when the
NIFTY_FUT_1 series is absent (e.g. a DB copy without the NFO sync). PRIOR until
calibrated.
"""
from __future__ import annotations
import numpy as np
from .base import Signal, squash, clamp

SYMBOL = "NIFTY_FUT_1"   # near-month future


def compute(da, now: str, ctx: dict, n: int | None = None) -> Signal:
    # Shared momentum window from ctx; scale = base×√(n/ref). See MomentumWindow.
    mom = ctx.get("momentum")
    n = int(n if n is not None else (ctx.get("lookback_bars") or 15))
    scale = mom.scale_for("futures_flow", n) if mom else 0.12 * (n / 15.0) ** 0.5

    bars = da.bars(SYMBOL, "1m", end=now, limit=2 * n + 5)
    if not bars:
        return Signal.no_data("futures_flow", f"no {SYMBOL} bars as-of now")
    if len(bars) < n + 3:
        return Signal("futures_flow", 0.0, 0.15, "PRIOR",
                      status="INSUFFICIENT_HISTORY", detail={"n_bars": len(bars)})

    c = np.array([b["close"] for b in bars], float)
    v = np.array([(b.get("volume") or 0.0) for b in bars], float)
    recent_ret = (c[-1] / c[-n - 1] - 1.0) * 100.0

    # REAL futures volume: recent window vs prior baseline
    vol_ok = v.sum() > 0
    recent_v = float(v[-n:].mean())
    base_v = float(v[-2 * n:-n].mean()) if len(v) >= 2 * n else float(v[:-n].mean() or recent_v)
    rel_vol = (recent_v / base_v) if (vol_ok and base_v > 0) else 1.0

    # participation boost: 1.0× -> ~1, 2× -> ~1.6, 0.5× -> ~0.7 (same shape as rel_volume)
    boost = clamp(0.4 + 0.6 * rel_vol, 0.3, 1.6)
    score = clamp(squash(recent_ret, scale=scale) * boost)

    # thin-volume move = suspect (price moving while volume shrinks)
    thin_move = vol_ok and abs(recent_ret) > 0.03 and rel_vol < 0.9

    confidence = clamp(
        (0.30 + 0.30 * min(1.0, abs(recent_ret) * 4) + 0.20 * clamp(rel_vol - 1.0, 0.0, 1.0))
        * (1.0 if vol_ok else 0.4),
        0.0, 0.85,
    )
    if thin_move:
        confidence *= 0.7

    return Signal("futures_flow", score, confidence, "PRIOR",
                  detail={"lookback_bars": n, "tanh_scale": round(scale, 4),
                          "fut_recent_ret_pct": round(float(recent_ret), 3),
                          "fut_rel_volume": round(float(rel_vol), 3),
                          "participation_boost": round(float(boost), 3),
                          "vol_source": "NIFTY_FUT_1 REAL traded volume" if vol_ok else "no volume",
                          "thin_volume_move": bool(thin_move),
                          "convention": "futures move on rising REAL volume = conviction; + = up"})
