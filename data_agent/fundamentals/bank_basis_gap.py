#!/usr/bin/env python3
"""
bank_basis_gap.py — measure the consolidated-minus-standalone profit gap, per company.

WHAT QUESTION THIS ANSWERS
--------------------------
Correction C28 recorded that our aggregate FY26 panel profit (₹844,074 cr) sits 16.3%
below Chola Securities' published Nifty-50 figure (₹10,08,780 cr), and that the three
missing names are only 3.05% of index weight — so the gap is DEFINITIONAL, not coverage.
That was as far as the evidence went. "Definitional" was honest but it was a label, not
a measurement.

One component of it is now measurable. Our panel holds the five large banks on a
STANDALONE basis and everything else CONSOLIDATED. Standalone excludes subsidiaries —
for HDFC Bank that is HDB Financial, HDFC Securities, the AMC and insurance stakes — while
NSE's index earnings are consolidated throughout. Fetch both bases for the same company
and the difference is not an estimate, it is a subtraction.

This script does that subtraction and reports what share of the C28 gap it explains. What
it does NOT explain remains open and is printed as a residual rather than absorbed.

PREREQUISITE
------------
    python3 screener_tables.py fetch --basis both --only HDFCBANK,ICICIBANK,SBIN,KOTAKBANK,AXISBANK
    python3 screener_tables.py parse

Any company with both bases in screener_page_tables.csv is included automatically; the
banks are simply the ones that matter most here.

    python3 bank_basis_gap.py
"""
from __future__ import annotations

import collections
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TABLES = HERE / "screener_page_tables.csv"
UNIVERSE = ROOT / "nifty-50-stock-list.csv"
OUT = HERE / "bank_basis_gap.json"

# C28's external reference points, recorded with their source so the comparison is
# auditable rather than a remembered number.
EXTERNAL = {
    "source": "Chola Securities, Q4FY26 Earnings Review and Market Outlook",
    "url": "https://www.cholasecurities.com/research/fundamental/q4fy26-earnings-review-and-market-outlook",
    "nifty50_fy26_pat_cr": 1008780.0,
    "nifty50_fy25_pat_cr": 874313.0,
    "fy26_growth_reported_pct": 15.38,
    "fy26_growth_ex_exceptional_pct": 3.72,
    "caveat": ("Reported PAT, which the source itself says leans on exceptional gains — "
               "ex-exceptional growth is 3.72%. Treat the LEVEL as a different definition "
               "from ours, not as ground truth our number should be forced to match."),
}
OUR_FY26_PANEL_CR = 844074.0     # 47-name balanced panel, backend/quant/index_valuation.py
FY26 = "2026-03-31"
PROFIT_ROWS = ("Net Profit", "Net profit")


