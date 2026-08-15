#!/usr/bin/env python3
"""theta_study -- theta is a VOLATILITY question, not a direction question.

REFRAME. Holding a long option, you do not lose to time as such -- you lose when the
market moves LESS than the option's implied volatility was charging you for. Theta bleed
is exactly the gap between IMPLIED and REALISED movement. So the question "will theta eat
me" is answered by comparing what the option is priced to move against what the index
actually does, and direction barely enters it.

  implied 1-day move  =  INDIAVIX / sqrt(252)      (VIX is an annualised sigma, in %)
  realised 1-day move =  |next session's return|

The ratio of the two IS the variance risk premium. Above 1 means options were charging
more than the market delivered -- the buyer bled, the seller collected. This is the single
number that decides whether holding a long option is a losing proposition on average.

THEN the user's hypothesis, tested in the form that matters for theta: after a DOWN day,
does a BROAD decline predict a larger next-day move than a CONCENTRATED one? Not a bigger
FALL -- a bigger MOVE. A long put is helped by movement in either direction and killed by
stillness, so absolute move is the correct target. Note this is a genuinely different test
from the breadth-direction one that already failed.
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(77)
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
D = pd.DataFrame({"idx": O.NIFTY.pct_change() * 100, "vix": O.INDIAVIX,
                  "n_ok": R.notna().sum(axis=1)})
D["implied_1d"] = D.vix / np.sqrt(252)
D["realised_next"] = D.idx.shift(-1).abs()
D["ew"] = R.mean(axis=1)
D["ew_minus_cw"] = D.ew - D.idx
D["breadth"] = (R > 0).sum(axis=1) / R.notna().sum(axis=1) * 100
D = D[D.n_ok >= 35].dropna()

print("=== 1. THE NUMBER THAT DECIDES IT: implied vs realised, by VIX level ===")
print("   %-14s %6s %11s %11s %8s %9s" % ("VIX bucket", "n", "implied 1d", "realised", "ratio", "buyer P/L"))
print("   " + "-" * 66)
rows = {}
for lo, hi in ((0, 12), (12, 14), (14, 17), (17, 22), (22, 999)):
    m = (D.vix >= lo) & (D.vix < hi)
    if m.sum() < 30: continue
    s = D[m]
    imp, real = s.implied_1d.mean(), s.realised_next.mean()
    rows["%d-%d" % (lo, hi)] = {"n": int(len(s)), "implied": imp, "realised": real,
                                "ratio": real / imp}
    print("   %-14s %6d %10.3f%% %10.3f%% %8.2f %9s"
          % ("VIX %d-%d" % (lo, hi), len(s), imp, real, real / imp,
             "loses" if real < imp else "gains"))
allr = D.realised_next.mean() / D.implied_1d.mean()
print("   %-14s %6d %10.3f%% %10.3f%% %8.2f" % ("ALL", len(D), D.implied_1d.mean(),
                                                D.realised_next.mean(), allr))
print("\n   VIX today ~12.2 -> option is charging %.3f%% of movement per day."
      % (12.2 / np.sqrt(252)))
print("   Historically at VIX 12-14 the index actually moved %.3f%% -- a ratio of %.2f."
      % (rows.get("12-14", {}).get("realised", float('nan')),
         rows.get("12-14", {}).get("ratio", float('nan'))))

print("\n=== 2. DOES A BROAD DECLINE PREDICT A BIGGER NEXT MOVE THAN A CONCENTRATED ONE? ===")
print("   (target is |next-day move| -- what a long option needs -- not direction)")
dn = D[D.idx < 0].copy()
# concentrated = index fell but the typical stock held up (ew_minus_cw high)
dn["broad"] = dn.ew_minus_cw <= dn.ew_minus_cw.median()   # typical stock fell WITH index
print("   %-26s %6s %11s %11s %8s" % ("group", "n", "realised", "implied", "ratio"))
print("   " + "-" * 66)
out = {}
for lab, m in (("ALL down days", pd.Series(True, index=dn.index)),
               ("BROAD decline", dn.broad), ("CONCENTRATED decline", ~dn.broad)):
    s = dn[m]
    out[lab] = {"n": int(len(s)), "realised": float(s.realised_next.mean()),
                "ratio": float(s.realised_next.mean() / s.implied_1d.mean())}
    print("   %-26s %6d %10.3f%% %10.3f%% %8.2f"
          % (lab, len(s), s.realised_next.mean(), s.implied_1d.mean(),
             s.realised_next.mean() / s.implied_1d.mean()))
diff = out["BROAD decline"]["realised"] - out["CONCENTRATED decline"]["realised"]
lab = np.array([1] * out["BROAD decline"]["n"] + [0] * out["CONCENTRATED decline"]["n"])
vals = np.concatenate([dn[dn.broad].realised_next.values, dn[~dn.broad].realised_next.values])
nd = [np.mean(vals[(pm := RNG.permutation(lab)) == 1]) - np.mean(vals[pm == 0])
      for _ in range(5000)]
p = float((np.abs(np.array(nd)) >= abs(diff)).mean())
print("\n   broad minus concentrated: %+.3f pp of next-day movement   permutation p=%.3f"
      % (diff, p))
print("   >>> %s" % ("BROAD declines are followed by bigger moves -- worth holding through"
                     if p < 0.05 and diff > 0 else
                     "no difference -- concentration does NOT tell you whether tomorrow "
                     "moves enough to cover theta"))

print("\n=== 3. HOW MANY DAYS OF DECAY DOES A TYPICAL MOVE COVER? ===")
for lo, hi in ((0, 12), (12, 14), (14, 17), (17, 999)):
    m = (D.vix >= lo) & (D.vix < hi)
    if m.sum() < 30: continue
    s = D[m]
    print("   VIX %-7s realised/implied %.2f  ->  a long option recovers its daily decay "
          "on %.0f%% of days" % ("%d-%d" % (lo, hi), s.realised_next.mean() / s.implied_1d.mean(),
                                 (s.realised_next > s.implied_1d).mean() * 100))
json.dump({"by_vix": rows, "all_ratio": float(allr), "down_day_split": out,
           "broad_minus_concentrated": float(diff), "p": p},
          open("theta_study_result.json", "w"), indent=1)
print("\nwrote theta_study_result.json")
