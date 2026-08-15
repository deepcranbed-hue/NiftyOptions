#!/usr/bin/env python3
"""path_risk -- does shock containment predict PATH excursion, not terminal outcome?

WHY THIS IS A NEW TEST. Three earlier tests failed and none of them measured the right
thing for a short strangle:
    next-day mean |move|         p=0.380   -- wrong statistic (mean, not tail)
    next-day P(>2x implied)      p=0.183   -- right statistic, wrong horizon (one day)
    breadth -> next-day return   t=1.20    -- wrong question entirely (direction)
A strangle is killed by the WORST POINT of the path before expiry, not by where the path
ends. A week that finishes flat after touching -2% on day two can force a defensive
adjustment and realise the loss anyway. So the target here is MAXIMUM ADVERSE EXCURSION
over the holding period, measured on intraday HIGHS AND LOWS rather than closes, because
a close-only excursion understates what actually hits the position.

    MAE = max over the next 6 sessions of |extreme / entry spot - 1|

Also decomposed, because it bears on what can be managed: how much of the excursion
arrives in OVERNIGHT GAPS versus during the session. Gap risk is unhedgeable for a
position held overnight, and this session established that the overnight channel is the
one that reliably carries information.
"""
import sqlite3, json
import numpy as np, pandas as pd

RNG = np.random.default_rng(4242)
H = 6                      # sessions in a weekly holding period
BAND = 1.65                # % -- breakeven band for +/-150 strikes with ~250 premium
con = sqlite3.connect("option_chains.db")
csv = pd.read_csv("nifty-50-stock-list.csv")
syms = [s.strip() for s in csv["Symbol"].dropna()]
px = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                 "(%s)" % ",".join("?" * len(syms)), con, params=syms)
px["d"] = pd.to_datetime(px.ts.str[:10])
P = px.pivot_table(index="d", columns="symbol", values="close").sort_index()
n = pd.read_sql("SELECT ts,open,high,low,close FROM price_bars WHERE timeframe='1d' "
                "AND symbol='NIFTY' ORDER BY ts", con)
n["d"] = pd.to_datetime(n.ts.str[:10])
N = n.set_index("d")[["open", "high", "low", "close"]]
v = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
v["d"] = pd.to_datetime(v.ts.str[:10])
N = N.join(v.set_index("d")["close"].rename("vix"), how="inner")
P = P.reindex(N.index)
R = P.pct_change(fill_method=None) * 100
N["idx"] = N.close.pct_change() * 100
N["ew_minus_cw"] = R.mean(axis=1) - N.idx
N["n_ok"] = R.notna().sum(axis=1)
N["gap"] = (N.open / N.close.shift(1) - 1) * 100

hi = N.high.values; lo = N.low.values; cl = N.close.values
mae = np.full(len(N), np.nan)
gap_share = np.full(len(N), np.nan)
for i in range(len(N) - H):
    base = cl[i]
    up = (np.max(hi[i + 1:i + 1 + H]) / base - 1) * 100
    dn = (np.min(lo[i + 1:i + 1 + H]) / base - 1) * 100
    mae[i] = max(abs(up), abs(dn))
    g = np.abs(N.gap.values[i + 1:i + 1 + H]).sum()
    tot = np.abs(np.diff(np.concatenate([[base], cl[i + 1:i + 1 + H]])) / base * 100).sum()
    gap_share[i] = g / tot * 100 if tot > 0 else np.nan
N["mae"] = mae; N["gap_share"] = gap_share
D = N[(N.n_ok >= 35)].dropna(subset=["mae", "ew_minus_cw", "idx", "vix"])
print("sessions: %d   holding period: %d   breakeven band: +/-%.2f%%" % (len(D), H, BAND))
print("\n=== 1. HOW BIG IS THE PATH RISK, versus the terminal risk? ===")
term = (N.close.shift(-H) / N.close - 1).abs() * 100
term = term.reindex(D.index)
print("   median MAE over %d sessions      : %.2f%%" % (H, D.mae.median()))
print("   median |terminal move|           : %.2f%%" % term.median())
print("   -> the path travels %.1fx further than the endpoint suggests"
      % (D.mae.median() / term.median()))
