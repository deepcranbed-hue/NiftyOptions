"""
calibration.py — wire the EXISTING calibration into the overlay.

The project already calibrates:
  * build_events.py -> events.db `linkage_conf`  = historical HIT-RATE per relationship
    (e.g. "US semis (SOX) -> Indian IT/EMS": 57%, n=1323), refreshed from ~3-6y of history.
  * build_events.py -> events.db `event_stats`   = historical ANALOGUES per condition
    (e.g. sox_drop_3, oil_up_3, vix_spike_5, riskoff_combo) with mean/median/hit_down.
  * calibrate.py     -> suggested_sensitivity.py  = regression-fitted coefficients + R².

So `historical_reliability` should NOT be a hard-coded PRIOR — it is a real, calibrated
number sitting in events.db. This module loads it and grades it:
    n >= 60  -> CALIBRATED
    n <  60  -> PRIOR (descriptive only)  (mirrors D-MA-04)

Everything is read-only; nothing here re-runs the crawler or the regression — it consumes
what the existing pipeline already produced. Run `python build_events.py` / `calibrate.py`
to refresh the underlying numbers.
"""
from __future__ import annotations

import re

_CACHE: dict | None = None
_MIN_SESSIONS = 60


def _load() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    linkages, events = {}, {}
    try:
        import build_events as be   # importable: core.py put NEWSINDEX_HOME on sys.path
        linkages = be.load_linkage_conf() or {}
        try:
            events = be.load_event_stats() or {}
        except Exception:
            events = {}
    except Exception:
        pass
    _CACHE = {"linkages": linkages, "events": events}
    return _CACHE


def available() -> bool:
    d = _load()
    return bool(d["linkages"])


def _tokens(s: str) -> set:
    """Tokenize a relationship name into a word-set (for fuzzy linkage matching).
    NB: distinct from common.norm (numeric normalization) — this is name matching."""
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _wilson(hits: int, n: int, z: float = 1.96):
    """95% Wilson score interval for a proportion, in percent. Robust at small n."""
    if not n:
        return (None, None)
    p = hits / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (round(max(0.0, c - h) * 100, 1), round(min(1.0, c + h) * 100, 1))


def grade(hit_rate_pct, n) -> dict:
    """hit_rate as 0-100 + sample n -> a graded reliability object.

    n >= 60 was a SAMPLE-SIZE gate, not a SIGNIFICANCE gate — so a linkage could be
    stamped CALIBRATED while being statistically indistinguishable from a coin flip,
    or even inverted. "Weak rupee → IT exporters up" scores 46% on n=1312: plenty of
    rows, and its 95% interval (43.4–48.7%) sits ENTIRELY BELOW 50%. Large n made it
    look authoritative; it is evidence the rule is backwards.

    The grade now depends on where the interval sits relative to chance:
        lower bound > 55   -> CALIBRATED   (a real, usable edge)
        lower bound > 50   -> MARGINAL     (positive, but thin)
        upper bound < 50   -> INVERTED     (sign is probably wrong)
        otherwise          -> NOISE        (indistinguishable from a coin flip)
    Anything under _MIN_SESSIONS stays PRIOR regardless — too few rows to judge.
    """
    if hit_rate_pct is None or n is None:
        return {"value": None, "n": n, "tag": "PRIOR", "source": "events.db"}

    lo, hi = _wilson(round(hit_rate_pct / 100.0 * n), n)
    if n < _MIN_SESSIONS:
        tag, verdict = "PRIOR", f"only {n} observations — descriptive only"
    elif lo is not None and lo > 55:
        tag, verdict = "CALIBRATED", "edge is real at 95% confidence"
    elif lo is not None and lo > 50:
        tag, verdict = "MARGINAL", "positive but the interval is thin"
    elif hi is not None and hi < 50:
        tag, verdict = "INVERTED", "95% interval sits BELOW chance — sign likely backwards"
    else:
        tag, verdict = "NOISE", "interval spans 50% — not distinguishable from a coin flip"

    return {
        "value": round(hit_rate_pct / 100.0, 3),
        "hit_rate_pct": hit_rate_pct,
        "n": n,
        "ci95": [lo, hi],
        "tag": tag,
        "verdict": verdict,
        # kept so anything downstream that tested `tag == "CALIBRATED"` to mean
        # "has enough rows" still gets that answer explicitly
        "sample_ok": n >= _MIN_SESSIONS,
        "source": "events.db/linkage_conf",
    }


def reliability_for(name: str) -> dict | None:
    """Match a relationship/edge name to a calibrated linkage hit-rate.
    Exact key match first, then best token-overlap match."""
    d = _load()
    linkages = d["linkages"]
    if not linkages or not name:
        return None
    if name in linkages:
        v = linkages[name]
        return grade(v.get("hit_rate"), v.get("n")) | {"matched": name}
    want = _tokens(name)
    best, best_score = None, 0.0
    for key, v in linkages.items():
        kt = _tokens(key)
        if not kt:
            continue
        overlap = len(want & kt) / max(1, len(want | kt))
        if overlap > best_score:
            best, best_score = (key, v), overlap
    if best and best_score >= 0.34:
        key, v = best
        return grade(v.get("hit_rate"), v.get("n")) | {"matched": key, "match_score": round(best_score, 2)}
    return None


# driver_key -> the calibrated linkage that best represents its transmission
DRIVER_LINKAGE = {
    "oil_pct": "Oil → producers up / users down",
    "sox_pct": "US semis (SOX) → Indian IT services",
    "kospi_pct": "Kospi (AI proxy) → Indian IT",
    "us10y_pct": "Rising US yields → banks pressured",
    "usdinr": "Weak rupee → IT exporters up",
}

# regime/condition -> event_stats analogue key
EVENT_KEYS = {
    "sox_drop": "sox_drop_3",
    "oil_up": "oil_up_3",
    "oil_down": "oil_down_2",
    "vix_spike": "vix_spike_5",
    "riskoff": "riskoff_combo",
}


def reliability_for_driver(driver_key: str) -> dict | None:
    name = DRIVER_LINKAGE.get(driver_key)
    return reliability_for(name) if name else None


def event_analogue(key: str) -> dict | None:
    """Return the historical analogue stats for a condition key (e.g. 'sox_drop_3')."""
    d = _load()
    ev = d["events"]
    if not ev:
        return None
    if key in ev:
        return ev[key]
    # allow short keys like 'sox_drop'
    full = EVENT_KEYS.get(key)
    return ev.get(full) if full else None
