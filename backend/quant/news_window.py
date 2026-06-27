"""
news_window.py
--------------
Prepare a NewsAPI response for the sentiment pipeline. Fixes "I'm getting
yesterday's (and last week's) news mixed with today's":

  1. filters to a recency window (default 36h) so week-old stragglers drop out
  2. carries each article's REAL publishedAt -> the decay weighting can then
     down-weight older news instead of treating everything as 'now'
  3. dedupes near-identical headlines (aggregators repost the same story)
  4. reports the time span so you can SEE how stale the batch is

Also: at the query level, set NewsAPI `from=(now-24h)` and sortBy=publishedAt;
this helper is the second line of defence for whatever still slips through.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta


def prepare_articles(newsapi_articles: list[dict], max_age_hours: float = 36.0,
                     now: datetime | None = None) -> list[dict]:
    """Map NewsAPI items -> pipeline article dicts, filtered & timestamped.
    Input items are raw NewsAPI 'articles' (need title, publishedAt; description
    optional). Output dicts carry title, body, published_at (real)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)

    seen, out, stamps = set(), [], []
    for a in newsapi_articles:
        ts = a.get("publishedAt") or a.get("published_at")
        if not ts:
            continue                                  # no timestamp -> can't weight; drop
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < cutoff:
            continue                                  # too old for this window
        title = (a.get("title") or "").strip()
        key = title.lower()[:60]
        if not title or key in seen:
            continue                                  # dedupe reposts
        seen.add(key)
        out.append({"title": title,
                    "body": a.get("description") or a.get("content") or "",
                    "published_at": dt.isoformat()})
        stamps.append(dt)

    if stamps:
        span_h = (max(stamps) - min(stamps)).total_seconds() / 3600.0
        oldest_h = (now - min(stamps)).total_seconds() / 3600.0
        print(f"news window: {len(out)} articles | spans {span_h:.0f}h "
              f"(oldest {oldest_h:.0f}h ago)")
        if span_h > 24:
            print("  ⚠️ batch spans >24h — recency decay will down-weight the "
                  "older ones, but tighten NewsAPI `from` to reduce the spread.")
    else:
        print("news window: 0 articles after filtering (widen max_age_hours or "
              "check that NewsAPI returned publishedAt)")
    return out


if __name__ == "__main__":
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    # the user's batch, with the real dates it actually spans
    raw = [
        {"title": "Sensex jumps 790 points, Nifty reclaims 24,000 as banking and IT power rally",
         "publishedAt": "2026-06-25T04:10:00Z"},
        {"title": "Sensex rises 291 points, Nifty above 24,100; markets rebound on global cues",
         "publishedAt": "2026-06-24T10:30:00Z"},
        {"title": "KOSPI meltdown drags Indian indices into red; Nifty falls 279 points",
         "publishedAt": "2026-06-23T14:00:00Z"},
        {"title": "Sensex falls 893 points, Nifty ends lower as IT and metal stocks drag",
         "publishedAt": "2026-06-23T11:00:00Z"},
        {"title": "Nifty 50 Falls 0.64% as IT Stocks Slide on Accenture's Weak Guidance",
         "publishedAt": "2026-06-20T10:00:00Z"},
        {"title": "Sensex, Nifty snap 5-day rally amid IT sell-off, global uncertainties",
         "publishedAt": "2026-06-19T10:00:00Z"},
        {"title": "Sensex jumps 790 points, Nifty reclaims 24,000 as banking and IT power rally",
         "publishedAt": "2026-06-25T04:12:00Z"},   # duplicate repost
    ]
    print("--- 36h window (default) ---")
    arts = prepare_articles(raw, max_age_hours=36, now=now)
    for a in arts:
        print(f"  {a['published_at'][:16]}  {a['title'][:55]}")
    print("\n--- 12h window (today only) ---")
    prepare_articles(raw, max_age_hours=12, now=now)
