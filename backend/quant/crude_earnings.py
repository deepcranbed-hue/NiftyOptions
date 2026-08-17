#!/usr/bin/env python3
"""crude_earnings.py — is crude actually the biggest shock to Nifty EARNINGS?

WHY ASK IT THIS WAY
    This repo has already killed crude → index PRICE several times over: high-and-rising
    crude fails to predict next-day weakness at p = 0.979 (H11), the beta flips sign by
    regime (H12), and the daily macro→index regression was retired at R-squared 0.036.
    Those are all tests of the FAST channel.

    Crude → EARNINGS is a different, slower channel, and it is the one that matters now
    that the walk-forward test (H52/H53) has shown the multiple does not do the work.
    Nobody in this repo had tested it. This file does.

WHAT THIS FILE DOES **NOT** SHOW
    It does not show that the Nifty is "hedged against crude". It shows something
    narrower and the distinction matters: the index's PROFIT MIX partially offsets the
    DIRECT earnings damage from higher crude, because upstream energy and commodity
    profits move the other way.

    Three things are outside this measurement entirely, and any of them can be larger
    than the direct effect:
      · MACRO/POLICY — import bill, current account, INR, inflation, RBI room, bond
        yields, risk premium. This hits the multiple, not the earnings, and the multiple
        is where H52/H53 say the damage actually shows up.
      · DEMAND — higher fuel diverts household spending away from discretionary
        categories. It reaches earnings, but with a lag and through volumes, not input
        costs, so it is invisible in an input-cost framing.
      · SECOND ROUND — financials are 33.9% of profit and have no direct crude line at
        all; their exposure runs through credit costs and growth.

    PROFIT SHARE IS NOT A CRUDE BETA. This file weights cohorts by what they earn, not
    by how sensitive their earnings are to crude, because eight annual observations
    cannot estimate a per-cohort earnings elasticity. Reliance in particular is a
    refiner plus petrochemicals plus retail plus telecom whose refining margin can WIDEN
    when crude falls — grouping it as a crude long is a simplification the sensitivity
    grid inherits.

THE ANSWER IS ABOUT COMPOSITION, NOT ELASTICITY
    The Nifty is not a crude consumer. It is a portfolio containing large crude and
    commodity PRODUCERS alongside the consumers, and the two legs partly cancel at index
    level. Measuring one aggregate elasticity would average them into a number that
    describes neither, so this file decomposes the panel by what a commodity move
    actually does to each cohort, weighted by PROFIT SHARE rather than index weight —
    because it is earnings being shocked, not price.

WHAT IS MEASURED AND WHAT IS ARITHMETIC
    MEASURED: aggregate profit growth by cohort, annually and quarterly; the correlation
    between fiscal-year crude change and aggregate profit growth, with the minimum
    detectable correlation printed beside it so a large-but-unusable point estimate
    cannot be read as a finding.

    ARITHMETIC: the sensitivity grid. "If the consumer cohort loses X points of growth
    and energy gains Y, the index earnings growth moves by share-weighted Z" is an
    identity given X and Y, not an estimate of X and Y. It is labelled as such. This file
    does not claim to know the elasticity, and with eight annual observations it cannot.

THE FINDING THAT MATTERS MOST IS NOT ABOUT CRUDE
    Once the panel is split, the index's earnings slowdown turns out to sit almost
    entirely in the non-financial, non-commodity cohort — the largest single block of
    index profit — while commodities mask it. That cohort is contracting while crude is
    CHEAP, which means the current earnings problem is not a crude problem. Crude is a
    risk to it, not the cause of it.
"""
from __future__ import annotations

import csv
import datetime
import json
import math
import os
import sqlite3
import statistics as st
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

DB = os.path.join(_ROOT, "option_chains.db")
DELIVERY = os.path.join(_ROOT, "delivery_history.json")
UNIVERSE = os.path.join(_ROOT, "nifty-50-stock-list.csv")
OUT = os.path.join(_ROOT, "crude_earnings.json")

FIRST_FY, LAST_FY = 2018, 2026
# Cohorts by what a commodity move does to the P&L, not by GICS tidiness. Energy and
# metals are separated because they are different cycles that happen to share a sign.
COHORT = {"Oil & Gas": "energy", "Metals & Mining": "metals",
          "Financial Services": "financials"}
DEFAULT_COHORT = "other non-financial"


def _cohort(sector: str) -> str:
    return COHORT.get(sector, DEFAULT_COHORT)


def _mdc(n: int) -> float:
    """Smallest |r| distinguishable from zero at n observations, alpha .05, two-sided.
    Printed beside every correlation so an impressive-looking r on a tiny n cannot be
    quoted as a result — the failure mode that produced corrections C1 and C14."""
    return 1.96 / math.sqrt(max(n - 1, 1))


