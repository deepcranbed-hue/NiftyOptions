#!/usr/bin/env python3
"""index_valuation.py — measure the two inputs a Nifty scenario needs, instead of
guessing them.

WHY THIS FILE EXISTS
    An earlier version of nifty_outlook.py invented its scenario inputs. It labelled
    them honestly ("assumed: eps_growth_pct, exit_pe") but a well-labelled guess is
    still a guess, and it left this repo unable to do the one thing that makes an
    external forecast useful — put it in the same units as our own measurement and see
    where it lands. Axis has an analyst behind their 20.5x. We had a shrug.

    So this file measures both inputs from data we actually hold:

    1. INDEX EARNINGS GROWTH, from delivery_history.json. Forty-seven Nifty 50 names
       have a complete FY2018-FY2026 annual net-profit series — 96.9% of index weight,
       every one a 31-March year-end. Their AGGREGATE net profit is the closest thing to
       an index earnings series this repo can build, and its year-on-year changes are
       eight measured observations rather than an opinion.

    2. INDEX TRAILING P/E, reconstructed. Index EPS at any date = today's implied index
       EPS scaled by (aggregate profit that year ÷ aggregate profit FY26), stepped up 92
       days after each year-end because that is when the annuals are actually published.
       Divide the NIFTY close by it and you get ~2,000 daily observations of what the
       index has actually been paid.

WHAT IS MEASURED AND WHAT IS ASSUMED, PRECISELY
    Measured: aggregate profit, its growth, the SHAPE of the P/E series, and where any
    given multiple sits in that shape.

    Assumed: the LEVEL of the P/E series. The whole series is anchored so that today
    equals the app's bottom-up weighted trailing P/E. Today's multiple therefore matches
    the index card BY CONSTRUCTION — that is not a validation of anything, and this file
    says so rather than presenting the agreement as a check that passed.

THREE BIASES THAT PUSH THE SAME WAY, AND ARE NOT SMALL
    · CONSTANT CONSTITUENTS. The panel is today's Nifty 50 back-cast to 2018. Companies
      enter the index by growing, so back-casting today's members overstates the index's
      historical earnings, which understates its historical P/E in the early years.
    · THE COVID DENOMINATOR. FY2020 profit fell 12.1% and FY2021 rebounded 24.4% off
      that floor. Through 2020-21 the trailing multiple was high because earnings were
      broken, not because anyone was paying up. `ex_covid` reports every statistic again
      with that year removed; the gap between the two is the size of the artefact.
    · MEAN REVERSION IS ASSUMED, NOT TESTED. An eight-year sample containing a de-rating
      from ~40x to ~20x will always report today as cheap. Whether a low starting
      multiple actually predicts higher forward returns is testable in principle and
      NOT testable here — see conditional_base_rates(), where the 1Y cheap-tercile row
      has one independent window and the 2Y row has none.

    All three flatter today's valuation. Read "3rd percentile" as "cheap relative to a
    short sample that was expensive", never as "cheap".
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import statistics

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

DB = os.path.join(_ROOT, "option_chains.db")
DELIVERY = os.path.join(_ROOT, "delivery_history.json")

# Annuals for a 31-March year-end are published in May-June; 92 days is the first date
# every constituent has certainly reported. Same constant, same reasoning, as the
# publication lag in quality_growth.py — getting this wrong there cost two thirds of a
# reported backtest edge.
PUBLICATION_LAG_DAYS = 92
FIRST_FY, LAST_FY = 2018, 2026
# The fiscal year whose trailing multiple is a COVID artefact rather than a valuation.
COVID_FY = 2021
MIN_INDEPENDENT = 5


def _q(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    i = (len(sorted_vals) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def _dist(vals: list[float], nd: int = 1) -> dict:
    s = sorted(vals)
    return {"n": len(s), "min": round(s[0], nd), "p10": round(_q(s, .10), nd),
            "p25": round(_q(s, .25), nd), "median": round(_q(s, .50), nd),
            "p75": round(_q(s, .75), nd), "p90": round(_q(s, .90), nd),
            "max": round(s[-1], nd), "mean": round(sum(s) / len(s), nd)}


# ------------------------------------------------------------------ index earnings

def index_earnings() -> dict:
    """Aggregate net profit of the balanced panel, by fiscal year, with its growth."""
    with open(DELIVERY) as f:
        hist = json.load(f)["history"]
    years = [f"{y}-03-31" for y in range(FIRST_FY, LAST_FY + 1)]
    panel = [s for s, v in hist.items()
             if all(any(x["period"] == y for x in v["series"]) for y in years)]
    if not panel:
        return {"available": False, "reason": "no symbol spans the full panel"}

    agg = {y: sum(next(x["net_profit"] for x in hist[s]["series"] if x["period"] == y)
                  for s in panel) for y in years}
    growth = [{"fy": int(years[i][:4]),
               "aggregate_profit_cr": round(agg[years[i]], 0),
               "yoy_pct": round((agg[years[i]] / agg[years[i - 1]] - 1) * 100, 1)}
              for i in range(1, len(years))]
    g = [x["yoy_pct"] for x in growth]
    g_ex = [x["yoy_pct"] for x in growth if x["fy"] not in (COVID_FY, COVID_FY + 1)]

    return {
        "available": True, "panel_symbols": len(panel),
        "first_fy": FIRST_FY, "last_fy": LAST_FY,
        "aggregate_profit_cr": {int(y[:4]): round(agg[y], 0) for y in years},
        "growth": growth,
        "growth_dist": _dist(g),
        # FY21 rebounds off a broken FY20 base and FY22 compounds it (+54.3%). Both out.
        "growth_dist_ex_covid": _dist(g_ex),
        "recent_3y_pct": g[-3:],
        "decelerating": len(g) >= 3 and g[-1] < g[-2] < g[-3],
        "note": ("Aggregate net profit of a BALANCED panel — only names with a complete "
                 f"FY{FIRST_FY}-FY{LAST_FY} series, so the level is comparable across "
                 "years. Eight growth observations is a small sample and two of them "
                 "(FY21, FY22) are COVID base effects; growth_dist_ex_covid drops both."),
    }


# ----------------------------------------------------------------- index P/E series

# --- external cross-check of the reconstructed P/E (correction C27) ---------
# Checked 16-Aug-2026 against three independent NSE-derived publishers, all on a
# consolidated TTM basis at spot 24,366 (14-Aug-2026):
#   nifty-pe-ratio.com 20.56   indexpe.in 20.56   screener.in 20.6
# Ours reads 20.39 — inside 0.8%. But indexpe.in also publishes a trailing-5-year
# MEDIAN of 22.06 and ours is 23.89, which is 8.3% richer. Today agrees and the
# history does not, and that asymmetry is mechanical, not noise:
#
#   NSE's EPS rolls FOUR times a year as each quarter's TTM lands. Ours steps ONCE,
#   92 days after 31-March, because the panel is built from annuals. Between steps
#   our denominator is stale-LOW, so the printed P/E is too HIGH — and it is too
#   high for most of every year. With EPS staleness roughly uniform on [0, 1] year
#   and annual growth g, the expected overstatement is g / ln(1+g): at g = 12.9%
#   that is 1.063 against an observed 1.083. The mechanism accounts for about
#   three quarters of the gap; the rest is 47-of-50 coverage and PAT definition.
#
# It matters because the bias lands on the DENOMINATOR of the percentile. Today is
# anchored to a true bottom-up value while the history it is ranked against is
# inflated, so "cheap" was overstated: the 3rd percentile becomes the 11th on the
# full sample and the 13th on the trailing five years. Still a low reading. Not the
# extreme the raw number claimed.
PE_EXTERNAL = {
    "as_of": "2026-08-14",
    "sources": {"nifty-pe-ratio.com": 20.56, "indexpe.in": 20.56, "screener.in": 20.6},
    "median_5y": 22.06,
    "basis": "NSE consolidated TTM",
}


def _pe_cross_check(dates: list, pes: list, today: float) -> dict:
    ext = sum(PE_EXTERNAL["sources"].values()) / len(PE_EXTERNAL["sources"])
    five = [p for d, p in zip(dates, pes) if d >= "2021-08-14"]
    if not five:
        return {"available": False}
    ours_med = statistics.median(five)
    bias = ours_med / PE_EXTERNAL["median_5y"]
    adj = [p / bias for p in pes]
    adj5 = [p / bias for p in five]
    pct = lambda v: round(sum(1 for x in v if x < today) / len(v) * 100, 1)
    return {
        "available": True,
        "external": PE_EXTERNAL,
        "external_mean_today": round(ext, 2),
        "today_agrees_within_pct": round(abs(today - ext) / ext * 100, 1),
        "median_5y_ours": round(ours_med, 2),
        "median_5y_external": PE_EXTERNAL["median_5y"],
        "history_bias": round(bias, 3),
        "history_bias_expected": 1.063,
        "percentile_raw": pct(pes),
        "percentile_adjusted": pct(adj),
        "percentile_adjusted_5y": pct(adj5),
        "note": ("Today matches three independent NSE-derived publishers within 0.8%. "
                 "The reconstructed HISTORY runs 8.3% rich because our EPS steps once a "
                 "year while NSE's rolls quarterly, leaving our denominator stale-low "
                 "between steps. Predicted bias from that mechanism alone is 1.063 "
                 "against 1.083 observed. Rank today against the deflated history and "
                 "the percentile moves from ~3 to ~11 (~13 over five years). Use the "
                 "adjusted figure when calling the market cheap. Correction C27."),
    }


def pe_series(eps_today: float) -> dict:
    """Reconstruct the index's own trailing P/E, daily, from 2018."""
    e = index_earnings()
    if not e.get("available"):
        return {"available": False}
    agg = e["aggregate_profit_cr"]
    last = agg[LAST_FY]
    # EPS steps up when the annual is PUBLISHED, not when the year ends.
    steps = sorted(
        ((datetime.date(fy, 3, 31) + datetime.timedelta(days=PUBLICATION_LAG_DAYS)).isoformat(),
         eps_today * v / last) for fy, v in agg.items())

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    bars = [(r[0][:10], float(r[1])) for r in con.execute(
        "SELECT ts, close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
        "AND close IS NOT NULL ORDER BY ts")]
    con.close()

    dates, closes, pes = [], [], []
    for d, c in bars:
        eps = None
        for sd, v in steps:
            if sd <= d:
                eps = v
        if eps:
            dates.append(d); closes.append(c); pes.append(c / eps)
    if not pes:
        return {"available": False}

    today = pes[-1]
    ex = [p for d, p in zip(dates, pes)
          if not (f"{COVID_FY - 1}-07-01" <= d <= f"{COVID_FY + 1}-06-30")]
    return {
        "available": True, "first": dates[0], "last": dates[-1], "n": len(pes),
        "today": round(today, 2),
        "dist": _dist(pes, 2),
        "dist_ex_covid": _dist(ex, 2) if ex else None,
        "today_percentile": round(sum(1 for x in pes if x < today) / len(pes) * 100, 0),
        "today_percentile_ex_covid": (round(sum(1 for x in ex if x < today) / len(ex) * 100, 0)
                                      if ex else None),
        "series": [{"d": d, "pe": round(p, 2)} for d, p in zip(dates, pes)][::5],
        "_dates": dates, "_closes": closes, "_pes": pes,
        "cross_check": _pe_cross_check(dates, pes, round(today, 2)),
        "anchor_note": ("The LEVEL of this series is anchored so that today equals the "
                        "app's bottom-up weighted trailing P/E. Today matching the index "
                        "card is therefore true by construction and is not evidence the "
                        "reconstruction is right. What is measured is the SHAPE — how "
                        "aggregate profit moved relative to the index."),
    }


