#!/usr/bin/env python3
"""bounce_test -- does recovering off the low change the path risk that follows?

THE CLAIM: "-200 then -100" and "-200 then +65" should not produce the same gamma signal.
The second is a shock being absorbed; the first is a shock continuing. Intuitively obvious,
and untested.

THE MEASURE. The intraday version needs tick data (31 days here). The daily analogue has
2,100 observations and captures the same thing: WHERE IN THE DAY'S RANGE DID IT CLOSE?

    close_pos = (close - low) / (high - low)      0 = closed on its low
                                                  1 = closed on its high

A day that falls 200 points and closes on its low is close_pos ~0. A day that falls 200,
bottoms, then recovers 65 into the close is close_pos ~0.3-0.4. That is exactly the
distinction being drawn, and it is measurable back to 2018.

TARGETS, at the horizons a short-gamma position actually cares about:
    MAE over the next 1, 2 and 6 sessions -- the excursion, on intraday highs and lows.

AND THE TEST THAT MATTERS IS INCREMENTAL. close_pos will look informative on its own,
because a day closing on its low tends to be a high-volatility day and VIX already knows
that. The question is whether it survives VIX and |recent move|.
"""
import sqlite3, json
import numpy as np, pandas as pd

BAND = 1.65
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
rng = (N.high - N.low).replace(0, np.nan)
N["close_pos"] = (N.close - N.low) / rng
N["bounce_pct"] = (N.close / N.low - 1) * 100          # how far it recovered off the low

hi, lo, cl = N.high.values, N.low.values, N.close.values
for H in (1, 2, 6):
    m = np.full(len(N), np.nan)
    for i in range(len(N) - H):
        b = cl[i]
        m[i] = max(abs(np.max(hi[i+1:i+1+H]) / b - 1),
                   abs(np.min(lo[i+1:i+1+H]) / b - 1)) * 100
    N["mae%d" % H] = m
D = N.dropna(subset=["mae6", "vix", "absr", "close_pos"])
print("sessions %d   %s .. %s" % (len(D), D.index.min().date(), D.index.max().date()))
print("close_pos: median %.2f  (0 = closed on the low, 1 = on the high)" % D.close_pos.median())

def ols(cols, y):
    X = np.column_stack([np.ones(len(y))] + [D[c].values for c in cols])
    b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b
    se = np.sqrt(np.diag(np.linalg.pinv(X.T @ X) * (e @ e) / (len(y) - X.shape[1])))
    return b / se, 1 - (e @ e) / ((y - y.mean()) ** 2).sum()

print("\n=== 1. INCREMENTAL VALUE of close_pos, at three horizons ===")
print("   %-10s %14s %16s %14s %10s" % ("target", "VIX+|r| R2", "+close_pos R2", "close_pos t", "dR2"))
print("   " + "-" * 68)
res = {}
for H in (1, 2, 6):
    y = D["mae%d" % H].values
    _, r2a = ols(["vix", "absr"], y)
    t, r2b = ols(["vix", "absr", "close_pos"], y)
    res["mae%d" % H] = {"t": float(t[3]), "dr2": float((r2b - r2a) * 100)}
    print("   %-10s %13.1f%% %15.1f%% %14.1f %9.2fpp"
          % ("MAE %dd" % H, r2a * 100, r2b * 100, t[3], (r2b - r2a) * 100))

print("\n=== 2. THE STATE TABLE: after a DOWN day, split by where it closed in its range ===")
dn = D[D.r < -0.5].copy()
dn["q"] = pd.qcut(dn.close_pos, 4, labels=["Q1 closed on low", "Q2", "Q3", "Q4 closed on high"])
print("   %-22s %6s %8s %10s %10s %11s" % ("", "n", "med VIX", "MAE 1d", "MAE 6d", "P(breach 6d)"))
print("   " + "-" * 72)
for lab in dn.q.cat.categories:
    g = dn[dn.q == lab]
    print("   %-22s %6d %8.1f %9.2f%% %9.2f%% %10.0f%%"
          % (lab, len(g), g.vix.median(), g.mae1.median(), g.mae6.median(),
             (g.mae6 > BAND).mean() * 100))
print("   %-22s %6d %8.1f %9.2f%% %9.2f%% %10.0f%%"
      % ("(all days)", len(D), D.vix.median(), D.mae1.median(), D.mae6.median(),
         (D.mae6 > BAND).mean() * 100))

print("\n=== 3. IS IT JUST VIX AGAIN? same split, inside VIX buckets ===")
loq, hiq = D.vix.quantile(.33), D.vix.quantile(.67)
for lab, band in (("VIX low", dn.vix <= loq), ("VIX mid", (dn.vix > loq) & (dn.vix <= hiq)),
                  ("VIX high", dn.vix > hiq)):
    a = dn[band & (dn.close_pos <= dn.close_pos.quantile(.25))]
    b = dn[band & (dn.close_pos >= dn.close_pos.quantile(.75))]
    if len(a) < 15 or len(b) < 15: continue
    print("   %-9s closed-on-LOW MAE6 %.2f%% (n=%d)   closed-on-HIGH %.2f%% (n=%d)   diff %+.2f pp"
          % (lab, a.mae6.median(), len(a), b.mae6.median(), len(b),
             b.mae6.median() - a.mae6.median()))
json.dump(res, open("bounce_test_result.json", "w"), indent=1)
print("\nwrote bounce_test_result.json")
