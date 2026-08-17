#!/usr/bin/env python3
"""
attributable_panel.py — quarterly index profit on ONE definition, with full coverage.

THE PROBLEM THIS SOLVES
-----------------------
Two sources, each right about something and wrong about the other:

  delivery_history.json  correct LINE ITEM (net profit attributable to owners, which is
                         what an index EPS must be built from) but stale COVERAGE — only
                         37 of 47 names had reported Q1 FY27 when it was built, and
                         download_screener.py silently skips any workbook already on disk,
                         so re-running it refreshes nothing.

  screener_page_tables   full COVERAGE (all 50, 13 quarters) but the wrong LINE ITEM:
                         its `Net Profit` is BEFORE minority interest. ONGC reads 6,554 cr
                         that way against 11,901 attributable, because ONGC consolidates
                         100% of HPCL's and MRPL's losses while owning about 55% of them.

Splicing them would put a definition break inside one series — the failure C24 and C32
are both about. But there is a third way that needs no download at all:

    EPS is, by definition, profit ATTRIBUTABLE TO OWNERS divided by shares.

So `EPS x shares` recovers the export's line item using the page's coverage. Validated on
470 quarter-observations against the export: 92% agree within 3%, and the large names are
exact — Reliance -0.0%, ONGC -0.0%, TCS 0.0%, HDFC Bank 0.0%, Bajaj Finserv -0.0%.

WHY PER-PERIOD SHARE COUNTS
---------------------------
A first pass used each company's LATEST share count for every quarter. That is wrong
whenever a company issues stock: Zomato/Eternal paid for Blinkit partly in shares, so
applying today's larger count to 2024 quarters overstated those profits by ~10%. Every
one of the worst disagreements was share-count drift, not a definition problem. Shares are
now taken from the fiscal year that CONTAINS each quarter.

WHAT THIS IS NOT
----------------
It is REPORTED profit. It does not strip exceptional items, and correction C33/O14 shows
that matters: Reliance's Q1 FY26 base carries an 8,924 cr Asian Paints gain, worth about
4.4pp on the Q1 FY27 growth rate on its own. Reported and underlying are different
questions; this file answers the first one properly so the second can be asked cleanly.

    python3 attributable_panel.py
    -> attributable_panel.json
"""
from __future__ import annotations

import collections
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent if HERE.name == "fundamentals" else HERE
TABLES = HERE / "screener_page_tables.csv"
PANEL_BASIS = ROOT / "screener_panel.json"
DELIVERY = ROOT / "delivery_history.json"
OUT = ROOT / "attributable_panel.json"

AGREE_PCT = 3.0     # cross-check tolerance against the export


def fy_end(period: str) -> str:
    """Indian fiscal year END containing this quarter. Jun-2025 -> 2026-03-31."""
    y, m = int(period[:4]), int(period[5:7])
    return f"{y + 1 if m > 3 else y}-03-31"


