"""
event_calendar.py
-----------------
Economic-calendar panel for the dashboard. Two jobs:

  1. SCHEDULE — hold the upcoming India + US releases that move NIFTY, and flag
     how close we are to each (drives a pre-event caution/block for premium-sell).
  2. CONSENSUS — attach the market's expectation for each event, extracted from
     your RSS news via Gemini (e.g. "CPI seen easing to 4.1%", "RBI expected on
     hold"). Reuses the same RSS + LLM path as the sentiment tab.

The output feeds:
  * a dashboard panel (next releases, days away, consensus, impact),
  * the risk gate / complacency override (scheduled high-impact event within N
    days => even a complacent chain is dangerous to sell premium into).

Dates: index events recur on a known cadence but exact days shift — keep this
list as CONFIG you refresh from the MoSPI / RBI / BLS calendars (or wire a
calendar API). The cadence comments make refreshing easy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import httpx
from cachetools import TTLCache, cached
from event_fetcher import refresh_dates
from backend.quant.us_macro import synthesize_macro


# ── impact tiers (how much the event moves NIFTY) ────────────────────────────
IMPACT = {"RBI_MPC": "high", "IN_CPI": "high", "US_CPI": "high",
          "US_FOMC": "high", "US_NFP": "high", "IN_GDP": "high",
          "IN_WPI": "medium", "IN_IIP": "medium", "US_PCE": "high",
          "IN_FISCAL": "medium", "IN_BUDGET": "high"}


@dataclass
class Event:
    code: str            # e.g. "IN_CPI"
    name: str            # "India CPI (retail inflation)"
    date: date           # scheduled release date
    impact: str          # high | medium | low
    sector_focus: str    # which NIFTY sectors it hits most
    consensus: str | None = None   # filled from RSS news
    consensus_source: str | None = None
    stale: bool = False            # True if we fell back to the static date
    lead_note: str | None = None   # Additional info, e.g. leads IIP/GDP


# ── scheduled releases — CONFIG, refresh from official calendars ─────────────
# Cadence reminders:
#   IN_CPI  ~12th monthly | IN_WPI ~14th | IN_IIP ~end-month | RBI_MPC ~bi-monthly
#   US_CPI  ~mid-month | US_FOMC 8x/yr | US_NFP first Friday | US_PCE end-month
@cached(cache=TTLCache(maxsize=1, ttl=43200)) # 12h cache
def _get_fetched_dates(today: date) -> dict:
    def _get(url: str) -> str:
        try:
            return httpx.get(url, timeout=10.0, headers={'User-Agent': 'Mozilla/5.0'}).text
        except Exception:
            return ""
    return refresh_dates(_get, today)

def upcoming_events(today: date | None = None) -> list[Event]:
    """Replace with live calendar data / API. Dates here are placeholders to
    refresh from MoSPI, RBI, and the US BLS/Fed calendars."""
    today_dt = today or datetime.now(timezone.utc).date()
    
    # Static fallbacks
    static_events = [
        Event("IN_CPI", "India CPI (retail inflation)", date(2026, 7, 13),
              "high", "Banks, Financials, Auto, FMCG"),
        Event("IN_WPI", "India WPI (wholesale inflation)", date(2026, 7, 14),
              "medium", "Metals, Energy"),
        Event("IN_IIP", "India IIP (industrial production)", date(2026, 7, 31),
              "medium", "Capital Goods, Auto, Metals"),
        Event("RBI_MPC", "RBI Monetary Policy decision", date(2026, 8, 6),
              "high", "Banks, Financials, Realty, Auto"),
        Event("US_CPI", "US CPI", date(2026, 7, 14), "high",
              "IT, broad risk (via FII flows / rupee)"),
        Event("US_FOMC", "US Fed (FOMC) decision", date(2026, 7, 29), "high",
              "IT, Banks, broad (via rupee / FII)"),
        Event("US_NFP", "US NFP (Non-Farm Payrolls)", date(2026, 8, 7), "high",
              "IT, broad (via Fed rate odds)"),
        Event("US_PCE", "US Core PCE", date(2026, 7, 31), "high",
              "IT, broad (via Fed rate odds)"),
        Event("IN_GDP", "India GDP (Quarterly)", date(2026, 8, 31), "high",
              "broad, Banks, Auto"),
        Event("IN_FISCAL", "India Fiscal Deficit", date(2026, 7, 31), "medium",
              "Banks, Financials (via yields/rupee)"),
        Event("IN_BUDGET", "Union Budget", date(2026, 7, 23), "high",
              "broad — Infra, Banks, Auto, consumption"),
    ]
    
    from backend.quant.india_macro import pmi_events
    for pm in pmi_events(today_dt):
        static_events.append(Event(**pm))
    
    fetched = _get_fetched_dates(today_dt)
    out = []
    for e in static_events:
        fd = fetched.get(e.code)
        if fd and fd.date and not fd.stale:
            e.date = fd.date
            e.stale = False
        else:
            e.stale = True
        out.append(e)
    return out


# ── proximity flag — drives the pre-event caution/override ───────────────────
def event_proximity(events: list[Event], today: date | None = None,
                    caution_days: int = 3, block_days: int = 1) -> dict:
    """Nearest high-impact event and what it implies for premium-selling."""
    today = today or datetime.now(timezone.utc).date()
    upcoming = sorted([e for e in events if e.date >= today], key=lambda e: e.date)
    high = [e for e in upcoming if e.impact == "high"]
    if not high:
        return {"nearest_high_impact": None, "days_away": None,
                "action": "normal", "note": "no high-impact event ahead."}
    nxt = high[0]
    days = (nxt.date - today).days
    if days <= block_days:
        action, note = "block_premium_sell", (
            f"{nxt.name} in {days}d — block new premium-selling; even a "
            f"complacent chain can gap through the range on the print.")
    elif days <= caution_days:
        action, note = "caution_downsize", (
            f"{nxt.name} in {days}d — downsize premium-selling and widen wings; "
            f"event-gap risk not priced by a calm chain.")
    else:
        action, note = "normal", f"{nxt.name} in {days}d — no event override yet."
    return {"nearest_high_impact": nxt.code, "name": nxt.name,
            "days_away": days, "consensus": nxt.consensus,
            "action": action, "note": note}


# ── consensus extraction from RSS news (reuse the Gemini path) ───────────────
def attach_consensus(events: list[Event], tagged_articles: list[dict]) -> list[Event]:
    """Match RSS/Gemini-tagged articles to events and attach the market
    expectation. `tagged_articles` are dicts that have at least {title, body,
    and optionally an 'event_consensus' field your Gemini prompt fills}.

    Lightweight keyword match here; for production, have the Gemini prompt return
    {event_code, consensus_text} per article and join on event_code."""
    kw = {
        "IN_CPI": ("cpi", "retail inflation", "consumer price"),
        "IN_WPI": ("wpi", "wholesale price", "wholesale inflation"),
        "IN_IIP": ("iip", "industrial production", "factory output"),
        "RBI_MPC": ("rbi", "mpc", "repo rate", "monetary policy"),
        "US_CPI": ("us cpi", "u.s. inflation", "us inflation"),
        "US_FOMC": ("fed", "fomc", "powell", "warsh", "rate hike"),
        "IN_GDP": ("gdp", "economic growth"),
        "US_NFP": ("nfp", "payrolls", "jobs report"),
        "IN_FISCAL": ("fiscal deficit", "fiscal", "cga"),
        "IN_BUDGET": ("budget", "union budget", "sitharaman"),
    }
    from backend.quant.india_macro import PMI_KEYWORDS
    kw.update(PMI_KEYWORDS)
    by_code = {e.code: e for e in events}
    for art in tagged_articles:
        text = f"{art.get('title','')} {art.get('description','') or art.get('body','')}".lower()
        # prefer an explicit Gemini field if present
        raw_ec = art.get("event_code")
        ecs = raw_ec if isinstance(raw_ec, list) else [raw_ec]
        
        matched_ec = False
        for ec in ecs:
            if ec and isinstance(ec, str) and ec in by_code and not by_code[ec].consensus:
                by_code[ec].consensus = art.get("event_consensus") or art.get("title")
                by_code[ec].consensus_source = art.get("source")
                matched_ec = True
        
        if matched_ec:
            continue
        for code, words in kw.items():
            if by_code.get(code) and by_code[code].consensus is None \
               and any(w in text for w in words):
                by_code[code].consensus = art.get("title")
                by_code[code].consensus_source = art.get("source")
                break
    return list(by_code.values())


def build_panel(tagged_articles: list[dict], today: date | None = None) -> dict:
    events = attach_consensus(upcoming_events(today), tagged_articles)
    prox = event_proximity(events, today)
    macro = synthesize_macro(tagged_articles)
    return {
        "events": [{"code": e.code, "name": e.name, "date": e.date.isoformat(),
                    "impact": e.impact, "sector_focus": e.sector_focus,
                    "consensus": e.consensus, "source": e.consensus_source, "stale": getattr(e, "stale", True)}
                   for e in sorted(events, key=lambda e: e.date)],
        "proximity": prox,
        "us_macro": macro
    }


if __name__ == "__main__":
    import json
    # pretend RSS gave us a couple of consensus headlines
    articles = [
        {"title": "India CPI seen easing to 4.1% in June on softer food prices",
         "description": "Economists expect retail inflation to cool...",
         "source": "ET Markets"},
        {"title": "RBI likely to hold repo at 5.25%, watch for stance shift",
         "description": "Most economists see status quo at August MPC...",
         "source": "Moneycontrol"},
        {"title": "US CPI expected at 0.3% m/m, keeping Fed hike odds alive",
         "description": "", "source": "Reuters"},
    ]
    panel = build_panel(articles, today=date(2026, 7, 11))
    print(json.dumps(panel, indent=2))
