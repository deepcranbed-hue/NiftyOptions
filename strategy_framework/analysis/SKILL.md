---
name: Signal Weight Agent (moved)
description: The Signal Weight Agent now lives in its own folder — see SignalWeightAgent/SKILL.md at the repo root.
---

# Moved

The Signal Weight Agent packaging (SKILL, README, CLI, reports) moved to its own
folder at the repo root: **`SignalWeightAgent/`** — see `SignalWeightAgent/SKILL.md`.

Only the **deterministic core** remains here (`signal_ensemble.py`, `signal_study.py`),
because `corr_matrix_full` is the shared correlation primitive that
`api.signal_correlation` also imports. Run the agent with:

    python -m SignalWeightAgent.run --target NIFTY --horizon 60m
