"""
CalibrationAgent/run.py
=======================
CLI for the walk-forward calibration. ADVISORY ONLY — prints the evidence and writes
a proposal file; it never edits SignalWeights.

    python -m CalibrationAgent.run
    python -m CalibrationAgent.run --train-sessions 5 --test-sessions 2 --report
    python -m CalibrationAgent.run --pnl            # also run real backtest P&L (slow)
    python -m CalibrationAgent.run --json
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime

from CalibrationAgent import calibrate as C

_HERE = os.path.dirname(__file__)
_REPORTS, _STATE = os.path.join(_HERE, "reports"), os.path.join(_HERE, "state")


def _fmt(v, nd=3):
    return "·" if v is None else f"{v:+.{nd}f}"


def _text(rep: dict) -> str:
    out = [f"expiry={rep['expiry']}  sessions={rep['n_sessions']}  snapshots={rep['n_snapshots']}  "
           f"horizon={rep['horizon_min']}m  sampled={rep['sample_minutes']}m",
           f"folds: train {rep['train_sessions']} → test {rep['test_sessions']} sessions"]
    out.append("\nper-fold out-of-sample IC (held-out sessions the proposal never saw):")
    names = list(rep["summary"].keys())
    out.append("  " + f"{'fold':6}" + "".join(f"{n[:14]:>16}" for n in names))
    for f in rep["folds"]:
        row = "".join(f"{_fmt(f['results'].get(n, {}).get('ic')):>16}" for n in names)
        out.append(f"  {f['fold']:<6}{row}   test={','.join(f['test'])}")
    out.append("\naggregate across folds:")
    out.append("  " + f"{'method':22}{'mean_IC':>10}{'mean_hit':>10}{'mean_PnL':>12}{'beats_inc':>11}")
    for n in names:
        s = rep["summary"][n]
        hit = "·" if s["mean_hit"] is None else f"{s['mean_hit']:.3f}"
        pnl = "·" if s["mean_pnl"] is None else f"{s['mean_pnl']:,.0f}"
        beats = "·" if s["beats_incumbent_folds"] is None else f"{s['beats_incumbent_folds']}/{s['folds']}"
        out.append(f"  {n:22}{_fmt(s['mean_ic']):>10}{hit:>10}{pnl:>12}{beats:>11}")
    out.append("")
    if rep.get("proposal"):
        p = rep["proposal"]
        out.append(f"PROPOSAL — method '{p['method']}' beat the incumbent out-of-sample "
                   f"({p['beats_incumbent_folds']}/{p['of_folds']} folds, "
                   f"mean IC {p['oos_mean_ic']:+.4f} vs {p['incumbent_mean_ic']:+.4f}):")
        for k, v in sorted(p["weights"].items(), key=lambda kv: -kv[1]):
            if v > 0:
                out.append(f"    {k:24} {v:.3f}")
    else:
        out.append("NO PROPOSAL — no candidate beat the incumbent out-of-sample. Keep current weights.")
    out.append("\n" + rep["note"])
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Walk-forward calibration of signal weights (advisory)")
    ap.add_argument("--expiry", default=None)
    ap.add_argument("--train-sessions", type=int, default=3)
    ap.add_argument("--test-sessions", type=int, default=1)
    ap.add_argument("--horizon-min", type=int, default=60)
    ap.add_argument("--sample-minutes", type=float, default=30)
    ap.add_argument("--window-days", type=float, default=None)
    ap.add_argument("--pnl", action="store_true", help="also run real options-backtest P&L (slow)")
    ap.add_argument("--report", action="store_true", help="write report + proposal to reports//state/")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = C.calibrate(expiry=args.expiry, train_sessions=args.train_sessions,
                      test_sessions=args.test_sessions, horizon_min=args.horizon_min,
                      sample_minutes=args.sample_minutes, window_days=args.window_days,
                      with_pnl=args.pnl)
    if args.json:
        print(json.dumps(rep, indent=2)); return
    if "error" in rep:
        print("ERROR:", rep["error"]); print(rep.get("sessions", "")); return

    print("=== CALIBRATION (walk-forward, advisory only) ===")
    text = _text(rep)
    print(text)

    if args.report:
        os.makedirs(_REPORTS, exist_ok=True); os.makedirs(_STATE, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        rp = os.path.join(_REPORTS, f"calibration_{ts}.md")
        with open(rp, "w") as f:
            f.write(f"# Calibration — {datetime.now().isoformat(timespec='minutes')}\n\n"
                    f"```\n{text}\n```\n")
        sp = os.path.join(_STATE, "latest_proposal.json")
        with open(sp, "w") as f:
            json.dump({"generated": datetime.now().isoformat(timespec="minutes"),
                       "expiry": rep["expiry"], "summary": rep["summary"],
                       "proposal": rep.get("proposal"),
                       "sufficient": rep.get("sufficient"),
                       "applied": False,
                       "note": "ADVISORY — apply by hand via SignalWeights(overrides=…)."}, f, indent=2)
        print(f"\nreport:   {rp}\nproposal: {sp}  (advisory — not applied)")


if __name__ == "__main__":
    main()
