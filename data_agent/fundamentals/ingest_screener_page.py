#!/usr/bin/env python3
"""
ingest_screener_page.py — turn screener_page_tables.csv into a margin panel.

WHAT THIS IS FOR
----------------
Correction C26 removed financials from the aggregate operating margin because Screener's
Excel export gave banks no usable margin: its `sales` row is interest earned and its
`operating profit` does not net interest expense, so the ratio read 58-71% for the five
large banks. That left the channel covering ~77% of panel revenue and BLIND to the cohort
supplying the largest share of index earnings growth.

Screener's company PAGE carries a separate lender template the export throws away —
`Financing Profit` (revenue less interest less expenses) and `Financing Margin %`. Seven
of the eleven financials in the universe have it, worth 31.95 of 34.75 index-weight
points, so about 92% of the blind spot becomes measurable.

THE DECISION THIS FILE MAKES, AND WHY
-------------------------------------
It does NOT blend the two into a single index-wide margin, and refusing to is the point.

Operating margin and financing margin are different ratios over different denominators.
A non-financial's denominator is revenue from selling things; a lender's is interest and
fee income, against which interest paid is a cost of goods rather than a financing item.
Summing the numerators and denominators across both cohorts would produce a number with
no referent — which is exactly the error C26 was raised to correct, just committed in the
opposite direction. One blended figure would LOOK like restored coverage while meaning
less than the honest gap it replaced.

So this writes TWO series that are each internally consistent, each with its own coverage,
and leaves the comparison to the reader. Two numbers that mean something beat one that
does not.

ONE SOURCE PER SERIES
---------------------
Everything here comes from the page scrape and nothing from the Excel export. Validated on
40 overlapping quarterly values: Sales and Operating Profit agree to the rupee, but Net
Profit reads 0.39-0.59% ABOVE the export on every TCS quarter and never below — minority
interest. A series spliced across the two sources gets a ~0.5% step at the joint that will
read as a growth inflection. Rebuild, never patch.

TWO THINGS THE FIRST RUN GOT WRONG
----------------------------------
A. INSURERS ARE NOT LOW-MARGIN MANUFACTURERS. SBILIFE and HDFCLIFE render on the generic
   template, so the first version swept them into the operating-margin aggregate with
   premium income as "sales": SBI Life at 1.6%, HDFC Life at 1.5%, against Reliance's
   15.4% and TCS's 25.7%. Premium income is not revenue from operations in any sense that
   makes those ratios comparable, and JIOFIN at 69.7% is a holding company, not a
   business with a margin. Net effect on the aggregate was only 0.2pp — but it is C26's
   error exactly, and a small wrong number is still wrong. They are excluded by RULE, not
   by a hardcoded list: a Financial Services company that Screener does not give the
   lender template to has no comparable margin, so it is named unmeasurable.

B. THE LENDER QUARTERLY MARGIN CANNOT CARRY A TREND. Our recomputation matches Screener's
   own published Financing Margin % to within 0.5pp, so the parse is right — but the
   series itself lurches: Axis 8.0 -> 1.0 -> 1.0 -> 7.0 -> 1.0 -> 8.0, HDFC Bank 15.0 ->
   -1.0 -> 13.0. Screener's `Expenses` for a lender absorbs provisions, which are lumpy
   by nature. Measured over the panel: stdev of quarter-on-quarter change is 2.30pp for
   lenders against 0.89pp for non-financials, with swings up to 4.4pp.

   The first run reported "+7.0pp YoY" for lenders. That was a trough quarter compared
   against a peak quarter. On a four-quarter TTM basis the same move is +1.9pp, and TTM
   stdev falls to 0.82pp — roughly a third of the quarterly noise. TTM is therefore the
   headline for both cohorts and the volatility diagnostic ships with the artifact, so
   the choice is justified by the data rather than asserted.

BALANCED PANELS
---------------
Each cohort's series keeps only the periods where every member of that cohort reported
(correction C24). A cohort that grows because a large member started reporting is a
cohort that looks like it grew.

    python3 ingest_screener_page.py
    -> screener_panel.json
"""
from __future__ import annotations

