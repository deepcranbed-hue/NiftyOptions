#!/usr/bin/env python3
"""
sector_scorecard.py — roll the per-company fundamentals up into a SECTOR read.

Reads the views in sector_scorecard.sql and produces, per sector, a structured
scorecard the app's fundamentals view can render:

    valuation (median P/E, P/B, EV/EBITDA + cheapest/richest constituents)
    quality   (median ROE, ROCE)
    earnings momentum (avg revenue & net-profit YoY / QoQ + a label)
    ownership (avg FII holding delta + who's being accumulated / distributed)
    setup_verdict (one-line "standing setup" — the slow, fundamental backdrop)

This is the FUNDAMENTAL layer only — the slow backdrop. The daily catalyst
(oil/USD/yields) and forward guidance (earnings news) are separate layers that
get combined in the attribution step (next piece).

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    python sector_scorecard.py                 # all sectors present
    python sector_scorecard.py --sector "IT"   # substring match
    python sector_scorecard.py --json          # emit JSON only (for the app)
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_pg_dsn

import argparse
import json
import os
import sys
from statistics import median

try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required: pip install "psycopg[binary]"')

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Heuristic "typical" P/E band per sector — a CALIBRATION input, not derived.
# Replace with a price-history percentile when the app supplies the price series.
SECTOR_PE_BANDS = {
    "NIFTY IT": (18, 26), "IT": (18, 26),
    # add other sectors as you expand: "NIFTY BANK": (12,18), "NIFTY FMCG": (40,55), ...
}

# Group by NSE index membership, NOT Upstox's per-company `profile.sector`
# (which is noisy: it split the IT names into "IT - Software"/"DVR"/"Engineering").
# This mirrors the app's constituents-registry view of sectors.
SECTOR_UNIVERSES = {
    "Nifty IT": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM",
                 "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"],
    # "Nifty Bank": [...], "Nifty FMCG": [...], ... as you load more sectors
}


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(median(xs), 2) if xs else None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def pe_band(sector):
    if not sector:
        return None
    key = sector.upper()
    for k, band in SECTOR_PE_BANDS.items():
        if k in key or key in k:
            return band
    return None


def build_scorecard(rows, sector):
    """rows: list of dict per company (already filtered to one sector)."""
    n = len(rows)
    fnum = lambda k: [float(r[k]) for r in rows if r.get(k) is not None]

    med_pe = _median(fnum("pe"))
    band = pe_band(sector)
    level = None
    if med_pe is not None and band:
        level = "cheap" if med_pe < band[0] else "rich" if med_pe > band[1] else "fair"

    by_pe = sorted([r for r in rows if r.get("pe") is not None], key=lambda r: float(r["pe"]))
    cheapest = [(r["symbol"], round(float(r["pe"]), 1)) for r in by_pe[:3]]
    richest = [(r["symbol"], round(float(r["pe"]), 1)) for r in by_pe[-3:][::-1]]

    # median (not mean) so one outlier — e.g. COFORGE's FII swing — can't skew the sector
    rev_yoy, np_yoy = _median(fnum("rev_yoy")), _median(fnum("np_yoy"))
    rev_qoq, np_qoq = _median(fnum("rev_qoq")), _median(fnum("np_qoq"))
    if rev_yoy is None:
        mom_label = "insufficient history"
    elif rev_yoy > 0 and (np_yoy or 0) < 0:
        mom_label = "revenue growing, earnings under pressure"
    elif rev_yoy > 0 and (np_yoy or 0) >= 0:
        mom_label = "growing (revenue + earnings)"
    else:
        mom_label = "contracting"

    fii_delta = _median(fnum("fii_delta"))
    if fii_delta is None:
        own_label = "no ownership history"
    elif fii_delta <= -0.3:
        own_label = "FIIs distributing"
    elif fii_delta >= 0.3:
        own_label = "FIIs accumulating"
    else:
        own_label = "FII holding ~stable"
    distributing = sorted([(r["symbol"], float(r["fii_delta"])) for r in rows
                           if r.get("fii_delta") is not None and float(r["fii_delta"]) < 0],
                          key=lambda t: t[1])[:3]
    accumulating = sorted([(r["symbol"], float(r["fii_delta"])) for r in rows
                           if r.get("fii_delta") is not None and float(r["fii_delta"]) > 0],
                          key=lambda t: -t[1])[:3]

    med_roe = _median(fnum("roe"))

    # one-line standing-setup verdict
    bits = []
    if level:
        bits.append(f"{level} (median P/E {med_pe})")
    elif med_pe is not None:
        bits.append(f"median P/E {med_pe}")
    if med_roe is not None:
        bits.append(f"{'high' if med_roe >= 20 else 'moderate'}-quality (ROE {med_roe}%)")
    if rev_yoy is not None:
        bits.append(mom_label)
    if fii_delta is not None:
        bits.append(own_label.lower())
    verdict = "; ".join(bits) if bits else "insufficient data"

    return {
        "sector": sector,
        "n_companies": n,
        "valuation": {"median_pe": med_pe, "median_pb": _median(fnum("pb")),
                      "median_ev_ebitda": _median(fnum("ev_ebitda")),
                      "band": list(band) if band else None, "level": level,
                      "cheapest": cheapest, "richest": richest,
                      "note": "band is a heuristic calibration; swap for price-history percentile"},
        "quality": {"median_roe": med_roe, "median_roce": _median(fnum("roce"))},
        "earnings_momentum": {"rev_yoy_avg": rev_yoy, "np_yoy_avg": np_yoy,
                              "rev_qoq_avg": rev_qoq, "np_qoq_avg": np_qoq, "label": mom_label},
        "ownership": {"fii_delta_avg": fii_delta, "label": own_label,
                      "distributing": distributing, "accumulating": accumulating},
        "setup_verdict": verdict,
        "companies": sorted(
            [{"symbol": r["symbol"],
              "pe": _f(r.get("pe")), "roe": _f(r.get("roe")),
              "rev_yoy": _f(r.get("rev_yoy")), "np_yoy": _f(r.get("np_yoy")),
              "fii_delta": _f(r.get("fii_delta"))} for r in rows],
            key=lambda d: (d["pe"] is None, d["pe"] or 0)),
    }


def _f(v):
    return None if v is None else round(float(v), 2)


def fetch(conn):
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM fundamentals.v_company_scorecard")
        return cur.fetchall()


def group_by_universe(rows):
    """Group by NSE index membership (SECTOR_UNIVERSES), not Upstox's sector field."""
    by_sym = {r["symbol"]: r for r in rows}
    assigned, out = set(), {}
    for uni, members in SECTOR_UNIVERSES.items():
        present = [by_sym[s] for s in members if s in by_sym]
        if present:
            out[uni] = present
            assigned.update(members)
    for r in rows:                       # anything not in a defined index -> raw sector
        if r["symbol"] not in assigned:
            out.setdefault(r.get("sector") or "Unassigned", []).append(r)
    return out


