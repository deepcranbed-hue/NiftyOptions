"""
daily_sentiment.py
==================
The daily NIFTY-50 sentiment board. Once a day it builds ONE sentiment row per
constituent from PERMITTED, non-scraped sources only:

  * BSE corporate filings   (fetch_filings) — authoritative per-company events:
                              orders/contracts (Reg 30), earnings, ratings, regulatory
  * your existing RSS        (fetch_rss)      — broad market press / "floating around" buzz,
                              attributed to companies via the Qwen tagger's entity extraction
  * (optional) RBI/SEBI RSS  — the government/regulatory bucket

WHAT THIS IS HONEST ABOUT
-------------------------
BSE alone = official material disclosures, NOT every rumor/analyst note. The RSS
layer adds press buzz but per-company coverage is partial. So on a quiet day a
name legitimately gets NO_NEWS -> sentiment 0.0 (neutral). That is the correct
answer for a signal, not a gap. Fuller per-company buzz would need a licensed
news API (deliberately out of scope on cost).

OUTPUT: list[dict], one per symbol:
  {date, symbol, sentiment[-1..1], confidence[0..1], status, n_items,
   categories:{order/ai/earnings/regulatory/other counts}, top_headline}

The heavy tagger (Qwen via llm_tag.tag_batch_sync) is INJECTED, so this file's
aggregation logic tests offline with a stub.
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CSV = os.path.join(_ROOT, "nifty-50-stock-list.csv")

# ── universe -----------------------------------------------------------------
_SUFFIX = re.compile(r"\b(ltd|limited|corporation|industries|company|enterprise|"
                     r"enterprises|india|indian|of|the|&|and)\b\.?", re.I)


def load_universe(csv_path: str = _CSV) -> list[dict]:
    """[{symbol, name, sector, alias}] — alias is a short name key for text matching."""
    out = []
    if not os.path.exists(csv_path):
        return out
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            sym = (r.get("Symbol") or "").strip()
            name = (r.get("Company Name") or "").strip()
            if not sym:
                continue
            core = _SUFFIX.sub(" ", name)
            alias = " ".join(core.split()[:2]).upper()   # first 2 meaningful words
            out.append({"symbol": sym, "name": name,
                        "sector": (r.get("Sector") or "").strip(), "alias": alias})
    return out


def attribute_symbols(text: str, universe: list[dict]) -> set[str]:
    """Fallback attribution when the tagger gave no constituents: match ticker token
    or the company's leading name phrase. Conservative to avoid false hits."""
    up = f" {text.upper()} "
    hits = set()
    for u in universe:
        if re.search(rf"\b{re.escape(u['symbol'])}\b", up):
            hits.add(u["symbol"])
        elif len(u["alias"]) >= 5 and u["alias"] in up:
            hits.add(u["symbol"])
    return hits


# ── event category (order / ai / earnings / regulatory) ----------------------
_CATS = [
    ("earnings",   re.compile(r"\b(result|results|profit|revenue|PAT|EPS|earnings|"
                              r"quarter|Q[1-4]\s?FY|net income|topline|bottomline)\b", re.I)),
    ("order",      re.compile(r"\b(order|orders|contract|bags?|awarded|wins?|won|deal|"
                              r"LoI|letter of intent|purchase order|tender|mandate)\b", re.I)),
    ("ai",         re.compile(r"\b(artificial intelligence|\bAI\b|GenAI|generative|"
                              r"machine learning|\bLLM\b|data ?cent(er|re)|chip|semiconductor)\b", re.I)),
    ("regulatory", re.compile(r"\b(SEBI|RBI|regulator|penalty|probe|investigation|ban|"
                              r"show[- ]cause|notice|compliance|GST|tax demand|CCI|fine|"
                              r"scrutiny|sanction)\b", re.I)),
]
_FILING_EVENT_CAT = {"RESULTS": "earnings", "BOARD_MEETING": "earnings", "MNA": "order",
                     "RATING": "regulatory", "DIVIDEND": "earnings", "BUYBACK": "earnings",
                     "AGM": "other"}


def categorize(text: str, filing_event: str | None = None) -> str:
    if filing_event and filing_event in _FILING_EVENT_CAT:
        return _FILING_EVENT_CAT[filing_event]
    for name, pat in _CATS:
        if pat.search(text or ""):
            return name
    return "other"


# ── the board -----------------------------------------------------------------
def _symbols_for(art: dict, universe: list[dict]) -> set[str]:
    syms = set()
    if art.get("symbol"):
        syms.add(art["symbol"])
    for c in art.get("constituents", []) or []:       # from the Qwen tagger's entity pass
        if c.get("symbol"):
            syms.add(c["symbol"])
    if not syms:
        syms = attribute_symbols(f"{art.get('title','')} {art.get('description','')}", universe)
    return syms


