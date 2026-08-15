#!/usr/bin/env python3
"""drawdown -- worst cycles and the strategy equity curve, with dates and index levels.

NON-OVERLAPPING cycles: one trade per weekly, entered every 6th session. Overlapping
windows would count the same crash 6 times and inflate both the drawdown and its duration.

Short strangle +/-0.62%, priced off the measured smile, held to expiry, 1pt/leg slippage.
Where the worst day falls inside the 1-minute capture window (2026-06-28 .. 2026-08-10)
the timing is resolvable to the minute; before that only daily OHLC exists, so the damage
is attributed to GAP vs INTRADAY instead.
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
n = pd.read_sql("SELECT ts,open,high,low,close FROM price_bars WHERE timeframe='1d' AND "
                "symbol='NIFTY' ORDER BY ts", con)
n["d"] = pd.to_datetime(n.ts.str[:10]); N = n.set_index("d")
v = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
v["d"] = pd.to_datetime(v.ts.str[:10])
N = N.join(v.set_index("d")["close"].rename("vix"), how="inner")
idx = N.index; op, hi, lo, cl, vx = N.open.values, N.high.values, N.low.values, N.close.values, N.vix.values

rows = []
for i in range(0, len(N) - LIFE - 1, LIFE):
    S, vi = cl[i], vx[i]
    if not (S > 0 and vi > 0): continue
    ck, pk = S * (1 + SHORT / 100), S * (1 - SHORT / 100)
    T0 = LIFE / 252.0
    credit = bs(S, ck, T0, vi/100*smile(SHORT), True) + bs(S, pk, T0, vi/100*smile(-SHORT), False)
    ST = cl[i + LIFE]
    pay = max(ST - ck, 0) + max(pk - ST, 0)
    pnl = (credit - pay) / S * 100 - 2 * SLIP
    seg = slice(i + 1, i + 1 + LIFE)
    up = (np.max(hi[seg]) / S - 1) * 100; dn = (np.min(lo[seg]) / S - 1) * 100
    mae_i = i + 1 + (int(np.argmax(hi[seg])) if abs(up) > abs(dn) else int(np.argmin(lo[seg])))
    gap = (op[mae_i] / cl[mae_i - 1] - 1) * 100
    intra = (cl[mae_i] / op[mae_i] - 1) * 100
    rows.append({"entry": idx[i].date(), "exit": idx[i + LIFE].date(), "S0": S, "ST": ST,
                 "move": (ST / S - 1) * 100, "vix0": vi, "vix1": vx[i + LIFE],
                 "mae": up if abs(up) > abs(dn) else dn, "worst_day": idx[mae_i].date(),
                 "gap": gap, "intra": intra, "pnl": pnl})
T = pd.DataFrame(rows)
T["equity"] = T.pnl.cumsum()
T["peak"] = T.equity.cummax(); T["dd"] = T.equity - T.peak

print("=== STRATEGY EQUITY (non-overlapping weeklies, %d cycles, %s .. %s) ==="
      % (len(T), T.entry.iloc[0], T.exit.iloc[-1]))
print("   total %+.1f%% of spot   mean/cycle %+.3f%%   win rate %.0f%%"
      % (T.equity.iloc[-1], T.pnl.mean(), (T.pnl > 0).mean() * 100))
tr = int(T.dd.idxmin()); pk = int(T.loc[:tr, "equity"].idxmax())
rec = T[(T.index > tr) & (T.equity >= T.equity[pk])]
print("   MAX DRAWDOWN %.1f%% of spot" % T.dd.min())
print("      peak    %s  equity %+.1f%%" % (T.entry[pk], T.equity[pk]))
print("      trough  %s  equity %+.1f%%  (%d cycles, %d weeks)"
      % (T.exit[tr], T.equity[tr], tr - pk, tr - pk))
print("      recovered %s" % (rec.entry.iloc[0] if len(rec) else "NOT YET RECOVERED"))

print("\n=== 15 WORST CYCLES ===")
print("%-11s %-11s %8s %8s %8s %7s %7s %8s %-11s %7s %7s"
      % ("entry", "exit", "spot in", "spot out", "move", "VIX in", "VIX out", "MAE", "worst day",
         "gap", "intra"))
print("-" * 116)
for r in T.nsmallest(15, "pnl").itertuples():
    print("%-11s %-11s %8.0f %8.0f %+7.2f%% %7.1f %7.1f %+7.2f%% %-11s %+6.2f%% %+6.2f%%   P/L %+.2f%%"
          % (r.entry, r.exit, r.S0, r.ST, r.move, r.vix0, r.vix1, r.mae, r.worst_day,
             r.gap, r.intra, r.pnl))

print("\n=== DRAWDOWN EPISODES (peak-to-trough worse than 2%% of spot) ===")
T["grp"] = (T.dd == 0).cumsum()
eps = []
for g, s in T.groupby("grp"):
    if s.dd.min() < -2:
        t = s.dd.idxmin()
        eps.append((s.entry.iloc[0], T.exit[t], s.dd.min(), len(s), T.vix0[t]))
for e in sorted(eps, key=lambda x: x[2])[:8]:
    print("   %s -> %s   %.1f%%   %d cycles   VIX at trough entry %.1f" % e)
T.to_json("drawdown_result.json", orient="records", indent=1)
print("\n   1-minute data exists only 2026-06-28..2026-08-10, so time-of-day is not")
print("   resolvable for older episodes -- gap vs intraday is the finest split available.")
