"""
resilient_quotes.py — multi-source price fallback so a Yahoo outage doesn't blank the report.

The engine fetches every quote from ONE source (Yahoo/yfinance). When Yahoo rate-limits or 403s,
those rows come back with last=None / NaN and cascade into the whole report. This module runs AFTER
the engine builds the snapshot and BACKFILLS only the rows Yahoo failed to return, from:

    1. NSE India API   — the authoritative exchange source for Indian indices & .NS stocks
                         (https://www.nseindia.com/api/allIndices, /api/quote-equity)
    2. Stooq           — keyless CSV for global tickers (indices/US) when NSE doesn't cover them

News is already multi-source (8 RSS feeds + Google News) — this only hardens PRICES.

It never overwrites a good Yahoo value; it only fills gaps, tags row['source'], and degrades
silently if a fallback is unreachable. Disable with NEWSAGENT_QUOTE_FALLBACK=0.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import urllib.request

_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
       "Accept": "application/json, text/csv, */*",
       "Accept-Language": "en-US,en;q=0.9"}

# yfinance index symbol -> NSE allIndices name
_NSE_INDEX = {
    "^NSEI": "NIFTY 50", "^NSEBANK": "NIFTY BANK", "^CNXIT": "NIFTY IT",
    "^CNXAUTO": "NIFTY AUTO", "^INDIAVIX": "INDIA VIX", "^CNXPHARMA": "NIFTY PHARMA",
    "^CNXFMCG": "NIFTY FMCG", "^CNXMETAL": "NIFTY METAL", "^CNXENERGY": "NIFTY ENERGY",
}
# yfinance global symbol -> stooq symbol.
# The old comment claimed "commodities now come from tradingeconomics" — but that path
# (metals_web) covers the METALS complex, NOT crude. So BZ=F had no fallback anywhere:
# NSE skips it (not an index), Stooq skipped it (not mapped), tradingeconomics doesn't
# carry it. A failed Yahoo call for Brent therefore killed the entire oil LEVEL subsystem
# silently. Commodity futures are mapped here so crude has a second source.
# NOTE: these Stooq futures tickers are best-effort and unverified from this environment;
# _stooq_backfill() already skips anything that fails, so a wrong ticker degrades to the
# previous behaviour rather than breaking the run. Verify on a live box.
# VERIFIED ON A LIVE BOX: Stooq 404s for the commodity futures tickers I guessed
# (cb.f for Brent). Removed rather than left in as a dead lookup — commodities are now
# covered by the Yahoo-HTML tier and by TradingEconomics, both confirmed working.
# Do not re-add a commodity mapping here without testing the CSV endpoint first:
#   https://stooq.com/q/l/?s=<ticker>&f=sd2t2ohlcvn&e=csv
_STOOQ = {"^IXIC": "^ndq", "^DJI": "^dji", "^GSPC": "^spx", "^SOX": "^sox", "^KS11": "^kospi"}


def _num(x):
    try:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            x = float(x)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _needs_fix(q):
    # A quote needs a fallback if it is MISSING data OR flagged SUSPECT. The suspect case
    # is the one that bit Brent: yfinance returned $84.57 / -8.26% (a stale/unadjusted
    # tick, range-check-flagged) — present but WRONG. Only checking for missing data let
    # that flagged value pass through while a correct $94 sat in the next tier.
    return (_num(q.get("last")) is None or _num(q.get("pct_change")) is None
            or bool(q.get("suspect")))


# ---- NSE India ------------------------------------------------------------
def _nse_session():
    """Prime cookies by hitting the homepage first (NSE blocks cold API calls)."""
    cj = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(cj)
    opener.addheaders = list(_UA.items())
    try:
        opener.open("https://www.nseindia.com", timeout=8).read(2000)
    except Exception:
        pass
    return opener


def _nse_json(opener, url):
    req = urllib.request.Request(url, headers=_UA)
    with opener.open(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _nse_backfill(rows_by_symbol: dict) -> dict:
    """rows_by_symbol: {yf_symbol: row}. Returns {yf_symbol: (last, pct)} it could fill."""
    out = {}
    opener = _nse_session()
    # indices in one call
    idx_syms = [s for s in rows_by_symbol if s in _NSE_INDEX]
    if idx_syms:
        try:
            data = _nse_json(opener, "https://www.nseindia.com/api/allIndices").get("data", [])
            by_name = {d.get("index"): d for d in data}
            for s in idx_syms:
                d = by_name.get(_NSE_INDEX[s])
                if d:
                    out[s] = (_num(d.get("last")), _num(d.get("percentChange")))
        except Exception:
            pass
    # stocks one at a time (only .NS names)
    for s, row in rows_by_symbol.items():
        if s in _NSE_INDEX or not s.endswith(".NS"):
            continue
        nse_sym = s[:-3]
        try:
            pi = _nse_json(opener,
                           f"https://www.nseindia.com/api/quote-equity?symbol={urllib.parse.quote(nse_sym)}")
            p = pi.get("priceInfo", {})
            last, pct = _num(p.get("lastPrice")), _num(p.get("pChange"))
            if last is not None:
                out[s] = (last, pct)
        except Exception:
            continue
    return out


# ---- Stooq (global) -------------------------------------------------------
def _stooq_backfill(rows_by_symbol: dict) -> dict:
    out = {}
    for s in rows_by_symbol:
        st = _STOOQ.get(s)
        if not st:
            continue
        try:
            url = f"https://stooq.com/q/l/?s={urllib.parse.quote(st)}&f=sd2t2ohlcvn&e=csv"
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=8) as r:
                text = r.read().decode("utf-8", "ignore")
            row = next(csv.DictReader(io.StringIO(text)), {})
            close, opn = _num(row.get("Close")), _num(row.get("Open"))
            if close is not None and opn not in (None, 0):
                out[s] = (close, round((close - opn) / opn * 100, 2))
        except Exception:
            continue
    return out


import urllib.parse  # noqa: E402  (after functions to keep the header tidy)


# ---- Yahoo via a real browser --------------------------------------------
# yfinance calls query1.finance.yahoo.com/v8/finance/chart/... — the API, which Yahoo
# rate-limits hard (429) and increasingly gates behind crumb/cookie auth. The consumer
# WEBSITE (finance.yahoo.com/quote/XXX) is a different path with much laxer limits
# because it is built for browsers. So when the API 429s, the page usually still loads
# fine — which is why the price is visible in a normal browser during an outage.
#
# This is the tier that actually rescues commodity futures (BZ=F), which neither NSE
# nor Stooq covers reliably.
_PW_MAX = 6          # cap browser fetches per run — one browser, a few pages
_PW_TIMEOUT_MS = 15000


def _yahoo_html(rows_by_symbol: dict, limit: int = 12) -> dict:
    """
    Yahoo quote page as STATIC HTML — no browser, no API.

    The API (query1.finance.yahoo.com/v8/finance/chart) 429s constantly and now wants
    crumb/cookie auth. The consumer page has much laxer limits AND embeds the live quote
    directly in server-rendered <fin-streamer> tags:

        <fin-streamer data-symbol="BZ=F" data-field="regularMarketPrice" data-value="90.84">

    So a plain GET + regex gets the number without rendering anything. This covers the
    whole symbol universe (indices, stocks, FX, futures), unlike NSE (Indian only) or
    TradingEconomics (commodities only).
    """
    if os.environ.get("NEWSAGENT_YAHOO_HTML", "1") == "0":
        return {}
    out = {}
    for sym in list(rows_by_symbol)[:limit]:
        try:
            url = f"https://finance.yahoo.com/quote/{urllib.parse.quote(sym)}"
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                html = r.read().decode("utf-8", "ignore")
            price = pct = None
            # attribute order is not guaranteed — scan each tag's attribute blob
            for m in re.finditer(r"<fin-streamer([^>]*)>", html):
                at = m.group(1)
                if sym not in at and 'data-symbol="' in at:
                    continue                       # a different symbol on the same page
                v = re.search(r'data-value="(-?[\d.]+)"', at)
                if not v:
                    continue
                if "regularMarketPrice" in at and price is None:
                    price = _num(v.group(1))
                elif "regularMarketChangePercent" in at and pct is None:
                    pct = _num(v.group(1))
            if price is None:                      # fallback: embedded JSON blob
                j = re.search(r'"regularMarketPrice"\s*:\s*\{\s*"raw"\s*:\s*(-?[\d.]+)', html)
                price = _num(j.group(1)) if j else None
                j2 = re.search(r'"regularMarketChangePercent"\s*:\s*\{\s*"raw"\s*:\s*(-?[\d.]+)', html)
                pct = _num(j2.group(1)) if j2 else pct
            if price is not None:
                out[sym] = (price, round(pct, 2) if pct is not None else None)
        except Exception:
            continue
    return out


# yfinance symbol -> TradingEconomics anchor on /commodities (static HTML, no browser)
_TE_ANCHOR = {
    "BZ=F": "Brent", "CL=F": "Crude Oil", "GC=F": "Gold",
    "SI=F": "Silver", "HG=F": "Copper", "NG=F": "Natural gas",
}


def _te_commodities(rows_by_symbol: dict) -> dict:
    """{symbol: (price, pct)} from tradingeconomics.com/commodities. Reuses the parser
    that metals_web already proves against this page — no new scraper, no browser."""
    wanted = {s: _TE_ANCHOR[s] for s in rows_by_symbol if s in _TE_ANCHOR}
    if not wanted:
        return {}
    try:
        import metals_web
        quotes = metals_web.fetch_commodity_quotes(sorted(set(wanted.values())))
    except Exception:
        return {}
    out = {}
    for sym, anchor in wanted.items():
        price, pct = (quotes.get(anchor) or (None, None))
        if price is not None:
            out[sym] = (price, pct)
    return out


# Browser tier, ROTATED ACROSS HOSTS. Hitting one site for every missing symbol is what
# invites rate-limiting in the first place; spreading the load keeps each host's request
# count low and gives redundancy when one layout drifts. Each entry is
# (host_label, url_template, [css selectors to try], attr or None for text).
_PW_SITES = [
    ("yahoo", "https://finance.yahoo.com/quote/{q}",
     ['fin-streamer[data-field="regularMarketPrice"]'], "data-value"),
    ("marketwatch", "https://www.marketwatch.com/investing/future/{mw}",
     ["bg-quote.value", ".intraday__price .value"], None),
    ("cnbc", "https://www.cnbc.com/quotes/{cnbc}",
     ['span.QuoteStrip-lastPrice', '.QuoteStrip-lastTimeAndPriceContainer span'], None),
]
# per-site symbol translation; a site is skipped for symbols it has no mapping for
_PW_SYMMAP = {
    "marketwatch": {"BZ=F": "brentcrude", "CL=F": "crudeoil", "GC=F": "gold", "SI=F": "silver"},
    "cnbc":        {"BZ=F": "@LCO.1", "CL=F": "@CL.1", "GC=F": "@GC.1", "SI=F": "@SI.1"},
}


def _playwright_quotes(rows_by_symbol: dict) -> dict:
    """Fetch {symbol: (last, pct)} with a headless browser, rotating across _PW_SITES so
    no single host absorbs every request. Best-effort; silently skipped if unavailable."""
    if os.environ.get("NEWSAGENT_PW_QUOTES", "1") == "0":
        return {}
    syms = list(rows_by_symbol)[:_PW_MAX]
    if not syms:
        return {}
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {}                     # playwright not installed -> silently skip

    out = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"),
                locale="en-IN")
            page = ctx.new_page()
            for i, sym in enumerate(syms):
                # ROTATE the starting host per symbol, then fall through the rest.
                order = _PW_SITES[i % len(_PW_SITES):] + _PW_SITES[:i % len(_PW_SITES)]
                for host, tmpl, sels, attr in order:
                    mapped = _PW_SYMMAP.get(host, {}).get(sym, sym if host == "yahoo" else None)
                    if not mapped:
                        continue
                    try:
                        url = tmpl.format(q=urllib.parse.quote(sym), mw=mapped, cnbc=mapped)
                        page.goto(url, timeout=_PW_TIMEOUT_MS, wait_until="domcontentloaded")
                        val = None
                        for sel in sels:
                            val = (page.get_attribute(sel, attr) if attr
                                   else page.text_content(sel))
                            if val:
                                break
                        v = _num((val or "").replace(",", "").replace("$", "").strip())
                        if v is not None:
                            # pct is site-specific and often absent → level only, which is
                            # what the band/amplifier logic actually needs.
                            out[sym] = (v, None)
                            break
                    except Exception:
                        continue
            browser.close()
    except Exception:
        return out
    return out


def _reconcile_commodities(snap: dict) -> dict:
    """
    COMMODITY CROSS-CHECK: yfinance is unreliable for crude (returns stale/split-adjusted
    ticks — e.g. Brent $85 when it's really $94). TradingEconomics is a live spot scrape
    and proved correct. So for commodities we compare the two DIRECTLY and trust TE when
    yfinance is suspect OR disagrees by >5%.

    Critically, this does NOT use previous_close as a reference — for a suspect commodity
    the prev_close comes from the same bad feed (~$85), which is exactly what made an
    earlier guard reject the correct $94. Two live sources are compared against each
    other, not against stale history. The raw yfinance value is kept in `yfinance_raw`
    so the report can show the discrepancy.
    """
    if os.environ.get("NEWSAGENT_COMMODITY_XCHECK", "1") == "0":
        return {}
    wanted = {}
    for q in snap.get("quotes_macro", []) or []:
        if q.get("symbol") in _TE_ANCHOR:
            wanted[q["symbol"]] = q
    if not wanted:
        return {}
    try:
        te = _te_commodities(wanted)          # {sym: (price, pct)} — static HTML, no browser
    except Exception:
        return {}
    reconciled = {}
    for sym, q in wanted.items():
        tp = te.get(sym)
        if not tp or tp[0] is None:
            continue
        te_price, te_pct = tp
        yf = _num(q.get("last"))
        suspect = bool(q.get("suspect")) or yf is None
        disagree = yf is not None and te_price and abs(yf - te_price) / te_price > 0.05
        if suspect or disagree:
            q["yfinance_raw"] = yf            # keep both for transparency
            q["last"] = round(te_price, 2)
            if te_pct is not None:
                q["pct_change"] = te_pct
            q["suspect"] = False
            q["source"] = "tradingeconomics"
            q["fallback"] = True
            reconciled[sym] = {"yfinance": yf, "tradingeconomics": te_price,
                               "used": "tradingeconomics",
                               "why": "suspect" if suspect else f"disagree {abs(yf-te_price):.1f}"}
    return reconciled


def backfill(snap: dict) -> dict:
    """Fill any Yahoo-missing quote from NSE/Stooq, in place. Returns a small stats dict."""
    if os.environ.get("NEWSAGENT_QUOTE_FALLBACK", "1") == "0":
        return {"enabled": False}
    # Commodities first — cross-check yfinance vs TradingEconomics BEFORE the generic
    # gap logic, so a WRONG-but-present crude tick (not flagged as a "gap") is still
    # corrected. Runs independent of _needs_fix.
    commodity_xcheck = _reconcile_commodities(snap)
    lists = ("quotes_idx", "quotes_macro", "quotes_stk", "it_quotes",
             "sector_quotes", "theme_quotes", "univ_quotes")
    gaps = {}
    for k in lists:
        for q in snap.get(k, []) or []:
            if q.get("symbol") and _needs_fix(q):
                gaps.setdefault(q["symbol"], q)          # first row per symbol; patch all later
    if not gaps:
        return {"enabled": True, "gaps": 0, "filled": 0,
                "commodity_xcheck": commodity_xcheck}

    filled = {}
    try:
        filled.update(_nse_backfill(gaps))
    except Exception:
        pass
    # TIER 1b: Yahoo quote page as static HTML. Universal coverage (indices, stocks, FX,
    # futures) and no browser. Placed early because it is cheap and broad; the API being
    # rate-limited says nothing about the page, which is why the price is visible in a
    # normal browser during an "outage".
    remaining = {s: r for s, r in gaps.items() if s not in filled}
    yh_syms = set()
    if remaining:
        try:
            yh = _yahoo_html(remaining)
            yh_syms = set(yh)
            filled.update(yh)
        except Exception:
            pass

    remaining = {s: r for s, r in gaps.items() if s not in filled}
    try:
        filled.update(_stooq_backfill(remaining))
    except Exception:
        pass

    # TIER 2b: TradingEconomics commodities page — STATIC HTML, no browser.
    # metals_web already scrapes this page reliably (9/9 metals), and the same page
    # carries Brent/WTI/Gold/Silver/Copper. Cheaper and cooler than a browser, and it
    # takes load OFF Yahoo entirely, so try it before spinning Chromium up.
    remaining = {s: r for s, r in gaps.items() if s not in filled}
    te_syms = set()
    if remaining:
        try:
            te = _te_commodities(remaining)
            te_syms = set(te)
            filled.update(te)
        except Exception:
            pass

    # TIER 2c: real browser, ROTATED ACROSS SITES. Runs before the cache because it
    # returns LIVE data where the cache can only return yesterday's level.
    remaining = {s: r for s, r in gaps.items() if s not in filled}
    pw_syms = set()
    if remaining:
        try:
            pw = _playwright_quotes(remaining)
            pw_syms = set(pw)
            filled.update(pw)
        except Exception:
            pass

    # patch EVERY row carrying a filled symbol (across all lists)
    n_patched = 0
    for k in lists:
        for q in snap.get(k, []) or []:
            sym = q.get("symbol")
            if sym in filled and _needs_fix(q):
                last, pct = filled[sym]
                # REGRESSION FIX: only fill a field that is ACTUALLY MISSING. _needs_fix
                # fires when EITHER last OR pct is absent, so a quote with a good last
                # (Brent 94) but no pct used to have its last OVERWRITTEN by a fallback
                # source (~84 = WTI / a mis-parsed column). A live primary value must
                # never be clobbered by a fallback — the fallback only fills gaps.
                # A GOOD existing last is one that is present AND not flagged suspect.
                # A suspect last is bad data and MAY be replaced by a plausible fallback.
                had_good_last = _num(q.get("last")) is not None and not q.get("suspect")
                changed = False
                # PLAUSIBILITY GUARD: a fallback can return the WRONG contract / a
                # mis-parsed column — WTI (~$85) for Brent (~$94). Accept a replacement
                # only if it is within 8% of yesterday's close (when we have one).
                prev = _num(q.get("previous_close"))
                plausible = (last is not None and
                             (prev is None or abs(last - prev) / prev <= 0.08))
                if not had_good_last and last is not None and plausible:
                    q["last"] = round(last, 2)
                    if pct is not None:
                        q["pct_change"] = pct
                    q["suspect"] = False          # replaced with a verified value
                    changed = True
                elif not had_good_last and last is not None and not plausible:
                    q["fallback_rejected"] = f"{q.get('source','fallback')} {last} vs prev {prev} (>8%)"
                if _num(q.get("pct_change")) is None and pct is not None:
                    q["pct_change"] = pct
                    changed = True
                if changed:
                    q["source"] = ("yahoo-html" if sym in yh_syms else
                                   "tradingeconomics" if sym in te_syms else
                                   "browser" if sym in pw_syms else
                                   "nse" if sym in _NSE_INDEX or sym.endswith(".NS") else "stooq")
                    q["fallback"] = True
                    n_patched += 1
    # ---- TIER 3: last-known-good cache — RECORD ONLY BY DEFAULT -----------
    # save() always runs, so the cache accumulates history and stays useful for
    # diagnosis ("we had Brent at 88.1 yesterday, so today's blank is a real outage").
    # apply() is OFF unless NEWSAGENT_QUOTE_CACHE_FILL=1, because a stale price that
    # LOOKS live is worse than an honest gap: the report would show a plausible number
    # feeding the oil band, the level amplifier and every oil-driven sector score, with
    # nothing signalling that it came from yesterday.
    cache_stats = {}
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _shared = _Path(__file__).resolve().parents[2]
        if str(_shared) not in _sys.path:
            _sys.path.insert(0, str(_shared))
        import quote_cache as _qc
        cache_stats = _qc.apply(*[snap.get(k, []) or [] for k in lists])
        _qc.save(*[snap.get(k, []) or [] for k in lists])
    except Exception:
        cache_stats = {}

    return {"enabled": True, "gaps": len(gaps), "filled": len(filled), "rows_patched": n_patched,
            "sources": sorted({("nse" if s in _NSE_INDEX or s.endswith('.NS') else 'stooq') for s in filled}),
            "cache": cache_stats, "commodity_xcheck": commodity_xcheck}
