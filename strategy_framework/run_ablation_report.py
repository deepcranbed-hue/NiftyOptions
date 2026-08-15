"""
strategy_framework/run_ablation_report.py
=========================================
BACKTESTING REPORT — did the new additions improve decisions?

The same 20 labelled mornings are replayed walk-forward under four versions of the
engine, each adding ONE correction, so every layer's contribution is isolated:

  M0  ranking only            analogue ranking picks a structure every day
                              (no gates, no familiarity) — the "before" engine
  M1  + risk gates            adds L1 fatal (coverage<0.05, trend-expansion)
                              and L2 (coverage<0.10 → no capped structures)
  M2  + familiarity           adds the unfamiliar-state stand-aside (<40%)
                              == the current morning-report hierarchy
  M3  + management overlay    M2's positions plus the state-triggered futures
                              overlay (breach + trend-expansion confirmation)

All at 1 lot, ₹85/leg everywhere (the slippage correction is baked into every
number). Baselines: always-condor / always-straddle / hindsight-best.
Output: per-day decisions to knowledge/ablation_report.csv + a summary verdict.

Honesty: gate thresholds were frozen AFTER seeing most of this window, so M1–M3
carry look-back flavour; the ablation shows the MECHANISM of each layer, the
journal measures the truth forward.

    python -m strategy_framework.run_ablation_report
"""
from __future__ import annotations
import csv
import os
import sqlite3

import numpy as np

from strategy_framework.run_analogue_days import _FEATS, _f
from strategy_framework.run_overlay_test import run_entry

_KNOW = os.path.join(os.path.dirname(__file__), "knowledge")
_STRUCTS = ["condor", "fly", "strangle", "straddle"]
_CAPPED = {"condor", "fly"}
_DB = "option_chains_full.db"


def _chain_info(d, expfull_map, cs):
    """Entry-morning chain facts: spot, ATM strike, walls, and a premium lookup —
    so every decision row records WHERE the market was and WHICH strikes were sold."""
    from strategy_framework.signals.data_access import DataAccess
    da = DataAccess(_DB)
    exp = expfull_map.get(cs[d]["expiry"])
    ch = da.chain_as_of(f"{d}T04:30:00Z", exp) if exp else None
    if not ch or ch.ts[:10] != d:
        return None
    K = min(ch.strikes, key=lambda k: abs(k - ch.spot))
    below = [(k, ch.put_oi.get(k, 0) or 0) for k in ch.strikes if k < ch.spot]
    above = [(k, ch.call_oi.get(k, 0) or 0) for k in ch.strikes if k > ch.spot]
    if not below or not above:
        return None
    pw = max(below, key=lambda x: x[1])[0]
    cw = max(above, key=lambda x: x[1])[0]

    def px(cp, k):
        v = (ch.call_ltp if cp == "C" else ch.put_ltp).get(k)
        return round(v, 1) if v else None

    def legs(structure, wing=100):
        L = {"straddle": [("C", K), ("P", K)],
             "strangle": [("C", cw), ("P", pw)],
             "condor": [("C", cw), ("C", cw + wing), ("P", pw), ("P", pw - wing)],
             "fly": [("C", K), ("P", K), ("C", K + wing), ("P", K - wing)]}[structure]
        shorts = {("C", cw), ("P", pw)} if structure in ("condor", "strangle") \
            else {("C", K), ("P", K)}
        parts, credit = [], 0.0
        for cp, k in L:
            p = px(cp, k)
            sgn = 1 if (cp, k) in shorts else -1
            if p is not None:
                credit += sgn * p
            parts.append(f"{'S' if sgn > 0 else 'L'}{cp}{k:.0f}@{p if p is not None else '?'}")
        return " ".join(parts), round(credit, 1)

    return {"spot": round(ch.spot, 1), "atm": K, "pw": pw, "cw": cw, "legs": legs}


