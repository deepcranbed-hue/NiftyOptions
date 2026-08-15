"""
impact_monitor.py
=================
Live "impact decipher" agent for the NIFTY desk. Every poll it:
  1. gathers current NEWS headlines + live CROSS-ASSET moves (crude, USDINR, GIFT,
     KOSPI/tech, gold),
  2. deciphers them into the CHAIN + BALANCE model:
        - chains  : cause -> effect cascades (oil -> rupee ; AI/Korea -> EM risk-off -> rupee)
        - drivers : each signed (+ bullish / - bearish) with magnitude + priced-in-vs-new
        - tilt    : the NET of the competing forces (-1..+1)
        - impact  : each headline ranked HIGH/MED/LOW (HIGH = flash at top)
  3. returns a short summary + the flagged items for the top banner.

Deciphering uses the LLM configured in `llm_config.py` — by default the ACTIVE
provider is `local` = **qwen2.5:7b via Ollama** (http://localhost:11434), with the
config's fallback chain (local -> gemini) on failure; if every provider fails a
deterministic rule-based classifier runs so the monitor NEVER blocks.
The DECIPHER_PROMPT below is the tunable "agent brain" — edit it to change behaviour.
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
import time
from typing import Optional

# ── the agent prompt (tune this) ──────────────────────────────────────────────
DECIPHER_PROMPT = """You are a markets desk analyst for the Indian NIFTY 50 index.
Decipher the CURRENT situation into a structured impact read. You are given
(a) recent NEWS headlines and (b) live CROSS-ASSET moves.

Apply this model:
1. CHAINS — identify cause->effect cascades. Examples: oil spike -> import bill ->
   rupee weak -> financials; AI/chip fear -> EM risk-off -> dollar bid -> rupee/IT.
   Multiple chains can run in parallel and often CONVERGE on USDINR.
2. BALANCE — list each DRIVER with a SIGN for NIFTY (+1 bullish / -1 bearish) and a
   magnitude 0..1. Drivers can FLIP sign day to day (AI can be + on a chip rebound,
   - on a peak-fear day). Net the signed magnitudes into an overall tilt -1..+1.
