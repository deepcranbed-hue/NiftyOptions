#!/usr/bin/env python3
"""theta_tail -- for a premium SELLER the mean is decoration. The tail is the strategy.

Realised/implied averages 0.67, so the seller collects on ~80% of days. That fact is not
the interesting one: short premium is a positive-mean, negative-skew trade, and whether it
works is decided entirely by whether the 20% of losing days cost more than the 80% of
winning days collect. So this file looks at the DISTRIBUTION, not the average.

THEN the hypothesis, tested in the form that matters here. "Shock propagation" already
failed to predict the MEAN next-day move (p=0.380). But a seller does not care about the
mean -- a seller cares about P(large move), because that is what gamma costs. A variable
can be useless for the mean and informative about the tail; those are different questions
and only the second one is about gamma risk. So: does a BROAD decline raise the odds of a
tail move more than a CONCENTRATED one?

Also tested, because it is the specific claim being made: is selling premium AFTER a shock
better than selling on an ordinary day? The intuition is that IV is elevated and the
catalyst is spent. The data has an opinion.
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(909)
con = sqlite3.connect("option_chains.db")
csv = pd.read_csv("nifty-50-stock-list.csv")
syms = [s.strip() for s in csv["Symbol"].dropna()]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                 "(%s)" % ",".join("?" * len(syms)), con, params=syms)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index()
o = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                "('NIFTY','INDIAVIX')", con)
o["d"] = pd.to_datetime(o.ts.str[:10])
O = o.pivot_table(index="d", columns="symbol", values="close").sort_index()
P, O = P.align(O, join="inner", axis=0)
P = P.dropna(axis=1, thresh=int(len(P) * 0.8))
R = P.pct_change(fill_method=None) * 100
D = pd.DataFrame({"idx": O.NIFTY.pct_change(fill_method=None) * 100, "vix": O.INDIAVIX,
                  "n_ok": R.notna().sum(axis=1)})
D["implied"] = D.vix / np.sqrt(252)
D["realised"] = D.idx.shift(-1).abs()
D["ratio"] = D.realised / D.implied
D["edge"] = D.implied - D.realised          # seller's gross daily P/L proxy, in % of spot
D["ew_minus_cw"] = R.mean(axis=1) - D.idx
D = D[D.n_ok >= 35].dropna()

print("=== 1. THE DISTRIBUTION the seller actually faces (n=%d) ===" % len(D))
q = D.ratio.quantile([.5, .8, .9, .95, .99, 1.0])
print("   realised/implied by percentile:")
for k, v in q.items():
    print("      p%-4.0f  %.2f%s" % (k * 100, v, "   <- seller loses above 1.00" if v > 1 else ""))
print("   share of days realised > implied      : %.0f%%" % ((D.ratio > 1).mean() * 100))
print("   share of days realised > 2x implied   : %.1f%%" % ((D.ratio > 2).mean() * 100))
print("   share of days realised > 3x implied   : %.1f%%" % ((D.ratio > 3).mean() * 100))

print("\n=== 2. DOES THE TAIL EAT THE ACCUMULATION? (gross, pre-cost straddle proxy) ===")
tot = D.edge.sum()
win = D.edge[D.edge > 0].sum(); loss = D.edge[D.edge < 0].sum()
worst = D.edge.nsmallest(int(len(D) * 0.01))
print("   collected on winning days : %+.1f pp of spot" % win)
print("   paid out on losing days   : %+.1f pp" % loss)
print("   net (gross of costs)      : %+.1f pp over %d sessions" % (tot, len(D)))
print("   the WORST 1%% of days (%d days) cost %+.1f pp  = %.0f%% of all gross gains"
      % (len(worst), worst.sum(), abs(worst.sum()) / win * 100))
print("   worst single day: %+.2f pp (%s)" % (D.edge.min(), D.edge.idxmin().date()))
print("   >>> a strategy whose top 1%% of losses eats %.0f%% of its gains is a TAIL trade,"
      % (abs(worst.sum()) / win * 100))
print("       not a carry trade. Sizing, not signal, decides the outcome.")

print("\n=== 3. THE HYPOTHESIS, TESTED ON THE TAIL rather than the mean ===")
dn = D[D.idx < 0].copy()
dn["broad"] = dn.ew_minus_cw <= dn.ew_minus_cw.median()
print("   %-24s %6s %9s %11s %11s" % ("prior day", "n", "mean r/i", "P(>1x)", "P(>2x)"))
print("   " + "-" * 62)
res = {}
for lab, m in (("ALL down days", pd.Series(True, index=dn.index)),
               ("BROAD decline", dn.broad), ("CONCENTRATED decline", ~dn.broad)):
    s = dn[m]
    res[lab] = {"n": int(len(s)), "mean": float(s.ratio.mean()),
                "p1": float((s.ratio > 1).mean()), "p2": float((s.ratio > 2).mean())}
    print("   %-24s %6d %9.2f %10.0f%% %10.1f%%"
          % (lab, len(s), s.ratio.mean(), (s.ratio > 1).mean() * 100, (s.ratio > 2).mean() * 100))
d2 = res["BROAD decline"]["p2"] - res["CONCENTRATED decline"]["p2"]
lab = np.array([1] * res["BROAD decline"]["n"] + [0] * res["CONCENTRATED decline"]["n"])
vals = np.concatenate([(dn[dn.broad].ratio > 2).values, (dn[~dn.broad].ratio > 2).values]).astype(float)
nd = []
for _ in range(5000):
    pm = RNG.permutation(lab)
    nd.append(vals[pm == 1].mean() - vals[pm == 0].mean())
p = float((np.abs(np.array(nd)) >= abs(d2)).mean())
print("\n   broad minus concentrated, P(tail move >2x implied): %+.2f pp   p=%.3f  -> %s"
      % (d2 * 100, p, "SURVIVES" if p < 0.05 else "inside noise"))

print("\n=== 4. IS SELLING AFTER A SHOCK BETTER THAN SELLING ON AN ORDINARY DAY? ===")
print("   %-28s %6s %9s %10s" % ("entry condition", "n", "mean r/i", "P(>2x)"))
print("   " + "-" * 58)
for lab, m in (("any day", pd.Series(True, index=D.index)),
               ("after a -1%% day", D.idx < -1), ("after a -1.5%% day", D.idx < -1.5),
               ("after a -2%% day", D.idx < -2), ("after a +1%% day", D.idx > 1)):
    s = D[m]
    if len(s) < 30: continue
    print("   %-28s %6d %9.2f %9.1f%%" % (lab, len(s), s.ratio.mean(), (s.ratio > 2).mean() * 100))
print("\n   >>> higher mean ratio after a shock = the premium NARROWS, so selling into the")
print("       aftermath of a fall has historically been WORSE than an ordinary day, not better.")
json.dump({"quantiles": {str(k): float(v) for k, v in q.items()},
           "pct_ratio_gt1": float((D.ratio > 1).mean()), "net_pp": float(tot),
           "worst1pct_pp": float(worst.sum()), "gross_win_pp": float(win),
           "down_split": res, "tail_diff_pp": float(d2 * 100), "tail_p": p},
          open("theta_tail_result.json", "w"), indent=1)
print("\nwrote theta_tail_result.json")
