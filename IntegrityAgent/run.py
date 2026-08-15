"""
IntegrityAgent/run.py
=====================
Run every invariant check and print a pass/fail report. Exit code 0 if all pass,
1 otherwise — so it doubles as a CI gate.

    python -m IntegrityAgent.run
    python -m IntegrityAgent.run --report      # also writes reports/*.md
    python -m IntegrityAgent.run --json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime

from IntegrityAgent import checks

_REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")


def _write_report(results, passed) -> str:
    os.makedirs(_REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = os.path.join(_REPORTS_DIR, f"integrity_{ts}.md")
    lines = [f"# Integrity Report — {datetime.now().isoformat(timespec='minutes')}",
             "", f"**{passed}/{len(results)} checks passed**", ""]
    for name, ok, detail in results:
        lines.append(f"- {'✅' if ok else '❌'} **{name}**" + (f" — {detail}" if not ok else ""))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser(description="Audit codebase single-source-of-truth invariants")
    ap.add_argument("--report", action="store_true", help="write a markdown report to reports/")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    results = checks.run_all()
    passed = sum(1 for _, ok, _ in results if ok)

    if args.json:
        print(json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in results], indent=2))
    else:
        print("=== IntegrityAgent — single-source-of-truth invariants ===")
        for name, ok, detail in results:
            tag = "PASS" if ok else "FAIL"
            print(f"  [{tag}] {name}" + (f"\n         → {detail}" if not ok else ""))
        print(f"\n{passed}/{len(results)} checks passed"
              + ("" if passed == len(results) else "  ⚠ INVARIANT VIOLATION"))
    if args.report:
        print(f"report written: {_write_report(results, passed)}")

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
