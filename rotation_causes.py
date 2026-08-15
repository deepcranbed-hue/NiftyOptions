#!/usr/bin/env python3
"""rotation_causes -- is the rotation oil, the rupee, FII, or fundamentals?

FREQUENCY IS THE FIRST FILTER, and it settles two of the four before any test runs:

  FUNDAMENTALS  cannot move at 15-minute frequency. Earnings, ROE and book value are
                quarterly. The repo's `fundamentals` table holds 55 TTM snapshots with
                no time dimension at all. A 30-minute reversal is not a fundamentals
                story, and no test is needed to say so -- only a clock.
  FII FLOWS     are published DAILY, after the close (35 rows here, from 2026-06-18).
                A number that prints once a day cannot explain what happens between
                11:15 and 11:45. It may well explain the DAY's cross-section; that is a
                different question, tested separately at daily frequency.

  OIL and the RUPEE are the two that CAN be tested at matched frequency, because this
  repo carries CRUDEOIL_MCX, USDINR, GOLD and INDIAVIX as 1-minute bars alongside the
  sector indices. So they get a real test.

TWO QUESTIONS, deliberately separated:
  Q1 WHAT DRIVES THE CROSS-SECTION: does a sector's relative strength track crude and
     the rupee intraday? (Is "IT is strong" really "the rupee is weak"?)
  Q2 WHAT DRIVES THE REVERSAL: if crude and the rupee are PARTIALLED OUT of dRS, does
     the mean-reversion found earlier survive? If it vanishes, the reversal was macro
     noise being corrected. If it survives, it is a liquidity/inventory effect that has
     nothing to do with either.

Q2 is the one that matters: it asks whether the effect we measured is macro at all.
Official NSE sector indices are used here rather than the hand-built buckets, so
"Financial Services" no longer lumps HDFC Bank with insurers.
"""
import sqlite3, json
import numpy as np, pandas as pd

SECT = ["NIFTYIT","NIFTYAUTO","NIFTYFMCG","NIFTYMETAL","NIFTYPHARMA","NIFTYFIN",
        "NIFTYPSU","NIFTYREALTY","NIFTYENERGY","NIFTYCONSUM","NIFTYINFRA"]
MACRO = ["CRUDEOIL_MCX","USDINR","GOLD","INDIAVIX"]
STEP = 15                      # sampling / lookback, minutes
FWD = 30                       # forward window, minutes

con = sqlite3.connect("option_chains.db")
need = SECT + MACRO + ["NIFTY"]
df = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1m' "
                 "AND symbol IN (%s)" % ",".join("?" * len(need)), con, params=need)
df["ts"] = pd.to_datetime(df["ts"].str.replace("Z", "", regex=False))
df["day"] = df["ts"].dt.date

rows = []
for day, g in df.groupby("day"):
    px = g.pivot_table(index="ts", columns="symbol", values="close").sort_index().ffill()
    if "NIFTY" not in px or px.shape[0] < 150:
        continue
    have = [s for s in SECT if s in px and px[s].notna().sum() > 150]
    hm = [m for m in MACRO if m in px and px[m].notna().sum() > 150]
    if len(have) < 6:
        continue
    # Base off each column's FIRST VALID bar, not row 0: MCX crude, USDINR and the
    # sector indices open at different times, so px.iloc[0] is NaN for some of them
    # and dividing by it silently NaNs the entire column.
    base = px.bfill().iloc[0]
    r = px.divide(base, axis=1) - 1.0
    idx = np.arange(STEP, len(r) - FWD, STEP)
    for t in idx:
        row = {"day": str(day), "t": int(t)}
        for m in hm:
            row["m::" + m] = (r[m].values[t] - r[m].values[t - STEP]) * 100
        for s in have:
            rs = (r[s].values - r["NIFTY"].values)
            row["d::" + s] = (rs[t] - rs[t - STEP]) * 100
            row["f::" + s] = (rs[t + FWD] - rs[t]) * 100
        rows.append(row)
D = pd.DataFrame(rows)
print("sessions %d   observations %d   sectors %d   macro %d"
      % (D.day.nunique(), len(D), sum(c.startswith("d::") for c in D.columns),
         sum(c.startswith("m::") for c in D.columns)))

macros = [c for c in D.columns if c.startswith("m::")]
secs = [c[3:] for c in D.columns if c.startswith("d::")]


def cc(a, b):
    m = a.notna() & b.notna()
    if m.sum() < 50: return np.nan
    return float(np.corrcoef(a[m], b[m])[0, 1])


print("\nQ1 -- does 15-min sector relative strength TRACK crude / the rupee?")
print("     (contemporaneous r; |r|<%.3f is inside 2-sigma noise)" % (1.96 / np.sqrt(len(D))))
hdr = "%-14s" % "sector" + "".join("%14s" % m[3:] for m in macros)
print(hdr); print("-" * len(hdr))
q1 = {}
for s in secs:
    line = "%-14s" % s.replace("NIFTY", "")
    q1[s] = {}
    for m in macros:
        v = cc(D["d::" + s], D[m]); q1[s][m[3:]] = None if np.isnan(v) else round(v, 3)
        line += "%14s" % ("%+.3f" % v if not np.isnan(v) else "--")
    print(line)

print("\nQ2 -- does the REVERSAL survive partialling out crude + rupee + gold + VIX?")
print("%-14s %12s %12s %10s" % ("sector", "raw r", "residual r", "n"))
print("-" * 50)
X = D[macros].fillna(0.0).values
X = np.column_stack([np.ones(len(X)), X])
q2 = {}
for s in secs:
    d, f = D["d::" + s], D["f::" + s]
    m = d.notna() & f.notna()
    if m.sum() < 50: continue
    raw = cc(d, f)
    # residualise BOTH sides on the macro factors, then re-correlate
    Xm = X[m.values]
    bd = np.linalg.lstsq(Xm, d[m].values, rcond=None)[0]
    bf = np.linalg.lstsq(Xm, f[m].values, rcond=None)[0]
    rd, rf = d[m].values - Xm @ bd, f[m].values - Xm @ bf
    res = float(np.corrcoef(rd, rf)[0, 1])
    q2[s] = {"raw_r": round(raw, 3), "residual_r": round(res, 3), "n": int(m.sum())}
    print("%-14s %12.3f %12.3f %10d" % (s.replace("NIFTY", ""), raw, res, m.sum()))

rawm = np.mean([v["raw_r"] for v in q2.values()])
resm = np.mean([v["residual_r"] for v in q2.values()])
kept = resm / rawm * 100 if rawm else 0
print("\nmean raw %+.3f -> mean residual %+.3f   (%.0f%% of the effect survives)"
      % (rawm, resm, kept))
print(">>> %s" % ("MACRO does NOT explain the reversal -- it is a liquidity/inventory "
                  "effect, unrelated to oil or the rupee" if kept > 80 else
                  "macro explains a material share of the reversal"))
json.dump({"step_min": STEP, "forward_min": FWD, "sessions": int(D.day.nunique()),
           "observations": len(D), "q1_contemporaneous": q1, "q2_residualised": q2,
           "mean_raw_r": round(rawm, 4), "mean_residual_r": round(resm, 4),
           "pct_effect_surviving_macro_controls": round(kept, 1)},
          open("rotation_causes_result.json", "w"), indent=1)
print("wrote rotation_causes_result.json")
