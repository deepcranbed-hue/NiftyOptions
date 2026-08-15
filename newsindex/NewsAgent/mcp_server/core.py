"""
core.py — Deterministic Core adapter for the News Intelligence Agent MCP server.

This module is a THIN WRAPPER. It imports the existing `market_scan.py` engine
(unmodified) and exposes its functions as a clean, JSON-serializable "core" API that
the MCP tools (server.py) and the MIO assembler (mio_builder.py) call into.

Design rules (from NewsAgent/ARCHITECTURE.md §6):
  * Numbers come from the Core (market_scan.py), never invented here.
  * Everything returned is JSON-serializable (plain dict / list / str / number / bool).
  * No edits to market_scan.py — we only import and call it.

The engine is a daily/session aggregate scanner, so a "snapshot" here is one capture of
all quote universes + news + flows + earnings. Compute functions run off that snapshot.
"""
from __future__ import annotations

import os
import sys
import math
import datetime as dt
from pathlib import Path
from typing import Any

# --- import the VENDORED engine (NewsAgent is self-contained) ----------------
# The engine lives at NewsAgent/engine/ (a verbatim copy of market_scan.py + siblings),
# so NewsAgent runs with NO dependency on the parent newsindex/ project. Setting
# NEWSINDEX_HOME can still override the engine location (e.g. to track the live engine).
_ENGINE_DIR = Path(os.environ.get(
    "NEWSAGENT_ENGINE",
    Path(__file__).resolve().parents[1] / "engine",
))
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
# legacy override: if NEWSINDEX_HOME is set, prefer the parent engine (opt-in)
_LEGACY = os.environ.get("NEWSINDEX_HOME")
if _LEGACY and str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

try:
    import market_engine as ms          # vendored engine (default)
except ModuleNotFoundError:
    import market_scan as ms            # fallback: parent engine if NEWSINDEX_HOME points at it


# =============================================================================
# Snapshot: one capture of every input universe the engine consumes.
# Held in-process so multiple tool calls in a session reuse the same as-of data.
# =============================================================================
_SNAP: dict[str, Any] | None = None
# Replay lock: when a snapshot is injected (offline/replay/backtest), a live refresh
# must NOT clobber it. The Collector's refresh tool then returns the existing snapshot.
_REPLAY: bool = False


def _clean_quotes(rows: list[dict]) -> list[dict]:
    """Keep only JSON-safe fields from a fetch_quotes row list.

    previous_close MUST be kept: _level_of() falls back to it when `last` is missing, so
    that a failed intraday fetch still yields a usable price LEVEL (band table, ×level
    amplifier, oil-driven sector scores). Dropping it here silently disabled that entire
    fallback — the engine could never see the field it was told to fall back to.
    """
    out = []
    for r in rows or []:
        out.append({
            "name": r.get("name"),
            "symbol": r.get("symbol"),
            "last": r.get("last"),
            "previous_close": r.get("previous_close"),
            "pct_change": r.get("pct_change"),
            "pct_intraday": r.get("pct_intraday"),
            "asof": r.get("asof"),
            "suspect": r.get("suspect", False),
            # provenance written by resilient_quotes tiers (nse/stooq/tradingeconomics/
            # browser/cache) — keep it so the report can show WHICH source rescued a row
            "source": r.get("source"),
            "fallback": r.get("fallback", False),
            "cached": r.get("cached", False),
            "cache_age_days": r.get("cache_age_days"),
        })
    return out


