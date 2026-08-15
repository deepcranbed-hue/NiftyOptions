"""AI Infrastructure theme (/api/ai-infra-theme) — India-listed AI-infra beneficiaries.

Serves the curated dataset in ai_infra_theme.json (repo root) and, on request,
enriches each company with live quotes via yfinance ("{SYMBOL}.NS"). Quotes are
cached in .state/ai_infra_quotes_cache.json (15-min TTL) so a page refresh never
hammers Yahoo; the curated JSON itself is always read fresh from disk so hand
edits show up immediately.

Dataset maintenance: edit ai_infra_theme.json directly. Per
SECTOR_INTELLIGENCE_FRAMEWORK.md, evidence is temporal — every item carries a
date, and order-win items decay in weeks-to-quarters. Prune/refresh stale items
rather than trusting them.
"""
from __future__ import annotations  # backend runs on py3.9 — keep `X | None` lazy

import json
import os
import sqlite3
import sys
import time
from datetime import date

from fastapi import APIRouter, HTTPException

router = APIRouter()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_THEME_PATH = os.path.join(_REPO_ROOT, "ai_infra_theme.json")
_STATE_DIR = os.path.join(_REPO_ROOT, ".state")
_QUOTES_CACHE = os.path.join(_STATE_DIR, "ai_infra_quotes_cache_v2.json")  # v2: 52w hi/lo
_QUOTES_TTL_S = 15 * 60

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)  # bar_store.py sits at the repo root


def _load_theme() -> dict:
    if not os.path.exists(_THEME_PATH):
        raise HTTPException(status_code=500, detail=f"ai_infra_theme.json not found at {_THEME_PATH}")
    with open(_THEME_PATH, "r") as f:
        return json.load(f)


def _read_quotes_cache() -> dict | None:
    try:
        with open(_QUOTES_CACHE, "r") as f:
            cached = json.load(f)
        if time.time() - cached.get("fetched_at", 0) <= _QUOTES_TTL_S:
            return cached
    except Exception:
        pass
    return None


def _fetch_quotes(symbols: list[str], since_dates: dict | None = None) -> dict:
    """Batch-fetch 1y of daily bars for all symbols; derive last / 1d / 3m / 1y moves.

    since_dates maps symbol -> 'YYYY-MM-DD' (the outlook call date); when given,
    also computes since_pct — the price move since that date — so the view can
    check each 3-month lean against realized price action.

    Per-symbol failures degrade to an absent entry — the view renders '—' for
    those instead of failing the whole panel.
    """
    import pandas as pd  # noqa: F401  (yfinance returns DataFrames)
    import yfinance as yf

    since_dates = since_dates or {}
    tickers = {s: f"{s}.NS" for s in symbols}
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

            def pct_back(n_days: int):
                if len(close) <= n_days:
                    return None
                base = float(close.iloc[-1 - n_days])
                return round((last / base - 1.0) * 100.0, 2) if base else None

            since_pct = None
            since = since_dates.get(sym)
            if since:
                try:
                    ref = close[close.index >= since]
                    if len(ref) > 0 and float(ref.iloc[0]):
                        since_pct = round((last / float(ref.iloc[0]) - 1.0) * 100.0, 2)
                except Exception:
                    pass  # since-call check is optional per symbol

            lo, hi = float(close.min()), float(close.max())
            quotes[sym] = {
                "last": round(last, 2),
                "d1_pct": pct_back(1),
                "m3_pct": pct_back(63),
                "y1_pct": pct_back(min(len(close) - 1, 248)),
                "since_pct": since_pct,
                "hi_52w": round(hi, 2),
                "lo_52w": round(lo, 2),
                "as_of": str(close.index[-1].date()),
            }
        except Exception:
            continue  # degrade per symbol, never fail the batch
    return quotes


def _get_quotes(symbols: list[str], force: bool, since_dates: dict | None = None) -> dict:
    if not force:
        cached = _read_quotes_cache()
        if cached is not None:
            return cached
    quotes = _fetch_quotes(symbols, since_dates=since_dates)
    payload = {"fetched_at": time.time(), "quotes": quotes}
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_QUOTES_CACHE, "w") as f:
            json.dump(payload, f)
    except Exception:
        pass  # cache write is best-effort
    return payload


@router.get("/api/ai-infra-theme")
def ai_infra_theme(quotes: bool = False, force_quotes: bool = False):
    """The AI-infra thematic dataset; ?quotes=true adds live NSE quotes per company."""
    theme = _load_theme()
    if quotes:
        symbols = [c["symbol"] for c in theme.get("companies", [])]
        since_dates = {
            c["symbol"]: c["outlook_3m"]["as_of"]
            for c in theme.get("companies", [])
            if c.get("outlook_3m", {}).get("as_of")
        }
        try:
            payload = _get_quotes(symbols, force=force_quotes, since_dates=since_dates)
            qmap = payload.get("quotes", {})
            for c in theme["companies"]:
                c["quote"] = qmap.get(c["symbol"])
            theme["quotes_as_of"] = payload.get("fetched_at")
        except Exception as e:
            # Quotes are enrichment, not the product — the curated view must
            # still render offline / with yfinance down.
            theme["quotes_error"] = str(e)
    return {"success": True, "theme": theme}


