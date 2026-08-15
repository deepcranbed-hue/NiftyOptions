#!/usr/bin/env python3
"""breadth_study -- when NIFTY rises, does everything rise with it?

The index is a WEIGHTED AVERAGE of 50 prices. Nothing forces its constituents to move
together, and the weights are extremely unequal -- HDFCBANK alone is 11.6% while the
smallest names are ~0.35%. So the index can rise while most stocks fall, if the rise is
concentrated in the heavy names. This file measures how often that happens and how wide
the spread gets, at horizons from one day to one year.

SURVIVORSHIP BIAS, stated up front because it makes everything below look BETTER than
reality: the constituent list is TODAY's Nifty 50 applied backwards. Names that were
dropped from the index (generally after underperforming) are absent, and names added
later are present for years before they joined. The true historical share of losers was
higher than what is printed here. The dispersion figures are less affected than the
"how many are down" counts.
"""
import sqlite3
import numpy as np, pandas as pd

con = sqlite3.connect("option_chains.db")
csv = pd.read_csv("nifty-50-stock-list.csv")
syms = [s.strip() for s in csv["Symbol"].dropna()]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                 "(%s)" % ",".join("?" * len(syms)), con, params=syms)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index()
nif = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='NIFTY'", con)
nif["d"] = pd.to_datetime(nif.ts.str[:10])
N = nif.set_index("d")["close"].sort_index()
P, N = P.align(N, join="inner", axis=0)
P = P.dropna(axis=1, thresh=int(len(P) * 0.8))
print("constituents with history: %d   sessions: %d   %s .. %s"
      % (P.shape[1], len(P), P.index.min().date(), P.index.max().date()))

for H, lab in ((1, "1 DAY"), (21, "1 MONTH"), (63, "3 MONTHS"), (252, "1 YEAR")):
    r_idx = (N.shift(-H) / N - 1) * 100
    r_stk = (P.shift(-H) / P - 1) * 100
    up = r_idx > 0
    valid = r_stk.notna().sum(axis=1) >= 30
    m = up & valid & r_idx.notna()
    if m.sum() < 20:
        continue
    sub_i, sub_s = r_idx[m], r_stk[m]
    pct_down = (sub_s < 0).sum(axis=1) / sub_s.notna().sum(axis=1) * 100
    disp = sub_s.std(axis=1)
    med = sub_s.median(axis=1)
    print("\n=== %s horizon -- windows where NIFTY ROSE (n=%d) ===" % (lab, int(m.sum())))
    print("   share of stocks DOWN while the index was UP:  mean %.0f%%   median %.0f%%"
          % (pct_down.mean(), pct_down.median()))
    print("   ... at least a QUARTER of stocks down: %.0f%% of windows"
          % ((pct_down >= 25).mean() * 100))
    print("   ... a MAJORITY of stocks down:         %.0f%% of windows"
          % ((pct_down > 50).mean() * 100))
    print("   cross-sectional spread (std of stock returns): median %.1f pp" % disp.median())
    print("   index return vs MEDIAN stock: index %+.2f%%  median stock %+.2f%%"
          % (sub_i.median(), med.median()))
    print("   %-16s %8s %10s %10s %10s" % ("index move", "n", "% down", "spread", "worst stock"))
    if H == 1:
        bks = [(0, .25), (.25, .5), (.5, 1), (1, 2), (2, 99)]
    else:
        bks = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 999)]
    for lo, hi in bks:
        b = (sub_i > lo) & (sub_i <= hi)
        if b.sum() < 10: continue
        print("   +%-5.2f..%-8.2f%% %8d %9.0f%% %9.1f %10.1f%%"
              % (lo, hi, int(b.sum()), pct_down[b].mean(), disp[b].median(),
                 sub_s[b].min(axis=1).median()))

print("\n=== THE EXTREME CASE: index UP, most stocks DOWN ===")
r1 = (N.shift(-1) / N - 1) * 100
s1 = (P.shift(-1) / P - 1) * 100
pd1 = (s1 < 0).sum(axis=1) / s1.notna().sum(axis=1) * 100
bad = (r1 > 0) & (pd1 > 50) & s1.notna().sum(axis=1).ge(40)
print("   days NIFTY rose while >50%% of constituents fell: %d of %d up-days (%.0f%%)"
      % (int(bad.sum()), int(((r1 > 0) & s1.notna().sum(axis=1).ge(40)).sum()),
         bad.sum() / ((r1 > 0) & s1.notna().sum(axis=1).ge(40)).sum() * 100))
ex = pd1[bad].nlargest(5)
for d in ex.index:
    print("     %s  NIFTY %+.2f%%  but %.0f%% of stocks down" % (d.date(), r1[d], pd1[d]))

print("\n=== WHERE YOU ARE NOW: since the 2026 low, and over the past year ===")
for start, lab in ((P.index[-252], "past 1 year"), (pd.Timestamp("2026-04-01"), "since Apr 2026")):
    if start not in P.index:
        start = P.index[P.index.get_indexer([start], method="nearest")[0]]
    ret = (P.iloc[-1] / P.loc[start] - 1) * 100
    ir = (N.iloc[-1] / N.loc[start] - 1) * 100
    ret = ret.dropna()
    dn = ret[ret < 0].sort_values()
    print("\n   %s (%s -> %s): NIFTY %+.2f%%"
          % (lab, start.date(), P.index[-1].date(), ir))
    print("   stocks DOWN: %d of %d   median stock %+.2f%%   spread %+.1f%% .. %+.1f%%"
          % (len(dn), len(ret), ret.median(), ret.min(), ret.max()))
    print("   biggest laggards: %s"
          % ", ".join("%s %.1f%%" % (s, v) for s, v in dn.head(6).items()))
    print("   biggest leaders : %s"
          % ", ".join("%s +%.1f%%" % (s, v) for s, v in ret.nlargest(5).items()))
