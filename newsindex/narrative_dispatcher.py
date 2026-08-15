#!/usr/bin/env python3
"""
narrative_dispatcher.py — "which economic narratives are active today?"

The missing orchestration layer. Acquisition and normalization sit above it;
transmission and validation sit below and are untouched. This is the layer that was
absent, forcing every narrative module to sniff the news for itself.

It behaves like an OS dispatching interrupts, not like a decision tree:
    news → parse → ask EVERY registered narrative → activate any that fire

Two outputs
-----------
1. ACTIVATIONS — [Activation(narrative, weight, evidence, triggers_hit)], sorted.
2. RELATIONSHIPS — which narratives fired TOGETHER, and how strongly.

(2) matters and is easy to miss. The engine already models Oil × Geopolitics and
Oil × India-CPI as compounding interactions. If the dispatcher only emitted a flat
list, that co-activation would have to be rediscovered downstream. Emitting it here
means the interaction engine is handed the pairing instead of inferring it — and
newly-observed pairs surface as candidates for interaction terms we have not modelled.

Usage
-----
    import narrative_dispatcher as nd
    out = nd.dispatch(news, snap)
    out["activations"]    # [{narrative, weight, kind, evidence, ...}]
    out["relationships"]  # [{pair, strength, known_interaction, ...}]
    print(nd.to_markdown(out))
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from narratives.base import REGISTRY, Activation, NarrativeContext  # noqa: F401

# Pairs the engine ALREADY models as compounding. A co-activation on this list is a
# known interaction; anything else is a candidate we have not modelled yet.
KNOWN_INTERACTIONS = {
    frozenset({"Oil", "Geopolitics"}),
    frozenset({"Oil", "India CPI"}),
}

# Below this a narrative is background noise, not an active driver.
MIN_ACTIVATION = 0.15
# Both sides must be meaningfully active before a co-activation is worth reporting.
MIN_PAIR = 0.30


def load_plugins() -> int:
    """Import every module in narratives/ so its @register decorator runs."""
    pkg_dir = Path(__file__).resolve().parent / "narratives"
    n = 0
    for m in pkgutil.iter_modules([str(pkg_dir)]):
        if m.name.startswith("_") or m.name == "base":
            continue
        try:
            importlib.import_module(f"narratives.{m.name}")
            n += 1
        except Exception:
            continue          # a broken plugin must not take the pipeline down
    return n


def dispatch(news: list[dict], snap: dict | None = None,
             min_activation: float = MIN_ACTIVATION) -> dict:
    """Ask every registered narrative whether it is active. Multi-label by design."""
    load_plugins()
    acts: list[Activation] = []
    errors = []
    for cls in REGISTRY:
        try:
            a = cls().detect(news, snap)
            if a and a.weight >= min_activation:
                acts.append(a)
        except Exception as e:                      # one bad plugin ≠ a dead pipeline
            errors.append(f"{getattr(cls,'name',cls.__name__)}: {type(e).__name__}")
    acts.sort(key=lambda a: (-a.weight, getattr(a, "priority", 5), a.narrative))
    rels = _relationships(acts)
    contexts = _contexts(acts, rels)
    return {"activations": [a.to_dict() for a in acts],
            "contexts": [c.to_dict() for c in contexts],   # ← what downstream consumes
            "relationships": rels,
            "n_registered": len(REGISTRY),
            "errors": errors}


def _horizon_for(name: str) -> str:
    for cls in REGISTRY:
        if getattr(cls, "name", None) == name:
            return getattr(cls, "horizon", "days")
    return "days"


def _confidence(a: Activation) -> float:
    """
    How much do we TRUST this reading? Distinct from activation strength.

    Built from: corroboration (how many independent items), and whether the news and
    price channels AGREE. A narrative shouted by one low-quality blog can be highly
    'active' and barely trustworthy — collapsing the two would hide that.
    """
    n_ev = len(a.evidence or [])
    corroboration = min(1.0, n_ev / 3.0)          # 3+ items ≈ fully corroborated
    agreement = 1.0 if a.kind == "both" else 0.75 if a.kind == "price" else 0.6
    return round(min(1.0, 0.5 * corroboration + 0.5 * agreement), 3)


def _contexts(acts: list[Activation], rels: list[dict]) -> list[NarrativeContext]:
    """Assemble the downstream object. `interactions` can ONLY be filled here — a
    plugin has no visibility of which peers also fired."""
    partners: dict[str, list[str]] = {}
    for r in rels:
        a, b = r["pair"].split(" × ")
        partners.setdefault(a, []).append(b)
        partners.setdefault(b, []).append(a)
    out = []
    for a in acts:
        via = (["headline", "price"] if a.kind == "both"
               else ["price"] if a.kind == "price" else ["headline"])
        out.append(NarrativeContext(
            name=a.narrative, activation=a.weight, via=via,
            evidence=a.evidence[:4], interactions=partners.get(a.narrative, []),
            horizon=_horizon_for(a.narrative), confidence=_confidence(a),
            triggers_hit=a.triggers_hit[:8], metadata=a.detail))
    return out


def active(out: dict, name: str, threshold: float = MIN_ACTIVATION) -> bool:
    """Helper for downstream modules replacing their own keyword detection:

        if nd.active(mio["narratives"], "Oil"): build_oil_chain()
    """
    for c in (out or {}).get("contexts", []):
        if c["name"] == name:
            return c["activation"] >= threshold
    return False


def weight_of(out: dict, name: str) -> float:
    for c in (out or {}).get("contexts", []):
        if c["name"] == name:
            return c["activation"]
    return 0.0


def _relationships(acts: list[Activation]) -> list[dict]:
    """
    Co-activation pairs. Strength = geometric mean of the two weights — deliberately
    NOT the max, so a strong narrative cannot drag a barely-active one into looking
    like a real interaction.
    """
    out = []
    strong = [a for a in acts if a.weight >= MIN_PAIR]
    for i, a in enumerate(strong):
        for b in strong[i + 1:]:
            s = (a.weight * b.weight) ** 0.5
            known = frozenset({a.narrative, b.narrative}) in KNOWN_INTERACTIONS
            out.append({
                "pair": f"{a.narrative} × {b.narrative}",
                "strength": round(s, 3),
                "known_interaction": known,
                "note": ("modelled compounding interaction" if known else
                         "co-active today — NOT a modelled interaction; "
                         "candidate for one if it recurs"),
            })
    out.sort(key=lambda r: -r["strength"])
    return out


def to_markdown(out: dict) -> str:
    acts = out.get("activations", [])
    if not acts:
        return "_No narrative reached the activation threshold today._"
    L = ["### 🧭 Active narratives (dispatcher)\n",
         "_Multi-label: every narrative is asked independently, so several fire at once. "
         "Weight is a PRIOR heuristic (quality-weighted trigger hits, saturating), "
         "not a fitted probability — it ranks dominance, it is not a likelihood._\n",
         "| Narrative | Weight | Via | Evidence |", "|---|---:|---|---|"]
    for a in acts:
        ev = (a["evidence"][0][:70] + "…") if a.get("evidence") else "—"
        L.append(f"| **{a['narrative']}** | {a['weight']:.2f} | {a['kind']} | {ev} |")
    rels = out.get("relationships", [])
    if rels:
        L.append("\n**Co-activations** — narratives firing together\n")
        L.append("| Pair | Strength | Status |")
        L.append("|---|---:|---|")
        for r in rels[:6]:
            mark = "✅ modelled" if r["known_interaction"] else "🆕 unmodelled"
            L.append(f"| {r['pair']} | {r['strength']:.2f} | {mark} — {r['note']} |")
    if out.get("errors"):
        L.append(f"\n_⚠️ plugin errors: {', '.join(out['errors'])}_")
    return "\n".join(L)


if __name__ == "__main__":
    n = load_plugins()
    print(f"{n} plugin module(s), {len(REGISTRY)} narrative(s) registered:")
    for c in REGISTRY:
        print(f"  {c.name:22} priority={c.priority} triggers={len(c.triggers)}")
