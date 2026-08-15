"""
news_fetch.py — robust news acquisition so the engine reads BODIES, not just headlines.

RSS gives you a headline + link + short summary. Every nuanced signal the classifiers need
(IBM redirecting budgets, cloud still growing, a NIM figure, a USFDA 483, a Cabinet scheme)
lives in the article BODY. This module makes body-level reading reliable and broadens
discovery:

  1. fetch_body(url)     — trafilatura → Playwright → **keyless requests + HTML-strip fallback**,
                           so a body is pulled even when trafilatura/playwright aren't installed.
  2. search_google_news  — targeted topic search via Google News RSS (keyless), so we FIND the
                           right articles instead of only what the market feeds happen to carry.
  3. augment_snapshot    — runs the topic searches, merges results into the snapshot's news,
                           and fills bodies for the most relevant items. Degrades gracefully
                           (network / dep failures are skipped, never fatal).

Everything is keyless and optional; the engine is untouched.
"""
from __future__ import annotations

import os
import re
import json
import urllib.parse
import urllib.request
from pathlib import Path

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
_MIN_GOOD = 400          # chars — below this we try the next tier
_BODY_CAP = 8000


# ---------------------------------------------------------------------------
# 1. body extraction — three tiers, last one needs no optional deps
# ---------------------------------------------------------------------------
def _strip_html(html: str) -> str:
    """Dependency-free readable-text extraction: drop scripts/styles/tags, collapse space."""
    html = re.sub(r"(?is)<(script|style|noscript|header|footer|nav|form|aside)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</p>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)                 # remove remaining tags
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _requests_fallback(url: str) -> str:
    """Keyless plain fetch + tag-strip. Always available (requests/urllib only)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read().decode("utf-8", "replace")
        return _strip_html(raw)
    except Exception:
        return ""


def fetch_body(url: str, core=None) -> str:
    """Best-effort full body. Tier 1 trafilatura/Playwright via the engine's fetch_article;
    Tier 2 the keyless requests fallback. Returns '' if nothing works."""
    if not url:
        return ""
    # Tier 1 — the engine's fetch_article (trafilatura → Playwright → crawl4ai)
    fetch = getattr(getattr(core, "ms", None), "fetch_article", None) if core else None
    if fetch:
        try:
            res = fetch(url)
            body = (res[1] if isinstance(res, tuple) and len(res) > 1 else res) or ""
            body = str(body).strip()
            if len(body) >= _MIN_GOOD:
                return body[:_BODY_CAP]
        except Exception:
            pass
    # Tier 2 — keyless fallback (works with no optional deps)
    return _requests_fallback(url)[:_BODY_CAP]


# ---------------------------------------------------------------------------
# 2. targeted topic search — Google News RSS (keyless)
# ---------------------------------------------------------------------------
# built-in fallback (used if news_queries.json is missing/unreadable)
_DEFAULT_QUERIES = [
    "IBM OR Accenture AI software budget shift",
    "hyperscaler AI capex guidance Microsoft Amazon Google Meta",
    "cloud spending growth AI infrastructure",
    "AI deflation Indian IT services TCS Infosys",
    "SOX semiconductor Nvidia demand",
    "US CPI PPI inflation Fed rate",
    "India CPI RBI monetary policy",
    "Union Cabinet PLI scheme India manufacturing",
    "USFDA India pharma warning letter",
    "FII DII flows India equities",
]

# editable query list — edit news_queries.json (no code change), else fall back to the defaults
_QUERIES_FILE = Path(os.environ.get(
    "NEWSAGENT_QUERIES", Path(__file__).resolve().parent / "news_queries.json"))


def load_queries() -> list[str]:
    """Load topic queries from news_queries.json (fresh each run so edits take effect),
    falling back to the built-in defaults if the file is missing or malformed."""
    try:
        data = json.loads(_QUERIES_FILE.read_text())
        qs = [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        return qs or _DEFAULT_QUERIES
    except Exception:
        return _DEFAULT_QUERIES


# back-compat: some callers reference TOPIC_QUERIES directly
TOPIC_QUERIES = load_queries()


def search_google_news(query: str, limit: int = 6) -> list[dict]:
    """Keyless topic search via Google News RSS. Returns news-item dicts with a summary."""
    try:
        import feedparser
    except Exception:
        return []
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    out = []
    try:
        feed = feedparser.parse(url)
        for e in feed.entries[:limit]:
            title = (e.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "source": f"Google News: {query[:24]}",
                "title": title,
                "link": e.get("link", ""),
                "published": e.get("published", ""),
                "summary": re.sub(r"(?s)<[^>]+>", " ", e.get("summary", "")).strip(),
                "macro": True,
                "tags": "search",
            })
    except Exception:
        return []
    return out


# ---------------------------------------------------------------------------
# 3. orchestrate: search + merge + body enrichment
# ---------------------------------------------------------------------------
def _key(item: dict) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (item.get("title", "") or "").lower()).strip()


def augment_snapshot(core, queries: list[str] | None = None,
                     body_limit: int | None = None, search: bool | None = None) -> dict:
    """Enrich the cached snapshot's news IN PLACE: add targeted-search results, then fill bodies
    for the most relevant items. Returns a small summary. Never raises."""
    snap = core._ensure()
    news = snap.get("news", [])
    seen = {_key(n) for n in news}
    added = 0

    # (a) targeted search (keyless) — broadens discovery beyond the fixed feeds
    do_search = (os.environ.get("NEWSAGENT_SEARCH", "1") != "0") if search is None else search
    if do_search:
        for q in (queries or load_queries()):    # re-read the file each run so edits apply
            for item in search_google_news(q):
                k = _key(item)
                if k and k not in seen:
                    seen.add(k)
                    news.append(item)
                    added += 1

    # (b) body enrichment — pull FULL text for the top macro/market items
    if os.environ.get("NEWSAGENT_FULLTEXT", "1") == "0":
        snap["news"] = news
        return {"searched": do_search, "added": added, "bodies_pulled": 0}
    limit = body_limit if body_limit is not None else int(os.environ.get("NEWSAGENT_FULLTEXT_LIMIT", "20"))
    ordered = sorted(news, key=lambda n: 0 if n.get("macro") else 1)
    pulled = 0
    for n in ordered:
        if pulled >= limit:
            break
        if n.get("fulltext") or not n.get("link"):
            continue
        body = fetch_body(n["link"], core)
        if body:
            n["fulltext"] = body
            pulled += 1

    snap["news"] = news
    return {"searched": do_search, "added": added, "bodies_pulled": pulled, "total_news": len(news)}
