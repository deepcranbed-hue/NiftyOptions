"""
strategy_framework/run_demo.py
==============================
End-to-end demo / CLI for the directional-momentum strategy framework.

    python strategy_framework/run_demo.py suggest --expiry <ISO> [--now <ISO>]
    python strategy_framework/run_demo.py backtest --expiry <ISO> [--exit horizon|expiry] [--hold N]
    python strategy_framework/run_demo.py expiries

If --now is omitted for `suggest`, the latest capture for the expiry is used.
If --expiry is omitted, the most recent expiry in the DB is used.
"""
from __future__ import annotations
import argparse, json, sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategy_framework.config.settings import FrameworkConfig
from strategy_framework.signals.data_access import DataAccess
from strategy_framework.strategy import suggester
from strategy_framework.backtest import walkforward


def _pick_expiry(cfg, expiry):
    da = DataAccess(cfg.db_path)
    exps = da.expiries()
    if not exps:
        print("No expiries in DB:", cfg.db_path); sys.exit(1)
    return expiry or exps[-1]


def cmd_suggest(cfg, args):
    expiry = _pick_expiry(cfg, args.expiry)
    da = DataAccess(cfg.db_path)
    now = args.now
    if not now:
        caps = da.list_captures(expiry=expiry)
        if not caps:
            print("No captures for expiry", expiry); return
        now = caps[-1]["captured_at"]
    sug = suggester.suggest(cfg, now, expiry)
    print(json.dumps(sug.as_dict(), indent=2, default=str))


def cmd_backtest(cfg, args):
    expiry = _pick_expiry(cfg, args.expiry)
    res = walkforward.run(cfg, expiry, exit_mode=args.exit, hold=args.hold)
    out = {"config": cfg.summary(), "expiry": expiry,
           "exit_mode": args.exit, "hold": args.hold,
           "metrics": res.metrics,
           "n_decisions": len(res.decisions),
           "trades": res.trades}
    print(json.dumps(out, indent=2, default=str))


def cmd_expiries(cfg, args):
    da = DataAccess(cfg.db_path)
    for e in da.expiries():
        caps = da.list_captures(expiry=e)
        span = (caps[0]["captured_at"], caps[-1]["captured_at"]) if caps else ("-", "-")
        print(f"{e}  captures={len(caps):>4}  span={span[0]} .. {span[1]}")


def main():
    ap = argparse.ArgumentParser(description="Directional-momentum strategy framework")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest"); s.add_argument("--expiry"); s.add_argument("--now")
    b = sub.add_parser("backtest"); b.add_argument("--expiry")
    b.add_argument("--exit", default="horizon", choices=["horizon", "expiry", "manage"])
    b.add_argument("--hold", type=int, default=2)
    sub.add_parser("expiries")
    args = ap.parse_args()

    cfg = FrameworkConfig()
    print(f"# DB: {cfg.db_path}", file=sys.stderr)
    {"suggest": cmd_suggest, "backtest": cmd_backtest,
     "expiries": cmd_expiries}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
