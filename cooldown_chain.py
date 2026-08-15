#!/usr/bin/env python3
"""cooldown_chain -- does the "don't enter after a shock" rule show up in REAL option P&L?

The rule is already supported on 2,097 daily index observations (EDGE/ES 0.35 quiet vs
0.13 after a >1% move). This checks whether the same pattern appears in traded prices.

DESIGN: enter a +/-150 strangle at 09:15 on EVERY day of every cycle, hold to that
expiry, settle at intrinsic. Classify each entry by information available BEFORE it --
the previous session's Nifty move and the prevailing VIX. Never by what the trade went
on to do.

WHY THIS IS A CHECK AND NOT A TEST: entries inside the same expiry overlap heavily, so
the ~30 observations are nowhere near independent -- a single bad week contaminates every
entry that week. It can corroborate or contradict the large-sample finding. It cannot
stand alone.
"""
import sqlite3
import numpy as np, pandas as pd

X, SLIP = 150.0, 1.0
con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False)) + pd.Timedelta("5:30:00")
cap["day"] = cap.dt.dt.date
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "day", "spot"]], on="capture_id")
n = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='NIFTY' "
                "ORDER BY ts", con)
n["d"] = pd.to_datetime(n.ts.str[:10]); N = n.set_index("d")["close"]
NR = (N.pct_change() * 100)
v = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
v["d"] = pd.to_datetime(v.ts.str[:10]); VX = v.set_index("d")["close"].sort_index()

rows = []
for exp, g in ch.groupby("exp"):
    days = sorted(g.day.unique())
    last = g[g.dt == g.dt.max()]
    ST = float(last.spot.iloc[0])
    for d in days[:-1]:                       # need at least one day of holding
        gd = g[g.day == d]
        first = gd[gd.dt == gd.dt.min()]
        S = float(first.spot.iloc[0])
        ks = np.asarray(sorted(first.strike.unique()), dtype=float)
        ck = float(ks[np.argmin(np.abs(ks - (S + X)))]); pk = float(ks[np.argmin(np.abs(ks - (S - X)))])
        c = first[first.strike == ck].call_ltp; p = first[first.strike == pk].put_ltp
        if c.empty or p.empty: continue
        cv, pv = float(c.iloc[0]), float(p.iloc[0])
        if not (cv > 0.05 and pv > 0.05): continue
        ts = pd.Timestamp(d)
        prior = NR[NR.index < ts]
        vix = VX[VX.index < ts]
        if not len(prior) or not len(vix): continue
        pnl = (cv + pv - 2 * SLIP) - (max(ST - ck, 0) + max(pk - ST, 0))
        rows.append({"exp": str(exp), "entry": str(d), "spot": S, "credit": cv + pv,
                     "prior_move": prior.iloc[-1], "vix": vix.iloc[-1],
                     "days_held": len(days) - days.index(d) - 1, "pnl": pnl})
T = pd.DataFrame(rows)
print("entries: %d across %d expiries   (heavily overlapping)" % (len(T), T.exp.nunique()))
print("prior-move range %.2f%% .. %+.2f%%   VIX range %.1f .. %.1f"
      % (T.prior_move.min(), T.prior_move.max(), T.vix.min(), T.vix.max()))

print("\n=== ENTRY STATE vs OUTCOME (real LTPs) ===")
print("%-26s %5s %9s %9s %9s %7s" % ("entry state", "n", "credit", "mean P/L", "worst", "win"))
print("-" * 70)
for lab, m in (("ALL entries", pd.Series(True, index=T.index)),
               ("prior |move| < 0.5%", T.prior_move.abs() < 0.5),
               ("prior |move| 0.5-1%", (T.prior_move.abs() >= 0.5) & (T.prior_move.abs() < 1.0)),
               ("prior |move| > 1%", T.prior_move.abs() >= 1.0),
               ("prior move < -1% (down)", T.prior_move <= -1.0),
               ("prior move > +1% (up)", T.prior_move >= 1.0)):
    s = T[m]
    if len(s) < 3: continue
    print("%-26s %5d %9.1f %+9.1f %+9.1f %6.0f%%"
          % (lab, len(s), s.credit.mean(), s.pnl.mean(), s.pnl.min(), (s.pnl > 0).mean() * 100))

print("\n=== WHAT A COOLDOWN WOULD HAVE DONE ===")
print("%-28s %6s %11s %10s %10s" % ("rule", "trades", "total P/L", "mean", "worst"))
print("-" * 70)
for lab, m in (("A. always trade", pd.Series(True, index=T.index)),
               ("C. skip if |move| > 1.0%", T.prior_move.abs() < 1.0),
               ("D. skip if |move| > 1.5%", T.prior_move.abs() < 1.5),
               ("E. skip if |move| > 2.0%", T.prior_move.abs() < 2.0),
               ("F. skip if move < -1.0%", T.prior_move > -1.0)):
    s = T[m]
    print("%-28s %6d %+11.1f %+10.1f %+10.1f"
          % (lab, len(s), s.pnl.sum(), s.pnl.mean(), s.pnl.min()))
T.round(2).to_json("cooldown_chain_result.json", orient="records", indent=1)
print("\n   note: entries overlap within an expiry, so 'trades' is not a count of")
print("   independent observations. Read the MEAN, not the total.")
