#!/usr/bin/env python3
"""conditional_vrp -- is VIX 12 the best place to sell, or merely the safest-looking?

THE RATIO IS NOT THE EDGE. realised/implied came back at 0.62-0.75 in every VIX bucket,
which looks like the opportunity is constant. It is not: the ratio is scale-free but the
PREMIUM is not. At VIX 12 you are paid 0.70% of spot per day and give back 0.47%; at VIX
20 you are paid 1.21% and give back 0.82%. Same ratio, nearly double the absolute edge.

So this measures three things per bucket, because they can disagree:
    EDGE      implied - realised, in % of spot per day. What you actually collect.
    RATIO     realised/implied. Whether the pricing is generous.
    TAIL      5th and 1st percentile of the seller's daily result, and expected
              shortfall (the mean of the worst 5%) -- because variance is the wrong
              risk measure for a payoff that is capped up and open-ended down, a point
              the previous overnight comparison glossed over.

The decision metric is EDGE / |EXPECTED SHORTFALL|: how much you are paid per unit of the
loss you actually fear. A bucket can have the best edge and the worst ratio of edge to
tail at the same time, and only that last column says where to deploy.

Then crossed with recent-move state, since path risk was shown to depend on it.
"""
import sqlite3, json
import numpy as np, pandas as pd

con = sqlite3.connect("option_chains.db")
o = pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
                "('NIFTY','INDIAVIX')", con)
o["d"] = pd.to_datetime(o.ts.str[:10])
O = o.pivot_table(index="d", columns="symbol", values="close").sort_index().dropna()
D = pd.DataFrame({"vix": O.INDIAVIX})
D["implied"] = D.vix / np.sqrt(252)
D["r"] = O.NIFTY.pct_change() * 100
D["realised"] = D.r.shift(-1).abs()
D["edge"] = D.implied - D.realised
D = D.dropna()

def block(m, label):
    s = D[m]
    if len(s) < 40: return None
    es = s.edge.nsmallest(max(int(len(s) * 0.05), 1)).mean()
    return {"label": label, "n": len(s), "implied": s.implied.mean(),
            "realised": s.realised.mean(), "edge": s.edge.mean(),
            "ratio": s.realised.mean() / s.implied.mean(),
            "p5": s.edge.quantile(.05), "p1": s.edge.quantile(.01), "es": es,
            "eff": s.edge.mean() / abs(es), "sharpe": s.edge.mean() / s.edge.std()}

print("=== 1. VRP BY VIX BUCKET -- edge, ratio, and what the tail costs ===")
print("%-12s %5s %8s %8s %8s %7s %8s %8s %9s %7s"
      % ("VIX", "n", "implied", "realis'd", "EDGE", "ratio", "p5", "p1", "ES(5%)", "EDGE/ES"))
print("-" * 90)
rows = []
for lo, hi in ((0, 12), (12, 14), (14, 17), (17, 22), (22, 999)):
    b = block((D.vix >= lo) & (D.vix < hi), "VIX %d-%d" % (lo, hi))
    if not b: continue
    rows.append(b)
    print("%-12s %5d %7.3f%% %7.3f%% %+7.3f%% %7.2f %+7.3f%% %+7.3f%% %+8.3f%% %7.2f"
          % (b["label"], b["n"], b["implied"], b["realised"], b["edge"], b["ratio"],
             b["p5"], b["p1"], b["es"], b["eff"]))
best_edge = max(rows, key=lambda r: r["edge"])
best_eff = max(rows, key=lambda r: r["eff"])
print("\n   highest raw EDGE      : %s (%+.3f%%/day)" % (best_edge["label"], best_edge["edge"]))
print("   highest EDGE per unit of TAIL : %s (%.2f)" % (best_eff["label"], best_eff["eff"]))
print("   >>> %s" % ("same bucket -- the safest-looking regime is also the best paid"
                     if best_edge["label"] == best_eff["label"] else
                     "DIFFERENT buckets: the best-paid regime is not the best risk-adjusted "
                     "one, so 'sell when VIX is low' and 'sell where the edge is' disagree"))

print("\n=== 2. CROSSED WITH RECENT MOVE ===")
print("%-28s %5s %8s %7s %9s %7s" % ("state", "n", "EDGE", "ratio", "ES(5%)", "EDGE/ES"))
print("-" * 70)
prev = D.r.abs()
for vlab, vm in (("VIX<=13", D.vix <= 13), ("VIX 13-17", (D.vix > 13) & (D.vix <= 17)),
                 ("VIX>17", D.vix > 17)):
    for mlab, mm in (("quiet (|r|<0.5%)", prev < 0.5), ("moved (|r|>1%)", prev > 1)):
        b = block(vm & mm, "%s / %s" % (vlab, mlab))
        if not b: continue
        print("%-28s %5d %+7.3f%% %7.2f %+8.3f%% %7.2f"
              % (b["label"], b["n"], b["edge"], b["ratio"], b["es"], b["eff"]))

print("\n=== 3. WHERE IS TODAY? ===")
cur = D.iloc[-1]
print("   last observation %s: VIX %.1f, prior move %+.2f%%"
      % (D.index[-1].date(), cur.vix, cur.r))
b = block((D.vix <= 13) & (prev < 0.5), "VIX<=13 / quiet")
if b:
    print("   that state historically: edge %+.3f%%/day, ratio %.2f, worst-5%% mean %+.3f%%, "
          "edge/tail %.2f" % (b["edge"], b["ratio"], b["es"], b["eff"]))
json.dump({"by_vix": rows}, open("conditional_vrp_result.json", "w"), indent=1)
print("\nwrote conditional_vrp_result.json")
