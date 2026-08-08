#!/usr/bin/env python3
"""
bank_view_data.py — pure data extractor for the Bank Nifty VIEW dashboard. No decision logic here
(that's applied transparently when the HTML is built). Emits one JSON blob with, per bank:
  standalone P/B + where it sits in its OWN 2017-26 history · GNPA + 1yr change · PCR · ROA
  1W / 1M / 6M price return (absolute)  +  1M / 6M RELATIVE to BANKNIFTY
Plus BANKNIFTY's own 1W/1M/6M for context.

USAGE
    export DATABASE_URL="postgresql://localhost/niftyoptions"
    export OPTION_CHAINS_DB="/path/to/option_chains.db"
    python bank_view_data.py            # copy everything between ###JSON_START / ###JSON_END
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
BANKS = ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
         "BANKBARODA", "PNB", "AUBANK", "IDFCFIRSTB", "FEDERALBNK", "BANDHANBNK"]
INDEX = "BANKNIFTY"
LAG_DAYS = 45
NICE_SPLITS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0]
LI = {"equity_capital": ["equity_capital", "equity capital"], "reserves": ["reserves"],
      "shares": ["shares"], "total_assets": ["total assets"],
      "pat": ["net_profit", "profit after tax"]}


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
    """simple return over `days` calendar days ending at the bank's latest bar."""
    if b not in px:
        return None
    d, c = px[b]
    last = pd.Timestamp(d[-1])
    p1, p0 = float(c[-1]), asof(px, b, last - pd.Timedelta(days=days))
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
    px = load_prices(scon, BANKS + [INDEX])
    scon.close()

    conn = psycopg.connect(os.getenv("DATABASE_URL") or "")
    cur = conn.cursor()
    cur.execute("SELECT symbol, isin FROM fundamentals.companies WHERE symbol = ANY(%s)", (BANKS,))
    sym2isin = {s: i for s, i in cur.fetchall()}
    isin2sym = {v: k for k, v in sym2isin.items()}
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

    cur.execute("SELECT symbol, period_end, gnpa_pct, pcr_pct FROM fundamentals.asset_quality "
                "WHERE symbol = ANY(%s)", (BANKS,))
    aq = pd.DataFrame(cur.fetchall(), columns=["symbol", "period_end", "gnpa", "pcr"])
    aq["period_end"] = pd.to_datetime(aq["period_end"]).dt.normalize()
    conn.close()

    fin["net_worth"] = fin.get("equity_capital", np.nan) + fin.get("reserves", np.nan)
    out = []
    for b in BANKS:
        g = fin[fin["symbol"] == b].sort_values("fy_end").copy()
        rec = {"bank": b}
        if not g.empty and "shares" in g and g["shares"].notna().any():
            g["adj_shares"] = adjust_shares(g["shares"].to_numpy(float), g["net_worth"].to_numpy(float))
            g["avail"] = g["fy_end"] + pd.Timedelta(days=LAG_DAYS)
            g["price"] = [asof(px, b, d) for d in g["avail"]]
            g["pb"] = g["price"] * g["adj_shares"] / g["net_worth"]
            g["roa"] = g.get("pat", np.nan) / g.get("total_assets", np.nan) * 100
            gg = g.dropna(subset=["pb"])
            if not gg.empty:
                last = gg.iloc[-1]
                hist = gg["pb"].to_numpy()
                # CURRENT P/B for the view: spot price × latest shares ÷ latest book (matches street,
                # consistent with the returns date). pb_avail (avail-price) kept for the model's PIT ref.
                spot = float(px[b][1][-1]) if b in px else float("nan")
                pb_now = (spot * last["adj_shares"] / last["net_worth"]
                          if (last["net_worth"] and spot == spot) else float(last["pb"]))
                bvps = float(last["net_worth"]) / float(last["adj_shares"]) if last["adj_shares"] else float("nan")
                # ROE = PAT ÷ standalone net worth — the metric P/B decodes into (embedded-expectation engine)
                pat = float(last["pat"]) if ("pat" in last.index and last["pat"] == last["pat"]) else float("nan")
                roe = round(pat / last["net_worth"] * 100, 1) if (pat == pat and last["net_worth"]) else None
                rec.update(pb=round(float(pb_now), 2),
                           pb_avail=round(float(last["pb"]), 2),
                           bvps=round(bvps, 1) if bvps == bvps else None,
                           roe=roe,
                           pb_pct=round(float((hist < pb_now).mean()), 2) if len(hist) > 1 else None,
                           pb_min=round(float(hist.min()), 2), pb_max=round(float(hist.max()), 2),
                           roa=round(float(last["roa"]), 2) if last["roa"] == last["roa"] else None,
                           fy=str(last["fy_end"].date()))
        a = aq[aq["symbol"] == b].sort_values("period_end")
        if not a.empty:
            rec["gnpa"] = round(float(a["gnpa"].iloc[-1]), 2) if a["gnpa"].iloc[-1] == a["gnpa"].iloc[-1] else None
            rec["pcr"] = round(float(a["pcr"].iloc[-1]), 0) if a["pcr"].iloc[-1] == a["pcr"].iloc[-1] else None
            rec["gnpa_chg1y"] = round(float(a["gnpa"].iloc[-1] - a["gnpa"].iloc[-5]), 2) if len(a) >= 5 else None
        # returns
        rec["ret_1w"], rec["ret_1m"], rec["ret_6m"] = ret(px, b, 7), ret(px, b, 30), ret(px, b, 182)
        bi = {d: ret(px, INDEX, d) for d in (30, 182)}
        rec["rel_1m"] = round(rec["ret_1m"] - bi[30], 2) if (rec["ret_1m"] is not None and bi[30] is not None) else None
        rec["rel_6m"] = round(rec["ret_6m"] - bi[182], 2) if (rec["ret_6m"] is not None and bi[182] is not None) else None
        rec["last_px"] = round(float(px[b][1][-1]), 1) if b in px else None
        rec["last_date"] = str(pd.Timestamp(px[b][0][-1]).date()) if b in px else None
        out.append(rec)

    payload = {"as_of": (str(pd.Timestamp(px[INDEX][0][-1]).date()) if INDEX in px else None),
               "index": {"bank": INDEX, "ret_1w": ret(px, INDEX, 7),
                         "ret_1m": ret(px, INDEX, 30), "ret_6m": ret(px, INDEX, 182)},
               "banks": out}
    blob = json.dumps(payload, indent=1)
    outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bank_view.json")
    with open(outpath, "w") as f:
        f.write(blob)
    print("###JSON_START"); print(blob); print("###JSON_END")
    print(f"\nSAVED → {outpath}")


if __name__ == "__main__":
    main()
