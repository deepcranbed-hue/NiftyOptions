"""
filings.py
----------
Exchange corporate-filings fetcher — the missing input the RSS pipeline lacks.
Emits article dicts in the SAME shape as rss_news.fetch_rss():

    {"title", "publishedAt"(ISO UTC), "description", "source"}

plus two passthrough keys the tagger preserves (they ride through {**a, **tag}):

    "filing_category" : BSE category, e.g. "Result" | "Board Meeting" | "Acquisition"
    "symbol"          : resolved NIFTY constituent symbol (or None if off-universe)
    "attachment"      : PDF url (BSE AttachLive / NSE nsearchives) for optional deep-read

So the integration is one line in get_tagged_news():
    raw = fetch_rss() + fetch_filings()
and every downstream stage (window, relevance, injection scan, Qwen tagging,
cache, market_view) runs unchanged.

ACCESS NOTE
-----------
BSE publishes a machine-readable announcements JSON (AnnGetData). It covers most
NIFTY names (dual-listed) and is the practical route. NSE's www.nseindia.com/api/*
JSON needs live browser cookies and its ToS forbids automated use — DO NOT use it.
NSE filing PDFs are static downloadable files under nsearchives.nseindia.com and
are referenced from the announcement item when present.

The network call is isolated in _http_get_json so the parser (_bse_to_articles)
is unit-testable offline against a saved payload.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

try:                                    # requests is in the backend env
    import requests
except Exception:                       # keep import-safe in bare sandboxes
    requests = None

# ── BSE endpoints ───────────────────────────────────────────────────────────
BSE_ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
BSE_ATTACH_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
# BSE's JSON 403s without a browser-ish UA + a bseindia Referer:
_BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NiftyFilingsBot/1.0)",
    "Referer": "https://www.bseindia.com/corporates/ann.html",
    "Accept": "application/json, text/plain, */*",
}

# Category -> coarse filing event tag (a strong prior for the LLM tagger).
_CATEGORY_EVENT = {
    "result": "RESULTS",
    "board meeting": "BOARD_MEETING",
    "acquisition": "MNA",
    "amalgamation": "MNA",
    "scheme of arrangement": "MNA",
    "credit rating": "RATING",
    "dividend": "DIVIDEND",
    "agm/egm": "AGM",
    "buy back": "BUYBACK",
    "buyback": "BUYBACK",
}

# BSE scrip-code -> NSE symbol map for the full NIFTY-50 universe
# (backend/quant/../nifty-50-stock-list.csv). A BSE filing is tagged with a numeric
# SCRIP_CD, not a ticker, so this table is what resolves a filing to its stock.
# Codes are stable BSE identifiers; verify against SLONGNAME on the first live
# fetch (a mismatch is obvious), and override at runtime via load_scrip_map().
SEED_SCRIP_MAP = {
    500325: "RELIANCE",   532540: "TCS",         500180: "HDFCBANK",  500209: "INFY",
    532174: "ICICIBANK",  500696: "HINDUNILVR",  500875: "ITC",       500112: "SBIN",
    532454: "BHARTIARTL", 500247: "KOTAKBANK",   500510: "LT",        532215: "AXISBANK",
    507685: "WIPRO",      500820: "ASIANPAINT",  532281: "HCLTECH",   532500: "MARUTI",
    500034: "BAJFINANCE", 500114: "TITAN",       524715: "SUNPHARMA", 532755: "TECHM",
    500790: "NESTLEIND",  532898: "POWERGRID",   532538: "ULTRACEMCO",512599: "ADANIENT",
    500570: "TATAMOTORS", 500312: "ONGC",        500470: "TATASTEEL", 500228: "JSWSTEEL",
    532555: "NTPC",       532187: "INDUSINDBK",  500520: "M&M",       533278: "COALINDIA",
    532978: "BAJAJFINSV", 500440: "HINDALCO",    500124: "DRREDDY",   500300: "GRASIM",
    532488: "DIVISLAB",   532977: "BAJAJ-AUTO",  500825: "BRITANNIA", 500182: "HEROMOTOCO",
    532921: "ADANIPORTS", 500087: "CIPLA",       512070: "UPL",       540719: "SBILIFE",
    505200: "EICHERMOT",  500547: "BPCL",        500800: "TATACONSUM",508869: "APOLLOHOSP",
    500387: "SHREECEM",   500010: "HDFC",
}


def _to_iso_utc(dt_str: str | None) -> str | None:
    """BSE timestamps look like '2026-07-10T18:30:00' (IST, naive). Store UTC."""
    if not dt_str:
        return None
    s = dt_str.strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d %b %Y %H:%M:%S"):
        try:
            naive = datetime.strptime(s[:26], fmt)
            # BSE stamps are IST; convert to UTC (-5:30) so it matches rss_news.
            ist = naive.replace(tzinfo=timezone.utc)  # placeholder tz
            # subtract 5h30m to go IST->UTC
            epoch = calendar.timegm(ist.timetuple()) - (5 * 3600 + 30 * 60)
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _event_for(category: str | None) -> str | None:
    if not category:
        return None
    c = category.strip().lower()
    for key, ev in _CATEGORY_EVENT.items():
        if key in c:
            return ev
    return None


def _bse_to_articles(payload: dict, scrip_map: dict | None = None,
                     per_source: int = 40) -> list[dict]:
    """Pure parser: BSE AnnGetData JSON -> fetch_rss-shaped article dicts.
    No network — unit-testable against a saved payload."""
    scrip_map = scrip_map or SEED_SCRIP_MAP
    rows = payload.get("Table") or []
    out, seen = [], set()
    for r in rows[:per_source]:
        headline = (r.get("HEADLINE") or r.get("NEWSSUB") or "").strip()
        if not headline:
            continue
        iso = _to_iso_utc(r.get("NEWS_DT") or r.get("DT_TM") or r.get("DissemDT"))
        if not iso:
            continue
        key = headline.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        try:
            scrip = int(r.get("SCRIP_CD")) if r.get("SCRIP_CD") not in (None, "") else None
        except (TypeError, ValueError):
            scrip = None
        symbol = scrip_map.get(scrip)
        company = (r.get("SLONGNAME") or "").strip()
        category = (r.get("CATEGORYNAME") or "").strip()
        subcat = (r.get("SUBCATNAME") or "").strip()
        attach = r.get("ATTACHMENTNAME") or ""
        desc_bits = [company, category, subcat, (r.get("NEWSSUB") or "").strip()]
        description = " | ".join([b for b in desc_bits if b])[:500]
        out.append({
            "title": headline,
            "publishedAt": iso,
            "description": description,
            "source": "BSE Filings",
            # passthrough (preserved by the tagger's {**a, **tag}) --------------
            "filing_category": category or None,
            "filing_event": _event_for(category),
            "symbol": symbol,
            "attachment": (BSE_ATTACH_BASE + attach) if attach else None,
        })
    return out


def _http_get_json(url: str, params: dict, headers: dict, timeout: int = 15) -> dict:
    if requests is None:
        raise RuntimeError("requests unavailable in this environment")
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_bse(prev_date: str | None = None, to_date: str | None = None,
              category: str = "-1", scrip: str = "", pageno: int = 1,
              scrip_map: dict | None = None, per_source: int = 40) -> list[dict]:
    """Live BSE announcements for a date range (YYYYMMDD). Network failure
    returns [] rather than raising, mirroring fetch_rss's be-a-good-citizen policy."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prev_date = prev_date or today
    to_date = to_date or today
    params = {"strCat": category, "strPrevDate": prev_date, "strScrip": scrip,
              "strSearch": "P", "strToDate": to_date, "strType": "C", "pageno": pageno}
    try:
        payload = _http_get_json(BSE_ANN_URL, params, _BSE_HEADERS)
    except Exception as e:
        print(f"  [filings] BSE fetch failed: {e}")
        return []
    arts = _bse_to_articles(payload, scrip_map, per_source)
    print(f"  [filings] {len(arts)} BSE announcements ({prev_date}->{to_date})")
    return arts


