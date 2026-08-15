# Signal Weight Agent

Detects signal **independence** (correlation, redundancy, families, effective-independent
bets) and proposes **ensemble weights** three ways — for any target instrument (the NIFTY
index, a constituent stock, or an index future). Deterministic core + optional LLM
narration. See **`SKILL.md`** for the full spec.

## Run (from the repo root)

```bash
python -m SignalWeightAgent.run --target NIFTY --horizon 60m
python -m SignalWeightAgent.run --target RELIANCE --window-days 60
python -m SignalWeightAgent.run --target NIFTY_FUT_1 --report    # also writes reports/*.md
python -m SignalWeightAgent.run --target NIFTY --json            # machine-readable
```

Needs `numpy` and the project DB (auto-resolved; prefers your live `option_chains.db`).
`--source auto` uses the feature store if backfilled, else falls back to live evaluation.

## What you get

- **Independence report** — pairwise correlation, redundancy per signal, effective-independent
  count, and families (correlated signals that should share a budget).
- **Skill** — information coefficient (IC) per signal vs the target's forward return.
- **Three weight vectors** — `inverse_redundancy`, `mv_ic` (Σ⁻¹·IC), `family` — side by side.

Everything is **PRIOR / descriptive** until ≥60 sessions of history; the report leads with
`n_obs` and says so.

## Layout

- `run.py` — CLI launcher + report writer
- `reports/` — generated `*.md` study reports
- Deterministic core: `strategy_framework/analysis/{signal_ensemble,signal_study}.py`
  (lives in the framework because `corr_matrix_full` is shared with `api.signal_correlation`)
- LLM narration wrapper: `.agents/signal-weight-analyst.md`
