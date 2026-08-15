"""
earnings_aggregator.py
======================
A URL -> earnings tool you own. Give it a page address (company IR "Financial
Results" page, a filing, screener/aggregator page) and it:

  1. FETCHES it through a pure-Python ladder that climbs only as far as needed:
       rung 1  machine door   — a known JSON/RSS endpoint for that host (no HTML at all)
       rung 2  browser session — real UA + Referer + COOKIE PRIMING (defeats most 403s)
       rung 3  headless render — Playwright runs the page's JS (optional import)
     No paid services, no API keys. Each rung is tried in order; first success wins.
  2. CLEANS the page to readable text (trafilatura if present, else BeautifulSoup).
  3. EXTRACTS with your local Qwen 2.5 7B (Ollama) into a strict earnings schema,
     with a regex heuristic fallback so it never returns nothing.
  4. EMITS BOTH:
       - a structured earnings dict (revenue, PAT, EPS, YoY/QoQ, margin, guidance)
       - a filings-shape article dict (title/publishedAt/description/source/symbol...)
         so results flow straight into the existing news pipeline.

WHY SITES "BLOCK PYTHON": it's not the site, it's the knock. Default requests sends
User-Agent 'python-requests/x', no cookies, runs no JS. Rungs 1-3 fix identity,
session, and rendering respectively — in that order of preference.

Everything heavy (requests, playwright, trafilatura, the LLM) is a guarded import,
so this module loads and its pure logic tests even in a bare sandbox.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

try:
    import requests
except Exception:
    requests = None

# reuse the project's constituent universe for symbol resolution
try:
    from .filings import SEED_SCRIP_MAP
    _SYMBOLS = set(SEED_SCRIP_MAP.values())
except Exception:
    _SYMBOLS = {"TCS", "INFY", "RELIANCE", "HDFCBANK", "ICICIBANK"}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,application/json",
            "Accept-Language": "en-IN,en;q=0.9"}

OLLAMA = "http://localhost:11434"
QWEN_MODEL = "llama3.2:3b"

# ── rung 1: machine doors (host -> function returning clean text/json) ───────
# Register a host's real data endpoint here so we NEVER scrape its HTML. This is
# the most robust rung; extend it per source. Left generic on purpose.
MACHINE_DOORS: dict[str, callable] = {}


def _fetch_machine_door(url: str) -> str | None:
    host = urlsplit(url).netloc.lower()
    for known, fn in MACHINE_DOORS.items():
        if known in host:
            return fn(url)
    return None


# ── rung 2: browser-like session with cookie priming ────────────────────────
def _fetch_session(url: str) -> str | None:
    if requests is None:
        raise RuntimeError("requests unavailable")
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}/"
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:                                   # prime cookies from the homepage first
        s.get(root, timeout=10)
    except Exception:
        pass
    s.headers["Referer"] = root
    r = s.get(url, timeout=20)
    r.raise_for_status()
    return r.text


# ── rung 3: headless browser (renders JS) — optional ────────────────────────
def _fetch_headless(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None                        # not installed -> skip this rung
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(user_agent=_UA)
        try:
            pg.goto(url, wait_until="networkidle", timeout=30000)
            html = pg.content()
        finally:
            b.close()
        return html


LADDER = [("machine_door", _fetch_machine_door),
          ("session", _fetch_session),
          ("headless", _fetch_headless)]


def fetch(url: str) -> tuple[str | None, str | None]:
    """Climb the ladder; return (content, method_used). None,None if all fail."""
    for name, fn in LADDER:
        try:
            content = fn(url)
        except Exception as e:
            print(f"  [fetch:{name}] {e}")
            continue
        if content:
            print(f"  [fetch] {name} ok ({len(content)} chars) <- {url[:60]}")
            return content, name
    print(f"  [fetch] all rungs failed <- {url}")
    return None, None


# ── text cleaning ────────────────────────────────────────────────────────────
def clean_text(html: str) -> str:
    if not html:
        return ""
    try:
        import trafilatura
        t = trafilatura.extract(html)
        if t:
            return t
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def resolve_symbol(text: str) -> str | None:
    up = text.upper()
    for sym in _SYMBOLS:
        if re.search(rf"\b{re.escape(sym)}\b", up):
            return sym
    names = {"TATA CONSULTANCY": "TCS", "INFOSYS": "INFY", "RELIANCE": "RELIANCE",
             "HDFC BANK": "HDFCBANK", "ICICI BANK": "ICICIBANK"}
    for name, sym in names.items():
        if name in up:
            return sym
    return None


# ── extraction: local Qwen with heuristic fallback ──────────────────────────
EARNINGS_SYSTEM = (
    "You are a precise financial-results extractor for Indian companies. Extract ONLY "
    "figures explicitly stated in the text. If a field is not stated, return null — never "
    "guess or infer. Monetary values in INR crore (convert if the text uses another unit). "
    "Percentages as plain numbers (12.3 not '12.3%'). sentiment is the expected effect on "
    "the stock, graded -1.0..1.0 in 0.1 steps; reserve |0.9|-|1.0| for exceptional surprises. "
    "Return ONLY a JSON object, no prose, no code fences."
)

EARNINGS_SCHEMA_HINT = (
    '{"company":str|null,"period":str|null,"revenue_cr":num|null,"net_profit_cr":num|null,'
    '"eps":num|null,"yoy_profit_pct":num|null,"qoq_profit_pct":num|null,"margin_pct":num|null,'
    '"guidance":str|null,"surprise":"beat"|"miss"|"inline"|null,"sentiment":num,"confidence":num}'
)


def _qwen_extract(text: str) -> dict | None:
    prompt = (f"Extract the earnings into this exact JSON shape:\n{EARNINGS_SCHEMA_HINT}\n\n"
              f"Results text:\n{text[:6000]}")
    body = json.dumps({"model": QWEN_MODEL, "temperature": 0.0, "stream": False,
                       "messages": [{"role": "system", "content": EARNINGS_SYSTEM},
                                    {"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(OLLAMA + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    txt = resp["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", re.sub(r"```json|```", "", txt), re.DOTALL)
    return json.loads(m.group(0)) if m else None


_NUM = r"([0-9][0-9,]*\.?[0-9]*)"


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def _heuristic_extract(text: str) -> dict:
    """Regex best-effort — the always-available fallback (never an LLM)."""
    t = text.replace("\xa0", " ")
    out = {"company": None, "period": None, "revenue_cr": None, "net_profit_cr": None,
           "eps": None, "yoy_profit_pct": None, "qoq_profit_pct": None, "margin_pct": None,
           "guidance": None, "surprise": None, "sentiment": 0.0, "confidence": 0.3,
           "_provider": "heuristic"}
    def grab(pat):
        m = re.search(pat, t, re.I)
        return _f(m.group(1)) if m else None
    out["revenue_cr"] = grab(rf"revenue[^0-9]{{0,40}}(?:Rs\.?|₹|INR)?\s*{_NUM}\s*(?:cr|crore)")
    out["net_profit_cr"] = grab(rf"(?:net profit|PAT|profit after tax)[^0-9]{{0,40}}(?:Rs\.?|₹|INR)?\s*{_NUM}\s*(?:cr|crore)")
    out["eps"] = grab(rf"\bEPS[^0-9]{{0,20}}(?:Rs\.?|₹)?\s*{_NUM}")
    out["yoy_profit_pct"] = grab(rf"{_NUM}\s*%\s*(?:YoY|year[- ]on[- ]year)")
    p = re.search(r"\bQ[1-4]\s*FY\s*[0-9]{2,4}", t, re.I)
    out["period"] = p.group(0) if p else None
    low = t.lower()
    if "beat" in low or "above estimate" in low:
        out["surprise"] = "beat"; out["sentiment"] = 0.6
    elif "miss" in low or "below estimate" in low:
        out["surprise"] = "miss"; out["sentiment"] = -0.6
    return out


def extract_earnings(text: str, prefer_llm: bool = True) -> dict:
    data = None
    if prefer_llm:                        # respect the global on-device LLM toggle
        try:
            import agent_settings
            prefer_llm = agent_settings.local_llm_enabled()
        except Exception:
            pass
    if prefer_llm:
        try:
            data = _qwen_extract(text)
            if data:
                data.setdefault("_provider", QWEN_MODEL)
                data.setdefault("confidence", 0.7)
        except Exception as e:
            print(f"  [extract] Qwen unavailable ({e}); using heuristic")
    if not data:
        data = _heuristic_extract(text)
    data["symbol"] = resolve_symbol((data.get("company") or "") + " " + text[:400])
    return data


# ── the article-dict adapter (filings/fetch_rss shape) ──────────────────────
def to_article(earn: dict, url: str) -> dict:
    """Turn extracted earnings into the same dict shape fetch_rss/fetch_filings emit,
    so it drops into prepare_articles -> tag -> cache with no downstream changes."""
    company = earn.get("company") or earn.get("symbol") or "Results"
    period = earn.get("period") or ""
    bits = []
    if earn.get("revenue_cr"): bits.append(f"revenue ₹{earn['revenue_cr']}cr")
    if earn.get("net_profit_cr"): bits.append(f"PAT ₹{earn['net_profit_cr']}cr")
    if earn.get("yoy_profit_pct") is not None: bits.append(f"{earn['yoy_profit_pct']}% YoY")
    if earn.get("surprise"): bits.append(earn["surprise"])
    return {
        "title": f"{company} {period} results".strip(),
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "description": " | ".join(bits)[:500],
        "source": f"Earnings:{urlsplit(url).netloc}",
        "symbol": earn.get("symbol"),
        "filing_event": "RESULTS",
        "earnings": earn.get("surprise"),
        "earnings_data": earn,          # full structured payload rides along
    }


def aggregate(urls: list[str], prefer_llm: bool = True) -> list[dict]:
    """URL list -> [{earnings, article, method}]. Both outputs per the design."""
    results = []
    for url in urls:
        html, method = fetch(url)
        if not html:
            results.append({"url": url, "error": "fetch_failed", "method": None})
            continue
        earn = extract_earnings(clean_text(html), prefer_llm=prefer_llm)
        results.append({"url": url, "method": method,
                        "earnings": earn, "article": to_article(earn, url)})
    return results


if __name__ == "__main__":
    # offline demo: extract from a TCS-style results blurb (no network needed).
    SAMPLE = ("Tata Consultancy Services (TCS) reported Q1 FY27 results. Revenue stood at "
              "Rs 64,500 crore, up 6.2% YoY. Net profit (PAT) came in at Rs 12,400 crore, "
              "a rise of 9.1% YoY, beating street estimates. EPS was Rs 34.2. Operating "
              "margin was 24.8%. Management guided to double-digit revenue growth for FY27.")
    e = extract_earnings(SAMPLE, prefer_llm=False)   # heuristic path for the demo
    print(json.dumps(e, indent=2, ensure_ascii=False))
    print("\narticle dict:", json.dumps(to_article(e, "https://www.tcs.com/investor-relations"), ensure_ascii=False))
