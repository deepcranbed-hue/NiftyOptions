#!/usr/bin/env python3
"""
rbi_credit.py — CANONICAL source for Indian banking-system credit & deposit growth.

Why this exists
---------------
The sector factor model listed `credit_growth` and `deposit_growth` as Banks factors
carrying ~30% of the sector's nominal weight — but tagged them "needs data", so they
NEVER fired. The only positive channel left was the earnings kicker, which (until
fixed) was unconditionally bullish. Net effect: the model said "banks look good during
results season" by construction, not from evidence. This module closes that gap.

Source: RBI Weekly Statistical Supplement (WSS), "Scheduled Commercial Banks —
Business in India". Bank credit & aggregate deposits, released FORTNIGHTLY.

THE KEY INSIGHT — the GAP matters more than the levels
------------------------------------------------------
Credit growth alone looks unambiguously bullish for banks. It isn't. When credit
grows much faster than deposits, banks must fund the gap with costlier borrowings
(CDs, bulk deposits, refinance) → **cost of funds rises → NIM compresses**. So:

    credit growth ↑        → +ve (volume / earning assets)
    deposit growth ↑       → +ve (cheap, stable funding)
    credit − deposit gap ↑ → −ve (funding stress, NIM pressure)

A desk reading "credit +18.6%" as bullish while deposits grow 13.5% is reading half
the story: a >500bp gap is a margin headwind, not a tailwind.

DRY: this is the ONLY place banking-system credit/deposit data is defined. Both
market_scan.py and NewsAgent import from here — never re-implement.

Usage
-----
    import rbi_credit
    d = rbi_credit.get()          # cached; fetches if stale
    d["credit_growth_yoy"]        # e.g. 18.6
    d["deposit_growth_yoy"]       # e.g. 13.5
    d["cd_gap_bps"]               # e.g. 510
    d["signals"]                  # normalised [-1,+1] for the factor model
    d["stale"], d["as_of"], d["source"]

CLI:
    python3 rbi_credit.py            # show current values
    python3 rbi_credit.py --refresh  # force re-fetch
    python3 rbi_credit.py --set 18.6 13.5 2026-07-11   # manual override
"""

from __future__ import annotations

import json
import re
import sys
import datetime as dt
from pathlib import Path

try:
    import requests
except Exception:                                     # requests is optional
    requests = None

_HERE = Path(__file__).resolve().parent
CACHE = _HERE / "rbi_credit_cache.json"
MANUAL = _HERE / "rbi_manual.json"          # hand-entered fallback, wins if newer

WSS_INDEX = "https://www.rbi.org.in/Scripts/WSSView.aspx"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122 Safari/537.36")

# RBI publishes fortnightly; anything older than this is stale (data gap / holiday drift)
STALE_DAYS = 24

# ---- normalisation bands (PRIOR — judgement, documented, not fitted) -------
# Long-run SCB credit growth sits ~11-16%; deposits ~9-13%. Centre each band on
# its historical median so "normal" reads 0.0 rather than bullish.
CREDIT_MID, CREDIT_SPAN = 12.0, 6.0     # 18% -> +1.0, 6% -> -1.0
DEPOSIT_MID, DEPOSIT_SPAN = 11.0, 5.0   # 16% -> +1.0, 6% -> -1.0
GAP_MID_BPS, GAP_SPAN_BPS = 100.0, 400.0  # +500bp gap -> -1.0 (NIM stress)


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _signals(credit, deposit):
    """Map raw y/y growth to normalised [-1,+1] factor signals."""
    s = {}
    if credit is not None:
        s["credit_growth"] = round(_clamp((credit - CREDIT_MID) / CREDIT_SPAN), 3)
    if deposit is not None:
        s["deposit_growth"] = round(_clamp((deposit - DEPOSIT_MID) / DEPOSIT_SPAN), 3)
    if credit is not None and deposit is not None:
        gap = (credit - deposit) * 100.0                     # -> bps
        # sign is applied by the factor table (cd_gap carries sign -1); the SIGNAL
        # itself is "how stretched is funding", positive = more stretched.
        s["cd_gap"] = round(_clamp((gap - GAP_MID_BPS) / GAP_SPAN_BPS), 3)
    return s


# ------------------------------------------------------------------ parsing
# WSS table rows look like: "Bank Credit  ... 1,89,45,123 ... 18.6" — layouts shift
# between releases, so we match on the LABEL and take the last percentage on the line.
_PCT = re.compile(r"(-?\d{1,2}\.\d)\s*$")


def _row_pct(text: str, labels) -> float | None:
    for line in (text or "").splitlines():
        low = line.lower()
        if any(lb in low for lb in labels):
            m = _PCT.search(line.strip())
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
    return None


def _parse_wss(html_or_text: str):
    """Extract (credit_yoy, deposit_yoy) from a WSS page. Returns (None, None) on miss."""
    txt = html_or_text
    if "<" in txt[:2000]:                       # strip tags if we got raw HTML
        try:
            import trafilatura
            txt = trafilatura.extract(html_or_text, include_tables=True) or html_or_text
        except Exception:
            txt = re.sub(r"<[^>]+>", " ", html_or_text)
    txt = re.sub(r"[ \t]+", " ", txt)
    credit = _row_pct(txt, ["bank credit", "total credit", "loans and advances"])
    deposit = _row_pct(txt, ["aggregate deposits", "total deposits", "deposits"])
    return credit, deposit


