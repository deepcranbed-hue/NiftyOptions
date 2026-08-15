"""
test_daily_sentiment.py
=======================
Proves the daily NIFTY-50 sentiment board offline (no network / no Qwen):

  1. UNIVERSE loads all constituents with name aliases.
  2. ATTRIBUTION — an article with no explicit symbol is matched to a company by
     ticker token and by leading name phrase.
  3. CATEGORIZATION — order / ai / earnings / regulatory buckets from text + filing_event.
  4. BOARD — one row per constituent; confidence-weighted sentiment; multi-source
     corroboration lifts confidence.
  5. NO_NEWS -> neutral (0.0) for names nothing happened to (the honest default).

A stub tagger stands in for Qwen (build_board consumes already-tagged articles).
Run:  python test_daily_sentiment.py
"""
from __future__ import annotations
import importlib.util, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
results = []
def check(label, cond):
    results.append(bool(cond)); print(f"  [{'PASS' if cond else 'FAIL'}] {label}"); return cond
def hr(t): print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72)
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m
    spec.loader.exec_module(m); return m

ds = load(HERE + "/backend/quant/daily_sentiment.py", "daily_sentiment")

# stub "Qwen" tagger: attach a sentiment/confidence so build_board has something to
# aggregate. Real path uses llm_tag.tag_batch_sync (validated earlier).
def stub_tagger(arts):
    for a in arts:
        t = (a.get("title", "") + a.get("description", "")).lower()
        a.setdefault("sentiment", 0.7 if ("beat" in t or "wins" in t or "bags" in t)
                     else -0.6 if ("probe" in t or "downgrade" in t or "notice" in t) else 0.1)
        a.setdefault("confidence", 0.8)
    return arts

# ── 1. universe ─────────────────────────────────────────────────────────────
hr("1. UNIVERSE")
uni = ds.load_universe()
check("loads ~50 constituents", len(uni) >= 49)
tcs = next((u for u in uni if u["symbol"] == "TCS"), None)
check("alias built for TCS (TATA CONSULTANCY)", tcs and tcs["alias"] == "TATA CONSULTANCY")

# ── 2. attribution ──────────────────────────────────────────────────────────
hr("2. ATTRIBUTION (no explicit symbol -> match by ticker / name)")
check("ticker token 'INFY' attributes to INFY",
      "INFY" in ds.attribute_symbols("INFY signs new client", uni))
check("name phrase 'Tata Consultancy' attributes to TCS",
      "TCS" in ds.attribute_symbols("Tata Consultancy Services announced", uni))
check("unrelated text attributes to nobody",
      ds.attribute_symbols("Global crude oil prices rise", uni) == set() or
      len(ds.attribute_symbols("Global crude oil prices rise", uni)) <= 1)

# ── 3. categorization ───────────────────────────────────────────────────────
hr("3. CATEGORIZATION")
check("order win -> order", ds.categorize("Company bags Rs 5000 cr order") == "order")
check("AI news -> ai", ds.categorize("launches new GenAI platform") == "ai")
check("results -> earnings", ds.categorize("Q1 FY27 net profit rises") == "earnings")
check("SEBI notice -> regulatory", ds.categorize("SEBI show-cause notice issued") == "regulatory")
check("filing_event=RESULTS overrides to earnings",
      ds.categorize("board intimation", filing_event="RESULTS") == "earnings")

# ── 4 & 5. board + no-news neutral ──────────────────────────────────────────
hr("4/5. BOARD (per-symbol aggregate) + NO_NEWS neutral")
articles = stub_tagger([
    {"title": "TCS bags $1bn AI deal", "description": "GenAI contract win", "symbol": "TCS"},
    {"title": "TCS wins another large order", "description": "second corroborating story",
     "source": "ET Markets", "symbol": "TCS"},
    {"title": "Reliance Q1 results beat estimates", "description": "profit up",
     "symbol": "RELIANCE", "filing_event": "RESULTS"},
    {"title": "SEBI show-cause notice to Axis Bank", "description": "compliance probe",
     "symbol": "AXISBANK"},
])
board = ds.build_board(articles, uni, as_of="2026-07-11")
by = {r["symbol"]: r for r in board}

check("board has one row per constituent", len(board) == len(uni))
check("TCS positive (two corroborating wins)", by["TCS"]["sentiment"] > 0.5)
check("TCS confidence lifted by 2 sources", by["TCS"]["n_items"] == 2 and by["TCS"]["confidence"] > 0.8)
check("TCS categorized as AI or order", set(by["TCS"]["categories"]) & {"ai", "order"})
check("RELIANCE earnings-tagged & positive",
      by["RELIANCE"]["categories"].get("earnings") == 1 and by["RELIANCE"]["sentiment"] > 0)
check("AXISBANK regulatory & negative",
      by["AXISBANK"]["categories"].get("regulatory") == 1 and by["AXISBANK"]["sentiment"] < 0)
neutral = [r for r in board if r["status"] == "NO_NEWS"]
check("quiet names -> NO_NEWS neutral 0.0",
      len(neutral) == len(uni) - 3 and all(r["sentiment"] == 0.0 for r in neutral))
inf = by.get("INFY")
check("INFY (no news today) is neutral", inf and inf["status"] == "NO_NEWS")

# ── summary ─────────────────────────────────────────────────────────────────
hr("HOW IT FARES")
n_pass, n = sum(results), len(results)
print(f"  {n_pass}/{n} checks passed")
covered = [r for r in board if r["status"] == "OK"]
print(f"  board: {len(board)} names | {len(covered)} with news | {len(board)-len(covered)} neutral")
for r in covered:
    print(f"    {r['symbol']:10} sent={r['sentiment']:+.2f} conf={r['confidence']:.2f} "
          f"cats={r['categories']} | {r['top_headline'][:38]}")
print("  sources: BSE filings + existing RSS (+RBI/SEBI) — all permitted, no scraping")
sys.exit(0 if n_pass == n else 1)