print("   P(MAE breaches +/-%.2f%%)          : %.0f%%   vs P(terminal breach) %.0f%%"
      % (BAND, (D.mae > BAND).mean() * 100, (term > BAND).mean() * 100))
lo13 = D.vix <= 13
print("   at VIX<=13: P(MAE breach) %.0f%%   vs P(terminal breach) %.0f%%"
      % ((D.mae[lo13] > BAND).mean() * 100, (term[lo13] > BAND).mean() * 100))

print("\n=== 2. THE HYPOTHESIS: does a CONTAINED shock mean a safer path? ===")
dn_ = D[D.idx < 0].copy()
dn_["broad"] = dn_.ew_minus_cw <= dn_.ew_minus_cw.median()
print("   %-24s %6s %10s %13s" % ("prior day", "n", "median MAE", "P(MAE breach)"))
print("   " + "-" * 58)
out = {}
for lab, m in (("ALL down days", pd.Series(True, index=dn_.index)),
               ("BROAD decline", dn_.broad), ("CONCENTRATED decline", ~dn_.broad)):
    s = dn_[m]
    out[lab] = {"n": int(len(s)), "mae": float(s.mae.median()),
                "breach": float((s.mae > BAND).mean())}
    print("   %-24s %6d %9.2f%% %12.0f%%"
          % (lab, len(s), s.mae.median(), (s.mae > BAND).mean() * 100))
d = out["BROAD decline"]["breach"] - out["CONCENTRATED decline"]["breach"]
lab_ = np.array([1] * out["BROAD decline"]["n"] + [0] * out["CONCENTRATED decline"]["n"])
vals = np.concatenate([(dn_[dn_.broad].mae > BAND).values,
                       (dn_[~dn_.broad].mae > BAND).values]).astype(float)
nd = []
for _ in range(5000):
    pm = RNG.permutation(lab_)
    nd.append(vals[pm == 1].mean() - vals[pm == 0].mean())
p = float((np.abs(np.array(nd)) >= abs(d)).mean())
print("\n   broad minus concentrated, P(MAE breach): %+.1f pp   permutation p=%.3f  ->  %s"
      % (d * 100, p, "SURVIVES" if p < 0.05 else "inside noise"))

print("\n=== 3. WHAT ELSE PREDICTS PATH RISK? (same target, other conditioners) ===")
print("   %-30s %6s %10s %13s" % ("condition", "n", "median MAE", "P(breach)"))
print("   " + "-" * 62)
for lab, m in (("any day", pd.Series(True, index=D.index)),
               ("VIX <= 12", D.vix <= 12), ("VIX 12-14", (D.vix > 12) & (D.vix <= 14)),
               ("VIX > 17", D.vix > 17),
               ("after a -2% day", D.idx < -2), ("after a +2% day", D.idx > 2),
               ("after |move| < 0.25%", D.idx.abs() < 0.25)):
    s = D[m]
    if len(s) < 30: continue
    print("   %-30s %6d %9.2f%% %12.0f%%" % (lab, len(s), s.mae.median(),
                                             (s.mae > BAND).mean() * 100))

print("\n=== 4. HOW MUCH OF THE PATH ARRIVES OVERNIGHT (unhedgeable)? ===")
print("   median share of total path movement occurring in GAPS: %.0f%%" % D.gap_share.median())
print("   at VIX<=13: %.0f%%   at VIX>17: %.0f%%"
      % (D.gap_share[lo13].median(), D.gap_share[D.vix > 17].median()))
json.dump({"median_mae": float(D.mae.median()), "median_terminal": float(term.median()),
           "mae_breach": float((D.mae > BAND).mean()),
           "term_breach": float((term > BAND).mean()), "split": out,
           "diff_pp": float(d * 100), "p": p,
           "gap_share_median": float(D.gap_share.median())},
          open("path_risk_result.json", "w"), indent=1)
print("\nwrote path_risk_result.json")
