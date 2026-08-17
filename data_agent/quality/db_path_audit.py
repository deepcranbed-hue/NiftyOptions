#!/usr/bin/env python3
"""
db_path_audit.py — enforce the CLAUDE.md rule that nothing hardcodes a database path.

WHY THIS EXISTS
---------------
CLAUDE.md has stated the rule since 2026-08-15, in bold, under a heading titled
"MANDATORY: single source of truth (DRY) — check before you write":

    Never hardcode a database path or DSN. Never sqlite3.connect() a literal.

It also states the reader/writer split, and carries a "Why a rule and not a preference"
paragraph listing the six places the Drive path had previously been pasted.

On 2026-08-17 — two days later — six new modules were added that hardcode the path anyway,
plus two new WRITERS, one of which put a month of single-stock futures into the repo-local
mirror instead of the source of truth and printed success (correction C37). Nobody noticed
until a human asked why the database was being written at all.

So the rule was documented, mandatory, and rationalised, and it was still broken six times
in one session. That is not a documentation problem. A rule whose only enforcement is that
the author remembers to comply is the same class of control as "remember to run the script"
— the thing freshness.py exists to replace. The difference between a rule and a preference
is whether something fails when you break it. This is that something.

READER VS WRITER, BECAUSE THE RULE IS NOT UNIFORM
-------------------------------------------------
CLAUDE.md: "A reader may fall back to the repo-local copy. A writer may NOT."

So a hardcoded path is not automatically a defect. `backend/quant/*` read the mirror, which
is what the mirror is for, and they merely inherit its staleness. A WRITER doing it
manufactures a silent divergence, which is why resolve_writable_db_path() raises instead of
falling back. This audit therefore classifies rather than counts:

    ERROR    a module that writes AND hardcodes a path        -> exit non-zero
    WARN     a module that reads AND hardcodes a path         -> reported, not fatal
    ok       resolves through db_config

Write intent is detected from the source: INSERT/UPDATE/DELETE/CREATE/DROP SQL, a commit()
call, or passing db= to a save_* helper. Detection is deliberately generous — a false ERROR
costs one `# db-audit: reader` annotation, while a false ok costs another C37.

    python3 data_agent/quality/db_path_audit.py
    python3 data_agent/quality/db_path_audit.py --strict   # warnings count too
"""
from __future__ import annotations

import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))

SCAN_DIRS = ("data_agent", "backend", "strategy_framework", "src_py")
SCAN_ROOT_FILES = True

SKIP_PARTS = ("breeze_env", "node_modules", "__pycache__", "scratch", "_to_delete",
              ".git", "site-packages", "venv")

# NARROWED TO THE FILE THE RULE PROTECTS. A first version matched any "*.db" literal and
# produced 24 findings, nearly all noise: tempfile.mktemp(suffix=".db") in tests, demo blocks
# writing bt.db/demo.db to the working directory. A checker that noisy gets ignored, which is
# the alarm-fatigue failure freshness.py is built to avoid — so it is a defect here too.
#
# The rule protects the MARKET DATA database specifically: option_chains.db on Drive, with a
# read-only mirror in the repo. A throwaway temp file is not that, and no amount of hardcoding
# a temp path can manufacture the Drive-vs-mirror divergence. So the pattern names the file.
PROTECTED = r"option_chains(?:_\w+)?\.db"
HARDCODED = re.compile(rf"""(?x)
    (?:os\.path\.join\([^)]*?["']{PROTECTED}["']\s*\))
  | (?:/\s*["']{PROTECTED}["'])
  | (?:sqlite3\.connect\(\s*["'][^"']*{PROTECTED}[^"']*["'])
  | (?:["'](?:[./~][^"']*)?{PROTECTED}["'])
    """)

# Write intent from CODE, never from prose. lot_sizes.py was flagged a writer because its
# DOCSTRING mentions save_fo_bars, while the module opens the database mode=ro and cannot
# write at all — so docstrings and comments are stripped before this is applied.
WRITE_HINTS = re.compile(
    r"(?i)\b(insert\s+into|update\s+\w+\s+set|delete\s+from|create\s+table|drop\s+table"
    r"|\.commit\(\)|save_fo_bars|save_from_json_rows|save_bars|executemany)\b")