def main():
    ms = list(csv.DictReader(open(os.path.join(_KNOW, "market_state.csv"))))
    cs = {r["entry"]: r for r in csv.DictReader(open(os.path.join(_KNOW, "chain_structure.csv")))}
    idx = {r["date"]: i for i, r in enumerate(ms)}
    days = sorted(d for d in idx if d in cs)
    con = sqlite3.connect(_DB)
    expfull = {r[0][:10]: r[0] for r in con.execute("SELECT DISTINCT expiry FROM chain_rows")}
    con.close()

    X = np.array([[(_f(r, c) if _f(r, c) is not None else np.nan) for c in _FEATS] for r in ms], float)
    mu, sd = np.nanmean(X, axis=0), np.nanstd(X, axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd

    def decide(k, d, use_gates, use_fam):
        t = ms[idx[d]]
        cov, er = _f(t, "coverage_ratio"), _f(t, "er30")
        schg = _f(t, "straddle_chg_30m_pct")
        fatal = use_gates and ((cov is not None and cov < 0.05) or
                               (schg is not None and schg >= 3.0) or
                               (er is not None and er >= 0.55))
        restrict = use_gates and not fatal and (cov is not None and cov < 0.10)
        ti = idx[d]
        sims = []
        for p in days[:k]:
            j = idx[p]
            m = ~np.isnan(Z[ti]) & ~np.isnan(Z[j])
            if m.sum() >= 8:
                sims.append((float(np.sqrt(np.mean((Z[ti][m] - Z[j][m]) ** 2))), j))
        sims.sort()
        top = sims[:7]
        fam = max(0.0, min(100.0, (1.6 - float(np.mean([x[0] for x in top]))) / 1.6 * 100)) \
            if top else 0.0
        if fatal or len(top) < 3 or (use_fam and fam < 40):
            return "STAND", fam
        allowed = [s for s in _STRUCTS if not (restrict and s in _CAPPED)]
        w = np.array([1.0 / (x[0] + 0.25) for x in top]); w /= w.sum()
        scores = {s: float((w * np.array([_f(cs[ms[j]["date"]], f"{s}_pnl") or 0.0
                                          for _, j in top])).sum()) for s in allowed}
        return max(scores, key=scores.get), fam

    models = {"M0_rank_only": (False, False), "M1_plus_gates": (True, False),
              "M2_plus_familiarity": (True, True)}
    rows, tots = [], {m: {"pnl": 0.0, "n": 0, "wins": 0, "worst": 0.0} for m in
                      list(models) + ["M3_plus_overlay", "always_condor",
                                      "always_straddle", "hindsight_best"]}
    overlay_cache = {}
    for k, d in enumerate(days):
        pnls = {s: _f(cs[d], f"{s}_pnl") or 0.0 for s in _STRUCTS}
        info = _chain_info(d, expfull, cs)
        rec = {"date": d,
               "spot_1000": info["spot"] if info else None,
               "atm_strike": info["atm"] if info else None,
               "put_wall": info["pw"] if info else None,
               "call_wall": info["cw"] if info else None}
        for mname, (g, f_) in models.items():
            ch, fam = decide(k, d, g, f_)
            got = pnls.get(ch, 0.0) if ch != "STAND" else 0.0
            rec[mname] = ch
            rec[f"{mname}_pnl"] = round(got, 0)
            if ch != "STAND" and info:
                lg, cr = info["legs"](ch)
                rec[f"{mname}_legs"] = lg
                rec[f"{mname}_credit_pts"] = cr
            else:
                rec[f"{mname}_legs"] = ""
                rec[f"{mname}_credit_pts"] = None
            T = tots[mname]
            T["pnl"] += got
            if ch != "STAND":
                T["n"] += 1
                T["wins"] += got > 0
                T["worst"] = min(T["worst"], got)
        # M3 = M2's choice + overlay delta on that structure
        ch = rec["M2_plus_familiarity"]
        got = rec["M2_plus_familiarity_pnl"]
        delta = 0.0
        trig = ""
        if ch != "STAND":
            key = (d, ch)
            if key not in overlay_cache:
                try:
                    u, mgd, tr = run_entry(_DB, expfull[cs[d]["expiry"]], d, ch)
                    overlay_cache[key] = (mgd - u, tr["ts"][5:16] if tr else "")
                except Exception:
                    overlay_cache[key] = (0.0, "err")
            delta, trig = overlay_cache[key]
        rec["M3_plus_overlay_pnl"] = round(got + delta, 0)
        rec["overlay_trigger"] = trig
        T = tots["M3_plus_overlay"]
        T["pnl"] += got + delta
        if ch != "STAND":
            T["n"] += 1
            T["wins"] += (got + delta) > 0
            T["worst"] = min(T["worst"], got + delta)
        for base, s in [("always_condor", "condor"), ("always_straddle", "straddle")]:
            T = tots[base]
            T["pnl"] += pnls[s]; T["n"] += 1; T["wins"] += pnls[s] > 0
            T["worst"] = min(T["worst"], pnls[s])
        hb = max(max(pnls.values()), 0.0)
        tots["hindsight_best"]["pnl"] += hb
        rows.append(rec)

    out = os.path.join(_KNOW, "ablation_report.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("ABLATION — what did each addition change?  (walk-forward, 20 mornings, "
          "₹85/leg everywhere)\n")
    print(f"{'model':<22}{'total ₹':>9}{'trades':>7}{'win%':>6}{'worst ₹':>9}")
    print("-" * 56)
    order = ["M0_rank_only", "M1_plus_gates", "M2_plus_familiarity", "M3_plus_overlay",
             "always_condor", "always_straddle", "hindsight_best"]
    for m in order:
        T = tots[m]
        wr = f"{100 * T['wins'] / T['n']:.0f}" if T["n"] else "·"
        print(f"{m:<22}{T['pnl']:>9.0f}{T['n']:>7}{wr:>6}{T['worst']:>9.0f}")
    print("-" * 56)
    print(f"wrote per-day decisions → {out}")


if __name__ == "__main__":
    main()
