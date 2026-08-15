#!/usr/bin/env python3
"""
narratives/base.py — the Narrative contract + plugin registry.

The gap this fills
------------------
Everything below the narrative layer was already mature (transmission, validation,
per-narrative reasoning like semis_regime). What was missing was the layer ABOVE:
nothing answered "which economic narratives are active today?". Instead six
independent keyword checks were scattered across modules — is_it_ai_headline(),
is_policy_headline(), india_cpi_hot(), us_cpi_cool(), detect_themes(),
classify_company_news() — each sniffing the news for itself with no shared contract.

A narrative is now a PLUGIN. It registers itself, declares its triggers, and answers
questions. Adding Quantum Computing or Defence means adding one file, not editing a
growing if/else in the orchestrator.

MULTI-LABEL BY DESIGN
---------------------
This is deliberately NOT a decision tree. A tree routes an event down one path, but
real events are multi-narrative: "Iran strikes tanker in Hormuz" is Geopolitics AND
Oil AND Shipping AND Inflation simultaneously. Single-path routing would destroy the
interaction terms the engine depends on (Oil × Geopolitics, Oil × India-CPI), which
exist precisely BECAUSE two narratives fired together and compounded. So every
narrative is asked independently and any number may activate.

Weights are PRIOR
-----------------
Activation weight is a saturating function of trigger hits × source quality. It is a
judgement heuristic, not a fitted probability, and is tagged as such. It ranks which
narratives dominate today; it is not a claim about likelihood.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- registry
REGISTRY: list[type["Narrative"]] = []


def register(cls):
    """Class decorator — a narrative that imports is a narrative that participates."""
    if cls not in REGISTRY:
        REGISTRY.append(cls)
    return cls


# ------------------------------------------------------------- activation
@dataclass
class Activation:
    """One narrative's answer to 'are you active today, and how strongly?'"""
    narrative: str
    weight: float                       # 0..1, PRIOR heuristic
    kind: str = "news"                  # news | price | both
    evidence: list[str] = field(default_factory=list)
    triggers_hit: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"narrative": self.narrative, "weight": round(self.weight, 3),
                "kind": self.kind, "evidence": self.evidence[:4],
                "triggers_hit": self.triggers_hit[:8], "detail": self.detail}


@dataclass
class NarrativeContext:
    """
    The object EVERY downstream stage consumes — chains, interactions, sector scores,
    validation. One shape, so a module never has to know which narrative it came from.

    Why this is separate from Activation:
      * a plugin returns an Activation — what IT can determine about itself
      * the dispatcher returns a NarrativeContext — Activation PLUS cross-narrative
        facts a single plugin cannot know, above all `interactions` (which other
        narratives fired today). A plugin has no visibility of its peers.

    activation vs confidence are deliberately different questions:
      activation — HOW STRONGLY is this narrative present today?  (weight)
      confidence — HOW MUCH DO WE TRUST that reading?             (evidence quality)
    A single blog post shouting about oil can produce high activation and low
    confidence. Collapsing them would hide exactly that case.
    """
    name: str
    activation: float
    via: list[str] = field(default_factory=list)        # ["headline","price"]
    evidence: list[str] = field(default_factory=list)
    interactions: list[str] = field(default_factory=list)   # dispatcher-filled
    horizon: str = "days"
    confidence: float = 0.5
    triggers_hit: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "activation": round(self.activation, 3),
                "via": self.via, "evidence": self.evidence[:4],
                "interactions": self.interactions, "horizon": self.horizon,
                "confidence": round(self.confidence, 3),
                "triggers_hit": self.triggers_hit[:8], "metadata": self.metadata}

    def is_active(self, threshold: float = 0.15) -> bool:
        return self.activation >= threshold


@dataclass
class NarrativeSignal:
    """
    The standard unit every narrative emits and every sector analyzer consumes.

    THE RULE THIS ENFORCES: a narrative emits SIGNALS, never a sector score.

    Why that matters. Banks are touched today by Oil, Treasury, RBI and Earnings. If
    the Earnings plugin returns `overall = -0.22` and Treasury returns `-0.30`, those
    two numbers already contain overlapping rate reasoning — combining them
    double-counts one story told twice. It is the same correlated-evidence error the
    fundamentals block was built to avoid, one level up.

    So plugins answer only "what did I observe, how strongly, how sure am I" and a
    single aggregator (sector_analyzer) decides what it means for the sector. One place
    owns cross-narrative weighting; nothing double-counts by construction.

    dimension groups signals that should NOT be summed independently — Fundamentals,
    Valuation, Guidance, Management, Peer, Policy, FinancialConditions, Flows.
    """
    narrative: str                       # "Earnings" | "Oil" | "Treasury" | "RBI" …
    dimension: str                       # Fundamentals | Guidance | Valuation | Policy …
    direction: str                       # "Positive" | "Negative" | "Mixed" | "Neutral"
    sector: str = ""                     # "Banks" — blank = market-wide
    company: str = ""                    # "HDFC Bank" — blank = sector-wide
    metric: str = ""                     # "NIM", "Loan Growth", "GNPA" …
    strength: float = 0.0                # 0..1 magnitude of the observation
    confidence: float = 0.5              # 0..1 how much we trust the reading
    horizon: str = "quarter"             # intraday | days | quarter | 1-2 quarters | years
    evidence: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    @property
    def signed(self) -> float:
        """strength as a signed number. Mixed/Neutral contribute 0 magnitude but are
        still emitted, because 'we looked and it was ambiguous' is information."""
        s = {"positive": 1.0, "negative": -1.0}.get(self.direction.lower(), 0.0)
        return s * self.strength

    def to_dict(self) -> dict:
        return {"narrative": self.narrative, "dimension": self.dimension,
                "direction": self.direction, "sector": self.sector,
                "company": self.company, "metric": self.metric,
                "strength": round(self.strength, 3),
                "confidence": round(self.confidence, 3),
                "horizon": self.horizon, "signed": round(self.signed, 3),
                "evidence": self.evidence[:3], "detail": self.detail}