# A temp or demo database cannot cause the divergence this rule prevents.
BENIGN = re.compile(r"tempfile\.|NamedTemporaryFile|mkdtemp|gettempdir|:memory:")

# db_config is the module that IMPLEMENTS the rule; it must name the paths.
EXEMPT_FILES = {"db_config.py", "db_path_audit.py"}
ANNOTATION = re.compile(r"#\s*db-audit:\s*(reader|exempt)")


def _files():
    out = []
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, names in os.walk(base):
            if any(p in dirpath for p in SKIP_PARTS):
                dirnames[:] = []
                continue
            dirnames[:] = [x for x in dirnames if x not in SKIP_PARTS]
            out += [os.path.join(dirpath, n) for n in names if n.endswith(".py")]
    if SCAN_ROOT_FILES:
        out += [os.path.join(ROOT, n) for n in os.listdir(ROOT)
                if n.endswith(".py") and os.path.isfile(os.path.join(ROOT, n))]
    return sorted(out)


def audit() -> tuple[list, list, int]:
    errors, warns, clean = [], [], 0
    for path in _files():
        rel = os.path.relpath(path, ROOT)
        if os.path.basename(path) in EXEMPT_FILES:
            continue
        try:
            src = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue

        hits = []
        for n, line in enumerate(src.splitlines(), 1):
            code = line.split("#")[0]
            if "option_chains" not in code:
                continue
            if BENIGN.search(code):
                continue
            if HARDCODED.search(code):
                hits.append((n, line.strip()[:96]))
        if not hits:
            if "db_config" in src:
                clean += 1
            continue

        ann = ANNOTATION.search(src)
        if ann:
            continue
        # A file that IMPORTS the resolver has complied; a mirror constant beside it is
        # deliberate. backfill_daily_bars.py is the worked case — CANONICAL_DB comes from
        # resolve_writable_db_path() and MIRROR_DB exists so the module can WARN when the
        # copy is stale. Flagging that would be punishing the one file doing it properly,
        # and a checker that flags correct code is how checkers get switched off.
        if "resolve_writable_db_path" in src:
            continue
        # strip docstrings and comments: prose that NAMES a writer is not a writer
        code_only = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "", src)
        code_only = "\n".join(l.split("#")[0] for l in code_only.splitlines())
        writes = WRITE_HINTS.search(code_only)
        rec = {"file": rel, "hits": hits,
               "why": (writes.group(0) if writes else None)}
        (errors if writes else warns).append(rec)
    return errors, warns, clean


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="reader-only hardcodes count toward the exit code too")
    a = ap.parse_args()

    errors, warns, clean = audit()
    print("DB PATH AUDIT  —  CLAUDE.md: \"Never hardcode a database path or DSN.\"")
    print("A reader may fall back to the repo-local copy. A writer may NOT.\n")

    if errors:
        print(f"ERROR — {len(errors)} module(s) WRITE and hardcode a path. This is the C37 "
              f"defect:\n")
        for r in errors:
            print(f"  {r['file']}   (write intent: {r['why']})")
            for n, line in r["hits"][:3]:
                print(f"      {n}: {line}")
            print("      fix: from db_config import resolve_writable_db_path")
            print()
    if warns:
        print(f"WARN — {len(warns)} module(s) hardcode a path but only READ. Legitimate per")
        print("       CLAUDE.md; they inherit the mirror's staleness. Annotate with")
        print("       `# db-audit: reader` to silence, or route through resolve_db_path().\n")
        for r in warns:
            print(f"  {r['file']}:{r['hits'][0][0]}")

    print(f"\n  {clean} module(s) resolve through db_config")
    print(f"  {len(errors)} ERROR   {len(warns)} WARN")
    if errors or (a.strict and warns):
        sys.exit(1)
    if not errors and not warns:
        print("\nClean.")


if __name__ == "__main__":
    main()
