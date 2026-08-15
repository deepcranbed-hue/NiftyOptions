#!/usr/bin/env python3
"""rotation_fii -- the FII question at a sample size that can actually answer it.

fii_dii_flows holds 35 days: a +/-0.331 noise band, wide enough to hide almost anything.
participant_flows holds 248 days of FII/DII/Client/Pro F&O positioning -- 7x the sample
and a +/-0.124 band. Different quantity (index-futures POSITIONING, not cash traded), but
it is the flow series this repo actually has enough of.

WHAT IS TESTED
  net = idx_fut_long - idx_fut_short for FII, and its DAILY CHANGE. The change is the
  signal: a level tells you the standing book, a change tells you what they did today.
  Correlated against each sector's return RELATIVE to Nifty, so this measures rotation,
  not direction.

  Also: does the FII position change predict the NEXT day's rotation? A same-day
  correlation is nearly tautological -- FII buying and prices rising are the same event
  seen twice. The lagged test is the one with any content, and it is reported alongside.
"""
import sqlite3, json
import numpy as np, pandas as pd

SECT = ["NIFTYIT","NIFTYAUTO","NIFTYFMCG","NIFTYMETAL","NIFTYPHARMA","NIFTYFIN",
        "NIFTYPSU","NIFTYREALTY","NIFTYENERGY","NIFTYCONSUM","NIFTYINFRA"]
con = sqlite3.connect("option_chains.db")
need = SECT + ["NIFTY"]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' "
                 "AND symbol IN (%s)" % ",".join("?" * len(need)), con, params=need)
px["d"] = pd.to_datetime(px.ts.str[:10])
R = px.pivot_table(index="d", columns="symbol", values="close").sort_index().ffill().pct_change() * 100

pf = pd.read_sql("SELECT flow_date,participant_type,idx_fut_long,idx_fut_short,"
                 "stk_fut_long,stk_fut_short FROM participant_flows "
                 "WHERE participant_type='FII' ORDER BY flow_date", con)
pf["d"] = pd.to_datetime(pf.flow_date)
pf = pf.set_index("d")
pf["idx_net"] = pf.idx_fut_long - pf.idx_fut_short
pf["stk_net"] = pf.stk_fut_long - pf.stk_fut_short
pf["d_idx_net"] = pf.idx_net.diff()
pf["d_stk_net"] = pf.stk_net.diff()

X = R.join(pf[["idx_net", "d_idx_net", "d_stk_net"]], how="inner").dropna(subset=["d_idx_net"])
band = 1.96 / np.sqrt(len(X))
print("days with both prices and FII positioning: %d  (%s to %s)"
      % (len(X), X.index.min().date(), X.index.max().date()))
print("2-sigma noise band: +/-%.3f\n" % band)

drv = ["d_idx_net", "d_stk_net", "idx_net"]
hdr = "%-10s" % "sector" + "".join("%14s" % d for d in drv) + "%16s" % "d_idx_net(t-1)"
print(hdr); print("-" * len(hdr))
out, stars = {}, 0
for s in SECT:
    if s not in X: continue
    rel = X[s] - X["NIFTY"]
    line = "%-10s" % s.replace("NIFTY", ""); out[s] = {}
    for d in drv:
        m = rel.notna() & X[d].notna()
        r = float(np.corrcoef(rel[m], X[d][m])[0, 1])
        out[s][d] = round(r, 3); stars += abs(r) > band
        line += "%14s" % ("%+.3f%s" % (r, "*" if abs(r) > band else ""))
    lag = X["d_idx_net"].shift(1)
    m = rel.notna() & lag.notna()
    rl = float(np.corrcoef(rel[m], lag[m])[0, 1])
    out[s]["d_idx_net_lag1"] = round(rl, 3); stars += abs(rl) > band
    line += "%16s" % ("%+.3f%s" % (rl, "*" if abs(rl) > band else ""))
    print(line)

n_tests = len(out) * 4
print("\n* outside 2-sigma. %d tests -> ~%.1f stars expected by chance; observed %d."
      % (n_tests, n_tests * 0.05, stars))
lags = [v["d_idx_net_lag1"] for v in out.values()]
same = [v["d_idx_net"] for v in out.values()]
print("mean |same-day r| = %.3f    mean |next-day r| = %.3f"
      % (np.mean(np.abs(same)), np.mean(np.abs(lags))))
print(">>> %s" % ("FII positioning shows a same-day footprint in the cross-section; "
                  "the NEXT-day link is what would be usable, and it is %s"
                  % ("present" if np.mean(np.abs(lags)) > band else "absent")))
json.dump({"n_days": len(X), "noise_band": round(band, 3), "corr": out,
           "stars_observed": int(stars), "stars_expected": round(n_tests * 0.05, 1)},
          open("rotation_fii_result.json", "w"), indent=1)
print("wrote rotation_fii_result.json")
