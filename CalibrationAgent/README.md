# Calibration Agent

Walk-forward validation of the signal-blend weights: propose on a rolling **train**
window, grade **out-of-sample** on held-out sessions, and report whether anything
genuinely beats the incumbent. **Advisory only — never writes `SignalWeights`.**
Full spec in **`SKILL.md`**.

## Run (from the repo root)

```bash
python -m CalibrationAgent.run                                 # 3 train → 1 test sessions
python -m CalibrationAgent.run --train-sessions 5 --test-sessions 2 --report
python -m CalibrationAgent.run --horizon-min 30 --sample-minutes 60
python -m CalibrationAgent.run --pnl                           # + real backtest P&L (slow)
python -m CalibrationAgent.run --json
```

## What you get

- **Per-fold out-of-sample IC** for each candidate (`incumbent`, `inverse_redundancy`,
  `mv_ic`, `family`) on sessions the proposal never saw.
- **Aggregate table** — mean IC, mean hit, mean P&L, and how many folds each candidate
  beat the incumbent.
- **A proposal or an explicit refusal.** If nothing beats the incumbent it says
  "keep current weights" — that's a valid result, not a failure.

## Layout

- `calibrate.py` — the walk-forward engine (gather → folds → fit → out-of-sample score)
- `run.py` — CLI + report/proposal writer
- `state/latest_proposal.json` — the advisory proposal (`"applied": false`)
- `reports/` — per-run evidence reports

## Before you trust it

Everything is flagged `PRIOR` until **≥60 sessions** (D-MA-04). A winner over 2–3 folds
is plumbing, not an edge. Apply a proposal only when it wins consistently across folds
*and* holds on more than one metric.
