#!/usr/bin/env python3
"""roll_backtest -- simulate the ACTUAL rolling strategy on real option prices.

THE QUESTION, stated as the user framed it: when the market moves, does harvesting the
profitable leg and rolling the threatened one convert path volatility into extra theta --
or does repeated rolling eventually eat the theta? A terminal-breakeven study cannot
answer that, because the strategy never reaches the terminal payoff of its original
strikes. Only a simulation over real LTPs can.

SAMPLE, AND WHY THIS IS A HARNESS RATHER THAN EVIDENCE. chain_rows covers 31 sessions, so
there are about five complete weekly cycles. Five trades cannot establish anything. What
it CAN do is (a) verify the mechanics price correctly against real quotes, (b) show the
P&L DECOMPOSITION -- how much came from harvested winners versus how much leaked to rolls
-- and (c) be ready to run properly once capture resumes and cycles accumulate.

RULES IMPLEMENTED
  entry     first capture of a cycle: sell CE at spot+X and PE at spot-X
  harvest   a leg whose LTP falls to <= HARVEST x its entry price is bought back; profit
            realised. Optionally a fresh leg is sold on that side at the new spot +/- X.
  roll      when spot breaches a short strike, that leg is bought back and re-sold X points
            beyond the new spot. The debit is recorded as a roll cost -- it does NOT erase
            the loss already taken, which is the trap in "move the losing leg further away".
  exit      remaining legs settled at intrinsic against the last observed spot.

COSTS ARE NOT OPTIONAL HERE. The whole question is whether rolling pays, and rolling is
the thing that generates costs, so a zero-cost simulation would answer a different
question favourably by construction. Each leg traded is charged SLIP points (bid/ask plus
impact). Sensitivity across SLIP is reported, because the conclusion may hinge on it.
"""
import sqlite3, json, sys
import numpy as np, pandas as pd

X = 150.0            # strike offset from spot
HARVEST = 0.50       # buy back a leg once it has lost half its value
con = sqlite3.connect("option_chains.db")

cap = pd.read_sql("SELECT capture_id,captured_at,spot FROM captures ORDER BY captured_at", con)
cap["dt"] = pd.to_datetime(cap.captured_at.str.replace("Z", "", regex=False))
cap["day"] = cap.dt.dt.date
ch = pd.read_sql("SELECT capture_id,expiry,strike,call_ltp,put_ltp FROM chain_rows", con)
ch["exp"] = pd.to_datetime(ch.expiry.str[:10]).dt.date          # normalise both formats
ch = ch.merge(cap[["capture_id", "dt", "spot"]], on="capture_id")
print("chain rows %d   expiries %s" % (len(ch), sorted(set(ch.exp))))

