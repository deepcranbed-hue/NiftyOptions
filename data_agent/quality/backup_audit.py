#!/usr/bin/env python3
"""
backup_audit.py — is the work actually anywhere other than this disk?

WHY THIS EXISTS
---------------
Asked "is the fundamental data in Google Drive as well as locally", the answer turned out to
be that the framing hid the real exposure: on 2026-08-17 `main` was **51 commits ahead of
origin/main**, the oldest dated 2026-08-07. Eleven days of work — every correction from C26
to C37, every new script, every rebuilt panel — existed on one machine. Drive was never the
weak point.

THREE STORES, THREE DIFFERENT ANSWERS
-------------------------------------
  SQLite market data    Google Drive. Syncs off-machine by itself. Fine.
  Repo artifacts        delivery_history.json, attributable_panel.json, quality_growth.json,
                        screener_page_tables.csv and the rest are all TRACKED. git is the
                        right home for these and a better one than Drive: Drive keeps one
                        current copy, git keeps HISTORY — and after three days spent
                        establishing which numbers are right, being able to see when a number
                        changed and why is the valuable part. Copying them into Drive as well
                        would create a second uncontrolled copy of the same file, which is
                        precisely the defect behind C37 and the mirror confusion. So: push,
                        do not duplicate.
  PostgreSQL            localhost/niftyoptions — macro + fundamentals, written by 13 of 19
                        files in data_agent/macro and 19 of 31 in data_agent/fundamentals.
                        NOT in Drive, and git cannot hold a live database. This is the one
                        place the question's premise was right, and there was no pg_dump
                        anywhere in the repo.

WHAT THIS CHECKS, AND WHY EACH ONE IS DATA-DRIVEN
-------------------------------------------------
Not "does a backup directory exist" but "is the newest work in it". An empty backup that
exists is the same failure as a cron job that returns 0 without capturing (C36).

    python3 data_agent/quality/backup_audit.py
    python3 data_agent/quality/backup_audit.py --quiet
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))

OK, WARN, DUE = "OK", "WARN", "DUE"

# Artifacts the last three days produced. Tracked means a push protects them.
KEY_ARTIFACTS = [
    "delivery_history.json", "attributable_panel.json", "screener_panel.json",
    "quality_growth.json", "pe_history.json", "fii_holdings.json",
    "expectation_snapshots.json", "nifty50_drivers.json", "earnings_acceleration.json",
    "nifty_history.json", "nifty-50-stock-list.csv", "SecurityMaster.zip",
    "data_agent/fundamentals/screener_page_tables.csv",
    "StrategyBacktesting/Hypotheses.md",
]


def _git(*a):
    try:
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def check_pushed():
    """BLOCKING. The single highest-value check in this file.

    A commit is not a backup. Until it is pushed it is one disk failure from gone, and the
    reassuring part is that `git log` looks identical either way — which is exactly why this
    went unnoticed for eleven days.
    """
    if _git("rev-parse", "--git-dir") is None:
        return WARN, "not a git repository"
    remotes = _git("remote") or ""
    if not remotes:
        return DUE, "NO REMOTE AT ALL — commits cannot leave this machine"
    up = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if not up:
        br = _git("rev-parse", "--abbrev-ref", "HEAD") or "?"
        return DUE, f"branch {br} tracks no upstream — nothing is being pushed"
    ahead = _git("rev-list", "--count", f"{up}..HEAD")
    behind = _git("rev-list", "--count", f"HEAD..{up}")
    n = int(ahead or 0)
    if n:
        oldest = _git("log", "--format=%ad", "--date=short", f"{up}..HEAD") or ""
        first = oldest.splitlines()[-1] if oldest.splitlines() else "?"
        return DUE, (f"{n} commit(s) unpushed to {up}, oldest dated {first} — "
                     f"this work exists on one disk")
    return OK, f"level with {up}" + (f" ({behind} behind)" if behind not in ("0", None) else "")


def check_untracked_artifacts():
    """BLOCKING. A push only protects what git tracks.

    SecurityMaster.zip is the live example: it is excluded by the blanket `*.zip` rule, and it
    is now load-bearing — contract_size is stamped from it at capture and freshness.py treats
    it as blocking. It is re-downloadable from Breeze, so losing it is not data loss, but a
    2.9 MB monthly input to STORED data belongs in history.
    """
    missing, ignored = [], []
    for rel in KEY_ARTIFACTS:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            missing.append(rel)
            continue
        if _git("ls-files", "--error-unmatch", rel) is None:
            ignored.append(rel)
    msg = f"{len(KEY_ARTIFACTS) - len(ignored) - len(missing)}/{len(KEY_ARTIFACTS)} tracked"
    if missing:
        msg += f"; absent: {', '.join(missing)}"
    if ignored:
        return DUE, msg + f"; NOT TRACKED so a push will not save them: {', '.join(ignored)}"
    return (WARN if missing else OK), msg


def check_pg_dump():
    """BLOCKING. The one thing git genuinely cannot hold.

    Verifies a dump EXISTS, is RECENT, and is not trivially small — a 2 KB dump is a schema
    with no rows, which passes an existence check and restores nothing.
    """
    from_env = os.environ.get("PG_BACKUP_DIR")
    cands = [from_env] if from_env else []
    home = os.path.expanduser("~")
    cands += [
        os.path.join(home, "Library", "CloudStorage",
                     "GoogleDrive-deepcranbed@gmail.com", "My Drive", "niftyoptions_pg"),
        os.path.join(ROOT, "backups", "pg"),
    ]
    for d in cands:
        if d and os.path.isdir(d):
            dumps = [os.path.join(d, f) for f in os.listdir(d)
                     if f.endswith((".sql", ".sql.gz", ".dump"))]
            if not dumps:
                return DUE, f"{d} exists but holds no dump"
            newest = max(dumps, key=os.path.getmtime)
            age = (dt.datetime.now()
                   - dt.datetime.fromtimestamp(os.path.getmtime(newest))).days
            size = os.path.getsize(newest)
            msg = (f"{os.path.basename(newest)}, {age}d old, "
                   f"{size / 1e6:.1f} MB, in {os.path.dirname(newest)}")
            if size < 100_000:
                return DUE, msg + " — suspiciously small; a schema-only dump restores nothing"
            if age > 7:
                return DUE, msg + " — older than a week"
            return OK, msg
    return DUE, ("no Postgres dump found — macro + fundamentals live only in "
                 "localhost/niftyoptions. Run data_agent/pg_backup.sh")


def check_drive_vs_mirror():
    """ADVISORY. Both copies are market data and Drive is authoritative; the mirror being
    stale is expected and harmless for readers. Reported so a number traced back to the
    mirror can be dated."""
    drive = os.path.join(
        os.path.expanduser("~"), "Library", "CloudStorage",
        "GoogleDrive-deepcranbed@gmail.com", "My Drive", "option_chains.db")
    mirror = os.path.join(ROOT, "option_chains.db")
    if not os.path.exists(drive):
        return WARN, "Drive copy not reachable from here (expected in a cloud session)"
    if not os.path.exists(mirror):
        return OK, "Drive present; no local mirror"
    dm = dt.datetime.fromtimestamp(os.path.getmtime(drive))
    mm = dt.datetime.fromtimestamp(os.path.getmtime(mirror))

    # DIRECTION MATTERS, AND THE TWO CASES ARE NOT THE SAME SEVERITY.
    # SQLite flows Drive -> local. So a mirror BEHIND Drive is the normal resting state —
    # it just needs a cp before anything reads it. A mirror AHEAD of Drive is a RULE
    # VIOLATION: it means something wrote to the read-only copy, which is exactly C37, and
    # whatever it wrote is not in the source of truth and will be destroyed by the next
    # refresh. The first version of this check only tested "behind", so it would have stayed
    # green through the very defect it was written after.
    if mm > dm:
        ahead = (mm - dm).total_seconds() / 3600
        return DUE, (f"MIRROR IS {ahead:.1f}h NEWER THAN DRIVE — something wrote to the "
                     f"read-only copy (C37). Whatever it wrote is NOT in the source of "
                     f"truth and the next cp will destroy it. Find the writer before "
                     f"refreshing: python3 data_agent/quality/db_path_audit.py")
    lag = (dm - mm).days
    if lag > 0:
        return WARN, (f"mirror is {lag}d behind Drive — expected direction; refresh before "
                      f"an agent reads it: cp '{drive}' '{mirror}'")
    return OK, f"mirror level with Drive ({mm:%Y-%m-%d})"


CHECKS = [
    {"name": "git: pushed to remote", "fn": check_pushed, "sev": "blocking",
     "fix": "git push origin main",
     "why": "a commit is not a backup; git log looks identical pushed or not"},
    {"name": "git: artifacts tracked", "fn": check_untracked_artifacts, "sev": "blocking",
     "fix": "git add -f <file>   (and drop it from .gitignore if it belongs in history)",
     "why": "a push only protects what git tracks"},
    {"name": "postgres dump", "fn": check_pg_dump, "sev": "blocking",
     "fix": "data_agent/pg_backup.sh",
     "why": "macro + fundamentals are here and git cannot hold a live database"},
    # Advisory for the normal "mirror is behind" case; the function returns DUE for the
    # reverse, which is a rule violation rather than staleness.
    {"name": "sqlite mirror vs Drive", "fn": check_drive_vs_mirror, "sev": "advisory",
     "fix": "cp \"$DRIVE/option_chains.db\" ./option_chains.db",
     "why": "readers may use the mirror; it inherits whatever age it has"},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    print(f"BACKUP AUDIT  {dt.date.today()}   is this work anywhere other than one disk?\n")
    todo = []
    for c in CHECKS:
        state, detail = c["fn"]()
        if state != OK:
            todo.append((c, state))
        if a.quiet and state == OK:
            continue
        print(f"  [{state:4s}] {c['name']:26s} {detail}")
        if not a.quiet:
            print(f"           {c['sev']} · {c['why']}")

    blocking = [(c, s) for c, s in todo if c["sev"] == "blocking" and s == DUE]
    if not todo:
        print("\nEverything is in at least two places.")
        return
    print(f"\n{len(todo)} item(s) need attention — {len(blocking)} blocking:\n")
    for c, s in todo:
        print(f"  # [{s}/{c['sev']}] {c['name']}: {c['why']}")
        print(f"  {c['fix']}\n")
    if blocking:
        sys.exit(1)


if __name__ == "__main__":
    main()
