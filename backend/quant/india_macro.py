"""
india_macro.py
--------------
Adds the indicators an India desk actually watches (PMI, earnings season) AND a
CONCLUSION synthesizer that turns the event panel from a LIST into a read:
"here's what all this means for positioning."

Three parts:
  1. PMI — manufacturing + services, leading activity indicators (lead IIP/GDP).
  2. Earnings-season regime — when the heavyweights report, the market goes
     bottom-up (stock-specific gap risk rises); the suggester should know.
  3. conclude() — reads events + proximity + regime + oil + flows into a short,
     plain-English positioning conclusion for the event panel footer.

Dates are CONFIG (refresh from S&P Global / company results calendars). The
conclusion is a SYNTHESIS of signals already computed elsewhere — it does not
invent new data, and it never says "buy/sell"; it frames the environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


# ── 1. PMI events (add to event_calendar.upcoming_events) ───────────────────
def pmi_events(today: date | None = None) -> list:
    """Manufacturing PMI ~1st business day, Services PMI ~3rd, monthly.
    Returns Event-shaped tuples to merge into the calendar."""
    return [
        {"code": "IN_PMI_MFG", "name": "India Manufacturing PMI",
         "date": date(2026, 7, 1), "impact": "medium",
         "sector_focus": "Metals, Auto, Capital Goods, Industrials",
         "lead_note": "Leading activity gauge — leads IIP/GDP; >50 expansion."},
        {"code": "IN_PMI_SVC", "name": "India Services PMI",
         "date": date(2026, 7, 3), "impact": "medium",
         "sector_focus": "Financials, IT, Consumer",
         "lead_note": "Services dominate India GDP — leads the growth read."},
    ]


PMI_KEYWORDS = {
    "IN_PMI_MFG": ("manufacturing pmi", "factory activity", "s&p global manufacturing"),
    "IN_PMI_SVC": ("services pmi", "services activity", "composite pmi"),
}


# ── 2. earnings-season regime ────────────────────────────────────────────────
@dataclass
class EarningsWindow:
    name: str
    start: date
    end: date
    heavyweights: list   # which index-movers report in this window


def earnings_season(today: date | None = None) -> list[EarningsWindow]:
    """Q1 FY27 results window (CONFIG — refresh from company calendars).
    Heavyweights listed because THEY move the index when they report."""
    return [
        EarningsWindow("Q1 FY27 results", date(2026, 7, 10), date(2026, 8, 14),
                       ["TCS", "HDFCBANK", "RELIANCE", "INFY", "ICICIBANK"]),
    ]


def earnings_regime(today: date) -> dict:
    """Are we in earnings season? If so, the driver regime is BOTTOM-UP:
    stock-specific gap risk rises, index-level macro signals matter less."""
    for w in earnings_season(today):
        if w.start <= today <= w.end:
            days_in = (today - w.start).days
            return {"active": True, "window": w.name,
                    "heavyweights_reporting": w.heavyweights,
                    "note": (f"Earnings season ({w.name}) — market is BOTTOM-UP; "
                             "single-stock gap risk elevated as heavyweights report. "
                             "Favour defined-risk and smaller index-level bets; "
                             "watch management commentary on demand/margins/orders."),
                    "days_into": days_in}
    return {"active": False, "note": "Not in earnings season — macro drivers lead."}


# ── 3. the CONCLUSION synthesizer (event panel footer) ──────────────────────
def conclude(*, proximity: dict, today: date,
             oil_falling: bool | None = None,
             flow_regime: str | None = None,
             complacency: float | None = None,
             bias: float | None = None,
             cues_state: dict | None = None,
             flows_state: dict | None = None) -> dict:
    """Read everything into a short positioning conclusion. Pure synthesis of
    signals computed elsewhere — no new data, no buy/sell call."""
    lines: list[str] = []
    posture = "balanced"

    # Flows cross-check hook for the daily report
    if cues_state:
        cues_dict = cues_state.get("cues", {}) if isinstance(cues_state, dict) else {}
        chg_10y = cues_dict.get("India 10Y")
        z10 = (chg_10y / 3.0) if (chg_10y is not None) else 0.0
        
        if abs(z10) > 1.5:
            from backend.quant.flows_fetcher import fetch_nse_cash_sync
            cash_days, _ = fetch_nse_cash_sync()
            if cash_days:
                latest_day = cash_days[-1]
                eq_flow = getattr(latest_day, "fii_cash", 0.0)
                dt_flow = getattr(latest_day, "fii_debt", 0.0)
                
                if not eq_flow:
                    lines.append("FII flows cross-check: equity leg is missing.")
                elif not dt_flow:
                    lines.append("FII flows cross-check: debt leg is missing.")
                elif dt_flow < 0 and eq_flow > 0:
                    lines.append("FII flows cross-check: debt→equity rotation consistent.")
                elif dt_flow < 0 and eq_flow < 0:
                    lines.append("FII flows cross-check: global risk reduction, not rotation.")
                else:
                    lines.append("FII flows cross-check: mixed flows, no clear rotation.")

    # event proximity drives the headline
    near = proximity.get("nearest_high_impact")
    days = proximity.get("days_away")
    act = proximity.get("action")
    if act == "block_premium_sell":
        lines.append(f"High-impact event ({proximity.get('name')}) in {days}d — "
                     "gap risk; avoid new premium-selling, protect held positions.")
        posture = "defensive"
    elif act == "caution_downsize":
        lines.append(f"{proximity.get('name')} in {days}d — downsize, widen wings; "
                     "event-gap risk not priced by a calm chain.")
        posture = "cautious"
    else:
        lines.append("No imminent high-impact event — event-gap risk low near-term.")

    # earnings season overlay
    er = earnings_regime(today)
    if er["active"]:
        lines.append(er["note"])
        if posture == "balanced":
            posture = "cautious"

    # oil (the India macro positive Nair flagged)
    if oil_falling is True:
        lines.append("Soft crude is an India tailwind — eases inflation, fiscal, "
                     "and current-account, giving RBI dovish room (supports "
                     "rate-sensitives: Banks, Auto).")
    elif oil_falling is False:
        lines.append("Firm crude is an India headwind — pressures inflation/CAD "
                     "and narrows RBI room.")

    # flows
    if flow_regime == "broad_outflow":
        lines.append("Institutions net sellers — no bid; size down.")
        posture = "defensive"
    elif flow_regime == "fii_exit_dii_absorb":
        lines.append("FIIs exiting but DIIs/SIP absorbing — downside cushioned, "
                     "rallies capped.")

    # complacency
    if complacency is not None and complacency >= 70:
        lines.append("Complacency high — premium thin and shock-prone; long-vol or "
                     "stand aside over selling premium.")

    # bias (user, optional)
    if bias is not None and abs(bias) >= 0.3:
        lines.append(f"Net lean {'bullish' if bias>0 else 'bearish'} "
                     f"({bias:+.2f}) — express directionally with defined risk.")

    headline = {
        "defensive": "DEFENSIVE — protect capital; event/flow risk dominates.",
        "cautious": "CAUTIOUS — selective, defined-risk, smaller size.",
        "balanced": "BALANCED — no dominant risk; normal defined-risk posture.",
    }[posture]

    return {"posture": posture, "headline": headline, "points": lines,
            "earnings_season": er["active"],
            "disclaimer": "Synthesis of current signals for context — not a trade "
                          "recommendation; gates + risk_budget decide whether/how big."}


if __name__ == "__main__":
    import json
    # current-ish setup: NFP in 2d, earnings season just starting, oil soft,
    # FII exit/DII absorb, complacency ~68, slight bullish bias.
    prox = {"nearest_high_impact": "US_NFP",
            "name": "US Non-Farm Payrolls + Unemployment Rate",
            "days_away": 2, "action": "caution_downsize"}
    out = conclude(proximity=prox, today=date(2026, 7, 11),
                   oil_falling=True, flow_regime="fii_exit_dii_absorb",
                   complacency=68, bias=0.2)
    print(out["headline"], "\n")
    for p in out["points"]:
        print(" •", p)
    print("\nearnings_season:", out["earnings_season"])
