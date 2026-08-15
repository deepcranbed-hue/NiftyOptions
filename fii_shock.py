"""Did FII make money on short index futures during the Jan-Mar 2026 drawdown --
and does futures positioning predict returns INSIDE a shock regime even though it
does not unconditionally?

Two separate claims are tangled together in "they were short futures and made money":
  (1) a P&L attribution claim -- what their position earned;
  (2) a predictive claim -- whether that position, published with a one-day lag,
      told you anything you could act on.
They need different tests and they can have different answers.

THE ANCHOR PROBLEM, stated up front. participant_flows carries NSE's
fao_participant_vol_ file: traded volume. idx_fut_long - idx_fut_short is therefore the
one-day CHANGE in FII net index-futures position, verified earlier against the cash-flow
cache (18/20 exact matches). Cumulating it recovers the SHAPE of the position but not its
LEVEL -- the true position is p_t = c_t + C for an unknown constant C, and nothing in this
database pins C down (price_bars.open_interest is null for NIFTY_FUT_1, and the
fao_participant_oi_ series has never been collected).

So this script does NOT assert whether FII were net short. It computes P&L as an explicit
function of C and solves for the break-even, which turns "were they short?" into the
sharper question "how short would they have had to be for this to have paid?"
"""
import sqlite3, json
import numpy as np
import pandas as pd

LOT = 75          # NIFTY contract multiplier -- stated as an assumption, not a lookup
NSIM = 4000
RNG = np.random.default_rng(20260813)
con = sqlite3.connect("option_chains.db")

px = pd.read_sql("select substr(ts,1,10) d, open, close from price_bars "
                 "where symbol='NIFTY' and timeframe='1d' and ts>='2025-08-01' order by ts", con)
px["d"] = pd.to_datetime(px.d); px = px.drop_duplicates("d").sort_values("d").reset_index(drop=True)
px["peak"] = px.close.cummax(); px["dd"] = px.close / px.peak - 1.0
px["r_cc"] = px.close.shift(-1) / px.close - 1.0
px["r_oc"] = px.close.shift(-1) / px.open.shift(-1) - 1.0
px["r20"] = px.close / px.close.shift(20) - 1.0

vix = pd.read_sql("select substr(ts,1,10) d, close vix from price_bars "
                  "where symbol='INDIAVIX' and timeframe='1d' and ts>='2025-08-01' order by ts", con)
vix["d"] = pd.to_datetime(vix.d); vix = vix.drop_duplicates("d")

fii = pd.read_sql("select flow_date, idx_fut_long, idx_fut_short from participant_flows "
                  "where participant_type='FII' order by flow_date", con)
fii["d"] = pd.to_datetime(fii.flow_date)
fii["dpos"] = fii.idx_fut_long - fii.idx_fut_short     # one-day CHANGE in net position
fii["cum"] = fii.dpos.cumsum()                          # shape only; level unknown by +C

df = fii[["d", "dpos", "cum"]].merge(px, on="d").merge(vix, on="d").sort_values("d").reset_index(drop=True)
out = {"n": len(df), "lot_assumed": LOT}

# ---------- the episode ----------
tr = df.loc[df.dd.idxmin()]
pk = df[(df.d <= tr.d) & (df.close == tr.peak)].iloc[-1]
ep = df[(df.d >= pk.d) & (df.d <= tr.d)]
print(f"EPISODE  peak {pk.d.date()} {pk.close:.0f}  ->  trough {tr.d.date()} {tr.close:.0f}"
      f"   {tr.dd:.2%}   ({len(ep)} sessions, VIX max {ep.vix.max():.1f})")
print(f"today    {df.iloc[-1].d.date()} {df.iloc[-1].close:.0f}, still {df.iloc[-1].dd:.2%} below that peak\n")

# ---------- 1. did they SELL futures into the fall? ----------
# This part needs no anchor: it is a sum of changes, which is what the data actually is.
sold = ep.dpos.sum()
print("1. DIRECTION OF THE POSITION CHANGE (anchor-free)")
print(f"   net change in FII index-futures position, peak->trough: {sold:+,.0f} contracts")
print(f"   = {sold*LOT*ep.close.mean()/1e7:+,.0f} crore notional at the window's mean index")
print(f"   days they added net long {int((ep.dpos>0).sum())} / net short {int((ep.dpos<0).sum())}")
post = df[df.d > tr.d]
print(f"   post-trough change {post.dpos.sum():+,.0f} contracts over {len(post)} sessions")
out["episode"] = dict(peak=str(pk.d.date()), trough=str(tr.d.date()), drawdown=round(float(tr.dd), 4),
                      sessions=len(ep), vix_max=round(float(ep.vix.max()), 2),
                      net_change_contracts=int(sold), post_trough_change=int(post.dpos.sum()))

# ---------- 2. P&L as a function of the unknown anchor ----------
# MTM on a position held through price changes:
#   PnL(C) = sum_t (c_t + C) * (P_{t+1} - P_t) * LOT
#          = [sum_t c_t * dP_t] * LOT  +  C * (P_end - P_start) * LOT
# Linear in C, so the break-even anchor is a single number.
def pnl_terms(sub):
    c = sub.cum.values[:-1]
    dP = np.diff(sub.close.values)
    base = float(np.sum(c * dP) * LOT)             # P&L from the SHAPE
    slope = float((sub.close.values[-1] - sub.close.values[0]) * LOT)  # per unit of C
    return base, slope

