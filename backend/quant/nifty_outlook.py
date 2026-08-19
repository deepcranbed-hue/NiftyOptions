#!/usr/bin/env python3
"""nifty_outlook.py — where the Nifty could be in 6 months, 1 year and 2 years.

READ THIS BEFORE READING THE NUMBERS
    This file does NOT forecast. This repo has tested whether the things people
    forecast with actually forecast, and the answers are in StrategyBacktesting/
    Hypotheses.md: the daily macro→index regression was retired at R² = 0.036, high
    and rising crude fails to predict next-day weakness at p = 0.979, crude beta flips
    sign by regime (+0.006 full sample, −0.075 in 2026), and every FII positioning
    signal that survived a null test turned out to be unreachable in time. Nothing in
    this repo has earned the right to produce a point estimate for the index.

    So the tab this file feeds does two separate, honest things instead.

    LAYER 1 — WHAT THE INDEX HAS ACTUALLY DONE. Rolling 6M / 1Y / 2Y total price
    returns over the whole available history, reported as a distribution: median,
    deciles, best, worst, share positive. This is the no-view anchor. Its weakness is
    stated in the output rather than hidden: with ~8.6 years of daily bars there are
    thousands of OVERLAPPING windows but only a handful of INDEPENDENT ones, and the
    2-year row is the worst offender. Both counts are reported. An overlapping-window
    percentile is a description of one path through history, not a probability.

    LAYER 2 — SCENARIO ARITHMETIC. Every level here is
        level(T) = EPS_today × (1 + g)^T × exit_PE
    with g and exit_PE stated per scenario. Nothing is hidden in a model. Change the
    two inputs and the level changes; that is the entire mechanism. Scenarios carry NO
    probabilities from this file — weights belong to whoever is reading, and the UI
    makes them editable so the expected value is the reader's own, not this file's.

    The two layers meet in one number per scenario: the PERCENTILE of that scenario's
    return within layer 1. If someone's "base case" sits at the 92nd percentile of
    every 1-year outcome since 2018, that is worth knowing before it is called a base
    case.

EXTERNAL TARGETS ARE STORED AS LEVELS, NOT REBUILT
    Sell-side targets are quoted as (forward-year EPS × target multiple) regardless of
    where in the year the target is set, which is a different convention from the one
    above. Rather than fudge their numbers into this model, a scenario with a published
    level keeps that level and this file BACKS OUT the exit multiple it implies under
    this convention, at each horizon. Where the two conventions disagree, the
    disagreement is the output.

HISTORY DEPTH
    Uses price_bars NIFTY 1d (2018→) by default. If `nifty_history_long.csv` exists at
    the repo root with columns date,close it is used instead and the longer sample is
    reported. That file is not committed because neither this container nor the device
    VM can reach Yahoo; the command to build it on a machine that can is printed at the
    end of a run.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

DB = os.path.join(_ROOT, "option_chains.db")
UNIVERSE = os.path.join(_ROOT, "nifty-50-stock-list.csv")
SNAPSHOT = os.path.join(_ROOT, "expectation_snapshots.json")
LONG_HISTORY = os.path.join(_ROOT, "nifty_history_long.csv")
OUT = os.path.join(_ROOT, "nifty_outlook.json")

HORIZONS = [("6M", 126, 0.5), ("1Y", 252, 1.0), ("2Y", 504, 2.0)]

# Below this many INDEPENDENT windows a row is not evidence and is flagged as such, so
# the UI can suppress it rather than print a median with a warning nobody reads. At
# 2018-onward history the 2Y row has 3 and its drawdown-conditioned variant has 1.
MIN_INDEPENDENT = 5

# The measurement layer. Scenario inputs come from here, not from this file's opinion.
try:
    from index_valuation import (index_earnings, pe_series, conditional_base_rates,
                                 percentile_of)
except ImportError:
    sys.path.insert(0, _HERE)
    from index_valuation import (index_earnings, pe_series, conditional_base_rates,
                                 percentile_of)

# One definition of cheap/fair/rich for the whole repo — the index card in
# nifty50_routes.py imports the same constant.
sys.path.insert(0, os.path.join(_ROOT, "backend"))
try:
    from quant.valuation_band import PE_BAND, pe_label as _pe_label
except ImportError:
    PE_BAND = (18.0, 21.0, 24.0)

    def _pe_label(pe):
        lo, mid, hi = PE_BAND
        return ("cheap" if pe < lo else "fair" if pe < mid
                else "mildly rich" if pe < hi else "rich")


# ------------------------------------------------------------------ layer 0: anchor

def anchor() -> dict:
    """Spot, and the bottom-up weighted trailing/forward P/E — Σw ÷ Σ(w/PE), i.e. total
    market cap over total earnings. Same method as the Nifty view's index card, so the
    two cannot drift apart about what the index earns."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ts, spot = con.execute(
        "SELECT ts, close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
        "AND close IS NOT NULL ORDER BY ts DESC LIMIT 1").fetchone()
    con.close()

    with open(UNIVERSE) as f:
        wt = {r["Symbol"]: float(r["Weight"]) for r in csv.DictReader(f)}
    with open(SNAPSHOT) as f:
        snap = json.load(f)["snapshots"][-1]
    pe = {r["symbol"]: (r.get("trailingPE"), r.get("forwardPE")) for r in snap["rows"]}

    tn = td = fn = fd = 0.0
    for s, w in wt.items():
        t, fw = pe.get(s, (None, None))
        if t and t > 0:
            tn += w; td += w / t
        if fw and fw > 0:
            fn += w; fd += w / fw
    wpe, wfpe = tn / td, fn / fd
    return {
        "spot": round(spot, 1), "spot_date": ts[:10],
        "trailing_pe": round(wpe, 2), "forward_pe": round(wfpe, 2),
        "pe_coverage_pct": round(tn, 1),
        "index_eps": round(spot / wpe, 0),
        "implied_growth_pct": round((wpe / wfpe - 1) * 100, 1),
        "pe_label": _pe_label(wpe),
        "expectation_captured_at": snap.get("captured_at"),
        "note": ("index_eps is IMPLIED (spot ÷ weighted trailing P/E), not a published "
                 "figure. implied_growth_pct is what the trailing→forward multiple "
                 "already pays for, and Yahoo's forward P/E spans one to two years "
                 "rather than a clean next fiscal year — so it is not directly "
                 "comparable to any single-year growth estimate."),
    }


