"""Sector Intelligence view (/api/sector-view/{sector}) — Nifty Bank first; IT / Financials
slot in later by adding an entry to _SECTOR_JSON.

Serves the fundamentals dataset produced by data_agent/fundamentals/bank_view_data.py
(bank_view.json). ?quotes=true refreshes live NSE prices via yfinance and recomputes the
current P/B (= live price / book-value-per-share), 1W/1M/6M returns and the 6M-vs-index
residual; quotes are cached 15 min in .state/ so a refresh never hammers Yahoo. The curated
JSON is read fresh each request, so re-running the extractor shows up immediately.

Per SECTOR_INTELLIGENCE_FRAMEWORK.md §6.7/§6.8: P/B is a regime-conditional THESIS (not a
tradeable signal); the panel decodes P/B -> implied-ROE on the client, on button press only.
Nothing computes on app start.
"""
from __future__ import annotations  # backend runs on py3.9 — keep `X | None` lazy

import json
import os
import time

from fastapi import APIRouter, HTTPException

router = APIRouter()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_DIR = os.path.join(_REPO_ROOT, ".state")
_FUND_DIR = os.path.join(_REPO_ROOT, "data_agent", "fundamentals")

# sector -> (curated json produced by the extractor, yfinance index ticker for relatives)
_SECTOR_JSON = {
    "bank": os.path.join(_FUND_DIR, "bank_view.json"),
    "it": os.path.join(_FUND_DIR, "it_view.json"),
    # "financials": os.path.join(_FUND_DIR, "fin_view.json"),        # added later
}
_SECTOR_INDEX = {"bank": "^NSEBANK", "it": "^CNXIT"}
_QUOTES_TTL_S = 15 * 60


def _load(sector: str) -> dict:
    path = _SECTOR_JSON.get(sector)
    if not path:
        raise HTTPException(status_code=404,
                            detail=f"unknown sector '{sector}' (have: {list(_SECTOR_JSON)})")
    if not os.path.exists(path):
        raise HTTPException(status_code=500,
                            detail=f"{os.path.basename(path)} not found — run bank_view_data.py first")
    with open(path, "r") as f:
        return json.load(f)


def _cache_path(sector: str) -> str:
    return os.path.join(_STATE_DIR, f"sector_{sector}_quotes.json")


def _read_cache(sector: str) -> dict | None:
    try:
        with open(_cache_path(sector), "r") as f:
            cached = json.load(f)
        if time.time() - cached.get("fetched_at", 0) <= _QUOTES_TTL_S:
            return cached
    except Exception:
        pass
    return None


def _fetch_quotes(symbols: list[str], index_yf: str | None) -> dict:
    """1y daily bars for all names (+ the sector index); derive last / 1w / 1m / 6m moves.
    Per-symbol failures degrade to an absent entry — the panel renders the stored value."""
    import yfinance as yf

    tickers = {s: f"{s}.NS" for s in symbols}
    if index_yf:
        tickers["__INDEX__"] = index_yf
    data = yf.download(
        list(tickers.values()), period="1y", interval="1d",
        group_by="ticker", progress=False, threads=True, auto_adjust=True,
    )
    quotes: dict = {}
    for sym, yft in tickers.items():
        try:
            df = data[yft] if len(tickers) > 1 else data
            close = df["Close"].dropna()
            if close.empty:
                continue
            last = float(close.iloc[-1])

            def back(n_days: int):
                if len(close) <= n_days:
                    return None
                base = float(close.iloc[-1 - n_days])
                return round((last / base - 1.0) * 100.0, 2) if base else None

            quotes[sym] = {  # 5/21/126 trading days ~= 1w / 1m / 6m
                "last": round(last, 2), "r1w": back(5), "r1m": back(21), "r6m": back(126),
                "as_of": str(close.index[-1].date()),
            }
        except Exception:
            continue  # degrade per symbol, never fail the batch
    return quotes


def _get_quotes(sector: str, symbols: list[str], force: bool) -> dict:
    if not force:
        cached = _read_cache(sector)
        if cached is not None:
            return cached
    quotes = _fetch_quotes(symbols, _SECTOR_INDEX.get(sector))
    payload = {"fetched_at": time.time(), "quotes": quotes}
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_cache_path(sector), "w") as f:
            json.dump(payload, f)
    except Exception:
        pass  # cache write is best-effort
    return payload


@router.get("/api/sector-view/{sector}")
def sector_view(sector: str, quotes: bool = False, force_quotes: bool = False):
    """Curated sector fundamentals; ?quotes=true refreshes live NSE prices + returns + current P/B."""
    view = _load(sector)
    if quotes:
        # shape-agnostic: bank -> banks[]/bvps/pb ; it -> stocks[]/eps/pe
        rows = view.get("banks") if "banks" in view else view.get("stocks", [])
        id_key = "bank" if "banks" in view else "stock"
        symbols = [r[id_key] for r in rows]
        try:
            payload = _get_quotes(sector, symbols, force=force_quotes)
            q = payload.get("quotes", {})
            idx = q.get("__INDEX__")
            for r in rows:
                qq = q.get(r[id_key])
                if not qq:
                    continue
                r["last_px"] = qq["last"]
                r["ret_1w"], r["ret_1m"], r["ret_6m"] = qq["r1w"], qq["r1m"], qq["r6m"]
                if r.get("bvps"):
                    r["pb"] = round(qq["last"] / r["bvps"], 2)   # live current P/B
                elif r.get("eps"):
                    r["pe"] = round(qq["last"] / r["eps"], 1)    # live current P/E
                if idx and qq.get("r6m") is not None and idx.get("r6m") is not None:
                    r["rel_6m"] = round(qq["r6m"] - idx["r6m"], 2)
                if idx and qq.get("r1m") is not None and idx.get("r1m") is not None:
                    r["rel_1m"] = round(qq["r1m"] - idx["r1m"], 2)
            if idx and view.get("index"):
                view["index"].update({"ret_1w": idx.get("r1w"), "ret_1m": idx.get("r1m"), "ret_6m": idx.get("r6m")})
            view["quotes_as_of"] = payload.get("fetched_at")
        except Exception as e:
            # Quotes are enrichment — the curated view must still render with stored prices.
            view["quotes_error"] = str(e)
    return {"success": True, "sector": sector, "view": view}
