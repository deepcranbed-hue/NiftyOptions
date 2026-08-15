# News Intelligence Agent — Market Knowledge Graph Builder

> **Status: IMPLEMENTED and running.** This folder now contains a live pipeline —
> ~28 overlay modules, 13 agents, a deterministic engine, evals and an MCP server.
> The documents below (ARCHITECTURE / AGENTS / KNOWLEDGE_GRAPH …) remain the **design
> blueprint**: they describe the system as *intended*.
>
> 👉 **For what is actually built and running, read
> [ARCHITECTURE_ASBUILT.md](ARCHITECTURE_ASBUILT.md)** — real module map, the real 21-step
> pipeline order, the shared-module (DRY) boundary, and an honest list of known gaps.
> Where the blueprint and the as-built doc disagree, the as-built doc is correct.
>
> **Reasoning substrate:** deterministic by default. Every committed number comes from the
> numerical core; the LLM path exists but is off unless configured, and may only *propose*
> a number as a tagged hypothesis.

## What this is

This folder specifies an **institutional-grade market intelligence engine** — the way a
Bloomberg, BlackRock Aladdin, Bridgewater, Citadel, or JPMorgan desk would frame it.

The defining reframe: the News Intelligence Agent is **not a news reader**. It is a
**Market Knowledge Graph Builder**. News is one input among many. Its job is to:

> Transform every market event into a dynamic **causal knowledge graph**, continuously
> **validate those relationships against live market behaviour**, detect when traditional
> relationships are being **overridden by stronger drivers or changing regimes**, and provide
> **explainable, quantified** market intelligence to every downstream analytical and
> trading agent.

That is the line between a *news aggregation system* and an *institutional market
intelligence engine*. The engine never stops at "what happened?" — it asks **why** it
happened, **how** it propagates, **which** relationships are active today, and **which**
factors dominate price discovery.

## The distinction, in one table

| Conventional news processor | Market Knowledge Graph Builder |
|---|---|
| Stores articles | Stores **relationships** (causal edges) |
| Sentiment score (bull/bear) | Directional call **with confidence, horizon, and driver attribution** |
| "Oil +5%" | "Oil +5% **because supply shock** → inflation → yields → banks → Nifty" |
| Assumes textbook relationships hold | **Validates** relationships vs the tape, flags **overrides** |
| One time horizon, blurred | Immediate / short / medium / structural, **separated** |
| One driver | **Driver-dominance decomposition** (what actually moved the market) |
| Free-text output | **Standardized Market Intelligence Object (MIO)** every downstream agent consumes |

## Reading order

1. **[SKILL.md](SKILL.md)** — the framework at a glance: mission, data-flow diagram,
   agent roles, hard rules, validation. Start here.
2. **[ARCHITECTURE.md](ARCHITECTURE.md)** — the full multi-agent topology, orchestration,
   the ingest→graph→intelligence pipeline, and the message bus.
3. **[AGENTS.md](AGENTS.md)** — every sub-agent specified: mission, tools, inputs/outputs,
   prompt contract, and guardrails.
4. **[KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md)** — the graph ontology: node types, edge
   types, properties, override modeling, regime layering.
5. **[MARKET_INTELLIGENCE_OBJECT.md](MARKET_INTELLIGENCE_OBJECT.md)** — the MIO: the single
   standardized object every downstream agent receives.
6. **[AGENT_INTERACTION.md](AGENT_INTERACTION.md)** — contracts with the Macro, Sector,
   Company, Risk, Strategy, Portfolio, and Execution agents.
7. **[DATA_SOURCES.md](DATA_SOURCES.md)** — the source taxonomy and source-reliability
   weighting.
8. **[schemas/](schemas/)** — machine-readable JSON Schemas for the MIO, events, and graph.

## Inherited invariants (from the NiftyOptions Strategy Desk Framework)

These are non-negotiable and carried into every agent below:

1. **No lookahead.** Every inference reads only data stamped `ts ≤ decision_time`.
2. **PRIOR until calibrated.** Every coefficient / transmission weight / threshold is a
   judgement prior tagged `PRIOR` until ≥ 60 sessions of history validate it. Reliability
   numbers below that bar are **descriptive only**, never presented as validated edge.
3. **Explainability is mandatory.** No conclusion ships without its causal chain and its
   confidence decomposition. "Bullish" is never allowed; "Bullish, 82%, because…" is.
4. **Validate against the tape.** Textbook relationships are hypotheses. When the observed
   move contradicts the expected one, the engine records an **override with a reason**, it
   does not say "rule failed."
5. **Separate the horizons.** Immediate / short / medium / structural impacts are never
   mixed in a single number.

## Scope note

This is a **blueprint**. It defines *what each agent is, what it consumes, what it emits,
and how they compose* — not the code. Implementation would live alongside `market_scan.py`
in the parent `newsindex/` project; the deterministic numerical primitives that already
exist there (causal engine, transmission map, cause→effect scorecard, regime detection,
driver dominance) are the natural **Deterministic Core** these LLM agents call into.
