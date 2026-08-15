#!/usr/bin/env python3
"""bullput -- the structure actually traded: sell PE 150 below spot, buy PE 350 below.

Width 200, so max loss = 200 - credit. One-sided: it is a bet that the index does not fall
more than 150 points (0.62% at 24,000) before expiry. No upside risk at all -- a rally is a
full win, which is a material difference from every structure tested so far, because four
of six of the worst outcomes in this chain window came from RALLIES.

Two questions:
  1. What does it actually pay, on real LTPs, across every entry in the capture window?
  2. Is the premise right? "Market always goes up" is a base rate, and it is measurable:
     how often has Nifty been above entry-0.62% six sessions later, 2018-2026?
"""
import sqlite3
import numpy as np, pandas as pd

SHORT_OFF, LONG_OFF, SLIP = 150.0, 350.0, 1.0
con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False)) + pd.Timedelta("5:30:00")
cap["day"] = cap.dt.dt.date
ch = pd.read_sql("SELECT capture_id,expiry,strike,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "day", "spot"]], on="capture_id")

rows = []
for exp, g in ch.groupby("exp"):
    ST = float(g[g.dt == g.dt.max()].spot.iloc[0])
    for d in sorted(g.day.unique())[:-1]:
        gd = g[g.day == d]; f = gd[gd.dt == gd.dt.min()]
        S = float(f.spot.iloc[0])
        ks = np.asarray(sorted(f.strike.unique()), dtype=float)
        sk = float(ks[np.argmin(np.abs(ks - (S - SHORT_OFF)))])
        lk = float(ks[np.argmin(np.abs(ks - (S - LONG_OFF)))])
        if sk <= lk: continue
        sp = f[f.strike == sk].put_ltp; lp = f[f.strike == lk].put_ltp
        if sp.empty or lp.empty: continue
        sv, lv = float(sp.iloc[0]), float(lp.iloc[0])
        if not (sv > 0.05 and lv > 0.05): continue
        credit = sv - lv - 2 * SLIP
        width = sk - lk
        payoff = max(sk - ST, 0) - max(lk - ST, 0)
        # worst mark during the hold, from the same two legs
        gg = g[(g.dt >= f.dt.iloc[0]) & (g.strike.isin([sk, lk]))]
        pv = gg.pivot_table(index="dt", columns="strike", values="put_ltp").ffill()
        mtm = (credit - (pv[sk] - pv[lk])) if (sk in pv and lk in pv) else pd.Series([0.0])
        rows.append({"exp": str(exp), "entry": str(d), "spot": S, "sk": sk, "lk": lk,
                     "width": width, "credit": credit, "maxloss": width - credit,
                     "settle": ST, "pnl": credit - payoff, "worst_mtm": float(mtm.min())})
T = pd.DataFrame(rows)
print("=== BULL PUT SPREAD on real LTPs, %d entries, %d expiries ===" % (len(T), T.exp.nunique()))
print("   avg credit %.1f   avg width %.0f   avg max loss %.1f   risk/reward %.2fx"
      % (T.credit.mean(), T.width.mean(), T.maxloss.mean(), T.maxloss.mean() / T.credit.mean()))
print("   mean P/L %+.1f   median %+.1f   win rate %.0f%%   worst %+.1f   best %+.1f"
      % (T.pnl.mean(), T.pnl.median(), (T.pnl > 0).mean() * 100, T.pnl.min(), T.pnl.max()))
print("   worst intraday mark-to-market seen: %+.1f pts" % T.worst_mtm.min())
print("\n   %-14s %6s %9s %9s %8s" % ("P/L bucket", "n", "share", "mean", "cum"))
for lo, hi, lab in ((-999, -50, "big loss <-50"), (-50, 0, "small loss"),
                    (0, 30, "small win 0-30"), (30, 999, "full win >30")):
    s = T[(T.pnl > lo) & (T.pnl <= hi)]
    if not len(s): continue
    print("   %-14s %6d %8.0f%% %+9.1f" % (lab, len(s), 100 * len(s) / len(T), s.pnl.mean()))

print("\n=== IS THE PREMISE RIGHT? base rate of 'market does not fall 0.62%%' ===")
n = pd.read_sql("SELECT ts,low,close FROM price_bars WHERE timeframe='1d' AND symbol='NIFTY' "
                "ORDER BY ts", con)
n["d"] = pd.to_datetime(n.ts.str[:10]); N = n.set_index("d")
for H in (1, 3, 6):
    fwd = (N.close.shift(-H) / N.close - 1) * 100
    path = pd.Series([(N.low.values[i+1:i+1+H].min() / N.close.values[i] - 1) * 100
                      if i + H < len(N) else np.nan for i in range(len(N))], index=N.index)
    ok = (fwd > -0.625).mean() * 100
    full = (fwd < -1.458).mean() * 100
    touch = (path < -0.625).mean() * 100
    print("   %d session%s: short strike safe at expiry %.0f%%   |  max loss hit %.0f%%   "
          "|  strike TOUCHED intraday %.0f%%" % (H, "s" if H > 1 else "", ok, full, touch))
print("\n   'touched' matters because that is when the position is under water and you")
print("   would be deciding whether to close -- not the same as finishing there.")
T.round(1).to_json("bullput_result.json", orient="records", indent=1)
