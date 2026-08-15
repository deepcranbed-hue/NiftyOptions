"""
strategy_framework/portfolio/context.py
=======================================
Build the pricing context used to mark the book.

Preference order for each price (the "live feed with capture fallback" the user
chose): an explicit live override passed in by the caller (e.g. the backend's
Breeze feed) -> the latest stored chain snapshot / bar -> None.

`as_of` lets the backtest reuse the exact same marking logic at a historical
timestamp (backward as-of, no lookahead).
"""
from __future__ import annotations
from ..signals.data_access import DataAccess


def build_context(db_path: str, expiry: str, as_of: str | None = None,
                  live_chain: dict | None = None,
                  live_prices: dict | None = None) -> dict:
    da = DataAccess(db_path)

    # ---- option chain ----------------------------------------------------
    if live_chain:
        chain = live_chain
        spot = live_chain.get("spot")
        ts = live_chain.get("captured_at", as_of)
    else:
        snap = None
        if as_of:
            snap = da.chain_as_of(as_of, expiry)
        else:
            caps = da.list_captures(expiry=expiry)
            if caps:
                snap = da.chain_as_of(caps[-1]["captured_at"], expiry)
        if snap:
            chain = {"call_ltp": snap.call_ltp, "put_ltp": snap.put_ltp,
                     "spot": snap.spot}
            spot = snap.spot; ts = snap.ts
        else:
            chain = None; spot = None; ts = as_of

    # ---- futures / stocks: latest bar close per symbol -------------------
    prices = dict(live_prices or {})
    for sym in da.available_symbols("1m"):
        if sym in prices:
            continue
        bars = da.bars(sym, "1m", end=as_of, limit=1) if as_of else da.bars(sym, "1m", limit=1)
        if bars:
            prices[sym] = bars[-1]["close"]
    if spot is None:
        spot = prices.get("NIFTY")

    return {"chain": chain, "symbol_prices": prices, "spot": spot,
            "as_of": ts, "source": "live" if (live_chain or live_prices) else "capture"}
