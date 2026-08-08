#!/usr/bin/env python3
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))

scripts = [
    "sync_commodities.py",
    "sync_sectors_yf.py",
    "sync_nifty50_bars_yf.py",
    "sync_bank_bars_yf.py",
    "sync_it_bars_yf.py",
    "sync_finnifty_bars_yf.py"
]

for script in scripts:
    path = os.path.join(HERE, script)
    if os.path.exists(path):
        print(f"Running {script}...")
        subprocess.run([sys.executable, path], check=False)
    else:
        print(f"Skipping {script}, not found.")
print("All auxiliary syncs complete.")
