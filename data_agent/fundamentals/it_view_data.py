#!/usr/bin/env python3
"""
it_view_data.py — data extractor for the Nifty IT sector view (sibling of bank_view_data.py).
IT is valued on P/E (not P/B) with margin + growth as the quality axis. CONSOLIDATED basis
(IT holdcos' subs are operating units). Emits it_view.json for /api/sector-view/it.

Per stock: current P/E (= live price × shares ÷ PAT, split-consistent) + where it sits in its
own history · operating margin % · revenue growth YoY % · ROE % · EPS (₹) · 1W/1M/6M returns
(+ vs NIFTYIT). NO regime factor — that is bank-specific; IT stance is expectation + momentum.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python it_view_data.py
"""
from __future__ import annotations
import os, sqlite3, sys, json
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
IT = ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"]
INDEX = "NIFTYIT"                 # in price_bars (relative returns); backend uses ^CNXIT for live
BASIS = "consolidated"
LAG_DAYS = 45
NICE_SPLITS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0]
# rough street trailing P/E anchors (FY26, approximate) — pre-flight sanity only
ANCHOR_PE = {"TCS": 26, "INFY": 25, "HCLTECH": 25, "WIPRO": 21, "TECHM": 32,
             "LTIM": 32, "PERSISTENT": 58, "COFORGE": 48, "MPHASIS": 32, "LTTS": 35}
LI = {"pat": ["net_profit", "profit after tax"], "revenue": ["revenue", "total revenue", "total income", "sales"],
      "shares": ["shares"], "opm": ["operating_profit", "operating profit", "ebit"],
      "equity_capital": ["equity_capital", "equity capital"], "reserves": ["reserves"]}


def canon(label):
    l = str(label).lower()
    for k, frags in LI.items():
        if any(f in l for f in frags):
            return k
    return None