def print_human(sc):
    v, q, m, o = sc["valuation"], sc["quality"], sc["earnings_momentum"], sc["ownership"]
    print(f"\n=== {sc['sector']}  ({sc['n_companies']} companies) ===")
    print(f"  Valuation : median P/E {v['median_pe']}  P/B {v['median_pb']}  "
          f"EV/EBITDA {v['median_ev_ebitda']}   [{v['level'] or 'no band'}]")
    print(f"              cheapest {v['cheapest']}  |  richest {v['richest']}")
    print(f"  Quality   : median ROE {q['median_roe']}%   ROCE {q['median_roce']}%")
    print(f"  Momentum  : revenue YoY {m['rev_yoy_avg']}%  net-profit YoY {m['np_yoy_avg']}%  "
          f"-> {m['label']}")
    print(f"  Ownership : FII delta {o['fii_delta_avg']}pp -> {o['label']}")
    if o["distributing"]:
        print(f"              being sold: {o['distributing']}")
    print(f"  VERDICT   : {sc['setup_verdict']}")
    print(f"  {'sym':<12}{'P/E':>8}{'ROE%':>8}{'RevYoY':>9}{'NPyoY':>9}{'FIIΔ':>8}")
    for c in sc["companies"]:
        print(f"  {c['symbol']:<12}{_s(c['pe']):>8}{_s(c['roe']):>8}"
              f"{_s(c['rev_yoy']):>9}{_s(c['np_yoy']):>9}{_s(c['fii_delta']):>8}")


def _s(v):
    return "—" if v is None else f"{v}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sector", help="substring filter on index name (e.g. IT)")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()

    dsn = os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    rows = fetch(conn)
    conn.close()
    if not rows:
        sys.exit("No companies found (load fundamentals first).")

    groups = group_by_universe(rows)
    if args.sector:
        groups = {k: v for k, v in groups.items() if args.sector.upper() in k.upper()}
        if not groups:
            sys.exit(f"No index matching '{args.sector}'.")

    cards = [build_scorecard(rws, name) for name, rws in groups.items()]
    if args.json:
        print(json.dumps(cards if len(cards) > 1 else cards[0], indent=2, default=str))
    else:
        for c in cards:
            print_human(c)
        print("\n--- JSON (for the app) ---")
        print(json.dumps(cards if len(cards) > 1 else cards[0], indent=2, default=str))


if __name__ == "__main__":
    main()
