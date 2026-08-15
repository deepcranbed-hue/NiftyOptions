# ARCHITECTURE — AS BUILT

> **Companion to [ARCHITECTURE.md](ARCHITECTURE.md).** That document is the *design
> blueprint* — the topology as intended. This one describes **what is actually running**:
> real modules, real execution order, real boundaries. Where they disagree, this file wins.
>
> Written because `README.md` still opens with *"design only — no implementation code in
> this folder"*, which stopped being true some time ago. There are now ~28 overlay modules,
> 13 agents and a live pipeline.

---

## 1. The one-paragraph version

A **deterministic numerical core** (`engine/market_engine.py`) fetches prices, flows and
news, and computes every number. A **21-step enrichment pipeline** (`overlay/enrich.py`)
layers economic structure on top — causal chains, sector factor models, relationship
validation, evidence retrieval. **13 agents** (`agents/definitions.py`) wrap that into a
standard **Market Intelligence Object (MIO)**, and `agents/reporter.py` renders the MIO to
markdown. The LLM is optional and never produces a committed number.

```
sources → engine (numbers) → overlay (structure) → agents (MIO) → reporter (markdown)
                                    ↑
                       shared modules in ../  (newsindex root)
```

---

## 2. Directory map — what actually lives where

```
newsindex/                          ← SHARED HOME (importable by both engines)
├── market_scan.py                  standalone deterministic engine + report
├── reason_discovery.py             ★ canonical evidence-backed override discovery
├── textutil.py                     ★ canonical news-text helpers (news_text/sentences/dedupe)
├── audit.py                        ★ instrumented audit trail + backtest attribution
├── rbi_credit.py                   ★ RBI fortnightly credit/deposit growth
├── show_audit.py                   CLI: inspect an audit trail
│
└── NewsAgent/
    ├── engine/
    │   ├── market_engine.py        the deterministic core (fork of market_scan)
    │   ├── build_events.py         events.db — historical hit-rates for calibration
    │   └── fetch_article.py        full-text extraction (trafilatura→crawl4ai→playwright)
    ├── overlay/                    28 modules — the economic structure layer
    │   ├── enrich.py               ★ THE PIPELINE — 21 ordered steps (see §3)
    │   ├── common.py               shared helpers (text helpers re-exported from ../textutil)
    │   ├── reason_discovery.py     thin shim → ../reason_discovery.py
    │   ├── sector_factors.py       per-sector factor library (priors)
    │   ├── subsectors.py           sub-sector factor models (EV vs PV, etc.)
    │   ├── validation_states.py    ✅/⚠️/🔄/⏸️ relationship states
    │   ├── relationship_tiers.py   market / sector / idiosyncratic + decoupling
    │   ├── causal_graph.py         nodes + edges with activation
    │   ├── semis_regime.py         semiconductor cause analysis (6 regimes)
    │   ├── metals_*.py             metals complex via web
    │   └── …                       amplifiers, interactions, chains, policy, terminology
    ├── agents/
    │   ├── definitions.py          ★ the 13 agents
    │   ├── orchestrator.py         runs agents in order, collects the trace
    │   ├── reporter.py             ★ MIO → markdown report
    │   ├── llm.py                  optional LLM client (deterministic by default)
    │   └── run.py                  CLI entrypoint
    ├── mcp_server/                 exposes the MIO over MCP
    ├── evals/                      deterministic + LLM-graded quality scoring
    └── reports/                    generated output
```

★ = touched or created in the most recent refactor.

---

## 3. The pipeline — real execution order

`overlay/enrich.py` runs these **21 steps in this order**. Each is wrapped by `step()`, so
a failure degrades that step rather than the run.

| # | Step | What it adds |
|---:|---|---|
| 1 | `news_acquisition` | RSS + targeted search + full-text extraction |
| 2 | `chains` | explicit cause→effect chains per active driver |
| 3 | `amplifiers` | level multipliers (Brent band, USDINR band, VIX band) |
| 4 | `interactions` | Oil × Geopolitics, Oil × India-CPI |
| 5 | `dominance_adjusted` | driver dominance after weak-transmission caps |
| 6 | `sectors` | base sector reads |
| 7 | `terminology` | normalises naming across modules |
| 8 | `causal_graph` | nodes + edges + activation |
| 9 | `extract_fundamentals` | numbers parsed out of article bodies |
| 10 | `semis_regime` | why chips moved → 4 target buckets |
| 11 | `subsectors` | sub-sector factor models (EV vs PV …) |
| 12 | `calibration` | hit-rates from `events.db` |
| 13 | `macro_expectations` | Fed / RBI stance |
| 14 | `policy_catalysts` | government schemes + named beneficiaries |
| 15 | `sector_factor_library` | per-sector weighted factor scores |
| 16 | `metals_sentiment` | metals complex (web + news) |
| 17 | `extra_validations` | additional relationship checks |
| 18 | `validation_states` | ✅ / ⚠️ / 🔄 / ⏸️ per relationship |
| 19 | `reason_discovery` | **evidence-backed WHY for every break** |
| 20 | `relationship_tiers` | systematic / sector / idiosyncratic + decoupling |
| 21 | `macro_dashboard` | the regime table |

