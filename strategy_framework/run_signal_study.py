"""
strategy_framework/run_signal_study.py  (DEPRECATED SHIM)
=========================================================
The Signal Weight Agent moved to its own folder: `SignalWeightAgent/`.
Use the canonical entry point instead:

    python -m SignalWeightAgent.run --target NIFTY --horizon 60m

This shim just delegates so any old command keeps working.
"""
from __future__ import annotations

from SignalWeightAgent.run import main

if __name__ == "__main__":
    main()
