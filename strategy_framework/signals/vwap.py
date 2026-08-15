"""
strategy_framework/signals/vwap.py
==================================
Session VWAP position — a classic intraday directional read.

VWAP (volume-weighted average price) is the volume-weighted mean price *since the
session open* — the reference institutions trade around. Where spot sits relative
to it reads intraday control:

  * spot ABOVE session VWAP  => buyers in control  => bullish
  * spot BELOW session VWAP  => sellers in control  => bearish

The NIFTY *index* carries no volume, so the per-minute volume used to weight the
VWAP is ESTIMATED from the constituents, **index-weighted** (Σ wᵢ·volumeᵢ per minute)
so a heavyweight's volume counts more — anything else would misweight the index.
Both the index-weighted VWAP (used for the score) and an unweighted (equal-volume)
VWAP are reported so you can compare. If no constituent volume is available it falls
back to a time-weighted average (TWAP) at lower confidence.

Two components, both as-of `now`, anchored to the CURRENT session (VWAP resets at
09:15 IST): position (spot vs VWAP) and slope (is VWAP itself rising or falling).
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import numpy as np
from .base import Signal, squash, clamp

_IST = timezone(timedelta(hours=5, minutes=30))


def _session_start_z(now: str):
    try:
        d = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(_IST)
    except Exception:
        return None
    open_ist = d.replace(hour=9, minute=15, second=0, microsecond=0)
    return open_ist.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute(da, now: str, ctx: dict) -> Signal:
    start = _session_start_z(now)
    bars = da.bars("NIFTY", "1m", end=now, start=start, limit=400)
    if len(bars) < 5:
        return Signal("vwap", 0.0, 0.15, "PRIOR", status="INSUFFICIENT_HISTORY",
                      detail={"n_bars": len(bars), "note": "too few session bars for VWAP"})

    c = np.array([b["close"] for b in bars], float)
    ts_list = [b["ts"] for b in bars]
    chain = ctx.get("chain")
    spot = chain.spot if chain else float(c[-1])

    # index volume reconstructed from constituents — shared single source of truth
    from .index_volume import per_bar_index_volume
    iv, uv, n_used = per_bar_index_volume(da, ts_list)
    vol_ok = iv.sum() > 0
    weight = iv if vol_ok else np.ones_like(c)          # index-weighted volume, else TWAP

    def _vwap_series(wgt):
        cum_pw = np.cumsum(c * wgt); cum_w = np.cumsum(wgt)
        return cum_pw / np.where(cum_w == 0, 1.0, cum_w)

    vwap_series = _vwap_series(weight)
    vwap = float(vwap_series[-1])
    vwap_unw = float(_vwap_series(uv)[-1]) if uv.sum() > 0 else None    # equal-volume VWAP

    dist_pct = (spot - vwap) / vwap * 100.0
    parts = [(squash(dist_pct, scale=0.15), 0.7)]
    detail = {"vwap": round(vwap, 1), "vwap_unweighted": round(vwap_unw, 1) if vwap_unw else None,
              "spot": round(spot, 1), "dist_pct": round(dist_pct, 3),
              "vol_source": f"constituents ({n_used})" if vol_ok else "TWAP (no volume)",
              "n_bars": len(bars)}

    if len(vwap_series) > 16:
        slope_pct = (vwap_series[-1] - vwap_series[-16]) / vwap * 100.0
        parts.append((squash(slope_pct, scale=0.05), 0.3))
        detail["vwap_slope_pct"] = round(float(slope_pct), 4)

    tw = sum(p[1] for p in parts)
    score = clamp(sum(s * wt for s, wt in parts) / tw)

    mag = abs(dist_pct)
    n_ok = min(1.0, len(bars) / 120.0)
    confidence = clamp((0.35 + min(0.35, mag * 0.8)) * (1.0 if vol_ok else 0.6)
                       * (0.55 + 0.45 * n_ok), 0.0, 0.85)

    return Signal("vwap", score, confidence, "PRIOR",
                  detail={**detail, "convention": "spot above session VWAP = bullish"})