def load_rows() -> list[dict]:
    if not TABLES.exists():
        sys.exit(f"no {TABLES.name} — run screener_tables.py first")
    with open(TABLES, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def weights() -> dict:
    if not UNIVERSE.exists():
        return {}
    with open(UNIVERSE, newline="", encoding="utf-8") as fh:
        return {r["Symbol"].strip().upper(): float(r.get("Weight") or 0)
                for r in csv.DictReader(fh) if r.get("Symbol")}


def main() -> None:
    rows = load_rows()
    wt = weights()

    # annual profit per (symbol, basis, period)
    prof: dict = collections.defaultdict(dict)
    for r in rows:
        if r["section"] == "pnl" and r["metric"] in PROFIT_ROWS:
            prof[(r["symbol"], r["basis"])][r["period"]] = float(r["value"])

    syms = sorted({s for s, _ in prof})
    both = [s for s in syms
            if (s, "consolidated") in prof and (s, "standalone") in prof]

    if not both:
        print("No company has BOTH bases cached, so there is nothing to subtract.\n")
        print("Run this first, then re-run me:\n")
        print("  python3 screener_tables.py fetch --basis both \\")
        print("      --only HDFCBANK,ICICIBANK,SBIN,KOTAKBANK,AXISBANK")
        print("  python3 screener_tables.py parse")
        sys.exit(1)

    print(f"Consolidated minus standalone, FY26 ({FY26}) net profit, ₹ cr\n")
    print(f"{'symbol':12s}{'weight':>7s}{'standalone':>13s}{'consolidated':>14s}"
          f"{'gap':>11s}{'gap %':>8s}")
    print("-" * 66)

    detail, tot_sa, tot_con, missing = [], 0.0, 0.0, []
    for s in sorted(both, key=lambda x: -wt.get(x, 0)):
        sa = prof[(s, "standalone")].get(FY26)
        co = prof[(s, "consolidated")].get(FY26)
        if sa is None or co is None:
            missing.append(s)
            continue
        gap = co - sa
        pct = gap / sa * 100 if sa else float("nan")
        tot_sa += sa
        tot_con += co
        detail.append({"symbol": s, "weight_pct": wt.get(s, 0), "standalone_cr": sa,
                       "consolidated_cr": co, "gap_cr": gap, "gap_pct": round(pct, 1)})
        print(f"{s:12s}{wt.get(s,0):7.2f}{sa:13,.0f}{co:14,.0f}{gap:11,.0f}{pct:7.1f}%")

    print("-" * 66)
    total_gap = tot_con - tot_sa
    print(f"{'TOTAL':12s}{'':7s}{tot_sa:13,.0f}{tot_con:14,.0f}{total_gap:11,.0f}"
          f"{(total_gap/tot_sa*100 if tot_sa else 0):7.1f}%")

    if missing:
        print(f"\n  both bases cached but no {FY26} annual row: {', '.join(missing)}")

    # ---- how much of C28 does this actually explain? --------------------------
    c28_gap = EXTERNAL["nifty50_fy26_pat_cr"] - OUR_FY26_PANEL_CR
    share = total_gap / c28_gap * 100 if c28_gap else 0.0
    residual = c28_gap - total_gap

    print(f"\nC28 reconciliation")
    print(f"  external (Chola, reported)      {EXTERNAL['nifty50_fy26_pat_cr']:12,.0f}")
    print(f"  our 47-name panel              {OUR_FY26_PANEL_CR:12,.0f}")
    print(f"  gap to explain                 {c28_gap:12,.0f}")
    print(f"  explained by bank basis        {total_gap:12,.0f}   {share:5.1f}%")
    print(f"  residual, still unexplained    {residual:12,.0f}   {100-share:5.1f}%")
    print("\n  The residual is NOT absorbed into the basis story. It is some mix of the")
    print("  three excluded names (3.05% of weight), exceptional items — the source's own")
    print("  ex-exceptional growth is 3.72% against a reported 15.38% — and minority")
    print("  interest, the same line that makes page Net Profit read ~0.5% above the")
    print("  export. Naming a residual beats quietly rounding it into the part we solved.")
    print("\n  Note this does NOT propagate to the forecast: the projection uses")
    print("  (1+g)^T x (exit_PE / PE_asof), so a stable level offset cancels the way the")
    print("  P/E anchor constant k does. What must be right is the GROWTH rate.")

    doc = {
        "as_of_period": FY26,
        "external": EXTERNAL,
        "our_panel_fy26_cr": OUR_FY26_PANEL_CR,
        "companies": detail,
        "total_standalone_cr": tot_sa,
        "total_consolidated_cr": tot_con,
        "total_gap_cr": total_gap,
        "c28_gap_cr": c28_gap,
        "explained_pct": round(share, 1),
        "residual_cr": residual,
        "note": ("Consolidated minus standalone FY26 net profit for every company with "
                 "both bases cached. Measures one named component of correction C28 and "
                 "leaves the rest as an explicit residual rather than absorbing it."),
    }
    OUT.write_text(json.dumps(doc, indent=1))
    print(f"\nwrote {OUT.name}")


if __name__ == "__main__":
    main()
