"""
strategy_framework/signals/crude_energy.py
==========================================
Crude / energy macro tilt for NIFTY.

India imports the bulk of its crude, so a crude spike is a terms-of-trade and
inflation shock: **rising crude => bearish NIFTY**, falling crude => bullish.
This is the signal that should have seen the 8-Jul-2026 oil-shock leg (crude
+5–8% on the US–Iran headline) coming through into the index over the following
hours.

Reads the `CRUDEOIL` 1-minute series from `price_bars` (the same table NIFTY and
the constituents live in — no new table needed; the commodity sync writes it
there). Everything is a backward as-of read on bars with ts <= now (D-MA-01).

Sign convention matches the rest of the framework: score in [-1, +1], + = bullish
NIFTY. Confidence rises with move magnitude and the number of corroborating
horizons; the signal returns NO_DATA cleanly when the CRUDEOIL series is absent
(e.g. a DB copy without the commodity sync), so it never disturbs the blend.
"""
from __future__ import annotations
from .base import Signal, squash, clamp

SYMBOL = "CRUDEOIL"


def _ret_pct(da, now: str, lookback: int):
    """% change of the symbol's close over the last `lookback` 1m bars, as-of now."""
    bars = da.bars(SYMBOL, "1m", end=now, limit=lookback + 2)
    if len(bars) < 3:
        return None
    close = [b["close"] for b in bars if b["close"]]
    if len(close) < 3 or close[0] == 0:
        return None
    return (close[-1] / close[0] - 1.0) * 100.0


def compute(da, now: str, ctx: dict) -> Signal:
    r30 = _ret_pct(da, now, 30)          # ~30-minute thrust
    rday = _ret_pct(da, now, 375)        # session / overnight carry (~1 trading day)
    if r30 is None and rday is None:
        return Signal.no_data("crude_energy", f"no {SYMBOL} bars as-of now")

    parts, detail = [], {}
    if r30 is not None:
        parts.append((squash(r30, scale=0.8), 0.6)); detail["crude_ret_30m_pct"] = round(r30, 3)
    if rday is not None:
        parts.append((squash(rday, scale=1.5), 0.4)); detail["crude_ret_day_pct"] = round(rday, 3)

    w = sum(p[1] for p in parts)
    raw = sum(s * wt for s, wt in parts) / w
    score = clamp(-raw)                  # crude UP -> score DOWN (bearish NIFTY)

    mag = max(abs(r30 or 0.0), abs(rday or 0.0))
    confidence = clamp(0.30 + 0.12 * len(parts) + min(0.28, mag * 0.06), 0.0, 0.85)

    return Signal("crude_energy", score, confidence, "PRIOR",
                  detail={**detail, "convention": "crude up = bearish NIFTY",
                          "n_streams": len(parts)})
