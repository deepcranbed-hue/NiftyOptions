"""
strategy_framework/signals/global_gap.py
========================================
Overnight-gap / global risk-off tilt — the only forward-looking signal across
sessions.

The 8-Jul-2026 move happened largely **overnight**: the cash index gapped at the
open. Every other signal here reads the intraday tape and therefore cannot see a
gap forming. This one reads **GIFT Nifty** (the NSE-IX contract that trades while
NSE is closed) versus the last NIFTY spot: GIFT trading above spot implies a
higher open (bullish next session), below implies a lower open (bearish).

    gift_premium_pct = (GIFTNIFTY_last - NIFTY_spot) / NIFTY_spot * 100

Reads the `GIFTNIFTY` 1-minute series from `price_bars` (backward as-of, D-MA-01)
and the spot from the option-chain snapshot in `ctx`. Score in [-1, +1], + =
bullish. Confidence is highest pre-open / early in the session when the gap is
still information, and decays through the day once the gap is already priced into
the cash index. Returns NO_DATA cleanly when GIFTNIFTY bars are absent.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from .base import Signal, squash, clamp

SYMBOL = "GIFTNIFTY"
_IST = timezone(timedelta(hours=5, minutes=30))


def _minutes_since_open(now: str) -> float:
    """Minutes since 09:15 IST for the session of `now` (negative if pre-open)."""
    try:
        d = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(_IST)
    except Exception:
        return 0.0
    open_ist = d.replace(hour=9, minute=15, second=0, microsecond=0)
    return (d - open_ist).total_seconds() / 60.0


def _ret_pct(da, now: str, lookback: int):
    bars = da.bars(SYMBOL, "1m", end=now, limit=lookback + 2)
    if len(bars) < 3:
        return None
    close = [b["close"] for b in bars if b["close"]]
    if len(close) < 3 or close[0] == 0:
        return None
    return (close[-1] / close[0] - 1.0) * 100.0


def compute(da, now: str, ctx: dict) -> Signal:
    bars = da.bars(SYMBOL, "1m", end=now, limit=5)
    chain = ctx.get("chain")
    spot = chain.spot if chain else ctx.get("spot")
    if not bars or not spot:
        return Signal.no_data("global_gap", f"no {SYMBOL} bars or spot as-of now")
    gift = next((b["close"] for b in reversed(bars) if b["close"]), None)
    if not gift:
        return Signal.no_data("global_gap", f"no {SYMBOL} price as-of now")

    premium_pct = (gift - spot) / spot * 100.0
    parts = [(squash(premium_pct, scale=0.3), 0.7)]
    detail = {"gift_last": round(gift, 1), "spot": round(spot, 1),
              "gift_premium_pct": round(premium_pct, 3)}

    gret = _ret_pct(da, now, 30)         # GIFT's own recent drift corroborates
    if gret is not None:
        parts.append((squash(gret, scale=0.6), 0.3)); detail["gift_ret_30m_pct"] = round(gret, 3)

    w = sum(p[1] for p in parts)
    score = clamp(sum(s * wt for s, wt in parts) / w)   # GIFT above spot => bullish

    # Confidence decays through the session: a gap is information pre-open and early,
    # noise by midday once it's in the cash index.
    mins = _minutes_since_open(now)
    freshness = clamp(0.75 - 0.0013 * max(mins, 0.0), 0.30, 0.80)
    mag = abs(premium_pct)
    confidence = clamp(freshness * (0.7 + min(0.3, mag * 0.4)), 0.0, 0.85)

    return Signal("global_gap", score, confidence, "PRIOR",
                  detail={**detail, "minutes_since_open": round(mins, 1),
                          "convention": "GIFT above spot = bullish next session",
                          "n_streams": len(parts)})