def build_board(tagged_articles: list[dict], universe: list[dict] | None = None,
                as_of: str | None = None) -> list[dict]:
    """Aggregate ALREADY-TAGGED articles (each carries sentiment/confidence, and
    ideally 'constituents') into one row per constituent. Pure + offline-testable."""
    universe = universe or load_universe()
    as_of = as_of or datetime.now(timezone.utc).date().isoformat()
    bucket: dict[str, list[dict]] = defaultdict(list)

    for art in tagged_articles:
        text = f"{art.get('title','')} {art.get('description','')}"
        cat = categorize(text, art.get("filing_event"))
        s = float(art.get("sentiment", 0.0) or 0.0)
        c = float(art.get("confidence", 0.0) or 0.0)
        for sym in _symbols_for(art, universe):
            bucket[sym].append({"cat": cat, "sent": s, "conf": c,
                                "title": art.get("title", ""), "source": art.get("source", "")})

    rows = []
    for u in universe:
        items = bucket.get(u["symbol"], [])
        if not items:
            rows.append({"date": as_of, "symbol": u["symbol"], "sector": u["sector"],
                         "sentiment": 0.0, "confidence": 0.0, "status": "NO_NEWS",
                         "n_items": 0, "categories": {}, "top_headline": None})
            continue
        wsum = sum(i["conf"] for i in items)
        sent = (sum(i["sent"] * i["conf"] for i in items) / wsum) if wsum else \
               (sum(i["sent"] for i in items) / len(items))
        conf = min(1.0, wsum / len(items) * (1 + 0.1 * (len(items) - 1)))  # more corroboration -> more confidence
        cats: dict[str, int] = defaultdict(int)
        for i in items:
            cats[i["cat"]] += 1
        top = max(items, key=lambda i: i["conf"])
        rows.append({"date": as_of, "symbol": u["symbol"], "sector": u["sector"],
                     "sentiment": round(sent, 3), "confidence": round(conf, 3),
                     "status": "OK", "n_items": len(items), "categories": dict(cats),
                     "top_headline": top["title"]})
    return rows


# ── production entry (live sources + real Qwen tagger) -----------------------
def _default_tagger(articles: list[dict]) -> list[dict]:
    from .llm_tag import tag_batch_sync          # Qwen 2.5 7B local, per llm_config
    return tag_batch_sync(articles)


def run_daily(use_llm: bool = False, write: bool = True) -> list[dict]:
    """Fetch permitted sources, tag with Qwen, build + persist the 50-name board."""
    from .filings import fetch_filings
    from .rss_news import fetch_rss
    articles = []
    try:
        articles += fetch_rss()
    except Exception as e:
        print(f"  [daily] rss failed: {e}")
    try:
        articles += fetch_filings()
    except Exception as e:
        print(f"  [daily] filings failed: {e}")

    tagged = _default_tagger(articles) if use_llm else articles
    board = build_board(tagged)

    covered = sum(1 for r in board if r["status"] == "OK")
    print(f"  [daily] {covered}/{len(board)} names with news; "
          f"{len(board) - covered} neutral (NO_NEWS)")
    if write:
        path = os.path.join(_ROOT, f"daily_sentiment_{board[0]['date']}.json")
        with open(path, "w") as f:
            json.dump(board, f, indent=2, ensure_ascii=False)
        print(f"  [daily] wrote {path}")
    return board


if __name__ == "__main__":
    # offline demo: hand-tagged articles -> board (no network / no Qwen needed)
    uni = load_universe()
    demo = [
        {"title": "TCS bags $1bn AI deal", "description": "large GenAI contract win",
         "symbol": "TCS", "sentiment": 0.8, "confidence": 0.85},
        {"title": "Reliance Q1 results beat estimates", "description": "net profit up",
         "symbol": "RELIANCE", "sentiment": 0.6, "confidence": 0.8, "filing_event": "RESULTS"},
        {"title": "SEBI issues show-cause notice to a bank", "description": "compliance probe",
         "symbol": "AXISBANK", "sentiment": -0.5, "confidence": 0.7},
    ]
    board = build_board(demo, uni)
    shown = [r for r in board if r["status"] == "OK"]
    print(f"universe={len(uni)}  with_news={len(shown)}  neutral={len(board)-len(shown)}")
    for r in shown:
        print(f"  {r['symbol']:10} sent={r['sentiment']:+.2f} conf={r['confidence']:.2f} "
              f"cats={r['categories']} | {r['top_headline'][:40]}")
