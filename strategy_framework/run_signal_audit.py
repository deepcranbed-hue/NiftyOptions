"""
strategy_framework/run_signal_audit.py
======================================
SIGNAL-CONSTRUCTION AUDIT — the bounded, positive-EV hygiene pass to run BEFORE
spending effort refining signals or elaborating the backtester. It answers two
"are the signals even built right" questions, neither of which is alpha-hunting:

  PART A — HORIZON ALIGNMENT.
    For each signal, where does its predictive power actually peak across a fine
    forward-horizon grid (unconditional, regime_by=none, off minute bars)? A signal
    whose edge lives at 15m but is read/used at 60m (or vice-versa) is mis-aligned by
    construction — the fix is horizon, not more data. We show each signal's natural
    horizon by |IC| and by gross expectancy, so a mismatch is visible.

  PART B — COLLINEARITY.
    How many INDEPENDENT bets does the roster really contain? The hollow/15m "cluster"
    was five volume/momentum cousins voting together (~1–2 real degrees of freedom, not
    five confirmations). We build the score-correlation matrix, list the near-duplicate
    pairs (|ρ|>0.7), rank each signal's redundancy, and report the participation-ratio
    'effective independent' count.

Everything DELEGATES to existing canonical code (CLAUDE.md DRY):
  * horizon IC/expectancy  → api.signal_regime_horizon (regime_by='none')
  * per-capture scores      → api._eval_signals_series
  * correlation math        → analysis.signal_ensemble.corr_matrix_full / redundancy /
                              effective_independent (HARD RULE 12 — one home)

Run on the full Drive history:

    python -m strategy_framework.run_signal_audit --from 2026-06-29 --to 2026-07-24
"""
from __future__ import annotations
import argparse


