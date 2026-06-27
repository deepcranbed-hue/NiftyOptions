"""
rss_news.py
-----------
Real-time news for the Sector News / Global Cues tabs via RSS, replacing both
NewsAPI (24h free-tier delay) and ddgs/duckduckgo_search (rate-limits after a
couple of calls). RSS from Indian financial outlets is free, key-less, real-time,
and not rate-limited — just poll politely (every few minutes, not every second).

Output dicts match what prepare_articles() expects:
    {"title", "publishedAt"(ISO UTC), "description"}
so the flow is:  fetch_rss() -> prepare_articles() -> Gemini -> run_pipeline()

NOTE: feed URLs change occasionally; verify them and prune any that 404.
Be a good citizen: set a User-Agent, a timeout, and cache between refreshes.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

import feedparser

# Curated India-markets feeds (verify periodically).
DEFAULT_FEEDS = {
    "ET Markets":        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "ET Stocks":         "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "Moneycontrol Top":  "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "Moneycontrol Mkts": "https://www.moneycontrol.com/rss/marketreports.xml",
    "Livemint Markets":  "https://www.livemint.com/rss/markets",
    "Business Std Mkts":  "https://www.business-standard.com/rss/markets-106.rss",
}

GLOBAL_FEEDS = {
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "CNBC Markets": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "Investing Global": "https://www.investing.com/rss/news_25.rss"
}

_UA = "Mozilla/5.0 (compatible; SectorNewsBot/1.0)"


def _to_iso_utc(entry) -> str | None:
    """RSS pubDate -> ISO UTC. feedparser gives published_parsed (UTC struct_time)."""
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return None
    return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc).isoformat()


def fetch_rss(feeds: dict[str, str] | None = None, per_feed: int = 25) -> list[dict]:
    """Pull and merge feeds into article dicts (deduped, newest-first).
    Network failures on one feed don't kill the batch."""
    feeds = feeds or DEFAULT_FEEDS
    seen, out = set(), []
    for name, url in feeds.items():
        try:
            parsed = feedparser.parse(url, agent=_UA)
        except Exception as e:                       # one bad feed shouldn't break the rest
            print(f"  [rss] {name} failed: {e}")
            continue
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            print(f"  [rss] {name}: no entries (check URL)")
            continue
        for e in parsed.entries[:per_feed]:
            title = (getattr(e, "title", "") or "").strip()
            iso = _to_iso_utc(e)
            key = title.lower()[:60]
            if not title or not iso or key in seen:
                continue
            seen.add(key)
            out.append({
                "title": title,
                "publishedAt": iso,
                "description": (getattr(e, "summary", "") or "")[:500],
                "source": name,
            })
    out.sort(key=lambda a: a["publishedAt"], reverse=True)
    print(f"  [rss] {len(out)} unique articles from {len(feeds)} feeds")
    return out


if __name__ == "__main__":
    # offline parse test (feedparser accepts a raw RSS string), since the
    # sandbox can't reach the news domains — proves the mapping/shape is right.
    sample = '''<?xml version="1.0"?><rss version="2.0"><channel>
      <title>ET Markets</title>
      <item><title>Sensex jumps 790 as banking and IT power rally</title>
        <description>Benchmarks rallied led by financials...</description>
        <pubDate>Wed, 25 Jun 2026 09:48:28 GMT</pubDate></item>
      <item><title>Nifty IT drags as Accenture guidance disappoints</title>
        <description>IT majors slipped...</description>
        <pubDate>Wed, 25 Jun 2026 06:10:00 GMT</pubDate></item>
    </channel></rss>'''
    parsed = feedparser.parse(sample)
    arts = []
    for e in parsed.entries:
        arts.append({"title": e.title, "publishedAt": _to_iso_utc(e),
                     "description": e.summary})
    for a in arts:
        print(a["publishedAt"], "|", a["title"])
    print("\nshape ready for prepare_articles():", list(arts[0].keys()))
