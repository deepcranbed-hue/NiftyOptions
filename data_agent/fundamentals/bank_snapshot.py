#!/usr/bin/env python3
"""
bank_snapshot.py — FACTUAL current state of each Nifty Bank name (no prediction, no score).
Reuses bank_factor_model.py's P/B plumbing (net_worth = equity_capital+reserves, split-adjusted
shares, price_bars adjusted close). For each bank, latest available:
  P/B  +  where it sits in the bank's OWN 2017-26 range (percentile: cheap vs its own history)
  GNPA latest + 1yr change (improving = falling)   ·   NIM · PCR · ROA
Sorted cheapest-first. This is the layer you pair with the regime read — NOT a buy/sell tag.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python bank_snapshot.py
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
NICE_SPLITS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0]
LI = {"equity_capital": ["equity_capital", "equity capital"], "reserves": ["reserves"],
      "shares": ["shares"], "total_assets": ["total assets"],
      "pat": ["net_profit", "profit after tax"], "revenue": ["revenue"]}


def canon(label):
    l = str(label).lower()
    for k, frags in LI.items():
        if any(f in l for f in frags):
            return k
    return None


def load_prices(con):
    out = {}
    for b in BANKS:
        rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                           "AND close IS NOT NULL ORDER BY ts", (b,)).fetchall()
        if rows:
            d = pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize().values
            out[b] = (d, np.asarray([float(r[1]) for r in rows]))
    return out


def price_asof(px, b, when):
    if b not in px:
        return np.nan
    d, c = px[b]
    i = np.searchsorted(d, np.datetime64(pd.Timestamp(when).normalize()), side="right") - 1
    return float(c[i]) if i >= 0 else np.nan


def adjust_shares(s, nw):
    factor = np.ones(len(s))
    for t in range(1, len(s)):
        if s[t - 1] and s[t] and s[t] / s[t - 1] >= 1.4:
            raw = s[t] / s[t - 1]
            nwr = (nw[t] / nw[t - 1]) if (nw[t - 1] and nw[t] and nw[t - 1] > 0 and nw[t] > 0) else float("nan")
            if nwr == nwr and nwr >= 1.25:
                continue
            snap = min(NICE_SPLITS, key=lambda r: abs(r - raw))
            if abs(snap - raw) / snap <= 0.06:
                factor[t] = snap
    adj = s.copy()
    for i in range(len(s)):
        adj[i] = s[i] * float(np.prod(factor[i + 1:])) if i + 1 < len(s) else s[i]
    return adj


def main():
    if not os.path.exists(SQLITE_DB):
        sys.exit(f"SQLite not found: {SQLITE_DB}")
    scon = sqlite3.connect(SQLITE_DB)
    px = load_prices(scon)
    scon.close()

    conn = psycopg.connect(os.getenv("DATABASE_URL") or "")
    cur = conn.cursor()
    cur.execute("SELECT symbol, isin FROM fundamentals.companies WHERE symbol = ANY(%s)", (BANKS,))
    sym2isin = {s: i for s, i in cur.fetchall()}
    isin2sym = {v: k for k, v in sym2isin.items()}
    # BANKS: standalone ONLY (consolidated bloats net worth with subs → collapses P/B).
    cur.execute("SELECT isin, period_end, section, line_item, value FROM fundamentals.financials "
                "WHERE time_period='yearly' AND basis='standalone' AND isin = ANY(%s)",
                ([sym2isin[b] for b in BANKS if b in sym2isin],))
    recs = []
    for isin, pend, section, li, val in cur.fetchall():
        c = canon(li)
        if c and val is not None:
            recs.append((isin2sym.get(isin), pd.Timestamp(pend).normalize(), section or "", c, float(val)))
    fin = pd.DataFrame(recs, columns=["symbol", "fy_end", "section", "item", "value"])
    fin = fin.sort_values("section").drop_duplicates(["symbol", "fy_end", "item"], keep="first")
    fin = fin.pivot_table(index=["symbol", "fy_end"], columns="item", values="value", aggfunc="first").reset_index()

    cur.execute("SELECT symbol, period_end, gnpa_pct, nnpa_pct, pcr_pct, nim_pct "
                "FROM fundamentals.asset_quality WHERE symbol = ANY(%s)", (BANKS,))
    aq = pd.DataFrame(cur.fetchall(), columns=["symbol", "period_end", "gnpa", "nnpa", "pcr", "nim"])
    aq["period_end"] = pd.to_datetime(aq["period_end"]).dt.normalize()
    conn.close()

    # P/B time series per bank
    fin["net_worth"] = fin.get("equity_capital", np.nan) + fin.get("reserves", np.nan)
    rows = []
    for b, g in fin.groupby("symbol"):
        g = g.sort_values("fy_end").copy()
        if "shares" not in g or not g["shares"].notna().any():
            continue
        g["adj_shares"] = adjust_shares(g["shares"].to_numpy(float), g["net_worth"].to_numpy(float))
        g["avail"] = g["fy_end"] + pd.Timedelta(days=LAG_DAYS)
        g["price"] = [price_asof(px, b, d) for d in g["avail"]]
        g["pb"] = g["price"] * g["adj_shares"] / g["net_worth"]
        g["roa"] = g.get("pat", np.nan) / g.get("total_assets", np.nan) * 100.0
        g = g.dropna(subset=["pb"])
        if g.empty:
            continue
        last = g.iloc[-1]
        hist = g["pb"].to_numpy()
        pct = float((hist < last["pb"]).mean()) if len(hist) > 1 else float("nan")
        # asset quality: latest + 1yr(4q) change
        aqb = aq[aq["symbol"] == b].sort_values("period_end")
        gnpa = gnpa_chg = nim = pcr = float("nan")
        if not aqb.empty:
            gnpa = aqb["gnpa"].iloc[-1]
            nim = aqb["nim"].iloc[-1]
            pcr = aqb["pcr"].iloc[-1]
            if len(aqb) >= 5:
                gnpa_chg = aqb["gnpa"].iloc[-1] - aqb["gnpa"].iloc[-5]
        rows.append(dict(bank=b, date=last["fy_end"].date(), pb=last["pb"], pb_pct=pct,
                         roa=last["roa"], gnpa=gnpa, gnpa_chg=gnpa_chg, nim=nim, pcr=pcr,
                         yrs=len(hist)))

    snap = pd.DataFrame(rows).sort_values("pb")
    print("\n" + "=" * 92)
    print("  BANK SNAPSHOT — factual current state (P/B vs OWN history + asset quality). NOT a call.")
    print("=" * 92)
    print(f"  regime read: benign/normalization (system GNPA ~falling) → validated cross-sectional")
    print(f"  P/B edge ≈ 0 in THIS regime. Differentiation now is idiosyncratic, not the factor.\n")
    hdr = f"  {'bank':<12}{'P/B':>6}{'vs own hist':>13}{'GNPA%':>7}{'Δ1yr':>7}{'NIM%':>6}{'PCR%':>6}{'ROA%':>6}  quality"
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for _, r in snap.iterrows():
        pos = ("cheapest 3rd" if r["pb_pct"] <= .33 else "dearest 3rd" if r["pb_pct"] >= .67
               else "mid") if r["pb_pct"] == r["pb_pct"] else "n/a"
        q = ("improving" if r["gnpa_chg"] < -0.05 else "deteriorating" if r["gnpa_chg"] > 0.05
             else "stable") if r["gnpa_chg"] == r["gnpa_chg"] else "n/a"
        def s(x, f="{:.2f}"):
            return f.format(x) if x == x else "  —"
        print(f"  {r['bank']:<12}{s(r['pb']):>6}{pos:>13}{s(r['gnpa'],'{:.2f}'):>7}"
              f"{s(r['gnpa_chg'],'{:+.2f}'):>7}{s(r['nim']):>6}{s(r['pcr'],'{:.0f}'):>6}"
              f"{s(r['roa']):>6}  {q}")
    print("\n  READING IT: 'cheapest 3rd' + 'improving' = the re-rating candidate our finding likes")
    print("  (but the edge is ~0 in this regime — treat as a watchlist tilt, verify each name live).")
    print("  'cheapest 3rd' + 'deteriorating' = value-trap WATCH (cheap for a reason).")
    print("  P/B vs own history matters more than absolute P/B — each bank re-rates within its OWN band.")


if __name__ == "__main__":
    main()
