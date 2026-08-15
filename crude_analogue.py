#!/usr/bin/env python3
"""crude_analogue -- the systematic version of "last time we were here, what happened?"

The single-episode read (22-28 July 2026) is vivid and it fits: crude +11%, Nifty -1.9%,
FII -8,644 cr; then crude -14%, Nifty +2.5%, FII +11,745 cr. But n=1. One episode cannot
distinguish "crude drove it" from "crude happened to be there". So the same conditions are
counted across 2018-2026 and the forward distribution is compared against the
unconditional one -- which is the only way to know whether the condition tells you
anything you did not already know.

CONDITIONS TESTED, each against all history and against the base rate:
  LEVEL   crude in its top quintile
  RISING  crude up >5% over 5 sessions
  BOTH    high AND rising -- today's stated setup
Each reports forward NIFTY returns at +1 and +5 sessions, the share of down days, and a
bootstrap p-value for the difference in means versus the unconditional sample.
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(811)
con = sqlite3.connect("option_chains.db")
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol "
                 "IN ('NIFTY','CRUDEOIL','INDIAVIX')", con)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index().ffill().dropna()
P["cr_q"] = P.CRUDEOIL.rank(pct=True)
P["cr_5d"] = P.CRUDEOIL.pct_change(5) * 100
P["f1"] = (P.NIFTY.shift(-1) / P.NIFTY - 1) * 100
P["f5"] = (P.NIFTY.shift(-5) / P.NIFTY - 1) * 100
D = P.dropna()
print("sample %s .. %s   n=%d" % (D.index.min().date(), D.index.max().date(), len(D)))
print("crude today per this DB: %.2f   (2026 range %.1f .. %.1f)"
      % (D.CRUDEOIL.iloc[-1], D.CRUDEOIL[D.index >= "2026-01-01"].min(),
         D.CRUDEOIL[D.index >= "2026-01-01"].max()))

conds = {
    "ALL (unconditional)":      pd.Series(True, index=D.index),
    "crude top quintile":       D.cr_q >= 0.80,
    "crude +5% over 5 sessions": D.cr_5d > 5,
    "BOTH high and rising":     (D.cr_q >= 0.80) & (D.cr_5d > 5),
    "crude FALLING >5%/5d":     D.cr_5d < -5,
}
base1, base5 = D.f1, D.f5
print("\n%-28s %6s %9s %9s %9s %9s" % ("condition", "n", "fwd+1d", "P(dn+1d)", "fwd+5d", "P(dn+5d)"))
print("-" * 74)
res = {}
for name, m in conds.items():
    s = D[m]
    if len(s) < 20: continue
    res[name] = {"n": int(len(s)), "f1": float(s.f1.mean()), "f5": float(s.f5.mean()),
                 "dn1": float((s.f1 < 0).mean()), "dn5": float((s.f5 < 0).mean())}
    print("%-28s %6d %9.3f %8.0f%% %9.3f %8.0f%%"
          % (name, len(s), s.f1.mean(), (s.f1 < 0).mean() * 100, s.f5.mean(),
             (s.f5 < 0).mean() * 100))

print("\nbootstrap: is the conditional mean different from the unconditional?")
print("(resample the SAME number of days at random, 4000 times; blocks of 5 to respect")
print(" autocorrelation. p = share of random draws at least as extreme.)")
print("\n%-28s %12s %12s" % ("condition", "p(+1d)", "p(+5d)"))
print("-" * 54)
n_all = len(D)
for name, m in conds.items():
    if name.startswith("ALL"): continue
    k = int(m.sum())
    if k < 20: continue
    obs1, obs5 = D[m].f1.mean(), D[m].f5.mean()
    b1, b5 = [], []
    nblk = max(k // 5, 1)
    for _ in range(4000):
        st = RNG.integers(0, n_all - 5, nblk)
        idx = np.concatenate([np.arange(s, s + 5) for s in st])
        b1.append(base1.values[idx].mean()); b5.append(base5.values[idx].mean())
    b1, b5 = np.array(b1), np.array(b5)
    p1 = float((np.abs(b1 - base1.mean()) >= abs(obs1 - base1.mean())).mean())
    p5 = float((np.abs(b5 - base5.mean()) >= abs(obs5 - base5.mean())).mean())
    res[name]["p1"], res[name]["p5"] = p1, p5
    print("%-28s %12.3f %12.3f%s" % (name, p1, p5, "  <<<" if min(p1, p5) < 0.05 else ""))

# what actually happened the day after every sub-24000 close, for reference
sub = D[D.NIFTY < 24000]
print("\nfor reference: every NIFTY close < 24,000 in sample (n=%d)" % len(sub))
print("  next-day mean %+.3f%%   P(down) %.0f%%   +5d mean %+.3f%%"
      % (sub.f1.mean(), (sub.f1 < 0).mean() * 100, sub.f5.mean()))
json.dump(res, open("crude_analogue_result.json", "w"), indent=1)
print("\nwrote crude_analogue_result.json")
