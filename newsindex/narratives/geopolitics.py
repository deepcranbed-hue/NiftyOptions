#!/usr/bin/env python3
"""
narratives/geopolitics.py — the Geopolitics narrative plugin.

Included as the third reference because it is the classic CO-ACTIVATOR: it rarely
matters to Indian equities on its own, but compounds hard with Oil (Hormuz → crude →
CAD → inflation) and with Shipping. It is the reason the dispatcher emits relationships
rather than a flat list — Oil × Geopolitics is already a modelled interaction, and this
plugin is what makes that pairing visible at dispatch time.

Note the India-relevance weighting: Middle-East/Hormuz events are oil-dominant and
matter a great deal; Russia-Ukraine is currently muted for India. Treating all
geopolitics alike would overstate the second.
"""

from __future__ import annotations

from narratives.base import Narrative, Activation, register, _text, source_quality

_HIGH_RELEVANCE = ["hormuz", "iran", "gulf", "middle east", "israel", "red sea",
                   "houthi", "strait", "opec"]
_LOW_RELEVANCE = ["russia", "ukraine", "nato"]
_SEVERE = ["nuclear", "closure", "blockade", "invasion", "strike", "missile", "war"]


@register
class GeopoliticsNarrative(Narrative):
    name = "Geopolitics"
    priority = 2
    saturation = 3.0
    triggers = _HIGH_RELEVANCE + _LOW_RELEVANCE + _SEVERE + [
        "sanction", "sanctions", "tariff", "export ban", "defence budget",
    ]

    def detect(self, news, snap=None) -> Activation | None:
        hits, evidence, fired, severe = 0.0, [], set(), 0
        for n in news or []:
            t = _text(n)
            matched = [k for k in self.triggers if k in t]
            if not matched:
                continue
            # India-relevance weighting: Hormuz/Iran is oil-dominant for India;
            # Russia-Ukraine is currently muted. Equal weighting would overstate the latter.
            rel = 1.0 if any(k in t for k in _HIGH_RELEVANCE) else (
                0.3 if any(k in t for k in _LOW_RELEVANCE) else 0.6)
            hits += source_quality(n) * rel
            fired.update(matched)
            if any(k in t for k in _SEVERE):
                severe += 1
            title = (n.get("title") or "").strip()
            if title and len(evidence) < 6:
                evidence.append(title[:140])
        if hits <= 0:
            return None
        import math
        w = 1.0 - math.exp(-hits / max(0.5, self.saturation))
        return Activation(self.name, round(w, 3), "news", evidence, sorted(fired),
                          {"severe_headlines": severe,
                           "severity": ("SEVERE (closure/war/nuclear risk)" if severe >= 3
                                        else "elevated" if severe else "background")})

    def analyse(self, snap=None) -> dict:
        return {"question": "Does this reach India through OIL, FREIGHT, or risk premium?",
                "why_it_matters": "geopolitics is rarely a direct Indian-equity driver; "
                                  "it matters via the channels it opens"}

    def decision_tree(self) -> list[str]:
        return ["Geopolitical event",
                "  ├─ energy-corridor risk (Hormuz/OPEC) → OIL channel → 🔴 India macro",
                "  ├─ shipping-lane risk (Red Sea) → FREIGHT channel → 🔴 import-reliant mfg",
                "  ├─ defence spending → 🟢 HAL, BEL, BDL, Mazagon",
                "  └─ distant conflict (RU-UA) → muted for India; watch wheat/fertiliser only"]

    def transmission(self) -> list[str]:
        return ["Geopolitics → ① Oil ↑ → inflation → RBI → banks & consumer",
                "              → ② freight/tanker insurance ↑ → import landed cost ↑",
                "                    → chemicals, auto-components, electronics 🔴",
                "              → ③ defence order flow 🟢 (one branch, not the whole story)"]
