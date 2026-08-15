"""
strategy_framework/signals/time_of_day.py
=========================================
Intraday session-phase modulator (IST).

Empirically the NIFTY tape is not stationary across the day:
  * OPENING DRIVE  09:15-09:45 : overnight gaps + order imbalance -> the day's
                                 largest directional bursts. Momentum is more
                                 trustworthy here -> amplify confidence.
  * MIDDAY         09:45-14:45 : chop / mean-reversion -> discount raw momentum,
                                 favour range structures.
  * POWER HOUR     14:45-15:30 : positioning into the close -> momentum returns.
  * EXPIRY CLOSE   expiry day, 14:45-15:30 : gamma/pin dynamics -> very large,
                                 fast moves AND pin risk. Flag it so the strategy
                                 constructor avoids short-gamma condors/butterflies
                                 into the print and prefers defined-risk / directional.

This is NOT a directional signal (score stays 0). It returns, in `detail`, a
`phase`, a `momentum_multiplier` (applied to momentum-signal confidence by the
combiner), an `expected_move_mult`, and boolean flags the regime classifier and
constructor read.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from .base import Signal

IST = timezone(timedelta(hours=5, minutes=30))


def _phase(minutes_since_open: int, dte_days: float):
    """minutes_since_open: minutes past 09:15 IST (0..375)."""
    is_expiry_day = dte_days <= 0.30          # <~7.2h to expiry => expiry session
    if minutes_since_open < 0:
        return "PRE_OPEN", 0.6, 0.8, False
    if minutes_since_open <= 30:
        return "OPENING_DRIVE", 1.30, 1.35, False
    if minutes_since_open >= 330:             # 14:45 onward
        if is_expiry_day:
            return "EXPIRY_CLOSE", 1.35, 1.50, True
        return "POWER_HOUR", 1.20, 1.20, False
    if minutes_since_open >= 300:             # 14:15-14:45 ramp
        return "PRE_POWER", 1.05, 1.05, False
    return "MIDDAY", 0.80, 0.85, False


def compute(da, now: str, ctx: dict) -> Signal:
    dt = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(IST)
    open_ist = dt.replace(hour=9, minute=15, second=0, microsecond=0)
    minutes = int((dt - open_ist).total_seconds() // 60)
    dte = ctx.get("dte_days", 1.0)

    phase, mom_mult, em_mult, pin_risk = _phase(minutes, dte)
    in_session = 0 <= minutes <= 375

    return Signal("time_of_day", 0.0, 0.0, "PRIOR",
                  status="OK" if in_session else "OUT_OF_SESSION",
                  detail={"phase": phase, "ist_time": dt.strftime("%H:%M"),
                          "minutes_since_open": minutes,
                          "momentum_multiplier": mom_mult,
                          "expected_move_mult": em_mult,
                          "pin_risk": pin_risk,
                          "expiry_day": dte <= 0.30,
                          "in_session": in_session})
