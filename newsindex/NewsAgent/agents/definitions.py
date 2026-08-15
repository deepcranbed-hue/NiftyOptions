"""
definitions.py — the 14 blueprint agents, instantiated.

Each agent binds the prompt contract + guardrails from NewsAgent/AGENTS.md to a bounded
set of Core tools, plus a deterministic `reduce` that shapes the tool outputs into the
agent's output contract (and serves as the no-LLM fallback).

The agents are grouped by pipeline layer (see NewsAgent/ARCHITECTURE.md). The Orchestrator
(orchestrator.py) is a deterministic controller, not an LLM agent, so it is defined there.
"""
from __future__ import annotations

from agent import Agent


# --- small helpers for the deterministic reducers ---------------------------
def _dir(x, band=0.10):
    if x is None:
        return "Neutral"
    return "Up" if x > band else "Down" if x < -band else "Neutral"


# ===========================================================================
# L1 — Ingestion & event formation
# ===========================================================================
Collector = Agent(
    name="Collector Agent",
    role="Poll every source as-of and stamp provenance; produce the market snapshot.",
    prompt_contract=(
        "Refresh the market snapshot. Report the as-of time and how many items each source "
        "family returned. Do not interpret significance — only structure and count."),
    guardrails="ts_event <= as_of. Never fabricate a timestamp. Preserve source coverage.",
    tool_names=["refresh_market_snapshot"],
    output_hint='{"as_of":..., "counts":..., "coverage_ok":true|false}',
    reduce=lambda t, c: {
        "as_of": t["refresh_market_snapshot"].get("as_of"),
        "counts": t["refresh_market_snapshot"].get("counts", {}),
        "coverage_ok": sum((t["refresh_market_snapshot"].get("counts") or {}).values()) > 0,
    },
)

EventDetection = Agent(
    name="Event Detection Agent",
    role="Decide which items can change market expectations; classify them.",
    prompt_contract=(
        "From themes, company catalysts and the observed tape, list the candidate events "
        "that can move expectations. Classify each into Economic/Corporate/Policy/"
        "Geopolitical/Market. Tune for recall; drop pure noise."),
    guardrails="Judge market-moving capability, not just presence. No magnitude estimates here.",
    tool_names=["market_themes", "company_intelligence", "market_verdict"],
    output_hint='{"candidates":[{"label":...,"class":...,"why":...}]}',
    reduce=lambda t, c: {
        "candidates": (
            [{"label": th["name"], "class": "Market", "why": th.get("why", "")}
             for th in (t["market_themes"] or [])]
            + [{"label": f"{co['company']} — {co.get('kind','news')}",
                "class": "Corporate", "why": co.get("title", "")}
               for co in (t["company_intelligence"] or [])
               if co.get("kind") == "Catalyst"][:8]
        ),
        "tape": t["market_verdict"].get("verdict"),
    },
)

Normalization = Agent(
    name="Normalization Agent",
    role="Collapse many headlines describing one event into a single canonical event.",
    prompt_contract=(
        "Given the candidate events, merge duplicates that describe the same underlying "
        "event into one canonical event. Name the EVENT, not any outlet's headline."),
    guardrails="Canonical labels name the event. Preserve every member's provenance.",
    tool_names=["market_themes"],
    output_hint='{"canonical_events":[{"label":...,"members":n}]}',
    reduce=lambda t, c: {
        "canonical_events": [
            {"label": th["name"], "members": th.get("hits", 1)}
            for th in (t["market_themes"] or [])
        ] or [{"label": (c.get("event_detection", {}) or {}).get("tape", "Session"),
               "members": 1}],
    },
)

KnowledgeGraph = Agent(
    name="Knowledge Graph Agent",
    role="Upsert entities and directed causal edges from the canonical events.",
    prompt_contract=(
        "From the causal engine's chains and the transmission map, express the implied "
        "directed causal edges (source -> target, with a one-clause mechanism). Request "
        "edge weights/signs from the Core; do not invent weights."),
    guardrails="Edges are directed and carry a mechanism. No LLM-invented weights.",
    tool_names=["causal_engine", "transmission_map"],
    output_hint='{"edges":[{"chain":[...],"mechanism":...}],"node_count":n}',
    reduce=lambda t, c: {
        "edges": [{"chain": ch.split("→")[0:3] if "→" in ch else [ch],
                   "mechanism": ch} for ch in (t["causal_engine"].get("chains") or [])],
        "transmission_lines": len(t["transmission_map"] or []),
        "node_count": len(set(w for ch in (t["causal_engine"].get("chains") or [])
                              for w in ch.split("→"))),
    },
)


