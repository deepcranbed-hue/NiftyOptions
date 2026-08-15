"""
MarketHealthAgent/run.py
========================
CLI launcher for the daily Market-Health gauge. The deterministic core lives in
`strategy_framework/market_health/` (shared with `api.market_health` and the UI
panel); this is the agent's launcher + report writer, mirroring SignalWeightAgent.

Run from the repo root:
    python -m MarketHealthAgent.run                 # latest, text
    python -m MarketHealthAgent.run --as-of 2026-07-01
    python -m MarketHealthAgent.run --json
    python -m MarketHealthAgent.run --report        # writes reports/*.md
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime

from strategy_framework.config.settings import FrameworkConfig
from strategy_framework.market_health.trend import market_health

_REPORTS = os.path.join(os.path.dirname(__file__), "reports")


def _fmt(r: dict) -> str:
    L = []
    L.append(f"MARKET HEALTH  {r['score']}/100  →  {r['band']}"
             if r["score"] is not None else "MARKET HEALTH  —  (insufficient data)")
    L.append(f"as-of {r['as_of']}  ·  {r['sessions']} daily sessions  ·  "
             f"coverage {r['coverage_pct']:.0f}% of the intended model")
    L.append("")
    for lname, lv in r["layers"].items():
        if lv["data_ready"]:
            L.append(f"  {lname:16} {lv['awarded']:>5.1f}/{lv['available_points']:<5.0f}"
                     f"  ({lv['pct']:.0f}% of available)")
        else:
            L.append(f"  {lname:16}   pending (no data yet)")
    L.append("")
    L.append("  components:")
    for k, v in r["components"].items():
        if v["data_ready"]:
            extra = {kk: vv for kk, vv in v.items()
                     if kk not in ("points", "data_ready", "score01", "awarded")}
            L.append(f"    {k:22} {v['awarded']:>5.1f}/{v['points']:<4.0f}  {extra}")
        else:
            L.append(f"    {k:22} pending  {v.get('note','')}")
    if r.get("notes"):
        L.append("")
        for n in r["notes"]:
            L.append(f"  ⚠ {n}")
    L.append("")
    L.append(f"  omitted (no feed): {', '.join(r['omitted_layers'])}")
    L.append(f"  {r['disclaimer']}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Daily market-health / trend gauge")
    ap.add_argument("--as-of", default=None, help="ISO date for a historical read (default: latest)")
    ap.add_argument("--json", action="store_true", help="emit raw JSON")
    ap.add_argument("--report", action="store_true", help="also write reports/*.md")
    args = ap.parse_args()

    r = market_health(FrameworkConfig().db_path, as_of=args.as_of)
    if args.json:
        print(json.dumps(r, indent=2)); return
    text = _fmt(r)
    print(text)
    if args.report:
        os.makedirs(_REPORTS, exist_ok=True)
        fn = f"market_health_{r['as_of'] or 'latest'}_{datetime.now():%Y-%m-%d_%H%M}.md"
        path = os.path.join(_REPORTS, fn)
        with open(path, "w") as f:
            f.write(f"# Market Health — {r['as_of']}\n\n```\n{text}\n```\n")
        print(f"\nreport written: {path}")


if __name__ == "__main__":
    main()
