#!/usr/bin/env python3
"""sync_all_auxiliary.py — DEPRECATED shim. Forwards to data_agent/sync_all.py.

This file used to carry its own list of seven scripts and its own interpreter
resolution. That made it the second place a new data source had to be registered,
and it is exactly why the two entry points drifted: a script added here was
invisible to /api/sync-all-data, and a script added there was invisible to this.

The list now lives in ONE place — `sync_all.build_steps()`. This shim exists only
so that `backend/data_agent_routes.py`, which spawns it in the background after the
Breeze sync, keeps working while it is repointed.

    Use instead:  python data_agent/sync_all.py

DO NOT ADD A SCRIPT HERE. Add a Step to sync_all.py.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SYNC_ALL = os.path.join(REPO_ROOT, "data_agent", "sync_all.py")

# The daily-bar subset this script was historically responsible for. Verification
# is left off because the caller runs this as one half of a larger sync; the full
# entry point audits at the end of the whole thing.
STEP_IDS = "sectors,nifty50,banks,it,finnifty,crude,commodities"


def main():
    print("NOTE: sync_all_auxiliary.py is a shim. The one sync is:")
    print("      python data_agent/sync_all.py\n")
    if not os.path.exists(SYNC_ALL):
        print(f"ERROR: {SYNC_ALL} not found.")
        return 1
    return subprocess.run(
        [sys.executable, SYNC_ALL, "--only", STEP_IDS, "--no-verify"],
        cwd=REPO_ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
