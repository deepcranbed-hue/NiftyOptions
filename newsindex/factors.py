#!/usr/bin/env python3
"""
factors.py — Layer 2: the canonical ECONOMIC FACTORS. The central abstraction.

The change this makes
---------------------
Everything used to start from RELATIONSHIPS ("Oil → ONGC", "SOX → IT"). That forces a
row per (driver, target) pair, so the same driver is re-declared for every stock it
touches, and one market series that means two things (SOX = chip supply cycle AND US
tech-spend proxy) gets bundled into one wrong relationship.

Factors invert it. A FACTOR is an economic primitive (Oil price, Semiconductor cycle,
Financial conditions…) with a stable ID. Relationships are no longer stored — they
EMERGE from (factor activation × company exposure). "Oil → ONGC/BPCL/HPCL/IOC" all
disappear into one factor (FACTOR_OIL_PRICE) plus four exposures.

The SOX / Kospi payoff
----------------------
A market series is an OBSERVABLE that measures one or more factors — it is not itself a
factor. OBSERVABLE_TO_FACTORS encodes that:

    SOX  → SEMI_CYCLE (+1)  AND  US_TECH_SPENDING (+1)
    KOSPI→ SEMI_CYCLE (+1)   only

Companies hold exposures to FACTORS, not to observables:

    DIXON (EMS)      → SEMI_CYCLE
    INFY  (IT svcs)  → US_TECH_SPENDING

So a SOX move hits BOTH Dixon and Infosys — through DIFFERENT factors, for different
reasons. A Kospi move hits Dixon (SEMI_CYCLE) but NOT Infosys, because Kospi is simply
not wired to US_TECH_SPENDING. The "Kospi→IT is incidental" special-case is gone: the
wiring itself refuses to make the link. No relationship, no exception.

Canonical IDs
-------------
Every component references these IDs, never free-form strings. One definition each.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Factor:
    """A self-describing economic primitive.

    STORES identity only: what activates it, what measures it, why it matters. It does
    NOT store which sectors/companies it affects — that is DERIVED from the exposure
    registry (see factor_card.affected_companies). Storing transmission on the factor AND
    exposure on the company would be two sources of truth for one fact — the very
    duplication the factors-first design removes. The factor answers "who am I / what
    turns me on"; the exposure registry answers "who is exposed to me"; the card joins them.
    """
    id: str
    label: str
    family: str                 # LEGACY short family (kept for existing callers)
    note: str = ""
    measured_by: tuple = ()      # observable series that MEASURE it (yfinance symbols)
    # ---- richer self-describing metadata (all optional, additive) ----
    category: str = ""           # Macro | Commodity | Technology | Financial | Policy | Geopolitics | Demand
    description: str = ""        # plain-English
    mechanism: str = ""          # WHY it matters ("raises input costs", "tightens conditions")
    triggers: tuple = ()         # events/entities that activate it (OPEC, Iran, Nvidia…)
    threshold: float = 0.0       # activation threshold on the primary indicator (% move)

    @property
    def cat(self) -> str:
        return self.category or self.family.title()


@dataclass
class Activation:
    """Today's measured STATE of a factor — the missing 6th object.

    A factor is not simply on/off. "Iran hit a tanker, oil +5%" is near-certain and
    persistent; "Trump MAY impose tariffs" is a low-probability, low-confidence rumor.
    Treating both as activation=1 lets a rumor move companies as hard as a fact. So an
    activation carries:
        strength      signed magnitude IF it plays out
        probability   0..1  how likely this is the state (a rumor is 0.3, a print is 0.95)
        confidence    0..1  how much we trust the read (source quality / corroboration)
        persistence   half-life in days — how long the effect should linger
    The resolver consumes EXPECTED impact = strength × probability, so uncertain
    narratives are discounted automatically without any special-case code.
    """
    factor_id: str
    strength: float
    probability: float = 1.0
    confidence: float = 0.5
    persistence_days: float = 5.0
    triggered_by: tuple = ()

    @property
    def expected(self) -> float:
        return self.strength * self.probability

    def to_dict(self) -> dict:
        return {"factor_id": self.factor_id, "strength": round(self.strength, 3),
                "probability": round(self.probability, 2), "confidence": round(self.confidence, 2),
                "persistence_days": self.persistence_days,
                "expected": round(self.expected, 3), "triggered_by": list(self.triggered_by)}


def expected_impacts(activations) -> dict:
    """{factor_id: Activation | float} → {factor_id: expected float}.
    A bare float is treated as certain (probability 1) — backward compatible with the
    old {factor: impact} inputs, so nothing downstream breaks."""
    out: dict[str, float] = {}
    for fid, a in (activations or {}).items():
        out[fid] = a.expected if isinstance(a, Activation) else float(a)
    return out


# ── THE FACTOR REGISTRY — economic primitives, one definition each ────────────
FACTORS: dict[str, Factor] = {
    "FACTOR_OIL_PRICE": Factor(
        "FACTOR_OIL_PRICE", "Oil price", "energy",
        "crude level/move; sets upstream realisations and user input cost", ("BZ=F", "CL=F"),
        category="Commodity",
        description="Change in crude affecting energy costs, inflation, and oil-company earnings.",
        mechanism="raises input costs for users, lifts realisations for producers",
        triggers=("OPEC", "Iran", "Hormuz", "US inventories", "OPEC+ quota"),
        threshold=2.0),
    "FACTOR_SEMI_CYCLE": Factor(
        "FACTOR_SEMI_CYCLE", "Semiconductor cycle", "tech",
        "global chip demand; DIRECT supply-chain read for electronics/EMS", ("^SOX", "^KS11"),
        category="Technology",
        description="Global semiconductor demand — the supply chain that feeds electronics/EMS.",
        mechanism="chip demand → electronics manufacturing volume",
        triggers=("Nvidia", "TSMC", "Samsung", "SK Hynix", "memory pricing"), threshold=2.0),
    "FACTOR_US_TECH_SPENDING": Factor(
        "FACTOR_US_TECH_SPENDING", "US enterprise tech spending", "tech",
        "US IT/cloud budgets; the DEMAND that drives Indian IT services (proxied, not causal)",
        ("^SOX", "^IXIC"), category="Technology",
        description="US enterprise IT/cloud budgets — the demand behind Indian IT services.",
        mechanism="client tech budgets → Indian IT-services revenue (a PROXY, not a cause)",
        triggers=("Accenture guidance", "IBM", "Microsoft", "cloud spend"), threshold=2.0),
    "FACTOR_AI_CAPEX": Factor(
        "FACTOR_AI_CAPEX", "AI infrastructure capex", "tech",
        "hyperscaler/data-centre buildout; lifts EMS, power, telecom",
        category="Technology",
        description="Spending on AI infrastructure — GPUs, networking, data centres, cloud.",
        mechanism="capex → EMS, power, electrical equipment demand",
        triggers=("Nvidia earnings", "OpenAI", "hyperscaler capex", "data centre")),
    "FACTOR_AI_SUBSTITUTION": Factor(
        "FACTOR_AI_SUBSTITUTION", "AI services substitution", "tech",
        "AI displacing billed IT-services hours; regime-gated headwind for IT"),
    "FACTOR_GLOBAL_RISK": Factor(
        "FACTOR_GLOBAL_RISK", "Global risk appetite", "flows",
        "risk-on/off; the COMMON factor behind incidental co-movements", ("^INDIAVIX", "^VIX"),
        category="Financial",
        description="Market-wide swing in investor risk appetite.",
        mechanism="risk-off → FII outflows, high-beta/small-cap pressure, rupee weakness",
        triggers=("war", "recession fear", "banking stress", "VIX spike")),
    "FACTOR_FINANCIAL_CONDITIONS": Factor(
        "FACTOR_FINANCIAL_CONDITIONS", "Financial conditions (rates)", "rates",
        "US/India yields → funding cost, FII flows, rupee", ("^TNX", "^FVX")),
    "FACTOR_USD": Factor("FACTOR_USD", "US dollar", "rates", "DXY; EM flow + commodity headwind", ("DX-Y.NYB",)),
    "FACTOR_RUPEE": Factor("FACTOR_RUPEE", "Rupee (USDINR)", "rates", "weaker rupee lifts exporters", ("INR=X",)),
    "FACTOR_INDIA_INFLATION": Factor("FACTOR_INDIA_INFLATION", "India inflation / RBI", "policy",
                                     "CPI → RBI stance → rate-sensitives"),
    "FACTOR_CREDIT_GROWTH": Factor("FACTOR_CREDIT_GROWTH", "Bank credit growth", "policy",
                                   "RBI fortnightly credit/deposit; bank volume"),
    "FACTOR_FII_FLOW": Factor("FACTOR_FII_FLOW", "FII net flow", "flows", "foreign flow into large-caps"),
    "FACTOR_CHINA_CONSTRUCTION": Factor("FACTOR_CHINA_CONSTRUCTION", "China construction/PMI", "metals",
                                        "the real driver of Indian STEEL demand"),
    "FACTOR_IRON_ORE": Factor("FACTOR_IRON_ORE", "Iron ore / coking coal", "metals", "steel input cost"),
    "FACTOR_COPPER": Factor("FACTOR_COPPER", "Copper / base metals", "metals", "Dr Copper; base-metal producers", ("HG=F",)),
    "FACTOR_EV_THEME": Factor("FACTOR_EV_THEME", "EV adoption", "demand", "policy/PLI/demand for EV makers"),
    "FACTOR_GOVT_CAPEX": Factor("FACTOR_GOVT_CAPEX", "Government capex", "policy", "budget/PLI/order inflow"),
    "FACTOR_USFDA": Factor("FACTOR_USFDA", "USFDA action", "policy", "approvals/warning letters for pharma"),
    "FACTOR_TELECOM_TARIFF": Factor("FACTOR_TELECOM_TARIFF", "Telecom tariff/ARPU", "demand", ""),
    "FACTOR_WINDFALL_TAX": Factor("FACTOR_WINDFALL_TAX", "Windfall tax / oil policy", "policy",
                                  "modifies upstream realisation"),
    "FACTOR_MARKETING_MARGIN": Factor("FACTOR_MARKETING_MARGIN", "OMC marketing margin", "policy", ""),
    "FACTOR_CONSUMER_DEMAND": Factor("FACTOR_CONSUMER_DEMAND", "Consumer / rural demand", "demand", ""),
    "FACTOR_GEOPOLITICS": Factor("FACTOR_GEOPOLITICS", "Energy-corridor geopolitics", "energy",
                                 "Hormuz/OPEC risk; reaches equities via the oil factor"),
}


def factor(fid: str) -> Factor | None:
    return FACTORS.get(fid)


# ── OBSERVABLE → FACTORS: how a market series MEASURES factors ────────────────
# THE crucial table. A series can inform several factors; a factor can be read from
# several series. This is where SOX-means-two-things is expressed ONCE, and where
# Kospi is deliberately NOT wired to US tech spending.
#   observable_symbol -> {factor_id: sign}
OBSERVABLE_TO_FACTORS: dict[str, dict] = {
    "^SOX":  {"FACTOR_SEMI_CYCLE": +1, "FACTOR_US_TECH_SPENDING": +1},
    "^KS11": {"FACTOR_SEMI_CYCLE": +1},          # memory (Samsung/SK Hynix) — NOT tech-spend
    "^IXIC": {"FACTOR_US_TECH_SPENDING": +1, "FACTOR_GLOBAL_RISK": +1},
    "BZ=F":  {"FACTOR_OIL_PRICE": +1},
    "CL=F":  {"FACTOR_OIL_PRICE": +1},
    "HG=F":  {"FACTOR_COPPER": +1},
    "^TNX":  {"FACTOR_FINANCIAL_CONDITIONS": +1},
    "^FVX":  {"FACTOR_FINANCIAL_CONDITIONS": +1},
    "DX-Y.NYB": {"FACTOR_USD": +1},
    "INR=X": {"FACTOR_RUPEE": +1},
    "^INDIAVIX": {"FACTOR_GLOBAL_RISK": +1},
}

# NARRATIVE (from the dispatcher) → factors it activates, with sign. Regime handled by
# the caller (Substitution flips US_TECH_SPENDING). Complements OBSERVABLE_TO_FACTORS:
# news activates factors that have no clean price series (geopolitics, USFDA, capex).
NARRATIVE_TO_FACTORS: dict[str, dict] = {
    "Oil": {"FACTOR_OIL_PRICE": +1},
    "Geopolitics": {"FACTOR_GEOPOLITICS": +1, "FACTOR_OIL_PRICE": +1},
    "Semiconductors / AI": {"FACTOR_AI_CAPEX": +1, "FACTOR_AI_SUBSTITUTION": +1,
                            "FACTOR_SEMI_CYCLE": +1, "FACTOR_US_TECH_SPENDING": +1},
    "Earnings": {},                              # company-level, not a factor
    "Treasury": {"FACTOR_FINANCIAL_CONDITIONS": +1},
    "RBI": {"FACTOR_INDIA_INFLATION": +1, "FACTOR_FINANCIAL_CONDITIONS": +1},
}


def activate_from_observables(moves: dict) -> dict:
    """{observable_symbol: pct_move} → {factor_id: signed activation}.
    A SOX move lands on BOTH semi_cycle and us_tech_spending, in one call."""
    out: dict[str, float] = {}
    for sym, mv in (moves or {}).items():
        if mv is None:
            continue
        for fid, sign in OBSERVABLE_TO_FACTORS.get(sym, {}).items():
            out[fid] = out.get(fid, 0.0) + sign * mv
    return out


def activate_from_narratives(activations: dict, ai_regime: str = "Neutral") -> dict:
    """{narrative: strength} → {factor_id: signed activation}. Regime-aware: under
    Substitution a chip rally is capital leaving services, so US_TECH_SPENDING flips."""
    out: dict[str, float] = {}
    for nar, strength in (activations or {}).items():
        for fid, sign in NARRATIVE_TO_FACTORS.get(nar, {}).items():
            s = sign
            if fid == "FACTOR_US_TECH_SPENDING" and ai_regime == "Substitution":
                s = -1
            out[fid] = out.get(fid, 0.0) + s * strength
    return out


def validate() -> dict:
    """Every observable/narrative must reference a KNOWN factor id (no typos)."""
    errors = []
    for src, table in (("OBSERVABLE_TO_FACTORS", OBSERVABLE_TO_FACTORS),
                       ("NARRATIVE_TO_FACTORS", NARRATIVE_TO_FACTORS)):
        for k, m in table.items():
            for fid in m:
                if fid not in FACTORS:
                    errors.append(f"{src}[{k}] references unknown factor {fid}")
    return {"ok": not errors, "errors": errors, "n_factors": len(FACTORS)}


if __name__ == "__main__":
    v = validate()
    print(f"{v['n_factors']} factors · {'✅ valid' if v['ok'] else '❌ '+str(v['errors'])}")
    print("\nSOX measures :", list(OBSERVABLE_TO_FACTORS["^SOX"]))
    print("KOSPI measures:", list(OBSERVABLE_TO_FACTORS["^KS11"]), " ← no US_TECH_SPENDING → can't reach IT")