def refresh_snapshot() -> dict:
    """
    LIVE fetch of every engine input via market_scan's own fetchers, cached in-process.
    Requires the engine's runtime deps (yfinance for prices, network for news/flows).
    Returns a compact summary; the full snapshot stays in memory.
    """
    global _SNAP
    # Honour the replay lock: never overwrite an injected snapshot with a live fetch.
    if _REPLAY and _SNAP is not None:
        return snapshot_summary()

    # -- hard timeouts so a hung download can't stall the run (additive; no engine edit) --
    _ovl = str(Path(__file__).resolve().parents[1] / "overlay")
    if _ovl not in sys.path:
        sys.path.insert(0, _ovl)
    try:
        import net_timeout as _nt
        _nt.install_default_timeouts()          # cap requests + curl_cffi reads
        _budget = float(os.environ.get("NEWSAGENT_FETCH_BUDGET_S", "20"))
        _timeouts = []

        def _fetch(label, fn, default):
            val, timed_out = _nt.run_with_timeout(fn, _budget, default)
            if timed_out:
                _timeouts.append(label)
            return val
    except Exception:
        _timeouts = []
        def _fetch(label, fn, default):            # noqa: E306 — fallback if helper import fails
            try:
                return fn()
            except Exception:
                return default

    def _placeholders(symmap):
        return [{"name": n, "symbol": s, "last": None, "pct_change": None}
                for n, s in (symmap or {}).items()]

    def _q(label, symmap):
        # in-process fallback: threaded fetch under the wall-clock cap; placeholders on timeout so
        # the NSE/Stooq backfill can still recover the group.
        ph = _placeholders(symmap)
        return _clean_quotes(_fetch(label, lambda: ms.fetch_quotes(symmap), ph) or ph)

    # group name -> engine symbol map
    _GROUPS = {"quotes_idx": ms.INDICES, "quotes_macro": ms.MACRO, "quotes_stk": ms.STOCKS,
               "it_quotes": ms.IT_STOCKS, "sector_quotes": ms.SECTOR_PROXIES,
               "theme_quotes": ms.THEME_STOCKS, "univ_quotes": ms.SECTOR_UNIVERSE}

    # PRIMARY: fetch all quotes in a KILLABLE subprocess (survives a curl_cffi GIL-holding hang).
    quotes, mode = None, "inline"
    try:
        import proc_fetch
        budget = float(os.environ.get("NEWSAGENT_QUOTES_BUDGET_S", "60"))
        quotes = proc_fetch.fetch_quotes_isolated(str(_ENGINE_DIR), _GROUPS,
                                                  overlay_dir=_ovl, budget=budget)
    except Exception as e:
        quotes = {"__error__": str(e)[:120]}

    snap: dict[str, Any] = {"as_of": dt.datetime.now(dt.timezone.utc).isoformat(), "live": True}
    if isinstance(quotes, dict) and not quotes.get("__timeout__") and not quotes.get("__error__"):
        mode = "isolated"
        for g, sm in _GROUPS.items():
            rows = quotes.get(g)
            if not isinstance(rows, list):        # a per-group error → placeholders for backfill
                rows = _placeholders(sm)
            snap[g] = _clean_quotes(rows)
    else:
        # subprocess killed/errored/disabled → in-process threaded fetch (still capped), then backfill
        if isinstance(quotes, dict) and quotes.get("__timeout__"):
            _timeouts.append("quotes(isolated)")
            if quotes.get("stall_trace"):
                snap["quote_stall_trace"] = quotes["stall_trace"]   # thread stacks at the stall
        for g, sm in _GROUPS.items():
            snap[g] = _q(g, sm)
    snap["quote_fetch_mode"] = mode
    snap["flows"] = _fetch("flows", lambda: ms.fetch_fii_dii() or [], []) or []
    snap["news"] = _fetch("news", lambda: ms.fetch_news() or [], []) or []
    snap["earnings"] = []
    try:
        snap["earnings"] = _fetch("earnings",
                                  lambda: ms.enrich_earnings(ms.fetch_earnings() or []), []) or []
    except Exception:
        snap["earnings"] = []
    if _timeouts:
        snap["fetch_timeouts"] = _timeouts          # which groups hit the wall-clock cap
    # multi-source resilience: backfill any quote Yahoo failed to return (NSE → Stooq).
    # Additive + guarded — never overwrites a good value, never raises into the pipeline.
    try:
        _ovl = str(Path(__file__).resolve().parents[1] / "overlay")
        if _ovl not in sys.path:
            sys.path.insert(0, _ovl)
        import resilient_quotes
        snap["quote_fallback"] = resilient_quotes.backfill(snap)
    except Exception as e:
        snap["quote_fallback"] = {"error": str(e)[:120]}
    _SNAP = snap
    return snapshot_summary()


def load_snapshot(snap: dict, replay: bool = True) -> dict:
    """Inject a snapshot directly (used for offline testing / replay).
    Sets the replay lock by default so a Collector refresh won't refetch over it."""
    global _SNAP, _REPLAY
    _REPLAY = replay
    snap.setdefault("as_of", dt.datetime.now(dt.timezone.utc).isoformat())
    snap.setdefault("live", False)
    for k in ("quotes_idx", "quotes_macro", "quotes_stk", "it_quotes",
              "sector_quotes", "theme_quotes", "univ_quotes", "flows", "news", "earnings"):
        snap.setdefault(k, [])
    _SNAP = snap
    return snapshot_summary()


def _ensure() -> dict:
    """Return the current snapshot, doing a live refresh if none exists yet."""
    global _SNAP
    if _SNAP is None:
        refresh_snapshot()
    return _SNAP  # type: ignore[return-value]


def has_snapshot() -> bool:
    return _SNAP is not None


