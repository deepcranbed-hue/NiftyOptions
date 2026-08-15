#!/usr/bin/env python3
"""rotation_daily -- FII flows are a DAILY number, so test them at daily frequency.

FII/DII cash is published after the close. It cannot speak to a 30-minute reversal, but
it can speak to the day's cross-section: when foreigners buy, WHICH sectors outperform?
That is a real question and this is the right frequency for it.

THE BINDING CONSTRAINT IS SAMPLE SIZE, and it is severe. fii_dii_flows holds 35 rows.
At n=35 the 2-sigma noise band is +/-0.33 -- so anything short of a very large
correlation is unreadable, and a correlation that DOES clear 0.33 at n=35 is still one
good month away from being a fluke. Reported anyway, with the band printed next to it,
because a null result at n=35 means "we cannot see", not "there is nothing".

Also tests same-day crude and rupee at daily frequency, where the moves are larger than
the intraday noise and any macro link should show up more clearly than it does at 15m.
"""
import sqlite3, json
import numpy as np, pandas as pd

SECT = ["NIFTYIT","NIFTYAUTO","NIFTYFMCG","NIFTYMETAL","NIFTYPHARMA","NIFTYFIN",
        "NIFTYPSU","NIFTYREALTY","NIFTYENERGY","NIFTYCONSUM","NIFTYINFRA"]
DRV = ["CRUDEOIL_MCX","USDINR","GOLD","INDIAVIX"]
con = sqlite3.connect("option_chains.db")
need = SECT + DRV + ["NIFTY"]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' "
                 "AND symbol IN (%s)" % ",".join("?" * len(need)), con, params=need)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index().ffill()
R = P.pct_change() * 100
fii = pd.read_sql("SELECT flow_date,fii_net,dii_net FROM fii_dii_flows ORDER BY flow_date", con)
fii["d"] = pd.to_datetime(fii.flow_date)
X = R.join(fii.set_index("d")[["fii_net", "dii_net"]], how="inner").dropna(subset=["fii_net"])
print("daily rows with BOTH prices and FII flows: %d   (%s to %s)"
      % (len(X), X.index.min().date(), X.index.max().date()))
band = 1.96 / np.sqrt(len(X))
print("2-sigma noise band at this n: +/-%.3f   <-- almost everything below is inside it\n" % band)

drivers = ["fii_net", "dii_net"] + [d for d in DRV if d in X]
hdr = "%-12s" % "sector(rel)" + "".join("%13s" % d.replace("_MCX", "")[:12] for d in drivers)
print(hdr); print("-" * len(hdr))
out = {}
for s in SECT:
    if s not in X: continue
    rel = X[s] - X["NIFTY"]
    line = "%-12s" % s.replace("NIFTY", ""); out[s] = {}
    for d in drivers:
        m = rel.notna() & X[d].notna()
        r = float(np.corrcoef(rel[m], X[d][m])[0, 1]) if m.sum() > 10 else np.nan
        out[s][d] = None if np.isnan(r) else round(r, 3)
        line += "%13s" % (("%+.3f" % r + ("*" if abs(r) > band else "")) if not np.isnan(r) else "--")
    print(line)
print("\n* = outside the 2-sigma band. With %d sectors x %d drivers = %d tests, expect"
      % (len(out), len(drivers), len(out) * len(drivers)))
print("  ~%.0f stars from chance alone -- count them before believing any single one."
      % (len(out) * len(drivers) * 0.05))

# does FII flow explain the SIZE of the rotation (dispersion), not its direction?
disp = X[[s for s in SECT if s in X]].sub(X["NIFTY"], axis=0).std(axis=1)
for d in ("fii_net", "dii_net"):
    m = disp.notna() & X[d].notna()
    print("\ndispersion of sector rel-returns vs %-8s : r = %+.3f   |%s| vs band %.3f"
          % (d, np.corrcoef(disp[m], X[d][m])[0, 1], d, band))
    print("  vs |%s| (size of flow, either direction) : r = %+.3f"
          % (d, np.corrcoef(disp[m], X[d][m].abs())[0, 1]))
json.dump({"n_days": len(X), "noise_band": round(band, 3), "corr": out,
           "note": "n=35: a null here means 'cannot see', not 'nothing there'."},
          open("rotation_daily_result.json", "w"), indent=1)
print("\nwrote rotation_daily_result.json")
