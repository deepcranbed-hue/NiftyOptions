"""
sector_tagging.py
-----------------
Build sector sentiment DIRECTLY from Gemini's per-article output instead of
re-deriving sectors from the thin keyword map. Fixes two bugs:

  1. zero net score  -> caused by sentiment never being written into articles.
                        This function REQUIRES a sentiment per article and will
                        warn loudly if the whole batch is zero (the tell-tale sign
                        Gemini's score isn't being passed through).
  2. OTHER-only drop  -> real headlines say "banking", "IT stocks", "metal",
                        which the keyword map misses. Gemini's sectors_affected
                        already names them, so we use that.

Each article dict must carry:
    {"title", "published_at"(iso/datetime), "sentiment"(-1..1),
     "sectors_affected"(list[str])}   # e.g. ["Banks","IT"]
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone


def _decay(age_h, half_life=12.0):
    return math.exp(-math.log(2) * max(age_h, 0.0) / half_life)


def sector_sentiment_from_gemini(articles: list[dict], now=None,
                                 half_life_hours: float = 12.0) -> dict[str, float]:
    """Decay-weighted average sentiment per sector, using Gemini's
    sectors_affected. Returns {} if nothing tagged."""
    now = now or datetime.now(timezone.utc)

    # guard: catch the 'all sentiment is zero' wiring bug explicitly
    sents = [float(a.get("sentiment", 0.0)) for a in articles]
    if articles and all(s == 0.0 for s in sents):
        print("⚠️  WARNING: every article sentiment is 0.0 — Gemini's score is not "
              "being written into the articles. Sector scores will all be zero. "
              "Fix the tagging step before trusting this tab.")

    num: dict[str, float] = defaultdict(float)
    den: dict[str, float] = defaultdict(float)
    for a in articles:
        ts = a.get("published_at")
        dt = datetime.fromisoformat(ts) if isinstance(ts, str) else (ts or now)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        w = _decay((now - dt).total_seconds() / 3600.0, half_life_hours)
        s = float(a.get("sentiment", 0.0))
        for sec in a.get("sectors_affected", []) or []:
            num[sec] += w * s
            den[sec] += w
    return {sec: num[sec] / den[sec] for sec in num if den[sec] > 0}


if __name__ == "__main__":
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    # the same batch, now as Gemini WOULD return it: sentiment + sectors_affected
    arts = [
        {"title": "Sensex jumps 790, banking and IT power rally",
         "published_at": "2026-06-25T04:00:00+00:00", "sentiment": 0.7,
         "sectors_affected": ["Banks", "IT"]},
        {"title": "Indian, global stocks weak as AI-heavy Korea plunges 10%",
         "published_at": "2026-06-23T13:00:00+00:00", "sentiment": -0.7,
         "sectors_affected": ["IT"]},
        {"title": "KOSPI meltdown drags Indian indices; Nifty falls 279",
         "published_at": "2026-06-23T14:00:00+00:00", "sentiment": -0.8,
         "sectors_affected": ["IT"]},
        {"title": "Sensex falls 893, IT and metal stocks drag",
         "published_at": "2026-06-23T11:00:00+00:00", "sentiment": -0.7,
         "sectors_affected": ["IT", "Metals"]},
        {"title": "Nifty falls 0.64% as IT slides on Accenture weak guidance",
         "published_at": "2026-06-20T10:00:00+00:00", "sentiment": -0.5,
         "sectors_affected": ["IT"]},
        {"title": "Sensex, Nifty snap 5-day rally amid IT sell-off",
         "published_at": "2026-06-19T10:00:00+00:00", "sentiment": -0.5,
         "sectors_affected": ["IT"]},
    ]
    res = sector_sentiment_from_gemini(arts, now=now)
    print("sector sentiment:", {k: round(v, 3) for k, v in res.items()})
