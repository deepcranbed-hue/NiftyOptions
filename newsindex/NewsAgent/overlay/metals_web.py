"""
metals_web.py — pull the live industrial-metals complex from a static-HTML source.

MCX's own index is JS-gated (no free feed), but the metals that actually drive Indian steel —
copper, aluminium, zinc, nickel, lead AND the steel complex (China rebar steel, HRC, iron ore,
coking coal) — are all available as STATIC HTML on tradingeconomics.com/commodities with a daily
% change. This module fetches that page and parses each metal's day-move.

Design / safety:
  * Keyless HTTP GET with a browser UA (same pattern as news_fetch's fallback); guarded, times out.
  * Tolerant parser: for each anchor it finds the FIRST signed decimal-% after the anchor text —
    works whether the page is served as a table or flattened to text.
  * NEVER fabricates: a metal with no parseable value returns None → the caller marks it n/a.
  * Set NEWSAGENT_METALS_WEB=0 to disable the network call entirely (offline/testing).
"""
from __future__ import annotations

import os
import re
import urllib.request

DEFAULT_URL = "https://tradingeconomics.com/commodities"
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")}


def _fetch_text(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "ignore")
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"[ \t]+", " ", text)


def parse_moves(text: str, anchors: list[str]) -> dict:
    """anchor (e.g. 'Copper USD/Lbs') → first signed % after it (the day change), or None."""
    out = {}
    for a in anchors:
        # tolerant: allow non-digit junk (link markup, extra spaces) between the anchor's words,
        # then grab the FIRST signed decimal-% after it (the day change).
        pat = re.escape(a).replace(r"\ ", r"[^\d%]{0,25}?")
        m = re.search(pat + r"[^%]{0,80}?(-?\d+(?:\.\d+)?)\s*%", text, re.I | re.S)
        out[a] = float(m.group(1)) if m else None
    return out


def fetch_commodity_moves(anchors: list[str], url: str | None = None) -> dict:
    """Return {anchor: day_pct or None}. Empty dict if disabled or the fetch fails."""
    if os.environ.get("NEWSAGENT_METALS_WEB", "1") == "0" or not anchors:
        return {}
    try:
        return parse_moves(_fetch_text(url or DEFAULT_URL), anchors)
    except Exception:
        return {}


def parse_quotes(text: str, anchors: list[str]) -> dict:
    """
    anchor → (price, day_pct). Same tolerant approach as parse_moves(), but also grabs
    the LEVEL, because the oil band table / level amplifier need the absolute price and
    a % alone cannot drive them.

    TE's commodities row reads: Name | Price | Day | % | Weekly | Monthly | YoY | Date —
    so after the anchor the FIRST bare number is the price and the first signed % is the
    day change. Returns (None, None) for anything not parseable; never fabricates.
    """
    out = {}
    for a in anchors:
        pat = re.escape(a).replace(r"\ ", r"[^\d%]{0,25}?")
        m = re.search(pat + r"[^%]{0,120}?(-?\d[\d,]*(?:\.\d+)?)"      # price
                            r"[^%]{0,60}?(-?\d+(?:\.\d+)?)\s*%",        # day %
                      text, re.I | re.S)
        if m:
            try:
                out[a] = (float(m.group(1).replace(",", "")), float(m.group(2)))
                continue
            except ValueError:
                pass
        out[a] = (None, None)
    return out


def fetch_commodity_quotes(anchors: list[str], url: str | None = None) -> dict:
    """Return {anchor: (price, day_pct)}. Empty dict if disabled or the fetch fails."""
    if os.environ.get("NEWSAGENT_METALS_WEB", "1") == "0" or not anchors:
        return {}
    try:
        return parse_quotes(_fetch_text(url or DEFAULT_URL), anchors)
    except Exception:
        return {}