def build() -> dict:
    with open(DELIVERY) as f:
        hist = json.load(f)["history"]
    with open(UNIVERSE) as f:
        rows = list(csv.DictReader(f))
    sector = {r["Symbol"]: r["Sector"] for r in rows}

    years = [f"{y}-03-31" for y in range(FIRST_FY, LAST_FY + 1)]
    panel = [s for s, v in hist.items()
             if all(any(x["period"] == y for x in v["series"]) for y in years)]

    def pat(s, y):
        return next(x["net_profit"] for x in hist[s]["series"] if x["period"] == y)

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    crude = dict(con.execute("SELECT substr(ts,1,10), close FROM price_bars "
                             "WHERE symbol='CRUDEOIL' AND timeframe='1d'"))
    con.close()

    def fy_crude(fy: int):
        v = [c for d, c in crude.items() if f"{fy - 1}-04-01" <= d <= f"{fy}-03-31"]
        return round(sum(v) / len(v), 1) if v else None

    total = {y: sum(pat(s, y) for s in panel) for y in years}
    annual = []
    for i in range(1, len(years)):
        fy = int(years[i][:4])
        a, b = fy_crude(fy), fy_crude(fy - 1)
        annual.append({
            "fy": fy, "avg_wti": a,
            "crude_change_pct": round((a / b - 1) * 100, 1) if (a and b) else None,
            "profit_growth_pct": round((total[years[i]] / total[years[i - 1]] - 1) * 100, 1),
        })

    pairs = [(r["crude_change_pct"], r["profit_growth_pct"]) for r in annual
             if r["crude_change_pct"] is not None]
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = st.mean(xs), st.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    r = cov / math.sqrt(vx * vy) if vx and vy else None
    mdc = _mdc(len(xs))

    # ---- cohorts, weighted by PROFIT share ----------------------------------
    groups = defaultdict(list)
    for s in panel:
        groups[_cohort(sector.get(s, ""))].append(s)

    qperiods = sorted({q["period"] for v in hist.values() for q in v.get("quarters", [])})

    def qpat(s, p):
        return next((q["net_profit"] for q in hist[s].get("quarters", [])
                     if q["period"] == p), None)

    cohorts = []
    for name, names in groups.items():
        tot = [sum(pat(s, y) for s in names) for y in years]
        growth = [round((tot[i] / tot[i - 1] - 1) * 100, 1) for i in range(1, len(tot))]
        # Quarterly YoY on ONE FIXED member set for the whole path. Recomputing the
        # eligible members quarter by quarter lets coverage change underneath the series,
        # so a cohort growing because a large member started reporting reads as a
        # cohort growing. The member set is the intersection across every quarter shown
        # and its year-ago base; a quarter that would shrink the set is dropped instead.
        want = qperiods[-5:]
        def eligible(ps):
            return [s for s in names
                    if all(qpat(s, p) is not None
                           and qpat(s, f"{int(p[:4]) - 1}{p[4:]}") is not None for p in ps)]
        members = eligible(want)
        while want and len(members) < max(2, len(names) // 2):
            want = want[:-1] if len(want) > 1 else []
            members = eligible(want) if want else []
        qpath = []
        for p in want:
            base = f"{int(p[:4]) - 1}{p[4:]}"
            c = sum(qpat(s, p) for s in members)
            b = sum(qpat(s, base) for s in members)
            qpath.append({"period": p, "yoy_pct": round((c / b - 1) * 100, 1),
                          "names": len(members)})
        cohorts.append({
            "cohort": name, "n": len(names),
            "profit_share_first_pct": round(tot[0] / total[years[0]] * 100, 1),
            "profit_share_latest_pct": round(tot[-1] / total[years[-1]] * 100, 1),
            "annual_growth_pct": growth,
            "latest_fy_growth_pct": growth[-1],
            "quarterly_yoy": qpath,
            "latest_quarter_yoy_pct": qpath[-1]["yoy_pct"] if qpath else None,
            "crude_sign": ("gains on higher crude" if name in ("energy", "metals")
                           else "loses on higher crude" if name == DEFAULT_COHORT
                           else "mixed / second-order"),
            "members": sorted(names),
        })
    cohorts.sort(key=lambda c: -c["profit_share_latest_pct"])

    # ---- CONTRIBUTION, not share -------------------------------------------
    # Share says who earns the money. Contribution says who MOVED the index number:
    #     contribution_pp = (profit_t - profit_{t-1}) / total_profit_{t-1} x 100
    # and the four cohort contributions sum exactly to aggregate growth. This is the
    # decomposition that makes "can financials carry it?" a question with an answer,
    # because it prices each cohort's growth by the size of the base it grows from.
    def contributions(get, periods, base_of):
        out = []
        for p in periods:
            b = base_of(p)
            tot_b = sum(get(s, b) for s in panel_for(get, p, b))
            if not tot_b:
                continue
            row = {"period": p, "total_pp": 0.0}
            for name, names in groups.items():
                mem = [s for s in names if s in panel_for(get, p, b)]
                cur = sum(get(s, p) for s in mem)
                pri = sum(get(s, b) for s in mem)
                c = round((cur - pri) / tot_b * 100, 1)
                row[name] = c
                row["total_pp"] = round(row["total_pp"] + c, 1)
            out.append(row)
        return out

    def panel_for(get, p, b):
        return [s for s in panel if get(s, p) is not None and get(s, b) is not None]

    annual_contrib = contributions(
        lambda s, y: pat(s, y) if any(x["period"] == y for x in hist[s]["series"]) else None,
        years[1:], lambda y: f"{int(y[:4]) - 1}-03-31")

    # Quarterly, on the fixed panel that has every quarter and every year-ago base.
    qwant = qperiods[-5:]
    qpanel = [s for s in panel
              if all(qpat(s, p) is not None
                     and qpat(s, f"{int(p[:4]) - 1}{p[4:]}") is not None for p in qwant)]
    quarterly_contrib = []
    for p in qwant:
        b = f"{int(p[:4]) - 1}{p[4:]}"
        tot_b = sum(qpat(s, b) for s in qpanel)
        row = {"period": p, "panel": len(qpanel), "total_pp": 0.0}
        for name, names in groups.items():
            mem = [s for s in names if s in qpanel]
            c = round((sum(qpat(s, p) for s in mem) - sum(qpat(s, b) for s in mem))
                      / tot_b * 100, 1) if mem else 0.0
            row[name] = c
            row["total_pp"] = round(row["total_pp"] + c, 1)
        quarterly_contrib.append(row)

    # What each cohort must deliver for the index to reach a target growth rate.
    share_now = {n: sum(pat(s, years[-1]) for s in ns) / total[years[-1]]
                 for n, ns in groups.items()}
    latest_q = quarterly_contrib[-1] if quarterly_contrib else {}
    required = []
    for target in (10.0, 13.0):
        delivered = sum(latest_q.get(n, 0.0) for n in groups)
        required.append({
            "target_index_growth_pct": target,
            "current_run_rate_pp": round(delivered, 1),
            "shortfall_pp": round(target - delivered, 1),
            "if_financials_alone_pct": (round((target - (delivered - latest_q.get("financials", 0)))
                                              / share_now.get("financials", 1) , 1)
                                        if share_now.get("financials") else None),
            "note": ("if_financials_alone is the growth rate financials would need if every "
                     "other cohort simply repeated its latest quarter. It is a bound, not a "
                     "forecast, and it is deliberately unflattering."),
        })

    # ---- sensitivity grid: ARITHMETIC, not an estimate -----------------------
    share = {c["cohort"]: c["profit_share_latest_pct"] / 100 for c in cohorts}
    grid = []
    for hit in (5, 10, 15):
        for gain in (10, 20, 30):
            net = (-hit * share.get(DEFAULT_COHORT, 0)
                   + gain * (share.get("energy", 0) + share.get("metals", 0)))
            grid.append({"consumer_cohort_hit_pp": hit, "commodity_cohort_gain_pp": gain,
                         "index_earnings_growth_delta_pp": round(net, 1)})

    return {
        "as_of": datetime.date.today().isoformat(),
        "panel_names": len(panel), "first_fy": FIRST_FY, "last_fy": LAST_FY,
        "annual": annual,
        "correlation": {
            "r": round(r, 3) if r is not None else None, "n": len(xs),
            "min_detectable_r": round(mdc, 2),
            "detectable": bool(r is not None and abs(r) >= mdc),
            "note": ("DIRECT CHANNEL ONLY. Fiscal-year average WTI change against aggregate panel profit "
                     "growth. The POSITIVE sign is not a mistake: roughly a quarter of "
                     "index profit comes from crude and commodity producers whose "
                     "earnings rise with the price. With n=8 the point estimate sits "
                     "essentially AT the detection threshold, so it establishes nothing "
                     "in either direction — it only rules out the assumption that the "
                     "index-level relationship is obviously negative."),
        },
        "cohorts": cohorts,
        "annual_contribution_pp": annual_contrib,
        "quarterly_contribution_pp": quarterly_contrib,
        "required_to_hit_target": required,
        "contribution_note": ("Cohort contributions sum EXACTLY to aggregate growth by construction. Profit share tells you who earns; contribution tells you who moved the number."),
        "sensitivity_grid": grid,
        "sensitivity_note": ("DIRECT EARNINGS CHANNEL ONLY, AND ARITHMETIC, NOT AN ESTIMATE. This is not the total Nifty impact of crude — the macro/FX/policy and demand channels are not measured here and either can exceed it. Each row says: if the "
                             "non-financial non-commodity cohort loses X points of "
                             "earnings growth and the commodity cohorts gain Y, the "
                             "profit-share-weighted effect on INDEX earnings growth is Z. "
                             "X and Y are inputs. This file does not know them and eight "
                             "annual observations cannot supply them."),
        "caveats": [
            "Profit share, not index weight. It is earnings being shocked, so earnings "
            "are the right weights — but they move a lot year to year, and a commodity "
            "cohort's share is highest exactly when its earnings peak.",
            "'Energy' here is ONGC and Reliance. Reliance is a refiner plus retail plus "
            "telecom, and a refiner's margin can widen when crude FALLS. Treating it as a "
            "simple crude long is wrong, and it is 9.2% of the index.",
            "Metals track their own cycle, which correlates with crude without being "
            "caused by it. The cohort is grouped by sign of commodity exposure, not by a "
            "claim that crude drives steel.",
            "n = 8 annual observations. Every correlation here is under-powered and is "
            "printed with its minimum detectable value for that reason.",
            "Constant constituents and restatement (C22) apply as everywhere else.",
        ],
    }


def main() -> int:
    d = build()
    with open(OUT, "w") as f:
        json.dump(d, f, indent=1)

    print(f"panel {d['panel_names']} names, FY{d['first_fy']}-FY{d['last_fy']}\n")
    print(f"{'FY':6}{'avg WTI':>9}{'crude':>9}{'index profit growth':>21}")
    for r in d["annual"]:
        print(f"{r['fy']:<6}{r['avg_wti']:>9}{r['crude_change_pct']:>8.1f}%"
              f"{r['profit_growth_pct']:>20.1f}%")

    c = d["correlation"]
    verdict = "DETECTABLE" if c["detectable"] else "NOT DETECTABLE at this n"
    print(f"\ncorr(crude change, index profit growth) = {c['r']:+.3f}   n={c['n']}   "
          f"min detectable |r| = {c['min_detectable_r']}   -> {verdict}")

    print(f"\n{'cohort':22s}{'n':>3}{'profit share':>14}{'FY' + str(d['last_fy'] % 100):>8}"
          f"{'latest Q':>10}   crude")
    for x in d["cohorts"]:
        lq = x["latest_quarter_yoy_pct"]
        print(f"{x['cohort']:22s}{x['n']:3d}{x['profit_share_latest_pct']:13.1f}%"
              f"{x['latest_fy_growth_pct']:7.1f}%"
              f"{(f'{lq:+.1f}%' if lq is not None else 'n/a'):>10}   {x['crude_sign']}")

    print("\nquarterly YoY path by cohort — fixed member set per row, where the slowdown is")
    for x in d["cohorts"]:
        if x["quarterly_yoy"]:
            q0 = x["quarterly_yoy"][0]
            print(f"  {x['cohort']:22s} n={q0['names']:2d}  "
                  f"{x['quarterly_yoy'][0]['period'][:7]}→{x['quarterly_yoy'][-1]['period'][:7]}  " +
                  " → ".join(f"{q['yoy_pct']:+.1f}" for q in x["quarterly_yoy"]))

    print("\nCONTRIBUTION TO INDEX PROFIT GROWTH (pp) — sums to aggregate growth")
    cols = [c["cohort"] for c in d["cohorts"]]
    print(f"{'quarter':12s}" + "".join(f"{c[:11]:>13}" for c in cols) + f"{'total':>9}")
    for r in d["quarterly_contribution_pp"]:
        print(f"{r['period']:12s}" + "".join(f"{r.get(c, 0):13.1f}" for c in cols)
              + f"{r['total_pp']:9.1f}")
    for q in d["required_to_hit_target"]:
        print(f"   to reach {q['target_index_growth_pct']:.0f}% from a "
              f"{q['current_run_rate_pp']:.1f}pp run-rate: shortfall {q['shortfall_pp']:.1f}pp"
              f" — financials alone would need {q['if_financials_alone_pct']:.0f}% growth")

    print("\nSENSITIVITY (arithmetic — inputs are assumptions, not measurements)")
    print(f"{'consumer hit':>14}{'commodity gain':>16}{'index earnings delta':>22}")
    for g in d["sensitivity_grid"]:
        print(f"{g['consumer_cohort_hit_pp']:13d}pp{g['commodity_cohort_gain_pp']:15d}pp"
              f"{g['index_earnings_growth_delta_pp']:20.1f}pp")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
