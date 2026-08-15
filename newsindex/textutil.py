#!/usr/bin/env python3
"""
textutil.py — CANONICAL news-text helpers, shared by every engine.

These three helpers were defined in NewsAgent/overlay/common.py and were only
reachable by code running with overlay/ on sys.path. Lifting reason_discovery to a
shared home meant either duplicating them (violating the repo's DRY rule) or giving
them one definition. This is that one definition.

  news_text(item) — concatenate every crawled field, lower-cased and cleaned
  sentences(text) — sentence-ish split so proximity checks don't span an article
  dedupe(seq)     — order-preserving de-duplication

NewsAgent/overlay/common.py now re-exports from here, so existing `common.sentences`
call sites keep working unchanged.
"""

from __future__ import annotations

import re

# Fields a crawled news item may carry. Kept here so both engines agree on what
# "the text of a news item" means — a silent disagreement here would make the two
# engines match different keywords on the same article.
NEWS_FIELDS = ("title", "tags", "summary", "body", "fulltext")


def news_text(item: dict) -> str:
    """Concatenate every crawled field of a news item, lower-cased and CLEANED.

    Skips non-string fields (a dict body must not leak "{'url': ...}" into the text)
    and strips embedded dict/JSON fragments that slipped into a summary/body string.
    """
    parts = []
    for k in NEWS_FIELDS:
        v = item.get(k, "")
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):              # e.g. body stored as {'text':.., 'url':..}
            parts.append(str(v.get("text") or v.get("body") or v.get("summary") or ""))
    t = " ".join(parts).lower()
    t = re.sub(r"\{\s*['\"]?\w+['\"]?\s*:\s*.*?\}", " ", t)   # drop leaked {'url': '...'}
    t = re.sub(r"https?://\S+", " ", t)                       # drop bare urls
    return re.sub(r"\s+", " ", t).strip()


def sentences(text: str) -> list[str]:
    """Split into sentence-ish spans so proximity checks don't span a whole article."""
    return [s for s in re.split(r"(?<=[.!?;])\s+|\n+", text or "") if s.strip()]


def dedupe(seq):
    """Order-preserving de-duplication."""
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
