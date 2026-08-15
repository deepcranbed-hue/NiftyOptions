#!/usr/bin/env python3
"""regime_test -- is the crude beta REGIME-SWITCHING, or is a short window just wandering?

THE CLAIM UNDER TEST. Rolling beta of NIFTY on WTI reads +0.037 (2018-24), +0.008 (2025),
-0.039 (2026 YTD), -0.048 (last 60). That looks like a regime change and the whole
reframing rests on it. But a beta estimated on a SHORT window wanders on its own: with
~150 sessions and a correlation near zero, the sub-sample beta has a wide sampling
distribution even when the true beta never moves. So the question is not "is 2026
different from 2018-24" -- it is "is 2026 different by MORE than a randomly chosen
150-session window would be."

Tested by drawing random contiguous windows of the SAME length from the full sample
(preserving autocorrelation and volatility clustering) and locating the observed 2026
beta in that distribution. That is the null of a constant beta.

ALIGNMENT CAVEAT, stated because it changes what the beta means. Yahoo's CL=F settles
~14:30 ET, i.e. after midnight IST -- so crude's date-D close postdates NIFTY's date-D
close, exactly like the S&P. The beta below is therefore a CONTEMPORANEOUS ASSOCIATION
(both reacting to the same global tape), not a lead. That is fine for "crude defines a
regime" but it cannot support "crude predicts". The properly-aligned intraday read needs
CRUDEOIL_MCX, which trades in Indian hours and has 23 daily bars here.

ALSO TESTED
  B. the four-quadrant cross-asset interaction (US up/down x crude up/down)
  C. whether an SP500 x crude INTERACTION term adds anything to the additive model
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(1234)
con = sqlite3.connect("option_chains.db")
SY = ["NIFTY", "NIFTYIT", "CRUDEOIL", "USDINR", "INDIAVIX", "SP500", "SOX"]
df = pd.read_sql("SELECT symbol,ts,open,close FROM price_bars WHERE timeframe='1d' AND "
                 "symbol IN (%s)" % ",".join("?" * len(SY)), con, params=SY)
df["d"] = pd.to_datetime(df.ts.str[:10])
C = df.pivot_table(index="d", columns="symbol", values="close").sort_index()
R = (C.pct_change(fill_method=None) * 100)

# ---------- A. is the crude regime change real? ----------
J = R[["NIFTY", "CRUDEOIL"]].dropna()
def beta(x, y): return float(np.cov(x, y)[0, 1] / np.var(y))
b_full = beta(J.NIFTY, J.CRUDEOIL)
j26 = J[J.index >= "2026-01-01"]
b26, n26 = beta(j26.NIFTY, j26.CRUDEOIL), len(j26)
b_early = beta(J[J.index < "2025-01-01"].NIFTY, J[J.index < "2025-01-01"].CRUDEOIL)
print("A. CRUDE BETA REGIME TEST")
print("   full sample %+.4f (n=%d) | 2018-24 %+.4f | 2026 YTD %+.4f (n=%d)"
      % (b_full, len(J), b_early, b26, n26))
draws = []
for _ in range(5000):
    s = int(RNG.integers(0, len(J) - n26))
    w = J.iloc[s:s + n26]
    draws.append(beta(w.NIFTY, w.CRUDEOIL))
draws = np.array(draws)
pct = float((draws <= b26).mean())
print("   random %d-session windows from the SAME data: median %+.4f  5th %+.4f  95th %+.4f"
      % (n26, np.median(draws), np.percentile(draws, 5), np.percentile(draws, 95)))
print("   observed 2026 beta sits at the %.1fth percentile  ->  p(two-sided) = %.3f"
      % (pct * 100, 2 * min(pct, 1 - pct)))
print("   >>> %s" % ("REGIME CHANGE beyond normal window-to-window wandering"
                     if 2 * min(pct, 1 - pct) < 0.05 else
                     "INSIDE the range a random window of this length produces -- "
                     "the 'regime change' is not established"))

# ---------- B. four-quadrant cross-asset interaction ----------
usr = R[["SP500", "SOX", "CRUDEOIL"]].dropna()
left = pd.DataFrame({"ind": C[["NIFTY", "NIFTYIT"]].dropna().index}).sort_values("ind")
M = pd.merge_asof(left, usr.reset_index().rename(columns={"d": "us_date"}),
                  left_on="ind", right_on="us_date", direction="backward",
                  allow_exact_matches=False)
M["age"] = (M["ind"] - M["us_date"]).dt.days
M = M.set_index("ind"); M = M[M.age <= 4]
M["f_nifty"] = R.NIFTY.reindex(M.index)
M["f_rel"] = (R.NIFTYIT - R.NIFTY).reindex(M.index)
# BUG FIXED: USDINR and INDIAVIX are INDIAN-hours series, so their date-D value is
# contemporaneous with the date-D NIFTY return being predicted. India VIX is
# mechanically inverse to same-day NIFTY (it is computed FROM option prices that move
# with the index), so including it unlagged produced R2=33% and t=-26.6 -- an identity,
# not a forecast. Both are shifted one session so only PRIOR-day values are used.
M["USDINR"] = R.USDINR.shift(1).reindex(M.index)
M["VIX"] = R.INDIAVIX.shift(1).reindex(M.index)
M = M.dropna()
print("\nB. FOUR QUADRANTS -- prior US session x prior crude session -> NEXT Indian session")
print("   %-26s %6s %11s %11s %9s" % ("quadrant", "n", "NIFTY", "IT relative", "P(up)"))
print("   " + "-" * 66)
base = M.f_nifty.mean()
print("   %-26s %6d %10.3f%% %10.3f%% %8.0f%%"
      % ("ALL (base rate)", len(M), base, M.f_rel.mean(), (M.f_nifty > 0).mean() * 100))
quads = {}
for su, lu in ((True, "SP500 up"), (False, "SP500 dn")):
    for sc, lc in ((True, "crude up"), (False, "crude dn")):
        m = ((M.SP500 > 0) == su) & ((M.CRUDEOIL > 0) == sc)
        s = M[m]; quads["%s + %s" % (lu, lc)] = (len(s), s.f_nifty.mean(), s.f_rel.mean())
        print("   %-26s %6d %10.3f%% %10.3f%% %8.0f%%"
              % (lu + " + " + lc, len(s), s.f_nifty.mean(), s.f_rel.mean(),
                 (s.f_nifty > 0).mean() * 100))
# null on the spread between best and worst quadrant
obs_spread = max(v[1] for v in quads.values()) - min(v[1] for v in quads.values())
null = []
n = len(M)
for _ in range(2000):
    k = int(RNG.integers(20, n - 20))
    sp, cr = np.roll(M.SP500.values, k), np.roll(M.CRUDEOIL.values, k)
    vals = [M.f_nifty.values[((sp > 0) == su) & ((cr > 0) == sc)].mean()
            for su in (True, False) for sc in (True, False)]
    null.append(max(vals) - min(vals))
null = np.array(null)
p = float((null >= obs_spread).mean())
print("   best-worst quadrant spread %.3f%%   null median %.3f%%   p=%.3f  ->  %s"
      % (obs_spread, np.median(null), p, "SURVIVES" if p < 0.05 else "inside noise"))

# ---------- C. does an interaction TERM add anything? ----------
print("\nC. ADDITIVE vs INTERACTION model, target = next-session NIFTY")
def ols(X, y):
    X = np.column_stack([np.ones(len(X))] + list(X.T))
    b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b
    se = np.sqrt(np.diag(np.linalg.pinv(X.T @ X) * (e @ e) / (len(y) - X.shape[1])))
    r2 = 1 - (e @ e) / ((y - y.mean()) ** 2).sum()
    return b, b / se, r2
y = M.f_nifty.values
add = np.column_stack([M.SP500, M.CRUDEOIL, M.USDINR, M.VIX])
b, t, r2 = ols(add, y)
print("   additive     R2=%.2f%%  SP500 t=%+.1f  crude t=%+.1f  USDINR t=%+.1f  VIX t=%+.1f"
      % (r2 * 100, t[1], t[2], t[3], t[4]))
inter = np.column_stack([M.SP500, M.CRUDEOIL, M.USDINR, M.VIX, M.SP500 * M.CRUDEOIL])
b2, t2, r22 = ols(inter, y)
print("   +interaction R2=%.2f%%  SP500xCRUDE t=%+.1f   dR2=%+.3f pp"
      % (r22 * 100, t2[5], (r22 - r2) * 100))
print("   >>> %s" % ("interaction adds explanatory power"
                     if abs(t2[5]) > 2 else
                     "the interaction term adds nothing -- cross-asset effects here are "
                     "ADDITIVE, not multiplicative"))
json.dump({"beta_full": b_full, "beta_2026": b26, "beta_early": b_early,
           "regime_p": 2 * min(pct, 1 - pct), "quadrants": quads,
           "quadrant_spread": obs_spread, "quadrant_p": p,
           "r2_additive": r2 * 100, "r2_interaction": r22 * 100,
           "t_interaction": float(t2[5])}, open("regime_test_result.json", "w"), indent=1)
print("\nwrote regime_test_result.json")