# ---------------------------------------------------------------------------
# Per-company detail — the view Tickertape structurally cannot build
# ---------------------------------------------------------------------------
# Their page answers "what are this company's numbers". This one answers three
# questions a data aggregator has no way to hold:
#
#   1. How OLD is each fact, and has it outlived its half-life?
#   2. What is this company load-bearing FOR — which hypotheses break if it fails?
#   3. What did we say about it before, and what did the price do afterwards?
#
# All three are read-only derivations over ai_infra_theme.json,
# ai_infra_call_history.json and price_bars. Nothing here writes.

_CALLS_PATH = os.path.join(_REPO_ROOT, "ai_infra_call_history.json")

# Half-lives in DAYS, from SECTOR_INTELLIGENCE_FRAMEWORK.md's decay table. These are
# the framework's own numbers, not new ones invented here: an order-win headline stops
# carrying information in weeks, a quarterly print lasts a quarter, a capacity plan or
# a policy change lasts years. An item past its half-life is not WRONG — it is no
# longer news, and the view greys it rather than deleting it.
_HALF_LIFE_DAYS = {"interpretation": 21, "order": 90, "earnings": 95,
                   "structural": 730, "policy": 730}

# Word-stem -> decay class. Deliberately crude and deliberately visible: the class is
# returned in the payload so a wrong call is obvious in the UI rather than buried.
_DECAY_HINTS = [
    (("q1", "q2", "q3", "q4", "results", "pat ", "revenue", "ebitda", "margin"), "earnings"),
    (("order", "contract", "award", "win", "backlog", "inflow"), "order"),
    (("capex", "capacity", "plant", "expansion", "target", "roadmap", "guidance",
      "demerger", "qip", "policy", "tax", "gw ", " mw"), "structural"),
    (("rallied", "read-across", "upper circuit", "broker", "rating", "analyst",
      "no news", "research check"), "interpretation"),
]


def _decay_class(note: str) -> str:
    low = (note or "").lower()
    for stems, cls in _DECAY_HINTS:
        if any(t in low for t in stems):
            return cls
    return "interpretation"


def _age_days(datestr: str, today: date) -> "int | None":
    """Evidence dates are YYYY-MM or YYYY-MM-DD. A month-only date is aged from the
    15th, so a mid-month item is not treated as a fortnight older or newer than it is.

    Clamped at 0: for an item dated in the CURRENT month the 15th may not have
    happened yet, and a negative age would render as freshness above 1.0 — an
    opacity greater than one, which is a silent bug rather than a visible one.
    """
    try:
        parts = str(datestr).split("-")
        y, m = int(parts[0]), int(parts[1])
        d = int(parts[2]) if len(parts) > 2 else 15
        return max(0, (today - date(y, m, d)).days)
    except (ValueError, IndexError, TypeError):
        return None


def _closes(con, symbol):
    rows = con.execute(
        "select ts, close from price_bars where symbol=? and timeframe='1d' "
        "order by ts", (symbol.upper(),)).fetchall()
    return [(r[0][:10], float(r[1])) for r in rows if r[1] is not None]


def _move(series, frm, to=None):
    """% move between the first close on/after `frm` and the last close on/before `to`.

    Returns None — never 0 — when the window cannot be measured. A call made after
    the last stored bar has no result yet, and scoring it flat would quietly enter
    the track record as a neutral outcome the market never actually delivered.

    Anchoring to the first close ON OR AFTER the call date is deliberate: calls are
    dated when they were written, and 2026-08-02 was a Sunday. The honest entry
    price is Monday's close, not a price that never traded.
    """
    if not series:
        return None
    start = next((c for d, c in series if d >= frm), None)
    if start is None:
        return None
    ends = [c for d, c in series if to is None or d <= to]
    if not ends:
        return None
    return round((ends[-1] / start - 1.0) * 100.0, 2)


