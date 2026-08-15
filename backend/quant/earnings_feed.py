"""
earnings_feed.py
----------------
Real-time earnings-season headlines for the Macro Shock tab, via free RSS
(feedparser — same approach as rss_news.py). Filters the markets feeds for
results/earnings items and tags each headline to a NIFTY-50 constituent, so on a
no-macro-trigger day you can see WHICH company reported and read the move as
earnings-driven rather than macro.

Returns dicts: {title, source, publishedAt (ISO UTC), link, symbol, sentiment}.

Note: RSS carries CURRENT headlines. For a past replay date the live feed won't
hold that day's items, so we return recent earnings headlines and mark whether
they fall on the requested date.
"""
from __future__ import annotations
import calendar
from datetime import datetime, timezone

import feedparser

# Earnings/results-leaning feeds + general markets feeds (results show up there too).
EARNINGS_FEEDS = {
    "MC Results":     "https://www.moneycontrol.com/rss/results.xml",
    "MC Markets":     "https://www.moneycontrol.com/rss/marketreports.xml",
    "ET Earnings":    "https://economictimes.indiatimes.com/markets/stocks/earnings/rssfeeds/2146845.cms",
    "ET Markets":     "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Business Std":   "https://www.business-standard.com/rss/markets-106.rss",
}

_UA = "Mozilla/5.0 (compatible; EarningsBot/1.0)"

# results / earnings keywords a headline must contain to count
_EARN_KW = ("q1 ", "q2 ", "q3 ", "q4 ", "q1fy", "q2fy", "results", "result ",
            "earnings", "net profit", "profit ", "revenue", "pat ", "ebitda",
            "beats", "misses", "topline", "bottomline", "quarter", "yoy",
            "dividend", "margin")

_POS = ("beats", "surge", "jump", "rise", "rises", "gain", "record", "strong",
        "up ", "soars", "profit rises", "growth", "outperform", "tops")
_NEG = ("misses", "falls", "drop", "slump", "decline", "weak", "down ",
        "disappoint", "cuts", "loss", "plunge", "warns")

# NIFTY-50 ticker -> lowercase aliases that appear in headlines
ALIASES = {
    "RELIANCE": ["reliance"], "TCS": ["tcs", "tata consultancy"], "HDFCBANK": ["hdfc bank"],
    "ICICIBANK": ["icici bank"], "INFY": ["infosys"], "SBIN": ["sbi", "state bank"],
    "BHARTIARTL": ["bharti airtel", "airtel"], "ITC": ["itc"], "LT": ["l&t", "larsen"],
    "AXISBANK": ["axis bank"], "KOTAKBANK": ["kotak"], "HINDUNILVR": ["hindustan unilever", "hul"],
    "BAJFINANCE": ["bajaj finance"], "M&M": ["mahindra & mahindra", "m&m"], "MARUTI": ["maruti"],
    "SUNPHARMA": ["sun pharma"], "NTPC": ["ntpc"], "TATAMOTORS": ["tata motors"],
    "HCLTECH": ["hcl tech", "hcltech"], "POWERGRID": ["power grid", "powergrid"], "TITAN": ["titan"],
    "ULTRACEMCO": ["ultratech"], "ASIANPAINT": ["asian paints"], "BAJAJFINSV": ["bajaj finserv"],
    "ADANIENT": ["adani enterprises"], "ONGC": ["ongc", "oil and natural gas"], "WIPRO": ["wipro"],
    "COALINDIA": ["coal india"], "NESTLEIND": ["nestle"], "JSWSTEEL": ["jsw steel"],
    "TATASTEEL": ["tata steel"], "GRASIM": ["grasim"], "ADANIPORTS": ["adani ports"],
    "TECHM": ["tech mahindra"], "HINDALCO": ["hindalco"], "INDUSINDBK": ["indusind"],
    "DRREDDY": ["dr reddy", "dr. reddy"], "CIPLA": ["cipla"], "BAJAJ-AUTO": ["bajaj auto"],
    "EICHERMOT": ["eicher"], "HEROMOTOCO": ["hero motocorp", "hero moto"], "BRITANNIA": ["britannia"],
    "DIVISLAB": ["divi's", "divis"], "SBILIFE": ["sbi life"], "TATACONSUM": ["tata consumer"],
    "BPCL": ["bpcl", "bharat petroleum"], "APOLLOHOSP": ["apollo hospital"], "SHREECEM": ["shree cement"],
    "UPL": ["upl"],
}


def _iso(entry):
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return None
    return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc).isoformat()


def _tag_symbol(title_l: str):
    for sym, al in ALIASES.items():
        for a in al:
            if a in title_l:
                return sym
    return None


def _sentiment(title_l: str):
    p = sum(1 for k in _POS if k in title_l)
    n = sum(1 for k in _NEG if k in title_l)
    return "positive" if p > n else "negative" if n > p else "neutral"


def fetch_earnings_headlines(date: str | None = None, limit: int = 40) -> dict:
    """Fetch + filter earnings headlines, tag to constituents. `date` (YYYY-MM-DD)
    marks which items fall on that day (RSS is current, so past dates may be empty)."""
    seen, out = set(), []
    n_feeds_ok = 0
    for name, url in EARNINGS_FEEDS.items():
        try:
            parsed = feedparser.parse(url, agent=_UA)
        except Exception:
            continue
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            continue
        n_feeds_ok += 1
        for e in parsed.entries[:30]:
            title = (getattr(e, "title", "") or "").strip()
            tl = title.lower()
            if not title or not any(k in tl for k in _EARN_KW):
                continue
            key = tl[:60]
            if key in seen:
                continue
            seen.add(key)
            iso = _iso(e)
            out.append({
                "title": title, "source": name, "publishedAt": iso,
                "link": getattr(e, "link", ""), "symbol": _tag_symbol(tl),
                "sentiment": _sentiment(tl),
                "on_date": bool(iso and date and iso[:10] == date),
            })
    # tagged constituents first, then on-date, then newest
    out.sort(key=lambda a: (a["symbol"] is None, not a["on_date"],
                            a["publishedAt"] or ""), reverse=False)
    tagged = [a for a in out if a["symbol"]]
    return {"success": True, "date": date, "feeds_ok": n_feeds_ok,
            "count": len(out), "tagged_count": len(tagged),
            "headlines": out[:limit]}