# ===========================================================================
# L2 — Graph & causal reasoning
# ===========================================================================
Transmission = Agent(
    name="Transmission Agent",
    role="Ask WHY; classify the shock type; score each propagation path.",
    prompt_contract=(
        "Determine why the move happened (classify the shock type from the tape). Enumerate "
        "the plausible transmission paths from the engine chains and the transmission map, "
        "and rank them by the Core's contribution scores. State each mechanism."),
    guardrails=("Shock type is evidence-based. Path scores come from the Core, not guessed. "
                "For OIL, always state the absolute Brent price and its level band: impact is "
                "level-scaled, so a +2% move at $100 is more negative than +2% at $70. Never "
                "report an oil % move without its price level."),
    tool_names=["causal_engine", "transmission_map", "shock_type", "detect_regime"],
    output_hint='{"shock_type":...,"paths":[{"chain":...,"mechanism":...}],"regime":...}',
    reduce=lambda t, c: {
        "shock_type": t["shock_type"] if isinstance(t["shock_type"], str) else "none",
        "regime": t["detect_regime"].get("ai_regime"),
        "paths": [{"chain": ch} for ch in (t["causal_engine"].get("chains") or [])][:8],
        "expected_move": t["causal_engine"].get("expected_move", {}),
        "oil_level": t["causal_engine"].get("oil_level"),   # price + level amplifier + band
    },
)

Validation = Agent(
    name="Validation Agent",
    role="Compare expected vs observed; record confirm / override + reason (never 'failed').",
    prompt_contract=(
        "For each cross-asset rule, compare the Core's expected direction with the observed "
        "tape. If they disagree, do NOT say the rule failed — identify the overriding driver "
        "(government pricing, positioning/profit-taking, a stronger concurrent driver, or a "
        "regime change) and state the reason with evidence."),
    guardrails="The word 'failed' is banned. Every override needs a reason.",
    tool_names=["validate_relationships"],
    output_hint='{"statuses":[{"rule":...,"status":...,"reason":...}]}',
    reduce=lambda t, c: {
        "statuses": [
            {"rule": r["name"], "driver": r["driver"], "status": r["status"],
             "weighted_agreement_pct": r["weighted_agreement_pct"],
             "reason": (f"observed tape disagreed with the {r['driver']} rule; likely a "
                        f"stronger concurrent driver, positioning, or policy override."
                        if r["status"] == "OVERRIDDEN" else "")}
            for r in (t["validate_relationships"] or [])
        ],
        "overrides": [r["name"] for r in (t["validate_relationships"] or [])
                      if r["status"] == "OVERRIDDEN"],
    },
)

Regime = Agent(
    name="Regime Agent",
    role="Detect which relationship regime is active so edges are conditioned correctly.",
    prompt_contract=(
        "From the AI regime, the observed tape tone, the oil regime, and any persistent "
        "overrides, state which regimes are active now and how they re-sign or re-weight "
        "edges. Flag any regime transition in progress."),
    guardrails="Regime is a conditioning layer; name the edges it re-signs. Transitions are PRIOR.",
    tool_names=["detect_regime", "validate_relationships"],
    output_hint='{"active":[...],"primary":...,"transition":...}',
    reduce=lambda t, c: {
        "active": [t["detect_regime"].get("ai_regime"),
                   t["detect_regime"].get("observed_tone")],
        "primary": t["detect_regime"].get("observed_tone"),
        "ai_regime": t["detect_regime"].get("ai_regime"),
        "ai_confidence": t["detect_regime"].get("ai_confidence"),
        "transition": ("possible — persistent overrides"
                       if sum(1 for r in (t["validate_relationships"] or [])
                              if r["status"] == "OVERRIDDEN") >= 2 else "none"),
    },
)


# ===========================================================================
# L3 — Impact & attribution
# ===========================================================================
Impact = Agent(
    name="Impact Engine",
    role="Estimate magnitude SEPARATELY by horizon; never blend.",
    prompt_contract=(
        "For each horizon (immediate/short/medium/structural) report the Core's expected "
        "direction and magnitude. Do not average across horizons. The engine is a session-"
        "horizon read: fill immediate/short; leave medium/structural neutral unless a "
        "fundamental overlay applies, and say so."),
    guardrails="Four distinct fields, never collapsed. Magnitudes come from the Core.",
    tool_names=["causal_engine"],
    output_hint='{"immediate":...,"short":...,"medium":...,"structural":...}',
    reduce=lambda t, c: (lambda nt: {
        "immediate": {"direction": _dir(nt), "magnitude": abs(nt), "unit": "pct_nifty"},
        "short": {"direction": _dir(nt), "magnitude": abs(nt), "unit": "pct_nifty"},
        "medium": {"direction": "Neutral", "magnitude": 0.0, "unit": "pct_nifty",
                   "note": "not modelled — session-horizon engine"},
        "structural": {"direction": "Neutral", "magnitude": 0.0, "unit": "pct_nifty",
                       "note": "requires fundamental/regime overlay"},
    })(t["causal_engine"].get("expected_move", {}).get("Nifty 50", {}).get("total", 0.0) or 0.0),
)

