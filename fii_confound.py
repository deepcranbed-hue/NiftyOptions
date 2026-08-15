#!/usr/bin/env python3
"""fii_confound -- is the FII positioning result actually expiry seasonality?

THE CONFOUND. cycle_pos is a cumulative sum that RESTARTS at every monthly expiry, so it
is mechanically tied to how far through the cycle a day sits. FII build net SHORT across
this sample (range -218,267 to +69,543), so the series drifts downward within each cycle:
  "most long"  days cluster EARLY in a cycle
  "most short" days cluster LATE  in a cycle
If Nifty has any day-of-cycle seasonality at all -- and expiry effects are well documented
-- then a calendar pattern would reproduce the exact quintile table already seen, with FII
contributing nothing. That table cannot distinguish the two.

THREE TESTS THAT CAN
  A. How strong is the mechanical link? corr(cycle_pos, day-of-cycle).
  B. Run the SAME quintile analysis on day-of-cycle alone. If the calendar reproduces the
     spread, the FII reading is not identified.
  C. RESIDUALISE cycle_pos on day-of-cycle (and its square), then re-bucket on what is
     left -- the part of FII positioning that is NOT explained by where we are in the
     cycle. If the spread survives that, it is FII. If it collapses, it was the calendar.

C is the one that decides it.
"""
import calendar, json, sqlite3
from datetime import date, timedelta
import numpy as np, pandas as pd

HOR = [1, 2, 3, 5]; N_PERM = 400
RNG = np.random.default_rng(4)
con = sqlite3.connect("option_chains.db")
pf = pd.read_sql("SELECT flow_date,idx_fut_long,idx_fut_short FROM participant_flows "
                 "WHERE participant_type='FII' ORDER BY flow_date", con)
pf["flow"] = pf.idx_fut_long - pf.idx_fut_short
pf["d"] = pd.to_datetime(pf.flow_date); pf = pf.set_index("d")[["flow"]]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' "
                 "AND symbol='NIFTY'", con)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.set_index("d")["close"].sort_index()

def last_tue(y, m):
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() != 1: d -= timedelta(days=1)
    return pd.Timestamp(d)

exp = {last_tue(d.year, d.month) for d in pf.index}
cyc, doc, run, k = [], [], 0.0, 0
for d in pf.index:
    run += float(pf.at[d, "flow"]); k += 1
    cyc.append(run); doc.append(k)
    if d in exp: run, k = 0.0, 0
pf["cycle_pos"], pf["day_of_cycle"] = cyc, doc
D = pf.join(P.rename("NIFTY"), how="inner").dropna()
for h in HOR:
    D["f%d" % h] = (D.NIFTY.shift(-h) / D.NIFTY - 1) * 100

def spread(series, col):
    q = pd.qcut(series, 5, labels=False, duplicates="drop")
    return {h: D["f%d" % h][q == 4].mean() - D["f%d" % h][q == 0].mean() for h in HOR}

def null_p(series_vals, obs):
    n = len(series_vals); out = {h: [] for h in HOR}
    for _ in range(N_PERM):
        sh = pd.Series(np.roll(series_vals, int(RNG.integers(10, n - 10))), index=D.index)
        s = spread(sh, None)
        for h in HOR: out[h].append(s[h])
    return {h: float((np.abs(np.array(out[h])) >= abs(obs[h])).mean()) for h in HOR}

r_link = float(np.corrcoef(D.cycle_pos, D.day_of_cycle)[0, 1])
print("A. corr(cycle_pos, day_of_cycle) = %+.3f   %s"
      % (r_link, "STRONG mechanical link -- the confound is live" if abs(r_link) > 0.4
         else "weak link"))
print("   cycle_pos mean %.0f (net SHORT bias)  day_of_cycle 1..%d"
      % (D.cycle_pos.mean(), D.day_of_cycle.max()))

print("\nB. same analysis on DAY-OF-CYCLE alone (no FII data at all):")
s_doc = spread(D.day_of_cycle, None); p_doc = null_p(D.day_of_cycle.values, s_doc)
print("   %-10s" % "horizon" + "".join("%10s" % ("+%dd" % h) for h in HOR))
print("   %-10s" % "spread" + "".join("%10.3f" % s_doc[h] for h in HOR))
print("   %-10s" % "p" + "".join("%10.3f" % p_doc[h] for h in HOR))

print("\nC. cycle_pos RESIDUALISED on day-of-cycle (+ its square):")
X = np.column_stack([np.ones(len(D)), D.day_of_cycle, D.day_of_cycle ** 2])
b = np.linalg.lstsq(X, D.cycle_pos.values, rcond=None)[0]
resid = D.cycle_pos.values - X @ b
print("   variance of cycle_pos explained by the calendar: %.1f%%"
      % ((1 - resid.var() / D.cycle_pos.var()) * 100))
s_raw = spread(D.cycle_pos, None)
s_res = spread(pd.Series(resid, index=D.index), None)
p_res = null_p(resid, s_res)
print("   %-14s" % "horizon" + "".join("%10s" % ("+%dd" % h) for h in HOR))
print("   %-14s" % "raw spread" + "".join("%10.3f" % s_raw[h] for h in HOR))
print("   %-14s" % "residualised" + "".join("%10.3f" % s_res[h] for h in HOR))
print("   %-14s" % "p (null)" + "".join("%10.3f" % p_res[h] for h in HOR))
kept = abs(s_res[5]) / abs(s_raw[5]) * 100 if s_raw[5] else 0
print("\n   %.0f%% of the +5d spread survives removing the calendar" % kept)
print(">>> %s" % ("FII positioning carries information beyond the expiry calendar"
                 if (kept > 60 and p_res[5] < 0.10) else
                 "the spread is largely the EXPIRY CALENDAR, not FII positioning"))
json.dump({"corr_pos_dayofcycle": round(r_link, 3),
           "spread_day_of_cycle": {str(h): s_doc[h] for h in HOR},
           "p_day_of_cycle": {str(h): p_doc[h] for h in HOR},
           "spread_raw": {str(h): s_raw[h] for h in HOR},
           "spread_residualised": {str(h): s_res[h] for h in HOR},
           "p_residualised": {str(h): p_res[h] for h in HOR},
           "pct_5d_spread_surviving": round(kept, 1)},
          open("fii_confound_result.json", "w"), indent=1)
print("wrote fii_confound_result.json")