@router.get("/api/ai-infra-company/{symbol}")
def ai_infra_company(symbol: str, bars: int = 400):
    """Everything the company page needs, in one call.

    `bars` caps the returned price series; the scoring below always uses the full
    series so a long lookback cannot be silently truncated by a display limit.
    """
    sym = symbol.upper()
    theme = _load_theme()
    company = next((c for c in theme.get("companies", []) if c["symbol"].upper() == sym), None)
    if company is None:
        raise HTTPException(status_code=404, detail=f"{sym} is not in the AI-infra theme")

    today = date.today()

    # ---- 1. evidence, aged against the framework's decay table ----
    evidence = []
    for e in company.get("evidence", []):
        cls = _decay_class(e.get("note", ""))
        age = _age_days(e.get("date"), today)
        hl = _HALF_LIFE_DAYS[cls]
        evidence.append({
            **e, "decay_class": cls, "half_life_days": hl, "age_days": age,
            # 1.0 = brand new, 0.0 = at or past the half-life. A bar, not a score:
            # it drives opacity in the UI and is not used in any calculation.
            "freshness": (None if age is None else round(min(1.0, max(0.0, 1.0 - age / hl)), 3)),
            "stale": (None if age is None else age >= hl),
        })
    evidence.sort(key=lambda x: (x.get("date") or ""), reverse=True)

    # ---- 2. hypothesis edges, both directions ----
    hyp_text = {h["id"]: h for h in theme.get("hypotheses", [])}
    edges = []
    for L in theme.get("hypothesis_links", []):
        if L.get("symbol", "").upper() != sym:
            continue
        h = hyp_text.get(L["hypothesis"], {})
        edges.append({**L, "hypothesis_text": h.get("text"), "status": h.get("status"),
                      "note": h.get("note")})
    # How exposed is each hypothesis if THIS name fails — i.e. how many other
    # companies carry the same side of it. A hypothesis resting on one company is
    # a different animal from one resting on six.
    for e in edges:
        others = [L for L in theme.get("hypothesis_links", [])
                  if L["hypothesis"] == e["hypothesis"] and L["role"] == e["role"]
                  and L["symbol"].upper() != sym]
        e["other_names_same_side"] = sorted({L["symbol"] for L in others})

    # ---- 3. call history, scored over the window each call was actually live ----
    try:
        with open(_CALLS_PATH) as f:
            history = json.load(f).get("calls", {}).get(sym, [])
    except (OSError, ValueError):
        history = []

    series, price_note = [], None
    try:
        import bar_store
        con = sqlite3.connect(bar_store.DB_PATH)
        try:
            series = _closes(con, sym)
        finally:
            con.close()
    except Exception as ex:                                   # noqa: BLE001
        price_note = f"price_bars unreadable: {ex}"
    if not series and not price_note:
        price_note = ("No daily bars stored for this symbol yet — run the 'ai-infra' "
                      "step in data_agent/sync_all.py. Calls cannot be scored without them.")

    # A call is live until the next call OF THE SAME KIND supersedes it. Scoring an
    # 8-Aug 'up' lean over a window that includes three weeks after we abandoned it
    # would flatter or damn the call for a view we no longer held.
    by_kind = {}
    for c in history:
        by_kind.setdefault(c["kind"], []).append(c)
    for kind, calls in by_kind.items():
        calls.sort(key=lambda c: c["as_of"])
        for i, c in enumerate(calls):
            nxt = calls[i + 1]["as_of"] if i + 1 < len(calls) else None
            c["superseded_on"] = nxt
            c["live"] = nxt is None
            c["move_while_live_pct"] = _move(series, c["as_of"], nxt) if series else None
            c["move_to_date_pct"] = _move(series, c["as_of"]) if series else None

    calls = sorted(history, key=lambda c: (c["as_of"], c["kind"]), reverse=True)

    # ---- 4. segment peers, for positioning on the three grade inputs ----
    peers = [{
        "symbol": c["symbol"], "name": c["name"], "exposure": c["exposure"],
        "grade": (c.get("grade_12m") or {}).get("grade"),
        "conviction": (c.get("grade_12m") or {}).get("conviction"),
        "evidence_strength": (c.get("grade_12m") or {}).get("evidence_strength"),
        "priced_in": (c.get("grade_12m") or {}).get("priced_in"),
        "pe_ttm": ((c.get("grade_12m") or {}).get("valuation") or {}).get("pe_ttm"),
        "is_self": c["symbol"].upper() == sym,
    } for c in theme.get("companies", []) if c["segment"] == company["segment"]]

    return {
        "success": True,
        "symbol": sym,
        "as_of": theme.get("as_of"),
        "company": company,
        "segment_label": next((s["label"] for s in theme.get("segments", [])
                               if s["id"] == company["segment"]), company["segment"]),
        "evidence": evidence,
        "hypotheses": edges,
        "calls": calls,
        "peers": peers,
        "prices": [{"d": d, "c": c} for d, c in series[-bars:]],
        "price_note": price_note,
        "half_life_note": (
            "Half-lives are the framework's own decay table, not new numbers: "
            "interpretation 3 weeks, order wins ~1 quarter, earnings ~1 quarter, "
            "structural and policy items 2 years. An item past its half-life is not "
            "wrong — it has stopped being news. The decay class is inferred from the "
            "note text and is shown so a misclassification is visible."),
        "disclaimer": theme.get("disclaimer"),
    }
