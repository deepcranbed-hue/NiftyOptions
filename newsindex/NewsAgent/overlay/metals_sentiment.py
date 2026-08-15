"""
metals_sentiment.py — a BROAD base-metals sentiment read (global + Indian MCX), not just copper.

Copper (LME/COMEX 'Dr Copper') is the classic global-demand proxy, but it is one contract. This
module blends it with the wider base-metals complex into a single sentiment score:

    GLOBAL  — copper (HG=F) + aluminium (ALI=F)            → world demand / cycle
    INDIA   — MCX base-metals (METLDEX) / MCX contracts    → domestic price = LME × INR × import
                                                              duty + local demand

so the Metals relationship (§7) and metals sector read are driven by BROAD metal sentiment, not a
single chart. Fully config-driven via metals_config.json — add/point the MCX slot at any feed.

Data policy:
  * 'from_snapshot' components read a name the engine already fetched (no extra network).
  * 'symbol' components are fetched once via the engine's fetch_quotes() helper (guarded).
  * a component with no reachable value is reported as unavailable — NEVER fabricated. MCX ships
    without a free feed, so by default it shows 'n/a (configure feed)' until you wire one.
  * the composite is a weighted average over ONLY the buckets that have data (weights renormalised),
    and coverage is reported so the reader knows how broad the read actually is.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import common
import metals_web

_CFG_PATH = Path(__file__).with_name("metals_config.json")


def _num(x):
    """Return x only if it is a real finite number — filters None, bools and NaN/inf."""
    try:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        return x if math.isfinite(x) else None
    except Exception:
        return None

# --- news-derived metal sentiment (this is what NewsAgent is FOR: MCX/domestic sentiment
#     comes from NEWS search, not a price feed we don't have) -------------------------------
_METAL_KW = ["copper", "aluminium", "aluminum", "zinc", "lead ", "nickel", "base metal", "base-metal",
             "steel", "iron ore", "coking coal", "lme", "mcx", "metal price", "metals price",
             "hindalco", "vedanta", "tata steel", "jsw steel", "nalco", "sail ", "hindustan zinc",
             "jindal steel"]
_METAL_UP = ["rise", "rises", "rose", "rally", "rallied", "surge", "surged", "jump", "jumped",
             "gain", "gains", "gained", "higher", "climb", "strong demand", "robust demand",
             "china stimulus", "stimulus", "supply cut", "output cut", "production cut", "restock",
             "shortage", "deficit", "infrastructure push", "price hike", "hiked prices", "tariff protection",
             "safeguard duty", "import duty hike"]
_METAL_DN = ["fall", "falls", "fell", "slump", "slumped", "drop", "dropped", "decline", "declined",
             "lower", "slide", "weak demand", "soft demand", "glut", "oversupply", "surplus",
             "china slowdown", "property crisis", "demand concern", "price cut", "inventory build",
             "destock", "dumping", "cheap chinese", "export slump"]


# market-moving vs BACKGROUND: broker/app/how-to items ("Groww expands commodity trading") tell
# us nothing about steel fundamentals — filter them out.
_NEWS_EXCLUDE = ["groww", "zerodha", "upstox", "angel one", "demat", "broker", "brokerage",
                 "trading app", "trading platform", "open account", "how to trade", "sip ",
                 "mutual fund", "ipo allotment", "trading account", "referral", "cashback"]
# a metal item is market-moving only if it also carries a FUNDAMENTAL cue (price/demand/supply/China)
_NEWS_CONTEXT = _METAL_UP + _METAL_DN + ["price", "prices", "demand", "supply", "china", "property",
                                         "pmi", "stimulus", "inventory", "output", "duty", "margin",
                                         "export", "import", "tariff", "capacity", "production"]


# generic non-market content that a mis-scraped body can drag in (quote-of-the-day, bios, etc.)
_JUNK = ["a person's work", "values, beliefs", "deeper insight", "quote suggest", "their identity",
         "sign in", "cookie", "subscribe", "newsletter", "read more", "advertisement"]


def _metal_window(text):
    """Return a TIGHT window where a metal term and a market-context word co-occur (same clause),
    or None. Proximity (not 'both somewhere in a long body') kills polluted-body false positives."""
    import re
    pat = re.compile("|".join(re.escape(k) for k in _METAL_KW))
    for m in pat.finditer(text):
        i = m.start()
        win = text[max(0, i - 70): i + 100]
        if not any(c in win for c in _NEWS_CONTEXT):     # a price/demand/China cue must be NEARBY
            continue
        if any(j in win for j in _JUNK):                 # window looks like boilerplate/quote junk
            continue
        win = win.strip(" .,-—|")
        return ("…" + win + "…")[:180]
    return None


def _age_hours(item):
    """Article age in hours, or None if undatable. Handles RSS (RFC-822) + ISO."""
    raw = (item.get("published") or item.get("published_at") or item.get("date") or "").strip()
    if not raw:
        return None
    import datetime as _dt
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(raw)
    except Exception:
        try:
            d = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - d).total_seconds() / 3600.0


def news_sentiment(snap) -> dict | None:
    """Scan the (search-augmented) snapshot news for base-metals cues → the domestic/cyclical read.
    An item counts ONLY if a metal term and a market-context word appear NEAR each other (so a metal
    word buried in an unrelated/mis-scraped body doesn't qualify), and it isn't broker/app/junk."""
    news = snap.get("news", []) or []
    score, bull, bear, ev = 0, 0, 0, []
    n_items, dropped, stale = 0, 0, 0
    for n in news:
        # AGE GATE: the same "metals on fire / base metals rally" pieces were re-counted
        # every run because there was no date check — a days-old rally read as today's.
        # Anything older than 48h is dropped, so the metals read reflects TODAY.
        age = _age_hours(n)
        if age is not None and age > 48.0:
            stale += 1
            continue
        t = common.news_text(n)
        if not any(k in t for k in _METAL_KW):
            continue
        if any(x in t for x in _NEWS_EXCLUDE) or any(j in t for j in _JUNK):
            dropped += 1
            continue
        win = _metal_window(t)                # proximity-checked, junk-filtered window
        if not win:
            dropped += 1                      # metal word present but NOT near market context → skip
            continue
        n_items += 1
        up = any(w in win for w in _METAL_UP)   # direction judged on the RELEVANT window, not the blob
        dn = any(w in win for w in _METAL_DN)
        s = (1 if up else 0) - (1 if dn else 0)
        score += s
        bull += 1 if s > 0 else 0
        bear += 1 if s < 0 else 0
        ev.append(win)
    if not n_items:
        return None
    norm = max(-1.0, min(1.0, score / max(1, n_items)))
    label = "Bullish" if norm > 0.2 else "Bearish" if norm < -0.2 else "Neutral"
    return {"score": round(norm, 2), "label": label, "n_items": n_items, "filtered_out": dropped,
            "bullish": bull, "bearish": bear, "evidence": common.dedupe(ev)[:3]}


def load_config() -> dict:
    try:
        with open(_CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"buckets": {"global": {"weight": 1.0, "components":
                [{"name": "Copper (COMEX)", "from_snapshot": "Copper", "symbol": "HG=F"}]}},
                "thresholds": {"bullish": 0.5, "bearish": -0.5}}


def _snapshot_pct(core, snap, name):
    try:
        return _num(core.ms._pct_of(snap.get("quotes_macro", []) or [], name))
    except Exception:
        return None


def _fetch_pct(core, name, symbol):
    """One guarded fetch via the engine helper (works live; no-ops in an offline sandbox)."""
    if not symbol or os.environ.get("NEWSAGENT_METALS_FETCH", "1") == "0":
        return None
    try:
        rows = core.ms.fetch_quotes({name: symbol}) or []
        for q in rows:
            v = _num(q.get("pct_change"))
            if v is not None:
                return v
    except Exception:
        return None
    return None


def _label(v, thr):
    if v is None:
        return "n/a"
    if v >= thr.get("bullish", 0.5):
        return "Bullish"
    if v <= thr.get("bearish", -0.5):
        return "Bearish"
    return "Neutral"


def compute(core, snap, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    thr = cfg.get("thresholds", {"bullish": 0.5, "bearish": -0.5})
    buckets_cfg = cfg.get("buckets", {})
    ws = cfg.get("web_source") or {}

    # one keyless web pull for every anchor across all buckets (static HTML, day-%)
    anchors = [c["anchor"] for b in buckets_cfg.values() for c in b.get("components", []) if c.get("anchor")]
    web_moves = metals_web.fetch_commodity_moves(anchors, ws.get("url")) if ws.get("enabled", True) else {}
    web_used = any(_num(v) is not None for v in web_moves.values())

    components, bucket_avgs, bucket_wts = [], {}, {}
    for bkey, b in buckets_cfg.items():
        wt = b.get("weight", 0.0)
        vals = []
        for c in b.get("components", []):
            name = c.get("name", "?")
            pct, src = None, "n/a"
            a = c.get("anchor")
            if a and _num(web_moves.get(a)) is not None:          # 1) live web (tradingeconomics)
                pct, src = _num(web_moves[a]), "web(TE)"
            if pct is None and c.get("from_snapshot"):            # 2) engine snapshot (copper)
                pct = _snapshot_pct(core, snap, c["from_snapshot"])
                if pct is not None:
                    src = "snapshot"
            if pct is None and c.get("symbol"):                   # 3) yfinance fallback
                pct = _fetch_pct(core, name, c["symbol"])
                if pct is not None:
                    src = "fetch"
            components.append({"name": name, "bucket": bkey, "pct": pct,
                               "available": pct is not None, "source": src, "role": c.get("role", "")})
            if pct is not None:
                vals.append(pct)
        if vals:
            bucket_avgs[bkey] = round(sum(vals) / len(vals), 3)
            bucket_wts[bkey] = wt

    # composite = weight-renormalised average over buckets that actually have data
    composite = None
    if bucket_wts:
        tot = sum(bucket_wts.values()) or 1.0
        composite = round(sum(bucket_avgs[k] * (bucket_wts[k] / tot) for k in bucket_wts), 3)

    news = news_sentiment(snap)                                   # market-moving metal news read

    n_avail = sum(1 for c in components if c["available"])
    n_total = len(components)
    src_tag = "web (tradingeconomics)" if web_used else ("engine tape" if bucket_avgs else "none")
    coverage = f"{n_avail}/{n_total} metals via {src_tag}" + (" + metal news" if news else "")

    # overall sentiment: blend the price composite with the news read (each in its own space)
    tape_label = _label(composite, thr)
    overall = tape_label
    if news:
        if composite is None:
            overall = news["label"]
        elif tape_label != "Neutral" and news["label"] != "Neutral" and tape_label != news["label"]:
            overall = "Mixed (tape vs news diverge)"
        elif tape_label == "Neutral":
            overall = news["label"]

    note = None
    if not web_used:
        note = ("Live metals web-pull unavailable (offline or source blocked) — showing engine copper "
                "+ metal news only. Values populate on a networked run; set NEWSAGENT_METALS_WEB=0 to disable.")

    return {
        "composite": composite,
        "label": tape_label,
        "overall_label": overall,
        "news": news,
        "bucket_avgs": bucket_avgs,
        "components": components,
        "coverage": coverage,
        "n_available": n_avail, "n_total": n_total,
        "web_used": web_used,
        "note": note,
        "tag": "PRIOR",
    }
