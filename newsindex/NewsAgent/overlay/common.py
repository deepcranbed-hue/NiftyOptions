"""
common.py — the SINGLE SOURCE OF TRUTH for the overlay's shared logic.

Before this, the same helpers were copy-pasted across modules (3 driver-label maps, 2 caps
tables, 3 news-text extractors, 2 up/down cue banks, several direction detectors, sentence
splitters, dedupers). That drift is a maintenance hazard — fix a keyword in one place, miss
it in four others. Everything shared now lives here, and every overlay module imports it.

Change a cap, a driver label, or a cue word ONCE here and it applies everywhere.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# CANONICAL text helpers — one definition, in newsindex/textutil.py.
# They used to be defined here, which made them unreachable to any engine not
# running with overlay/ on sys.path (market_scan.py). Lifted rather than copied.
# ---------------------------------------------------------------------------
import sys as _sys
from pathlib import Path as _Path
_SHARED = _Path(__file__).resolve().parents[2]
if str(_SHARED) not in _sys.path:
    _sys.path.insert(0, str(_SHARED))
from textutil import news_text, sentences, dedupe, NEWS_FIELDS  # noqa: E402,F401



# ---------------------------------------------------------------------------
# 1. numeric helpers
# ---------------------------------------------------------------------------
def clamp(x, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# normalization caps — the scale of a "large" move for each driver (superset of every module)
CAPS: dict[str, float] = {
    "oil_pct": 4.0, "vix_pct": 12.0, "us10y_pct": 3.0, "dxy_pct": 0.8,
    "copper_pct": 3.0, "fii_kcr": 8.0, "sox_pct": 3.0, "kospi_pct": 2.5,
    "usdinr_move": 0.6, "india_cpi_hot": 1.0, "us_cpi_cool": 1.0,
    "geopolitics_hits": 3.0,
}


def cap_for(key: str, default: float = 1.0) -> float:
    return CAPS.get(key, default)


def norm(v, cap) -> float:
    """Normalize a value into [-1, 1] by a cap (a number). None → 0.0."""
    if v is None or not cap:
        return 0.0
    return clamp(v / cap)


def norm_key(v, key: str) -> float:
    """Normalize by the CAPS entry for `key`."""
    return norm(v, CAPS.get(key))


# ---------------------------------------------------------------------------
# 2. driver labels — sourced from the engine adapter (core) when available
# ---------------------------------------------------------------------------
_FALLBACK_LABELS = {
    "oil_pct": "Oil", "vix_pct": "India VIX", "us10y_pct": "US 10Y Yield",
    "dxy_pct": "Dollar Index", "kospi_pct": "Kospi", "sox_pct": "SOX (US semis)",
    "fii_kcr": "FII flow", "geopolitics_hits": "Geopolitics",
    "india_cpi_hot": "India CPI", "us_cpi_cool": "US CPI cooling",
    "interaction": "Driver interaction",
}
try:
    import core as _core                        # the MCP engine adapter (mcp_server/core.py)
    DRIVER_LABELS = dict(getattr(_core, "DRIVER_LABELS", _FALLBACK_LABELS))
    for _k, _v in _FALLBACK_LABELS.items():
        DRIVER_LABELS.setdefault(_k, _v)
except Exception:
    DRIVER_LABELS = dict(_FALLBACK_LABELS)


def driver_label(key: str) -> str:
    return DRIVER_LABELS.get(key, key)


# ---------------------------------------------------------------------------
# 3. text + direction from news (one bank, one detector)
# ---------------------------------------------------------------------------
# every field the crawler may populate — title/tags/summary/full-article body
_NEWS_FIELDS = ("title", "tags", "summary", "body", "fulltext")

UP_WORDS = ["rose", "beat", "strong", "grew", "grow", "expand", "expanded", "surge", "improve",
            "improved", "accelerat", "revival", "up ", "hike", "raise", "approval", "boost",
            "tailwind", "stimulus", "cut rates", "demand", "jump", "higher", "increase", "gain"]
DOWN_WORDS = ["fell", "miss", "weak", "slow", "slump", "contract", "contracted", "decline",
              "declined", "drop", "down ", "pressure", "erosion", "warning letter", "483",
              "import alert", "ban", "duty", "deficit", "cut", "lower", "plunge", "tumble"]


# news_text() now lives in newsindex/textutil.py (single definition);
# re-exported here so existing `common.news_text(...)` call sites keep working.

def all_news_text(news: list[dict]) -> str:
    return " ".join(news_text(n) for n in news or [])


def news_direction(news: list[dict], keywords) -> int | None:
    """+1/-1/0 if any item mentions a keyword (with an up/down lean), else None.
    0 means 'mentioned but ambiguous'. Note: 'cut rates' is scored up before bare 'cut'."""
    hit, score = False, 0
    for n in news or []:
        t = news_text(n)
        if any(k in t for k in keywords):
            hit = True
            up = any(w in t for w in UP_WORDS)
            dn = any(w in t for w in DOWN_WORDS)
            # 'rate cut' / 'cut rates' is dovish-positive; don't let bare 'cut' flip it
            if "cut rates" in t or "rate cut" in t:
                up, dn = True, False
            score += (1 if up else 0) - (1 if dn else 0)
    if not hit:
        return None
    return 1 if score > 0 else -1 if score < 0 else 0


# ---------------------------------------------------------------------------
# 4. small structural helpers
# ---------------------------------------------------------------------------
# sentences() now lives in newsindex/textutil.py (single definition);
# re-exported here so existing `common.sentences(...)` call sites keep working.

# dedupe() now lives in newsindex/textutil.py (single definition);
# re-exported here so existing `common.dedupe(...)` call sites keep working.