3. PRICED-IN vs NEW — mark whether each driver is already priced (old) or NEW/overnight
   (fresh — matters for the NEXT move; yesterday's crude close is already priced).
4. IMPACT RANK — rank each news item HIGH / MED / LOW by likely NIFTY impact.
   HIGH items get flashed at the top.

5. THEMES — report the big market NARRATIVES the Indian market is watching right NOW.
   Always cover this standard set: AI / Semiconductors, US Rates / Fed, Earnings Season,
   Oil / Geopolitics, FII / DII Flows, Rupee / USD, China / EM, Global Risk — AND add any
   OTHER theme currently dominating the headlines. For each: status HOT (top current driver)
   / WARM (in play) / QUIET (dormant), sign for NIFTY (+1/-1/0), a one-line "what's driving
   it now", and priced vs new.

Return ONLY valid JSON, no prose:
{
 "summary": "<=40-word plain net read",
 "tilt": <float -1..1>,
 "tilt_label": "BULLISH|BEARISH|MIXED|NEUTRAL",
 "themes": [{"theme": "...", "status": "HOT|WARM|QUIET", "sign": 1 or -1 or 0, "priced": "priced|new", "note": "what's driving it now"}],
 "drivers": [{"name": "...", "sign": 1 or -1, "magnitude": 0..1, "priced": "priced|new", "note": "..."}],
 "chains": [["cause","effect","..."], ...],
 "impact_items": [{"headline": "...", "impact": "HIGH|MED|LOW", "direction": "+|-|0", "driver": "...", "flash": true|false}]
}

NEWS:
%(news)s

CROSS-ASSET (latest %% moves; + = up):
%(reads)s
"""

_HIGH_KW = ("iran", "hormuz", "crude", "oil", "war", "strike", "ceasefire", "fed",
            "rate", "inflation", "cpi", "ai ", "chip", "semiconductor", "kospi",
            "tariff", "downgrade", "rupee", "fii", "selloff", "crash", "gap")

# ── DISPLAY PRIORITY ─────────────────────────────────────────────────────────
# _HIGH_KW is binary: any hit -> HIGH, and everything HIGH sorts together. That means
# a Hormuz escalation and a passing "tariff" mention compete for the same banner slot
# on equal terms. Only ~5 items are ever flashed, so ORDER decides what the desk sees.
#
# Explicit tiers instead:
#   0  OIL / Middle-East      — drives the index via inflation -> RBI -> rate-sensitives
#   1  NIFTY HEAVYWEIGHTS     — the names that mechanically move the index
#   2  other macro (Fed/CPI/AI/rupee/FII)
#   3  everything else
# Within the heavyweight tier, ordering is by INDEX WEIGHT — a Reliance headline
# outranks a Maruti one because it moves the index ~5x as much.
_OIL_KW = ("oil", "crude", "brent", "wti", "opec", "iran", "hormuz", "tanker",
           "refinery", "petrol", "diesel", "ceasefire", "israel", "red sea", "houthi")

# Nifty top-15 by index weight (68.7% of the index). Weight doubles as the intra-tier
# rank. Keep in sync with nifty-50-stock-list.csv if NSE rebalances.
_HEAVYWEIGHTS = {
    "hdfc bank": 11.6, "reliance": 9.2, "icici": 8.3, "infosys": 5.5,
    "bharti": 4.3, "airtel": 4.3, "tcs": 4.0, "tata consultancy": 4.0,
    "itc": 3.9, "larsen": 3.6, "l&t": 3.6, "state bank": 3.2, "sbi": 3.2,
    "axis bank": 3.1, "kotak": 2.9, "hindustan unilever": 2.6, "hul": 2.6,
    "bajaj finance": 2.4, "mahindra": 2.2, "maruti": 1.9,
}


def _priority(txt: str):
    """(tier, -weight, ) sort key + the tier's label. Lower tier = shown first."""
    if any(k in txt for k in _OIL_KW):
        return 0, 0.0, "oil/geopolitics"
    hits = [w for n, w in _HEAVYWEIGHTS.items() if n in txt]
    if hits:
        return 1, -max(hits), "heavyweight"
    if any(k in txt for k in _HIGH_KW):
        return 2, 0.0, "macro"
    return 3, 0.0, "other"

# Market NARRATIVES the desk watches. (keyword lexicon, linked cross-asset symbol or None)
THEMES = {
    "AI / Semiconductors": (["ai ", "a.i", "artificial intelligence", "chip", "semiconductor",
                             "nvidia", "tsmc", "kospi", "hynix", "capex"], "KOSPI"),
    "US Rates / Fed": (["fed", "fomc", "rate hike", "rate cut", "powell", "treasury", "yield",
                        "cpi", "inflation", "payroll", "jobs"], None),
    "Earnings Season": (["earnings", "results", " q1", " q2", " q3", " q4", "profit", "beat",
                         "miss", "guidance"], None),
    "Oil / Geopolitics": (["oil", "crude", "brent", "opec", "iran", "hormuz", "war", "strike",
                           "israel", "middle east"], "CRUDEOIL"),
    "FII / DII Flows": (["fii", "fpi", "dii", "outflow", "inflow", "institutional"], None),
    "Rupee / USD": (["rupee", "usdinr", "dollar", "currency", "forex"], "USDINR"),
    "China / EM": (["china", "yuan", "hang seng", "emerging market"], None),
    "Global Risk": (["wall street", "nasdaq", "s&p", "dow", "risk-off", "selloff", "volatility", "vix"], None),
}
_THEME_POS = ("beat", "surge", "rally", "rebound", "gains", "ceasefire", "optimism", "progress", "talks", "cut", "ease")
_THEME_NEG = ("miss", "fall", "crash", "fear", "war", "strike", "hike", "selloff", "tumble", "plunge", "outflow", "weak", "spike", "tension")

# driver sign rules for the fallback: (symbol, sign_if_up, scale_for_full_magnitude)
_DRIVER_RULES = [
    ("CRUDEOIL", -1, 4.0),   # crude up = bearish (import shock)
    ("USDINR",   -1, 0.5),   # rupee weak (USDINR up) = bearish
    ("GIFTNIFTY", 1, 0.7),   # GIFT up = bullish next
    ("KOSPI",     1, 3.0),   # Korea up = risk-on (down = AI/EM fear)
    ("GOLD",     -1, 1.5),   # gold up = risk-off tilt (mild)
]


# ── data gathering ────────────────────────────────────────────────────────────
def _day_move(db_path: str, sym: str) -> Optional[float]:
    """Latest % move for a symbol: last two 1d closes, else last vs prior-day 1m close."""
    try:
        con = sqlite3.connect(db_path)
        r = con.execute("SELECT close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                        "ORDER BY ts DESC LIMIT 2", (sym,)).fetchall()
        if len(r) < 2:
            r = con.execute("SELECT close FROM price_bars WHERE symbol=? AND timeframe='1m' "
                            "ORDER BY ts DESC LIMIT 2", (sym,)).fetchall()
        con.close()
        if len(r) == 2 and r[1][0]:
            return round((r[0][0] / r[1][0] - 1.0) * 100.0, 3)
    except Exception:
        return None
    return None


def gather_situation(db_path: Optional[str], news_state: Optional[dict]) -> dict:
    reads = {}
    if db_path:
        for sym, _, _ in _DRIVER_RULES:
            m = _day_move(db_path, sym)
            if m is not None:
                reads[sym] = m
    headlines = []
    if news_state:
        for a in (news_state.get("articles") or [])[:25]:
            t = a.get("title") or a.get("headline") or a.get("text")
            if t:
                headlines.append({"headline": t, "symbol": a.get("symbol"),
                                  "sentiment": a.get("sentiment")})
    return {"reads": reads, "headlines": headlines}


_ASSET_SIGN_UP = {sym: s for sym, s, _ in [("CRUDEOIL", -1, 4.0), ("USDINR", -1, 0.5),
                                           ("GIFTNIFTY", 1, 0.7), ("KOSPI", 1, 3.0), ("GOLD", -1, 1.5)]}


def _extract_themes(sit: dict) -> list:
    """Which narratives is the market on right now? Scans headlines for each theme's
    lexicon + its linked cross-asset move; returns status/sign/note, HOT first."""
    reads = sit["reads"]
    heads = [(h["headline"] or "").lower() for h in sit["headlines"]]
    blob = " ".join(heads)
    out = []
    for name, (kw, asset) in THEMES.items():
        hits = sum(blob.count(k) for k in kw)
        move = reads.get(asset) if asset else None
        active = hits > 0 or (move is not None and abs(move) >= 0.3)
        status = ("HOT" if (hits >= 2 or (move is not None and abs(move) >= 1.0))
                  else "WARM" if active else "QUIET")
        # sign: prefer the linked asset's NIFTY sign; else infer from pos/neg words in matching heads
        sign = 0
        if move is not None and abs(move) >= 0.1:
            su = _ASSET_SIGN_UP.get(asset, 0)
            sign = su if move >= 0 else -su
        else:
            matched = " ".join(h for h in heads if any(k in h for k in kw))
            p = sum(matched.count(w) for w in _THEME_POS)
            n = sum(matched.count(w) for w in _THEME_NEG)
            sign = 1 if p > n else -1 if n > p else 0
        note = (f"{asset} {move:+.2f}%" if move is not None else
                (next((h["headline"] for h in sit["headlines"]
                       if any(k in (h["headline"] or "").lower() for k in kw)), "—")))
        out.append({"theme": name, "status": status, "sign": sign,
                    "priced": "priced", "note": note[:90], "hits": hits})
    rank = {"HOT": 0, "WARM": 1, "QUIET": 2}
    out.sort(key=lambda t: (rank[t["status"]], -t["hits"]))
    return out


# ── rule-based fallback ────────────────────────────────────────────────────────
def _rule_based(sit: dict) -> dict:
    reads = sit["reads"]
    drivers = []
    tilt = 0.0
    for sym, sign_up, scale in _DRIVER_RULES:
        pct = reads.get(sym)
        if pct is None:
            continue
        sign = sign_up if pct >= 0 else -sign_up
        mag = min(1.0, abs(pct) / scale)
        if mag < 0.05:
            continue
        drivers.append({"name": sym, "sign": sign, "magnitude": round(mag, 2),
                        "priced": "priced", "note": f"{sym} {pct:+.2f}%"})
        tilt += sign * mag
    tilt = max(-1.0, min(1.0, tilt / 2.0))
    label = ("BULLISH" if tilt > 0.2 else "BEARISH" if tilt < -0.2
             else "MIXED" if drivers else "NEUTRAL")

    items = []
    for h in sit["headlines"]:
        txt = (h["headline"] or "").lower()
        tier, negw, cat = _priority(txt)
        # FLASH IS SCARCE ON PURPOSE. It sets the red alert border and the
        # "N high-impact" count, and only ~5 items fit in the banner. Previously any
        # _HIGH_KW hit flashed, so a routine cement result and a Hormuz escalation
        # both lit the alert — and an alert that fires on everything conveys nothing.
        # Only tier 0 (oil/geopolitics) and tier 1 (index heavyweights) flash now;
        # macro still ranks above noise but does not raise the alarm.
        impact = ("HIGH" if tier <= 1 else "MED" if tier == 2 else "LOW")
        direction = ("-" if any(w in txt for w in ("fall", "crash", "fear", "war", "strike", "selloff", "downgrade"))
                     else "+" if any(w in txt for w in ("rally", "beat", "surge", "rebound", "ceasefire", "gains"))
                     else "0")
        items.append({"headline": h["headline"], "impact": impact, "direction": direction,
                      "driver": None, "flash": impact == "HIGH",
                      # exposed so the banner/desk can see WHY an item ranked where it did
                      "category": cat, "priority": tier})
    # oil first, then heavyweights (heaviest first), then macro, then everything else
    items.sort(key=lambda x: (x["priority"],
                              _priority((x["headline"] or "").lower())[1]))

    # a couple of canonical chains, shown only if the relevant driver moved
    chains = []
    if reads.get("CRUDEOIL", 0) > 0:
        chains.append(["Crude up", "import bill / inflation", "rupee weak (USDINR up)", "financials & index pressure"])
    if reads.get("KOSPI", 0) < 0:
        chains.append(["AI / chip fear (KOSPI down)", "EM risk-off", "dollar bid → rupee weak", "IT & index pressure"])

    summary = (f"Net {label.lower()} (tilt {tilt:+.2f}). "
               + (f"{len([i for i in items if i['impact']=='HIGH'])} high-impact headline(s). " if items else "")
               + (", ".join(f"{d['name']} {d['note'].split()[-1]}" for d in drivers[:3]) if drivers else "quiet cross-asset tape."))
    return {"summary": summary[:240], "tilt": round(tilt, 2), "tilt_label": label,
            "themes": _extract_themes(sit),
            "drivers": drivers, "chains": chains, "impact_items": items[:12],
            "engine": "rule-based"}


# ── LLM decipher (provider-agnostic, driven by llm_config) ──────────────────────
_SYSTEM = ("You are a markets desk analyst for the Indian NIFTY 50. "
           "Return ONLY valid JSON exactly as instructed — no markdown, no prose, no code fences.")


def _extract_json(txt: str) -> dict:
    m = re.search(r"\{.*\}", txt, re.S)               # strip any ``` fences / prose
    return json.loads(m.group(0) if m else txt)


def _call_provider(p, prompt: str) -> str:
    """Call one configured LLM provider (Gemini contents[] or OpenAI/Qwen/Ollama
    messages[]) and return the raw text. Timeout is generous for a local 7B model."""
    import httpx
    key = os.getenv(p.api_key_env) if p.api_key_env else None
    headers = dict(p.extra_headers or {})
    if p.request_style == "gemini":
        url = p.endpoint + (f"?key={key}" if p.auth_style == "query_key" and key else "")
        body = {"contents": [{"parts": [{"text": _SYSTEM + "\n\n" + prompt}]}]}
        r = httpx.post(url, json=body, headers=headers, timeout=90)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    # openai-compatible (Qwen / Ollama local / OpenRouter)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": p.model, "stream": False,
            "messages": [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": prompt}]}
    r = httpx.post(p.endpoint, json=body, headers=headers, timeout=90)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _llm_decipher(sit: dict) -> Optional[dict]:
    """Decipher via the ACTIVE provider in llm_config (default: local qwen2.5:7b via
    Ollama), walking the fallback chain on failure. Returns None if all providers fail."""
    try:
        from .llm_config import fallback_providers
        provs = fallback_providers()
    except Exception:
        return None
    news = "\n".join(f"- {h['headline']}" for h in sit["headlines"][:25]) or "(none)"
    reads = "\n".join(f"- {k}: {v:+.2f}%" for k, v in sit["reads"].items()) or "(none)"
    prompt = DECIPHER_PROMPT % {"news": news, "reads": reads}
    for p in provs:
        try:
            data = _extract_json(_call_provider(p, prompt))
            data["engine"] = f"{p.name}:{p.model}"
            return data
        except Exception:
            continue
    return None


# ── public entry ────────────────────────────────────────────────────────────────
def run(db_path: Optional[str], news_state: Optional[dict], use_llm: bool = False) -> dict:
    sit = gather_situation(db_path, news_state)
    out = _llm_decipher(sit) if use_llm else None
    if not out:
        out = _rule_based(sit)
    if not out.get("themes"):            # LLM omitted themes -> backfill deterministically
        out["themes"] = _extract_themes(sit)
    out["as_of"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out["reads"] = sit["reads"]
    return out