def _fmt(x, nd=3, plus=False):
    if x is None:
        return "   -"
    return (f"{x:+.{nd}f}" if plus else f"{x:.{nd}f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Signal-construction audit: horizon alignment + collinearity.")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--horizons", default="5,10,15,20,30,45,60,90,120",
                    help="fine forward-horizon grid (minutes) for the alignment scan")
    ap.add_argument("--rho", type=float, default=0.7, help="|corr| above this = near-duplicate pair")
    ap.add_argument("--inc-horizon", type=int, default=30, help="forward horizon (min) for the "
                    "incremental-IC study (Part C/D)")
    ap.add_argument("--cond-regime-by", default="tape_vol", choices=["tape_vol", "oi", "none"],
                    help="regime axis for the conditional incremental-IC study (Part D)")
    ap.add_argument("--factor-rho", type=float, default=0.6, help="|corr| to link two signals "
                    "into the same discovered factor (Part E)")
    ap.add_argument("--write-kb", action="store_true", help="persist Part C's per-signal evidence "
                    "to the knowledge base (knowledge/evidence.json) — this is what Belief Quality "
                    "reads. Re-run periodically as sessions accumulate.")
    args = ap.parse_args()

    from strategy_framework.api import (signal_regime_horizon, _eval_signals_series,
                                        _attach_front_expiry, _CFG)
    from strategy_framework.signals.data_access import DataAccess
    from strategy_framework.analysis.signal_ensemble import (
        corr_matrix_full, redundancy, effective_independent)
    import numpy as np

    # ---------------- PART A: horizon alignment ----------------
    # n_boot=0: we only need the point IC/expectancy per horizon here, not the bootstrap.
    res = signal_regime_horizon(args.date_from, args.date_to, min_n=1, regime_by="none",
                                horizons=args.horizons, n_boot=0)
    if "error" in res:
        print("ERROR:", res["error"])
        return
    horizons = res["horizons"]
    matrix = res["matrix"]
    n_sessions = len(res.get("session_dates", []))
    mom = _CFG.momentum.as_dict() if hasattr(_CFG, "momentum") else {}

    print("=" * 92)
    print(f"SIGNAL AUDIT   range {res.get('date_from')}..{res.get('date_to')}   sessions={n_sessions}")
    print(f"momentum-family lookback (MomentumWindow): {mom}")
    print("=" * 92)
    print("PART A — HORIZON ALIGNMENT   (unconditional; where each signal's edge peaks)")
    print(f"{'signal':<26}{'peak|IC|@':>10}{'IC':>8}{'peakExp@':>10}{'exp%':>8}{'eff_n':>7}")
    print("-" * 92)
    rowsA = []
    for sig in sorted(matrix.keys()):
        cells = matrix[sig].get("all", {})
        best_ic = max((h for h in horizons if cells.get(h, {}).get("ic") is not None),
                      key=lambda h: abs(cells[h]["ic"]), default=None)
        best_ex = max((h for h in horizons if cells.get(h, {}).get("gross_exp_pct") is not None),
                      key=lambda h: abs(cells[h]["gross_exp_pct"]), default=None)
        ic = cells.get(best_ic, {}).get("ic") if best_ic else None
        ex = cells.get(best_ex, {}).get("gross_exp_pct") if best_ex else None
        eff = cells.get(best_ic, {}).get("eff_n") if best_ic else None
        rowsA.append((sig, best_ic, ic, best_ex, ex, eff))
    # sort by |IC| so the strongest-aligned signals surface first
    for sig, bic, ic, bex, ex, eff in sorted(rowsA, key=lambda r: -(abs(r[2]) if r[2] else 0)):
        print(f"{sig:<26}{(str(bic)+'m' if bic else '-'):>10}{_fmt(ic, 3, True):>8}"
              f"{(str(bex)+'m' if bex else '-'):>10}{_fmt(ex, 3, True):>8}{(eff if eff else 0):>7}")
    print("-" * 92)
    print("Read: if peak|IC|@ and peakExp@ disagree, the signal ranks direction at one horizon but")
    print("      pays at another. A momentum signal peaking far from its lookback is mis-aligned.")

    # ---------------- PART B: collinearity ----------------
    da = DataAccess(_CFG.db_path)
    all_caps = da.list_captures()
    _attach_front_expiry(da, all_caps)
    lo, hi = (args.date_from or "0000"), (args.date_to or "9999")
    caps = [c for c in all_caps if lo <= c["captured_at"][:10] <= hi]
    dir_specs, records = _eval_signals_series(da, caps)
    cols = {s.name: [r["vals"][s.name][0] for r in records] for s in dir_specs}
    names, mat, pair_n = corr_matrix_full(cols)
    C = np.array([[np.nan if v is None else v for v in row] for row in mat], float)
    np.fill_diagonal(C, 1.0)
    red = redundancy(C)
    eff_ind = effective_independent(C)

    print("\n" + "=" * 92)
    print(f"PART B — COLLINEARITY   ({len(names)} signals, scores over {len(records)} captures)")
    print("=" * 92)
    if eff_ind is not None:
        print(f"EFFECTIVE INDEPENDENT BETS: {eff_ind:.1f} out of {len(names)} signals "
              f"(participation ratio). The roster is really ~{round(eff_ind)} distinct bets.")
    # near-duplicate pairs
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if mat[i][j] is not None and abs(mat[i][j]) >= args.rho:
                pairs.append((abs(mat[i][j]), names[i], names[j], mat[i][j], pair_n[i][j]))
    print(f"\nNEAR-DUPLICATE PAIRS  (|corr| ≥ {args.rho}):")
    if not pairs:
        print("  none — the signals are reasonably distinct at this threshold.")
    else:
        for _, a, b, r, nn in sorted(pairs, reverse=True):
            tag = "OPPOSED" if r < 0 else "duplicate"
            print(f"  {a:<24} ~ {b:<24} corr={r:+.2f}  (n={nn})  {tag}")
    # redundancy ranking
    print("\nREDUNDANCY (avg |corr| to the rest — high = adds little new information):")
    order = sorted(range(len(names)), key=lambda i: -(red[i] if not np.isnan(red[i]) else -1))
    for i in order:
        rv = red[i]
        print(f"  {names[i]:<26}{('%.3f' % rv) if not np.isnan(rv) else '   -':>8}")
    print("-" * 92)
    print("Takeaway: collapse near-duplicate pairs to one representative before weighting, so the")
    print("blend counts each bet once; treat the effective-independent count as your real breadth.")

    # ---------------- PART C: incremental information ----------------
    from strategy_framework.api import signal_incremental_ic
    inc = signal_incremental_ic(args.date_from, args.date_to, horizon=int(args.inc_horizon))
    print("\n" + "=" * 92)
    print(f"PART C — INCREMENTAL INFORMATION   (forward horizon {inc.get('horizon')}m, "
          f"controls = {inc.get('controls')})")
    print("=" * 92)
    if "error" in inc:
        print("  ", inc["error"])
    else:
        print(f"{'signal':<28}{'IC':>8}{'incr.IC':>9}{'n':>7}   verdict")
        print("-" * 92)
        for d in inc["signals"]:
            ic = f"{d['ic']:+.3f}" if d["ic"] is not None else "   -"
            ii = "(control)" if d["is_control"] else (f"{d['incremental_ic']:+.3f}" if d["incremental_ic"] is not None else "   -")
            verdict = ""
            if not d["is_control"] and d["ic"] is not None and d["incremental_ic"] is not None:
                if abs(d["incremental_ic"]) < 0.02 and abs(d["ic"]) > 0.05:
                    verdict = "redundant (IC captured by blend)"
                elif abs(d["incremental_ic"]) >= 0.03:
                    verdict = "ADDS new information"
            print(f"{d['name']:<28}{ic:>8}{ii:>9}{d['n']:>7}   {verdict}")
        print("-" * 92)
        print(inc["note"])
        if args.write_kb:
            from datetime import datetime as _dt
            from strategy_framework import knowledge as KB
            payload = {"written_at": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "date_from": args.date_from, "date_to": args.date_to,
                       "horizon": inc["horizon"], "controls": inc["controls"],
                       "n_sessions": len(inc.get("session_dates", [])),
                       "signals": {d["name"]: {"ic": d["ic"], "incremental_ic": d["incremental_ic"],
                                               "n": d["n"], "is_control": d["is_control"]}
                                   for d in inc["signals"]}}
            print("KB: wrote evidence →", KB.write_evidence(payload))

    # ---------------- PART D: conditional incremental IC (per regime) ----------------
    from strategy_framework.api import signal_conditional_incremental_ic
    cond = signal_conditional_incremental_ic(args.date_from, args.date_to,
                                             horizon=int(args.inc_horizon),
                                             regime_by=args.cond_regime_by)
    print("\n" + "=" * 92)
    print(f"PART D — CONDITIONAL INCREMENTAL IC   (incr. IC per regime, {cond.get('horizon')}m, "
          f"regime_by={args.cond_regime_by})   — 'when does each signal deserve to exist?'")
    print("=" * 92)
    if "error" in cond:
        print("  ", cond["error"])
    else:
        regs = cond["regimes"]
        print(f"{'signal':<26}" + "".join(f"{r[:10]:>11}" for r in regs))
        print("-" * 92)
        for name, m in cond["matrix"].items():
            if m["is_control"]:
                continue
            cells = m["cells"]
            row = f"{name:<26}"
            for r in regs:
                c = cells.get(r, {})
                v = c.get("incremental_ic")
                row += f"{(f'{v:+.2f}' if v is not None else '·'):>11}"
            print(row)
        print("-" * 92)
        print("Read each ROW: a signal with incr. IC in one regime and ~0/· in others should only vote")
        print("there (e.g. ADX in trend, not range). Cells with too few samples show ·. " + cond["note"][:0])

    # ---------------- PART E: factor discovery (data-driven clusters) ----------------
    from strategy_framework.api import signal_factor_discovery
    fac = signal_factor_discovery(args.date_from, args.date_to,
                                  corr_threshold=float(args.factor_rho), horizon=int(args.inc_horizon))
    print("\n" + "=" * 92)
    print(f"PART E — FACTOR DISCOVERY   (clusters at |corr|>={args.factor_rho}; primary = highest "
          f"incremental IC)   — 'which market properties exist?'")
    print("=" * 92)
    if "error" in fac:
        print("  ", fac["error"])
    else:
        print(f"{fac['n_signals']} directional signals → {fac['n_factors']} discovered factors\n")
        for fi, f in enumerate(fac["factors"], 1):
            tag = "singleton" if f["n_members"] == 1 else f"cohesion {f['cohesion']}"
            print(f"Factor {fi}  ({f['n_members']} signals · {tag})")
            for r in f["roles"]:
                wf = f"{r['within_factor_incr_ic']:+.3f}" if r.get("within_factor_incr_ic") is not None else "  -"
                ic = f"{r['ic']:+.3f}" if r["ic"] is not None else "  -"
                print(f"    {r['role']:<20}{r['name']:<26} IC {ic}  within-factor incrIC {wf}")
            print()
        print(fac["note"])


if __name__ == "__main__":
    main()
