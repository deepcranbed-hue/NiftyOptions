"""
SignalWeightAgent/run.py
========================
CLI entry point for the Signal Weight Agent. The deterministic core lives in
`strategy_framework/analysis/` (shared with `api.signal_correlation`); this is the
agent's launcher + report writer.

Run from the repo root:
    python -m SignalWeightAgent.run --target NIFTY --horizon 60m
    python SignalWeightAgent/run.py --target RELIANCE --window-days 60
    python -m SignalWeightAgent.run --target NIFTY_FUT_1 --report     # writes reports/*.md
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime

from strategy_framework.analysis import signal_study
from strategy_framework.analysis.signal_ensemble import format_report, format_horizon_table

_REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")


def _write_report(rep: dict, text: str) -> str:
    """Write a timestamped markdown report to SignalWeightAgent/reports/."""
    os.makedirs(_REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    fn = f"signal_study_{rep.get('target', '?')}_{rep.get('horizon', '')}_{ts}.md"
    path = os.path.join(_REPORTS_DIR, fn)
    thin = (rep.get("n_obs", 0) or 0) < 60
    hdr = (f"# Signal Study — {rep.get('target')} @ {rep.get('horizon')}\n\n"
           f"- expiry: `{rep.get('expiry')}` · source: `{rep.get('source')}` · "
           f"generated: {datetime.now().isoformat(timespec='minutes')}\n"
           f"- n_obs: **{rep.get('n_obs')}** "
           f"{'(⚠ thin — PRIOR/descriptive, D-MA-04)' if thin else ''}\n\n"
           f"```\n{text}\n```\n")
    with open(path, "w") as f:
        f.write(hdr)
    return path


def main():
    ap = argparse.ArgumentParser(description="Signal Weight Agent — independence + weight proposer")
    ap.add_argument("--target", default="NIFTY",
                    help="instrument whose forward return the signals are judged against "
                         "(NIFTY, a stock symbol, or NIFTY_FUT_1 / _2)")
    ap.add_argument("--expiry", default=None, help="expiry (default: latest with captures)")
    ap.add_argument("--horizon", default="60m", help="forward horizon: 5m|15m|30m|60m|2h|3h")
    ap.add_argument("--window-days", type=float, default=None, help="last N session days only")
    ap.add_argument("--source", default="auto", choices=["auto", "store", "live"])
    ap.add_argument("--cluster-threshold", type=float, default=0.6,
                    help="|corr| at/above which two signals are one family (default 0.6)")
    ap.add_argument("--horizons", default=None,
                    help="comma list (e.g. 5m,15m,30m,60m) or 'all' → signal × horizon "
                         "comparison table instead of the single-horizon weight study")
    ap.add_argument("--metric", default="ic", choices=["ic", "rank_ic", "spread", "sharpe", "hit"],
                    help="which metric to show in the horizon comparison table")
    ap.add_argument("--sample-minutes", type=float, default=None,
                    help="space entries ~this many minutes apart to reduce overlap (e.g. 60 "
                         "→ non-overlapping 60m windows). Default = every snapshot.")
    ap.add_argument("--min-coverage", type=float, default=0.0,
                    help="require a signal to cover this FRACTION of the window before it can "
                         "influence correlations/families/weights. Default 0.0 (off) — healthy "
                         "signals legitimately sit at 54-79%% coverage, so set this deliberately "
                         "(e.g. 0.5) to screen out a genuinely sparse feed.")
    ap.add_argument("--common-sample", action="store_true",
                    help="restrict every computation to rows where ALL included signals have "
                         "data (identical rows per cell → coherent, PSD-safe matrix)")
    ap.add_argument("--report", action="store_true", help="also write a markdown report to reports/")
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead of the text report")
    args = ap.parse_args()

    # multi-horizon comparison mode
    if args.horizons:
        hs = None if args.horizons == "all" else [h.strip() for h in args.horizons.split(",")]
        grid = signal_study.study_horizons(target=args.target, expiry=args.expiry, horizons=hs,
                                           window_days=args.window_days, source=args.source,
                                           sample_minutes=args.sample_minutes)
        if args.json:
            print(json.dumps(grid, indent=2)); return
        if "error" in grid:
            print("ERROR:", grid["error"]); return
        text = format_horizon_table(grid, metric=args.metric)
        print(f"=== SIGNAL STUDY (horizons) — target={grid['target']}  "
              f"expiry={str(grid['expiry'])[:10]}  source={grid.get('source')} ===")
        print(text)
        if (grid.get("n_obs", 0) or 0) < 60:
            print(f"\n⚠ n_obs={grid.get('n_obs')} thin — PRIOR/descriptive (D-MA-04).")
        if args.report:
            grid["horizon"] = args.horizons
            print(f"\nreport written: {_write_report(grid, text)}")
        return

    rep = signal_study.study(target=args.target, expiry=args.expiry, horizon=args.horizon,
                             window_days=args.window_days, source=args.source,
                             cluster_threshold=args.cluster_threshold,
                             sample_minutes=args.sample_minutes,
                             min_coverage=args.min_coverage,
                             common_sample=args.common_sample)
    if args.json:
        print(json.dumps(rep, indent=2))
        return
    if "error" in rep:
        print("ERROR:", rep["error"])
        return
    text = format_report(rep)
    print(f"=== SIGNAL STUDY — target={rep['target']}  horizon={rep['horizon']}  "
          f"expiry={str(rep['expiry'])[:10]}  source={rep.get('source')} ===")
    print(text)
    n = rep.get("n_obs", 0)
    if n < 60:
        print(f"\n⚠ n_obs={n} is thin — treat as PLUMBING/PRIOR, not a verdict. "
              f"Re-run on ≥60 sessions of live history before locking weights (D-MA-04).")
    if args.report:
        print(f"\nreport written: {_write_report(rep, text)}")


if __name__ == "__main__":
    main()
