#!/usr/bin/env python3
"""
fetch_article.py
----------------
Full-article extraction for the news engine. Turns a headline URL into the
actual article body + extracted numbers, so the LLM can reason over content
instead of just the RSS snippet.

Strategy (light -> heavy, only escalate when needed):
  1. trafilatura over a plain requests GET  — works for most server-rendered
     news pages, no browser, fast & cool.
  2. Playwright (headless Chromium) fallback — only if step 1 returns too little
     (JS-rendered / anti-scrape pages). Heavier; off unless installed + allowed.
  3. Give up gracefully -> caller falls back to the RSS snippet.

Everything is CACHED in SQLite (articles.db) keyed by URL, so re-runs never
re-fetch and we stay polite. Paywalled pages (Reuters/ET Prime/BS Premium) will
return truncated text — that's a hard limit, not a bug; we do NOT try to bypass
logins.

Install:
    pip install trafilatura requests
    # optional fallback:
    pip install playwright && playwright install chromium

CLI test:
    python3 fetch_article.py "https://www.moneycontrol.com/news/....html"
"""

from __future__ import annotations
import re
import sys
import time
import sqlite3
import datetime as dt
from pathlib import Path

import requests

try:
    import trafilatura
    HAVE_TRAFILATURA = True
except Exception:
    HAVE_TRAFILATURA = False

DB_PATH = Path(__file__).resolve().parent / "articles.db"
MIN_GOOD_CHARS = 400          # below this, escalate to the next backend
POLITE_DELAY_S = 1.0          # between live fetches
# Benchmarked (see compare_backends.py): trafilatura first (clean + not
# browser-fingerprinted, so it beats Akamai on open pages), then crawl4ai as the
# browser fallback (better stealth + ~2x faster than Playwright on blocked pages).
USE_CRAWL4AI = True           # primary browser fallback
ALLOW_PLAYWRIGHT = False      # crawl4ai replaces it; set True to keep as a last resort
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122 Safari/537.36")


# ---------------------------------------------------------------- cache
def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS articles(
        url TEXT PRIMARY KEY, title TEXT, body TEXT, published TEXT,
        numbers TEXT, method TEXT, fetched_at TEXT)""")
    return con


def _cache_get(url: str) -> dict | None:
    # Cache is a nice-to-have — never let a DB error break extraction.
    try:
        con = _db()
        row = con.execute("SELECT url,title,body,published,numbers,method,fetched_at "
                          "FROM articles WHERE url=?", (url,)).fetchone()
        con.close()
    except Exception:
        return None
    if not row:
        return None
    return {"url": row[0], "title": row[1], "body": row[2], "published": row[3],
            "numbers": (row[4] or "").split("|") if row[4] else [],
            "method": row[5], "fetched_at": row[6], "cached": True}


def _cache_put(rec: dict):
    try:
        con = _db()
        con.execute("INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?,?,?)",
                    (rec["url"], rec.get("title", ""), rec.get("body", ""),
                     rec.get("published", ""), "|".join(rec.get("numbers", [])),
                     rec.get("method", ""), dt.datetime.now().isoformat(timespec="seconds")))
        con.commit()
        con.close()
    except Exception:
        pass   # extraction still returns fine, just uncached this run


# ---------------------------------------------------------------- extract
_NUM_RE = re.compile(
    r"(?:(?:₹|rs\.?|inr)\s?[\d,]+(?:\.\d+)?\s?(?:crore|cr|lakh|bn|billion|million|mn)?"
    r"|\$\s?[\d,]+(?:\.\d+)?\s?(?:bn|billion|million|mn)?"
    r"|[\d,]+(?:\.\d+)?\s?(?:crore|cr|lakh|bps|basis points)"
    r"|[-+]?\d+(?:\.\d+)?\s?%)", re.IGNORECASE)


def extract_numbers(text: str, limit: int = 12) -> list[str]:
    """Pull out money / %, / bps figures — the quant meat of an article."""
    seen, out = set(), []
    for m in _NUM_RE.finditer(text or ""):
        s = re.sub(r"\s+", " ", m.group(0).strip())
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (text or "").strip())


# ---------------------------------------------------------------- fetchers
def _trafilatura_fetch(url: str) -> tuple[str, str]:
    """Return (title, body) or ('','') on failure. No browser."""
    if not HAVE_TRAFILATURA:
        return "", ""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            # fall back to a manual requests GET, then feed HTML to trafilatura
            r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            downloaded = r.text if r.status_code == 200 else None
        if not downloaded:
            return "", ""
        body = trafilatura.extract(downloaded, include_comments=False,
                                   include_tables=True, favor_precision=True) or ""
        meta = trafilatura.extract_metadata(downloaded)
        title = getattr(meta, "title", "") or ""
        return title, _clean(body)
    except Exception:
        return "", ""


def _playwright_fetch(url: str) -> tuple[str, str]:
    """Render a JS-heavy page. Only used as a fallback. Requires playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return "", ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=UA)
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            html = page.content()
            title = page.title()
            browser.close()
        if HAVE_TRAFILATURA:
            body = trafilatura.extract(html, include_comments=False,
                                       include_tables=True) or ""
        else:  # crude tag-strip fallback
            body = re.sub(r"<[^>]+>", " ", html)
        return title, _clean(body)
    except Exception:
        return "", ""


