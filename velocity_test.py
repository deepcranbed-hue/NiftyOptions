#!/usr/bin/env python3
"""velocity_test -- does ACCELERATION add anything to VIX and |recent move|?

Established already: VIX and the size of the last move both predict path excursion (MAE),
strongly. The new claim is that a THIRD variable helps -- whether the market is still
accelerating or has begun to stabilise after a shock. The distinction is intuitive
("-200 then flat" vs "-200 then -200") and it is the one the roll engine would act on.

The test that matters is INCREMENTAL. Acceleration will correlate with future excursion on
its own, because it is built from the same volatility that VIX already measures. The
question is whether it survives once VIX and |recent move| are in the model. If it does
not, the engine gains a variable and no information.

  accel      = |r_t| - |r_t-1|          the change in the size of the move
  continued  = sign(r_t) == sign(r_t-1) same direction two sessions running
  target     = MAE over the NEXT 6 sessions, intraday high/low (the excursion that
               actually threatens a short strangle)

Also reported as the state table the engine would use, because a regression coefficient
is not a decision rule.
"""
import sqlite3, json
import numpy as np, pandas as pd

BAND = 1.65
H = 6
con = sqlite3.connect("option_chains.db")
n = pd.read_sql("SELECT ts,open,high,low,close FROM price_bars WHERE timeframe='1d' "
                "AND symbol='NIFTY' ORDER BY ts", con)
n["d"] = pd.to_datetime(n.ts.str[:10])
N = n.set_index("d")[["open", "high", "low", "close"]]
v = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
v["d"] = pd.to_datetime(v.ts.str[:10])
N = N.join(v.set_index("d")["close"].rename("vix"), how="inner")
N["r"] = N.close.pct_change() * 100
N["absr"] = N.r.abs()
N["accel"] = N.absr - N.absr.shift(1)
N["continued"] = (np.sign(N.r) == np.sign(N.r.shift(1))).astype(float)

hi, lo, cl = N.high.values, N.low.values, N.close.values
mae = np.full(len(N), np.nan)
for i in range(len(N) - H):
    b = cl[i]
    mae[i] = max(abs(np.max(hi[i+1:i+1+H]) / b - 1), abs(np.min(lo[i+1:i+1+H]) / b - 1)) * 100
N["mae"] = mae
D = N.dropna(subset=["mae", "vix", "absr", "accel"])
print("sessions %d   %s .. %s" % (len(D), D.index.min().date(), D.index.max().date()))

def ols(cols, y, names):
    X = np.column_stack([np.ones(len(y))] + [D[c].values for c in cols])
    b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b
    se = np.sqrt(np.diag(np.linalg.pinv(X.T @ X) * (e @ e) / (len(y) - X.shape[1])))
    r2 = 1 - (e @ e) / ((y - y.mean()) ** 2).sum()
    return b, b / se, r2

y = D.mae.values
print("\n=== 1. INCREMENTAL VALUE (target = MAE over next %d sessions) ===" % H)
_, t1, r1 = ols(["vix"], y, None)
print("   VIX alone                    R2 = %5.1f%%   VIX t=%+.1f" % (r1 * 100, t1[1]))
_, t2, r2 = ols(["vix", "absr"], y, None)
print("   + |recent move|              R2 = %5.1f%%   |r| t=%+.1f" % (r2 * 100, t2[2]))
_, t3, r3 = ols(["vix", "absr", "accel"], y, None)
print("   + acceleration               R2 = %5.1f%%   accel t=%+.1f   dR2=%+.2f pp"
      % (r3 * 100, t3[3], (r3 - r2) * 100))
_, t4, r4 = ols(["vix", "absr", "accel", "continued"], y, None)
print("   + same-direction flag        R2 = %5.1f%%   cont t=%+.1f   dR2=%+.2f pp"
      % (r4 * 100, t4[4], (r4 - r3) * 100))
print("   >>> %s" % ("acceleration adds real incremental information"
                     if abs(t3[3]) > 2 and (r3 - r2) * 100 > 0.3 else
                     "acceleration adds essentially nothing once VIX and |recent move| "
                     "are already in the model"))

print("\n=== 2. THE STATE TABLE the engine would actually use ===")
print("   after a LARGE move (|r| > 1%%), split by what the NEXT session did:")
big = D[D.absr.shift(1) > 1.0].copy()
big["state"] = np.where(big.absr < 0.35, "stabilised",
                        np.where(big.continued > 0, "continued same way", "reversed"))
print("   %-24s %6s %11s %13s" % ("state on day t", "n", "median MAE", "P(breach)"))
print("   " + "-" * 58)
for s in ("stabilised", "continued same way", "reversed"):
    g = big[big.state == s]
    if len(g) < 20: continue
    print("   %-24s %6d %10.2f%% %12.0f%%" % (s, len(g), g.mae.median(),
                                              (g.mae > BAND).mean() * 100))
print("   %-24s %6d %10.2f%% %12.0f%%" % ("(all days, reference)", len(D), D.mae.median(),
                                          (D.mae > BAND).mean() * 100))

print("\n=== 3. IS 'STABILISED' ACTUALLY SAFER, or just lower VIX? ===")
st = big[big.state == "stabilised"]; co = big[big.state == "continued same way"]
print("   stabilised: median VIX %.1f   continued: median VIX %.1f"
      % (st.vix.median(), co.vix.median()))
lo_v, hi_v = D.vix.quantile(.33), D.vix.quantile(.67)
for lab, band in (("low VIX", D.vix <= lo_v), ("mid VIX", (D.vix > lo_v) & (D.vix <= hi_v)),
                  ("high VIX", D.vix > hi_v)):
    a = big[(big.state == "stabilised") & band.reindex(big.index).fillna(False)]
    b = big[(big.state == "continued same way") & band.reindex(big.index).fillna(False)]
    if len(a) < 10 or len(b) < 10: continue
    print("   %-9s stabilised MAE %.2f%% (n=%d)  vs continued %.2f%% (n=%d)  diff %+.2f pp"
          % (lab, a.mae.median(), len(a), b.mae.median(), len(b),
             a.mae.median() - b.mae.median()))
json.dump({"r2_vix": r1 * 100, "r2_vix_absr": r2 * 100, "r2_plus_accel": r3 * 100,
           "t_accel": float(t3[3]), "t_continued": float(t4[4])},
          open("velocity_test_result.json", "w"), indent=1)
print("\nwrote velocity_test_result.json")