# High-quality sources count for more than an aggregator repost. Mirrors the weighting
# reason_discovery already uses, so the two agree about what "good evidence" means.
_HIGH_SRC = ("reuters", "bloomberg", "economic times", "et markets", "moneycontrol",
             "business standard", "livemint", "mint", "cnbc", "businessline",
             "financial express", "the hindu")


def source_quality(item: dict) -> float:
    s = (str(item.get("source", "")) + " " + str(item.get("link", ""))).lower()
    return 0.9 if any(h in s for h in _HIGH_SRC) else 0.5


def _text(item: dict) -> str:
    parts = []
    for k in ("title", "tags", "summary", "body", "fulltext"):
        v = item.get(k, "")
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            parts.append(str(v.get("text") or v.get("summary") or ""))
    return " ".join(parts).lower()


def _kw_hit(text: str, kw: str) -> bool:
    """Word-boundary for single tokens ('ev' must not fire inside 'revealed');
    substring for phrases."""
    kw = kw.strip().lower()
    if not kw:
        return False
    if " " in kw:
        return kw in text
    return re.search(rf"\b{re.escape(kw)}\b", text) is not None


# ------------------------------------------------------------- the contract
class Narrative:
    """
    Subclass, set `name` + `triggers`, and @register it.

    Only detect() has a working default (keyword scan). The rest are hooks a narrative
    fills in as it matures — a new narrative can activate and report evidence on day one
    and grow its analysis later, rather than being all-or-nothing.
    """

    name: str = "?"
    priority: int = 5                   # tie-break when weights are equal (lower = first)
    triggers: list[str] = []
    # hits needed for the weight to saturate near 1.0 — a narrative mentioned once is
    # not as active as one mentioned eight times.
    saturation: float = 4.0
    # Default time-horizon of this narrative's effect. Carried into NarrativeContext so
    # downstream stages never mix a quarters-long thesis with an intraday move.
    horizon: str = "days"

    # ---- Level 1: am I active today? -------------------------------------
    def detect(self, news: list[dict], snap: dict | None = None) -> Activation | None:
        """Default: quality-weighted keyword scan. Override to add price triggers."""
        hits, evidence, fired = 0.0, [], set()
        for n in news or []:
            t = _text(n)
            matched = [k for k in self.triggers if _kw_hit(t, k)]
            if not matched:
                continue
            hits += source_quality(n)
            fired.update(matched)
            title = (n.get("title") or "").strip()
            if title and len(evidence) < 6:
                evidence.append(title[:140])
        if hits <= 0:
            return None
        # saturating: 1 - e^(-hits/k). Two strong hits ≈ 0.4, eight ≈ 0.86 — diminishing
        # returns, so a repeated wire story cannot manufacture certainty.
        w = 1.0 - math.exp(-hits / max(0.5, self.saturation))
        return Activation(self.name, round(w, 3), "news", evidence, sorted(fired))

    # ---- Level 2: what is the mechanism? ---------------------------------
    def analyse(self, snap: dict | None = None) -> dict:
        """Root cause / regime for this narrative. e.g. semis → 'budget rotation'."""
        return {}

    def decision_tree(self) -> list[str]:
        """The narrative's own branching logic, as displayable lines."""
        return []

    # ---- Level 3: how does it reach India? -------------------------------
    def transmission(self) -> list[str]:
        return []

    def targets(self, snap: dict | None = None) -> dict:
        """{bucket: {"lean": "🟢 Bullish", "india": [...], "global": [...]}}"""
        return {}

    # ---- Level 4: did the tape agree? ------------------------------------
    def validate(self, snap: dict | None = None, targets: dict | None = None) -> dict:
        return {}

    # ---------------------------------------------------------------------
    def __repr__(self):
        return f"<Narrative {self.name} triggers={len(self.triggers)}>"
