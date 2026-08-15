#!/usr/bin/env python3
"""
it_fundamental_check.py — what landed in fundamentals.* for the 10 Nifty IT names, and are the
P/E + margin ingredients clean? IT analog of bank_fundamental_check + bank_data_diagnose, with
the lessons baked in:
  • IT uses CONSOLIDATED basis (opposite of banks) — subs are operating units, not valued separately.
  • MULTI-NAME P/E sanity vs rough street anchors (not one anchor) — catch a basis/units bug early.
No model. Confirms join key, granularity, basis values, line-items, per-name coverage, and a
latest-P/E smell test before we build it_factor_model.py.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python it_fundamental_check.py
"""
from __future__ import annotations
# --- single source for DB connections (D-SC-06, CLAUDE.md) ---
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import resolve_db_path, resolve_pg_dsn
import os, sqlite3, sys
try:
    import numpy as np, pandas as pd
except ImportError:
    sys.exit("needs numpy + pandas")
try:
    import psycopg
except ImportError:
    sys.exit("psycopg 3 required")
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

SQLITE_DB = resolve_db_path()
IT = ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"]
LAG_DAYS = 45
BASIS = "consolidated"          # IT primary basis
# rough street trailing P/E anchors (FY26, approximate) — smell test only, |off|>50% = SUSPECT
ANCHOR_PE = {"TCS": 26, "INFY": 25, "HCLTECH": 25, "WIPRO": 21, "TECHM": 32,
             "LTIM": 32, "PERSISTENT": 58, "COFORGE": 48, "MPHASIS": 32, "LTTS": 35}
NEED = {
    "eps":              ["eps", "earnings per share"],
    "net_profit (PAT)": ["net_profit", "profit after tax"],
    "shares":           ["shares"],
    "revenue":          ["revenue", "total revenue", "total income", "sales"],
    "operating_profit": ["operating_profit", "operating profit", "ebit", "ebitda"],
    "other_income":     ["other income"],
}
LI = {"eps": ["eps", "earnings per share"], "pat": ["net_profit", "profit after tax"],
      "shares": ["shares"], "revenue": ["revenue", "total revenue", "total income", "sales"],
      "opm": ["operating_profit", "operating profit", "ebit"]}


def canon(label):
    l = str(label).lower()
    for k, frags in LI.items():
        if any(f in l for f in frags):
            return k
    return None


def price_asof(px, b, when):
    if b not in px:
        return np.nan
    d, c = px[b]
    i = np.searchsorted(d, np.datetime64(pd.Timestamp(when).normalize()), side="right") - 1
    return float(c[i]) if i >= 0 else np.nan


