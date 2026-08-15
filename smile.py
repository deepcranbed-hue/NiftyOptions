#!/usr/bin/env python3
"""smile -- back out per-strike implied vol from observed LTPs, instead of assuming VIX.

THE ERROR THIS CORRECTS. The previous run priced options with Black-Scholes at sigma =
India VIX and read the gap against traded prices as a fat-tail premium. That comparison is
not valid. India VIX is computed the CBOE way: a model-free, variance-swap-style integral
over the whole OTM strip weighted by 1/K^2. It is an AGGREGATE of the surface, not the
BS implied vol of any single strike, and it sits above ATM implied vol by roughly the
skew/convexity contribution. So a flat-vol BS model at sigma=VIX will misprice ATM one way
and the wings the other way BY CONSTRUCTION, and the residual measures the mismatch of the
two methodologies rather than anything about the market.

The right primitive is the strike's own implied vol, inverted from its own traded price.
Then the wing question is asked in the only unit that is comparable: what IV am I SELLING
at the short strike, and what IV am I BUYING at the wing?

Bisection inversion -- robust, no dependencies, no convergence assumptions.
"""
import sqlite3, math
import numpy as np, pandas as pd

def _cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs(S, K, T, s, call):
    if T <= 0 or s <= 0: return max(S - K, 0.0) if call else max(K - S, 0.0)
    d1 = (math.log(S / K) + 0.5 * s * s * T) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
    return (S * _cdf(d1) - K * _cdf(d2)) if call else (K * _cdf(-d2) - S * _cdf(-d1))

def iv(price, S, K, T, call):
    intr = max(S - K, 0.0) if call else max(K - S, 0.0)
    if price <= intr + 1e-6 or T <= 0: return None
    lo, hi = 1e-4, 4.0
    if bs(S, K, T, hi, call) < price: return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if bs(S, K, T, mid, call) < price: lo = mid
        else: hi = mid
    return 0.5 * (lo + hi)

con = sqlite3.connect("option_chains.db")
cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
ids = cap.capture_id.values[::12]                      # ~every 12th minute, 900+ snapshots
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows "
                 "WHERE capture_id IN (%s)" % ",".join("?" * len(ids)), con,
                 params=[int(i) for i in ids])
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
ch["dte"] = (pd.to_datetime(ch.exp) - ch.dt).dt.days
ch = ch[(ch.dte >= 2) & (ch.dte <= 10)]
vx = pd.read_sql("SELECT ts,close FROM price_bars WHERE timeframe='1d' AND symbol='INDIAVIX'", con)
vx["d"] = pd.to_datetime(vx.ts.str[:10]); VX = vx.set_index("d")["close"].sort_index()
print("snapshots %d   rows %d" % (ch.capture_id.nunique(), len(ch)))

recs = []
for r in ch.itertuples():
    S, K, T = float(r.spot), float(r.strike), r.dte / 365.0
    mny = (K / S - 1) * 100
    if abs(mny) > 2.6: continue
    for px, call in ((r.call_ltp, True), (r.put_ltp, False)):
        if px is None or px != px or px <= 0.05: continue
        # only OTM options -- ITM LTPs are stale and their vega is tiny
        if (call and mny < 0.05) or ((not call) and mny > -0.05): continue
        s = iv(float(px), S, K, T, call)
        if s and 0.03 < s < 2.0:
            recs.append({"mny": mny, "iv": s * 100, "call": call,
                         "day": r.dt.date(), "dte": r.dte})
D = pd.DataFrame(recs)
D["vix"] = [VX.asof(pd.Timestamp(d)) for d in D.day]
print("inverted %d option prices" % len(D))

print("\n=== 1. THE SMILE, from your own traded prices ===")
print("   %-16s %8s %10s %10s" % ("moneyness", "n", "median IV", "vs VIX"))
print("   " + "-" * 50)
bks = [(-2.6, -2.0), (-2.0, -1.5), (-1.5, -1.0), (-1.0, -0.5), (-0.5, -0.05),
       (0.05, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.6)]
prof = {}
for lo, hi in bks:
    s = D[(D.mny >= lo) & (D.mny < hi)]
    if len(s) < 200: continue
    m, vv = s.iv.median(), s.vix.median()
    prof[(lo + hi) / 2] = m
    print("   %-16s %8d %9.2f%% %+9.2f" % ("%+.2f..%+.2f%%" % (lo, hi), len(s), m, m - vv))

print("\n=== 2. INDIA VIX vs ATM BS IMPLIED -- the methodology gap you flagged ===")
atm = D[D.mny.abs() < 0.5]
print("   near-ATM BS implied (median) : %.2f%%" % atm.iv.median())
print("   India VIX over the same days : %.2f%%" % atm.vix.median())
print("   gap                          : %+.2f pts  (VIX is %.0f%% of ATM BS IV)"
      % (atm.vix.median() - atm.iv.median(), atm.vix.median() / atm.iv.median() * 100))
print("   -> pricing every strike at sigma=VIX therefore %s the whole structure."
      % ("OVERSTATES" if atm.vix.median() > atm.iv.median() else "UNDERSTATES"))

print("\n=== 3. PUT vs CALL IV AT EQUAL DISTANCE -- the real skew ===")
print("   %-14s %9s %10s %10s %8s" % ("distance", "put IV", "call IV", "skew", "n(min)"))
print("   " + "-" * 56)
for lo, hi in ((0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.6)):
    p = D[(~D.call) & (D.mny <= -lo) & (D.mny > -hi)]
    c = D[(D.call) & (D.mny >= lo) & (D.mny < hi)]
    if len(p) < 150 or len(c) < 150: continue
    print("   %-14s %8.2f%% %9.2f%% %+9.2f %8d"
          % ("%.1f-%.1f%% OTM" % (lo, hi), p.iv.median(), c.iv.median(),
             p.iv.median() - c.iv.median(), min(len(p), len(c))))

print("\n=== 4. THE WING TRADE IN IV TERMS ===")
print("   short strikes at +/-0.62%%; you SELL that IV and BUY the wing's IV.")
sh_p = D[(~D.call) & (D.mny <= -0.45) & (D.mny > -0.85)].iv.median()
sh_c = D[(D.call) & (D.mny >= 0.45) & (D.mny < 0.85)].iv.median()
print("   sell: put %.2f%%  call %.2f%%" % (sh_p, sh_c))
for lo, hi, lab in ((0.9, 1.3, "+0.41%"), (1.3, 1.7, "+0.82%"), (1.7, 2.6, "+1.23%")):
    p = D[(~D.call) & (D.mny <= -lo) & (D.mny > -hi)].iv.median()
    c = D[(D.call) & (D.mny >= lo) & (D.mny < hi)].iv.median()
    if p != p or c != c: continue
    print("   buy wing %-8s put %.2f%% (%+.2f vs short)   call %.2f%% (%+.2f)"
          % (lab, p, p - sh_p, c, c - sh_c))
print("\n   A wing bought at HIGHER IV than the strike you sold is a negative-carry hedge")
print("   in vol terms; that differential -- not a price ratio against a flat-vol model --")
print("   is what the insurance actually costs.")
