"""Nifty 50 scan (/api/nifty50-view) — returns + fundamentals-relative pricing for all
index constituents.

Everything is computed ON DEMAND (the frontend button): nothing runs at import or app
start. One batch yfinance download gives 1Y of daily bars for all 50 names + ^NSEI,
from which 1D/1W/6M/1Y returns and the 52-week-range position are derived; a threaded
pass over Ticker.info collects trailing/forward P/E and P/B. The whole result is cached
in .state/nifty50_view_cache.json for 30 minutes (force=true bypasses).

"Priced high or low" is deliberately CATEGORICAL (per SECTOR_INTELLIGENCE_FRAMEWORK.md —
no invented fair values): each stock's trailing P/E (P/B fallback when P/E is missing or
negative) is compared to the MEDIAN of its own Nifty-50 sector peers; >+25% = rich,
<-25% = cheap, else in-line. Sectors with fewer than 3 valued peers fall back to the
whole-index median and are flagged. This is a cross-sectional heuristic — it ignores
growth and quality differences — and the payload says so.

Constituents come from nifty-50-stock-list.csv at the repo root (Symbol, Company Name,
Sector, Weight) — update that file when the index rebalances.
"""
from __future__ import annotations  # backend runs on py3.9 — keep `X | None` lazy

import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException

router = APIRouter()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSV_PATH = os.path.join(_REPO_ROOT, "nifty-50-stock-list.csv")
_DRIVERS_PATH = os.path.join(_REPO_ROOT, "nifty50_drivers.json")  # curated tailwinds/headwinds
_REACTIONS_PATH = os.path.join(_REPO_ROOT, "earnings_reactions.json")  # MEASURED earnings reactions
_STATE_DIR = os.path.join(_REPO_ROOT, ".state")
_CACHE = os.path.join(_STATE_DIR, "nifty50_view_cache_v7.json")  # v7: measured earnings-reaction bias

# Heuristic Nifty trailing-P/E band (PRIOR, not a measurement — post-2021 NSE
# consolidated-earnings methodology; calibrate from history when wired to a P/E series):
# <18 cheap · 18-21 fair · 21-24 mildly rich · >24 rich
_PE_BAND = (18.0, 21.0, 24.0)
_TTL_S = 30 * 60

# vs-sector-median thresholds for the categorical verdict (display convention)
_RICH = 1.25
_CHEAP = 0.75

# NSE symbol changes (renames / demergers). The constituents CSV may carry EITHER the
# old or the new symbol depending on when it was generated, and Yahoo only resolves the
# current one — a stale symbol silently returns an empty row. We try the canonical
# ticker first and fall back to the alternate, so both CSV vintages work.
#   ZOMATO  -> ETERNAL : Zomato Ltd renamed Eternal Ltd (2025)
#   TATAMOTORS -> TMPV : 2025 demerger; the passenger-vehicle entity carries the index seat
# Add a row here whenever NSE renames a constituent; nothing else needs to change.
_TICKER_ALTS = {
    "ZOMATO": ["ETERNAL", "ZOMATO"],
    "ETERNAL": ["ETERNAL", "ZOMATO"],
    "TATAMOTORS": ["TMPV", "TATAMOTORS"],
    "TMPV": ["TMPV", "TATAMOTORS"],
}


def _candidates(symbol: str) -> list:
    """Yahoo ticker candidates for a CSV symbol, most-current first."""
    return _TICKER_ALTS.get(symbol, [symbol])