import collections
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TABLES = HERE / "screener_page_tables.csv"
COVERAGE = HERE / "screener_page_coverage.csv"
UNIVERSE = ROOT / "nifty-50-stock-list.csv"
OUT = ROOT / "screener_panel.json"

# Numerator / denominator per cohort. Deliberately separate dicts — see the module note.
GENERIC = {"num": "Operating Profit", "den": "Sales", "label": "operating margin"}
LENDER = {"num": "Financing Profit", "den": "Revenue", "label": "financing margin"}

# Sectors whose revenue line is not operating revenue. A member here is excluded
# from the operating-margin cohort unless Screener gives it the lender template.
_NON_OPERATING_SECTORS = {"Financial Services"}


# --------------------------------------------------------------------------------
# EXCLUSION IS NOT ONE CATEGORY. Two reasons look identical in a coverage percentage
# and mean opposite things:
#
#   not_applicable    the metric does not exist for this business model. No amount of
#                     downloading produces it. A blank here is the CORRECT answer and
#                     the number that would fill it would be wrong.
#   data_unavailable  the metric applies, we simply do not have the rows. A blank here
#                     is a TODO with a known fix.
#
# Collapsing them into one "unmeasurable" bucket — which the first version did — hides
# which gaps are closeable. They are now counted and reported separately.
NOT_APPLICABLE = "not_applicable"
DATA_UNAVAILABLE = "data_unavailable"

# WHAT TO USE INSTEAD, per excluded name. The EXCLUSION is decided by rule
# (sector + absence of the lender template), so it generalises to any universe. This
# map is only the remediation hint, and a hint is inherently business-specific — so it
# is a lookup with a generic fallback, never a gate. An unknown excluded name still
# gets sensible guidance rather than silence.
REMEDIATION = {
    "SBILIFE": ("life insurer",
                "revenue line is premium income, which is largely a liability assumed "
                "rather than revenue earned",
                ["VNB margin", "value of new business", "embedded value",
                 "persistency", "solvency ratio"]),
    "HDFCLIFE": ("life insurer",
                 "revenue line is premium income, which is largely a liability assumed "
                 "rather than revenue earned",
                 ["VNB margin", "value of new business", "embedded value",
                  "persistency", "solvency ratio"]),
    "BAJAJFINSV": ("financial holding",
                   "one revenue line consolidates a lender's interest spread with two "
                   "insurers' premium income; neither survives the blend",
                   ["segment-level economics: lending NIM, general-insurance combined "
                    "ratio, life VNB margin, reported per segment"]),
    "JIOFIN": ("financial holding",
               "income is mostly dividends and interest on an investment book, not "
               "revenue from operations",
               ["look-through economics of the underlying holdings",
                "NAV / book value", "segment disclosures"]),
}
REMEDIATION_FALLBACK = ("financial services, non-lender",
                        "revenue line is not operating revenue for this business model",
                        ["business-model-appropriate profitability metrics from the "
                         "company's quarterly investor presentation"])


def remediation(sym: str) -> dict:
    model, why, metrics = REMEDIATION.get(sym, REMEDIATION_FALLBACK)
    return {"business_model": model, "why_not_applicable": why,
            "applicable_metrics": metrics,
            "source": "quarterly investor presentation / segment disclosures",
            "mapped": sym in REMEDIATION}