def _crawl4ai_fetch(url: str) -> tuple[str, str]:
    """
    crawl4ai extraction — LLM-friendly, returns clean markdown. Browser-based
    (heavier than trafilatura). Only used if USE_CRAWL4AI. Robust across the
    library's version differences in the .markdown attribute.
    """
    try:
        import asyncio
        from crawl4ai import AsyncWebCrawler
    except Exception:
        return "", ""

    async def _run():
        async with AsyncWebCrawler(verbose=False) as crawler:
            res = await crawler.arun(url=url)
            md = getattr(res, "markdown", "") or ""
            if not isinstance(md, str):                 # newer versions: object
                md = getattr(md, "raw_markdown", "") or str(md)
            title = ""
            meta = getattr(res, "metadata", None)
            if isinstance(meta, dict):
                title = meta.get("title", "") or ""
            return title, _clean(md)

    try:
        return asyncio.run(_run())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception:
        return "", ""


def fetch_article(url: str, allow_playwright: bool = ALLOW_PLAYWRIGHT,
                  refresh: bool = False) -> dict:
    """
    Return {url,title,body,published,numbers,method,cached}. Uses cache unless
    refresh=True. body='' means extraction failed (caller should use the RSS snippet).
    """
    if not url:
        return {"url": url, "title": "", "body": "", "numbers": [], "method": "none",
                "cached": False}
    if not refresh:
        hit = _cache_get(url)
        if hit and hit.get("body"):
            return hit

    title, body = _trafilatura_fetch(url)
    method = "trafilatura"
    if len(body) < MIN_GOOD_CHARS and USE_CRAWL4AI:      # richer fallback #1
        time.sleep(POLITE_DELAY_S)
        t2, b2 = _crawl4ai_fetch(url)
        if len(b2) > len(body):
            title, body, method = (t2 or title), b2, "crawl4ai"
    if len(body) < MIN_GOOD_CHARS and allow_playwright:  # fallback #2
        time.sleep(POLITE_DELAY_S)
        t3, b3 = _playwright_fetch(url)
        if len(b3) > len(body):
            title, body, method = (t3 or title), b3, "playwright"

    rec = {"url": url, "title": title, "body": body, "published": "",
           "numbers": extract_numbers(body), "method": method if body else "failed",
           "cached": False}
    if body:
        _cache_put(rec)
    time.sleep(POLITE_DELAY_S)
    return rec


def first_paragraph(body: str, max_chars: int = 320) -> str:
    """
    First real prose paragraph for display. Skips markdown nav/link lines and
    boilerplate (important for crawl4ai output, which includes page chrome).
    """
    if not body:
        return ""
    for raw in body.split("\n"):
        p = raw.strip()
        if len(p) < 60:
            continue
        if p[0] in "[#*-|>":                        # nav / list / heading / quote markers
            continue
        link_chars = sum(len(m) for m in re.findall(r"\[[^\]]*\]\([^)]*\)", p))
        if link_chars > len(p) * 0.35:              # mostly links -> nav block
            continue
        return (p[:max_chars] + "…") if len(p) > max_chars else p
    return body[:max_chars]


def _report(tag, title, body):
    print(f"\n── {tag} ──")
    print(f"  chars  : {len(body)}")
    print(f"  title  : {title[:90]}")
    print(f"  numbers: {extract_numbers(body)}")
    print(f"  lead   : {first_paragraph(body)[:220]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 fetch_article.py <url> [backend]")
        print("  backend = auto (default) | trafilatura | crawl4ai | playwright | compare")
        sys.exit(0)
    url = sys.argv[1]
    backend = sys.argv[2] if len(sys.argv) > 2 else "auto"

    if backend == "compare":
        # run all three on the same URL so you can see the difference
        t, b = _trafilatura_fetch(url);  _report("trafilatura", t, b)
        t, b = _crawl4ai_fetch(url);     _report("crawl4ai", t, b)
        t, b = _playwright_fetch(url);   _report("playwright", t, b)
    elif backend == "trafilatura":
        t, b = _trafilatura_fetch(url);  _report("trafilatura", t, b)
    elif backend == "crawl4ai":
        t, b = _crawl4ai_fetch(url);     _report("crawl4ai", t, b)
    elif backend == "playwright":
        t, b = _playwright_fetch(url);   _report("playwright", t, b)
    else:
        art = fetch_article(url, refresh=True)
        print(f"method: {art['method']}  cached: {art.get('cached')}")
        _report(art["method"], art["title"], art["body"])
