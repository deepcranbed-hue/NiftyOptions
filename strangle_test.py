#!/usr/bin/env python3
"""strangle_test -- what does a short strangle 150 points either side of ATM actually face?

FIRST, THE MECHANIC, because the phrasing "one will gain and the other fall" can hide it.
Both legs are SHORT. They do not hedge each other. If spot rallies, the short call loses
without limit while the short put's gain is capped at the premium it collected -- and the
same in reverse. The structure is not long-one/short-one; it is short volatility twice.
Maximum gain is the total premium; maximum loss is open-ended on either side. The two legs
offset each other's DELTA near the middle, not each other's RISK at the edges.

SECOND, THE SCALE PROBLEM. 150 points on a 24,300 index is 0.617%. With India VIX at 12.2
the implied ONE-DAY move is 12.2/sqrt(252) = 0.769%, i.e. about 187 points. So +/-150 is
roughly 0.8 of a single day's implied sigma. Over a 6-session weekly the implied terminal
move is 187*sqrt(6) = ~458 points, and +/-150 is 0.33 sigma. This is measured below rather
than assumed, on the actual return distribution, which has fatter tails than a normal.

THIRD, THE REAL PRICES. chain_rows carries LTPs, so the weeklies in the 31-day window can
be priced directly: sell both legs ~150 points out on the session after an expiry, mark to
the next expiry's settlement, and see what the structure actually collected and paid.
Five trades is not a backtest -- it is a sanity check that the statistics above are not
describing a different instrument from the one being traded.
"""
import sqlite3, json
import numpy as np, pandas as pd

con = sqlite3.connect("option_chains.db")
o = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                "('NIFTY','INDIAVIX')", con)
o["d"] = pd.to_datetime(o.ts.str[:10])
O = o.pivot_table(index="d", columns="symbol", values="close").sort_index().dropna()
SPOT, BAND = 24300.0, 150.0
band_pct = BAND / SPOT * 100
print("band: +/-%.0f points on %.0f = +/-%.3f%%" % (BAND, SPOT, band_pct))
print("India VIX 12.2 -> implied 1-day move %.3f%% (%.0f pts).  +/-150 = %.2f sigma of ONE day."
      % (12.2 / np.sqrt(252), 12.2 / np.sqrt(252) / 100 * SPOT, BAND / (12.2 / np.sqrt(252) / 100 * SPOT)))

print("\n=== 1. HOW OFTEN DOES NIFTY TRAVEL MORE THAN %.3f%% ? (2018-2026, actual returns) ===" % band_pct)
print("   %-12s %7s %14s %14s   %s" % ("horizon", "n", "P(|move|>band)", "P(stays in)", "at VIX<=13"))
print("   " + "-" * 68)
res = {}
for H in (1, 2, 3, 5, 6):
    mv = (O.NIFTY.shift(-H) / O.NIFTY - 1).abs() * 100
    m = mv.notna()
    lo = O.INDIAVIX <= 13
    br = (mv[m] > band_pct).mean()
    br_lo = (mv[m & lo] > band_pct).mean()
    res[H] = {"breach": float(br), "breach_lowvix": float(br_lo)}
    print("   %-12s %7d %13.0f%% %13.0f%%   %13.0f%%"
          % ("%d session%s" % (H, "s" if H > 1 else ""), int(m.sum()), br * 100,
             (1 - br) * 100, br_lo * 100))
print("\n   A weekly held to expiry is ~5-6 sessions. Both legs expire worthless only if")
print("   spot stays inside the band the WHOLE time -- the terminal figures above are the")
print("   friendlier version; an intraday touch can force a defensive adjustment earlier.")
mx = O.NIFTY.rolling(6).apply(lambda x: (x.max() - x.min()) / x[0] * 100, raw=True)
print("   P(the 6-session RANGE exceeded %.3f%% either side): %.0f%%"
      % (band_pct, (mx > band_pct).mean() * 100))

print("\n=== 2. WHAT THE STRUCTURE ACTUALLY COLLECTED, from your own chain ===")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
cap["day"] = cap.dt.dt.date
rows = []
for exp in sorted(pd.read_sql("SELECT DISTINCT expiry FROM chain_rows", con).expiry):
    ch = pd.read_sql("SELECT capture_id,strike,call_ltp,put_ltp FROM chain_rows WHERE expiry=?",
                     con, params=(exp,))
    if ch.empty: continue
    j = ch.merge(cap[["capture_id", "dt", "day", "spot"]], on="capture_id")
    if j.empty: continue
    first_day = j.day.min()
    entry = j[j.day == first_day].sort_values("dt").iloc[:1].dt.iloc[0] if len(j) else None
    ej = j[j.dt == j[j.day == first_day].dt.min()]
    if ej.empty: continue
    s0 = float(ej.spot.iloc[0])
    ck = min(ej.strike, key=lambda k: abs(k - (s0 + BAND)))
    pk = min(ej.strike, key=lambda k: abs(k - (s0 - BAND)))
    cprem = ej[ej.strike == ck].call_ltp.mean(); pprem = ej[ej.strike == pk].put_ltp.mean()
    if not (cprem > 0 and pprem > 0): continue
    last = j[j.dt == j.dt.max()]
    sT = float(last.spot.iloc[0])
    payoff = max(sT - ck, 0) + max(pk - sT, 0)
    rows.append({"expiry": exp[:10], "entry_day": str(first_day), "spot0": s0,
                 "call_k": ck, "put_k": pk, "premium": cprem + pprem,
                 "settle": sT, "payoff": payoff, "pnl": cprem + pprem - payoff})
if rows:
    T = pd.DataFrame(rows)
    print("   %-12s %9s %7s %7s %9s %9s %9s" % ("expiry", "spot@entry", "CE K", "PE K", "premium", "settle", "P/L"))
    for r in T.itertuples():
        print("   %-12s %9.0f %7.0f %7.0f %9.1f %9.0f %+9.1f"
              % (r.expiry, r.spot0, r.call_k, r.put_k, r.premium, r.settle, r.pnl))
    print("   ---")
    print("   %d trades  wins %d  total P/L %+.1f pts  worst %+.1f  best %+.1f"
          % (len(T), int((T.pnl > 0).sum()), T.pnl.sum(), T.pnl.min(), T.pnl.max()))
    print("   mean premium collected %.1f pts vs mean payoff %.1f pts"
          % (T.premium.mean(), T.payoff.mean()))
    print("   NOTE: %d trades is a sanity check, not evidence. Entry is the first capture of"
          % len(T))
    print("   the expiry's first observed day, held to the last capture -- no management.")
    T.round(1).to_json("strangle_test_result.json", orient="records", indent=1)
else:
    print("   could not price any complete expiry from the captured window")
