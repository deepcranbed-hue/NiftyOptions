#!/usr/bin/env python3
"""
narratives/oil.py — the Oil narrative plugin.

Reference implementation of a PRICE-AWARE narrative: oil is active when the news talks
about it OR when Brent simply moves, even in silence. A pure keyword scan would miss a
+3.6% Brent day that generated no Indian headlines — which is exactly the kind of day
that matters most for the level amplifier.
"""

from __future__ import annotations

from narratives.base import Narrative, Activation, register


@register
class OilNarrative(Narrative):
    name = "Oil"
    priority = 1
    saturation = 3.0
    triggers = [
        "brent", "crude", "wti", "opec", "opec+", "oil price", "oil prices",
        "barrel", "refinery", "refining", "gasoline", "diesel", "petrol",
        "hormuz", "tanker", "pipeline", "shale", "oil supply", "oil demand",
        "windfall tax", "under-recovery", "marketing margin",
    ]

    # Brent moves of this size are themselves an event, headlines or not.
    PRICE_TRIGGER_PCT = 1.5

    def detect(self, news, snap=None) -> Activation | None:
        a = super().detect(news, snap)                 # news-driven component

        oil_pct = None
        for q in (snap or {}).get("quotes_macro", []) or []:
            if q.get("name") == "Brent Crude":
                oil_pct = q.get("pct_change")
                break

        if oil_pct is None:
            return a
        mag = abs(oil_pct)
        if mag < self.PRICE_TRIGGER_PCT:
            return a

        # Price weight saturates at ~4% — a 4% Brent day is decisively an oil day.
        pw = min(1.0, mag / 4.0)
        fact = f"Brent {oil_pct:+.2f}% (price-triggered, no headline needed)"
        if a is None:
            return Activation(self.name, round(pw, 3), "price", [fact], ["<price move>"],
                              {"oil_pct": oil_pct})
        # Both channels fired: take the stronger, and record that they agree. Adding
        # them would double-count one event described two ways.
        a.weight = round(max(a.weight, pw), 3)
        a.kind = "both"
        a.evidence.insert(0, fact)
        a.detail["oil_pct"] = oil_pct
        return a

    def analyse(self, snap=None) -> dict:
        return {"question": "Is this a SUPPLY shock, a DEMAND shock, or policy?",
                "why_it_matters": "supply shocks are bearish for India (import bill); "
                                  "demand-led strength is a global-growth signal"}

    def decision_tree(self) -> list[str]:
        return ["Oil move detected",
                "  ├─ supply-driven (OPEC cut / Hormuz / sanctions) → 🔴 India: CAD + inflation",
                "  ├─ demand-driven (global growth) → 🟡 mixed: reflation vs input cost",
                "  └─ policy (windfall tax / price freeze) → sector-specific, not macro"]

    def transmission(self) -> list[str]:
        return ["Oil ↑ → ① upstream realisations ↑ (ONGC)",
                "        → ② OMC input cost ↑ (margin/GRM-modified, weak link)",
                "        → ③ fuel consumers squeezed (aviation, paints, tyres)",
                "        → ④ macro: import bill → inflation → RBI → rate-sensitives"]
