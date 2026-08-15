#!/usr/bin/env python3
"""overnight_test -- is a short-premium position paid enough for the overnight it carries?

THE STRUCTURAL QUESTION. Theta accrues in CALENDAR time -- the option decays through the
night whether or not the market is open. Gamma cost accrues in REALISED VARIANCE, and a
gap delivers its variance in one discontinuous jump. So the overnight is worth owning only
if its share of the decay exceeds its share of the variance. Those two shares are
measurable and they need not match.

PART 1 uses the index back to 2018 for power. For a short-gamma position the cost of a move
scales with its SQUARE, not its absolute size, so the gamma-cost split is computed on
squared returns -- an earlier note in this thread put gaps at 56% of ABSOLUTE movement,
which understates them here, because gaps are individually larger and squaring rewards that.

PART 2 uses the real chain for the 31 captured days: value a strangle at one day's last
capture and at the next day's first capture (the overnight), then across that day's session
(the intraday). No Greeks approximation -- these are traded prices.

PART 3 asks the user's better question: not "how many overnights" but "WHICH overnight" --
by weekday, and after a shock versus after a quiet session.
"""
import sqlite3, json
import numpy as np, pandas as pd

con = sqlite3.connect("option_chains.db")
n = pd.read_sql("SELECT ts,open,high,low,close FROM price_bars WHERE timeframe='1d' "
                "AND symbol='NIFTY' ORDER BY ts", con)
n["d"] = pd.to_datetime(n.ts.str[:10])
N = n.set_index("d")[["open", "high", "low", "close"]]
v = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
v["d"] = pd.to_datetime(v.ts.str[:10])
N = N.join(v.set_index("d")["close"].rename("vix"), how="inner")
N["gap"] = (N.open / N.close.shift(1) - 1) * 100
N["intra"] = (N.close / N.open - 1) * 100
N["r"] = N.close.pct_change() * 100
D = N.dropna()

g2, i2 = (D.gap ** 2).sum(), (D.intra ** 2).sum()
print("=== 1. GAMMA COST: where does the realised variance arrive? (n=%d) ===" % len(D))
print("   sum of squared GAP returns     : %8.1f   (%.0f%% of total)" % (g2, g2 / (g2 + i2) * 100))
print("   sum of squared INTRADAY returns: %8.1f   (%.0f%%)" % (i2, i2 / (g2 + i2) * 100))
print("   mean |gap| %.3f%%   mean |intraday| %.3f%%" % (D.gap.abs().mean(), D.intra.abs().mean()))
print("   CALENDAR TIME overnight is 17.75h of 24h = 74%% -- that is roughly the share of")
print("   the day's theta the overnight earns. Compare it with the %.0f%% of variance it"
      % (g2 / (g2 + i2) * 100))
print("   delivers. %s"
      % ("Overnight looks UNDER-compensated." if g2 / (g2 + i2) > 0.74 else
         "Overnight delivers LESS variance than the theta share it earns -- it looks "
         "adequately paid on this crude comparison."))

print("\n=== 2. THE REAL CHAIN: overnight vs intraday P/L of a short strangle ===")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
cap["day"] = cap.dt.dt.date
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "day", "spot"]], on="capture_id")
X = 150.0
on, intr = [], []
for exp, g in ch.groupby("exp"):
    days = sorted(g.day.unique())
    for a, b in zip(days, days[1:]):
        ga, gb = g[g.day == a], g[g.day == b]
        la = ga[ga.dt == ga.dt.max()]; fb = gb[gb.dt == gb.dt.min()]; lb = gb[gb.dt == gb.dt.max()]
        if la.empty or fb.empty or lb.empty: continue
        S = float(la.spot.iloc[0])
        ks = np.asarray(sorted(la.strike.unique()), dtype=float)
        ck = float(ks[np.argmin(np.abs(ks - (S + X)))]); pk = float(ks[np.argmin(np.abs(ks - (S - X)))])
        def val(fr):
            c = fr[fr.strike == ck].call_ltp; p = fr[fr.strike == pk].put_ltp
            if c.empty or p.empty: return None
            cv, pv = float(c.iloc[0]), float(p.iloc[0])
            return cv + pv if (cv > 0 and pv > 0) else None
        v0, v1, v2 = val(la), val(fb), val(lb)
        if None in (v0, v1, v2): continue
        on.append(v0 - v1)          # short position gains when value falls
        intr.append(v1 - v2)
on, intr = np.array(on), np.array(intr)
if len(on) >= 10:
    print("   %-22s %5s %10s %10s %10s %10s" % ("segment", "n", "mean P/L", "std", "worst", "P(loss)"))
    print("   " + "-" * 66)
    for lab, a in (("OVERNIGHT (cl->op)", on), ("INTRADAY (op->cl)", intr)):
        print("   %-22s %5d %+10.2f %10.2f %+10.2f %9.0f%%"
              % (lab, len(a), a.mean(), a.std(), a.min(), (a < 0).mean() * 100))
    print("   overnight share of total P/L : %.0f%%" % (on.sum() / (on.sum() + intr.sum()) * 100))
    print("   overnight share of total RISK: %.0f%% (by variance)"
          % (on.var() / (on.var() + intr.var()) * 100))
    print("   return per unit of risk -- overnight %.2f  intraday %.2f"
          % (on.mean() / on.std(), intr.mean() / intr.std()))
else:
    print("   too few clean day-pairs to price (%d)" % len(on))

print("\n=== 3. WHICH OVERNIGHT IS WORTH SELLING? (index, 2018-2026) ===")
D2 = D.copy(); D2["dow"] = D2.index.dayofweek
print("   %-22s %6s %11s %11s" % ("overnight into", "n", "mean gap^2", "P(|gap|>0.5%)"))
for k, lab in ((0, "Monday (after w/e)"), (1, "Tuesday"), (2, "Wednesday"),
               (3, "Thursday"), (4, "Friday")):
    s = D2[D2.dow == k]
    if len(s) < 30: continue
    print("   %-22s %6d %11.3f %10.0f%%" % (lab, len(s), (s.gap ** 2).mean(),
                                            (s.gap.abs() > 0.5).mean() * 100))
print()
prev = D2.r.shift(1)
for lab, m in (("after a quiet day (<0.25%)", prev.abs() < 0.25),
               ("after a -1% day", prev < -1), ("after a -2% day", prev < -2),
               ("after a +1% day", prev > 1), ("VIX <= 12", D2.vix <= 12),
               ("VIX > 17", D2.vix > 17)):
    s = D2[m.reindex(D2.index).fillna(False)]
    if len(s) < 30: continue
    print("   %-22s %6d %11.3f %10.0f%%" % (lab, len(s), (s.gap ** 2).mean(),
                                            (s.gap.abs() > 0.5).mean() * 100))
json.dump({"gap_var_share": float(g2 / (g2 + i2)),
           "overnight_n": int(len(on)),
           "overnight_mean": float(on.mean()) if len(on) else None,
           "intraday_mean": float(intr.mean()) if len(intr) else None},
          open("overnight_test_result.json", "w"), indent=1)
print("\nwrote overnight_test_result.json")