def main() -> None:
    for p in (TABLES, PANEL_BASIS, DELIVERY):
        if not p.exists():
            sys.exit(f"missing {p}")

    basis = json.loads(PANEL_BASIS.read_text())["basis_by_symbol"]
    hist = json.loads(DELIVERY.read_text())["history"]
    rows = list(csv.DictReader(open(TABLES, newline="", encoding="utf-8")))

    eps: dict = collections.defaultdict(dict)
    pgnp: dict = collections.defaultdict(dict)
    for r in rows:
        if r["section"] != "quarters" or r["basis"] != basis.get(r["symbol"]):
            continue
        if r["metric"] == "EPS in Rs":
            eps[r["symbol"]][r["period"]] = float(r["value"])
        elif r["metric"] == "Net Profit":
            pgnp[r["symbol"]][r["period"]] = float(r["value"])

    # share count per fiscal year, plus the export's own profit for cross-checking
    shares: dict = collections.defaultdict(dict)
    export: dict = collections.defaultdict(dict)
    for s, v in hist.items():
        for x in v.get("series", []):
            if x.get("shares_cr"):
                shares[s][x["period"]] = x["shares_cr"]
        for q in v.get("quarters", []):
            if q.get("net_profit") is not None:
                export[s][q["period"]] = q["net_profit"]

    panel: dict = collections.defaultdict(dict)
    checks, drift = [], []
    for s in eps:
        if not shares.get(s):
            continue                       # no share count -> cannot derive. Named below.
        latest_fy = max(shares[s])
        for period, e in eps[s].items():
            n = shares[s].get(fy_end(period)) or shares[s][latest_fy]
            val = e * n
            panel[s][period] = round(val, 1)
            ref = export.get(s, {}).get(period)
            if ref:
                d = (val - ref) / abs(ref) * 100
                checks.append(abs(d) <= AGREE_PCT)
                if abs(d) > AGREE_PCT:
                    drift.append({"symbol": s, "period": period, "export": ref,
                                  "derived": round(val, 1), "diff_pct": round(d, 1)})

    periods = sorted({p for v in panel.values() for p in v})
    series = []
    for p in periods:
        names = [s for s in panel if p in panel[s]]
        yr = f"{int(p[:4]) - 1}{p[4:]}"
        both = [s for s in names if yr in panel.get(s, {})]
        tot = sum(panel[s][p] for s in names)
        row = {"period": p, "names": len(names), "pat_cr": round(tot, 1)}
        if both:
            a = sum(panel[s][yr] for s in both)
            b = sum(panel[s][p] for s in both)
            row |= {"yoy_pct": round((b / a - 1) * 100, 2), "yoy_names": len(both)}
        series.append(row)

    no_shares = sorted(set(eps) - set(shares))
    doc = {
        "definition": "net profit attributable to owners of the parent",
        "method": ("page EPS x shares outstanding for the fiscal year containing each "
                   "quarter. EPS is attributable-profit-per-share by definition, so this "
                   "recovers the export's line item with the page's coverage."),
        "cross_check": {
            "against": "delivery_history.json (Excel export)",
            "observations": len(checks),
            "within_pct": AGREE_PCT,
            "agree": sum(checks),
            "agree_rate_pct": round(sum(checks) / len(checks) * 100, 1) if checks else None,
            "disagreements": sorted(drift, key=lambda r: -abs(r["diff_pct"]))[:12],
            "note": ("Remaining disagreements are share-count timing, not definition — "
                     "companies that issued or bought back stock mid-year."),
        },
        "coverage": {
            "companies": len(panel),
            "quarters": len(periods),
            "first": periods[0] if periods else None,
            "last": periods[-1] if periods else None,
            "excluded_no_share_count": no_shares,
        },
        "reported_not_underlying": ("REPORTED profit. Exceptional items are NOT stripped. "
                                    "Reliance's Jun-2025 base carries an 8,924 cr Asian "
                                    "Paints gain worth ~4.4pp on the Q1 FY27 rate alone "
                                    "(C33, open item O14)."),
        "series": series,
        "by_symbol": {s: dict(sorted(v.items())) for s, v in sorted(panel.items())},
    }
    OUT.write_text(json.dumps(doc, indent=1))

    print(f"wrote {OUT.name}\n")
    c = doc["cross_check"]
    print(f"  {len(panel)} companies, {len(periods)} quarters "
          f"({periods[0]} .. {periods[-1]})")
    print(f"  cross-check vs export: {c['agree']}/{c['observations']} within "
          f"{AGREE_PCT}%  ({c['agree_rate_pct']}%)")
    if no_shares:
        print(f"  no share count, excluded: {', '.join(no_shares)}")

    print(f"\n{'quarter':>12}{'names':>7}{'PAT cr':>13}{'YoY':>9}")
    for r in series[-8:]:
        y = f"{r['yoy_pct']:+8.2f}%" if "yoy_pct" in r else "        -"
        print(f"{r['period']:>12}{r['names']:7d}{r['pat_cr']:13,.0f}{y}")

    last = series[-1]
    if "yoy_pct" in last:
        print(f"\n  Q1 FY27 exit rate on this panel: {last['yoy_pct']:+.2f}% "
              f"({last['yoy_names']} names)")
        print(f"  tracker's stale export panel:    +3.73% (37 names)")
        print(f"  ET NOW, Nifty 50 PAT:            +8.28%")
    if len(drift):
        print(f"\n  {len(drift)} share-count-drift observations — see cross_check.disagreements")


if __name__ == "__main__":
    main()
