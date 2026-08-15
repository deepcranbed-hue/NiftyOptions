"""
extract.py — numeric fundamentals parser for crawled text.

Turns the crawler's headlines + full-article text into STRUCTURED metrics with exact
values, not just direction:

    "HDFC Bank Q1: NIM expanded to 3.6%, deposits grew 15% YoY, GNPA fell to 1.2%,
     provisions of ₹1,200 cr; PAT rose 12%"
  ->
    [ {metric:"NIM", value:3.6, unit:"%", direction:"up", ...},
      {metric:"Deposit growth", value:15, unit:"%", direction:"up", ...},
      {metric:"GNPA", value:1.2, unit:"%", direction:"down", ...},
      {metric:"Provisions", value:1200, unit:"cr", ...},
      {metric:"PAT growth", value:12, unit:"%", direction:"up", ...} ]

Pure regex + direction cues — deterministic, no LLM, no network. It reads whatever text the
snapshot carries (title + tags, and full-article body/summary/fulltext when present). An
optional helper can enrich items with market_scan.fetch_article to get the full body first.
"""
from __future__ import annotations

import re

# direction cue words
_UP = ["expand", "expanded", "rose", "grew", "grow", "up ", "jump", "improve", "improved",
       "higher", "beat", "strong", "surge", "accretion", "rise", "increased", "increase"]
_DOWN = ["compress", "compressed", "fell", "decline", "declined", "down ", "drop", "dropped",
         "miss", "lower", "deteriorat", "weak", "slip", "contracted", "contract", "fall", "shr—", "decreased"]


import common

def _text(n: dict) -> str:
    return common.news_text(n)


def _dir_near(text: str, start: int, end: int) -> str | None:
    """Direction from the whole CLAUSE containing the keyword (bounded by . ; ,), so a cue
    that sits BEFORE the keyword ('Microsoft CUTS capex') is caught, while a neighbouring
    metric's cue in another clause does not leak in."""
    lo = max((text.rfind(c, 0, start) for c in ".;,"), default=-1) + 1
    rights = [p for p in (text.find(c, end) for c in ".;,") if p != -1]
    hi = min(rights) if rights else len(text)
    seg = text[lo:hi].lower()
    up = any(w in seg for w in _UP)
    dn = any(w in seg for w in _DOWN)
    if up and not dn:
        return "up"
    if dn and not up:
        return "down"
    return None


# metric -> (list of keyword patterns, value regex builder, unit)
# value regex captures the number; we search for keyword then a nearby number.
_PCT = r"(\d+(?:\.\d+)?)\s*%"
_CR = r"₹?\s*([\d,]+(?:\.\d+)?)\s*(?:cr\b|crore)"
_BPS = r"(\d+(?:\.\d+)?)\s*bps"
_USD_BN = r"\$?\s*([\d,]+(?:\.\d+)?)\s*(?:billion|bn)\b"

# each: (metric_label, keywords, value_regex, unit, quality_sign)
# quality_sign: +1 means "up is good for the stock", -1 means "up is bad" (e.g. GNPA, provisions)
_SPECS = [
    ("NIM", [r"net interest margin", r"\bnim\b"], [_PCT, _BPS], "%", +1),
    ("CASA", [r"\bcasa\b"], [_PCT], "%", +1),
    ("Deposit growth", [r"deposits?"], [_PCT], "%", +1),
    ("Credit / advances growth", [r"advances", r"credit growth", r"loan growth", r"loan book"], [_PCT], "%", +1),
    ("GNPA", [r"gross npa", r"\bgnpa\b", r"\bnpa\b"], [_PCT], "%", -1),
    ("Slippages", [r"slippages?"], [_CR, _PCT], "cr", -1),
    ("Provisions", [r"provisions?", r"credit cost"], [_CR], "cr", -1),
    ("PAT growth", [r"net profit", r"\bpat\b", r"profit after tax", r"\bprofit\b"], [_PCT], "%", +1),
    ("Revenue growth", [r"revenue", r"total income", r"net sales"], [_PCT], "%", +1),
    ("USFDA observations", [r"observations"], [r"(\d+)\s*observation"], "count", -1),
    ("Hyperscaler capex", [r"capex", r"capital expenditure"], [_USD_BN], "usd_bn", 0),
    ("Order book / TCV", [r"\btcv\b", r"order book", r"deal wins", r"order win"], [_CR, _USD_BN], "cr", +1),
]


def extract_metrics(text: str) -> list[dict]:
    """Extract structured numeric metrics from a blob of text."""
    if not text:
        return []
    low = text.lower()
    out = []
    seen = set()
    for label, kws, val_res, unit, qsign in _SPECS:
        for kw in kws:
            for km in re.finditer(kw, low):
                # FORWARD first (keyword -> value, the normal case), then a small BACKWARD
                # window for number-before cases like "5 observations". Forward-first stops
                # a metric from grabbing the previous metric's trailing number.
                fwd = (km.end(), low[km.end(): km.end() + 55])
                bwd = (max(0, km.start() - 30), low[max(0, km.start() - 30): km.end()])
                val, matched_unit, val_end = None, unit, km.end()
                for base, window in (fwd, bwd):
                    for vr in val_res:
                        m = re.search(vr, window)
                        if m:
                            raw = m.group(1).replace(",", "")
                            try:
                                val = float(raw)
                            except ValueError:
                                continue
                            matched_unit = ("bps" if "bps" in vr else
                                            "%" if vr == _PCT else
                                            "cr" if vr == _CR else
                                            "usd_bn" if vr == _USD_BN else unit)
                            val_end = base + m.end()
                            break
                    if val is not None:
                        break
                if val is None:
                    continue
                direction = _dir_near(text, km.start(), max(km.end(), val_end))
                key = (label, val, matched_unit)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "metric": label, "value": val, "unit": matched_unit,
                    "direction": direction, "quality_sign": qsign,
                    "context": text[max(0, km.start() - 40): km.start() + 50].strip(),
                })
                break   # one hit per keyword group is enough
    return out


def extract_from_news(news: list[dict]) -> list[dict]:
    """Run the parser across every crawled news item; attach the source title."""
    results = []
    for n in news or []:
        for m in extract_metrics(_text(n)):
            m["source"] = n.get("title", "")
            m["link"] = n.get("link", "")
            results.append(m)
    return results


# ---------------------------------------------------------------------------
def enrich_news_fulltext(news: list[dict], core, limit: int = 8) -> list[dict]:
    """OPTIONAL: pull full-article body via market_scan.fetch_article so the parser sees more
    than the headline. Network + trafilatura/Playwright dependent; guarded and capped.
    Mutates each item in place (adds 'fulltext') and returns the list."""
    fetch = getattr(core.ms, "fetch_article", None)
    if fetch is None:
        return news
    done = 0
    for n in news or []:
        if done >= limit:
            break
        if n.get("fulltext") or not n.get("link"):
            continue
        try:
            res = fetch(n["link"])
            # fetch_article returns (title, body); older variants may return a str
            if isinstance(res, tuple):
                body = res[1] if len(res) > 1 else (res[0] if res else "")
            else:
                body = res
            body = str(body or "").strip()
            if body:
                n["fulltext"] = body[:8000]
                done += 1
        except Exception:
            continue
    return news
