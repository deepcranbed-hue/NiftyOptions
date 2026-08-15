"""
strategy_framework/signals/futures_oi_regime.py
===============================================
Directional signal from FUTURES positioning (price × open interest).

Turns the "who drove the move" read into a buy/sell vote:

    price ↑ + OI ↑  LONG BUILDUP   fresh longs, conviction   → BUY  (ride it)
    price ↓ + OI ↑  SHORT BUILDUP  fresh shorts, conviction  → SELL (ride it)
    price ↑ + OI ↓  SHORT COVERING hollow bounce             → mild SELL (fade)
    price ↓ + OI ↓  LONG UNWINDING hollow drop               → mild BUY  (fade)
    flat OI         CHURN                                     → neutral

The classification RULE is NOT re-implemented here — it comes from the single
source `backend.quant.intraday_oi._label` via `signals/futures_oi.classify_positioning`,
the same engine behind the Macro Shock view. This module only turns the regime into
a (score, confidence) via `futures_oi.regime_score`.

Needs futures 1-minute bars carrying `open_interest` (NIFTY_FUT_1). Returns NO_DATA
when the OI feed is absent — which is why the SignalSpec is `data_ready=False`
(pinned at weight 0) until the feed is in place.
"""
from __future__ import annotations
import numpy as np
from .base import Signal
from . import futures_oi as _oi

SYMBOL = "NIFTY_FUT_1"


def compute(da, now: str, ctx: dict, n: int = 30) -> Signal:
    bars = da.bars(SYMBOL, "1m", end=now, limit=2 * n + 5)
    if not bars:
        return Signal.no_data("futures_oi_regime", f"no {SYMBOL} bars as-of now")
    # open_interest is only present when the DB carries the column AND the feed populated it
    if not any(b.get("open_interest") for b in bars):
        return Signal.no_data("futures_oi_regime",
                              f"{SYMBOL} bars have no open interest — futures OI feed not in place")
    if len(bars) < n + 3:
        return Signal("futures_oi_regime", 0.0, 0.15, "PRIOR",
                      status="INSUFFICIENT_HISTORY", detail={"n_bars": len(bars)})

    c = np.array([b["close"] for b in bars], float)
    oi = np.array([(b.get("open_interest") or np.nan) for b in bars], float)
    # anchor on the nearest non-null OI within the window (open bars can be null)
    j0 = next((k for k in range(len(oi) - n - 1, len(oi)) if not np.isnan(oi[k])), None)
    oi_last = oi[-1] if not np.isnan(oi[-1]) else None
    if j0 is None or oi_last is None or oi[j0] <= 0:
        return Signal.no_data("futures_oi_regime", "insufficient non-null OI in window")

    dp_pct = (c[-1] / c[-n - 1] - 1.0) * 100.0
    doi_pct = (oi_last / oi[j0] - 1.0) * 100.0

    r = _oi.classify_positioning(dp_pct, doi_pct)      # single-source rule
    score, conf = _oi.regime_score(r["kind"])

    return Signal("futures_oi_regime", float(score), float(conf), "PRIOR",
                  detail={"regime": r["regime"], "lean": r["lean"],
                          "conviction": r["conviction"], "read": r["note"],
                          "d_price_pct": round(dp_pct, 3), "d_oi_pct": round(doi_pct, 3),
                          "convention": "buildup = trade WITH the move; covering/unwinding = fade it"})
