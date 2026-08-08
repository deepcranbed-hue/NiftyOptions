#!/usr/bin/env python3
"""
bank_data_diagnose.py — settle CHECK-vs-REDOWNLOAD for the bank P/B data. No model.
Answers three things so you re-fetch ONLY what's actually missing:
  (1) STANDALONE vs CONSOLIDATED — the `basis` column. Computes each bank's latest P/B under
      EACH basis and compares to a rough street anchor. If 'standalone' matches and 'consolidated'
      is the one giving Kotak ~1.0x, the fix is a FILTER (no download).
  (2) NIM sanity — dumps asset_quality.nim_pct latest per bank; flags impossible (<0 or >8).
  (3) AU / BANDHAN coverage — do their net_worth + shares ingredients even exist?

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python bank_data_diagnose.py
"""
from __future__ import annotations
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

SQLITE_DB = os.getenv("OPTION_CHAINS_DB",
    "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")
BANKS = ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
         "BANKBARODA", "PNB", "AUBANK", "IDFCFIRSTB", "FEDERALBNK", "BANDHANBNK"]
LAG_DAYS = 45
# rough street P/B anchors (FY26, approximate) — for sanity flagging only, |off|>40% = SUSPECT
ANCHOR = {"HDFCBANK": 2.0, "ICICIBANK": 2.9, "SBIN": 1.5, "KOTAKBANK": 2.8, "AXISBANK": 1.9,
          "INDUSINDBK": 1.1, "BANKBARODA": 1.1, "PNB": 1.0, "AUBANK": 3.5, "IDFCFIRSTB": 1.3,
          "FEDERALBNK": 1.3, "BANDHANBNK": 1.2}
LI = {"equity_capital": ["equity_capital", "equity capital"], "reserves": ["reserves"], "shares": ["shares"]}


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
    px = {}
    for b in BANKS:
        rows = scon.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                            "AND close IS NOT NULL ORDER BY ts", (b,)).fetchall()
        if rows:
            px[b] = (pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize().values,
                     np.asarray([float(r[1]) for r in rows]))
    scon.close()

    conn = psycopg.connect(os.getenv("DATABASE_URL") or "")
    cur = conn.cursor()
    cur.execute("SELECT symbol, isin FROM fundamentals.companies WHERE symbol = ANY(%s)", (BANKS,))
    sym2isin = {s: i for s, i in cur.fetchall()}
    isin2sym = {v: k for k, v in sym2isin.items()}

    print("\n" + "=" * 88)
    print("  BANK DATA DIAGNOSE — check-vs-redownload (no model)")
    print("=" * 88)

    # (0) distinct basis
    cur.execute("SELECT DISTINCT basis FROM fundamentals.financials")
    bases = sorted(str(r[0]) for r in cur.fetchall() if r[0] is not None)
    print(f"\n  (0) financials.basis values present: {bases}")

    # (1) per-bank P/B under EACH basis vs anchor
    cur.execute("SELECT isin, period_end, basis, section, line_item, value FROM fundamentals.financials "
                "WHERE time_period='yearly' AND isin = ANY(%s)", ([sym2isin[b] for b in BANKS if b in sym2isin],))
    recs = []
    for isin, pend, basis, section, li, val in cur.fetchall():
        c = canon(li)
        if c and val is not None:
            recs.append((isin2sym.get(isin), pd.Timestamp(pend).normalize(), str(basis), section or "", c, float(val)))
    fin = pd.DataFrame(recs, columns=["symbol", "fy_end", "basis", "section", "item", "value"])

    print("\n  (1) LATEST P/B under each basis vs street anchor  (SUSPECT if |off| > 40%)")
    print(f"      {'bank':<12}{'basis':<13}{'net_worth':>11}{'shares':>10}{'price':>8}{'P/B':>7}{'anchor':>8}  flag")
    for b in BANKS:
        sub = fin[fin["symbol"] == b]
        if sub.empty:
            print(f"      {b:<12}  (no financials rows)")
            continue
        for basis in sorted(sub["basis"].unique()):
            sb = sub[sub["basis"] == basis].sort_values("section").drop_duplicates(["fy_end", "item"], keep="first")
            w = sb.pivot_table(index="fy_end", columns="item", values="value", aggfunc="first")
            if not {"equity_capital", "reserves", "shares"}.issubset(w.columns):
                print(f"      {b:<12}{basis:<13}  (missing ingredients: have {sorted(w.columns)})")
                continue
            w = w.dropna(subset=["equity_capital", "reserves", "shares"])
            if w.empty:
                continue
            last = w.iloc[-1]; fy = w.index[-1]
            nw = last["equity_capital"] + last["reserves"]
            pr = price_asof(px, b, fy + pd.Timedelta(days=LAG_DAYS))
            pb = pr * last["shares"] / nw if (nw and pr == pr) else float("nan")
            a = ANCHOR.get(b, float("nan"))
            flag = ""
            if pb == pb and a == a:
                flag = "SUSPECT" if abs(pb - a) / a > 0.40 else "ok"
            print(f"      {b:<12}{basis:<13}{nw:>11.0f}{last['shares']:>10.1f}{pr:>8.0f}"
                  f"{pb:>7.2f}{a:>8.1f}  {flag}")

    # (2) NIM sanity
    print("\n  (2) asset_quality.nim_pct latest per bank  (IMPOSSIBLE if <0 or >8)")
    cur.execute("SELECT symbol, period_end, nim_pct, gnpa_pct FROM fundamentals.asset_quality "
                "WHERE symbol = ANY(%s) ORDER BY symbol, period_end", (BANKS,))
    aq = pd.DataFrame(cur.fetchall(), columns=["symbol", "period_end", "nim", "gnpa"])
    for b in BANKS:
        s = aq[aq["symbol"] == b]
        if s.empty:
            print(f"      {b:<12}  (no asset_quality rows)"); continue
        nim = s["nim"].iloc[-1]
        bad = "  ❌ IMPOSSIBLE" if (nim is not None and (nim < 0 or nim > 8)) else ""
        print(f"      {b:<12} nim={nim}  gnpa={s['gnpa'].iloc[-1]}{bad}")

    # (3) AU / BANDHAN coverage
    print("\n  (3) AU / BANDHAN ingredient coverage (why they dropped from the snapshot)")
    for b in ("AUBANK", "BANDHANBNK"):
        sub = fin[fin["symbol"] == b]
        have = sorted(sub["item"].unique()) if not sub.empty else []
        yrs = sub["fy_end"].dt.year.nunique() if not sub.empty else 0
        print(f"      {b:<12} items={have}  distinct_years={yrs}")

    conn.close()
    print("\n  ── VERDICT LOGIC ──")
    print("   • If a 'standalone' basis exists AND its P/B ≈ anchor while 'consolidated' is the SUSPECT")
    print("     one → FIX = filter basis='standalone', NO re-download.")
    print("   • If only 'consolidated' (or no basis split) exists and P/B is SUSPECT → re-download STANDALONE.")
    print("   • NIM ❌ everywhere → nim_pct is the wrong field; re-scrape NIM or drop it (GNPA/PCR are core).")
    print("   • AU/BANDHAN missing shares/net_worth → re-download their financials (full history).")


if __name__ == "__main__":
    main()