def load_constituents() -> list[dict]:
    if not os.path.exists(_CSV_PATH):
        raise HTTPException(status_code=500, detail=f"nifty-50-stock-list.csv not found at {_CSV_PATH}")
    out = []
    with open(_CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            sym = (row.get("Symbol") or "").strip()
            if not sym:
                continue
            out.append({
                "symbol": sym,
                "name": (row.get("Company Name") or sym).strip(),
                "sector": (row.get("Sector") or "—").strip(),
                "weight": float(row.get("Weight") or 0) or None,
            })
    return out


def _read_cache() -> dict | None:
    try:
        with open(_CACHE, "r") as f:
            cached = json.load(f)
        if time.time() - cached.get("fetched_at", 0) <= _TTL_S:
            return cached
    except Exception:
        pass
    return None


def _returns_block(close) -> dict:
    """1D/1W/6M/1Y returns + 52w range position from a 1y daily close series."""
    last = float(close.iloc[-1])

    def pct_back(n: int):
        if len(close) <= n:
            return None
        base = float(close.iloc[-1 - n])
        return round((last / base - 1.0) * 100.0, 2) if base else None

    lo, hi = float(close.min()), float(close.max())
    return {
        "last": round(last, 2),
        "d1_pct": pct_back(1),
        "w1_pct": pct_back(5),
        "m6_pct": pct_back(126),
        "y1_pct": pct_back(min(len(close) - 1, 248)),
        "pos_52w": round((last - lo) / (hi - lo), 2) if hi > lo else None,
        "hi_52w": round(hi, 2),
        "lo_52w": round(lo, 2),
        # Range SCENARIOS (not forecasts): the move if price revisits its own 52w extremes.
        "up_to_high_pct": round((hi / last - 1.0) * 100.0, 1) if last else None,
        "down_to_low_pct": round((lo / last - 1.0) * 100.0, 1) if last else None,
        "as_of": str(close.index[-1].date()),
    }


def _fetch_info(sym: str) -> dict:
    """trailing/forward P/E + P/B via yfinance Ticker.info — slow, so threaded upstream."""
    import yfinance as yf
    try:
        info = yf.Ticker(f"{sym}.NS").info or {}
        def num(k):
            v = info.get(k)
            return round(float(v), 2) if isinstance(v, (int, float)) else None
        dy = info.get("dividendYield")
        if isinstance(dy, (int, float)):
            # yahoo has served this both as a fraction (0.035) and a percent (3.5)
            dy = round(float(dy) * 100.0, 2) if dy < 1 else round(float(dy), 2)
        else:
            dy = None
        return {"pe": num("trailingPE"), "fwd_pe": num("forwardPE"), "pb": num("priceToBook"),
                "div_yield": dy}
    except Exception:
        return {"pe": None, "fwd_pe": None, "pb": None, "div_yield": None}


def _median(vals: list[float]) -> float | None:
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return None
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _verdicts(rows: list[dict]) -> None:
    """Categorical rich/in-line/cheap vs sector-median P/E (P/B fallback), in place."""
    index_pe = _median([r["pe"] for r in rows if r.get("pe") and r["pe"] > 0])
    index_pb = _median([r["pb"] for r in rows if r.get("pb") and r["pb"] > 0])
    by_sector: dict = {}
    for r in rows:
        by_sector.setdefault(r["sector"], []).append(r)

    for sector, members in by_sector.items():
        pes = [m["pe"] for m in members if m.get("pe") and m["pe"] > 0]
        use_index = len(pes) < 3
        med_pe = index_pe if use_index else _median(pes)
        pbs = [m["pb"] for m in members if m.get("pb") and m["pb"] > 0]
        # Same index-median fallback as P/E: without it, a loss-maker in a small
        # sector (e.g. IndiGo in a 2-name Services bucket) got NO verdict at all.
        use_index_pb = len(pbs) < 3
        med_pb = index_pb if use_index_pb else _median(pbs)

        for m in members:
            metric, val, med = "pe", m.get("pe"), med_pe
            if not val or val <= 0:  # loss-maker or missing → P/B fallback
                metric, val, med = "pb", m.get("pb"), med_pb
            if not val or val <= 0 or not med:
                m["verdict"] = None
                continue
            ratio = val / med
            label = "rich" if ratio >= _RICH else "cheap" if ratio <= _CHEAP else "in-line"
            m["verdict"] = {
                "label": label,
                "metric": metric,
                "value": round(val, 2),
                "vs_median_pct": round((ratio - 1.0) * 100.0, 1),
                "sector_median": round(med, 2),
                "basis": "index" if ((metric == "pe" and use_index) or
                                     (metric == "pb" and use_index_pb)) else "sector",
                # Reversion SCENARIO (not a forecast): the price move implied if this
                # stock's multiple went to the peer median with earnings/book unchanged.
                "reversion_pct": round((med / val - 1.0) * 100.0, 1),
            }


def _index_read(rows: list[dict], nsei_close) -> dict | None:
    """Index-level cheap/rich + short-term trend read, from data already in the scan.

    VALUATION: bottom-up index trailing P/E = Σw / Σ(w/PE) (harmonic, weight-based —
    equivalent to total mcap / total earnings), judged against the heuristic _PE_BAND.
    TREND: last close vs 50-DMA / 200-DMA + distance from 52-week high.
    The combined lean is a categorical READ of those two states — a prior, not a
    prediction; the options-based Market State view is the desk's real short-term tool.
    """
    try:
        # -- valuation: weighted harmonic P/E over constituents with valid P/E --
        num = den = wtot = 0.0
        for r in rows:
            w = r.get("weight")
            if w:
                wtot += w
                if r.get("pe") and r["pe"] > 0:
                    num += w
                    den += w / r["pe"]
        wpe = round(num / den, 2) if den else None
        coverage = round(num / wtot * 100.0) if wtot else 0
        lo, mid, hi = _PE_BAND
        val_label = (None if wpe is None else
                     "cheap" if wpe < lo else "fair" if wpe < mid else
                     "mildly rich" if wpe < hi else "rich")

        # -- breadth: how the 50 verdicts split --
        breadth = {"rich": 0, "in-line": 0, "cheap": 0}
        for r in rows:
            v = r.get("verdict")
            if v:
                breadth[v["label"]] += 1

        # -- trend: DMAs + distance from 52w high --
        last = float(nsei_close.iloc[-1])
        dma50 = round(float(nsei_close.tail(50).mean()), 2) if len(nsei_close) >= 50 else None
        dma200 = round(float(nsei_close.tail(200).mean()), 2) if len(nsei_close) >= 200 else None
        hi_52w = float(nsei_close.max())
        off_high_pct = round((last / hi_52w - 1.0) * 100.0, 1)
        above50 = dma50 is not None and last > dma50
        above200 = dma200 is not None and last > dma200
        trend_label = ("uptrend" if above50 and above200 else
                       "downtrend" if not above50 and not above200 else "mixed")

        # -- combined categorical lean --
        if trend_label == "uptrend":
            lean, why = ("constructive", "price above both 50- and 200-DMA")
            if val_label in ("mildly rich", "rich"):
                lean = "constructive but stretched"
                why += f"; valuation {val_label} caps the margin for error"
        elif trend_label == "downtrend":
            lean, why = ("cautious", "price below both 50- and 200-DMA")
            if val_label == "cheap":
                lean = "cautious — value building"
                why += "; valuation turning cheap is where bottoms form, but trend must turn first"
        else:
            lean, why = ("neutral", "price between its 50- and 200-DMA — no trend edge")
            if val_label in ("mildly rich", "rich"):
                why += f"; valuation {val_label} argues against chasing strength"

        return {
            "weighted_pe": wpe, "pe_coverage_pct": coverage, "pe_band": list(_PE_BAND),
            "val_label": val_label, "breadth": breadth,
            "dma50": dma50, "dma200": dma200, "above50": above50, "above200": above200,
            "off_high_pct": off_high_pct, "trend_label": trend_label,
            "lean": lean, "why": why,
            "note": ("Heuristic read: bottom-up weighted trailing P/E vs a PRIOR band "
                     f"(<{lo} cheap · {lo}-{mid} fair · {mid}-{hi} mildly rich · >{hi} rich; "
                     f"P/E data covers ~{coverage}% of index weight) + a 50/200-DMA trend state. "
                     "Valuation says almost nothing about the next 3 months — trend and flows "
                     "dominate short-term; see Market State for the options-based read. Not advice."),
        }
    except Exception:
        return None


def _compute() -> dict:
    import yfinance as yf

    cons = load_constituents()
    # Pass 1: batch-download the most-current ticker for every constituent.
    primary = {c["symbol"]: _candidates(c["symbol"])[0] for c in cons}
    data = yf.download(
        [f"{t}.NS" for t in primary.values()] + ["^NSEI"], period="1y", interval="1d",
        group_by="ticker", progress=False, threads=True, auto_adjust=True,
    )

    def _closes(yft: str):
        """Close series for a '<TICKER>.NS' key out of the batch frame, or None."""
        try:
            s = data[yft]["Close"].dropna()
            return s if not s.empty else None
        except Exception:
            return None

    rows: list[dict] = []
    resolved: dict = {}  # csv symbol -> ticker that actually returned bars
    for c in cons:
        sym = c["symbol"]
        row = dict(c)
        close = _closes(f"{primary[sym]}.NS")
        used = primary[sym] if close is not None else None

        # Pass 2: the primary ticker returned nothing (stale CSV symbol after a
        # rename/demerger, or a genuine Yahoo gap) — try the alternates one by one.
        if close is None:
            for alt in _candidates(sym)[1:]:
                try:
                    s = yf.Ticker(f"{alt}.NS").history(period="1y", auto_adjust=True)["Close"].dropna()
                    if not s.empty:
                        close, used = s, alt
                        break
                except Exception:
                    continue

        if close is not None:
            row.update(_returns_block(close))
        else:
            row.update({"last": None, "d1_pct": None, "w1_pct": None, "m6_pct": None,
                        "y1_pct": None, "pos_52w": None, "as_of": None})
        row["yahoo_symbol"] = used
        # Flag the substitution so the UI can explain a symbol that isn't the CSV's.
        row["symbol_note"] = (f"CSV symbol {sym} is stale — resolved via {used} "
                              f"(NSE rename/demerger)") if used and used != sym else None
        resolved[sym] = used
        rows.append(row)

    # Fundamentals use whichever ticker actually resolved.
    live = {s: t for s, t in resolved.items() if t}
    with ThreadPoolExecutor(max_workers=8) as ex:
        infos = dict(zip(live.keys(), ex.map(_fetch_info, live.values())))
    for row in rows:
        row.update(infos.get(row["symbol"], {"pe": None, "fwd_pe": None, "pb": None,
                                             "div_yield": None}))

    _verdicts(rows)

    # Curated qualitative drivers (tailwinds/headwinds/position) — judgment layer,
    # kept in nifty50_drivers.json so it can be hand-edited without touching code.
    drivers_meta = None
    try:
        with open(_DRIVERS_PATH, "r") as f:
            drv = json.load(f)
        companies = drv.get("companies", {})
        for r in rows:
            # Look up by every alias too — the drivers file is keyed on current NSE
            # symbols, so a stale CSV symbol would otherwise find no drivers.
            r["drivers"] = next((companies[k] for k in _candidates(r["symbol"])
                                 if k in companies), None)
        drivers_meta = {"as_of": drv.get("as_of"), "note": drv.get("note")}
    except Exception:
        for r in rows:
            r["drivers"] = None

    # MEASURED earnings reactions — the one layer here that is neither a live quote
    # nor a hand-written judgment. earnings_reactions.json is rebuilt from price_bars
    # by data_agent/fundamentals/earnings_reaction_backfill.py: announcement days are
    # picked by VOLUME ONLY (never by the size of the move, which would make the
    # result circular), then the next session's move is measured against NIFTY and
    # the stock's sector index.
    #
    # What earns its place in the table is the DIVERGENCE: full-sample bias is what
    # this stock usually does on results, recent_bias is the last 8. When those
    # disagree, the market has changed how it reads this name — that is a question
    # worth opening the row for, and 13 of the 50 currently disagree.
    reactions_meta = None
    try:
        with open(_REACTIONS_PATH, "r") as f:
            rx = json.load(f)
        summary = rx.get("summary", {})
        for r in rows:
            rec = next((summary[k] for k in _candidates(r["symbol"]) if k in summary), None)
            if rec:
                rec = dict(rec)
                rec["diverges"] = rec.get("full_bias") != rec.get("recent_bias")
            r["reaction"] = rec
        reactions_meta = {
            "as_of": rx.get("as_of"),
            "events": len(rx.get("events", [])),
            "names": len(summary),
            "diverging": sum(1 for r in rows
                             if (r.get("reaction") or {}).get("diverges")),
        }
    except Exception:
        for r in rows:
            r["reaction"] = None

    index_block = None
    index_read = None
    try:
        nsei_close = data["^NSEI"]["Close"].dropna()
        index_block = _returns_block(nsei_close)
        index_read = _index_read(rows, nsei_close)
    except Exception:
        pass

    # Relative performance vs the index — turns "is it still underperforming?" from a
    # narrative into a number: stock return minus index return over the same window.
    # (Same convention as the Sector View's rel_1m/rel_6m residuals.)
    if index_block:
        for r in rows:
            for key, rel in (("w1_pct", "rel_1w"), ("m6_pct", "rel_6m"), ("y1_pct", "rel_1y")):
                s, i = r.get(key), index_block.get(key)
                r[rel] = round(s - i, 2) if (s is not None and i is not None) else None

    return {
        "fetched_at": time.time(),
        "index": index_block,
        "index_read": index_read,
        "rows": rows,
        "drivers_meta": drivers_meta,
        "reactions_meta": reactions_meta,
        "note": ("Verdicts are categorical (rich / in-line / cheap) vs the sector-median "
                 "trailing P/E within the Nifty 50 (P/B fallback for loss-makers; index-median "
                 "fallback when a sector has <3 valued peers; thresholds ±25%). A cross-sectional "
                 "heuristic that ignores growth/quality differences — a starting point for "
                 "questions, not a fair-value model, and not investment advice."),
        "mechanism": [
            "1 · RETURNS — one batch download of 1 year of daily adjusted closes (Yahoo/yfinance, ~15-min delayed) for all 50 constituents + ^NSEI; 1D/1W/6M/1Y returns and the 52-week-range position are computed from those bars.",
            "2 · FUNDAMENTALS — trailing P/E, forward P/E and P/B per stock from Yahoo's fundamentals snapshot (Ticker.info); occasionally lags a fresh quarterly result.",
            "3 · VERDICT — each stock's trailing P/E is divided by the MEDIAN P/E of its own Nifty-50 sector peers: ≥1.25× = rich, ≤0.75× = cheap, else in-line. Loss-makers/missing P/E fall back to P/B vs sector-median P/B; sectors with <3 valued peers fall back to the whole-index median (flagged).",
            "4 · SCENARIOS (not forecasts) — upside/downside are mechanical reference points: the % move to the stock's own 52-week high and low, and the % move implied if its multiple reverted to the peer median with earnings/book unchanged. They say where price HAS been and where valuation WOULD be at peer parity — they carry no probability.",
            "5 · DRIVERS (judgment, not computation) — each stock's tailwinds (why investors hold: dividend support, deposit/NIM strength, low competition, capex pipeline) and headwinds (what can hurt) come from curated research in nifty50_drivers.json, dated and hand-editable. This is the qualitative layer the numbers can't see — and it goes stale: refresh it each earnings season.",
            "6 · INDEX READ — the overall cheap/fair/rich call is the bottom-up weighted trailing P/E (Σweight ÷ Σ(weight/P/E), i.e. total mcap over total earnings) judged against a heuristic band (<18 cheap · 18-21 fair · 21-24 mildly rich · >24 rich — a PRIOR, not a calibrated measurement). The short-term lean combines that with a 50/200-DMA trend state. Valuation is a poor 3-month timer — the lean weights trend over valuation, and the options-based Market State view remains the desk's real short-term instrument.",
            "7 · EARNINGS REACTION (measured, not judged) — how this stock has actually traded the session after a results announcement, from data_agent/fundamentals/earnings_reaction_backfill.py over price_bars back to 2018. Announcement days are identified by VOLUME ONLY — never by the size of the move, which would make the finding circular. Each reaction is measured against NIFTY and against the stock's own sector index, so a 'positive' bias means it beat the market on results day, not merely that it rose. The badge shows the RECENT bias (last 8 events); the arrow marks names whose recent behaviour has diverged from their full-sample habit, which is where the market has changed its mind about a name.",
            "A rich stock can stay rich for years (quality/growth premium) and a cheap one can be cheap for a reason (value trap) — the verdict is a question to investigate, not a signal to trade.",
        ],
    }


_FACTORS_PATH = os.path.join(_REPO_ROOT, "nifty_factors.json")
_TODAY_PATH = os.path.join(_REPO_ROOT, "nifty_today.json")


@router.get("/api/nifty-today")
def nifty_today():
    """Curated 'today's market' brief: ranked drivers, sector-weight math, what to
    monitor tomorrow. Plain file read of nifty_today.json — decays in a day; the
    frontend shows the as_of date prominently so a stale brief is visibly stale."""
    if not os.path.exists(_TODAY_PATH):
        raise HTTPException(status_code=500, detail=f"nifty_today.json not found at {_TODAY_PATH}")
    with open(_TODAY_PATH, "r") as f:
        return {"success": True, "today": json.load(f)}


@router.get("/api/nifty-factors")
def nifty_factors():
    """Curated macro-factor library: status, transmission, episode-based scenario priors,
    and the dated today/this-week expectation. A plain file read (nifty_factors.json at
    repo root) — no market data fetched; edit the file to update the factors."""
    if not os.path.exists(_FACTORS_PATH):
        raise HTTPException(status_code=500, detail=f"nifty_factors.json not found at {_FACTORS_PATH}")
    with open(_FACTORS_PATH, "r") as f:
        return {"success": True, "factors": json.load(f)}


@router.get("/api/nifty50-view")
def nifty50_view(force: bool = False):
    """Nifty 50 constituents scan — computed on request, cached 30 min."""
    if not force:
        cached = _read_cache()
        if cached is not None:
            return {"success": True, "view": cached, "cached": True}
    view = _compute()
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_CACHE, "w") as f:
            json.dump(view, f)
    except Exception:
        pass  # cache write is best-effort
    return {"success": True, "view": view, "cached": False}
