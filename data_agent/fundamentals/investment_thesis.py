#!/usr/bin/env python3
"""
investment_thesis.py — Investment Thesis Engine for Nifty IT (fundamentals, not daily attribution).

Answers "what has to be true for Nifty IT to deliver X%?" and "what growth is the
market already pricing in?" via an interpretable causal bridge, NOT opaque scores.

Pipeline:
    growth bridge (named drivers, categorical states -> % contributions)
        -> Revenue CAGR
        -> EPS CAGR  (+ margin trajectory + buyback)
    valuation: current P/E vs historical-mean P/E  -> re-rating
    FORWARD : expected return = EPS_CAGR + yield + rerating   (bear/base/bull)
    REVERSE : market-implied EPS_CAGR = required_return - yield - rerating
              verdict = bridge_EPS_CAGR - implied  (undervalued / stretched)
    sensitivity: swing the uncertain drivers -> honest confidence

Assumptions are explicit and overridable (later fed by NewsAgent classifications).
The engine organizes judgment transparently; it is not an oracle.

USAGE
    python investment_thesis.py
    python investment_thesis.py --current-pe 24 --hist-pe 27 --req-return 12 --years 3 --json
"""
from __future__ import annotations
import argparse, json, sys

# --- LAYER 1: growth-capacity bridge -----------------------------------------
# each driver: current categorical state -> annual revenue-CAGR contribution (pp)
# (states are what NewsAgent will set from evidence; maps are the economics)
BRIDGE = {
    "legacy_outsourcing": {"state": "stable",    "map": {"declining": 1.0, "stable": 2.0, "growing": 3.0}},
    "cloud_migration":    {"state": "strong",    "map": {"weak": 1.0, "moderate": 2.0, "strong": 3.0}},
    "ai_implementation":  {"state": "very_high", "map": {"low": 1.0, "high": 3.0, "very_high": 4.0}},
    "cybersecurity":      {"state": "strong",    "map": {"weak": 1.0, "strong": 2.0}},
    "pricing":            {"state": "pressure",  "map": {"stable": 0.0, "pressure": -2.0, "severe": -3.0}},
    "ai_automation":      {"state": "high",       "map": {"low": -1.0, "medium": -2.5, "high": -4.0}},
    "wage_inflation":     {"state": "normal",     "map": {"low": -0.5, "normal": -1.0, "high": -1.5}},
}
# drivers whose state is most uncertain — flexed for the confidence/sensitivity check
UNCERTAIN = ["ai_automation", "ai_implementation", "pricing"]

# EPS = revenue growth + margin trajectory + buyback (the "revenue slows, profit holds" nuance)
MARGIN_CAGR = 1.0     # pp/yr from AI-led efficiency (can be + even as revenue slows)
BUYBACK_YIELD = 2.0   # pp/yr EPS lift from buybacks
DIV_YIELD = 2.0       # pp/yr dividend
# valuation & horizon defaults (overridable) — seeded from current sell-side research
DEF_CURRENT_PE = 24.0
DEF_HIST_PE = 27.0    # ~5-10yr mean; the re-rating anchor
DEF_REQ_RETURN = 12.0 # required return for the reverse solve
DEF_YEARS = 3


def revenue_cagr(bridge):
    return round(sum(d["map"][d["state"]] for d in bridge.values()), 2)


def eps_cagr(rev):
    return round(rev + MARGIN_CAGR + BUYBACK_YIELD, 2)


def rerating_cagr(current_pe, target_pe, years):
    return round(((target_pe / current_pe) ** (1.0 / years) - 1.0) * 100, 2)


def forward(eps_g, yield_pp, rerate_pp, years):
    ann = eps_g + yield_pp + rerate_pp
    cum = ((1 + ann / 100.0) ** years - 1.0) * 100
    return round(ann, 2), round(cum, 1)