for label, sub in [("peak->trough", ep), ("full sample", df)]:
    base, slope = pnl_terms(sub)
    be = -base / slope if slope else np.nan
    print(f"\n2. P&L SENSITIVITY TO THE UNKNOWN ANCHOR C -- {label}")
    print(f"   PnL(C) = {base/1e7:+,.0f} cr  +  C x {slope/1e7:+.4f} cr per contract")
    print(f"   break-even anchor C* = {be:+,.0f} contracts")
    print(f"   -> profitable iff starting net position was "
          f"{'BELOW' if slope>0 else 'ABOVE'} {be:+,.0f} contracts")
    for C in [-200000, -100000, -50000, 0, 50000, 100000]:
        print(f"        C={C:+8,}  PnL = {(base + C*slope)/1e7:+10,.0f} cr")
    out.setdefault("pnl", {})[label] = dict(base_cr=round(base/1e7, 1), slope_cr_per_contract=round(slope/1e7, 6),
                                            breakeven_anchor=round(float(be), 0))

# ---------- 3. cash-market side, for comparison ----------
cash = pd.read_sql("select * from fii_dii_flows", con)
cash.columns = [c.lower() for c in cash.columns]
dcol = cash.columns[0]
cash[dcol] = pd.to_datetime(cash[dcol])
cash = cash.rename(columns={cash.columns[3]: "fii_net", cash.columns[6]: "dii_net"})
cw = cash[(cash[dcol] >= pk.d) & (cash[dcol] <= tr.d)]
print(f"\n3. CASH MARKET over the same window ({len(cw)} sessions with data)")
print(f"   FII net cash {cw.fii_net.sum():+,.0f} cr     DII net cash {cw.dii_net.sum():+,.0f} cr")
out["cash_window"] = dict(sessions=len(cw), fii_net_cr=round(float(cw.fii_net.sum()), 0),
                          dii_net_cr=round(float(cw.dii_net.sum()), 0))

# ---------- 4. CONDITIONAL prediction: does it work inside the shock? ----------
# This is the real challenge to the earlier null result: an unconditional correlation over
# a full year would wash out an effect that only lives in stressed regimes.
def test(sub, x, y):
    x = sub[x].values; y = sub[y].values
    ok = np.isfinite(x) & np.isfinite(y); x, y = x[ok], y[ok]
    if len(x) < 30: return None
    r = float(np.corrcoef(x, y)[0, 1])
    n = len(x)
    null = np.array([abs(np.corrcoef(np.roll(x, RNG.integers(1, n)), y)[0, 1]) for _ in range(NSIM)])
    return dict(n=n, r=round(r, 4), p=round(float((null >= abs(r)).mean()), 4),
                null_p95=round(float(np.percentile(null, 95)), 4))

for c in ["dpos", "cum"]:
    m = df[c].rolling(60, min_periods=30).mean().shift(1)
    s = df[c].rolling(60, min_periods=30).std().shift(1)
    df[c + "_z"] = (df[c] - m) / s

regimes = {
    "SHOCK: drawdown worse than -5%": df[df.dd < -0.05],
    "CALM: drawdown better than -5%": df[df.dd >= -0.05],
    "HIGH VIX (>16)": df[df.vix > 16],
    "LOW VIX (<=16)": df[df.vix <= 16],
    "FALLING 20d": df[df.r20 < 0],
    "RISING 20d": df[df.r20 >= 0],
    "THE EPISODE ONLY": df[(df.d >= pk.d) & (df.d <= tr.d)],
}
print("\n4. CONDITIONAL PREDICTION -- FII futures signal by regime")
print(f"   {'regime':34s}{'signal':10s}{'target':8s}{'n':>5}{'r':>9}{'p':>8}{'null p95':>10}")
cond = {}
for name, sub in regimes.items():
    for sig in ["dpos_z", "cum_z"]:
        for tgt in ["r_cc", "r_oc"]:
            res = test(sub.dropna(subset=[sig, tgt]), sig, tgt)
            if res is None:
                continue
            cond[f"{name}|{sig}|{tgt}"] = res
            print(f"   {name:34s}{sig:10s}{tgt:8s}{res['n']:5d}{res['r']:9.4f}"
                  f"{res['p']:8.4f}{res['null_p95']:10.4f}")
out["conditional"] = cond

# How many of these subgroup tests would clear p<0.05 by chance alone?
k = len(cond); hits = sum(1 for v in cond.values() if v["p"] < 0.05)
print(f"\n   {hits} of {k} subgroup tests cleared p<0.05; pure chance would deliver "
      f"about {0.05*k:.1f}. Subgroup testing is where spurious regime effects are born.")
out["subgroup_summary"] = dict(tests=k, hits=hits, expected_by_chance=round(0.05*k, 1))

json.dump(out, open("fii_shock_result.json", "w"), indent=1)
print("\nwrote fii_shock_result.json")
