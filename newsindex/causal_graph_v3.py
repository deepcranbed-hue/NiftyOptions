#!/usr/bin/env python3
"""
causal_graph.py — Version 3: a CAUSAL graph, not a factor graph.

The one abstraction that changes everything
--------------------------------------------
factor_graph.py rooted the DAG at `Oil`. But a price is not a cause — it is a
MEASUREMENT that a deeper shock emits. Rooting at Oil silently asserts that
"oil ↓" has a fixed meaning. It doesn't: the SAME oil move is disinflationary
risk-ON under a supply-easing shock and growth-negative risk-OFF under a demand
shock. You can't fix that by flipping edges; you fix it by putting the shock
ABOVE the observable and letting the shock EMIT the observable.

    Evidence → Narrative → Economic Shock → Observable → Economic State
             → Market State → Sector → Company (via Exposure Themes)

Seven structural commitments (each from the review):

  1. Observables are evidence, not roots. Shocks emit them. (root inversion)
  2. Economic state (Inflation/Growth/Credit/FinancialConditions) is SEPARATE
     from market state (RiskAppetite/GrowthStyle/Value/Volatility). Macro data
     and market psychology move on different clocks.
  3. Structural graph MAY contain cycles (RiskAppetite→USD→FinConditions→
     RiskAppetite). The RUNTIME graph is those cycles broken + topo-sorted, so
     evaluation stays stable. Knowledge stays faithful; runtime stays acyclic.
  4. Nodes propagate a BELIEF (value AND confidence). Confidence decays with
     depth, so a 5-hop inference is never as certain as a direct observation.
  5. Edges are OBJECTS — mechanism / direction / base_weight / confidence /
     half_life / regime — not a single conflated number. Calibration gets a
     clean target.
  6. An EXPOSURE THEME sits between factor and company: SOX → Semiconductor
     Cycle → Electronics Manufacturing → Dixon. Explanations read causally.
  7. Every node emits a belief with SUPPORTING and CONTRADICTING evidence, not
     just a score — closer to how an analyst reasons.

Still test-bench: not wired into the live NewsAgent report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_DECAY = 0.97  # fallback per-hop confidence retention when an edge doesn't set one


# --------------------------------------------------------------------------- #
#  Typed objects
# --------------------------------------------------------------------------- #
@dataclass
class Edge:
    """A causal mechanism — decomposed, not a single conflated weight (#5)."""
    parent: str
    direction: int          # +1 / -1
    base_weight: float      # strength of transmission
    confidence: float       # 0..1 how reliable this mechanism is (calibration target)
    half_life: float        # days — how fast the effect fades (persistence)
    mechanism: str          # the economic story the edge encodes
    regime: str = "all"     # regime in which this weight applies (state-dependent hook)
    # per-edge confidence retention (your note: Oil→Inflation should NOT decay like
    # GrowthStyle→IT). A well-understood mechanism keeps more certainty per hop; a
    # noisy one bleeds it. This is the honest replacement for the global HOP_DECAY.
    decay: float = _DEFAULT_DECAY


@dataclass
class Signal:
    """An observable is expected vs observed; markets price the SURPRISE (the residual)."""
    expected: float
    observed: float
    conf: float

    @property
    def surprise(self) -> float:
        return self.observed - self.expected


@dataclass
class Belief:
    """A node is a hypothesis: a value, a confidence, and its evidence (#7)."""
    value: float
    conf: float
    support: list = field(default_factory=list)   # (parent, contribution, mechanism)
    contra: list = field(default_factory=list)
    missing: list = field(default_factory=list)   # parents that are SILENT (no signal) — #7 "missing evidence"

    def direction(self) -> str:
        return "↑" if self.value > 0.02 else "↓" if self.value < -0.02 else "→"


# --------------------------------------------------------------------------- #
#  Layer 0-1: Shocks EMIT observables (the inversion, #1)
# --------------------------------------------------------------------------- #
# A shock does not just move oil — it stamps a SIGNATURE across several
# observables. We OBSERVE oil directly (high confidence) and INFER the co-moving
# observables the shock implies (lower confidence — that lower seed confidence
# is what then decays through the chain). `m` = the observed oil move (signed).
SHOCKS = {
    "supply_easing": {
        "label": "Supply easing (ceasefire / OPEC+ hike / inventory build)",
        "keywords": ["ceasefire", "peace", "de-escalation", "opec+ hike", "output increase",
                     "inventory build", "sanctions lifted", "truce", "supply rises"],
        # oil ↓ AND the risk premium fades → VIX eases. Disinflationary, risk-supportive.
        "emits": lambda m: {"Oil": (m, 0.90), "VIX": (-abs(m) * 0.5, 0.55)},
        "note": "supply/risk-premium story — disinflationary, risk-ON",
    },
    "supply_shock": {
        "label": "Supply shock (Hormuz / war / sanctions / outage)",
        "keywords": ["hormuz", "blockade", "war", "strike", "attack", "sanction",
                     "outage", "supply cut", "opec+ cut", "embargo", "tanker"],
        # a disruption raises fear regardless of the oil sign printed today.
        "emits": lambda m: {"Oil": (m, 0.90), "VIX": (abs(m) * 0.6, 0.60)},
        "note": "supply-disruption story — inflationary AND risk-OFF",
    },
    "demand_shock": {
        "label": "Demand shock (China slowdown / recession fear)",
        "keywords": ["recession", "china slowdown", "china collapse", "demand destruction",
                     "weak demand", "global slowdown", "pmi contraction", "hard landing"],
        # oil ↓ BECAUSE demand is weak → China co-moves down, growth scare lifts VIX.
        "emits": lambda m: {"Oil": (m, 0.90), "China": (m, 0.70), "VIX": (abs(m) * 0.4, 0.55)},
        "note": "demand story — growth-negative, risk-OFF despite lower inflation",
    },
    "policy": {
        "label": "Policy / pricing (windfall tax, price cap, excise)",
        "keywords": ["windfall tax", "price cap", "excise", "fuel subsidy", "retail price freeze"],
        "emits": lambda m: {"Oil": (m, 0.85)},
        "note": "domestic redistribution — muted macro propagation",
    },
}
_DEFAULT_SHOCK = "supply_shock"


def classify_shock(news: list[dict], oil_move: float) -> dict:
    """Narrative → shock type → the observable signature it emits (evidence seed)."""
    blob = " ".join((n.get("title", "") + " " + n.get("tags", "")).lower()
                    for n in (news or []))
    hits = {k: sum(1 for kw in c["keywords"] if kw in blob) for k, c in SHOCKS.items()}
    shock = max(hits, key=hits.get) if any(hits.values()) else _DEFAULT_SHOCK
    cfg = SHOCKS[shock]
    return {"shock_type": shock, "label": cfg["label"], "note": cfg["note"],
            "emits": cfg["emits"](oil_move),
            "keyword_hits": {k: v for k, v in hits.items() if v}}


# --------------------------------------------------------------------------- #
#  Layers 2-4: economic state, market state, sectors (#2)
# --------------------------------------------------------------------------- #
OBSERVABLES = ["Oil", "SOX", "USD", "US10Y", "VIX", "China"]
ECON_STATE  = ["Inflation", "FinancialConditions", "Growth", "Credit"]
MARKET_STATE = ["RiskAppetite", "GrowthStyle", "Value", "Volatility"]
SECTORS = ["IT", "Banks", "Energy", "Metals", "Auto", "FMCG"]

NODE_LAYER = ({o: "observable" for o in OBSERVABLES} | {e: "econ" for e in ECON_STATE}
              | {m: "market" for m in MARKET_STATE} | {s: "sector" for s in SECTORS})

# Runtime edges (child -> parents). ACYCLIC by construction; the one feedback
# loop reality has is declared separately in STRUCTURAL_FEEDBACK and excluded here.
EDGES: dict[str, list[Edge]] = {
    # ---- economic state (from observables) --------------------------------
    "Inflation": [
        # oil→inflation is a well-understood pass-through → keep more certainty per hop
        Edge("Oil", +1, 0.6, 0.80, 5, "crude → energy CPI → headline inflation", decay=0.93),
        Edge("China", +1, 0.2, 0.60, 20, "China demand → global commodity prices"),
    ],
    "FinancialConditions": [   # higher = TIGHTER
        Edge("US10Y", +1, 0.5, 0.85, 30, "US yields → global funding cost"),
        Edge("USD", +1, 0.3, 0.70, 20, "stronger dollar → tighter EM conditions"),
        Edge("Inflation", +1, 0.4, 0.70, 30, "inflation → central banks stay restrictive"),
    ],
    "Growth": [
        Edge("China", +1, 0.7, 0.75, 40, "China is the marginal driver of global growth"),
        Edge("FinancialConditions", -1, 0.4, 0.70, 30, "tighter conditions → lower growth"),
    ],
    "Credit": [
        Edge("FinancialConditions", -1, 0.5, 0.60, 40, "tighter conditions → wider credit spreads"),
    ],
    # ---- market state (from econ + observables; separate clock, #2) --------
    "RiskAppetite": [          # higher = MORE risk-on
        Edge("FinancialConditions", -1, 0.6, 0.80, 10, "tighter conditions → less risk appetite"),
        Edge("VIX", -1, 0.5, 0.85, 3, "fear gauge → risk-off"),
        Edge("Growth", +1, 0.5, 0.70, 15, "growth outlook → risk-on (the demand-shock channel)"),
    ],
    "GrowthStyle": [
        Edge("RiskAppetite", +1, 0.6, 0.70, 10, "risk-on favours long-duration growth"),
        Edge("FinancialConditions", -1, 0.5, 0.70, 20, "lower discount rate lifts growth multiples"),
    ],
    "Value": [
        Edge("FinancialConditions", +1, 0.4, 0.60, 20, "higher rates favour value/financials"),
        Edge("RiskAppetite", -1, 0.3, 0.60, 10, "value leads when risk appetite fades"),
    ],
    "Volatility": [
        Edge("VIX", +1, 0.9, 0.90, 3, "VIX is the volatility read"),
        Edge("RiskAppetite", -1, 0.3, 0.60, 10, "risk-off raises realised vol"),
    ],
    # ---- sectors ----------------------------------------------------------
    "IT": [
        # style→sector is a noisy, sentiment-driven leg → bleeds certainty faster
        Edge("GrowthStyle", +1, 0.6, 0.70, 20, "IT is a long-duration growth style", decay=0.85),
        Edge("SOX", +1, 0.2, 0.60, 20, "global tech-spend read", decay=0.85),
    ],
    "Banks": [
        Edge("Value", +1, 0.5, 0.65, 20, "banks are the core value/rate block"),
        Edge("FinancialConditions", +1, 0.2, 0.60, 20, "steeper curve helps NIM early"),
    ],
    "Energy": [Edge("Oil", +1, 0.8, 0.85, 10, "upstream tracks crude directly")],
    "Metals": [
        Edge("Growth", +1, 0.5, 0.60, 30, "metals track the growth cycle"),
        Edge("China", +1, 0.3, 0.60, 30, "China industrial demand"),
    ],
    "Auto": [
        Edge("RiskAppetite", +1, 0.3, 0.60, 15, "discretionary demand"),
        Edge("Oil", -1, 0.3, 0.60, 15, "fuel/running cost"),
    ],
    "FMCG": [Edge("RiskAppetite", -1, 0.4, 0.60, 15, "staples bid when risk-off")],
}

# Reality has feedback loops. We DECLARE them (structural truth) but keep them
# OUT of the runtime graph so evaluation is a stable topological pass (#3).
STRUCTURAL_FEEDBACK = [
    Edge("USD", +1, 0.3, 0.55, 10,
         "risk-OFF → safe-haven USD bid → tighter conditions → more risk-off "
         "(RiskAppetite→USD→FinancialConditions→RiskAppetite loop)"),
]

_RUNTIME_ORDER = OBSERVABLES + ECON_STATE + MARKET_STATE + SECTORS


# --------------------------------------------------------------------------- #
#  Runtime integrity: prove the runtime graph is acyclic (#3)
# --------------------------------------------------------------------------- #
def assert_acyclic() -> dict:
    """Every runtime edge's parent must be positioned before its child."""
    pos = {n: i for i, n in enumerate(_RUNTIME_ORDER)}
    back = []
    for child, edges in EDGES.items():
        for e in edges:
            if e.parent in pos and pos[e.parent] >= pos[child]:
                back.append((e.parent, child))
    return {"acyclic": not back, "back_edges": back,
            "feedback_excluded_from_runtime": [f"{fb.parent}→? : {fb.mechanism}"
                                               for fb in STRUCTURAL_FEEDBACK]}


# --------------------------------------------------------------------------- #
#  Expectation → Surprise: the graph propagates the RESIDUAL, not the level
# --------------------------------------------------------------------------- #
# Markets price change-relative-to-expectation. A widely-anticipated oil fall is
# already in the tape and barely moves anything; the SAME fall when nobody saw it
# coming triggers a rotation. So the shock gives us the OBSERVED move, a consensus
# gives us the EXPECTED move, and only surprise = observed − expected enters the DAG.
def build_surprise_seed(emits: dict, expectations: dict | None = None) -> dict[str, Signal]:
    """emits: {obs: (observed, conf)}. expectations: {obs: expected}. → {obs: Signal}."""
    exp = expectations or {}
    return {o: Signal(expected=exp.get(o, 0.0), observed=obs, conf=c)
            for o, (obs, c) in emits.items()}


def propagate(seed: dict) -> dict[str, Belief]:
    """
    seed: {observable: Signal}  (or legacy {observable: (value, conf)} — treated as
    pure surprise with expected=0). The value that flows is Signal.surprise.

    Confidence is a |contribution|-weighted blend of (edge.confidence ×
    parent.confidence × edge.decay) — depth costs certainty, and each edge decays
    at its OWN rate. Every node also records SILENT parents (no signal) as missing
    evidence, so an analyst sees not just what fired but what stayed quiet.
    """
    def as_signal(x):
        return x if isinstance(x, Signal) else Signal(0.0, x[0], x[1])

    B: dict[str, Belief] = {}
    for node in _RUNTIME_ORDER:
        if node not in EDGES:                       # observable: seeded surprise
            if node in seed:
                s = as_signal(seed[node])
                B[node] = Belief(round(s.surprise, 3), round(s.conf, 3))
            else:
                B[node] = Belief(0.0, 0.0)
            continue
        value = 0.0
        wconf = wsum = 0.0
        recs, missing = [], []
        for e in EDGES[node]:
            p = B.get(e.parent) or as_signal(seed.get(e.parent, (0.0, 0.0)))
            pv = p.value if isinstance(p, Belief) else p.surprise
            pc = p.conf
            contrib = e.direction * e.base_weight * pv
            if abs(contrib) < 1e-6:
                missing.append((e.parent, e.mechanism))   # a channel that stayed silent
                continue
            value += contrib
            ec = e.confidence * pc * e.decay        # reliability × parent certainty × per-edge decay
            wconf += ec * abs(contrib)
            wsum += abs(contrib)
            recs.append((e.parent, round(contrib, 3), e.mechanism))
        conf = (wconf / wsum) if wsum else 0.0
        support = [r for r in recs if (r[1] > 0) == (value >= 0)]
        contra  = [r for r in recs if (r[1] > 0) != (value >= 0)]
        B[node] = Belief(round(value, 3), round(conf, 3), support, contra, missing)
    return B


# --------------------------------------------------------------------------- #
#  Layer 5: Exposure Themes between factor and company (#6)
# --------------------------------------------------------------------------- #
# A company is never wired to a raw factor. It inherits a THEME, and the theme
# inherits from the state/observable graph. SOX → Semiconductor Cycle →
# Electronics Manufacturing → Dixon reads as a causal sentence.
THEMES = {
    "EnterpriseITSpend": {"drivers": {"GrowthStyle": 0.5, "SOX": 0.3},
                          "reads": "US tech-spending → enterprise IT budgets"},
    "SemiconductorCycle": {"drivers": {"SOX": 0.7, "Growth": 0.3},
                           "reads": "chip cycle → electronics manufacturing"},
    "ChinaCyclical": {"drivers": {"Growth": 0.5, "China": 0.4},
                      "reads": "China demand → industrial cyclicals"},
    "EnergyUpstream": {"drivers": {"Oil": 0.8}, "reads": "crude → upstream realisations"},
}
COMPANY_THEMES = {
    "Infosys": {"EnterpriseITSpend": 1.0},
    "TCS": {"EnterpriseITSpend": 0.9},
    "Dixon": {"SemiconductorCycle": 1.0},
    "Tata Steel": {"ChinaCyclical": 1.0},
    "ONGC": {"EnergyUpstream": 1.0},
}


def theme_value(theme: str, B: dict[str, Belief]) -> Belief:
    drivers = THEMES[theme]["drivers"]
    v = sum(w * B.get(k, Belief(0, 0)).value for k, w in drivers.items())
    cw = sum(w * B.get(k, Belief(0, 0)).conf for k, w in drivers.items())
    tw = sum(drivers.values())
    return Belief(round(v, 3), round(cw / tw * _DEFAULT_DECAY, 3) if tw else 0.0)


def resolve_company(name: str, B: dict[str, Belief]) -> dict:
    out, val, cw, tw = [], 0.0, 0.0, 0.0
    for theme, w in COMPANY_THEMES.get(name, {}).items():
        tb = theme_value(theme, B)
        val += w * tb.value
        cw += w * tb.conf
        tw += w
        out.append({"theme": theme, "value": tb.value, "conf": tb.conf,
                    "reads": THEMES[theme]["reads"]})
    return {"company": name, "value": round(val, 3),
            "conf": round(cw / tw, 3) if tw else 0.0, "via": out}


# --------------------------------------------------------------------------- #
#  Demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    integ = assert_acyclic()
    print("=== runtime integrity ===")
    print(f"  acyclic: {integ['acyclic']}   back_edges: {integ['back_edges']}")
    print(f"  feedback kept structural-only: {integ['feedback_excluded_from_runtime']}\n")

    def run(title, news, oil, expectations=None):
        rc = classify_shock(news, oil)
        seed = build_surprise_seed(rc["emits"], expectations)
        B = propagate(seed)
        print(f"=== {title} ===")
        print(f"  shock : {rc['label']}")
        surprises = {o: round(s.surprise, 2) for o, s in seed.items()}
        print(f"  observed {{o:(exp,obs)}}: "
              f"{ {o: (s.expected, s.observed) for o, s in seed.items()} }")
        print(f"  SURPRISE entering graph: {surprises}")
        for n in ("Inflation", "Growth", "RiskAppetite", "GrowthStyle", "IT"):
            b = B[n]
            print(f"    {n:20} {b.value:+.2f}  conf {b.conf:.2f} {b.direction()}")
        rb = B["RiskAppetite"]
        print(f"  RiskAppetite belief: {rb.direction()} {rb.value:+.2f} @ conf {rb.conf:.2f}")
        print(f"     supports    : {[ (s[0], s[1]) for s in rb.support ]}")
        print(f"     contradicts : {[ (c[0], c[1]) for c in rb.contra ]}")
        print(f"     missing/silent: {[ m[0] for m in rb.missing ]}")
        inf = resolve_company("Infosys", B)
        print(f"  Infosys via themes: {inf['value']:+.2f} conf {inf['conf']:.2f} "
              f"({inf['via'][0]['reads']})\n")

    # --- the headline: SAME observed oil fall, opposite EXPECTATION regime ------
    print("################  MARKETS PRICE SURPRISE, NOT LEVEL  ################\n")
    run("Oil −0.9, but the fall was FULLY EXPECTED (priced in)",
        [{"title": "Iran ceasefire holds, oil slides as expected", "tags": "ceasefire"}], -0.9,
        expectations={"Oil": -0.9, "VIX": -0.45})     # consensus already saw it
    run("Oil −0.9, and it was a SURPRISE (nobody positioned)",
        [{"title": "Iran ceasefire holds, oil slides as supply fears ease", "tags": "ceasefire"}], -0.9,
        expectations=None)                            # expected 0 → full surprise

    # --- cause still matters: same SURPRISE, different shock --------------------
    print("################  AND CAUSE STILL FLIPS IT  ################\n")
    run("China recession → oil down (DEMAND SHOCK, unexpected)",
        [{"title": "Oil tumbles on China recession fears, demand destruction", "tags": "recession"}], -0.9)

    # --- confidence decay, now per-edge ----------------------------------------
    B = propagate(build_surprise_seed(
        classify_shock([{"title": "Iran ceasefire", "tags": "ceasefire"}], -0.9)["emits"]))
    print("=== confidence decays with depth — and each edge at its own rate ===")
    for n in ("Oil", "Inflation", "FinancialConditions", "RiskAppetite", "GrowthStyle", "IT"):
        print(f"    {n:20} conf {B[n].conf:.2f}")