# ------------------------------------------------------- layer 1: what has happened

def _closes() -> tuple[list[tuple[str, float]], dict]:
    if os.path.exists(LONG_HISTORY):
        with open(LONG_HISTORY) as f:
            rows = [(r["date"][:10], float(r["close"])) for r in csv.DictReader(f)
                    if r.get("close")]
        rows.sort()
        src = {"source": "nifty_history_long.csv", "extended": True}
    else:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = [(r[0][:10], float(r[1])) for r in con.execute(
            "SELECT ts, close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
            "AND close IS NOT NULL ORDER BY ts")]
        con.close()
        src = {"source": "price_bars NIFTY 1d", "extended": False}
    return rows, src


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    i = (len(sorted_vals) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def base_rates() -> dict:
    rows, src = _closes()
    closes = [c for _, c in rows]
    dates = [d for d, _ in rows]
    out = []
    for label, n, _yrs in HORIZONS:
        rets, starts = [], []
        for i in range(len(closes) - n):
            rets.append((closes[i + n] / closes[i] - 1) * 100)
            starts.append(i)
        if not rets:
            continue
        s = sorted(rets)
        # A drawdown conditioner computable from price alone — no valuation history
        # needed, and it is the state the index is in today.
        cond = []
        for i in starts:
            lo = max(0, i - 252)
            hi52 = max(closes[lo:i + 1])
            if closes[i] < hi52 * 0.95:
                cond.append((closes[i + n] / closes[i] - 1) * 100)
        cs = sorted(cond)
        out.append({
            "label": label, "sessions": n,
            "n_windows": len(rets),
            # The number that actually bounds the inference. Overlapping windows share
            # most of their data; only these are independent draws.
            "n_independent": len(rets) // n,
            "median_pct": round(s[len(s) // 2], 1),
            "p10_pct": round(_pct(s, 0.10), 1), "p25_pct": round(_pct(s, 0.25), 1),
            "p75_pct": round(_pct(s, 0.75), 1), "p90_pct": round(_pct(s, 0.90), 1),
            "min_pct": round(s[0], 1), "max_pct": round(s[-1], 1),
            "pct_positive": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1),
            "all_returns": [round(x, 2) for x in s],   # for the UI's distribution strip
            # The UI hides any row where this is false. A median computed off three
            # independent draws is a description of one path, and printing it beside a
            # caveat has never stopped anyone reading it as a distribution.
            "sufficient": (len(rets) // n) >= MIN_INDEPENDENT,
            "conditioned": ({
                "label": "starting >5% below the prior 52-week high (today's state)",
                "n_windows": len(cond), "n_independent": len(cond) // n,
                "sufficient": (len(cond) // n) >= MIN_INDEPENDENT,
                "median_pct": round(cs[len(cs) // 2], 1),
                "pct_positive": round(sum(1 for x in cond if x > 0) / len(cond) * 100, 1),
            } if cond else None),
        })
    return {
        **src, "first": dates[0], "last": dates[-1], "sessions": len(closes),
        "years": round((datetime.date.fromisoformat(dates[-1])
                        - datetime.date.fromisoformat(dates[0])).days / 365.25, 1),
        "horizons": out,
        "warning": ("n_windows counts OVERLAPPING windows and overstates the evidence "
                    "badly. n_independent is the honest count: at this history depth "
                    "the 2Y row rests on a single-digit number of independent draws, "
                    "which is a description of one path, not a distribution. Price "
                    "return only — no dividends. The sample also contains exactly one "
                    "crash (2020) and one large drawdown (2026), so the left tail is "
                    "two events, not a population."),
    }


# --------------------------------------------------------- layer 2: the scenarios

def _scenarios(earn: dict, pv: dict, spot: float) -> list[dict]:
    """Scenario inputs are PERCENTILES OF MEASURED DISTRIBUTIONS, not opinions.

    g comes from eight observed fiscal years of aggregate Nifty-50 net profit; exit_pe
    comes from ~2,000 observed days of the index's own reconstructed trailing multiple.
    Each scenario records which percentile of which distribution each input is, so the
    question "why 25.4x?" has an answer that is not "it felt right".

    The published Axis scenarios stay, unchanged, as EXTERNAL COMPARISON — that is now
    the point of them. Their levels are mapped back into the same two units so their
    view and this measurement can be read on one axis.

    NAMING IS LOAD-BEARING HERE. The percentile rows are called REFERENCE, not bull or
    base, because that is what they are: "the multiple the index has actually traded at,
    applied to today's earnings". Calling a median-multiple row a "base case" embeds a
    mean-reversion forecast in a label and then lets it be quoted as a target. Exactly
    one row on this tab is a conditional projection rather than a reference point — the
    recent-run-rate row, which assumes no re-rating at all — and it is the only one whose
    inputs are both an observed rate and an observed multiple.

    A warning that belongs on the face of the output, not in a footnote: today's
    multiple sits near the bottom of its own eight-year range, so EVERY scenario built
    on a percentile of that range implies a re-rating. That is a property of a short
    sample that began expensive and de-rated — not a prediction, and not evidence the
    multiple mean-reverts. conditional_base_rates() is the test of that assumption and
    it cannot run at this sample depth.
    """
    G = earn["growth_dist"]
    Gx = earn["growth_dist_ex_covid"]
    P = pv["dist"]
    Px = pv["dist_ex_covid"]
    pes = pv["_pes"]
    today_pe = pv["today"]
    recent = earn["recent_3y_pct"][-1]

    def m(g, pe, gsrc, pesrc):
        return {"eps_growth_pct": g, "exit_pe": pe,
                "measured": {"g_source": gsrc, "exit_pe_source": pesrc,
                             "exit_pe_percentile": percentile_of(pe, pes)}}

    out = [
        {"id": "ours_recent", "name": "Conditional — recent run-rate, no re-rating",
         "kind": "conditional", "source": "delivery_history + reconstructed P/E",
         **m(recent, today_pe, f"last observed fiscal year (FY{earn['last_fy']})",
             "today's multiple, held"),
         "narrative": ("The most conservative thing the data supports: earnings grow at "
                       f"the rate they actually grew last year ({recent}%) and nobody "
                       "pays a different multiple. Aggregate profit growth has "
                       f"decelerated {earn['recent_3y_pct'][0]}% → "
                       f"{earn['recent_3y_pct'][1]}% → {earn['recent_3y_pct'][2]}%, so "
                       "this is the trend continuing rather than a shock."),
         "invalidated_by": "Aggregate profit growth re-accelerating above ~10% for two consecutive years."},
        {"id": "ours_weak", "name": "Reference — lower-quartile multiple (p25)",
         "kind": "reference", "source": "delivery_history + reconstructed P/E",
         **m(G["p25"], P["p25"], "25th percentile of 8 observed fiscal years",
             "25th percentile of ~2,000 observed days"),
         "narrative": ("Both inputs at their lower quartile. Note this still implies a "
                       "HIGHER multiple than today — the bottom quartile of the last "
                       "eight years is above where the index trades now, which is the "
                       "single most important fact on this tab."),
         "invalidated_by": "The multiple staying below ~22x through the horizon — then the sample, not the market, was wrong."},
        {"id": "ours_central", "name": "Reference — historical median multiple",
         "kind": "reference", "source": "delivery_history + reconstructed P/E",
         **m(G["median"], P["median"], "median of 8 observed fiscal years",
             "median of ~2,000 observed days"),
         "narrative": ("The literal middle of both measured distributions. Read it as "
                       "'what the last eight years would say if they repeated', which "
                       "is a description of 2018-2026, not a forecast of 2026-2028."),
         "invalidated_by": "Either input leaving its measured interquartile range for two quarters."},
        {"id": "ours_central_excovid", "name": "Reference — historical central, COVID removed",
         "kind": "reference", "source": "delivery_history + reconstructed P/E, ex-COVID",
         **m(Gx["median"], Px["median"], "median growth excluding FY21-FY22 base effects",
             "median multiple excluding Jul-2020 → Jun-2021"),
         "narrative": ("The same central case with the two distortions taken out: the "
                       "FY21/FY22 profit rebound off a broken FY20 base, and the "
                       "2020-21 window when the trailing multiple was high because "
                       "earnings had collapsed. The gap between this and the row above "
                       "is the size of the COVID artefact in every other number here."),
         "invalidated_by": "Nothing — this is a robustness variant, not a view."},
        {"id": "ours_strong", "name": "Reference — upper-quartile multiple (p75)",
         "kind": "reference", "source": "delivery_history + reconstructed P/E",
         **m(G["p75"], P["p75"], "75th percentile of 8 observed fiscal years",
             "75th percentile of ~2,000 observed days"),
         "narrative": ("Upper quartile on both. Worth naming what this requires: a "
                       f"multiple of {P['p75']}x, which the index last sustained when "
                       "its earnings base was materially smaller."),
         "invalidated_by": "Aggregate profit growth below the median for two years running."},
    ]

    axis = [
        {"id": "axis_bull", "name": "Axis Securities — bull", "published_level": 28615,
         "eps_growth_pct": 14.0, "exit_pe": 20.5,
         "quoted": ["published_level", "published_for", "exit_pe", "narrative"],
         "assumed": ["eps_growth_pct — Axis published a 12-14% band for its BASE case "
                     "only; 14% here is the top of that band applied to the bull case "
                     "by this file, not by Axis"],
         "narrative": ("Ceasefire holds, Hormuz fully reopens, Brent $70-80, one 25bp Fed "
                       "cut, above-normal monsoon. Rs 1.42-1.89 lakh cr of FPI inflows "
                       "over 3-4 months drives a re-rating."),
         "invalidated_by": "Brent back above $95, or FPI flows still negative 2 months after a reopening."},
        {"id": "axis_base", "name": "Axis Securities — base", "published_level": 27200,
         "eps_growth_pct": 13.0, "exit_pe": 20.5,
         "quoted": ["published_level", "published_for", "exit_pe",
                    "eps_growth_pct — midpoint of the published 12-14% FY27 band",
                    "narrative"],
         "assumed": [],
         "narrative": ("Hormuz reopens, Brent settles $80-90, RBI cuts once in H2, FY27 "
                       "earnings growth 12-14%. Market rotates from macro-led to "
                       "earnings-led."),
         "invalidated_by": "FY27 earnings growth tracking below 10% at the H1 mark."},
        {"id": "axis_bear", "name": "Axis Securities — bear", "published_level": 23030,
         "eps_growth_pct": 6.0, "exit_pe": 17.0,
         "quoted": ["published_level", "published_for", "narrative"],
         "assumed": ["eps_growth_pct — Axis said 'earnings disappoint' without a number; "
                     "6% is this file's reading",
                     "exit_pe — not published; 17.0 is the multiple implied by their "
                     "level under this file's convention, rounded"],
         "narrative": ("Hormuz disruption persists, Brent $110-120+, current account "
                       "deficit past 3.5% of GDP, rupee tests 100. Another Rs 50,000-80,000 "
                       "cr of FPI outflows, partly absorbed by DIIs."),
         "invalidated_by": "Brent sustained below $85 with the rupee inside 96."},
    ]
    # A SECOND PUBLISHED HOUSE, so "the sell side" stops meaning one broker. BofA Global
    # Research, quoted 2026-08-18, raised FY27 index earnings growth to 10% from 8.5% and
    # kept a Dec-2026 base of 26,200 and a bear of 22,000.
    #
    # THE INTERESTING PART IS THAT 26,200 IS NOT "NO RE-RATING". BofA describe the base as
    # "assuming no further expansion in valuations", but on THIS file's EPS (1,210) a 10%
    # year gives 1,331, and holding today's 20.14x would put the index at 26,806. Their
    # 26,200 implies 19.7x — a mild DE-rating. Published narrative and published arithmetic
    # disagree by about 2%, and the convention here is to record the level and show the
    # multiple it implies rather than to adopt the narrative.
    bofa = [
        {"id": "bofa_base", "name": "BofA Global Research — base", "published_level": 26200,
         "eps_growth_pct": 10.0, "exit_pe": 19.7,
         "quoted": ["published_level", "published_for", "eps_growth_pct", "narrative"],
         "assumed": ["exit_pe — NOT published. BofA state 'no further expansion in "
                     "valuations'; 19.7 is what their level implies on this file's index "
                     "EPS after 10% growth, and it is BELOW today's 20.14x"],
         "narrative": ("FY27 estimates raised across sectors covering ~71% of Nifty market "
                       "cap on a resilient June quarter. GST collections, direct tax "
                       "receipts, credit growth and power demand firm through the West Asia "
                       "conflict. 10% FY27 growth, 15% FY28."),
         "invalidated_by": "FY27 growth tracking below 8% at the H1 mark, or the multiple "
                           "re-rating above 21x on unchanged earnings."},
        {"id": "bofa_bear", "name": "BofA Global Research — bear", "published_level": 22000,
         "eps_growth_pct": 0.0, "exit_pe": 18.2,
         "quoted": ["published_level", "published_for", "narrative"],
         "assumed": ["eps_growth_pct — BofA list macro shocks without an earnings number; "
                     "0% is this file's reading, which makes their level a pure multiple "
                     "story",
                     "exit_pe — implied by their level on flat EPS under this file's "
                     "convention. Note 18.2x is still ABOVE the 16.56x minimum in the "
                     "P/E sample, so even their bear case does not set a new low"],
         "narrative": ("Simultaneous higher crude, weaker monsoon, rate hikes, rupee "
                       "depreciation and AI disruption."),
         "invalidated_by": "Any two of those five resolving without the index breaking 23,000."},
    ]
    for x in axis + bofa:
        x.update({"kind": "published", "source": "Axis Securities, quoted 2026-08",
                  "published_for": "2026-12-31",
                  "measured": {
                      "g_source": "external",
                      "exit_pe_source": "external",
                      "exit_pe_percentile": percentile_of(x["exit_pe"], pes),
                      "g_percentile": percentile_of(x["eps_growth_pct"],
                                                    [r["yoy_pct"] for r in earn["growth"]]),
                  }})
    for x in out:
        x.setdefault("quoted", [])
        x.setdefault("assumed", [])
    return out + axis + bofa


def project(a: dict, rates: dict, earn: dict, pv: dict) -> list[dict]:
    eps0, pe0, spot = a["index_eps"], a["trailing_pe"], a["spot"]
    dist = {h["label"]: h["all_returns"] for h in rates["horizons"]}
    out = []
    for s in _scenarios(earn, pv, spot):
        exit_pe = s["exit_pe"] if s["exit_pe"] is not None else pe0
        g = s["eps_growth_pct"] / 100.0
        levels = {}
        for label, _n, yrs in HORIZONS:
            eps_t = eps0 * (1 + g) ** yrs
            lvl = eps_t * exit_pe
            ret = (lvl / spot - 1) * 100
            d = dist.get(label, [])
            pctile = (round(sum(1 for x in d if x < ret) / len(d) * 100, 0) if d else None)
            levels[label] = {
                "level": round(lvl, 0), "ret_pct": round(ret, 1),
                "eps": round(eps_t, 0),
                "annualised_pct": round(((lvl / spot) ** (1 / yrs) - 1) * 100, 1),
                "history_percentile": pctile,
            }
        rec = {**s, "exit_pe_used": round(exit_pe, 2),
               "exit_pe_label": _pe_label(exit_pe), "levels": levels}
        # A published target keeps its own number; report what it implies here.
        if s.get("published_level"):
            pl = s["published_level"]
            rec["published_vs_model"] = {
                "published_level": pl,
                "published_for": s["published_for"],
                "ret_from_spot_pct": round((pl / spot - 1) * 100, 1),
                "implied_exit_pe": {
                    label: round(pl / (eps0 * (1 + g) ** yrs), 2)
                    for label, _n, yrs in HORIZONS
                },
                "note": ("Sell-side targets are quoted as forward-year EPS x target "
                         "multiple irrespective of the date; this model compounds "
                         "trailing EPS to the horizon. implied_exit_pe is the multiple "
                         "their level requires under THIS convention at each horizon — "
                         "where it differs from their stated multiple, the convention "
                         "is the reason, not an error in either."),
            }
        out.append(rec)
    return out


# ---------------------------------------------------------------------------- main

def main() -> int:
    a = anchor()
    rates = base_rates()
    earn = index_earnings()
    pv = pe_series(a["index_eps"])
    cond = conditional_base_rates(pv)
    scen = project(a, rates, earn, pv)
    pv_public = {k: v for k, v in pv.items() if not k.startswith("_")}

    doc = {
        "as_of": datetime.date.today().isoformat(),
        "anchor": a, "history": rates, "scenarios": scen,
        "earnings": earn, "pe": pv_public, "conditional": cond,
        "horizons": [{"label": l, "sessions": n, "years": y} for l, n, y in HORIZONS],
        "model": ("level(T) = index_EPS_today x (1 + g)^T x exit_PE. Two inputs per "
                  "scenario, both stated. No probabilities are assigned here — weights "
                  "belong to the reader and the UI makes them editable."),
        "caveats": [
            "EVERY SCENARIO BUILT ON A P/E PERCENTILE IMPLIES A RE-RATING, because "
            f"today's multiple sits at the {pv['today_percentile']:.0f}th percentile of a "
            "sample that started expensive and de-rated. Three biases push the same way: "
            "constant constituents (today's winners back-cast to 2018 overstate historical "
            "earnings and so understate historical P/E), the COVID denominator (2020-21 "
            "multiples were high because earnings broke), and the untested assumption of "
            "mean reversion itself. The ex-COVID variants are reported beside every "
            "headline figure so the artefact is measurable.",
            "MEAN REVERSION IS ASSUMED, NOT TESTED. conditional_base_rates() is the test "
            "and it cannot run here: the cheap-multiple bucket has one independent 1Y "
            "window and none at 2Y. Cheap-starting-multiple medians look emphatic and "
            "mean nothing at that count.",
            "THIS IS NOT A FORECAST. The repo's own testing found the daily macro→index "
            "regression worthless (R² = 0.036), crude unable to predict next-day weakness "
            "(p = 0.979), and crude beta sign-flipping by regime (+0.006 full sample vs "
            "−0.075 in 2026). Scenario levels are arithmetic conditional on inputs that "
            "nobody has demonstrated the ability to predict.",
            "index_EPS is IMPLIED from spot ÷ weighted trailing P/E across ~98% of index "
            "weight. It is not a published index EPS and inherits every staleness in "
            "Yahoo's per-stock fundamentals.",
            "History is price return only and covers one crash (2020) and one large "
            "drawdown (2026). The left tail of every distribution here is two events.",
            "history_percentile is computed against OVERLAPPING windows. It says where a "
            "scenario sits relative to one realised path, not how likely it is.",
            "The 2Y row is the weakest in the file — see history.horizons[].n_independent. "
            "Treat it as an illustration of compounding, not as evidence about two years.",
            "Published targets are recorded as attributed external opinion with the date "
            "they were quoted. Including one is not endorsing it.",
        ],
        "note": ("Scenario planning, not advice, and not a probability distribution over "
                 "the index. The useful output is the SHAPE: which scenarios need a "
                 "multiple change versus an earnings change, and how far each sits from "
                 "what the index has actually done."),
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1)

    print(f"spot {a['spot']} ({a['spot_date']})  trailing P/E {a['trailing_pe']} "
          f"[{a['pe_label']}]  implied EPS {a['index_eps']:.0f}  "
          f"priced for +{a['implied_growth_pct']}%\n")
    print(f"HISTORY  {rates['source']}  {rates['first']} → {rates['last']}  "
          f"({rates['years']}y, {rates['sessions']} sessions)")
    print(f"{'':6}{'median':>8}{'p10':>8}{'p90':>8}{'worst':>9}{'best':>9}"
          f"{'pos%':>7}{'windows':>9}{'indep':>7}")
    for h in rates["horizons"]:
        print(f"{h['label']:6}{h['median_pct']:8.1f}{h['p10_pct']:8.1f}{h['p90_pct']:8.1f}"
              f"{h['min_pct']:9.1f}{h['max_pct']:9.1f}{h['pct_positive']:7.1f}"
              f"{h['n_windows']:9d}{h['n_independent']:7d}")
    for h in rates["horizons"]:
        c = h.get("conditioned")
        if c:
            print(f"  {h['label']} when starting >5% off the 52w high: median "
                  f"{c['median_pct']:+.1f}%, {c['pct_positive']:.0f}% positive "
                  f"(n={c['n_windows']}, indep {c['n_independent']})")

    print(f"\nSCENARIOS   (level = EPS {a['index_eps']:.0f} x (1+g)^T x exit P/E)")
    print(f"{'scenario':34s}{'g%':>5}{'exitPE':>8}{'6M':>9}{'1Y':>9}{'2Y':>9}   pctile 1Y")
    for s in scen:
        L = s["levels"]
        print(f"{s['name'][:33]:34s}{s['eps_growth_pct']:5.0f}{s['exit_pe_used']:8.2f}"
              f"{L['6M']['level']:9.0f}{L['1Y']['level']:9.0f}{L['2Y']['level']:9.0f}"
              f"{L['1Y']['history_percentile']:>10.0f}")
    for s in scen:
        pv = s.get("published_vs_model")
        if pv:
            print(f"  {s['name']}: published {pv['published_level']} for "
                  f"{pv['published_for']} ({pv['ret_from_spot_pct']:+.1f}%) implies exit "
                  f"P/E {pv['implied_exit_pe']['6M']} at 6M / "
                  f"{pv['implied_exit_pe']['1Y']} at 1Y")

    if not rates.get("extended"):
        print("\nHistory is 2018→ only. To extend it (run on a machine with Yahoo access):")
        print("  python3 -c \"import yfinance as yf;d=yf.download('^NSEI',start='1990-01-01',"
              "progress=False,auto_adjust=False)['Close'];d.to_csv('nifty_history_long.csv',"
              "header=['close'],index_label='date')\"")
        print("  then re-run this script — it prefers nifty_history_long.csv automatically.")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
