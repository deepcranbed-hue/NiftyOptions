"""
strategy_framework/signals/usdinr.py
====================================
Rupee / forex-flow macro tilt for NIFTY.

USDINR is the cleanest fast proxy for foreign-flow direction and risk appetite:
**rupee weakness (USDINR up) => risk-off / FII outflow pressure => bearish NIFTY**;
rupee strength => inflow => bullish. On 8-Jul-2026 the rupee slid to ~95.55 as
oil spiked — exactly the kind of move this should read.

Reads the `USDINR` 1-minute series from `price_bars` (backward as-of, D-MA-01).
Note this **overlaps with `global_momentum`** (which already folds USDINR in) and
with `crude_energy` in a risk-off regime — check the Correlation view before
combining; treat them as one macro factor, not independent bets.

Score in [-1, +1], + = bullish NIFTY. Returns NO_DATA cleanly when the USDINR
series is absent, so a DB without the FX sync is unaffected.
"""
from __future__ import annotations
from .base import Signal, squash, clamp

SYMBOL = "USDINR"


def _ret_pct(da, now: str, lookback: int):
    bars = da.bars(SYMBOL, "1m", end=now, limit=lookback + 2)
    if len(bars) < 3:
        return None
    close = [b["close"] for b in bars if b["close"]]
    if len(close) < 3 or close[0] == 0:
        return None
    return (close[-1] / close[0] - 1.0) * 100.0


def compute(da, now: str, ctx: dict) -> Signal:
    # FX moves are an order of magnitude smaller than equities/commodities, so the
    # squash scales are tighter: a 0.3% intraday USDINR move is already large.
    r30 = _ret_pct(da, now, 30)
    rday = _ret_pct(da, now, 375)
    if r30 is None and rday is None:
        return Signal.no_data("usdinr", f"no {SYMBOL} bars as-of now")

    parts, detail = [], {}
    if r30 is not None:
        parts.append((squash(r30, scale=0.3), 0.6)); detail["usdinr_ret_30m_pct"] = round(r30, 3)
    if rday is not None:
        parts.append((squash(rday, scale=0.6), 0.4)); detail["usdinr_ret_day_pct"] = round(rday, 3)

    w = sum(p[1] for p in parts)
    raw = sum(s * wt for s, wt in parts) / w
    score = clamp(-raw)                  # USDINR UP (rupee weak) -> bearish NIFTY

    mag = max(abs(r30 or 0.0), abs(rday or 0.0))
    confidence = clamp(0.30 + 0.12 * len(parts) + min(0.28, mag * 0.5), 0.0, 0.85)

    return Signal("usdinr", score, confidence, "PRIOR",
                  detail={**detail, "convention": "USDINR up (rupee weak) = bearish NIFTY",
                          "n_streams": len(parts)})
