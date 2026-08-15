# NewsAgent Evals — reasoning-quality evaluation layer

Grades a report the way a **senior analyst grades another analyst's note**: not "was the market
call right", but "is the *reasoning* economically valid, internally consistent, evidence-backed and
well-calibrated?"

**Read-only and fully additive.** The evals import nothing from the engine/overlay/agent runtime
except the model-agnostic LLM client (read-only). They consume a saved MIO JSON + the rendered
report. Running evals never modifies a single existing file, and applying feedback is a separate,
manual step — so the pipeline is never touched by its own grader.

```
Market Data → News Intelligence Agent → MIO (JSON) + Report
                                          │
                    ┌─────────────────────┼─────────────────────┐
              Deterministic checks   LLM persona reviews     (feedback)
              (offline, rule-based)  (Chief Economist, …)         │
                    └─────────────────────┼─────────────────────┘
                                   Evaluation Scorecard  →  advisory prompt/rule changes
```

## Levels

**Deterministic (offline, zero-cost, always run):**

| Level | What it checks |
|---|---|
| L4  Contradiction detection | market bias vs Nifty forecast; canonical economic sign errors (e.g. Fed cut → USD **up**) |
| L5  Coverage | NIFTY sector universe vs what the note actually covered |
| L6  Confidence calibration | reported confidence vs relationship hold-rate + driver agreement |
| L8  Probability calibration | turns the label into **Bull% / Bear%** from the driver vote |
| L11 Historical consistency | today's relationship states vs their calibrated hit-rates |
| L12 News quality | did BACKGROUND / non-market-moving items ("Groww launches copper trading") leak in? |
| L13 Evidence / hallucination | is every override/explanation backed by market, news, or stated inference? |
| L14 Report quality (language) | over-deterministic wording a probabilistic note should avoid |

**Judgement (need an LLM provider; skipped cleanly offline):**

L1 Economic reasoning · L2 Relationship regime-awareness · L3 Missing factors · L7 Explainability ·
**Persona reviews** — Chief Economist, Macro Trading Desk, Portfolio Manager/CIO.

## Usage

```bash
# 1) produce a MIO from the pipeline (existing runtime, unchanged)
python ../agents/run.py --out ../../mio.json --report        # or --mock offline

# 2) grade it (deterministic-only)
python run_eval.py --mio ../../mio.json --report ../reports/news_agent_XXXX.md --no-llm

# 3) with persona reviews — set a provider in agents/llm_config.json, then:
python run_eval.py --mio ../../mio.json --report ../reports/news_agent_XXXX.md --out scorecard.json
```

Deterministic mode needs no network and no LLM. Persona reviews activate automatically when
`agents/llm_config.json` has a non-deterministic `provider` (anthropic / openai / ollama).

## Output

Markdown grade sheet (per-level bars, contradictions, calibration, coverage gaps) + a scorecard
JSON (`schemas/eval_scorecard.schema.json`) with per-level scores, an overall grade
(`Institutional Research` … `Needs work`), and an **advisory feedback list** (feedback → prompt/rules).

## Test

```bash
python test_evals_offline.py     # self-contained; asserts each evaluator catches a planted flaw
```
