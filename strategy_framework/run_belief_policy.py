"""
strategy_framework/run_belief_policy.py
=======================================
THE ULTIMATE EXPERIMENT: does the belief engine improve trading decisions?

Runs the SAME long-call/flat option policy twice over the same window, through the
same simulator, costs, and accounting — only the decision inputs differ:

  A. SIGNAL-driven : entry/exit from one raw signal (the pre-belief way)
  B. BELIEF-driven : entry when the trend_direction BELIEF is bullish AND the
                     trend_quality BELIEF clears a bar; exit when the belief flips.

If B consistently wins (net, drawdown, profit factor) across enough sessions, the
market-state layer has earned its complexity; if it doesn't, that's the honest answer.
IC/hit-rate are intermediate metrics — this is the terminal one.

    python -m strategy_framework.run_belief_policy \
        --from 2026-06-29 --to 2026-07-24 \
        --signal heavyweight_leadership --entry-thr 0.3 --exit-thr 0.3 \
        --quality-thr 0.0 --min-profit-pct 8 --stop-pct 12
"""
from __future__ import annotations
import argparse

from strategy_framework.backtest.signal_policy import run_policy, run_belief_policy


def _row(tag: str, res) -> str:
    s = res.stats
    if res.params.get("error"):
        return f"{tag:<18} ERROR: {res.params['error']}"
    if s.get("n_trades", 0) == 0:
        return f"{tag:<18} no trades ({s.get('note', '')})"
    return (f"{tag:<18} trades={s['n_trades']:<3} win={s['win_rate']}%  net=₹{s['net_pnl']:>7.0f}  "
            f"onBudget={s['return_on_budget_pct']}%  PF={s['profit_factor']}  "
            f"maxDD=₹{s['max_drawdown']:.0f}  exits={s['exit_reasons']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="A/B: belief-driven vs signal-driven policy.")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--signal", default="heavyweight_leadership", help="baseline signal (arm A)")
    ap.add_argument("--entry-thr", type=float, default=0.3)
    ap.add_argument("--exit-thr", type=float, default=0.3)
    ap.add_argument("--entry-factor", default="trend_direction")
    ap.add_argument("--quality-factor", default="trend_quality")
    ap.add_argument("--quality-thr", type=float, default=0.0, help="trend_quality belief must be "
                    "≥ this to allow entry (arm B's extra gate)")
    ap.add_argument("--min-profit-pct", type=float, default=8.0)
    ap.add_argument("--stop-pct", type=float, default=12.0)
    ap.add_argument("--budget", type=float, default=200000.0)
    ap.add_argument("--lots", type=int, default=1)
    args = ap.parse_args()

    common = dict(date_from=args.date_from, date_to=args.date_to,
                  entry_thr=args.entry_thr, exit_thr=args.exit_thr,
                  min_profit_pct=args.min_profit_pct, stop_pct=args.stop_pct,
                  budget=args.budget, n_lots=args.lots)

    a = run_policy(signal=args.signal, regime_by="none", regime="all", **common)
    b = run_belief_policy(entry_factor=args.entry_factor,
                          quality_factor=args.quality_factor, quality_thr=args.quality_thr,
                          **common)

    print("=" * 100)
    print(f"A/B — does the belief engine improve decisions?   window "
          f"{a.params.get('date_from')}..{a.params.get('date_to')}   "
          f"factor-map v{b.params.get('factor_map_version')}")
    print(f"same simulator/costs/exits for both; only the decision inputs differ.")
    print("=" * 100)
    print(_row(f"A signal({args.signal[:10]})", a))
    print(_row("B beliefs", b))
    print("-" * 100)
    print("B enters only when trend_direction belief ≥ thr AND trend_quality belief ≥ "
          f"{args.quality_thr}; A enters on the raw signal alone.")
    print("Honest read: one window is ONE sample — a verdict needs this comparison to hold across")
    print("many sessions/windows. Do NOT tune thresholds until B wins; that would be P&L-fitting")
    print("the belief layer, exactly the collapse the architecture forbids.")


if __name__ == "__main__":
    main()
