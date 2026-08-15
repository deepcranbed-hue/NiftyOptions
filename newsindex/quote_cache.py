#!/usr/bin/env python3
"""
quote_cache.py — last-known-good quote store, so a transient fetch failure never
blanks the report.

Why
---
A single Yahoo 429 killed Brent for a whole run: the level vanished, the $10 band
table went dormant, the ×1.4 amplifier reverted to 1.0, and every oil-driven sector
score quietly changed. One rate-limited HTTP call silently altered the verdict.

That is a resilience bug, not a data bug. The fix is not a different transport —
switching to a headless browser hits the SAME endpoint from the SAME IP and gets the
same 429. The fix is to remember what we already knew.

Key insight: LEVELS are slow variables. Brent at $88 yesterday is still ~$88 today for
the purpose of "which band are we in" — a cached level keeps the entire level subsystem
alive. MOVES are fast variables and must NOT be reused: a stale pct_change would be an
outright false statement about today. So:

    last / previous_close  → cached and reused, flagged stale
    pct_change             → NEVER reused; stays None if today's fetch failed

Usage
-----
    import quote_cache
    quotes = fetch_quotes(MACRO)            # some rows may be empty
    stats  = quote_cache.apply(quotes)      # fill gaps from cache, in place
    quote_cache.save(quotes)                # remember today's good rows

    stats -> {"filled": 2, "saved": 11, "stale": [("Brent Crude", 1)]}
"""

from __future__ import annotations

import json
import math
import datetime as dt
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "quote_cache.json"

# A level older than this is too stale to be trustworthy even as a band proxy —
# oil can traverse a $10 band in a fortnight.
MAX_AGE_DAYS = 10


def _num(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def _key(q: dict) -> str:
    return q.get("symbol") or q.get("name") or ""


def save(*quote_lists) -> int:
    """Persist every row that has a usable level. Returns rows saved."""
    db = _load()
    today = dt.date.today().isoformat()
    n = 0
    for ql in quote_lists:
        for q in ql or []:
            k = _key(q)
            lvl = _num(q.get("last")) or _num(q.get("previous_close"))
            if not k or lvl is None:
                continue
            db[k] = {"name": q.get("name"), "last": _num(q.get("last")),
                     "previous_close": _num(q.get("previous_close")),
                     "as_of": today}
            n += 1
    try:
        CACHE.write_text(json.dumps(db, indent=2, sort_keys=True))
    except Exception:
        return 0
    return n


def apply(*quote_lists) -> dict:
    """
    Fill missing LEVELS from cache, in place.

    DISABLED BY DEFAULT — set NEWSAGENT_QUOTE_CACHE_FILL=1 to enable.

    Rationale for the default: for a market report an honest "NO BRENT DATA" beats a
    plausible-looking stale price. A missing value is visibly missing and the reader
    discounts that section; a cached value silently propagates into the oil band, the
    level amplifier and every oil-driven sector score while looking entirely legitimate.
    That is the exact failure mode — a number that looks like evidence but isn't — that
    this engine spent a lot of effort eliminating elsewhere.

    The cache still RECORDS every run (see save()), so it retains diagnostic value:
    "we had Brent at 88.1 yesterday, so today's blank is a genuine outage, not a
    never-worked" is a useful thing to be able to check. It just doesn't feed the report.
    """
    import os
    if os.environ.get("NEWSAGENT_QUOTE_CACHE_FILL", "0") != "1":
        return {"filled": 0, "stale": [], "unavailable": [],
                "disabled": "cache fill is OFF by default — a missing price is reported "
                            "honestly rather than back-filled with a stale one. "
                            "Set NEWSAGENT_QUOTE_CACHE_FILL=1 to enable."}
    db = _load()
    if not db:
        return {"filled": 0, "stale": [], "unavailable": []}
    today = dt.date.today()
    filled, stale, unavailable = 0, [], []
    for ql in quote_lists:
        for q in ql or []:
            if _num(q.get("last")) is not None:
                continue                                  # today's data is fine
            rec = db.get(_key(q))
            if not rec:
                unavailable.append(q.get("name"))
                continue
            try:
                age = (today - dt.date.fromisoformat(rec.get("as_of", ""))).days
            except Exception:
                age = 999
            lvl = rec.get("last") if rec.get("last") is not None else rec.get("previous_close")
            if lvl is None or age > MAX_AGE_DAYS:
                unavailable.append(q.get("name"))
                continue
            q["last"] = lvl
            q["cached"] = True
            q["cache_age_days"] = age
            # pct_change deliberately untouched — see module docstring.
            filled += 1
            stale.append((q.get("name"), age))
    return {"filled": filled, "stale": stale, "unavailable": unavailable}


def note(stats: dict) -> str:
    """One-line report banner describing what was served from cache."""
    if not stats or not stats.get("filled"):
        return ""
    bits = ", ".join(f"{n} ({a}d old)" for n, a in stats.get("stale", [])[:6])
    return (f"ℹ️ **{stats['filled']} quote level(s) served from cache** — {bits}. "
            f"Levels are reused (slow variable, fine for band logic); today's % moves are "
            f"NOT reused and stay blank for these names.")


if __name__ == "__main__":
    db = _load()
    print(f"{len(db)} cached quote(s) in {CACHE}")
    today = dt.date.today()
    for k, v in sorted(db.items()):
        try:
            age = (today - dt.date.fromisoformat(v.get("as_of", ""))).days
        except Exception:
            age = "?"
        lvl = v.get("last") if v.get("last") is not None else v.get("previous_close")
        print(f"  {k:14} {v.get('name','')[:22]:24} {lvl!s:>10}  {age}d old")
