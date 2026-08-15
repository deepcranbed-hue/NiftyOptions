"""
flows.py
--------
Tracks the money that actually moves NIFTY: FII (foreign) and DII (domestic
institutional) flows, plus the SIP book (the structural domestic bid).

Why this matters more than news sentiment: news is a PROXY for what institutions
might do; flows are what they DID. FIIs drive the heavyweight sectors (Banks,
IT, Energy) and the rupee; DII/SIP flows are the steady domestic counterweight
that has repeatedly absorbed FII selling.

This module is pure ANALYTICS (trend, regime, divergence) tested offline. The
data FETCH runs in your env — see DATA SOURCES below.

DATA SOURCES (fetch in your env; none reachable from the build sandbox):
  * FII/DII daily cash:  NSE (nseindia.com FII/DII activity) or
                         moneycontrol / Trendlyne FII-DII pages.
  * FII derivatives:     NSE F&O participant-wise OI (FII index fut/opt net).
  * SIP monthly:         AMFI (amfiindia.com) monthly SIP contribution + folio data.
  * FPI fortnightly:     NSDL/CDSL FPI flows (sector-wise, richer but lagged).
Cadence: cash flows DAILY (after close), SIP MONTHLY (~7th-10th, AMFI), FPI
fortnightly. Keep the parsers pure; flag stale on fetch failure.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from backend.quant.formulas import trace_flow


@dataclass
class FlowDay:
    date: str            # ISO
    fii_cash: float      # ₹ cr, net (buy +, sell -)
    dii_cash: float      # ₹ cr, net
    fii_fut_net: float = 0.0   # FII index-futures net (₹ cr or contracts) — optional
    fii_debt: float = 0.0      # FII debt flow net (₹ cr, outflow = negative) — optional


# ── core signals ─────────────────────────────────────────────────────────────
def flow_trend(days: list[FlowDay], window: int = 5) -> dict:
    """Recent FII/DII posture: cumulative, streak, and accumulation/distribution."""
    if not days:
        return {"status": "no_data"}
    recent = days[-window:]
    fii_cum = round(sum(d.fii_cash for d in recent), 0)
    dii_cum = round(sum(d.dii_cash for d in recent), 0)

    # streak: consecutive same-sign FII days from the end
    sign = lambda x: (x > 0) - (x < 0)
    s = sign(days[-1].fii_cash)
    streak = 0
    for d in reversed(days):
        if sign(d.fii_cash) == s and s != 0:
            streak += 1
        else:
            break

    # regime label
    if fii_cum > 0 and dii_cum > 0:
        regime = "broad_inflow"          # both buying — strong bid
    elif fii_cum < 0 and dii_cum > 0:
        regime = "fii_exit_dii_absorb"   # the classic India pattern
    elif fii_cum > 0 and dii_cum < 0:
        regime = "fii_in_dii_book"       # FII-led
    else:
        regime = "broad_outflow"         # both selling — caution

    return {
        "status": "ok",
        "window": window,
        "fii_cum_cr": fii_cum,
        "dii_cum_cr": dii_cum,
        "net_institutional_cr": round(fii_cum + dii_cum, 0),
        "fii_streak_days": streak * (1 if s > 0 else -1),  # +buy streak / -sell streak
        "regime": regime,
    }


def flow_zscore(days: list[FlowDay], lookback: int = 20) -> dict:
    """How extreme is today's FII flow vs its recent distribution?
    A big-|z| day is a stronger signal than the raw rupee number."""
    if len(days) < 5:
        return {"status": "insufficient"}
    hist = [d.fii_cash for d in days[-lookback:]]
    mu, sd = statistics.mean(hist), (statistics.pstdev(hist) or 1.0)
    today = days[-1].fii_cash
    z = (today - mu) / sd
    return {"status": "ok", "fii_today_cr": round(today, 0),
            "mean_cr": round(mu, 0), "z": round(z, 2),
            "extreme": abs(z) >= 1.5}


def fii_derivative_posture(days: list[FlowDay]) -> dict:
    """FII index-futures net is the leading hedge tell. Persistent net-short
    futures while cash is mixed = cautious/hedged (the 'buy cash, short futures'
    posture analysts flagged). Optional — needs the F&O participant data."""
    if not days or all(d.fii_fut_net == 0 for d in days[-5:]):
        return {"status": "no_data"}
    net = round(sum(d.fii_fut_net for d in days[-5:]), 0)
    posture = ("net_long" if net > 0 else "net_short" if net < 0 else "flat")
    return {"status": "ok", "fii_fut_5d_net": net, "posture": posture,
            "note": ("FII hedged/cautious — short futures vs cash"
                     if posture == "net_short" else
                     "FII directionally long via futures" if posture == "net_long"
                     else "neutral")}


# ── SIP: the structural domestic bid ─────────────────────────────────────────
@dataclass
class SIPMonth:
    month: str           # 'YYYY-MM'
    sip_inflow_cr: float # AMFI monthly SIP contribution, ₹ cr
    accounts_cr: float = 0.0   # SIP accounts (crore), optional


def sip_trend(months: list[SIPMonth]) -> dict:
    """SIP is sticky, slow money — a rising SIP run-rate is a structural floor
    under the market that blunts FII outflows. Track the run-rate and its slope."""
    if len(months) < 2:
        return {"status": "insufficient"}
    latest = months[-1]
    prev = months[-2]
    mom = (latest.sip_inflow_cr - prev.sip_inflow_cr) / (prev.sip_inflow_cr or 1) * 100
    # 3-month average run-rate
    run = statistics.mean([m.sip_inflow_cr for m in months[-3:]])
    slope = "rising" if mom > 1 else "falling" if mom < -1 else "flat"
    return {"status": "ok", "latest_month": latest.month,
            "sip_inflow_cr": round(latest.sip_inflow_cr, 0),
            "mom_pct": round(mom, 1), "run_rate_3m_cr": round(run, 0),
            "trend": slope,
            "note": ("structural domestic bid strengthening" if slope == "rising"
                     else "domestic bid softening" if slope == "falling"
                     else "domestic bid steady")}


# ── combined flow bias (feeds the dashboard + complements news bias) ─────────
def flow_bias(days: list[FlowDay], months: list[SIPMonth] | None = None) -> dict:
    t = flow_trend(days)
    z = flow_zscore(days)
    d = fii_derivative_posture(days)
    s = sip_trend(months) if months else {"status": "no_data"}

    # a simple -1..+1 flow tilt: FII cash dominates, DII/SIP cushions downside
    tilt = 0.0
    formula_trace = None
    if t.get("status") == "ok":
        fii = t["fii_cum_cr"]; dii = t["dii_cum_cr"]
        tilt = max(-1.0, min(1.0, (fii + 0.5 * dii) / 20000.0))  # ~₹20k cr saturates
        formula_trace = trace_flow(fii, dii, tilt)
    return {
        "flow_tilt": round(tilt, 2),
        "trend": t, "zscore": z, "fii_derivatives": d, "sip": s,
        "reading": _read(t, s),
        "formula_trace": formula_trace
    }


def _read(t, s):
    if t.get("status") != "ok":
        return "no flow data."
    r = t["regime"]
    base = {
        "broad_inflow": "Both FII and DII buying — strong bid, supportive of upside.",
        "fii_exit_dii_absorb": "FIIs exiting, DIIs absorbing — the classic India "
            "standoff; downside cushioned but FII selling caps rallies.",
        "fii_in_dii_book": "FII-led buying, DIIs booking — momentum but less sticky.",
        "broad_outflow": "Both selling — no institutional bid; caution.",
    }[r]
    if s.get("status") == "ok" and s["trend"] == "rising":
        base += " Rising SIP run-rate adds a structural floor."
    return base


if __name__ == "__main__":
    import json
    # illustrative recent tape: FII selling, DII absorbing
    days = [
        FlowDay("2026-06-20", -3200, 2800, -1500),
        FlowDay("2026-06-23", -4100, 3600, -2200),
        FlowDay("2026-06-24", -1800, 2100, -900),
        FlowDay("2026-06-25", -2600, 3000, -1700),
        FlowDay("2026-06-26", -900, 1500, -400),
    ]
    months = [
        SIPMonth("2026-03", 25800), SIPMonth("2026-04", 26400),
        SIPMonth("2026-05", 27100),
    ]
    print(json.dumps(flow_bias(days, months), indent=2))
