#!/usr/bin/env python3
"""
fundamental_data_audit.py — is the fundamentals DB deep and clean enough to backtest?

The `gate_validation.py` of DATA. NO model, NO prediction — PASS / FAIL / FLAG on
backtest-readiness, so we learn whether the bottleneck is modelling or data acquisition
*before* building anything. (§6.6 build order, step 1.)

CHECKS
  1 History depth          quarters of financials per company (need years, not quarters)
  2 Point-in-time integrity is there an announcement/report date, or only period_end?
  3 Survivorship           do we have HISTORICAL Nifty IT membership, or only today's 10?
  4 Coverage               how many constituents actually have data
  5 Factor availability    which PIT-safe factors are computable (valuation/growth/ROE/FII)
  6 Cross-sectional N      companies × quarters (the real sample for a factor test)

Structural (slow) factors are what a fundamental base model uses; the software regime is
a TACTICAL factor added LAST (§6.6). This audit only covers the structural/fundamental side.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    python fundamental_data_audit.py
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_pg_dsn
import os
import sys
import pandas as pd

try:
    import psycopg
except ImportError:
    sys.exit('psycopg 3 required: pip install "psycopg[binary]"')
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SCHEMA = "fundamentals"
NIFTY_IT = ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM", "PERSISTENT",
            "COFORGE", "MPHASIS", "LTTS"]
# PIT-safe classification (from the discussion) — audited, not assumed
PIT_SAFE = {"Price": "✅", "Market Cap": "✅", "Trailing P/E": "✅ (reporting lag)",
            "Revenue growth": "✅ (lagged)", "ROE": "✅ (lagged)", "Margins": "✅ (lagged)",
            "FII holding": "✅ if dated", "EPS *revision*": "❌ needs estimate history",
            "Guidance": "❌", "Analyst target": "❌"}
# readiness thresholds
DEPTH_OK, DEPTH_WEAK = 20, 8          # quarters/company (5yr / 2yr)
XS_OK, XS_WEAK = 200, 80             # company×quarter observations


def connect():
    dsn = os.getenv("DATABASE_URL")
    return psycopg.connect(dsn) if dsn else psycopg.connect()


def q(cur, sql, args=()):
    try:
        cur.execute(sql, args)
        return cur.fetchall()
    except Exception as e:
        return [("__error__", str(e))]


def cols_of(cur, table):
    rows = q(cur, "SELECT column_name FROM information_schema.columns "
                  "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
             (SCHEMA, table))
    return [r[0] for r in rows if r and r[0] != "__error__"]


def tables(cur):
    rows = q(cur, "SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (SCHEMA,))
    return sorted(r[0] for r in rows if r and r[0] != "__error__")


def mark(ok):  # PASS/WEAK/FAIL/FLAG -> glyph
    return {"PASS": "✅", "WEAK": "⚠️", "FAIL": "❌", "FLAG": "🖐", "N/A": "—"}.get(ok, "•")


def main():
    conn = connect()
    cur = conn.cursor()
    verdicts = {}
    print("\n=== FUNDAMENTAL DATA AUDIT — backtest readiness (no model) ===")

    tbls = tables(cur)
    print(f"  schema `{SCHEMA}` tables: {tbls or 'NONE FOUND'}")
    if "financials" not in tbls:
        sys.exit(f"  ❌ no `{SCHEMA}.financials` table — nothing to audit.")

    fcols = cols_of(cur, "financials")
    print(f"  financials columns: {fcols}")

    # symbol -> isin map, restricted to the Nifty IT universe present
    comp = q(cur, f"SELECT symbol, isin FROM {SCHEMA}.companies")
    sym2isin = {s: i for s, i in comp if s != "__error__"}
    present_syms = [s for s in NIFTY_IT if s in sym2isin]
    universe_it = tuple(present_syms)
    print(f"\n  Nifty IT universe present in `companies`: {len(present_syms)}/{len(NIFTY_IT)}  {present_syms}")

    # ---- 1 History depth (quarterly) ------------------------------------------------
    print("\n  [1] HISTORY DEPTH — quarterly & yearly financials per company")
    in_clause = "'" + "','".join(universe_it) + "'"
    
    q_depth = pd.read_sql(f"""
        SELECT c.symbol,
               COUNT(DISTINCT f.period_end) AS quarters,
               MIN(f.period_end) AS start_q,
               MAX(f.period_end) AS end_q
        FROM {SCHEMA}.companies c
        JOIN {SCHEMA}.financials f ON f.isin = c.isin
        WHERE c.symbol IN ({in_clause}) AND f.time_period = 'quarterly'
        GROUP BY c.symbol
    """, conn)
    
    y_depth = pd.read_sql(f"""
        SELECT c.symbol,
               COUNT(DISTINCT f.period_end) AS years,
               MIN(f.period_end) AS start_y,
               MAX(f.period_end) AS end_y
        FROM {SCHEMA}.companies c
        JOIN {SCHEMA}.financials f ON f.isin = c.isin
        WHERE c.symbol IN ({in_clause}) AND f.time_period = 'yearly'
        GROUP BY c.symbol
    """, conn)

    for _, row in q_depth.iterrows():
        y_row = y_depth[y_depth['symbol'] == row['symbol']]
        y_str = ""
        if not y_row.empty:
            y_str = f" | {y_row.iloc[0]['years']} years ({y_row.iloc[0]['start_y']} → {y_row.iloc[0]['end_y']})"
        print(f"      {row['symbol']:<13} {row['quarters']:>2} quarters   {row['start_q']} → {row['end_q']}{y_str}")

    median_q = q_depth['quarters'].median() if not q_depth.empty else 0
    median_y = y_depth['years'].median() if not y_depth.empty else 0
    if median_y >= 5: # 5 years for backtest
        print(f"      → median {median_y:.0f} years/company  ✅ PASS  (5-yr backtest possible on annual data)")
        verdicts["History depth"] = "PASS"
    elif median_q >= 20:
        print(f"      → median {median_q:.0f} quarters/company  ✅ PASS  (5-yr backtest possible on quarterly data)")
        verdicts["History depth"] = "PASS"
    elif median_q < 8 and median_y < 5:
        print(f"      → median {median_q:.0f} quarters/company  ⚠️ WEAK  (need ≥20 for a 5-yr backtest; <8 ⇒ data acquisition)")
        verdicts["History depth"] = "WEAK"
    else:
        print(f"      → median {median_q:.0f} quarters / {median_y:.0f} years  ⚠️ MARGINAL")
        verdicts["History depth"] = "WEAK"

    # ---- 2 Point-in-time integrity --------------------------------------------------
    print("\n  [2] POINT-IN-TIME — announcement/report date present?")
    pit_cols = [c for c in fcols if any(k in c.lower() for k in
                ("announce", "report", "filing", "publish", "disclosed", "result_date"))]
    if pit_cols:
        verdicts["Point-in-time"] = "PASS"
        print(f"      {mark('PASS')} PASS — date column(s): {pit_cols}")
    else:
        verdicts["Point-in-time"] = "FLAG"
        print(f"      {mark('FLAG')} FLAG — only `period_end` (the quarter it's FOR, not when it "
              f"was announced).\n            Approximate availability = period_end + ~45d lag; "
              f"acceptable but must be explicit.")

    # ---- 3 Survivorship — historical constituent membership -------------------------
    print("\n  [3] SURVIVORSHIP — historical Nifty IT membership")
    memb = [t for t in tbls if "member" in t or "constituent" in t or "index_hist" in t]
    if memb:
        verdicts["Survivorship"] = "PASS"
        print(f"      {mark('PASS')} PASS — membership-history table(s): {memb}")
    else:
        verdicts["Survivorship"] = "FLAG"
        print(f"      {mark('FLAG')} FLAG — only TODAY's constituents are loaded. Projecting them "
              f"backward is survivorship bias\n            (dropped/added names invisible). Source a "
              f"historical NIFTY IT membership series before trusting cross-sectional results.")

    # ---- 4 Coverage -----------------------------------------------------------------
    print("\n  [4] COVERAGE — constituents with usable income data")
    covered = [s for s in present_syms if depth.get(s, 0) > 0]
    v = "PASS" if len(covered) >= 8 else ("WEAK" if len(covered) >= 5 else "FAIL")
    verdicts["Coverage"] = v
    print(f"      {mark(v)} {v} — {len(covered)}/{len(NIFTY_IT)} constituents have financials")

    # ---- 5 Factor availability (PIT-safe) -------------------------------------------
    print("\n  [5] FACTOR AVAILABILITY (PIT-safe classification):")
    for f, tag in PIT_SAFE.items():
        print(f"      {tag:<22} {f}")
    # what's actually in the DB to compute the ✅ ones
    line_items = [r[0] for r in q(cur, f"SELECT DISTINCT line_item FROM {SCHEMA}.financials "
                                       f"WHERE time_period='quarterly' LIMIT 60") if r[0] != "__error__"]
    ratios = [r[0] for r in q(cur, f"SELECT DISTINCT ratio FROM {SCHEMA}.key_ratios LIMIT 60")
              if r and r[0] != "__error__"] if "key_ratios" in tbls else []
    sh_cats = [r[0] for r in q(cur, f"SELECT DISTINCT category FROM {SCHEMA}.shareholding LIMIT 30")
               if r and r[0] != "__error__"] if "shareholding" in tbls else []
    sh_cols = cols_of(cur, "shareholding") if "shareholding" in tbls else []
    print(f"      financials line_items (sample): {line_items[:12]}")
    print(f"      key_ratios present: {ratios[:12]}")
    print(f"      shareholding categories: {sh_cats}   (columns: {sh_cols})")
    fii_dated = "period_end" in sh_cols and any("fii" in str(c).lower() for c in sh_cats)
    print(f"      → FII usable as a dated trend? {'yes' if fii_dated else 'NO (undated or absent)'}")

    # ---- 6 Cross-sectional sample size ----------------------------------------------
    print("\n  [6] CROSS-SECTIONAL N — companies × quarters (the real factor sample)")
    xs = sum(depth.values())
    v = "PASS" if xs >= XS_OK else ("WEAK" if xs >= XS_WEAK else "FAIL")
    verdicts["Cross-sectional N"] = v
    print(f"      {mark(v)} {v} — {xs} company-quarter observations"
          f"  (rank-IC on 3/6-mo fwd needs ≥{XS_OK}; <{XS_WEAK} ⇒ underpowered)")

    # ---- verdict --------------------------------------------------------------------
    print("\n  ── READINESS ──")
    for k, vv in verdicts.items():
        print(f"    {mark(vv)} {k:<18} {vv}")
    blocking = [k for k, vv in verdicts.items() if vv == "FAIL"]
    weak = [k for k, vv in verdicts.items() if vv == "WEAK"]
    if blocking:
        print(f"\n  VERDICT: ❌ DATA ACQUISITION REQUIRED — blocking: {blocking}.")
        print("           The bottleneck is DATA, not modelling. Source deeper/point-in-time")
        print("           fundamentals (e.g. screener.in / Capitaline / annual reports) first.")
    elif weak:
        print(f"\n  VERDICT: ⚠️ MARGINAL — {weak} underpowered. A 1-2 factor cross-sectional test is")
        print("           defensible but treat results as provisional; deeper data strongly preferred.")
    else:
        print("\n  VERDICT: ✅ READY — build the small cross-sectional base (1-2 PIT-safe factors,")
        print("           reporting-lagged, forward 60/120d, rank-IC + per-regime). Software regime LAST.")
    print("\n  (Point-in-time FLAG and Survivorship FLAG are approximations, not blockers — but every")
    print("   result inherits their caveat until a dated feed + membership history exist.)")
    conn.close()


if __name__ == "__main__":
    main()
