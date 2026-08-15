#!/usr/bin/env python3
"""
diagnose_quotes.py — find out EXACTLY which quote tier is failing, and why.

Written because "NO BRENT DATA" persisted across three fixes and I was guessing at the
cause from a machine with no network. This runs each tier in isolation on YOUR box and
reports pass/fail per tier, so the next fix targets the real failure instead of a
plausible one.

Usage:
    cd NewsAgent
    python3 diagnose_quotes.py                # diagnose BZ=F (Brent)
    python3 diagnose_quotes.py CL=F GC=F      # other symbols
    python3 diagnose_quotes.py --snapshot     # also run a live snapshot + show quote_fallback
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE / "overlay", HERE / "engine", HERE / "mcp_server", HERE.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

SYMS = [a for a in sys.argv[1:] if not a.startswith("--")] or ["BZ=F"]
WANT_SNAP = "--snapshot" in sys.argv


def hdr(t):
    print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")


def ok(b):
    return "✅" if b else "❌"


hdr("0 · environment")
print(f"  interpreter: {sys.executable}")
_missing_yf = False
for mod in ("yfinance", "playwright", "requests"):
    try:
        __import__(mod)
        print(f"  {ok(True)} {mod} importable")
    except Exception as e:
        print(f"  {ok(False)} {mod} — {type(e).__name__}: {str(e)[:60]}")
        if mod == "yfinance":
            _missing_yf = True
if _missing_yf:
    print("\n  🚨 ROOT CAUSE: yfinance is missing from THIS interpreter, so EVERY quote")
    print("     fetch fails and every row falls through to the backfill tiers. This is a")
    print("     dependency problem, not a rate-limit problem. Install it into the SAME")
    print("     interpreter that runs the report:")
    print(f"       {sys.executable} -m pip install yfinance")

import os
for var in ("NEWSAGENT_QUOTE_FALLBACK", "NEWSAGENT_PW_QUOTES", "NEWSAGENT_METALS_WEB"):
    v = os.environ.get(var)
    print(f"  {'⚠️' if v == '0' else '  '} {var}={v or '(unset → enabled)'}")

# ---------------------------------------------------------------- tier 0
hdr("1 · TIER 0 — yfinance (the API that 429s)")
try:
    import market_engine as ms
    rows = ms.fetch_quotes({"Brent Crude": SYMS[0]})
    r = rows[0] if rows else {}
    print(f"  last={r.get('last')}  prev_close={r.get('previous_close')}  pct={r.get('pct_change')}")
    print(f"  {ok(r.get('last') is not None)} yfinance returned a level")
except Exception as e:
    print(f"  ❌ {type(e).__name__}: {str(e)[:100]}")

# ---------------------------------------------------------------- tiers
import resilient_quotes as rq

gaps = {s: {"name": s, "symbol": s, "last": None, "pct_change": None} for s in SYMS}

hdr("2 · TIER 1 — NSE / Stooq")
try:
    print(f"  NSE  : {rq._nse_backfill(gaps)}")
except Exception as e:
    print(f"  NSE  ❌ {type(e).__name__}: {str(e)[:80]}")
try:
    mapped = {s: rq._STOOQ.get(s) for s in SYMS}
    print(f"  Stooq mapping: {mapped}")
    print(f"  Stooq: {rq._stooq_backfill(gaps)}")
except Exception as e:
    print(f"  Stooq ❌ {type(e).__name__}: {str(e)[:80]}")

hdr("3 · TIER 2b — TradingEconomics (static HTML, no browser)")
try:
    print(f"  anchors: { {s: rq._TE_ANCHOR.get(s) for s in SYMS} }")
    import metals_web
    raw = metals_web._fetch_text(metals_web.DEFAULT_URL)
    print(f"  page fetched: {len(raw):,} chars")
    for s in SYMS:
        a = rq._TE_ANCHOR.get(s)
        if a:
            i = raw.lower().find(a.lower())
            print(f"    anchor {a!r} found at char {i}" if i >= 0 else f"    ⚠️ anchor {a!r} NOT on page")
            if i >= 0:
                print(f"      context: …{raw[i:i+90].strip()}…")
    print(f"  parsed: {rq._te_commodities(gaps)}")
except Exception as e:
    print(f"  ❌ {type(e).__name__}: {str(e)[:120]}")

hdr("4 · TIER 2c — browser, rotated across hosts")
try:
    print(f"  result: {rq._playwright_quotes(gaps)}")
except Exception as e:
    print(f"  ❌ {type(e).__name__}: {str(e)[:120]}")

hdr("5 · TIER 3 — last-known-good cache")
try:
    import quote_cache as qc
    db = qc._load()
    print(f"  cache entries: {len(db)}")
    for s in SYMS:
        print(f"    {s}: {db.get(s, '(absent — nothing cached yet)')}")
except Exception as e:
    print(f"  ❌ {type(e).__name__}: {str(e)[:120]}")

hdr("6 · FULL backfill() over a synthetic gap")
snap = {"quotes_macro": [{"name": "Brent Crude", "symbol": s, "last": None,
                          "previous_close": None, "pct_change": None} for s in SYMS]}
try:
    stats = rq.backfill(snap)
    print(f"  stats: {stats}")
    for q in snap["quotes_macro"]:
        print(f"  row  : {q}")
except Exception as e:
    print(f"  ❌ {type(e).__name__}: {str(e)[:150]}")

if WANT_SNAP:
    hdr("7 · LIVE snapshot — what the pipeline actually got")
    try:
        import core
        core.refresh_snapshot()
        s = core._ensure()
        print(f"  quote_fallback: {s.get('quote_fallback')}")
        print(f"  fetch_timeouts: {s.get('fetch_timeouts')}")
        for q in s.get("quotes_macro", []):
            if "brent" in (q.get("name") or "").lower():
                print(f"  BRENT ROW: {q}")
        eng = core.run_engine()
        print(f"  engine brent_price={eng.get('brent_price')} src={eng.get('brent_src')} "
              f"oil_mult={eng.get('oil_mult')} missing={eng.get('missing_drivers')}")
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {str(e)[:150]}")

print("\nSend the output above and the failing tier can be fixed directly.\n")