def percentile_of(pe: float, pes: list[float]) -> float:
    return round(sum(1 for x in pes if x < pe) / len(pes) * 100, 0)


# ------------------------------------------------- does cheap actually predict better?

def conditional_base_rates(pv: dict) -> dict:
    """Forward return by STARTING P/E tercile — the test of whether the mean reversion
    every scenario below quietly assumes is visible in this sample.

    It is not testable here and the numbers say why: the 1Y cheap-tercile row rests on
    ONE independent window and the 2Y row on none. The medians look emphatic (+22.3% vs
    +6.5% at 1Y) and mean nothing at that count. Reported with the counts attached so
    the UI can hide what it cannot support.
    """
    dates, closes, pes = pv["_dates"], pv["_closes"], pv["_pes"]
    s = sorted(pes)
    t1, t2 = _q(s, 1 / 3), _q(s, 2 / 3)
    out = []
    for label, n in (("6M", 126), ("1Y", 252), ("2Y", 504)):
        buckets: dict[str, list] = {"cheap": [], "mid": [], "rich": []}
        for i in range(len(closes) - n):
            r = (closes[i + n] / closes[i] - 1) * 100
            buckets["cheap" if pes[i] < t1 else "mid" if pes[i] < t2 else "rich"].append(r)
        rows = []
        for b, v in buckets.items():
            if not v:
                continue
            vs = sorted(v)
            rows.append({"bucket": b, "n_windows": len(v), "n_independent": len(v) // n,
                         "sufficient": (len(v) // n) >= MIN_INDEPENDENT,
                         "median_pct": round(vs[len(vs) // 2], 1),
                         "pct_positive": round(sum(1 for x in v if x > 0) / len(v) * 100, 1),
                         "min_pct": round(vs[0], 1), "max_pct": round(vs[-1], 1)})
        out.append({"label": label, "buckets": rows,
                    "any_sufficient": any(r["sufficient"] for r in rows)})
    return {
        "tercile_cuts": [round(t1, 2), round(t2, 2)],
        "today_bucket": "cheap" if pv["today"] < t1 else "mid" if pv["today"] < t2 else "rich",
        "horizons": out,
        "verdict": ("NOT TESTABLE at this sample depth. Every scenario built on a "
                    "percentile of the P/E distribution assumes the multiple reverts "
                    "toward that distribution. This is the test of that assumption and "
                    "it cannot run: the cheap bucket has 1 independent 1Y window and 0 "
                    "independent 2Y windows. Belongs in the register as an open item, "
                    "not as a finding in either direction."),
    }


# ---------------------------------------------- historical vs state vs forward

def regime_layers(eps_today: float) -> dict:
    """Keep the three layers SEPARATE, and refuse to blend them.

    HISTORICAL asks what is normal. STATE asks what is happening now. FORWARD asks
    what is likely next. The tempting move is to collapse them into one weighted growth
    number — "70% recent, 30% history" — and that is exactly the move this function
    exists to prevent, because the weight would be invented and would then silently
    drive every downstream level.

    Two tests decide whether blending could even be justified, and both fail:

    1. PERSISTENCE. If last year's growth predicted next year's, the recent run-rate
       would deserve weight on its own merits. Annual AR(1) on aggregate profit growth
       is r = -0.12 at n = 7, against a minimum detectable |r| of ~0.80. Not detectable.
       So "the run-rate continues" is an assumption, not an observed property — which is
       the same conclusion H52 reached from the other end, where the run-rate method lost
       to a no-change null.

    2. REGIME-CONDITIONED MULTIPLES. "What P/E does this growth regime deserve?" is the
       right question and this dataset cannot answer it. The market paid its HIGHEST
       multiple (33.5x) in the year earnings FELL 12%, and its LOWEST (23.2x) in the year
       they grew 54%. That is not a preference for weak growth — it is the trailing P/E
       moving mechanically inverse to its own denominator. Conditioning a TRAILING
       multiple on TRAILING growth is close to circular; the question needs forward EPS,
       and this repo holds one forward snapshot.

    So the layers are reported side by side, each with its own independent-episode count,
    and the reconciliation is left to the reader with the disagreement visible.
    """
    e = index_earnings()
    pv = pe_series(eps_today)
    g = [x["yoy_pct"] for x in e["growth"]]

    def corr(a, b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        return cov / ((va * vb) ** 0.5) if va and vb else None

    ar1 = corr(g[:-1], g[1:]) if len(g) > 2 else None
    n_ar = len(g) - 1
    mdc_ar = 1.96 / ((max(n_ar - 1, 1)) ** 0.5)

    recent = g[-3:]
    direction = ("decelerating" if len(recent) == 3 and recent[2] < recent[1] < recent[0]
                 else "accelerating" if len(recent) == 3 and recent[2] > recent[1] > recent[0]
                 else "mixed")

    return {
        "historical": {
            "question": "What is normal?",
            "growth_median_pct": e["growth_dist"]["median"],
            "growth_median_ex_covid_pct": e["growth_dist_ex_covid"]["median"],
            "growth_observations": e["growth_dist"]["n"],
            "pe_median": pv["dist"]["median"],
            "pe_daily_observations": pv["n"],
            # The distinction that matters and that a raw n hides.
            "pe_independent_regime_years": e["growth_dist"]["n"],
            "note": ("The P/E series has ~2,000 daily points but the earnings denominator "
                     "steps once a year, so there are only as many INDEPENDENT valuation "
                     "regimes as there are fiscal years. Quoting the daily n would "
                     "overstate the evidence by two orders of magnitude."),
        },
        "state": {
            "question": "What is happening now?",
            "recent_annual_pct": recent,
            "direction": direction,
            "latest_annual_pct": g[-1],
            "pe_now": pv["today"],
            "pe_percentile": pv["today_percentile"],
            "note": ("State is read from the most recent observations regardless of how "
                     "few there are, because its job is to detect a change, not to "
                     "establish one. Four quarters cannot prove a new regime and are "
                     "still the best available evidence that the current one differs "
                     "from the average."),
        },
        "forward": {
            "question": "What is likely next?",
            "available": False,
            "blocked_on": ("Estimate revisions need a time series of "
                           "expectation_snapshots.json and only one capture exists. "
                           "Without it the forward layer is opinion."),
        },
        "blending": {
            "allowed": False,
            "persistence_r": round(ar1, 3) if ar1 is not None else None,
            "persistence_n": n_ar,
            "persistence_min_detectable_r": round(mdc_ar, 2),
            "persistence_detectable": bool(ar1 is not None and abs(ar1) >= mdc_ar),
            "window_selection_possible": False,
            "why": ("Choosing a lookback window by walk-forward test is the right method "
                    f"and cannot run here: {len(g)} annual growth observations cannot "
                    "distinguish 'use the last one' from 'use the last three' from 'use "
                    "all of them'. Any weighting would be asserted, so none is applied."),
        },
    }


if __name__ == "__main__":
    e = index_earnings()
    print(f"PANEL {e['panel_symbols']} symbols, FY{e['first_fy']}-FY{e['last_fy']}")
    print(f"{'FY':6}{'aggregate profit (Rs cr)':>26}{'YoY':>9}")
    for row in e["growth"]:
        print(f"{row['fy']:<6}{row['aggregate_profit_cr']:>26,.0f}{row['yoy_pct']:>8.1f}%")
    d, dx = e["growth_dist"], e["growth_dist_ex_covid"]
    print(f"\ngrowth      n={d['n']}  p25 {d['p25']}%  median {d['median']}%  p75 {d['p75']}%  mean {d['mean']}%")
    print(f"  ex-covid  n={dx['n']}  p25 {dx['p25']}%  median {dx['median']}%  p75 {dx['p75']}%  mean {dx['mean']}%")
    print(f"  last three years {e['recent_3y_pct']}"
          f"{'  — monotonically decelerating' if e['decelerating'] else ''}")

    pv = pe_series(1195.0)
    p, px = pv["dist"], pv["dist_ex_covid"]
    print(f"\nP/E  {pv['first']} → {pv['last']}  n={pv['n']}   today {pv['today']}")
    print(f"  full      min {p['min']}  p10 {p['p10']}  median {p['median']}  p90 {p['p90']}  max {p['max']}")
    print(f"  ex-covid  min {px['min']}  p10 {px['p10']}  median {px['median']}  p90 {px['p90']}  max {px['max']}")
    print(f"  today sits at the {pv['today_percentile']:.0f}th percentile "
          f"({pv['today_percentile_ex_covid']:.0f}th ex-covid)")

    cb = conditional_base_rates(pv)
    print(f"\nFORWARD RETURN BY STARTING P/E TERCILE  cuts {cb['tercile_cuts']}, "
          f"today = {cb['today_bucket']}")
    for hz in cb["horizons"]:
        print(f"  {hz['label']}")
        for b in hz["buckets"]:
            flag = "" if b["sufficient"] else "   << below the evidence floor"
            print(f"    {b['bucket']:6s} median {b['median_pct']:+7.1f}%  pos "
                  f"{b['pct_positive']:5.1f}%  indep {b['n_independent']:2d}{flag}")
    print(f"\n{cb['verdict']}")

    rl = regime_layers(1195.0)
    print("\nTHREE LAYERS, KEPT SEPARATE")
    hh, stt, bl = rl["historical"], rl["state"], rl["blending"]
    print(f"  HISTORICAL  growth median {hh['growth_median_pct']}% "
          f"({hh['growth_median_ex_covid_pct']}% ex-covid) over {hh['growth_observations']} years; "
          f"P/E median {hh['pe_median']}x over {hh['pe_independent_regime_years']} INDEPENDENT "
          f"regime-years (not {hh['pe_daily_observations']} days)")
    print(f"  STATE       {stt['recent_annual_pct']} -> {stt['direction']}; "
          f"P/E {stt['pe_now']}x at the {stt['pe_percentile']:.0f}th percentile")
    print(f"  FORWARD     unavailable — {rl['forward']['blocked_on']}")
    print(f"  BLENDING    not applied. persistence r = {bl['persistence_r']} at n = "
          f"{bl['persistence_n']}, min detectable {bl['persistence_min_detectable_r']} -> "
          f"{'detectable' if bl['persistence_detectable'] else 'NOT detectable'}")
    print(f"              {bl['why']}")