CrossAsset = Agent(
    name="Cross-Asset Propagation Agent",
    role="Walk the event through the financial system; return full chains.",
    prompt_contract=(
        "Propagate the impact through the cross-asset graph (transmission map + engine "
        "chains). For each hop, use the Core's sign/score under the active regime. Return "
        "full chains, not just endpoints; stop a chain when the score decays."),
    guardrails="Chains terminate on score decay, not arbitrarily. No hop sign invented.",
    tool_names=["transmission_map", "causal_engine"],
    output_hint='{"chains":[...]}',
    reduce=lambda t, c: {
        "chains": t["transmission_map"] or [],
        "engine_chains": [ch for ch in (t["causal_engine"].get("chains") or [])],
    },
)

Sector = Agent(
    name="Sector Intelligence Agent",
    role="Decompose to sub-sectors, each with its own transmission score.",
    prompt_contract=(
        "For every sector return the Core's net driver score, a direction, and the dominant "
        "contributing driver. Sub-sector granularity is required (upstream vs downstream can "
        "diverge)."),
    guardrails="'Energy' alone is rejected — use sub-sectors. Scores come from the Core.",
    tool_names=["sector_intelligence"],
    output_hint='{"sectors":[{"sector":...,"direction":...,"score":...}]}',
    reduce=lambda t, c: {
        "sectors": [
            {"sector": r["sector"], "direction": r["verdict"], "score": r["net"],
             "top_driver": (r["rows"][0][0] if r.get("rows") else None)}
            for r in (t["sector_intelligence"] or [])
        ],
    },
)

Company = Agent(
    name="Company Intelligence Agent",
    role="Map events to companies via an exposure vector; pull analogues.",
    prompt_contract=(
        "For each named company combine its exposure (sector, Nifty weight) with the event "
        "to produce a directional score. Note the catalyst-vs-news kind. Analogues must be "
        "real records, not recalled."),
    guardrails="Exposure numbers come from the Core. Nifty weight ties back to index impact.",
    tool_names=["company_intelligence", "standout_movers"],
    output_hint='{"companies":[{"company":...,"direction":...}],"movers":...}',
    reduce=lambda t, c: {
        "companies": [
            {"company": co["company"], "sector": co.get("sector"),
             "direction": {"pos": "Up", "neg": "Down"}.get(co.get("sentiment"), "Neutral"),
             "nifty_weight": co.get("nifty_wt"), "kind": co.get("kind")}
            for co in (t["company_intelligence"] or [])
        ],
        "movers": t["standout_movers"],
    },
)

Confidence = Agent(
    name="Confidence Agent",
    role="Replace bare verdicts with the confidence triple.",
    prompt_contract=(
        "Assess economic-rationale strength (stars) from conviction, request historical "
        "reliability from the Core (tag PRIOR if uncalibrated), and today's applicability "
        "from driver agreement. Never output a direction without the triple."),
    guardrails="Today's applicability is independent of standing reliability. <60 sessions = PRIOR.",
    tool_names=["causal_engine"],
    output_hint='{"econ_rationale_stars":...,"historical_reliability":...,"today_confidence":...}',
    reduce=lambda t, c: (lambda ce: {
        "econ_rationale_stars": {"High": 5, "Moderate": 3, "Low": 2}.get(ce.get("conviction"), 3),
        "historical_reliability": ce.get("agreement", 0.0),
        "today_confidence": ce.get("agreement", 0.0),
        "reliability_tag": "PRIOR",
    })(t["causal_engine"]),
)

DriverDominance = Agent(
    name="Driver-Dominance Agent",
    role="Decompose the day's move across drivers (shares sum to ~1).",
    prompt_contract=(
        "Request the Core's driver-dominance decomposition. Sanity-check it against the "
        "events detected today. Name the dominant driver and explain, in one line, why it "
        "led price discovery."),
    guardrails="Shares sum to ~1.0. On a single-catalyst day the vector must concentrate.",
    tool_names=["driver_dominance"],
    output_hint='{"vector":{...},"dominant_driver":...,"dominant_driver_score":...}',
    reduce=lambda t, c: {
        "vector": t["driver_dominance"].get("vector", {}),
        "dominant_driver": t["driver_dominance"].get("dominant_driver"),
        "dominant_driver_score": t["driver_dominance"].get("dominant_driver_score"),
    },
)


# pipeline order (the Orchestrator runs them in this DAG order)
PIPELINE = [
    Collector,
    EventDetection,
    Normalization,
    KnowledgeGraph,
    Regime,          # regime before transmission so edges are conditioned
    Transmission,
    Validation,
    Impact,
    CrossAsset,
    Sector,
    Company,
    Confidence,
    DriverDominance,
]

BY_NAME = {a.name: a for a in PIPELINE}
