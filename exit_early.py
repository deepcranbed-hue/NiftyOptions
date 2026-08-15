#!/usr/bin/env python3
"""exit_early -- does closing before expiry avoid the expiry-day gamma, and what does it cost?

EVERY SIMULATION SO FAR SETTLED AT EXPIRY. That is a design error: expiry day carries the
largest gamma of the cycle, and a roll-before-expiry strategy is never there for it. So
this re-runs the structures with an EXIT k sessions early, valuing the remaining legs at
the prevailing spot and VIX with the measured smile, rather than settling at intrinsic.

FIRST the premise is checked -- is expiry day actually wilder? NSE weeklies expire Tuesday,
so Tuesday's intraday range is compared with every other weekday over 2018-2026.

THEN the trade-off. Exiting early gives up the fastest theta of the cycle. Whether that is
worth avoiding the largest gamma of the cycle is exactly what the table answers.
"""
import sqlite3, math
import numpy as np, pandas as pd

SHORT, SLIP, LIFE = 0.62, 0.0041, 6
SM_X = np.array([-2.30, -1.75, -1.25, -0.75, -0.275, 0.275, 0.75, 1.25, 1.75, 2.30])
SM_Y = np.array([14.89, 14.09, 13.47, 13.00, 12.60, 12.91, 12.73, 12.56, 12.64, 12.89]) / 12.75
def smile(m): return float(np.interp(m, SM_X, SM_Y, left=SM_Y[0], right=SM_Y[-1]))
def _cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
def bs(S, K, T, s, call):
    if T <= 0 or s <= 0: return max(S - K, 0.0) if call else max(K - S, 0.0)
    d1 = (math.log(S / K) + 0.5 * s * s * T) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
    return (S * _cdf(d1) - K * _cdf(d2)) if call else (K * _cdf(-d2) - S * _cdf(-d1))

con = sqlite3.connect("option_chains.db")
n = pd.read_sql("SELECT ts,high,low,close FROM price_bars WHERE timeframe='1d' AND "
                "symbol='NIFTY' ORDER BY ts", con)
n["d"] = pd.to_datetime(n.ts.str[:10]); N = n.set_index("d")
v = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
v["d"] = pd.to_datetime(v.ts.str[:10])
N = N.join(v.set_index("d")["close"].rename("vix"), how="inner")
N["rng"] = (N.high - N.low) / N.close.shift(1) * 100
N["dow"] = N.index.dayofweek

print("=== 0. IS EXPIRY DAY ACTUALLY WILDER?  intraday range by weekday ===")
print("   %-14s %7s %11s %11s" % ("weekday", "n", "med range", "mean range"))
for k, lab in ((0, "Monday"), (1, "TUESDAY (expiry)"), (2, "Wednesday"), (3, "Thursday"),
               (4, "Friday")):
    s = N[N.dow == k].dropna(subset=["rng"])
    if len(s) < 50: continue
    print("   %-14s %7d %10.3f%% %10.3f%%" % (lab, len(s), s.rng.median(), s.rng.mean()))
tue = N[N.dow == 1].rng.median(); oth = N[N.dow != 1].rng.median()
print("   Tuesday vs the rest: %+.1f%% relative   -> %s"
      % ((tue / oth - 1) * 100,
         "premise SUPPORTED" if tue > oth * 1.03 else
         "premise NOT supported at the index level -- weekly expiry does not widen the "
         "index's own range"))

# ---- exit-early simulation ----
cl, vx = N.close.values, N.vix.values
print("\n=== 1. EXIT k SESSIONS EARLY (short strangle +/-%.2f%%, 6-session weekly) ===" % SHORT)
print("   %-22s %9s %9s %9s %9s %6s %9s" % ("hold / exit", "mean", "ES(5%)", "ES(1%)",
                                            "worst", "win", "P/L per ES"))
print("   " + "-" * 80)
rows = {}
for hold in (6, 5, 4, 3):
    left = LIFE - hold
    out = []
    for i in range(len(N) - LIFE - 1):
        S, vi = cl[i], vx[i]
        if not (S > 0 and vi > 0): continue
        ck, pk = S * (1 + SHORT / 100), S * (1 - SHORT / 100)
        T0 = LIFE / 252.0
        credit = (bs(S, ck, T0, vi / 100 * smile(SHORT), True)
                  + bs(S, pk, T0, vi / 100 * smile(-SHORT), False))
        ST, vT = cl[i + hold], vx[i + hold]
        Tl = left / 252.0
        mc, mp = (ck / ST - 1) * 100, (pk / ST - 1) * 100
        ex = (bs(ST, ck, Tl, vT / 100 * smile(mc), True)
              + bs(ST, pk, Tl, vT / 100 * smile(mp), False))
        out.append((credit - ex) / S * 100 - 2 * SLIP * (2 if left > 0 else 1))
    a = np.array(out); s5 = np.sort(a)[:max(int(len(a) * .05), 1)].mean()
    s1 = np.sort(a)[:max(int(len(a) * .01), 1)].mean()
    rows[hold] = (a.mean(), s5, s1, a.min(), (a > 0).mean(), a.mean() / abs(s5))
    lab = "hold all %d (to expiry)" % hold if left == 0 else "hold %d, exit %dd early" % (hold, left)
    print("   %-22s %+9.4f %+9.4f %+9.4f %+9.4f %5.0f%% %9.3f"
          % (lab, a.mean(), s5, s1, a.min(), (a > 0).mean() * 100, a.mean() / abs(s5)))

print("\n=== 2. WHAT THE LAST SESSIONS CONTRIBUTE ===")
for a, b in ((6, 5), (5, 4), (4, 3)):
    dm = rows[a][0] - rows[b][0]; de = rows[a][1] - rows[b][1]
    print("   session %d->%d:  adds %+.4f%% of mean P/L  and %+.4f%% of ES  ->  marginal ratio %.2f"
          % (b, a, dm, de, dm / abs(de) if de else float("nan")))
print("\n   A marginal ratio BELOW the whole-cycle P/L-per-ES means that session is")
print("   destroying risk-adjusted return even while it adds raw P&L.")