def snapshot_summary() -> dict:
    s = _ensure()
    return {
        "as_of": s["as_of"],
        "live": s.get("live", False),
        "counts": {
            "indices": len(s["quotes_idx"]),
            "macro": len(s["quotes_macro"]),
            "stocks": len(s["quotes_stk"]),
            "it": len(s["it_quotes"]),
            "sector_proxies": len(s["sector_quotes"]),
            "universe": len(s["univ_quotes"]),
            "flows": len(s["flows"]),
            "news": len(s["news"]),
            "earnings": len(s["earnings"]),
        },
    }


# =============================================================================
# Engine outputs — each function returns a JSON-safe view of a market_scan result.
# =============================================================================

# Human-readable labels for the internal driver keys.
DRIVER_LABELS = {
    "oil_pct": "Oil", "vix_pct": "India VIX", "us10y_pct": "US 10Y Yield",
    "dxy_pct": "Dollar Index", "kospi_pct": "Kospi", "sox_pct": "SOX (US semis)",
    "fii_kcr": "FII flow", "geopolitics_hits": "Geopolitics",
    "india_cpi_hot": "India CPI", "us_cpi_cool": "US CPI cooling",
    "interaction": "Driver interaction",
}

# Map a dominant driver to an MIO event class + a canonical event label.
DRIVER_EVENT = {
    "oil_pct":          ("Market",       "Crude Oil Move"),
    "geopolitics_hits": ("Geopolitical", "Geopolitical Risk Event"),
    "fii_kcr":          ("Market",       "Foreign-Flow Shift"),
    "vix_pct":          ("Market",       "Volatility Shock"),
    "us10y_pct":        ("Market",       "US Yield Move"),
    "dxy_pct":          ("Market",       "Dollar Move"),
    "india_cpi_hot":    ("Economic",     "India CPI Surprise"),
    "us_cpi_cool":      ("Economic",     "US CPI Surprise"),
    "sox_pct":          ("Market",       "Semiconductor / AI-Chip Move"),
    "kospi_pct":        ("Market",       "Asia Tech Move"),
    "interaction":      ("Market",       "Multi-Driver Interaction"),
}


def oil_level(price) -> dict:
    """Surface the engine's oil LEVEL context: a +2% move at $100 bites harder than at $70.
    Reuses market_scan._oil_level_mult (the same amplifier the causal engine applies)."""
    # use the ONE canonical band label so §1, §4 and the level table agree
    mult = ms._oil_level_mult(price)
    band = ms._oil_band_label(price)
    return {"price": price, "multiplier": mult, "band": band}


def detect_regime() -> dict:
    """Regime Agent — AI regime (news+tape), observed tape tone, oil regime."""
    s = _ensure()
    ai, ev, conf = ms.detect_ai_regime(s["news"], s["quotes_idx"], s["quotes_macro"])
    tone, observed = ms.market_regime(s["quotes_idx"], s["quotes_macro"], s["flows"])
    try:
        oil_lines = ms.build_oil_regime(s["quotes_macro"], s["news"])
    except Exception:
        oil_lines = []
    return {
        "ai_regime": ai,
        "ai_confidence": conf,
        "ai_evidence": [e.get("title") for e in ev][:3],
        "observed_tone": tone,
        "observed": observed,
        "oil_regime": oil_lines,
    }


def run_engine() -> dict:
    """Transmission + Impact core — the full causal engine dict (JSON-safe)."""
    s = _ensure()
    ai = ms.detect_ai_regime(s["news"], s["quotes_idx"], s["quotes_macro"])[0]
    eng = ms.build_causal_engine(s["quotes_idx"], s["quotes_macro"], s["flows"],
                                 s["news"], ai_regime=ai)
    return eng


def causal_engine() -> dict:
    """Compact view of the causal engine for the MCP tool surface."""
    eng = run_engine()
    idx = {name: {"total": v["total"], "lo": v["lo"], "hi": v["hi"]}
           for name, v in eng["indices"].items()}
    return {
        "sentiment": eng["sentiment"],
        "conviction": eng["conviction"],
        "agreement": eng["agreement"],
        "n_bull": eng["n_bull"], "n_bear": eng["n_bear"],
        "expected_move": idx,
        "drivers": eng["drivers"],
        "dissenters": [DRIVER_LABELS.get(k, k) for k in eng["dissenters"]],
        "chains": [c[0] for c in eng["chains"]],
        "brent_price": eng.get("brent_price"),
        "oil_mult": eng.get("oil_mult"),
        "oil_level": oil_level(eng.get("brent_price")),
    }


