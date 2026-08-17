#!/usr/bin/env python3
"""earnings_acceleration.py — the one unresolved question the index actually turns on.

THE QUESTION, STATED SO IT CAN BE WRONG
    "Nifty is cheap" is not a research question; it has no observable that settles it.
    This is:

        H — Aggregate Nifty-50 earnings growth reaccelerates from its recent run-rate
            toward the 12-14% the sell side is assuming for FY27.

    Everything below exists to feed that one claim, because the valuation work led here:
    at roughly today's multiple, 13% growth gives ~27,200 over a year and the recent
    run-rate gives ~26,047. The gap between the two Nifty levels is almost entirely the
    growth assumption, not the multiple. So the multiple is not the thing to research.

THE FOUR LAYERS
    L1 EVIDENCE      what earnings have actually done — measured here, annually and
                     quarterly, from delivery_history.json.
    L2 EXPECTATIONS  what is being assumed — the sell-side number and what the
                     trailing→forward multiple already pays for.
    L3 VALUATION     the multiple, and where it sits in its own reconstructed history.
    L4 PRICE         spot, and the level each (growth, multiple) pair implies.

    The gap between L1 and L2 is the research question. L3 and L4 are consequences.

WHY QUARTERLY, NOT ANNUAL
    The annual series says FY26 grew 6.9%. The quarterly series says that 6.9% is the
    average of a year that decelerated through itself, and the EXIT rate is far lower.
    An annual number hides the shape; a growth question is a question about shape. The
    quarterly panel is the sharper instrument and it changes what the hypothesis is
    asking — not "can 6.9% become 13%" but "can the exit rate become 13%".

TWO PANELS, DELIBERATELY
    The 47-name balanced panel runs to the March quarter — complete, comparable, but one
    quarter stale. The most recent June quarter has only 37 of 47 reported, so it is
    computed on a 37-name panel measured against ITS OWN year-ago base. Mixing the two
    would manufacture a growth rate out of a change in coverage, which is the same class
    of error as comparing a restated series to an original one.

EVIDENCE CHANNELS
    The channels that would move this hypothesis are listed with something more useful
    than a name: whether THIS REPO can currently observe each one. Three are already
    measurable, one is measurable but needs a time series it does not yet have, and the
    rest need data that is not here. Saying which is which is the difference between a
    research plan and a wish list.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import re
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

DELIVERY = os.path.join(_ROOT, "delivery_history.json")
SCREENER_PANEL = os.path.join(_ROOT, "screener_panel.json")
UNIVERSE = os.path.join(_ROOT, "nifty-50-stock-list.csv")
SNAPSHOT = os.path.join(_ROOT, "expectation_snapshots.json")
DB = os.path.join(_ROOT, "option_chains.db")
OUT = os.path.join(_ROOT, "earnings_acceleration.json")

# The sell-side assumption this hypothesis is tested against.
TARGET_BAND_PCT = (12.0, 14.0)
# Sectors whose Screener sales / operating-profit rows do not form a margin.
_MARGIN_EXCLUDED_SECTORS = {"Financial Services"}
TARGET_SOURCE = "Axis Securities FY27 earnings growth band, quoted 2026-08"


def _load(p, d=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return d


def _weights() -> dict:
    with open(UNIVERSE) as f:
        return {r["Symbol"]: float(r["Weight"]) for r in csv.DictReader(f)}


# ------------------------------------------------------------------- L1 · evidence

def _annual(hist: dict) -> dict:
    years = [f"{y}-03-31" for y in range(2018, 2027)]
    panel = [s for s, v in hist.items()
             if all(any(x["period"] == y for x in v["series"]) for y in years)]
    agg = {y: sum(next(x["net_profit"] for x in hist[s]["series"] if x["period"] == y)
                  for s in panel) for y in years}
    g = [{"fy": int(years[i][:4]),
          "yoy_pct": round((agg[years[i]] / agg[years[i - 1]] - 1) * 100, 1)}
         for i in range(1, len(years))]
    return {"panel": len(panel), "growth": g, "latest_fy_pct": g[-1]["yoy_pct"]}


ATTRIB_PANEL = os.path.join(_ROOT, "attributable_panel.json")


def _quarters_source() -> tuple[dict, str]:
    """Quarterly PAT per company. Prefers attributable_panel.json over the Excel export.

    WHY THE PREFERENCE, since both claim the same line item:

    The export (delivery_history.json) has the RIGHT definition — net profit attributable
    to owners — but its coverage freezes at whatever had reported when the workbooks were
    downloaded. For Q1 FY27 that was 37 of 47 names, and `download_screener.py` skipped
    every cached workbook while printing "All downloads complete", so re-running it
    refreshed nothing. The exit rate sat at +3.73% on a 37-name panel for two days.

    attributable_panel.json derives the same line item as EPS x shares — EPS being
    attributable-profit-per-share by definition — from the page scrape, which covers all
    47 names and 13 quarters. Cross-checked against the export on 470 observations: 96%
    agree within 3%, and the large names are exact (Reliance, ONGC, TCS, HDFC Bank,
    Bajaj Finserv all -0.0%). The residual is share-count timing, not definition.

    This matters beyond one number. On the stale panel the path read
    +12.4 -> +7.2 -> +7.9 -> +3.5 -> +3.7 and the tracker called it decelerating. On the
    full panel it reads +18.8 -> +13.1 -> +9.7 -> +4.5 -> +2.5 -> +7.1 — a trough in
    Q4 FY26 and a turn. Opposite conclusions from the same underlying quarters.

    Falls back to the export if the panel is missing, and SAYS which it used.
    """
    doc = _load(ATTRIB_PANEL, {}) or {}
    by = doc.get("by_symbol") or {}
    if by:
        hist = {s: {"quarters": [{"period": p, "net_profit": v}
                                 for p, v in sorted(q.items())]}
                for s, q in by.items()}
        return hist, "attributable_panel.json (EPS x shares, all reporters)"
    return ((_load(DELIVERY, {}) or {}).get("history", {}),
            "delivery_history.json (Excel export — coverage may be stale)")


def _quarterly(hist: dict, wt: dict) -> dict:
    """Aggregate quarterly PAT growth on two panels — see module docstring."""
    have = {}
    for s, v in hist.items():
        for q in v.get("quarters", []):
            have.setdefault(q["period"], set()).add(s)
    if not have:
        return {"available": False}
    periods = sorted(have)
    full_n = max(len(x) for x in have.values())
    complete = [p for p in periods if len(have[p]) == full_n]

    def pat(sym, per):
        return next((q["net_profit"] for q in hist[sym].get("quarters", [])
                     if q["period"] == per), None)

    def yoy(per, base, panel):
        ok = [s for s in panel if pat(s, per) is not None and pat(s, base) is not None]
        if not ok:
            return None
        c, p = sum(pat(s, per) for s in ok), sum(pat(s, base) for s in ok)
        return {"period": per, "yoy_pct": round((c / p - 1) * 100, 1), "names": len(ok),
                "weight_pct": round(sum(wt.get(s, 0) for s in ok), 1),
                "aggregate_pat_cr": round(c, 0)}

    def base_of(per):
        y, m, d = per.split("-")
        return f"{int(y) - 1}-{m}-{d}"

    balanced_panel = [s for s in hist if all(s in have[p] for p in complete)]
    balanced = [r for r in (yoy(p, base_of(p), balanced_panel) for p in complete)
                if r and r["names"] == len(balanced_panel)]

    # The freshest quarter, on whatever panel has actually reported it — measured
    # against that same panel's own year-ago base.
    latest = periods[-1]
    partial = None
    if latest not in complete:
        rep = sorted(have[latest])
        partial = yoy(latest, base_of(latest), rep)
        if partial:
            partial["consistent_panel_path"] = [
                r for r in (yoy(p, base_of(p), rep) for p in periods[-5:]) if r]

    path = [r["yoy_pct"] for r in balanced]
    return {
        "available": True,
        "balanced_panel_names": len(balanced_panel),
        "balanced": balanced,
        "partial_latest": partial,
        "exit_rate_pct": (partial or (balanced[-1] if balanced else {})).get("yoy_pct"),
        # Monotonic over the last three readings, and the LATEST reading is included —
        # an earlier version tested only the balanced path and kept printing
        # "decelerating" after the freshest quarter had turned up.
        "decelerating": len(path) >= 3 and path[-1] < path[-2] < path[-3],
        "turned_up": len(path) >= 2 and path[-1] > path[-2],
        "path": path,
        "note": ("YoY is quarter versus the SAME quarter a year earlier, never "
                 "sequential — Indian earnings are seasonal enough that Q1-vs-Q4 is "
                 "mostly the calendar. The partial panel is measured against its own "
                 "year-ago base so a change in coverage cannot masquerade as growth."),
    }


# Verified against HDFC Bank and SBI Q1FY27 filings (correction C31). The arithmetic
# ties exactly — SBI's Revenue minus Interest reproduces the filed NII of 46,992 to the
# rupee — but the reconciliation exposed a limit that no internal check could have found,
# and it travels with the number from here on rather than living in a conversation.
_FINANCING_CAVEAT = {
    "excludes_other_income": True,
    "measures": ("the lending spread net of costs — interest earned, less interest paid, "
                 "less operating expenses AND provisions, which Screener buries together "
                 "in one cost line"),
    "does_not_measure": ("fee, treasury and distribution income, which sit outside both "
                         "the numerator and the denominator"),
    "share_of_pretax_profit_outside_metric": {"HDFCBANK_Q1FY27": 51, "SBIN_Q1FY27": 56},
    "verified_against": ("HDFC Bank and SBI Q1FY27 filed results — Revenue = interest "
                         "earned, Interest = interest expended, Net Profit, Other Income "
                         "and Gross NPA % all match; FinProfit + OtherIncome - Depn "
                         "reproduces reported PBT to within 1 cr at both"),
    "therefore": ("NOT a proxy for bank profitability. Roughly half of what a bank earns "
                  "is invisible to it, and the two halves can move in opposite directions "
                  "— SBI's fee income grew 20.83% in the same quarter treasury fell. Read "
                  "it as a spread measure and nothing wider."),
}

# The profit series is one LINE ITEM among several that all get called "net profit", and
# which one you pick changes the number by 16% at index level. Stated here because an
# implicit choice is the failure that produced C26 — someone eventually checks our
# aggregate against a newspaper and the difference looks like an error.
PROFIT_DEFINITION = {
    "line_item": "net profit attributable to owners of the parent",
    "excludes": "minority / non-controlling interests",
    "verified": ("JSW Steel FY26 filed 'attributable to owners' = 22,316 cr = our figure "
                 "exactly, against 25,508 cr including minority interest (C32)"),
    "why": ("Index EPS must be earnings attributable to the constituents' OWN "
            "shareholders. Minority interest belongs to other people and cannot be "
            "capitalised into a per-share number for an index investor."),
    "not_comparable_to": ("street and newspaper headlines, which quote total consolidated "
                          "profit INCLUDING minority interest. Reliance reads 80,775 cr on "
                          "our definition and 95,754 cr on theirs — both correct, different "
                          "questions. This is a research-series definition and is NOT "
                          "necessarily identical to NSE Indices' P/E earnings methodology, "
                          "which uses free-float-adjusted constituent earnings, "
                          "consolidated where available and standalone otherwise."),
    "consistency_check": ("our index EPS of 1,195 matches NSE's implied 1,185 within 0.8% "
                          "WHILE the aggregate sits 16% below street figures. Both hold at "
                          "once; they are not in tension."),
    "reported_not_underlying": ("every growth rate built on this series is REPORTED, not "
                                "underlying. FY26 reads +6.92% reported and +6.68% "
                                "ex-one-off, but ranges +4.63% to +9.01% depending which "
                                "exceptional is stripped — JSW Steel's 18,051 cr BPSL gain "
                                "and ITC's 15,145 cr hotels-demerger gain push opposite "
                                "ways and happen to cancel (C33, open item O14)."),
}

# The exit rate is measured on whichever names have reported. That panel is not a random
# sample: late reporters have been growing FASTER, so a partial panel reads LOW. Measured
# on Jun-2026 — 37 names give +3.73%, all 47 give +4.55%, of which +1.31pp is coverage and
# -0.49pp is the definition difference between sources. Quoting +3.7% as "Nifty earnings
# growth" without this is the kind of unstated qualification C26 was about.
EXIT_RATE_COVERAGE_NOTE = (
    "PRELIMINARY — measured on the names that have reported, which is not a random "
    "sample. On Jun-2026 the late reporters (SBIN, BHARTIARTL, ONGC, POWERGRID, TITAN, "
    "TRENT and four others) grew faster than the early ones, so the partial-panel rate "
    "reads about 1.3pp LOW. Read it as the observed panel's growth, not the index's.")

def _margin(_hist: dict = None, _sector: dict = None) -> dict:
    """Aggregate margin — TWO cohorts, read from screener_panel.json, never summed.

    HISTORY, because the shape of the mistake matters more than the number:

    v1 summed operating profit over sales across all 47 names, reported 28.6%, and
    carried a caveat claiming banks were excluded because they "report no meaningful
    sales line". The caveat was FALSE — the banks were in the panel the whole time, with
    pseudo-margins of 58-71%, because Screener's export gives a bank `sales` = interest
    earned and an `operating profit` that does not net interest expense. Caught by
    cross-checking Trendlyne's OPM % TTM screener (correction C26). A wrong number is
    bad; a wrong number wearing a caveat that says the check was already done is worse.

    v2 excluded financials outright: 20.2% TTM on 37 names. Correct, but it left the
    channel covering ~65% of index weight and BLIND to the cohort supplying the largest
    share of index earnings growth.

    v3, this one, reads Screener's LENDER template — `Financing Profit` (revenue less
    interest less expenses) over `Revenue` — which the Excel export throws away and the
    company page carries. Seven names qualify, 31.95 of 34.75 financial-sector weight, so
    coverage goes 65.25% -> 97.20%.

    WHAT THIS DELIBERATELY DOES NOT DO: blend the two into one index-wide margin.
    Operating margin and financing margin are ratios over different denominators. Summing
    the numerators and denominators across both cohorts yields a figure with no referent —
    C26's error committed in the opposite direction, and it would LOOK like restored
    coverage while meaning less than the honest gap it replaced. Two series that each mean
    something beat one that does not.

    The remaining 2.8% (life insurers, financial holding companies) is excluded because
    the METRIC does not apply, not because data is missing — see the sensitivity block in
    screener_panel.json, which shows the aggregate moving from 18.97% to 18.10% with every
    company's profit held constant.
    """
    panel = _load(SCREENER_PANEL, {}) or {}
    if not panel:
        return {"available": False,
                "reason": ("screener_panel.json missing — run "
                           "data_agent/fundamentals/ingest_screener_page.py")}

    gen = (panel.get("quarterly") or {}).get("generic") or {}
    lend = (panel.get("quarterly") or {}).get("lender") or {}
    if not gen.get("available"):
        return {"available": False, "reason": "no generic cohort series in screener_panel.json"}

    def cohort(d, key):
        if not d.get("available"):
            return {"available": False, "reason": d.get("reason")}
        return {
            "available": True,
            "measure": d["measure"],
            "ratio": f"{d['numerator']} / {d['denominator']}",
            "panel": d["panel"],
            "weight_pct": ((panel.get("cohorts") or {}).get(key) or {}).get("weight_pct"),
            # TTM is the HEADLINE. The lender quarterly series swings up to 4.4pp because
            # Screener's `Expenses` for a lender absorbs provisions, which are lumpy —
            # QoQ stdev 2.30pp against 0.89pp for non-financials. Reading a trend off it
            # compares a trough quarter with a peak one (correction C30).
            "ttm_pct": d["latest_ttm_pct"],
            "ttm_period": d["latest_ttm_period"],
            "ttm_yoy_pp": d["yoy_ttm_change_pp"],
            "ttm_series": d["ttm_series"][-8:],
            "quarter_pct": d["latest_quarter_pct"],
            "quarter_yoy_pp": d["yoy_quarter_change_pp"],
            "qoq_volatility_pp": d["qoq_volatility_pp"],
            "ttm_volatility_pp": d["ttm_volatility_pp"],
            "trend_readable_quarterly": (d["qoq_volatility_pp"] or 0) <= 1.5,
            **(_FINANCING_CAVEAT if key == "lender" else {}),
        }

    return {
        "available": True,
        "blended": False,
        "operating": cohort(gen, "generic"),
        "financing": cohort(lend, "lender"),
        "coverage": panel.get("coverage"),
        "methodology": panel.get("methodology"),
        "sensitivity_test": panel.get("sensitivity_test"),
        "source": panel.get("source"),
        "note": ("TWO cohorts, never summed — see the docstring. Headline figures are TTM "
                 "because the lender quarterly series is too noisy to carry a trend. "
                 "Coverage is 97.2% of index weight; the excluded 2.8% is a metric that "
                 "does not apply, not data that is missing."),
    }


def _expectations() -> dict:
    snap = (_load(SNAPSHOT, {}) or {}).get("snapshots") or []
    wt = _weights()
    implied = None
    if snap:
        rows = snap[-1]["rows"]
        pe = {r["symbol"]: (r.get("trailingPE"), r.get("forwardPE")) for r in rows}
        tn = td = fn = fd = 0.0
        for s, w in wt.items():
            t, f = pe.get(s, (None, None))
            if t and t > 0:
                tn += w; td += w / t
            if f and f > 0:
                fn += w; fd += w / f
        if td and fd:
            implied = round(((tn / td) / (fn / fd) - 1) * 100, 1)
    return {
        "sell_side_band_pct": list(TARGET_BAND_PCT),
        "sell_side_source": TARGET_SOURCE,
        "market_implied_pct": implied,
        "market_implied_note": ("Trailing P/E ÷ forward P/E − 1 across the index. Yahoo's "
                                "forward multiple spans one to two years rather than a "
                                "clean next fiscal year, so this is NOT a FY27 growth "
                                "estimate and must not be compared to one directly. It is "
                                "here as an upper bound on what the price is paying for."),
        "snapshots_held": len(snap),
        "revisions_measurable": len(snap) >= MIN_SNAPSHOTS_FOR_REVISIONS,
        "revisions_needed": MIN_SNAPSHOTS_FOR_REVISIONS,
        "revisions_note": _revisions_channel(len(snap))[1],
    }


# Three snapshots, not two, and the reason is not arbitrary: two captures give exactly
# ONE difference, and a single difference cannot be separated from noise. Three give two
# differences, which is the minimum needed to say anything about direction at all. Even
# then it is weak — the honest read only arrives once captures straddle a reporting
# season, so an estimate cut has something to be a cut FROM.
MIN_SNAPSHOTS_FOR_REVISIONS = 3


def _revisions_channel(n_snapshots: int) -> tuple[bool, str]:
    """Whether the revisions channel can be read yet, derived from what is on disk."""
    if n_snapshots >= MIN_SNAPSHOTS_FOR_REVISIONS:
        return True, (f"{n_snapshots} snapshots held, giving {n_snapshots - 1} "
                      "differences — direction is measurable. Treat early readings as "
                      "provisional until captures straddle a reporting season.")
    need = MIN_SNAPSHOTS_FOR_REVISIONS - n_snapshots
    held = "no captures" if n_snapshots == 0 else f"{n_snapshots} capture(s)"
    return False, (f"expectation_snapshots.json holds {held}; {MIN_SNAPSHOTS_FOR_REVISIONS} "
                   f"are needed so there are at least two differences to compare "
                   f"({need} more to go). This is the highest-value gap on the list.")


# ------------------------------------------------------ evidence channels for/against

# Each channel names what to watch, and — the part that matters — whether this repo can
# currently see it. A channel marked observable=False is a data gap, not a signal.
CHANNELS = [
    # observable is computed, NOT hardcoded — see _revisions_channel() below. The
    # previous version pinned this to False with the text "holds a single capture",
    # which silently became a lie the moment a second snapshot landed and would have
    # stayed a lie after the third. A status flag whose underlying condition changes
    # must be derived from that condition, or it is decoration.
    {"id": "earnings_revisions", "direction": "both",
     "watch": "Direction of consensus EPS revisions across the index",
     "observable": None, "why": None,
     "source_needed": "expectation_snapshot.py on a weekly cron"},
    {"id": "operating_margin", "direction": "both",
     "watch": "Aggregate operating margin, quarter on quarter and year on year",
     "observable": True, "why": "computed here from the quarterly panel",
     "source_needed": None},
    {"id": "quarterly_pat", "direction": "both",
     "watch": "Aggregate quarterly PAT growth — the exit rate, not the annual average",
     "observable": True, "why": "computed here", "source_needed": None},
    {"id": "it_services", "direction": "against",
     "watch": "IT pricing resets and deal repricing — 13.0% of index weight with every "
              "constituent already failing the delivered-growth screen",
     "observable": True, "why": "delivery_history covers all five IT names; the "
     "it_ai_deflation factor tracks the qualitative side",
     "source_needed": None},
    {"id": "commodity_costs", "direction": "both",
     "watch": "Crude — input cost for FMCG, paints, autos, and the CAD channel",
     "observable": True, "why": "price_bars CRUDEOIL (WTI, not Brent — correction C6), "
     "and per-stock oil betas in oil_impact.json",
     "source_needed": None},
    {"id": "bank_credit_growth", "direction": "for",
     "watch": "System credit growth and NIM direction — financials are ~35% of index weight",
     "observable": False, "why": "no RBI credit series in this repo; bank_view_data.py "
     "covers valuation, not system aggregates",
     "source_needed": "RBI weekly statistical supplement"},
    {"id": "capex_cycle", "direction": "for",
     "watch": "Order books and capex announcements in capital goods and infrastructure",
     "observable": False, "why": "no order-book series; only the curated drivers file",
     "source_needed": "company order-inflow disclosures"},
    {"id": "domestic_demand", "direction": "against",
     "watch": "Volume growth in consumption names versus price-led growth",
     "observable": False, "why": "sales are captured but not volume/price split",
     "source_needed": "management commentary, per-company"},
]

# Crude first-pass tagger for the news agent. Deliberately narrow: a keyword hit is a
# CANDIDATE for a human or an LLM pass, never a scored signal. Precision over recall —
# an over-eager tagger that marks every article "relevant to earnings" is worse than
# nothing, because it launders noise as evidence.
_PATTERNS = [
    ("earnings_revisions", "for", r"\b(?:raise[sd]?|hike[sd]?|upgrade[sd]?)\s+(?:its\s+)?(?:FY\d+\s+)?(?:EPS|earnings|profit)\s+(?:estimate|forecast|guidance)"),
    ("earnings_revisions", "against", r"\b(?:cut|cuts|lower(?:s|ed)?|trim(?:s|med)?|downgrade[sd]?)\s+(?:its\s+)?(?:FY\d+\s+)?(?:EPS|earnings|profit)\s+(?:estimate|forecast|guidance)"),
    ("operating_margin", "for", r"\bmargin[s]?\s+(?:expan\w+|improv\w+|recover\w+|beat)"),
    ("operating_margin", "against", r"\bmargin[s]?\s+(?:compress\w+|contract\w+|pressur\w+|declin\w+|erod\w+)"),
    ("bank_credit_growth", "for", r"\bcredit\s+growth\b[^.]{0,40}\b(?:acceler\w+|pick(?:s|ed)?\s+up|improv\w+|strong)"),
    ("bank_credit_growth", "against", r"\bcredit\s+growth\b[^.]{0,40}\b(?:slow\w+|moderat\w+|weak\w+|declin\w+)"),
    ("capex_cycle", "for", r"\b(?:order\s+(?:book|inflow)|capex)\b[^.]{0,40}\b(?:record|surge\w*|jump\w*|rise[sn]?|strong|expand\w+)"),
    ("commodity_costs", "against", r"\b(?:crude|brent|oil)\b[^.]{0,30}\b(?:surge\w*|spike[sd]?|jump\w*|above\s*\$?1[01]\d)"),
    ("commodity_costs", "for", r"\b(?:crude|brent|oil)\b[^.]{0,30}\b(?:slump\w*|fall[sn]?|ease[sd]?|below\s*\$?[67]\d)"),
    ("it_services", "against", r"\b(?:deal|contract)\b[^.]{0,40}\b(?:cut|trimm?ed|renegotiat\w+|repric\w+|scaled\s+back)"),
    ("domestic_demand", "against", r"\b(?:volume|demand)\s+(?:growth\s+)?(?:slow\w+|weak\w+|declin\w+|soft\w+)"),
]
_COMPILED = [(c, d, re.compile(p, re.I)) for c, d, p in _PATTERNS]


def classify(text: str) -> list[dict]:
    """Which evidence channels does this text touch, and in which direction?

    Returns [] for most articles and that is correct. This is a first-pass filter for a
    human or an LLM, not a scorer: `confidence` is always "keyword", never a number, so
    nothing downstream can mistake a regex hit for a measurement.
    """
    if not text:
        return []
    hits, seen = [], set()
    for ch, direction, rx in _COMPILED:
        m = rx.search(text)
        if m and (ch, direction) not in seen:
            seen.add((ch, direction))
            hits.append({"channel": ch, "direction": direction,
                         "matched": m.group(0)[:80], "confidence": "keyword"})
    return hits


# ---------------------------------------------------------------------------- build

def build() -> dict:
    hist = (_load(DELIVERY, {}) or {}).get("history", {})
    if not hist:
        return {"available": False, "reason": "delivery_history.json missing"}
    wt = _weights()

    ann = _annual(hist)
    qhist, qsrc = _quarters_source()
    qtr = _quarterly(qhist, wt)
    qtr["source"] = qsrc
    marg = _margin()
    exp = _expectations()

    # Resolve the dynamic channel against what is actually on disk.
    obs, why = _revisions_channel(exp["snapshots_held"])
    channels = [dict(c, observable=obs, why=why) if c["id"] == "earnings_revisions"
                else c for c in CHANNELS]

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    spot = con.execute("SELECT close FROM price_bars WHERE symbol='NIFTY' AND "
                       "timeframe='1d' AND close IS NOT NULL ORDER BY ts DESC "
                       "LIMIT 1").fetchone()[0]
    con.close()

    exit_rate = qtr.get("exit_rate_pct")
    lo, hi = TARGET_BAND_PCT
    gap = round(lo - exit_rate, 1) if exit_rate is not None else None

    return {
        "as_of": datetime.date.today().isoformat(),
        "hypothesis": {
            "id": "H-EARN-ACCEL",
            "claim": (f"Aggregate Nifty-50 earnings growth reaccelerates from its recent "
                      f"quarterly run-rate of {exit_rate}% toward the {lo:.0f}-{hi:.0f}% "
                      f"assumed for FY27."),
            "status": "OPEN",
            "why_it_matters": ("At roughly today's multiple the two assumptions are ~1,150 "
                               "Nifty points apart over a year. The multiple is not the "
                               "research question; this is."),
            "gap_pp": gap,
            "settles_on": ("Four consecutive quarters of aggregate PAT growth. It is "
                           "confirmed if the exit rate reaches the band and holds two "
                           "quarters; refuted if it stays below ~8% through FY27 H1."),
            "next_observation": "Q2 FY27 results, from late October 2026",
        },
        "profit_definition": PROFIT_DEFINITION,
        "L1_evidence": {"annual": ann, "quarterly": qtr, "margin": marg,
                        "exit_rate_coverage": (EXIT_RATE_COVERAGE_NOTE
                                               if (qtr.get("partial_latest") or {}) else None)},
        "L2_expectations": exp,
        "L3_valuation": {"note": "see nifty_outlook.json — reconstructed P/E and its percentiles"},
        "L4_price": {"spot": round(spot, 1)},
        "channels": channels,
        "channels_observable": sum(1 for c in channels if c["observable"]),
        "note": ("The gap between L1 and L2 IS the research question. Everything the news "
                 "agent reads should be sorted by whether it bears on that gap; an article "
                 "that does not touch a channel is not evidence about the index, however "
                 "loud it is."),
        "caveats": [
            "Constant constituents — today's Nifty 50 back-cast. Names that entered the "
            "index by growing make the historical series look better than the index of "
            "the day actually was.",
            "The quarterly panel starts at the March 2024 quarter, so there are nine "
            "complete quarters and five YoY observations. A deceleration visible across "
            "five points is suggestive of a trend and is not a tested one.",
            "The margin series excludes financials in substance — banks and NBFCs have no "
            "meaningful sales line — while financials are ~35% of index weight.",
            "The keyword tagger is a filter, never a score. It returns candidates for a "
            "human or LLM pass and marks every hit `confidence: keyword`.",
        ],
    }


def main() -> int:
    doc = build()
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1)

    h = doc["hypothesis"]
    print(f"HYPOTHESIS {h['id']} — {h['status']}\n  {h['claim']}\n  gap to band: {h['gap_pp']}pp"
          f"   next observation: {h['next_observation']}\n")

    a = doc["L1_evidence"]["annual"]
    print(f"L1 ANNUAL   panel {a['panel']} names   "
          f"[{PROFIT_DEFINITION['line_item']} — REPORTED, not underlying (O14)]")
    print("   " + "  ".join(f"FY{x['fy'] % 100}:{x['yoy_pct']:+.1f}%" for x in a["growth"]))

    q = doc["L1_evidence"]["quarterly"]
    if q.get("available"):
        print(f"\nL1 QUARTERLY   balanced panel {q['balanced_panel_names']} names"
              f"   [{q.get('source','?')}]")
        for r in q["balanced"]:
            print(f"   {r['period']}  {r['yoy_pct']:+6.1f}%   PAT {r['aggregate_pat_cr']:,.0f} cr")
        p = q.get("partial_latest")
        if p:
            print(f"\n   most recent quarter {p['period']} — {p['names']} of "
                  f"{q['balanced_panel_names']} reported ({p['weight_pct']}% of weight), "
                  f"measured on its own panel:")
            for r in p.get("consistent_panel_path", []):
                print(f"     {r['period']}  {r['yoy_pct']:+6.1f}%")
        if q.get("partial_latest"):
            print(f"   {'':13s}^ PRELIMINARY: partial panel, and late reporters have been")
            print(f"   {'':13s}  growing faster — this reads ~1.3pp LOW (C33)")
        if q.get("turned_up") and not q.get("decelerating"):
            pth = "  ".join(f"{x:+.1f}" for x in q.get("path", [])[-6:])
            print(f"   path {pth}  -> TROUGHED AND TURNED UP, not decelerating")
        print(f"   EXIT RATE {q['exit_rate_pct']:+.1f}%"
              f"{'   (decelerating)' if q['decelerating'] else ''}")

    m = doc["L1_evidence"]["margin"]
    if not m.get("available"):
        print(f"\nL1 MARGIN   unavailable — {m.get('reason')}")
    else:
        print("\nL1 MARGIN   two cohorts, reported separately and never summed")
        for key, label in (("operating", "operating  "), ("financing", "financing  ")):
            c = m[key]
            if not c.get("available"):
                print(f"   {label} unavailable — {c.get('reason')}")
                continue
            print(f"   {label} {c['ttm_pct']:5.1f}% TTM  {c['ttm_yoy_pp']:+.1f}pp YoY   "
                  f"{c['panel']:2d} names, {c['weight_pct']:5.2f}% weight   ({c['ratio']})")
            if c.get("excludes_other_income"):
                sh = c["share_of_pretax_profit_outside_metric"]
                print(f"                SPREAD ONLY — excludes fee/treasury/distribution "
                      f"income; ~{min(sh.values())}-{max(sh.values())}% of bank pre-tax "
                      f"profit sits outside it (C31)")
            if not c["trend_readable_quarterly"]:
                print(f"                raw quarter {c['quarter_pct']}% "
                      f"({c['quarter_yoy_pp']:+.1f}pp) — QoQ noise "
                      f"{c['qoq_volatility_pp']}pp vs TTM {c['ttm_volatility_pp']}pp, "
                      f"do not read the quarterly as a trend")
        cov = m.get("coverage") or {}
        if cov:
            print(f"   coverage    {cov['measured_pct']}% of index weight measured; "
                  f"{cov['excluded_pct']}% excluded — {cov['reason']}")

    e = doc["L2_expectations"]
    print(f"\nL2 EXPECTATIONS   sell side {e['sell_side_band_pct'][0]:.0f}-"
          f"{e['sell_side_band_pct'][1]:.0f}%   market-implied {e['market_implied_pct']}% "
          f"(1-2yr, not FY27)")
    print(f"   revisions measurable: {e['revisions_measurable']} "
          f"({e['snapshots_held']} snapshot(s) held)")

    print(f"\nEVIDENCE CHANNELS   {doc['channels_observable']} of {len(CHANNELS)} observable today")
    for c in CHANNELS:
        print(f"   {'[+]' if c['observable'] else '[ ]'} {c['id']:20s} {c['direction']:8s} {c['watch'][:62]}")
        if not c["observable"]:
            print(f"       needs: {c['source_needed']}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