def reverse(req_return, yield_pp, rerate_pp):
    """EPS CAGR the current price implies, given the required return + re-rating path."""
    return round(req_return - yield_pp - rerate_pp, 2)


def scenario(bridge, current_pe, years, tag, rev_override=None, target_pe=None):
    rev = rev_override if rev_override is not None else revenue_cagr(bridge)
    eg = eps_cagr(rev)
    tpe = target_pe if target_pe is not None else DEF_HIST_PE
    rr = rerating_cagr(current_pe, tpe, years)
    yld = DIV_YIELD + BUYBACK_YIELD
    ann, cum = forward(eg, yld, rr, years)
    return {"tag": tag, "revenue_cagr": rev, "eps_cagr": eg, "target_pe": tpe,
            "rerating_cagr": rr, "yield": yld, "annual_return": ann, "cumulative_return": cum}


def sensitivity(bridge, current_pe, years):
    """Swing uncertain drivers to worst/best states -> return range = honest confidence."""
    lows, highs = dict(bridge), dict(bridge)
    def clone(b): return {k: {"state": v["state"], "map": v["map"]} for k, v in b.items()}
    lo, hi = clone(bridge), clone(bridge)
    for d in UNCERTAIN:
        states = sorted(bridge[d]["map"], key=lambda s: bridge[d]["map"][s])
        lo[d]["state"], hi[d]["state"] = states[0], states[-1]
    lo_r = scenario(lo, current_pe, years, "bear_drivers")["annual_return"]
    hi_r = scenario(hi, current_pe, years, "bull_drivers")["annual_return"]
    swing = round(hi_r - lo_r, 2)
    conf = "High" if swing < 6 else "Medium" if swing < 12 else "Low"
    return lo_r, hi_r, swing, conf


