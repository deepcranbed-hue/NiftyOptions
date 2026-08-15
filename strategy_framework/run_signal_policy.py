"""
strategy_framework/run_signal_policy.py
=======================================
CLI for the constrained long-call/flat signal policy backtest
(strategy_framework/backtest/signal_policy.py). Prints a trade blotter and a
summary of realized, cost-and-constraint-aware performance.

Example (run where the Drive DB with option chains lives):

    python -m strategy_framework.run_signal_policy \
        --from 2026-06-29 --to 2026-07-24 \
        --signal rel_volume --regime-by oi --regime hollow \
        --entry-thr 0.35 --exit-thr 0.35 \
        --min-profit-pct 8 --stop-pct 12 --budget 200000 --lots 1
"""
from __future__ import annotations
import argparse

from strategy_framework.backtest.signal_policy import run_policy


def main() -> None:
    ap = argparse.ArgumentParser(description="Constrained long-call/flat signal policy backtest.")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--signal", default="rel_volume")
    ap.add_argument("--regime-by", default="oi", choices=["oi", "tape_vol", "none"])
    ap.add_argument("--regime", default="hollow", help="regime the entry is gated to "
                    "(e.g. hollow/coiled/conviction/churn for oi; trend·hi etc for tape_vol; all for none)")
    ap.add_argument("--entry-thr", type=float, default=0.35, help="score ≥ this (bullish) = BUY")
    ap.add_argument("--exit-thr", type=float, default=0.35, help="score ≤ −this (bearish) = SELL trigger")
    ap.add_argument("--min-profit-pct", type=float, default=8.0, help="only sell if premium up ≥ this %% net")
    ap.add_argument("--stop-pct", type=float, default=12.0, help="force-exit if premium down ≥ this %%")
    ap.add_argument("--budget", type=float, default=200000.0)
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--blotter", type=int, default=20, help="how many trades to print")
    args = ap.parse_args()

    res = run_policy(date_from=args.date_from, date_to=args.date_to, signal=args.signal,
                     regime_by=args.regime_by, regime=args.regime,
                     entry_thr=args.entry_thr, exit_thr=args.exit_thr,
                     min_profit_pct=args.min_profit_pct, stop_pct=args.stop_pct,
                     budget=args.budget, n_lots=args.lots)

    p = res.params
    if p.get("error"):
        print("ERROR:", p["error"])
        if p.get("available"):
            print("available signals:", ", ".join(p["available"]))
        return

    print("=" * 92)
    print(f"POLICY  long-call/flat   signal={p['signal']}  regime={p['regime']} ({p['regime_by']})")
    print(f"range {p['date_from']}..{p['date_to']}   captures={p['n_captures']}   "
          f"entry≥{p['entry_thr']}  exit≤−{p['exit_thr']}  minProfit={p['min_profit_pct']}%  "
          f"stop={p['stop_pct']}%  budget=₹{p['budget']:.0f}  lots={p['n_lots']}×{p['lot_size']}")
    print("=" * 92)

    s = res.stats
    if s.get("n_trades", 0) == 0:
        print(s.get("note", "no trades"))
        return

    if res.trades and args.blotter:
        print(f"{'entry':<20}{'exit':<20}{'strike':>8}{'in':>7}{'out':>7}{'net₹':>9}{'ret%':>7}{'min':>6}  why")
        print("-" * 92)
        for t in res.trades[:args.blotter]:
            print(f"{t.entry_ts[:16]:<20}{t.exit_ts[:16]:<20}{t.strike:>8.0f}{t.entry_prem:>7.1f}"
                  f"{t.exit_prem:>7.1f}{t.net_pnl:>9.0f}{t.ret_pct:>7.2f}{t.hold_min:>6.0f}  {t.exit_reason}")
        if len(res.trades) > args.blotter:
            print(f"... (+{len(res.trades) - args.blotter} more)")
        print("-" * 92)

    print(f"trades={s['n_trades']}   win_rate={s['win_rate']}%   net=₹{s['net_pnl']:.0f}"
          f"   on budget={s['return_on_budget_pct']}%   avg/trade=₹{s['avg_net_per_trade']:.0f}")
    print(f"avg_hold={s['avg_hold_min']}min   max_drawdown=₹{s['max_drawdown']:.0f}"
          f"   profit_factor={s['profit_factor']}   costs=₹{s['total_cost']:.0f}")
    print(f"exits: {s['exit_reasons']}")
    print("Note: profit/stop % are on OPTION PREMIUM (leverage), not the index. 'signal' exits met the")
    print("      profit gate; 'stop' were cut; 'eod' were still open at range end (marked out).")


if __name__ == "__main__":
    main()
