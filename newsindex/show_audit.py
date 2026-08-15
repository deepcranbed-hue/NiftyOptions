#!/usr/bin/env python3
"""
show_audit.py — inspect the audit trail for the sector factor model.

This is the "show your working" runner. It fetches the same live data the report
uses, runs ONLY the audited section (build_sector_factor_model), and prints the
full trail: inputs with provenance, every arithmetic step, the decision threshold,
and the caveats.

WHAT IS AUDITED RIGHT NOW: build_sector_factor_model only. The causal engine,
scorecard, override analysis and AI-regime detection are NOT yet instrumented —
their conclusions appear in the report with no trail behind them.

Usage
-----
    python3 show_audit.py                  # live fetch, all sectors
    python3 show_audit.py --sector Banks   # one sector (substring match)
    python3 show_audit.py --offline        # no network; synthetic drivers, to
                                           # inspect the trail's SHAPE quickly
    python3 show_audit.py --save           # also write JSON to audit_trails/
    python3 show_audit.py --json           # dump raw JSON instead of markdown
"""

from __future__ import annotations

import argparse
import json
import sys

import market_scan as ms


def _offline_inputs():
    """Synthetic but realistic drivers, so the trail can be inspected with no network."""
    eng = {
        "drivers": {"oil_pct": -0.53, "us10y_pct": 0.40, "dxy_pct": 0.12,
                    "kospi_pct": -1.20, "sox_pct": -0.80, "vix_pct": 1.50,
                    "fii_kcr": -2.00, "india_cpi_hot": 1, "us_cpi_cool": 0},
        "raw": {"usdinr": 0.15},
        "brent_price": 84.0,
    }
    news = [
        {"title": "HDFC Bank Q1 profit beats estimates", "tags": "bank results"},
        {"title": "Ather EV scooter launch draws strong response", "tags": "ev auto"},
    ]
    return eng, [], news, "Neutral"


def _live_inputs():
    print("Fetching prices...", file=sys.stderr)
    quotes_idx = ms.fetch_quotes(ms.INDICES)
    ms.cross_check_indices(quotes_idx)
    quotes_macro = ms.fetch_quotes(ms.MACRO)
    print("Fetching FII/DII flows...", file=sys.stderr)
    flows = ms.fetch_fii_dii()
    print("Fetching news...", file=sys.stderr)
    news = ms.fetch_news()
    ai_regime, _ev, _conf = ms.detect_ai_regime(news, quotes_idx, quotes_macro)
    eng = ms.build_causal_engine(quotes_idx, quotes_macro, flows, news, ai_regime)
    return eng, quotes_macro, news, ai_regime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sector", help="filter to one sector (substring, e.g. Banks)")
    ap.add_argument("--offline", action="store_true", help="synthetic drivers, no network")
    ap.add_argument("--save", action="store_true", help="write the trail JSON to audit_trails/")
    ap.add_argument("--json", action="store_true", help="print raw JSON instead of markdown")
    a = ap.parse_args()

    eng, quotes_macro, news, ai_regime = _offline_inputs() if a.offline else _live_inputs()

    result = ms.build_sector_factor_model(eng, quotes_macro, {}, news, ai_regime)
    trail = getattr(ms, "_LAST_TRAIL", None)
    if trail is None:
        print("No trail produced — is audit.py importable from this directory?")
        sys.exit(1)

    # resolve the sector filter to the exact scope name used in the trail
    scope = None
    if a.sector:
        for s in result:
            if a.sector.lower() in s["sector"].lower():
                scope = s["sector"]
                break
        if scope is None:
            print(f"No sector matching {a.sector!r}. Available: "
                  + ", ".join(s["sector"] for s in result))
            sys.exit(1)

    if a.json:
        d = trail.to_dict()
        if scope:
            d["steps"] = [s for s in d["steps"] if s["scope"] == scope]
            d["decisions"] = [x for x in d["decisions"] if x["scope"] == scope]
            d["caveats"] = [c for c in d["caveats"] if c["scope"] == scope]
        print(json.dumps(d, indent=2))
    else:
        print(trail.to_markdown(scope=scope))
        print("\n**Net scores (what the trail above produced):**\n")
        print("| Sector | Net | Verdict |")
        print("|---|---:|---|")
        for s in result:
            if scope and s["sector"] != scope:
                continue
            print(f"| {s['sector']} | {s['net']:+.3f} | {s['verdict']} |")
        # cross-check: the trail must reproduce the reported net exactly
        print("\n**Reconciliation** — trail steps must sum to the reported net:\n")
        for s in result:
            if scope and s["sector"] != scope:
                continue
            tot = sum(st["result"] for st in trail.to_dict()["steps"]
                      if st["scope"] == s["sector"])
            ok = abs(tot - s["net"]) < 0.006          # rounding tolerance
            print(f"- {s['sector']}: steps sum {tot:+.3f} vs reported {s['net']:+.3f} "
                  f"{'✅ match' if ok else '❌ MISMATCH — trail does not explain the score'}")

    if a.save:
        p = trail.save()
        print(f"\nSaved: {p}", file=sys.stderr)


if __name__ == "__main__":
    main()