def driver_dominance(index: str = "Nifty 50") -> dict:
    """
    Driver-Dominance Agent — normalize |contribution| of each driver on the index
    into shares that sum to ~1.0. Pure re-projection of the Core's numbers.
    """
    eng = run_engine()
    contrib = eng["indices"].get(index, {}).get("contrib", {})
    absmap = {k: abs(v) for k, v in contrib.items() if v}
    tot = sum(absmap.values()) or 1.0
    vector = {DRIVER_LABELS.get(k, k): round(v / tot, 3) for k, v in absmap.items()}
    # sort desc for readability
    vector = dict(sorted(vector.items(), key=lambda kv: -kv[1]))
    dom_key = max(absmap, key=absmap.get) if absmap else None
    return {
        "index": index,
        "vector": vector,
        "dominant_driver": DRIVER_LABELS.get(dom_key, dom_key) if dom_key else None,
        "dominant_driver_key": dom_key,
        "dominant_driver_score": round(absmap[dom_key] / tot, 3) if dom_key else 0.0,
    }


def sector_intelligence() -> list[dict]:
    """Sector Intelligence Agent — per-sector net driver score + verdict."""
    s = _ensure()
    eng = run_engine()
    ai = ms.detect_ai_regime(s["news"], s["quotes_idx"], s["quotes_macro"])[0]
    _, observed = ms.market_regime(s["quotes_idx"], s["quotes_macro"], s["flows"])
    rows = ms.build_sector_factor_model(eng, s["quotes_macro"], observed, s["news"], ai)
    return rows


def transmission_map() -> list[str]:
    """Transmission Agent — driver → channel → sector network (text lines)."""
    s = _ensure()
    eng = run_engine()
    ai = ms.detect_ai_regime(s["news"], s["quotes_idx"], s["quotes_macro"])[0]
    try:
        return ms.build_transmission_map(eng, s["quotes_macro"], ai, s["news"])
    except Exception as e:
        return [f"(transmission map unavailable: {e})"]


def validate_relationships() -> list[dict]:
    """Validation Agent — expected vs observed per cross-asset rule, weighted."""
    s = _ensure()
    eng = run_engine()
    ai = ms.detect_ai_regime(s["news"], s["quotes_idx"], s["quotes_macro"])[0]
    stock_lists = [s["quotes_idx"], s["it_quotes"], s["sector_quotes"],
                   s["quotes_stk"], s["univ_quotes"]]
    rows = ms.build_cause_effect_scorecard(eng, s["quotes_macro"], stock_lists, ai)
    # make rows JSON-safe (checks tuples -> dicts) and add a status verdict
    out = []
    for r in rows:
        checks = [{"name": c[0], "observed_pct": c[1], "ok": bool(c[2]),
                   "expected_sign": (1 if c[3] > 0 else -1), "weight": c[4]}
                  for c in r.get("checks", [])]
        wagree = r.get("wagree", 0)
        status = ("CONFIRMED" if wagree >= 60 else
                  "WEAKENED" if wagree >= 40 else "OVERRIDDEN")
        out.append({
            "name": r["name"], "driver": r["driver"], "driver_key": r["dkey"],
            "strength": r["strength"], "expected": r["expected"], "regime": r["regime"],
            "weighted_agreement_pct": wagree, "status": status,
            "confirmed": r["c"], "disagreed": r["d"], "checks": checks,
        })
    return out


def company_intelligence() -> list[dict]:
    """Company Intelligence Agent — company-specific news, sentiment, Nifty weight."""
    s = _ensure()
    return ms.classify_company_news(s["news"])


def market_themes() -> list[dict]:
    """Active themes from the playbook (name, why, hit count)."""
    s = _ensure()
    themes = ms.detect_themes(s["news"])
    return [{"name": t["name"], "why": t["why"], "hits": len(t["hits"])} for t in themes]


def standout_movers(top: int = 4) -> dict:
    """Weight-adjusted biggest gainers / losers across the fetched universe."""
    s = _ensure()
    lists = [s["quotes_idx"], s["quotes_stk"], s["it_quotes"],
             s["sector_quotes"], s["theme_quotes"], s["univ_quotes"]]
    gainers, losers = ms.build_standout_movers(lists, top=top)
    keep = lambda q: {"name": q["name"], "pct_change": q["pct_change"], "last": q["last"]}
    return {"gainers": [keep(g) for g in gainers], "losers": [keep(l) for l in losers]}


def market_verdict() -> dict:
    """One-line observed-tape verdict + the observed dict."""
    s = _ensure()
    verdict = ms.build_verdict(s["quotes_idx"], s["quotes_macro"], s["news"], s["flows"])
    tone, observed = ms.market_regime(s["quotes_idx"], s["quotes_macro"], s["flows"])
    return {"verdict": verdict, "tone": tone, "observed": observed}


def shock_type() -> str:
    """Classify the oil/market shock type (supply/demand/... ) from news."""
    s = _ensure()
    try:
        raw = ms.classify_oil_shock(s["news"]) or ""
    except Exception:
        raw = ""
    r = raw.lower()
    for t in ("supply", "demand", "inventory", "speculation", "policy"):
        if t in r:
            return t
    return "none"
