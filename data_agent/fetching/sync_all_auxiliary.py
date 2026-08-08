#!/usr/bin/env python3
"""sync_all_auxiliary.py — the daily non-Breeze data pass.

Spawned in the background by backend/data_agent_routes.py once the Breeze sync
finishes. Runs its children SEQUENTIALLY on purpose: concurrent writers to one
SQLite file produce lock errors, not speed.

INTERPRETER
-----------
These scripts need yfinance, and the system Python's copy is old enough to raise a
TypeError on the download path we use — a failure that would land at 6am, in a
background process, with output going to DEVNULL. So the interpreter is resolved
explicitly to a breeze_env rather than inherited via sys.executable, and the choice
is printed. If no venv is found we fall back to sys.executable and say so loudly,
because a silent fallback is how this bites.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

# Order matters only in that the index/benchmark series should exist before
# anything that reads them. Everything here is idempotent and incremental.
SCRIPTS = [
    "sync_commodities.py",        # Upstox: GOLD, SILVER, COPPER, CRUDEOIL_MCX, USDINR, GIFTNIFTY
    "sync_crudeoil_yf.py",        # CRUDEOIL — WTI in USD. Was NOT in this list, so the
                                  # USD series went stale by a year while the INR one
                                  # updated daily under the same symbol.
    "sync_sectors_yf.py",         # NIFTY* sector indices + BANKNIFTY
    "sync_nifty50_bars_yf.py",    # the 50 constituents + NIFTY
    "sync_bank_bars_yf.py",
    "sync_it_bars_yf.py",
    "sync_finnifty_bars_yf.py",
]

# Preferred first. backend/main.py already invokes data_agent/breeze_env for the
# Breeze sync, so that is the house interpreter; scratch_scripts/breeze_env is the
# older one and stays as a fallback.
VENV_CANDIDATES = [
    os.path.join(REPO_ROOT, "data_agent", "breeze_env", "bin", "python"),
    os.path.join(REPO_ROOT, "scratch_scripts", "breeze_env", "bin", "python"),
]


def _interpreter():
    for cand in VENV_CANDIDATES:
        # A venv's python is a symlink; os.path.exists() follows it, so a venv whose
        # base interpreter has been removed correctly fails here rather than at run.
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand, True
    return sys.executable, False


def main():
    python, is_venv = _interpreter()
    print(f"interpreter: {python}")
    if not is_venv:
        print("WARNING: no breeze_env found — falling back to the launching interpreter.")
        print("         If its yfinance is old these syncs will fail with a TypeError.")

    failed = []
    for script in SCRIPTS:
        path = os.path.join(HERE, script)
        if not os.path.exists(path):
            print(f"skip {script} (not found)")
            continue
        print(f"\n=== {script} ===")
        res = subprocess.run([python, path], cwd=REPO_ROOT)
        if res.returncode != 0:
            failed.append(script)
            print(f"!! {script} exited {res.returncode}")

    print("\n" + "=" * 60)
    if failed:
        # Non-zero exit so the caller can see this went wrong even with output
        # discarded — the old version passed check=False and reported nothing.
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("All auxiliary syncs complete.")
    print("Verify with: python data_agent/quality/daily_bar_audit.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
