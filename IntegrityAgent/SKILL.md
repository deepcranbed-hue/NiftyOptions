---
name: Integrity Agent — Single-Source-of-Truth Invariant Auditor
description: Cross-imports the whole NiftyOptions codebase and asserts its DRY / consolidation invariants hold — one signal registry, one correlation+metrics engine, one lot-size source, one index-volume helper — so a future edit can't silently re-introduce duplication or drift. Runs as a standalone agent AND inside pytest.
---

# Integrity Agent — Single-Source-of-Truth Invariant Auditor

## What it is

The Integrity Agent is the codebase's **self-defence layer**. It has access to every
module and its only job is to prove that the architectural invariants we consolidated
around still hold. Where a normal test checks one function's output, this agent checks
*structural* truths across the whole project: that there is exactly one signal roster,
one correlation/metrics engine, one lot-size constant, one index-volume reconstruction —
and that every consumer derives from those single sources rather than keeping a private
copy that can drift.

It runs two ways from the same check functions:
* as an **agent / CI gate** — `python -m IntegrityAgent.run` (exit 0 all-pass, 1 on any
  violation), optionally writing a markdown report;
* as **pytest cases** — each invariant is a parametrized test in
  `strategy_framework/tests/test_integrity.py`, so `pytest` fails the moment an
  invariant breaks.

## What it checks

| Invariant | What would break it |
|---|---|
| `SignalWeights` roster + weights derive from `signals/registry.py`, sum to 1.0 | adding a weight field out of sync with the registry; weights not summing to 1 |
| `regime._DIRECTIONAL` / `_MOMENTUM_FAMILY`, `api._DIR_SIGNAL_NAMES`, `walkforward._DIR_SIGNALS` all == registry | re-hardcoding a signal list somewhere |
| `bundle.py` iterates the registry (no hardcoded `bundle.add(...)` list) | re-adding manual signal wiring |
| `signal_ensemble.corr_matrix_full` is correct; `api.signal_correlation` uses it (no inline `corrcoef` loop) | re-introducing a duplicate correlation implementation |
| `signal_metrics` matches `np.corrcoef` and returns the full metric set; api effectiveness/scoreboard/horizon-curve import the engine metrics | metric math drifting between the agent and the UI |
| lot size == 65 from `exchange_config` in `settings` / `FrameworkConfig` / `RiskConfig`; no stray `65`/`75` literals in prod modules | hardcoding a lot size anywhere |
| index-volume reconstruction shared (`per_bar_index_volume`) across technical_momentum / vwap / rel_volume | a signal re-rolling its own constituent-volume loop |
| `signals/registry.validate()` passes (15 directional, blended weights sum 1.0, unique names) | a malformed registry row |

## Hard rules (do NOT regress)

1. **A failing check is a build failure.** `run.py` exits non-zero and the pytest bridge
   fails; treat a red invariant as blocking, not advisory.
2. **Checks are cheap and deterministic.** No live DB, no network — runtime import
   asserts + static source scans only, so they run in milliseconds on every commit.
3. **New single-source rule → new check.** When a new canonical home is introduced
   (per CLAUDE.md / SKILL.md HARD RULES 9/12/13), add a `@check` here so it's enforced,
   not just documented.
4. **A check that errors is a failure, not a crash.** `run_all()` catches exceptions so
   one broken import can't hide the rest.

## How to run

```
python -m IntegrityAgent.run                 # print pass/fail, exit 0/1 (CI gate)
python -m IntegrityAgent.run --report        # + write reports/*.md
python -m IntegrityAgent.run --json          # machine-readable
python -m pytest strategy_framework/tests/test_integrity.py -q   # same checks, via pytest
```

## Relationship to the existing project

This agent enforces the invariants the rest of the codebase *documents*: HARD RULE 9
(lot size in `exchange_config`), HARD RULE 12 (shared helpers — index volume, the
correlation/metrics engine), HARD RULE 13 (one signal registry), and the `CLAUDE.md`
DRY canonical-homes list. It is the executable counterpart to those rules — so the
"single source of truth" claim is checked by a machine, not trusted by convention.
Add a check whenever you add a rule.
