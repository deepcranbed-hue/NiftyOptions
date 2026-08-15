---
name: Calibration Agent — Walk-Forward Weight Validation
description: Closes the loop between measurement and weights. Proposes signal-blend weights on a rolling TRAIN window, validates them OUT-OF-SAMPLE on held-out sessions (blended-signal IC, hit rate, and optional real backtest P&L), and reports whether any candidate genuinely beats the incumbent. Advisory only — never writes SignalWeights.
---

# Calibration Agent — Walk-Forward Weight Validation

## What it is

Every threshold in this project ships tagged `PRIOR` because nothing has been
validated out-of-sample. This agent is what turns `PRIOR` into *calibrated*: it runs
a **walk-forward** loop that proposes weights on data it has seen and grades them on
data it hasn't.

Per fold:

```
TRAIN (N sessions)  → ensemble study → candidate weight vectors
                       (inverse_redundancy, mv_ic, family) + the INCUMBENT
TEST  (M sessions)  → score every candidate on held-out data:
                       blended-signal IC · hit rate · (opt-in) real backtest P&L
step forward, repeat → aggregate: which method generalises, how often it beats
                       the incumbent, and by how much
```

If nothing beats the incumbent, it says so and proposes nothing. That is a valid —
and common — outcome.

## Why it's trustworthy (fidelity rules)

1. **Same blend as production.** Out-of-sample scoring builds `net_score` with
   `strategy/blend.py` — the exact confidence-weighted formula `regime.classify`
   trades. A calibration that validated a re-implementation would be worthless.
2. **Real confidences.** The blend is confidence-weighted, and the feature store
   doesn't retain effective confidence for weight-0 signals (its `contribution` is
   `w·conf·score`, which is 0 when `w=0`). Since the whole point is testing whether
   to promote zero-weight signals, the agent evaluates the **live bundle** instead of
   the store.
3. **Genuinely held out.** Candidates are fitted only on TRAIN sessions and scored
   only on TEST sessions; folds roll forward so no test data leaks backwards.
4. **Roster from the registry.** Signals come from `signals/registry.py`, so a newly
   added signal is calibrated automatically.

## Hard rules (do NOT regress)

1. **Advisory only.** This agent NEVER writes `SignalWeights`. It emits a proposal +
   evidence to `state/latest_proposal.json` (`"applied": false`); a human applies it.
   Mirrors HARD RULE 11 (optimizer is advisory-first).
2. **No lookahead.** Fit on TRAIN only; score on TEST only; the blend reads as-of data.
3. **PRIOR until ≥60 sessions.** Below that bar the report is flagged as plumbing, not
   a calibrated edge (D-MA-04). A "winner" over 2 folds means nothing.
4. **Beat the incumbent or propose nothing.** A candidate is only proposed if it
   improves mean out-of-sample IC over the current weights.
5. **The blend stays shared.** `regime` and this agent must both use
   `strategy/blend.py`; the Integrity Agent enforces it.

## How to run

```
python -m CalibrationAgent.run                                   # default 3 train → 1 test
python -m CalibrationAgent.run --train-sessions 5 --test-sessions 2
python -m CalibrationAgent.run --horizon-min 30 --sample-minutes 60
python -m CalibrationAgent.run --pnl                             # + real backtest P&L (slow)
python -m CalibrationAgent.run --report                          # write reports/ + state/proposal
```

Output: per-fold out-of-sample IC per candidate, an aggregate table (mean IC / hit /
P&L / folds-beaten), and either a concrete weight proposal with its evidence or an
explicit "no proposal — keep current weights".

## Applying a proposal (by hand, deliberately)

`SignalWeights` derives defaults from the registry and accepts overrides, so applying
a validated proposal is one edit — and reversible:

```python
SignalWeights(overrides={"skew_rnd": 0.25, "vrp": 0.25, ...})
```

Only do this once the run shows ≥60 sessions, a consistent fold win-rate, and the
proposal survives on more than one metric.

## Relationship to the existing project

The Signal Weight Agent *measures* (independence, IC, candidate weights); this agent
*validates* those candidates out-of-sample and closes the loop back to
`config/settings.SignalWeights`. It reuses `signals/registry.py` (roster),
`analysis/signal_ensemble.py` (study + metrics), `strategy/blend.py` (production
blend) and `backtest/walkforward.py` (optional P&L) — it adds no duplicate math.