**Order matters at two points.** `reason_discovery` (19) must run after `validation_states`
(18) because it only explains relationships already marked broken. `relationship_tiers` (20)
must run after `reason_discovery` so decoupling can cite a discovered catalyst.

---

## 4. The 13 agents

Defined in `agents/definitions.py`, run in order by `orchestrator.py`. Each is a thin
wrapper that reads the MIO, calls deterministic tools, and writes back a typed section.

```
Collector → Event Detection → Normalization → Knowledge Graph → Regime →
Transmission → Validation → Impact Engine → Cross-Asset Propagation →
Sector Intelligence → Company Intelligence → Confidence → Driver-Dominance
```

Every one currently reports `deterministic` in the execution trace. The LLM path exists
(`agents/llm.py`) but is **off by default** and, per the design invariant, may only
*propose* a number as a tagged hypothesis — never commit one.

---

## 5. Boundaries that matter

### 5.1 Numbers vs narration
The deterministic core owns every committed number. The LLM may reorder or synthesise
*explanations* (`reason_discovery.llm_rerank` can re-rank candidates) but is explicitly
forbidden from inventing a reason not already in the retrieved candidate set.

### 5.2 Modelled vs observed
The single most common historical bug class in this codebase. A model score
(`sector_factor_library`) is **not** a realised return, and must never be described with
observed language ("led", "lagged"). `reporter._market_narrative()` now states the model
tilt and the observed tape separately and flags contradictions between them.

### 5.3 Structural vs today
Multi-quarter theses and one-session validation are rendered in **separate blocks** with a
reconciliation table (see the semis section). A single day cannot confirm or break a
structural thesis, and the report must not imply it can.

### 5.4 Evidence vs mechanism vs unknown
When a relationship breaks there are three distinct explanation sources, in precedence
order:
1. `override_discovered` — retrieved news evidence, with confidence + sources
2. `override` / `reason_econ` — the economic mechanism that *can* override
3. `override_search` — how hard we actually looked (searched / timed out / not searched)

These carry different confidence and are labelled distinctly. "We searched and found
nothing" and "we never searched" must never render identically.

---

## 6. Shared modules (the DRY boundary)

Four modules live in `newsindex/` rather than here, because **both** engines need them:

| Module | Why it is shared |
|---|---|
| `reason_discovery.py` | `market_scan.py` had its own hardcoded ±2 override heuristic; this is the evidence-based replacement |
| `textutil.py` | `news_text`/`sentences`/`dedupe` were trapped in `overlay/common.py` and unreachable outside it |
| `audit.py` | instrumented derivation trail, engine-agnostic |
| `rbi_credit.py` | banking credit/deposit growth, needed by both sector models |

`overlay/reason_discovery.py` is a **shim** re-exporting the shared module, so existing
`import reason_discovery` call sites keep working. `overlay/common.py` re-exports the text
helpers from `textutil`. Do not add logic to either shim.

---

## 7. Known gaps

Honest list — these are real and unfixed:

1. **Sub-sector scores do not roll up.** `subsector_factors` and `sector_factor_library`
   are independent models that can disagree (Auto bearish while its EV sub-sector is
   strongly bullish). §5 now *flags* the divergence but does not resolve it — a proper
   roll-up needs sub-sector market-cap weights we do not have.
2. **Most weights are PRIOR.** Factor weights and normalisation bands are judgement, not
   fitted. `audit.py`'s attribution loop is the mechanism to fix this, but needs ~30+
   sessions of saved trails per factor before its hit-rates mean anything.
3. **Only the sector factor model is instrumented.** The causal engine — which produces the
   *headline* verdict — has no audit trail.
4. **Catalyst detection is keyword-based.** `reason_discovery` finds a reason only if it
   matches one of ~11 catalyst groups. Block deals, index rebalances and promoter pledges
   are invisible.
5. **Live search is capped** at 5 relationships per run with a 12s timeout, so later breaks
   are systematically less explained than earlier ones.

---

## 8. Running it

```bash
cd NewsAgent/agents
python3 run.py                    # full pipeline, deterministic
python3 run.py --trace            # + per-agent execution trace
python3 run.py --out mio.json     # save the MIO

cd ../evals && python3 test_evals_offline.py     # offline suites
cd ../agents && python3 test_agents_offline.py

cd ../..                          # newsindex/
python3 show_audit.py --offline --sector Banks   # inspect an audit trail
python3 rbi_credit.py                            # RBI credit/deposit state
```

Environment:
- `NEWSINDEX_HOME` — project root, if `market_scan.py` isn't auto-found
- `NEWSAGENT_REASON_SEARCH=0` — disable live catalyst search