def _fetch_live():
    """Best-effort live fetch. Returns dict or None. Never raises."""
    if requests is None:
        return None
    try:
        r = requests.get(WSS_INDEX, headers={"User-Agent": UA}, timeout=20)
        if r.status_code != 200:
            return None
        credit, deposit = _parse_wss(r.text)
        if credit is None and deposit is None:
            return None
        return {
            "credit_growth_yoy": credit,
            "deposit_growth_yoy": deposit,
            "as_of": dt.date.today().isoformat(),
            "source": WSS_INDEX,
            "method": "rbi_wss",
        }
    except Exception:
        return None


# ------------------------------------------------------------------- cache
def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _save(rec: dict):
    try:
        CACHE.write_text(json.dumps(rec, indent=2))
    except Exception:
        pass


def _finalise(rec: dict) -> dict:
    """Attach derived fields: gap, signals, staleness."""
    c = rec.get("credit_growth_yoy")
    d = rec.get("deposit_growth_yoy")
    rec["cd_gap_bps"] = round((c - d) * 100.0) if (c is not None and d is not None) else None
    rec["signals"] = _signals(c, d)
    age = None
    try:
        age = (dt.date.today() - dt.date.fromisoformat(rec.get("as_of", ""))).days
    except Exception:
        pass
    rec["age_days"] = age
    rec["stale"] = (age is None) or (age > STALE_DAYS)
    return rec


def get(refresh: bool = False) -> dict:
    """
    Canonical accessor. Order of preference:
      1. manual override (rbi_manual.json) if it is the newest
      2. cached fetch, if fresh and not refresh
      3. live RBI fetch
      4. cached fetch even if stale (flagged)
    Always returns a dict; `signals` is {} when we have nothing.
    """
    manual = _load(MANUAL)
    cached = _load(CACHE)

    if not refresh and cached and not _finalise(dict(cached)).get("stale"):
        best = cached
    else:
        live = _fetch_live()
        if live:
            _save(live)
            best = live
        else:
            best = cached

    # manual override wins when it is at least as recent as what we have
    if manual and manual.get("credit_growth_yoy") is not None:
        if not best or (manual.get("as_of", "") >= (best.get("as_of", "") or "")):
            manual = dict(manual)
            manual.setdefault("source", "manual (rbi_manual.json)")
            manual.setdefault("method", "manual")
            best = manual

    if not best:
        return {"credit_growth_yoy": None, "deposit_growth_yoy": None, "cd_gap_bps": None,
                "signals": {}, "stale": True, "age_days": None, "as_of": None,
                "source": None, "method": "unavailable",
                "note": "no RBI data — run `python3 rbi_credit.py --set <credit> <deposit> <YYYY-MM-DD>`"}
    return _finalise(dict(best))


def summary_line(d: dict | None = None) -> str:
    """One-line desk summary, including the GAP read (the part desks miss)."""
    d = d or get()
    c, dep, gap = d.get("credit_growth_yoy"), d.get("deposit_growth_yoy"), d.get("cd_gap_bps")
    if c is None and dep is None:
        return "RBI credit/deposit: _no data_ (factors inactive — Banks score is macro-only)."
    bits = []
    if c is not None:
        bits.append(f"credit **{c:+.1f}%** y/y")
    if dep is not None:
        bits.append(f"deposits **{dep:+.1f}%** y/y")
    s = "RBI banking system: " + ", ".join(bits)
    if gap is not None:
        if gap > 300:
            s += (f" · **gap {gap:+.0f}bp** → credit outpacing deposits: funding cost ↑, "
                  f"**NIM headwind** (not the tailwind a raw credit number implies)")
        elif gap < -100:
            s += f" · gap {gap:+.0f}bp → deposits outpacing credit: comfortable funding, NIM support"
        else:
            s += f" · gap {gap:+.0f}bp → balanced funding"
    if d.get("stale"):
        s += f" · ⚠️ stale ({d.get('age_days')}d old — RBI is fortnightly)"
    return s + "."


# --------------------------------------------------------------------- CLI
if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--set":
        if len(args) < 4:
            print("usage: python3 rbi_credit.py --set <credit_yoy> <deposit_yoy> <YYYY-MM-DD>")
            sys.exit(1)
        rec = {"credit_growth_yoy": float(args[1]), "deposit_growth_yoy": float(args[2]),
               "as_of": args[3], "source": "manual (rbi_manual.json)", "method": "manual"}
        MANUAL.write_text(json.dumps(rec, indent=2))
        print("saved manual override ->", MANUAL)
        print(summary_line(_finalise(rec)))
        sys.exit(0)

    data = get(refresh=("--refresh" in args))
    print(json.dumps(data, indent=2))
    print()
    print(summary_line(data))
