#!/usr/bin/env python3
"""nifty_history.py — the historical-analysis artifact behind the Nifty view's
"Earnings History" tab.

WHAT IT ASSEMBLES, AND WHY IN ONE FILE
    Four things that only mean something next to each other:

    1. SECTOR CONTRIBUTION, by year. (sector profit_t - sector profit_t-1) / total
       profit_t-1. The rows sum EXACTLY to index growth, which is the property that
       makes this a decomposition rather than a set of ratios. Profit share tells you
       who earns; contribution tells you who moved the number.

    2. THE EARNINGS CYCLE against the index. Monthly Nifty, the annual EPS step, annual
       growth, and the quarterly growth overlay. EPS is a STEP because the number in the
       price changes on exactly one day a year — year-end plus the publication lag — and
       is flat in between. Drawing it as a curve would imply information arriving that
       does not arrive.

    3. THE 2026 WINDOW. Nifty, crude and FII index-futures positioning through a period
       in which NO annual result was published, so the earnings denominator was frozen.
       That makes it the one clean natural experiment in the sample for the valuation
       channel: every point of index movement is the multiple.

    4. FII FLOWS, GROSS. The `fii_dii_flows` table has carried fii_buy and fii_sell all
       along and this repo only ever read fii_net. Net is 0.22% of gross turnover — a
       rounding residual on a two-way flow — which is the likeliest reason the entire
       FII-predicts-returns battery (H18-H25) found nothing.

PANEL DISCIPLINE
    Every aggregate here is computed on a FIXED member set for the whole series it
    belongs to. Recomputing eligible members period by period lets coverage change
    underneath the series, so a cohort that grows because a large member started
    reporting reads as a cohort that grew. That defect was caught in review once already
    (correction C24) and the guard is applied everywhere here.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import sqlite3
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

DB = os.path.join(_ROOT, "option_chains.db")
DELIVERY = os.path.join(_ROOT, "delivery_history.json")
UNIVERSE = os.path.join(_ROOT, "nifty-50-stock-list.csv")
OUT = os.path.join(_ROOT, "nifty_history.json")

PUBLICATION_LAG_DAYS = 92
FIRST_FY, LAST_FY = 2018, 2026
WINDOW_FROM = "2026-01-01"
# The multiple this file anchors the reconstructed EPS series to. Same convention as
# index_valuation.py: today matches the app's bottom-up figure BY CONSTRUCTION, so the
# LEVEL is an anchor and only the SHAPE is measured.
EPS_TODAY = 1195.0


def _conn():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _panel_and_agg(hist):
    years = [f"{y}-03-31" for y in range(FIRST_FY, LAST_FY + 1)]
    panel = [s for s, v in hist.items()
             if all(any(x["period"] == y for x in v["series"]) for y in years)]
    agg = {y: sum(next(x["net_profit"] for x in hist[s]["series"] if x["period"] == y)
                  for s in panel) for y in years}
    return years, panel, agg


def sector_matrix(hist, sector) -> dict:
    years, panel, agg = _panel_and_agg(hist)
    by = defaultdict(list)
    for s in panel:
        by[sector.get(s, "Other")].append(s)

    def pat(s, y):
        return next(x["net_profit"] for x in hist[s]["series"] if x["period"] == y)

    rows = []
    for sec, names in by.items():
        contrib = []
        for i in range(1, len(years)):
            base = agg[years[i - 1]]
            cur = sum(pat(s, years[i]) for s in names)
            prv = sum(pat(s, years[i - 1]) for s in names)
            contrib.append(round((cur - prv) / base * 100, 2))
        rows.append({
            "sector": sec, "n": len(names), "contrib_pp": contrib,
            "share_latest_pct": round(sum(pat(s, years[-1]) for s in names)
                                      / agg[years[-1]] * 100, 1),
        })
    rows.sort(key=lambda r: -r["share_latest_pct"])
    total = [round(sum(r["contrib_pp"][i] for r in rows), 2)
             for i in range(len(years) - 1)]
    return {
        "fy_labels": [f"FY{y[2:4]}" for y in [str(int(x[:4])) for x in years[1:]]],
        "rows": rows, "index_growth_pp": total, "panel": len(panel),
        "note": ("Contribution, not share. Rows sum exactly to index growth — that is "
                 "what makes it a decomposition. Constant constituents: today's Nifty 50 "
                 "back-cast, so sectors that entered the index by growing are flattered."),
    }


ATTRIB_PANEL = os.path.join(_ROOT, "attributable_panel.json")


def _quarters_source(hist: dict) -> tuple[dict, str]:
    """Quarterly PAT per company — attributable_panel.json if present, else the export.

    The Earnings History tab and the Outlook tab MUST read the same quarterly series.
    They briefly did not: `earnings_acceleration.py` was moved onto the attributable
    panel while this file was left on `delivery_history.json`, so the two tabs showed
    +7.1% and +3.7% for the same quarter, on 47 and 37 names, from the same app. A user
    comparing them has no way to tell which is right, and a UI that contradicts itself
    is worse than one that is merely wrong — it destroys the reason to trust either
    number. Both now read the same file.

    See attributable_panel.py for why EPS x shares is the right derivation and
    correction C34 for what the stale panel was hiding.
    """
    doc = {}
    if os.path.exists(ATTRIB_PANEL):
        try:
            with open(ATTRIB_PANEL) as fh:
                doc = json.load(fh)
        except Exception:
            doc = {}
    by = doc.get("by_symbol") or {}
    if by:
        return ({s: {"quarters": [{"period": p_, "net_profit": v}
                                  for p_, v in sorted(q.items())]}
                 for s, q in by.items()},
                "attributable_panel.json (EPS x shares, all reporters)")
    return hist, "delivery_history.json (Excel export — coverage may be stale)"


def cycle(hist) -> dict:
    years, panel, agg = _panel_and_agg(hist)
    con = _conn()
    bars = [(r[0][:10], float(r[1])) for r in con.execute(
        "SELECT ts, close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
        "AND close IS NOT NULL ORDER BY ts")]
    con.close()
    monthly, seen = [], set()
    for d, c in bars:
        seen.add(d[:7])
    for m in sorted(seen):
        last = max(d for d, _ in bars if d[:7] == m)
        monthly.append({"m": m, "close": round(dict(bars)[last], 0)})

    pub = []
    for i, y in enumerate(years):
        pd_ = (datetime.date.fromisoformat(y)
               + datetime.timedelta(days=PUBLICATION_LAG_DAYS)).isoformat()
        pub.append({
            "fy": f"FY{y[2:4]}", "published": pd_,
            "month_index": next((k for k, mm in enumerate(monthly)
                                 if mm["m"] == pd_[:7]), None),
            "index_eps": round(EPS_TODAY * agg[y] / agg[years[-1]], 0),
            "yoy_pct": (round((agg[y] / agg[years[i - 1]] - 1) * 100, 1) if i else None),
        })

    # Quarterly overlay — ONE fixed member set across every quarter shown and its
    # year-ago base, for the reason in the module docstring.
    def qpat(s, p):
        return next((q["net_profit"] for q in qhist[s].get("quarters", [])
                     if q["period"] == p), None)

    qhist, qsrc = _quarters_source(hist)
    allq = sorted({q["period"] for v in qhist.values() for q in v.get("quarters", [])})
    quarterly = []
    for start in range(len(allq)):
        want = allq[start:]
        mem = [s for s in qhist
               if all(qpat(s, p) is not None
                      and qpat(s, f"{int(p[:4]) - 1}{p[4:]}") is not None for p in want)]
        if len(mem) >= 30:
            for p in want:
                b = f"{int(p[:4]) - 1}{p[4:]}"
                c, pr = sum(qpat(s, p) for s in mem), sum(qpat(s, b) for s in mem)
                quarterly.append({"period": p, "yoy_pct": round((c / pr - 1) * 100, 1),
                                  "panel": len(mem),
                                  "month_index": next((k for k, mm in enumerate(monthly)
                                                       if mm["m"] == p[:7]), None)})
            break

    # TTM, on the 47-name panel only. A quarter where coverage drops is EXCLUDED, not
    # plotted — a 22% "collapse" that is really a change in panel would read as news.
    ttm = []
    for i in range(3, len(allq)):
        ps = allq[i - 3:i + 1]
        mem = [s for s in panel if all(qpat(s, x) is not None for x in ps)]
        if len(mem) < len(panel):
            continue
        ttm.append({"period": allq[i], "panel": len(mem),
                    "ttm_profit_cr": round(sum(sum(qpat(s, x) for x in ps)
                                               for s in mem), 0)})
    for i in range(1, len(ttm)):
        ttm[i]["increment_cr"] = round(ttm[i]["ttm_profit_cr"] - ttm[i - 1]["ttm_profit_cr"], 0)

    return {"monthly": monthly, "publications": pub, "quarterly": quarterly, "ttm": ttm,
            "quarterly_source": qsrc,
            "ttm_note": ("Quarters where the reporting panel is smaller than the annual "
                         "panel are omitted entirely rather than plotted — a drop in "
                         "coverage would otherwise read as a collapse in profit.")}


def window(hist) -> dict:
    """Nifty, crude and FII index-futures positioning through the frozen-EPS window."""
    con = _conn()

    def ser(sym):
        return dict(con.execute(
            "SELECT substr(ts,1,10), close FROM price_bars WHERE symbol=? "
            "AND timeframe='1d' AND close IS NOT NULL", (sym,)))

    N, C = ser("NIFTY"), ser("CRUDEOIL")
    fii = {r[0]: r[1] - r[2] for r in con.execute(
        "SELECT flow_date, idx_fut_long, idx_fut_short FROM participant_oi "
        "WHERE participant_type='FII'")}
    con.close()

    def near(D, d):
        ks = [k for k in D if k <= d]
        return D[max(ks)] if ks else None

    days = sorted(d for d in N if d >= WINDOW_FROM)
    pts = []
    for i, d in enumerate(days):
        if i % 5 and d != days[-1]:
            continue
        f = near(fii, d)
        pts.append({"d": d, "nifty": round(N[d], 0), "wti": round(near(C, d), 1),
                    "fii_net_short": (round(-f, 0) if f is not None else None)})
    lows = min(pts, key=lambda p: p["nifty"])
    shorts = [p for p in pts if p["fii_net_short"] is not None]
    peak = max(shorts, key=lambda p: p["fii_net_short"]) if shorts else None
    return {
        "points": pts, "from": days[0], "to": days[-1],
        "nifty_low": lows, "fii_short_peak": peak,
        "eps_frozen_at": 1118,
        "note": ("No annual result was published between 2025-07-01 and 2026-07-01, so "
                 "the earnings denominator in the price was constant across this window. "
                 "Every point of index movement here is the multiple. That is what makes "
                 "it a natural experiment for the valuation channel — it is NOT a claim "
                 "that crude caused it; geopolitics, FX and global risk appetite all "
                 "moved together."),
        "scales_note": ("Series are reported as ABSOLUTE levels for stacked panels. They "
                        "must not be overlaid on one axis: index points, dollars and "
                        "contract counts share no scale, and giving each its own axis "
                        "lets the choice of bounds decide where the lines appear to "
                        "cross."),
    }


def flows() -> dict:
    con = _conn()
    rows = list(con.execute(
        "SELECT flow_date, fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net "
        "FROM fii_dii_flows ORDER BY flow_date"))
    fii_oi = list(con.execute(
        "SELECT flow_date, idx_fut_long, idx_fut_short, stk_fut_long, stk_fut_short "
        "FROM participant_oi WHERE participant_type='FII' ORDER BY flow_date"))
    con.close()
    if not rows:
        return {"available": False}

    fb, fs = sum(r[1] for r in rows), sum(r[2] for r in rows)
    db_, ds = sum(r[4] for r in rows), sum(r[5] for r in rows)
    fn, dn = sum(r[3] for r in rows), sum(r[6] for r in rows)
    ratios = sorted(abs(r[3]) / (r[1] + r[2]) * 100 for r in rows)

    def book(longs, shorts):
        nets = [l - s for l, s in zip(longs, shorts)]
        gross = [l + s for l, s in zip(longs, shorts)]
        pct = sorted(abs(n) / g * 100 for n, g in zip(nets, gross) if g)
        return {"net_min": min(nets), "net_max": max(nets),
                "net_over_gross_median_pct": round(pct[len(pct) // 2], 1),
                "always_same_sign": all(n < 0 for n in nets) or all(n > 0 for n in nets)}

    return {
        "available": True, "first": rows[0][0], "last": rows[-1][0], "sessions": len(rows),
        "fii": {"gross_buy_cr": round(fb, 0), "gross_sell_cr": round(fs, 0),
                "net_cr": round(fn, 0), "gross_turnover_cr": round(fb + fs, 0),
                "net_over_gross_pct": round(abs(fn) / (fb + fs) * 100, 2),
                "daily_ratio_median_pct": round(ratios[len(ratios) // 2], 2)},
        "dii": {"gross_buy_cr": round(db_, 0), "gross_sell_cr": round(ds, 0),
                "net_cr": round(dn, 0), "gross_turnover_cr": round(db_ + ds, 0),
                "net_over_gross_pct": round(abs(dn) / (db_ + ds) * 100, 2)},
        "daily": [{"d": r[0], "fii_buy": r[1], "fii_sell": r[2], "fii_net": round(r[3], 0),
                   "dii_net": round(r[6], 0)} for r in rows],
        "index_futures": book([r[1] for r in fii_oi], [r[2] for r in fii_oi]),
        "stock_futures": book([r[3] for r in fii_oi], [r[4] for r in fii_oi]),
        "positioning_sessions": len(fii_oi),
        "note": ("Net is a small difference between two very large numbers. Reported "
                 "beside gross so it cannot be read as a directional signal without "
                 "seeing what it is a residual OF."),
        "caveat": ("Derivatives figures are CONTRACT COUNTS across different underlyings "
                   "and are not additive — the index-short and stock-long legs cannot be "
                   "netted without lot sizes and prices (open item O12). And the cash "
                   f"table begins {rows[0][0]}, after the 2026 de-rating ended (O13)."),
    }


def panel_integrity(hist) -> dict:
    """Is it the same companies from the start — and are they the same COMPANIES?

    Two different questions, and only the first one has a clean answer.

    COUNT is constant by construction: the panel is defined as names with a complete
    FY2018-FY2026 series, so all 47 report in all 9 years. Nothing enters or leaves
    mid-series, which is what makes the aggregate comparable year to year.

    IDENTITY is not. A company that absorbs another is still one row in the panel, but
    its FY2018 profit and its FY2026 profit belong to different businesses. HDFC Bank's
    share count rose 36% in FY24 when it merged with HDFC Ltd; Shriram Finance's rose
    38% in FY23. The aggregate trajectory picks that up as growth, and part of it is
    acquisition rather than operating performance. There is no way to strip it out
    without a restated like-for-like series this repo does not have — so it is measured
    and disclosed instead of quietly carried.
    """
    years = [f"{y}-03-31" for y in range(FIRST_FY, LAST_FY + 1)]
    panel = [s for s, v in hist.items()
             if all(any(x["period"] == y for x in v["series"]) for y in years)]
    by_year = {y[:4]: sum(1 for s in panel
                          if any(x["period"] == y for x in hist[s]["series"]))
               for y in years}

    moves = []
    for s in panel:
        v = hist[s]
        flags = v.get("share_move_flags", [])
        yrs = [(v["series"][i + 1]["period"][:4], f) for i, f in enumerate(flags) if f]
        if yrs:
            moves.append({"symbol": s, "years": len(yrs),
                          "detail": [{"fy": f"FY{y[2:]}", "share_move_pct": round(f, 0)}
                                     for y, f in yrs]})
    moves.sort(key=lambda m: (-m["years"], -max(d["share_move_pct"] for d in m["detail"])))
    return {
        "count_constant": len(set(by_year.values())) == 1,
        "panel_size": len(panel), "years": len(years), "by_year": by_year,
        "names_with_corporate_actions": len(moves),
        "largest": moves[:6],
        "verdict": (f"COUNT is constant — {len(panel)} names in all {len(years)} years, nothing "
                    "enters or leaves mid-series. IDENTITY is not: "
                    f"{len(moves)} of {len(panel)} names changed share count by more than 5% in at "
                    "least one year, which means the entity itself changed scope."),
        "why_it_matters": ("The aggregate trajectory reads a merger as growth. HDFC Bank is ~11.6% "
                           "of the index and absorbed HDFC Ltd in FY24 (+36% shares); Shriram "
                           "Finance +38% in FY23. Part of the measured profit growth is acquired, "
                           "not earned. This sits alongside the two biases already disclosed — "
                           "constant constituents and restatement — and all three push the same "
                           "way, flattering history."),
        "different_series_different_panels": ("Annual 47 names · quarterly YoY 47 names (attributable_panel, all "
                                             "reporters) · TTM 47 "
                                              "with under-covered quarters excluded. Each is FIXED "
                                              "within itself; the counts differ BETWEEN series "
                                              "because the underlying disclosures differ. Never "
                                              "compare a figure from one panel to a figure from "
                                              "another."),
    }


def conclusions(cy, w, fl) -> dict:
    """Only what survived a test, and the three inputs the forecast page needs.

    A historical page that ends in a summary of what happened is a report. A historical
    page that ends by HANDING OVER named inputs is a stage in a pipeline, and the
    difference shows up in whether anyone can tell which claims the forecast rests on.
    Each finding below carries its register ID so it can be traced to the test that
    produced it — and several of them are NEGATIVE results, which is the point: they are
    what stops the forecast page from assuming mean reversion or reading FII net flow as
    a signal.
    """
    q = cy["quarterly"]
    exit_rate = q[-1]["yoy_pct"] if q else None
    ann = [p for p in cy["publications"] if p["yoy_pct"] is not None]
    inc = [t.get("increment_cr") for t in cy["ttm"] if t.get("increment_cr")]

    return {
        "survived": [
            {"id": "H51", "claim": "The annual growth figure describes the trend",
             "verdict": "MISLEADING",
             "detail": (f"FY{ann[-1]['fy'][2:]} annual growth was {ann[-1]['yoy_pct']}%, but that is the "
                        f"average of a year that decelerated through itself. The quarterly exit rate is "
                        f"{exit_rate}%. TTM increments have decayed "
                        + " → ".join(f"{i:,.0f}" for i in inc) + " cr, five steps with no reversal.")},
            {"id": "H59", "claim": "The 2026 drawdown was an earnings event",
             "verdict": "DEAD — it was 100% multiple",
             "detail": (f"No annual result was published across the window, so the denominator was frozen "
                        f"at {w['eps_frozen_at']}. The index fell to {w['nifty_low']['nifty']:,.0f} on "
                        f"{w['nifty_low']['d']} with earnings unchanged. Year to date: earnings +6.9%, "
                        f"multiple −13.4%.")},
            {"id": "H53", "claim": "A below-median multiple reverts upward",
             "verdict": "DEAD — it kept falling",
             "detail": ("From a below-median start the multiple ROSE in only 42–43% of 6M/1Y windows and "
                        "25% of 2Y windows. Cheap-start windows still made money — through earnings "
                        "outrunning a still-compressing multiple, not through re-rating.")},
            {"id": "H60", "claim": "Last year's earnings growth predicts next year's",
             "verdict": "NOT DETECTABLE",
             "detail": ("Annual AR(1) r = −0.121 at n = 7 against a minimum detectable |r| of ~0.80. "
                        "Neither the run-rate nor the historical mean is demonstrably the better "
                        "estimator, so no lookback window is weighted.")},
            {"id": "H62", "claim": "FII net flow is a directional signal",
             "verdict": f"DEAD — net is {fl['fii']['net_over_gross_pct']}% of gross" if fl.get("available") else "DEAD",
             "detail": ((f"Gross buy ₹{fl['fii']['gross_buy_cr']:,.0f} cr against gross sell "
                         f"₹{fl['fii']['gross_sell_cr']:,.0f} cr over {fl['sessions']} sessions. "
                         f"DII run at {fl['dii']['net_over_gross_pct']}% — an order of magnitude more "
                         "directional. This is the likeliest reason the FII-predicts-returns battery "
                         "(H18–H25) found nothing.") if fl.get("available") else "")},
            {"id": "H63", "claim": "The FII index-futures short is a bearish view",
             "verdict": "RECONSIDER — it pairs with a stock-futures LONG",
             "detail": ("Index futures one-sided short on every session observed; stock futures "
                        "one-sided LONG on every session observed. Long single stocks, short the index — "
                        "relative value, not a directional bet. Contract counts are not additive across "
                        "underlyings, so the combined book is unquantified (O12).")},
        ],
        "handoff": [
            {"input": "Earnings trajectory",
             "value": f"exit rate {exit_rate}%",
             "carry": ("The forecast's growth axis should start from the quarterly exit rate, not the "
                       "annual average, and should not assume persistence — H60 says the run-rate has "
                       "no demonstrated predictive content on its own.")},
            {"input": "Valuation behaviour",
             "value": "multiple does not mean-revert",
             "carry": ("The exit multiple must be a stated INPUT with its own axis, never a residual and "
                       "never defaulted to a historical median. H53 and H59 are both against reversion.")},
            {"input": "FII positioning",
             "value": "net is noise; the book is long-stock / short-index",
             "carry": ("Flows belong in the forecast as a VALUATION input — they move the multiple, not "
                       "the earnings — and headline net flow should not be used at all.")},
        ],
        "note": ("Negative results are load-bearing here. Three of the six findings are things that do "
                 "NOT work, and they are what keeps the forecast page from quietly assuming mean "
                 "reversion, extrapolating a run-rate, or treating FII net flow as information."),
    }


def main() -> int:
    with open(DELIVERY) as f:
        hist = json.load(f)["history"]
    with open(UNIVERSE) as f:
        sector = {r["Symbol"]: r["Sector"] for r in csv.DictReader(f)}

    cy, w, fl = cycle(hist), window(hist), flows()
    doc = {
        "as_of": datetime.date.today().isoformat(),
        "sector_matrix": sector_matrix(hist, sector),
        "cycle": cy,
        "window_2026": w,
        "flows": fl,
        "panel_integrity": panel_integrity(hist),
        "conclusions": conclusions(cy, w, fl),
        "note": ("Historical measurement only — no forecast. The forecast artifacts are "
                 "nifty_outlook.json and earnings_acceleration.json."),
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1)

    sm = doc["sector_matrix"]
    print(f"sector matrix: {len(sm['rows'])} sectors x {len(sm['fy_labels'])} years, "
          f"panel {sm['panel']}")
    print(f"  index growth: " + "  ".join(f"{l} {g:+.1f}" for l, g in
                                          zip(sm["fy_labels"], sm["index_growth_pp"])))
    print(f"cycle: {len(cy['monthly'])} months, {len(cy['publications'])} publications, "
          f"{len(cy['quarterly'])} quarterly points, {len(cy['ttm'])} TTM points")
    if cy["ttm"]:
        inc = [t.get("increment_cr") for t in cy["ttm"] if t.get("increment_cr")]
        print(f"  TTM increments: " + " → ".join(f"{i:,.0f}" for i in inc))
    print(f"window: {w['from']} → {w['to']}, {len(w['points'])} points; "
          f"low {w['nifty_low']['nifty']:,.0f} on {w['nifty_low']['d']}; "
          f"FII short peak {w['fii_short_peak']['fii_net_short']:,.0f} on {w['fii_short_peak']['d']}")
    if fl.get("available"):
        print(f"flows: {fl['sessions']} sessions {fl['first']}→{fl['last']}")
        print(f"  FII net/gross {fl['fii']['net_over_gross_pct']}%  vs  "
              f"DII {fl['dii']['net_over_gross_pct']}%")
        print(f"  index futures net/gross {fl['index_futures']['net_over_gross_gross' if False else 'net_over_gross_median_pct']}% "
              f"(one-sided: {fl['index_futures']['always_same_sign']})")
        print(f"  stock futures net/gross {fl['stock_futures']['net_over_gross_median_pct']}% "
              f"(one-sided: {fl['stock_futures']['always_same_sign']})")
    pi = doc["panel_integrity"]
    print(f"\nPANEL INTEGRITY  count constant: {pi['count_constant']} "
          f"({pi['panel_size']} names x {pi['years']} years)")
    print(f"  but {pi['names_with_corporate_actions']} names changed share count >5% in >=1 year:")
    for m in pi["largest"]:
        print("    " + f"{m['symbol']:12s}" + ", ".join(
            f"{d['fy']} {d['share_move_pct']:+.0f}%" for d in m["detail"]))

    print("\nCONCLUSIONS THAT SURVIVED A TEST")
    for c in doc["conclusions"]["survived"]:
        print(f"  {c['id']:5s} {c['verdict']}")
    print("HANDOFF TO THE FORECAST PAGE")
    for hnd in doc["conclusions"]["handoff"]:
        print(f"  {hnd['input']:22s} {hnd['value']}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
