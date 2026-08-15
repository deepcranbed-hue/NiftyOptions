#!/usr/bin/env python3
"""
narratives/semis.py — the Semiconductor / AI narrative plugin.

Reference implementation of an ADAPTER narrative. semis_regime.py already does the hard
work (6 cause regimes → 4 target buckets, capex signal, historical analogue). Per the
repo's DRY rule this plugin does NOT re-implement any of it — it registers the narrative
with the dispatcher and delegates analyse()/targets() to the existing module.

That is the migration pattern for every mature narrative: wrap, don't rewrite.
"""

from __future__ import annotations

from narratives.base import Narrative, Activation, register


def _semis_regime():
    """Import the existing engine module from either layout, or None if unavailable."""
    for mod in ("semis_regime", "overlay.semis_regime"):
        try:
            return __import__(mod, fromlist=["*"])
        except Exception:
            continue
    return None


@register
class SemiconductorNarrative(Narrative):
    name = "Semiconductors / AI"
    priority = 1
    saturation = 3.5
    triggers = [
        "semiconductor", "semis", "chip", "chips", "gpu", "hbm", "foundry",
        "nvidia", "tsmc", "amd", "micron", "broadcom", "asml", "sox",
        "ai capex", "ai infrastructure", "data centre", "data center",
        "hyperscaler", "ai spending", "ai budget", "enterprise ai",
    ]

    # A big SOX move is itself the event, even on a quiet news day.
    SOX_TRIGGER_PCT = 2.0

    def detect(self, news, snap=None) -> Activation | None:
        a = super().detect(news, snap)

        sox = None
        for q in (snap or {}).get("quotes_macro", []) or []:
            if "SOX" in (q.get("name") or ""):
                sox = q.get("pct_change")
                break
        if sox is None or abs(sox) < self.SOX_TRIGGER_PCT:
            return a

        pw = min(1.0, abs(sox) / 5.0)
        fact = f"SOX {sox:+.2f}% (price-triggered)"
        if a is None:
            return Activation(self.name, round(pw, 3), "price", [fact], ["<price move>"],
                              {"sox_pct": sox})
        a.weight = round(max(a.weight, pw), 3)
        a.kind = "both"
        a.evidence.insert(0, fact)
        a.detail["sox_pct"] = sox
        return a

    # ---- delegate to the existing module rather than duplicating it ------
    def analyse(self, snap=None) -> dict:
        m = _semis_regime()
        if not m:
            return {}
        try:
            causes = getattr(m, "CAUSES", {})
            return {"available_causes": list(causes),
                    "note": "delegated to semis_regime.py — 6 cause regimes, "
                            "each with its own 4-target read"}
        except Exception:
            return {}

    def decision_tree(self) -> list[str]:
        return ["Chips moved — WHY?",
                "  ├─ enterprise budget ROTATION → infra 🟢 / software+services 🔴",
                "  ├─ AI productivity deflation → services revenue 🔴 (demand intact)",
                "  ├─ demand ACCELERATING → infra 🟢, services lag",
                "  ├─ valuation correction → positioning, thesis unchanged",
                "  └─ true demand slowdown → broad 🔴 (rare; needs capex CUT)"]

    def transmission(self) -> list[str]:
        return ["AI budget rotation → GPU/networking spend ↑ → Nvidia/TSMC/Broadcom",
                "        → EMS (Dixon, Kaynes, CG Power) → data centres → POWER + TELECOM ↑",
                "        → enterprise software/consulting budget ↓ → Indian IT services 🔴"]

    def targets(self, snap=None) -> dict:
        m = _semis_regime()
        if not m:
            return {}
        try:
            return (snap or {}).get("semis_regime", {}).get("target_reads", {}) or {}
        except Exception:
            return {}