def fetch_filings(scrip_map: dict | None = None, per_source: int = 40) -> list[dict]:
    """Top-level entry — merge all permitted filing sources into fetch_rss-shaped
    dicts. Currently BSE (covers NSE dual-listed names). NSE-archive-only PDFs are
    reached via the `attachment` url on the relevant BSE item."""
    return fetch_bse(scrip_map=scrip_map, per_source=per_source)


def load_scrip_map(csv_path: str) -> dict:
    """Optional: build a fuller BSE-code->symbol map from nifty-50-stock-list.csv
    if it carries a BSE code column; otherwise returns the seed map. Kept lenient."""
    import csv, os
    m = dict(SEED_SCRIP_MAP)
    if not os.path.exists(csv_path):
        return m
    try:
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                code = row.get("BSE Code") or row.get("bse_code") or row.get("ISIN Code")
                sym = row.get("Symbol") or row.get("symbol")
                if code and sym and str(code).isdigit():
                    m[int(code)] = sym.strip()
    except Exception as e:
        print(f"  [filings] scrip map load skipped: {e}")
    return m


if __name__ == "__main__":
    # Offline parse test — the sandbox can't reach BSE, so prove the shape against
    # a representative AnnGetData payload (same approach as rss_news.__main__).
    SAMPLE = {"Table": [
        {"SCRIP_CD": 532540, "SLONGNAME": "Tata Consultancy Services Ltd",
         "HEADLINE": "Financial Results for the quarter ended June 30, 2026",
         "CATEGORYNAME": "Result", "SUBCATNAME": "Financial Results",
         "NEWS_DT": "2026-07-10T18:30:00", "ATTACHMENTNAME": "abc123.pdf",
         "NEWSSUB": "TCS reports Q1FY27 results"},
        {"SCRIP_CD": 500325, "SLONGNAME": "Reliance Industries Ltd",
         "HEADLINE": "Board Meeting Intimation for considering fund raising",
         "CATEGORYNAME": "Board Meeting", "SUBCATNAME": "",
         "NEWS_DT": "2026-07-10T16:05:00", "ATTACHMENTNAME": "rel987.pdf",
         "NEWSSUB": "Intimation of board meeting"},
    ], "Table1": [{"ROWCNT": 2}]}
    arts = _bse_to_articles(SAMPLE)
    for a in arts:
        print(f"{a['publishedAt']} | {a['symbol']:9} | {a['filing_event'] or '-':13} | {a['title'][:50]}")
    print("\nshape:", list(arts[0].keys()))
