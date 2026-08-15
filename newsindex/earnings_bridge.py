#!/usr/bin/env python3
"""
earnings_bridge.py — connect the Earnings plugin to the hierarchical resolver's
COMPANY level.

The gap this fills
------------------
EarningsNarrative emits structured NarrativeSignals (Fundamentals / Guidance /
Valuation). resolve_company() consumes `conditions` (context multipliers) and
`company_signals` (idiosyncratic additive). Nothing joined them — earnings were
computed and dropped before reaching a company's score.

The split (from the structural-vs-contextual model)
---------------------------------------------------
Earnings information is TWO kinds of thing, and they go to different channels:

  GUIDANCE  → a CONDITION → context multiplier.
        Weak guidance doesn't add a fixed −X to TCS; it means TCS CAPTURES LESS of
        whatever tech-spend tailwind is in play. So "weak_guidance" scales the
        FACTOR_US_TECH_SPENDING exposure (×0.7) — exactly the CONTEXT_RULES we defined.
        A beat/strong guidance is the same in reverse (×1.2).

  FUNDAMENTALS (the standalone beat/miss verdict)  → an ADDITIVE company signal.
        A profit beat is good even on a flat-factor day — it has no factor to scale, so
        it enters as its own impulse.

  VALUATION (broker rating)  → additive, weakest (an opinion, not a fact).

So guidance modulates the macro capture; the raw result is its own term. One company,
one resolve_company() call, both channels fed from the same earnings extraction.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
import taxonomy as TAX  # noqa: E402

# guidance direction → the condition flag the resolver's CONTEXT_RULES understand
_GUIDANCE_CONDITION = {"Negative": "weak_guidance", "Positive": "deal_win"}


def bridge(earnings_signals: list) -> dict:
    """
    EarningsNarrative signals → {symbol: {"conditions": set, "company_signals": [...]}}.
    Ready to splat into resolve_company(sym, ..., **bridged[sym]).
    """
    out: dict[str, dict] = {}

    def _slot(sym):
        return out.setdefault(sym, {"conditions": set(), "company_signals": []})

    for s in (earnings_signals or []):
        d = s if isinstance(s, dict) else s.to_dict()
        # resolve the display name the plugin used ("Hdfc Bank", "Tcs") → symbol
        sym = TAX.resolve(d.get("company", "")) or (d.get("company") or "").upper()
        if not sym:
            continue
        dim = d.get("dimension")
        direction = d.get("direction", "Neutral")

        if dim == "Guidance":
            # GUIDANCE IS TWO THINGS (your fix). The forward EXPECTATION modifies how much
            # of a factor the company captures → a CONTEXT multiplier. The information
            # SURPRISE is independent and must register even when no factor is active
            # (a beat-but-weak-guidance name sells off on a flat-macro day) → an additive
            # COMPANY EVENT. So guidance fires BOTH channels.
            cond = _GUIDANCE_CONDITION.get(direction)
            if cond:
                _slot(sym)["conditions"].add(cond)
            # forward-surprise additive term (smaller than a full result beat/miss)
            surprise = {"Negative": -0.4, "Positive": +0.3}.get(direction)
            if surprise:
                _slot(sym)["company_signals"].append(
                    {"label": f"{direction.lower()} guidance (forward)", "signed": surprise,
                     "kind": "guidance_surprise"})

        elif dim == "Fundamentals":
            # the beat/miss verdict is a standalone impulse; a bare metric (Neutral) adds nothing
            if direction in ("Positive", "Negative"):
                _slot(sym)["company_signals"].append({
                    "label": f"{d.get('metric', 'result')} {direction.lower()}",
                    "signed": s.signed if not isinstance(s, dict) else d.get("signed", 0.0)})

        elif dim == "Valuation":
            v = s.signed if not isinstance(s, dict) else d.get("signed", 0.0)
            if abs(v) > 0.01:
                _slot(sym)["company_signals"].append(
                    {"label": f"broker {direction.lower()}", "signed": v})

    return out


if __name__ == "__main__":
    # demo: TCS weak guidance + ICICI beat → the two channels, from one extraction
    from narratives.earnings import EarningsNarrative
    import hierarchy as H
    news = [
        {"title": "TCS Q1 profit beats but management guides to slower H2, cautious outlook",
         "source": "ET Markets"},
        {"title": "ICICI Bank Q1 net profit beats estimates on strong loan growth",
         "source": "Business Standard"},
    ]
    sigs = EarningsNarrative().signals(news)
    b = bridge(sigs)
    print("bridged:", {k: {"conditions": sorted(v["conditions"]),
                           "signals": v["company_signals"]} for k, v in b.items()})
    print()
    ai = H.factor_impacts({"Semiconductors / AI": +0.6}, ai_regime="Substitution")
    for sym in b:
        v = H.resolve_company(sym, ai, **b[sym])
        print(f"{sym}: {v['verdict']} {v['final']:+.2f} — {v['explanation']}")