def load_prices(con, syms):
    out = {}
    for b in syms:
        rows = con.execute("SELECT ts, close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                           "AND close IS NOT NULL ORDER BY ts", (b,)).fetchall()
        if rows:
            out[b] = (pd.to_datetime([r[0] for r in rows]).tz_localize(None).normalize().values,
                      np.asarray([float(r[1]) for r in rows]))
    return out


def asof(px, b, when):
    if b not in px:
        return np.nan
    d, c = px[b]
    i = np.searchsorted(d, np.datetime64(pd.Timestamp(when).normalize()), side="right") - 1
    return float(c[i]) if i >= 0 else np.nan


def ret(px, b, days):
    if b not in px:
        return None
    d, c = px[b]
    last = pd.Timestamp(d[-1]); p1 = float(c[-1]); p0 = asof(px, b, last - pd.Timedelta(days=days))
    return round((p1 / p0 - 1) * 100, 2) if (p0 and p0 == p0) else None


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
    px = load_prices(scon, IT + [INDEX])
    scon.close()

    conn = psycopg.connect(os.getenv("DATABASE_URL") or "")
    cur = conn.cursor()
    cur.execute("SELECT symbol, isin FROM fundamentals.companies WHERE symbol = ANY(%s)", (IT,))
    sym2isin = {s: i for s, i in cur.fetchall()}
    isin2sym = {v: k for k, v in sym2isin.items()}
    cur.execute("SELECT isin, period_end, section, line_item, value FROM fundamentals.financials "
                "WHERE time_period='yearly' AND basis=%s AND isin = ANY(%s)",
                (BASIS, [sym2isin[b] for b in IT if b in sym2isin]))
    recs = []
    for isin, pend, section, li, val in cur.fetchall():
        c = canon(li)
        if c and val is not None:
            recs.append((isin2sym.get(isin), pd.Timestamp(pend).normalize(), section or "", c, float(val)))
    conn.close()
    if not recs:
        sys.exit(f"no consolidated yearly financials for IT — run it_fundamental_check.py first.")
    fin = pd.DataFrame(recs, columns=["symbol", "fy_end", "section", "item", "value"])
    fin = fin.sort_values("section").drop_duplicates(["symbol", "fy_end", "item"], keep="first")
    fin = fin.pivot_table(index=["symbol", "fy_end"], columns="item", values="value", aggfunc="first").reset_index()
    fin["net_worth"] = fin.get("equity_capital", np.nan) + fin.get("reserves", np.nan)

    out = []
    for b in IT:
        g = fin[fin["symbol"] == b].sort_values("fy_end").copy()
        if g.empty or "shares" not in g or not g["shares"].notna().any() or "pat" not in g:
            out.append({"stock": b}); continue
        g["adj_shares"] = adjust_shares(g["shares"].to_numpy(float),
                                        g["net_worth"].to_numpy(float) if "net_worth" in g else np.ones(len(g)))
        g["avail"] = g["fy_end"] + pd.Timedelta(days=LAG_DAYS)
        g["price"] = [asof(px, b, d) for d in g["avail"]]
        g["pe"] = g["price"] * g["adj_shares"] / g["pat"]
        gg = g.dropna(subset=["pe"])
        gg = gg[gg["pe"] > 0]
        rec = {"stock": b}
        if not gg.empty:
            last = gg.iloc[-1]; hist = gg["pe"].to_numpy()
            spot = float(px[b][1][-1]) if b in px else float("nan")
            pe_now = spot * last["adj_shares"] / last["pat"] if (last["pat"] and spot == spot) else float(last["pe"])
            eps = last["pat"] / last["adj_shares"] if last["adj_shares"] else np.nan
            opm = (last["opm"] / last["revenue"] * 100) if ("opm" in last.index and "revenue" in last.index and last.get("revenue")) else np.nan
            prev = gg.iloc[-2] if len(gg) >= 2 else None
            rev_g = ((last["revenue"] / prev["revenue"] - 1) * 100) if (prev is not None and "revenue" in last.index and prev.get("revenue")) else np.nan
            roe = (last["pat"] / last["net_worth"] * 100) if ("net_worth" in last.index and last.get("net_worth")) else np.nan
            rec.update(pe=round(float(pe_now), 1), pe_avail=round(float(last["pe"]), 1),
                       pe_pct=round(float((hist < pe_now).mean()), 2) if len(hist) > 1 else None,
                       pe_min=round(float(hist.min()), 1), pe_max=round(float(hist.max()), 1),
                       eps=round(float(eps), 1) if eps == eps else None,
                       opm=round(float(opm), 1) if opm == opm else None,
                       rev_growth=round(float(rev_g), 1) if rev_g == rev_g else None,
                       roe=round(float(roe), 1) if roe == roe else None,
                       fy=str(last["fy_end"].date()))
        rec["ret_1w"], rec["ret_1m"], rec["ret_6m"] = ret(px, b, 7), ret(px, b, 30), ret(px, b, 182)
        ix = {d: ret(px, INDEX, d) for d in (30, 182)}
        rec["rel_1m"] = round(rec["ret_1m"] - ix[30], 2) if (rec["ret_1m"] is not None and ix[30] is not None) else None
        rec["rel_6m"] = round(rec["ret_6m"] - ix[182], 2) if (rec["ret_6m"] is not None and ix[182] is not None) else None
        rec["last_px"] = round(float(px[b][1][-1]), 1) if b in px else None
        out.append(rec)

    # ---- pre-flight P/E anchor (catch a basis/units bug before it ships) ----
    print("\n  IT P/E anchor check (SUSPECT if |off| > 50%):")
    for r in out:
        a = ANCHOR_PE.get(r["stock"]); pe = r.get("pe")
        flag = ("SUSPECT" if (pe and a and abs(pe - a) / a > 0.5) else "ok") if pe else "no-data"
        print(f"    {r['stock']:<12} P/E {pe if pe else '—':>6}  anchor {a:>4}  {flag}")

    payload = {"as_of": (str(pd.Timestamp(px[INDEX][0][-1]).date()) if INDEX in px else
                         (str(pd.Timestamp(px[IT[0]][0][-1]).date()) if IT[0] in px else None)),
               "index": {"name": INDEX, "ret_1w": ret(px, INDEX, 7), "ret_1m": ret(px, INDEX, 30), "ret_6m": ret(px, INDEX, 182)},
               "stocks": out}
    outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "it_view.json")
    with open(outpath, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"\n  SAVED → {outpath}  ({len([r for r in out if r.get('pe')])}/{len(IT)} stocks with P/E)")


if __name__ == "__main__":
    main()