def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    scon = sqlite3.connect(SQLITE_DB)
    px, missing_px = {}, []
    for b in IT:
        rows = scon.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                            "AND close IS NOT NULL ORDER BY ts", (b,)).fetchall()
        if rows:
            px[b] = (pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize().values,
                     np.asarray([float(r[1]) for r in rows]))
        else:
            missing_px.append(b)
    scon.close()

    conn = psycopg.connect(os.getenv("DATABASE_URL") or "")
    cur = conn.cursor()
    print("\n" + "=" * 88)
    print("  IT FUNDAMENTAL CHECK — 10 Nifty IT names (no model)")
    print("=" * 88)
    if missing_px:
        print(f"  ⚠ price_bars MISSING: {missing_px}")

    cur.execute("SELECT symbol, isin FROM fundamentals.companies WHERE symbol = ANY(%s)", (IT,))
    sym2isin = {s: i for s, i in cur.fetchall()}
    isin2sym = {v: k for k, v in sym2isin.items()}
    print(f"\n  [companies] {len(sym2isin)}/10 IT names mapped symbol↔isin"
          + (f"   MISSING: {[b for b in IT if b not in sym2isin]}" if len(sym2isin) < 10 else ""))

    # basis present
    cur.execute("SELECT DISTINCT basis FROM fundamentals.financials")
    print(f"  financials.basis values: {sorted(str(r[0]) for r in cur.fetchall() if r[0] is not None)}"
          f"   (IT will use '{BASIS}')")

    # pull IT financials on consolidated basis
    isins = [sym2isin[b] for b in IT if b in sym2isin]
    cur.execute("SELECT isin, period_end, section, line_item, value FROM fundamentals.financials "
                "WHERE time_period='yearly' AND basis=%s AND isin = ANY(%s)", (BASIS, isins))
    recs = []
    for isin, pend, section, li, val in cur.fetchall():
        c = canon(li)
        recs.append((isin2sym.get(isin), pd.Timestamp(pend).normalize(), section or "", c, li,
                     float(val) if val is not None else np.nan))
    fin = pd.DataFrame(recs, columns=["symbol", "fy_end", "section", "item", "raw", "value"])

    # distinct raw line items that loaded
    raws = sorted(fin["raw"].dropna().unique().tolist())
    print(f"\n  [financials/{BASIS}] distinct line_items ({len(raws)}): {raws[:40]}")

    # ingredient availability
    print("\n  [INGREDIENTS for P/E + margin]")
    pool = [str(r).lower() for r in raws]
    for role, cands in NEED.items():
        hit = sorted({raws[i] for i, p in enumerate(pool) if any(c in p for c in cands)})
        print(f"    {'✅' if hit else '❌'} {role:<20} → {hit if hit else 'NOT FOUND'}")

    # per-name coverage + latest-P/E smell test
    print("\n  [per-name coverage + latest P/E vs street anchor]  (SUSPECT if |off| > 50%)")
    print(f"    {'name':<12}{'yrs':>4}{'PAT':>10}{'shares':>9}{'price':>8}{'P/E':>7}{'anchor':>8}  flag")
    fw = (fin.dropna(subset=["item"]).sort_values("section")
          .drop_duplicates(["symbol", "fy_end", "item"], keep="first"))
    for b in IT:
        sb = fw[fw["symbol"] == b]
        if sb.empty:
            print(f"    {b:<12}  (no {BASIS} financials rows)"); continue
        w = sb.pivot_table(index="fy_end", columns="item", values="value", aggfunc="first")
        yrs = len(w)
        if not {"pat", "shares"}.issubset(w.columns):
            print(f"    {b:<12}{yrs:>4}  missing ingredients (have {sorted(w.columns)})"); continue
        w = w.dropna(subset=["pat", "shares"])
        if w.empty:
            print(f"    {b:<12}{yrs:>4}  no rows with pat+shares"); continue
        last, fy = w.iloc[-1], w.index[-1]
        pr = price_asof(px, b, fy + pd.Timedelta(days=LAG_DAYS))
        pe = pr * last["shares"] / last["pat"] if (last["pat"] and pr == pr) else np.nan
        a = ANCHOR_PE.get(b, np.nan)
        flag = ("SUSPECT" if (pe == pe and a == a and abs(pe - a) / a > 0.50) else "ok") if pe == pe else "—"
        def s(x, f="{:.1f}"):
            return f.format(x) if x == x else "—"
        print(f"    {b:<12}{yrs:>4}{s(last['pat'],'{:.0f}'):>10}{s(last['shares']):>9}"
              f"{s(pr,'{:.0f}'):>8}{s(pe):>7}{s(a,'{:.0f}'):>8}  {flag}")

    conn.close()
    print("\n  ── VERDICT LOGIC ──")
    print("   • All ingredients ✅ and P/E all 'ok' → build it_factor_model.py (P/E + margin, consolidated).")
    print("   • A ❌ ingredient → tell me its real line_item name; I map it.")
    print("   • P/E SUSPECT on a name → basis/units/EPS-adjustment issue on that name — fix before ranking")
    print("     (same class of bug as the bank consolidated-P/B distortion we just corrected).")


if __name__ == "__main__":
    main()