# Pre-pivot once per expiry: the per-timestamp dataframe filtering in the first version
# was O(expiries x timestamps x rows) and did not finish. Arrays make the loop trivial.
PANELS = {}
for exp in sorted(set(ch.exp)):
    g = ch[ch.exp == exp]
    C = g.pivot_table(index="dt", columns="strike", values="call_ltp").sort_index()
    Pp = g.pivot_table(index="dt", columns="strike", values="put_ltp").sort_index()
    sp = g.groupby("dt")["spot"].first().sort_index()
    if len(sp) < 50:
        continue
    step = max(1, len(sp) // 600)          # ~5-minute sampling; management is not tick-level
    PANELS[exp] = (C.iloc[::step], Pp.iloc[::step], sp.iloc[::step])
print("cycles priced: %d" % len(PANELS))


def _px(row, k):
    if k not in row.index:
        return None
    v = row[k]
    return float(v) if (v == v and v is not None and v > 0.05) else None


def _near(cols, target):
    a = np.asarray([c for c in cols], dtype=float)
    return float(a[np.argmin(np.abs(a - target))])


def run(slip):
    trades = []
    for exp, (C, Pp, sp) in PANELS.items():
        ts = sp.index
        S0 = float(sp.iloc[0])
        ck, pk = _near(C.columns, S0 + X), _near(Pp.columns, S0 - X)
        c0, p0 = _px(C.iloc[0], ck), _px(Pp.iloc[0], pk)
        if not (c0 and p0):
            continue
        cash = c0 + p0 - 2 * slip
        legs = {"C": {"k": ck, "entry": c0, "live": True},
                "P": {"k": pk, "entry": p0, "live": True}}
        harv = rollcost = 0.0
        nroll = nharv = 0
        for i in range(1, len(ts)):
            S = float(sp.iloc[i])
            for side, panel, sign in (("C", C, +1), ("P", Pp, -1)):
                L = legs[side]
                if not L["live"]:
                    continue
                row = panel.iloc[i]
                cur = _px(row, L["k"])
                if cur is None:
                    continue
                if cur <= HARVEST * L["entry"]:                 # harvest the winner
                    cash -= cur + slip
                    harv += L["entry"] - cur
                    nharv += 1
                    nk = _near(panel.columns, S + sign * X)
                    npx = _px(row, nk)
                    if npx and abs(nk - S) > 40:
                        cash += npx - slip
                        legs[side] = {"k": nk, "entry": npx, "live": True}
                    else:
                        L["live"] = False
                    continue
                breached = (S > L["k"]) if side == "C" else (S < L["k"])
                if breached:                                    # chase the threatened leg
                    nk = _near(panel.columns, S + sign * X)
                    npx = _px(row, nk)
                    if npx and nk != L["k"]:
                        cash -= cur + slip
                        cash += npx - slip
                        rollcost += cur - npx
                        nroll += 1
                        legs[side] = {"k": nk, "entry": npx, "live": True}
        ST = float(sp.iloc[-1])
        settle = sum((max(ST - L["k"], 0) if s_ == "C" else max(L["k"] - ST, 0))
                     for s_, L in legs.items() if L["live"])
        trades.append({"expiry": str(exp), "S0": S0, "ST": ST, "credit0": c0 + p0,
                       "harvested": harv, "roll_cost": rollcost, "n_roll": nroll,
                       "n_harvest": nharv, "settle": settle, "pnl": cash - settle})
    return pd.DataFrame(trades)


print("\n=== ROLLING STRATEGY, real LTPs, X=%.0f, harvest at %.0f%% decay ===" % (X, HARVEST * 100))
base = run(1.0)
if base.empty:
    sys.exit("no complete cycles could be priced")
print("%-12s %7s %7s %8s %9s %9s %6s %6s %9s"
      % ("expiry", "S0", "ST", "credit", "harvest", "rollcost", "#roll", "#harv", "P/L"))
for r in base.itertuples():
    print("%-12s %7.0f %7.0f %8.1f %9.1f %9.1f %6d %6d %+9.1f"
          % (r.expiry, r.S0, r.ST, r.credit0, r.harvested, r.roll_cost, r.n_roll,
             r.n_harvest, r.pnl))
print("-" * 82)
print("%d cycles   wins %d   TOTAL %+.1f pts   mean %+.1f   worst %+.1f"
      % (len(base), int((base.pnl > 0).sum()), base.pnl.sum(), base.pnl.mean(), base.pnl.min()))
print("   harvested from winners : %+.1f pts" % base.harvested.sum())
print("   paid away in rolls     : %+.1f pts" % -base.roll_cost.sum())
print("   rolls %d, harvests %d across %d cycles"
      % (base.n_roll.sum(), base.n_harvest.sum(), len(base)))

print("\n=== SENSITIVITY TO SLIPPAGE (the roll is what generates cost) ===")
print("   %-10s %10s %10s %8s" % ("slip/leg", "total P/L", "mean/cycle", "wins"))
for s in (0.0, 0.5, 1.0, 2.0, 3.0):
    t = run(s)
    print("   %-10.1f %+10.1f %+10.1f %8d" % (s, t.pnl.sum(), t.pnl.mean(), int((t.pnl > 0).sum())))
base.round(1).to_json("roll_backtest_result.json", orient="records", indent=1)
print("\nwrote roll_backtest_result.json")
