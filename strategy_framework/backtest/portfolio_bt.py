"""
strategy_framework/backtest/portfolio_bt.py
==========================================
Backtest the *assembled book* — mark the exact positions the user added
(strategies + futures + stocks) forward through every stored snapshot and trace
the combined P&L. This is the "book" mode; the "auto" mode is the suggestion
walk-forward in walkforward.py. The API exposes both behind one toggle.

No lookahead: each mark uses the pricing context as-of that snapshot's timestamp.
Entry prices are the book's own (fixed when the position was opened), so this
shows how today's book would have travelled across the window.
"""
from __future__ import annotations
import numpy as np

from ..signals.data_access import DataAccess
from ..portfolio import valuation, context


def run_book_backtest(cfg, positions: list, expiry: str,
                      start: str | None = None, end: str | None = None,
                      max_marks: int = 120, freq_minutes: float | None = None) -> dict:
    from .walkforward import _cadence_min
    da = DataAccess(cfg.db_path)
    caps = da.list_captures(expiry=expiry, start=start, end=end)
    if not positions:
        return {"series": [], "metrics": {"note": "empty book"}}
    if len(caps) < 2:
        return {"series": [], "metrics": {"note": "need >=2 captures"}}

    if freq_minutes:
        stride = max(1, round(freq_minutes / _cadence_min(caps)))
    else:
        stride = max(1, len(caps) // max(1, max_marks))
    series = []
    for ci, cap in enumerate(caps):
        if ci % stride != 0:
            continue
        ts = cap["captured_at"]
        ctx = context.build_context(cfg.db_path, expiry, as_of=ts)
        if ctx["spot"] is None:
            continue
        val = valuation.value_book(positions, ctx["chain"], ctx["symbol_prices"],
                                   ctx["spot"])
        series.append({"ts": ts, "spot": ctx["spot"],
                       "pnl_rupees": val["total_pnl_rupees"],
                       "net_delta": val["net_delta_rupees_per_point"]})

    if not series:
        return {"series": [], "metrics": {"note": "no markable snapshots"}}

    pnl = np.array([s["pnl_rupees"] for s in series], float)
    peak = np.maximum.accumulate(pnl)
    dd = pnl - peak
    return {"series": series,
            "metrics": {"n_marks": len(series),
                        "start_pnl": round(float(pnl[0]), 0),
                        "final_pnl": round(float(pnl[-1]), 0),
                        "best_pnl": round(float(pnl.max()), 0),
                        "worst_pnl": round(float(pnl.min()), 0),
                        "max_drawdown_rupees": round(float(dd.min()), 0),
                        "note": "book marked forward, no-lookahead; "
                                "descriptive only on thin history (D-MA-04)"}}
