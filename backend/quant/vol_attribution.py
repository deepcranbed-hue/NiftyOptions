"""
vol_attribution.py
------------------
Two jobs:
  1. Get India VIX from RSS NEWS (interim, until a live quote feed exists) — with
     an explicit STALENESS flag, because news-sourced VIX lags the live index.
  2. ATTRIBUTE a vol reading: WHY is implied vol where it is? Decompose into
     expiry-mechanics / event-premium / genuine-fear / realized-catchup, so you
     never sell "rich" IV without knowing which KIND it is.

Why this matters for "we trade on options vol": selling high IV is only smart if
it's high for a HARVESTABLE reason (demand / risk premium). High IV that's really
expiry mechanics (0-1 DTE) or event premium (CPI tomorrow) is a TRAP — you'd be
short gamma into the worst day, or short the event. Attribution separates edge
from hand-grenade.

India VIX = the 30-DAY, expiry-CLEAN fear gauge. The chain's ATM IV on a 1-day
expiry is mechanically inflated. Comparing the two is how you detect distortion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone


# ── 1. VIX from RSS news (interim source) ────────────────────────────────────
# Headlines like "India VIX at 13.05, down 2.54%" / "India VIX rose 8.55% to 13.94"
_VIX_PATTERNS = [
    r"india vix[^0-9]{0,20}(\d{1,2}\.\d{1,2})",
    r"vix[^0-9]{0,12}(?:at|near|to|trading at)[^0-9]{0,6}(\d{1,2}\.\d{1,2})",
    r"volatility (?:gauge|index)[^0-9]{0,20}(\d{1,2}\.\d{1,2})",
]


@dataclass
class VixReading:
    value: float | None
    as_of: str | None         # published_at of the source article
    source: str | None
    age_hours: float | None
    stale: bool
    note: str


def vix_from_news(tagged_articles: list[dict], now: datetime | None = None) -> VixReading:
    """Extract the most RECENT India VIX value mentioned in the news.
    tagged_articles: [{title, description?, published_at(ISO), source}]"""
    now = now or datetime.now(timezone.utc)
    best = None  # (published_dt, value, source)
    for a in tagged_articles:
        text = f"{a.get('title','')} {a.get('description','') or ''}".lower()
        for pat in _VIX_PATTERNS:
            m = re.search(pat, text)
            if m:
                try:
                    v = float(m.group(1))
                except ValueError:
                    continue
                if not (5 <= v <= 100):     # sanity: VIX realistic range
                    continue
                try:
                    pub = datetime.fromisoformat(a["published_at"])
                except Exception:
                    pub = now
                if best is None or pub > best[0]:
                    best = (pub, v, a.get("source"))
                break
    if best is None:
        return VixReading(None, None, None, None, True,
                          "No India VIX value found in current news — VIX unavailable.")
    pub, v, src = best
    age = (now - pub).total_seconds() / 3600
    # news-sourced VIX is interim; flag stale if older than a few hours
    stale = age > 6
    note = (f"India VIX {v} from news ({src}), ~{age:.0f}h old. "
            + ("STALE — news-sourced VIX lags the live index; treat as approximate "
               "and replace with a live quote feed when available."
               if stale else
               "Recent, but still news-sourced (interim) — a live quote feed is "
               "more reliable."))
    return VixReading(v, pub.isoformat(), src, round(age, 1), stale, note)


# ── 2. vol attribution — WHY is vol here? ────────────────────────────────────
def attribute_vol(chain_atm_iv: float, *, days_to_expiry: float,
                  india_vix: float | None = None,
                  event_within_days: int | None = None,
                  realized_vol: float | None = None) -> dict:
    """
    Decompose an implied-vol reading into its likely cause(s).
    chain_atm_iv : ATM IV from the chain (decimal, e.g. 0.17)
    india_vix    : the 30-day VIX (percent, e.g. 13.0) — the expiry-clean reference
    All causes are flagged with whether they make the high IV HARVESTABLE or a TRAP.
    """
    causes = []
    iv_pct = chain_atm_iv * 100

    # a) EXPIRY MECHANICS: 0-1 DTE + chain IV >> VIX => the clock, not fear
    if days_to_expiry <= 1.5 and india_vix is not None and iv_pct > india_vix * 1.25:
        gap = iv_pct - india_vix
        causes.append({
            "cause": "expiry_mechanics",
            "detail": f"Chain ATM IV {iv_pct:.0f}% >> India VIX {india_vix:.0f}% "
                      f"with {days_to_expiry:.0f}d to expiry — ~{gap:.0f}pt gap is "
                      f"the expiry clock (gamma/√T), NOT fear.",
            "harvestable": False,
            "warning": "Selling this IV = short gamma into the highest-gamma day. "
                       "Rich-looking but a TRAP; pin/gap risk dominates."})

    # b) EVENT PREMIUM: known catalyst ahead => IV bid, will crush after
    if event_within_days is not None and event_within_days <= 3:
        causes.append({
            "cause": "event_premium",
            "detail": f"High-impact event within {event_within_days}d — IV is bid "
                      f"for the known catalyst; expect IV CRUSH after it resolves.",
            "harvestable": "conditionally",
            "warning": "Selling pre-event harvests the crush IF you survive the gap. "
                       "You are SHORT THE EVENT — defined risk + small size only."})

    # c) GENUINE FEAR: the 30-day VIX itself is elevated => real, news-attributable
    if india_vix is not None and india_vix >= 18:
        causes.append({
            "cause": "genuine_fear",
            "detail": f"India VIX itself elevated ({india_vix:.0f}) — broad 30-day "
                      f"fear, not just chain mechanics. Attributable to news/macro.",
            "harvestable": True,
            "warning": "Premium genuinely rich (real risk premium) — but realised "
                       "moves are larger. Source the driver via the news layer."})

    # d) REALIZED CATCH-UP: index actually moving more => IV rising to match
    if realized_vol is not None and realized_vol * 100 >= india_vix_or(iv_pct, india_vix):
        causes.append({
            "cause": "realized_catchup",
            "detail": f"Realized vol ({realized_vol*100:.0f}%) is running at/above "
                      f"implied — IV rising to catch actual movement.",
            "harvestable": False,
            "warning": "Not a premium edge — the market is genuinely moving that much."})

    if not causes:
        causes.append({
            "cause": "ordinary",
            "detail": "No distortion flag — chain IV roughly consistent with the "
                      "30-day VIX and no imminent expiry/event.",
            "harvestable": True,
            "warning": ""})

    # headline read
    primary = causes[0]["cause"]
    harvestable_overall = all(c.get("harvestable") is True for c in causes)
    return {
        "chain_atm_iv_pct": round(iv_pct, 1),
        "india_vix": india_vix,
        "iv_vs_vix_gap": round(iv_pct - india_vix, 1) if india_vix else None,
        "days_to_expiry": days_to_expiry,
        "primary_cause": primary,
        "causes": causes,
        "sell_premium_verdict": (
            "OK — IV elevation looks like genuine risk premium" if harvestable_overall
            else "CAUTION — IV is elevated for mechanical/event reasons, not pure "
                 "premium; selling it is not the clean edge it looks like."),
    }


def india_vix_or(iv_pct, vix):
    return vix if vix is not None else iv_pct


if __name__ == "__main__":
    import json
    # today's case: chain IV ~17%, 1-day expiry, VIX ~13 (from news), monthly expiry
    arts = [{"title": "India VIX at 13.05, down 2.54% as oil eases and RBI reassures",
             "published_at": "2026-06-27T04:30:00+00:00", "source": "HDFC Sky"}]
    vr = vix_from_news(arts, now=datetime(2026, 6, 28, 4, 0, tzinfo=timezone.utc))
    print("VIX from news:", vr.value, "|", vr.note, "\n")

    att = attribute_vol(0.17, days_to_expiry=1, india_vix=vr.value or 13.0,
                        event_within_days=1)   # monthly expiry tomorrow
    print(json.dumps(att, indent=2))
