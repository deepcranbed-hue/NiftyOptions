# News Intelligence Agent — Multi-Agent Runtime

The **agent layer** from the blueprint, as a standalone, **model-agnostic** Python runtime.
Each of the 14 blueprint agents is a real agent here (mission, prompt contract, bounded
tools, guardrails). They reason over the **Deterministic Core** (the `market_scan.py` engine,
exposed through the same tools as the MCP server) and the Orchestrator composes their outputs
into a standardized **Market Intelligence Object (MIO)**.

> **Self-contained.** The engine is vendored at `../engine/` (a verbatim copy of
> `market_scan.py` + siblings + `events.db`), so NewsAgent runs with **no dependency on the
> parent `newsindex/` project and no `NEWSINDEX_HOME`**. `core.py` imports the vendored
> `market_engine`; set `NEWSINDEX_HOME` only if you want to track the live parent engine instead.

## Model-agnostic by config

The LLM backend is a config switch — `llm_config.json` (copy from `llm_config.example.json`).
Point every agent at a different model without touching agent code:

| provider | what it uses | needs |
|---|---|---|
| `deterministic` | **no LLM** — each agent runs its Core tools and shapes the output | nothing (offline, zero cost) |
| `ollama` | local Ollama `/api/chat` | Ollama running locally |
| `anthropic` | Claude Messages API | `model` + API key in env |
| `openai` | OpenAI **or any OpenAI-compatible endpoint** (vLLM, LM Studio, Together, Groq, OpenRouter…) | `model`, `base_url`, key |

`deterministic` is also the automatic **fallback**: if an LLM call fails or returns
unparseable output, that agent degrades to its deterministic path and the MIO is tagged
`degraded`. This is the "degrade to the deterministic core" invariant from the framework.

### How the LLM is used (and the hard rule)

Each agent **prefetches its Core tool results** (the numbers), hands them to the LLM, and asks
for the structured output. So:

* every **number** originates in the Core (`market_scan.py`) — never invented by the LLM;
* the LLM only **reasons, normalizes, and narrates** over those numbers;
* it works with **any** model, including ones without tool-calling (single-turn); tool-calling
  models may optionally request more tools.

## The 14 agents (→ tools → `market_scan.py`)

| Agent | Core tools it uses |
|---|---|
| Collector | `refresh_market_snapshot` |
| Event Detection | `market_themes`, `company_intelligence`, `market_verdict` |
| Normalization | `market_themes` |
| Knowledge Graph | `causal_engine`, `transmission_map` |
| Regime | `detect_regime`, `validate_relationships` |
| Transmission | `causal_engine`, `transmission_map`, `shock_type`, `detect_regime` |
| Validation | `validate_relationships` |
| Impact | `causal_engine` |
| Cross-Asset Propagation | `transmission_map`, `causal_engine` |
| Sector Intelligence | `sector_intelligence` |
| Company Intelligence | `company_intelligence`, `standout_movers` |
| Confidence | `causal_engine` |
| Driver-Dominance | `driver_dominance` |
| **Orchestrator** (deterministic controller, not an LLM) | runs the DAG, assembles + validates the MIO |

Pipeline order and hand-offs are in `definitions.PIPELINE`; the Orchestrator runs them, puts
each agent's output on a shared `intelligence_bundle`, then builds the MIO (numbers from the
Core) and enriches it with agent narrative (override reasons, shock type, regime transition).

## Files

| File | Role |
|---|---|
| `llm.py` | model-agnostic LLM client (deterministic / ollama / anthropic / openai) |
| `llm_config.example.json` | the backend config — copy to `llm_config.json` |
| `tools.py` | Core tool registry (same tools as the MCP server) |
| `agent.py` | Agent base + single-turn LLM reasoning + deterministic fallback |
| `definitions.py` | the 14 agents (prompt contracts, tools, deterministic reducers) |
| `orchestrator.py` | deterministic controller — runs the DAG, assembles the MIO |
| `reporter.py` | renders a markdown desk report — the **agent intelligence layer + the full `market_scan.py` report** — from one snapshot |
| `run.py` | CLI entrypoint |
| `test_agents_offline.py` | full pipeline offline (deterministic provider, mock snapshot) |
| `SAMPLE_REPORT.md` | example generated report (from the mock snapshot) |

## Run

```bash
# offline, no LLM, no network — proves the whole pipeline
NEWSINDEX_HOME=/Users/deepak/antigravity/NiftyOptions/newsindex \
  python NewsAgent/agents/test_agents_offline.py

# live snapshot, provider from llm_config.json
cp NewsAgent/agents/llm_config.example.json NewsAgent/agents/llm_config.json   # edit provider
NEWSINDEX_HOME=/Users/deepak/antigravity/NiftyOptions/newsindex \
  newsindex_env_3.10/bin/python NewsAgent/agents/run.py --trace --out mio.json
```

`run.py` prints the MIO to stdout and the agent trace to stderr. `--out` saves the MIO;
`--bundle` dumps every agent's output.

### Generate a desk report (like market_scan.py)

```bash
NEWSINDEX_HOME=/Users/deepak/antigravity/NiftyOptions/newsindex \
  newsindex_env_3.10/bin/python NewsAgent/agents/run.py --report
```

`--report` reuses `market_scan.py`'s own `build_report()` on the run's snapshot, so you get
the **exact** familiar Market Scan report, with an **Agent Intelligence Layer** prepended
(the MIO read, driver-dominance table, transmission paths, horizoned impact, validation
overrides with reasons, affected sectors/companies, and the per-agent execution trace). The
file is saved to the project's `reports/` dir as `news_agent_<stamp>.md`, alongside the
`market_scan_<stamp>.md` files. See `SAMPLE_REPORT.md` for an example.

### Switch to your own model

```bash
# local Ollama
{ "provider": "ollama", "model": "llama3.2:3b" }

# any OpenAI-compatible endpoint
{ "provider": "openai", "model": "your-model",
  "base_url": "https://your-endpoint/v1", "api_key_env": "LLM_API_KEY" }

# Claude
{ "provider": "anthropic", "model": "claude-sonnet-5", "api_key_env": "ANTHROPIC_API_KEY" }
```

## Verified

`test_agents_offline.py` asserts: all 13 pipeline agents run, the MIO validates against
`../schemas/mio.schema.json`, driver-dominance sums to ~1.0, and the Validation agent
surfaces its statuses. Expected tail: `ALL PASS`.

## Notes / next steps

* **Session-level MIO** (the engine is a daily aggregate). Per-event MIOs need a streaming
  news feed under the Event Detection / Normalization agents.
* `historical_reliability` is tagged **PRIOR** until `calibrate.py` / `build_events.py` output
  is wired in (mirrors the framework's ≥60-session rule).
* The runtime and the MCP server share one Core, so they never diverge: the MCP server is the
  *tool surface*, this runtime is the *agent surface*, both over `market_scan.py`.