def build(current_pe, hist_pe, req_return, years):
    yld = DIV_YIELD + BUYBACK_YIELD
    base = scenario(BRIDGE, current_pe, years, "base")
    # bear: cheap stays cheap (no re-rating) + weaker drivers; bull: re-rate past mean + stronger
    bear = scenario(BRIDGE, current_pe, years, "bear",
                    rev_override=revenue_cagr(BRIDGE) - 3, target_pe=current_pe)
    bull = scenario(BRIDGE, current_pe, years, "bull",
                    rev_override=revenue_cagr(BRIDGE) + 3, target_pe=hist_pe + 2)
    rr = base["rerating_cagr"]
    bridge_eps = base["eps_cagr"]
    # TWO reverse framings, kept separate on purpose:
    implied_flat = round(req_return - yld, 2)          # growth the price needs at TODAY's multiple
    implied_rerate = round(req_return - yld - rr, 2)   # growth needed IF the multiple re-rates to fair
    growth_gap = round(bridge_eps - implied_flat, 2)   # >0 => cheap on earnings alone
    lo_r, hi_r, swing, conf = sensitivity(BRIDGE, current_pe, years)

    if growth_gap >= 1.0:
        verdict = (f"UNDERVALUED ON GROWTH — your bridge ({bridge_eps}% EPS CAGR) beats the "
                   f"{implied_flat}% the price needs at a flat multiple; any re-rating is upside on top.")
    elif growth_gap <= -1.0 and rr > 1.0:
        verdict = (f"PRICED FOR ITS GROWTH — upside is RE-RATING, not earnings. At today's multiple the "
                   f"price needs {implied_flat}% EPS CAGR but the bridge supports only {bridge_eps}%. The "
                   f"bull case rests on the cheap multiple normalizing (+{rr}%/yr), i.e. flows/sentiment — "
                   f"consistent with a Jefferies-led rally while guidance stays soft.")
    elif growth_gap <= -1.0:
        verdict = (f"STRETCHED — price needs {implied_flat}% EPS CAGR, bridge supports {bridge_eps}%, and "
                   f"no valuation cushion (already at/above fair P/E).")
    else:
        verdict = (f"FAIRLY PRICED ON GROWTH — bridge {bridge_eps}% ≈ implied {implied_flat}%; the "
                   f"re-rating (+{rr}%/yr) is the swing factor.")

    contribs = {k: d["map"][d["state"]] for k, d in BRIDGE.items()}
    return {
        "sector": "Nifty IT", "horizon_years": years,
        "assumptions": {"states": {k: d["state"] for k, d in BRIDGE.items()},
                        "current_pe": current_pe, "hist_mean_pe": hist_pe,
                        "required_return": req_return, "margin_cagr": MARGIN_CAGR,
                        "buyback_yield": BUYBACK_YIELD, "div_yield": DIV_YIELD},
        "growth_bridge": contribs,
        "revenue_cagr": revenue_cagr(BRIDGE), "eps_cagr_bridge": base["eps_cagr"],
        "forward": {"base": base, "bear": bear, "bull": bull},
        "reverse": {"bridge_eps_cagr": bridge_eps,
                    "market_implied_eps_cagr_flat_multiple": implied_flat,
                    "market_implied_eps_cagr_if_rerates": implied_rerate,
                    "growth_gap_pp": growth_gap, "rerating_cushion_cagr": rr},
        "confidence": {"driver_swing_pp": swing, "return_range": [lo_r, hi_r], "level": conf},
        "verdict": verdict,
        "biggest_drag": min(contribs, key=contribs.get),
        "biggest_lift": max(contribs, key=contribs.get),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--current-pe", type=float, default=DEF_CURRENT_PE)
    ap.add_argument("--hist-pe", type=float, default=DEF_HIST_PE)
    ap.add_argument("--req-return", type=float, default=DEF_REQ_RETURN)
    ap.add_argument("--years", type=int, default=DEF_YEARS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = build(a.current_pe, a.hist_pe, a.req_return, a.years)
    if a.json:
        print(json.dumps(r, indent=2)); return
    f = r["forward"]
    print(f"\n=== Investment Thesis — {r['sector']} ({r['horizon_years']}y) ===")
    print("  Growth bridge (revenue CAGR contributions, pp):")
    for k, v in r["growth_bridge"].items():
        print(f"    {k:<20}{v:+.1f}   [{r['assumptions']['states'][k]}]")
    print(f"  => Revenue CAGR {r['revenue_cagr']}%   EPS CAGR {r['eps_cagr_bridge']}% "
          f"(+margin {MARGIN_CAGR}, +buyback {BUYBACK_YIELD})")
    print(f"\n  Forward return (annualized / cumulative):")
    for k in ("bear", "base", "bull"):
        s = f[k]
        print(f"    {k:<5} EPS {s['eps_cagr']:>5}%  yield {s['yield']:>4}%  "
              f"re-rate {s['rerating_cagr']:>6}% (P/E->{s['target_pe']})  "
              f"=> {s['annual_return']:>5}%/yr  ({s['cumulative_return']}% total)")
    rv = r["reverse"]
    print(f"\n  REVERSE (what's priced in):")
    print(f"    market needs {rv['market_implied_eps_cagr_flat_multiple']}% EPS CAGR at today's multiple "
          f"(or {rv['market_implied_eps_cagr_if_rerates']}% if it re-rates to fair)")
    print(f"    your bridge delivers {rv['bridge_eps_cagr']}%   -> growth gap {rv['growth_gap_pp']:+}pp, "
          f"re-rating cushion +{rv['rerating_cushion_cagr']}%/yr")
    c = r["confidence"]
    print(f"  Confidence: {c['level']} (driver swing {c['driver_swing_pp']}pp, "
          f"return range {c['return_range'][0]}%..{c['return_range'][1]}%/yr)")
    print(f"  Biggest drag: {r['biggest_drag']}   biggest lift: {r['biggest_lift']}")
    print(f"\n  VERDICT: {r['verdict']}")
    print("\n--- JSON ---"); print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