def load() -> tuple[list[dict], dict]:
    if not TABLES.exists():
        sys.exit(f"no {TABLES.name} — run screener_tables.py first")
    with open(TABLES, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    wt = {}
    if UNIVERSE.exists():
        with open(UNIVERSE, newline="", encoding="utf-8") as fh:
            wt = {r["Symbol"].strip().upper(): float(r.get("Weight") or 0)
                  for r in csv.DictReader(fh) if r.get("Symbol")}
    return rows, wt


def pick_basis(rows: list[dict]) -> dict:
    """One basis per symbol. Prefer whichever the panel already uses.

    When both bases are cached (we fetch both for the banks to measure the subsidiary
    gap) a choice must be made, because carrying both into one aggregate would
    double-count. Standalone is chosen for lenders: it is the basis delivery_history.json
    already holds, and it is what makes a financing margin interpretable — consolidated
    folds an NBFC's and an insurer's economics into the same ratio. The choice is
    RECORDED per symbol rather than left implicit.
    """
    by_sym = collections.defaultdict(set)
    for r in rows:
        by_sym[r["symbol"]].add(r["basis"])
    is_lender = {r["symbol"] for r in rows if r["template"] == "bank"}
    out = {}
    for sym, bases in by_sym.items():
        if len(bases) == 1:
            out[sym] = next(iter(bases))
        else:
            out[sym] = "standalone" if sym in is_lender and "standalone" in bases \
                else ("consolidated" if "consolidated" in bases else sorted(bases)[0])
    return out


def _ttm(pairs: list[tuple[str, float, float]]) -> list[dict]:
    """Four-quarter rolling ratio. Sums numerators and denominators, never averages
    four ratios — averaging ratios silently reweights the quarters."""
    out = []
    for i in range(3, len(pairs)):
        w = pairs[i - 3:i + 1]
        num = sum(x[1] for x in w)
        den = sum(x[2] for x in w)
        if den:
            out.append({"period": pairs[i][0], "margin_pct": round(num / den * 100, 1)})
    return out


def _volatility(vals: list[float]) -> float | None:
    if len(vals) < 3:
        return None
    ch = [b - a for a, b in zip(vals, vals[1:])]
    mean = sum(ch) / len(ch)
    return round((sum((c - mean) ** 2 for c in ch) / len(ch)) ** 0.5, 2)


def series_for(rows: list[dict], members: set, spec: dict, section: str) -> dict:
    """Balanced aggregate ratio for one cohort. Periods with partial coverage dropped."""
    acc: dict = {}
    for r in rows:
        if r["section"] != section or r["symbol"] not in members:
            continue
        if r["metric"] not in (spec["num"], spec["den"]):
            continue
        cell = acc.setdefault(r["period"], {})
        cell.setdefault(r["symbol"], {})[r["metric"]] = float(r["value"])

    counted = {}
    for period, syms in acc.items():
        num = den = 0.0
        n = 0
        for sym, v in syms.items():
            if spec["num"] in v and v.get(spec["den"]):
                num += v[spec["num"]]
                den += v[spec["den"]]
                n += 1
        if n:
            counted[period] = (num, den, n)
    if not counted:
        return {"available": False, "reason": f"no {spec['num']}/{spec['den']} rows"}

    full = max(v[2] for v in counted.values())
    out = [{"period": p, "margin_pct": round(v[0] / v[1] * 100, 1), "names": v[2]}
           for p, v in sorted(counted.items()) if v[2] == full]
    if len(out) < 2:
        return {"available": False, "reason": "fewer than 2 balanced periods"}

    dropped = sorted(p for p, v in counted.items() if v[2] != full)
    pairs = [(p, counted[p][0], counted[p][1]) for p in sorted(counted) if counted[p][2] == full]
    ttm = _ttm(pairs)
    q_vals = [r["margin_pct"] for r in out]
    t_vals = [r["margin_pct"] for r in ttm]

    return {
        "available": True,
        "measure": spec["label"],
        "numerator": spec["num"],
        "denominator": spec["den"],
        "panel": full,
        "series": out,
        "ttm_series": ttm,
        # HEADLINE IS TTM. The quarterly series is kept because it is the raw
        # observation, but for lenders it is unusable as a trend — see note B.
        "latest_ttm_pct": ttm[-1]["margin_pct"] if ttm else None,
        "latest_ttm_period": ttm[-1]["period"] if ttm else None,
        "yoy_ttm_change_pp": (round(t_vals[-1] - t_vals[-5], 1) if len(t_vals) >= 5 else None),
        "latest_quarter_pct": out[-1]["margin_pct"],
        "latest_quarter_period": out[-1]["period"],
        "yoy_quarter_change_pp": (round(q_vals[-1] - q_vals[-5], 1) if len(q_vals) >= 5 else None),
        "qoq_volatility_pp": _volatility(q_vals),
        "ttm_volatility_pp": _volatility(t_vals),
        "periods_dropped_partial": dropped,
    }


def sensitivity_test(rows: list[dict], measured: set, excluded: list[str],
                     spec: dict, period: str) -> dict:
    """Controlled counterfactual: hold EVERY profit figure fixed, grow only the excluded
    cohort's denominator, and watch the aggregate ratio move.

    This is the validation evidence for the exclusion, and it is stronger than the
    assertion it replaces ("insurers use different accounting"). It demonstrates the
    precise thing the metric would misread: the aggregate can change without any company
    earning a rupee more or less, so a decline measured that way cannot represent
    deterioration in operating profitability.

    The danger is NOT the cross-sectional level error at one instant — today that is
    about 0.2pp and looks harmless. It is TIME-SERIES CONTAMINATION. If the excluded
    cohort's top line systematically outgrows the rest of the index, the aggregate margin
    drifts down every period for a reason unrelated to margins, and this channel exists
    specifically to detect margin deterioration. It would be reading its own artifact.

    ON INTERPRETING THE DENOMINATOR: for a life insurer, premium income is roughly
    policies x average premium, so it can rise on higher average premium, rate increases,
    a mix shift toward higher-premium products, or renewals and top-ups — with policy
    count flat or FALLING. 900,000 policies at Rs 50,000 beats 1,000,000 at Rs 40,000.
    So a premium increase supports neither "profitability fell" nor "more people bought
    insurance". It carries volume, price and mix together and separates none of them.
    That is a second, independent reason the ratio is not a margin signal here: even its
    denominator is not interpretable as revenue growth.
    """
    val = collections.defaultdict(dict)
    for r in rows:
        if r["section"] == "quarters" and r["period"] == period \
                and r["metric"] in (spec["num"], spec["den"]):
            val[r["symbol"]][r["metric"]] = float(r["value"])

    def agg(members):
        n = d = 0.0
        for sym in members:
            v = val.get(sym, {})
            if spec["num"] in v and v.get(spec["den"]):
                n += v[spec["num"]]
                d += v[spec["den"]]
        return n, d

    n_in, d_in = agg(measured)
    n_ex, d_ex = agg(excluded)
    if not d_in or not d_ex:
        return {"available": False}

    grid = []
    for g in (0.0, 0.10, 0.20, 0.30, 0.50):
        grid.append({
            "excluded_topline_growth_pct": round(g * 100, 1),
            "aggregate_with_excluded_pct": round((n_in + n_ex) / (d_in + d_ex * (1 + g)) * 100, 2),
            "aggregate_measured_only_pct": round(n_in / d_in * 100, 2),
        })

    return {
        "available": True,
        "period": period,
        "profit_held_constant": True,
        "excluded_share_of_denominator_pct": round(d_ex / (d_in + d_ex) * 100, 1),
        "excluded_share_of_numerator_pct": round(n_ex / (n_in + n_ex) * 100, 1),
        "grid": grid,
        "finding": (f"Holding all profit constant and growing only the excluded cohort's "
                    f"top line from 0% to +50% moves the reported aggregate from "
                    f"{grid[0]['aggregate_with_excluded_pct']}% to "
                    f"{grid[-1]['aggregate_with_excluded_pct']}%, purely through "
                    f"denominator growth. The measured-only series is unchanged at "
                    f"{grid[0]['aggregate_measured_only_pct']}% across every row. "
                    f"Therefore the conventional ratio is not a valid margin signal for "
                    f"those businesses."),
        "why_it_matters": ("The level error today is small; the SENSITIVITY is the "
                           "problem. This channel is used to detect whether margins are "
                           "improving or deteriorating, so a denominator that grows for "
                           "unrelated reasons contaminates the time series, not just the "
                           "level."),
        "denominator_caveat": ("Premium income is policies x average premium, so it can "
                               "rise on price, mix or renewals with policy count falling. "
                               "A premium increase therefore supports neither 'margins "
                               "fell' nor 'more people bought insurance' — it is not "
                               "interpretable as revenue growth either."),
    }


def main() -> None:
    rows, wt = load()
    basis = pick_basis(rows)
    rows = [r for r in rows if r["basis"] == basis[r["symbol"]]]

    sector = {}
    if UNIVERSE.exists():
        with open(UNIVERSE, newline="", encoding="utf-8") as fh:
            sector = {r["Symbol"].strip().upper(): (r.get("Sector") or "").strip()
                      for r in csv.DictReader(fh) if r.get("Symbol")}

    all_syms = {r["symbol"] for r in rows}
    lenders = sorted({r["symbol"] for r in rows if r["template"] == "bank"})

    # THE RULE, not a list: a Financial Services company that Screener does NOT give the
    # lender template to has no comparable margin at all. That is the insurers and the
    # holding companies — their "sales" is premium income or dividend receipts, and
    # dividing an operating profit by it produces a ratio that cannot sit in the same
    # aggregate as a manufacturer's. Excluding them by sector+template keeps this correct
    # for any future universe instead of hardcoding four tickers.
    non_lender = all_syms - set(lenders)
    has_gen = {r["symbol"] for r in rows if r["metric"] in (GENERIC["num"], GENERIC["den"])}

    # not_applicable takes precedence: if the metric does not exist for the business
    # model, missing rows are beside the point.
    not_applicable = sorted(s for s in non_lender
                            if sector.get(s) in _NON_OPERATING_SECTORS)
    data_unavailable = sorted((non_lender - set(not_applicable)) - has_gen)
    generics = sorted(non_lender - set(not_applicable) - set(data_unavailable))

    doc = {
        "source": "screener_page_tables.csv (Screener.in company pages)",
        "basis_by_symbol": basis,
        "cohorts": {
            "lender": {"members": lenders,
                       "weight_pct": round(sum(wt.get(s, 0) for s in lenders), 2)},
            "generic": {"members": generics,
                        "weight_pct": round(sum(wt.get(s, 0) for s in generics), 2)},
        },
        "coverage": {
            "basis": "Nifty 50 index weight, from nifty-50-stock-list.csv",
            "measured_pct": round(sum(wt.get(s, 0) for s in generics + lenders), 2),
            "excluded_pct": round(sum(wt.get(s, 0) for s in not_applicable
                                      + data_unavailable), 2),
            "reason": "metric not applicable, not data unavailable",
            "not_applicable": {
                "weight_pct": round(sum(wt.get(s, 0) for s in not_applicable), 2),
                "meaning": ("The metric does not exist for these business models. A blank "
                            "is the correct answer; any number filling it would be wrong. "
                            "Not closeable by acquiring more data."),
                "members": [{"symbol": s, "weight_pct": wt.get(s, 0), **remediation(s)}
                            for s in not_applicable],
            },
            "data_unavailable": {
                "weight_pct": round(sum(wt.get(s, 0) for s in data_unavailable), 2),
                "meaning": ("The metric APPLIES to these; we are missing the rows. A blank "
                            "is a TODO with a known fix, not a property of the business."),
                "members": data_unavailable,
            },
        },
        "quarterly": {
            "generic": series_for(rows, set(generics), GENERIC, "quarters"),
            "lender": series_for(rows, set(lenders), LENDER, "quarters"),
        },
        "annual": {
            "generic": series_for(rows, set(generics), GENERIC, "pnl"),
            "lender": series_for(rows, set(lenders), LENDER, "pnl"),
        },
        "methodology": (
            "Margin coverage: 97.2% of Nifty weight. The remaining 2.8% is excluded from "
            "the aggregate operating-margin calculation because Operating Profit / Sales "
            "is not economically comparable for life insurers and financial holding "
            "companies. Their PROFIT remains included in the earnings analysis. This is "
            "metric exclusion, not missing data — the same company is in for one metric "
            "and out for another."),
        "three_distinct_uses_of_this_data": {
            "profit": "usable — enters the earnings panel unchanged",
            "premium_or_topline": "usable — measures insurance business growth on its own terms",
            "profit_over_premium_as_operating_margin": ("NOT usable — not comparable with a "
                                                        "conventional operating margin, and "
                                                        "excluded from the aggregate signal"),
        },
        "sensitivity_test": sensitivity_test(
            rows, set(generics), not_applicable, GENERIC,
            max((r["period"] for r in rows if r["section"] == "quarters"), default="")),
        "note": ("TWO cohorts, reported SEPARATELY and never summed. Operating margin "
                 "(operating profit / sales) and financing margin (financing profit / "
                 "revenue) are ratios over different denominators; blending them would "
                 "produce a figure with no referent, which is correction C26's error "
                 "committed in the opposite direction. Each series is balanced — periods "
                 "where any cohort member is missing are dropped (C24) — and every value "
                 "comes from the page scrape, never spliced with the Excel export, whose "
                 "Net Profit runs ~0.5% lower."),
    }

    OUT.write_text(json.dumps(doc, indent=1))

    print(f"wrote {OUT.name}\n")
    for cohort in ("generic", "lender"):
        c = doc["cohorts"][cohort]
        q = doc["quarterly"][cohort]
        print(f"{cohort.upper():9s} {len(c['members']):2d} names, "
              f"{c['weight_pct']:5.2f}% index weight")
        if q.get("available"):
            print(f"          {q['measure']} TTM: {q['latest_ttm_pct']}% at "
                  f"{q['latest_ttm_period']}   YoY {q['yoy_ttm_change_pp']:+.1f}pp"
                  f"   balanced panel {q['panel']}")
            print("          TTM  " + "  ".join(f"{r['period'][:7]} {r['margin_pct']:.1f}%"
                                                for r in q["ttm_series"][-5:]))
            print(f"          raw quarter {q['latest_quarter_pct']}% "
                  f"(YoY {q['yoy_quarter_change_pp']:+.1f}pp)   "
                  f"noise: QoQ {q['qoq_volatility_pp']}pp vs TTM {q['ttm_volatility_pp']}pp")
            if (q["qoq_volatility_pp"] or 0) > 1.5:
                print("          ^ quarterly series too noisy to read as a trend; "
                      "use the TTM line.")
        else:
            print(f"          NOT AVAILABLE: {q.get('reason')}")
        print()

    cov = doc["coverage"]
    na, du = cov["not_applicable"], cov["data_unavailable"]

    print(f"COVERAGE   {cov['measured_pct']:.1f}% of index weight measured")
    print(f"EXCLUDED   {cov['excluded_pct']:.1f}%")
    print(f"REASON     {cov['reason']}\n")

    if na["members"]:
        print(f"  NOT APPLICABLE  {na['weight_pct']:.1f}% — the metric does not exist for")
        print("  these business models. Not closeable by acquiring more data.")
        for m in na["members"]:
            print(f"    {m['symbol']:12s} {m['weight_pct']:.2f}%  {m['business_model']}")
            print(f"      -> {', '.join(m['applicable_metrics'][:3])}"
                  f"{' ...' if len(m['applicable_metrics']) > 3 else ''}")
            if not m["mapped"]:
                print("      (generic guidance — no specific mapping recorded for this name)")
    st = doc.get("sensitivity_test") or {}
    if st.get("available"):
        print(f"\n  SENSITIVITY TEST ({st['period']}, all profit held constant)")
        print(f"    the excluded cohort is {st['excluded_share_of_denominator_pct']}% of the "
              f"denominator but {st['excluded_share_of_numerator_pct']}% of the numerator")
        print(f"    {'excluded top-line growth':>26s}{'included':>11s}{'excluded':>11s}")
        for g in st["grid"]:
            print(f"    {g['excluded_topline_growth_pct']:24.0f}%  "
                  f"{g['aggregate_with_excluded_pct']:9.2f}%"
                  f"{g['aggregate_measured_only_pct']:10.2f}%")
        print("    Left column moves, right column cannot. No company earned a rupee")
        print("    more or less in any row — so that decline cannot be deterioration.")

    if du["members"]:
        print(f"\n  DATA UNAVAILABLE  {du['weight_pct']:.1f}% — metric applies, rows missing.")
        print(f"  This one IS closeable: {', '.join(du['members'])}")
    elif na["members"]:
        print("\n  DATA UNAVAILABLE  0.0% — nothing is excluded for want of data.")

    print(f"\n  (was {doc['cohorts']['generic']['weight_pct']:.2f}% measured under C26, "
          "non-financials only)")
    print("  Two series, reported separately. They are NOT added together.")


if __name__ == "__main__":
    main()
