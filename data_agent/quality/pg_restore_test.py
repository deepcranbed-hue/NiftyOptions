#!/usr/bin/env python3
"""pg_restore_test.py — restore the newest Postgres dump into a scratch database and prove
the rows arrive.

WHY THIS EXISTS
---------------
`pg_backup.sh` verifies that the dump EXISTS, is plausibly sized, and contains COPY/INSERT
lines. That is circumstantial and it is the same shape as C36, where a wrapper proved success
by watching a counter go up and a duplicate write satisfied the test. A dump full of COPY
lines can still fail to restore: a missing extension, a table created before the schema it
lives in, an undefined type, a role reference `--no-owner` did not strip. None of that is
visible until something tries.

An untested backup is worse than no backup, because it is trusted.

WHAT IT COMPARES AGAINST, AND WHY NOT THE LIVE DATABASE
-------------------------------------------------------
The obvious check — restored counts against localhost/niftyoptions — is wrong. The live
database keeps ingesting after the dump is taken, so every append-only table drifts and the
test fails for a reason that is not a defect. Worse, it would pass a truncated dump on a day
nothing was written.

So the ground truth is THE FILE. This counts the data rows inside each `COPY ... FROM stdin;`
block (lines up to the terminating `\\.`) and requires the restored table to hold exactly
that many. File says 7,416, database must say 7,416. That is a closed loop over the artifact
under test, and it is independent of what the live database is doing. Drift against live is
still reported, as information, never as a failure.

TWO TRAPS THIS FILE IS BUILT AROUND
-----------------------------------
1. `psql` EXITS 0 AFTER SKIPPING EVERY FAILING STATEMENT. Without `ON_ERROR_STOP=1` a restore
   that dropped half the database reports success — exactly the "the command exited 0" proof
   this repo has been burned by three times. It is set, and stderr is captured and shown.

2. COUNTS MUST BE `COUNT(*)`, NOT `n_live_tup`. The planner statistic reads ZERO on a freshly
   restored table until ANALYZE runs, so the lazy version fails loudly on a perfectly good
   backup. `pg_stat_user_tables` is also schema-blind, which is how the O15 'correction' was
   made and retracted on 2026-08-17 — every table here is schema-qualified, because this
   database genuinely contains both a `fundamentals` SCHEMA and a `public.fundamentals`
   TABLE, and an unqualified name cannot tell them apart.

    python3 data_agent/quality/pg_restore_test.py
    python3 data_agent/quality/pg_restore_test.py --keep    # leave the scratch db for a look
    python3 data_agent/quality/pg_restore_test.py --dump path/to/file.sql.gz
"""
from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import urllib.parse as up

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))

SCRATCH = "niftyoptions_restoretest"
DEFAULT_DSN = "postgresql://localhost/niftyoptions"


def _dsn():
    return (os.environ.get("DATABASE_URL")
            or os.environ.get("NIFTY_PG_DSN")
            or DEFAULT_DSN)


def _swap_db(dsn: str, name: str) -> str:
    p = up.urlparse(dsn)
    return up.urlunparse(p._replace(path="/" + name))


def _dump_dirs():
    from_env = os.environ.get("PG_BACKUP_DIR")
    home = os.path.expanduser("~")
    return [d for d in [
        from_env,
        os.path.join(home, "Library", "CloudStorage",
                     "GoogleDrive-deepcranbed@gmail.com", "My Drive", "niftyoptions_pg"),
        os.path.join(ROOT, "backups", "pg"),
    ] if d]


def newest_dump(explicit=None):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    best = None
    for d in _dump_dirs():
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith((".sql", ".sql.gz")):
                p = os.path.join(d, f)
                if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                    best = p
    return best


def _open(path):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") \
        else open(path, "r", errors="replace")


COPY_RE = re.compile(r"^COPY\s+([^\s(]+)\s*\(", re.I)
INSERT_RE = re.compile(r"^INSERT INTO\s+([^\s(]+)", re.I)


TRAILER = "PostgreSQL database dump complete"


def counts_in_file(path):
    """Rows per schema-qualified table, read out of the dump itself, plus two completeness
    facts that the counts alone cannot give.

    THE COUNTS ARE A CLOSED LOOP, AND THAT IS BOTH THE POINT AND A HOLE. Comparing the file
    against itself makes the test immune to live-database drift — but it is equally immune to
    TRUNCATION, because a dump cut in half reports fewer rows and the restore faithfully
    reproduces the smaller number. Verified: a dump truncated to 60KB passed cleanly on the
    first version of this file. `macro.factor_series` claimed 473 rows, the restore produced
    473, and every row read 'ok'.

    So the loop needs an anchor outside itself. Two, both deterministic and neither dependent
    on what the live database is doing:

      1. THE TRAILER. pg_dump writes "-- PostgreSQL database dump complete" as its last line.
         Absent, the dump was cut short or the dump process died — regardless of how
         self-consistent what survived happens to be.
      2. EVERY COPY BLOCK TERMINATED. A COPY block ends with a lone `\\.`. Truncation
         mid-block leaves one open, and the row count for that table is then a count of
         'however far the file got', which looks like data.

    Handles both shapes pg_dump can emit: COPY blocks (the default, and what pg_backup.sh
    produces) and --inserts.
    """
    counts, in_copy, table = {}, False, None
    unterminated, trailer = None, False
    for line in _open(path):
        if in_copy:
            if line.startswith("\\."):
                in_copy, table = False, None
            else:
                counts[table] = counts.get(table, 0) + 1
            continue
        if TRAILER in line:
            trailer = True
        m = COPY_RE.match(line)
        if m:
            table = m.group(1).replace('"', "")
            counts.setdefault(table, 0)
            in_copy = True
            continue
        m = INSERT_RE.match(line)
        if m:
            t = m.group(1).replace('"', "")
            counts[t] = counts.get(t, 0) + 1
    if in_copy:
        unterminated = table
    return counts, trailer, unterminated


def _psql(dsn, sql, *, quiet=True):
    cmd = ["psql", "-v", "ON_ERROR_STOP=1", "-Atq" if quiet else "-At", "-d", dsn, "-c", sql]
    return subprocess.run(cmd, capture_output=True, text=True)


def live_counts(dsn, tables):
    out = {}
    for t in tables:
        schema, _, name = t.partition(".")
        r = _psql(dsn, f'select count(*) from "{schema}"."{name}"')
        out[t] = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=None, help="restore this file instead of the newest")
    ap.add_argument("--keep", action="store_true", help="do not drop the scratch database")
    ap.add_argument("--scratch", default=SCRATCH)
    a = ap.parse_args()

    # Only psql. CREATE/DROP DATABASE go through it rather than through createdb/dropdb,
    # whose connection flags differ from psql's — createdb takes the new database as a
    # POSITIONAL and has no -d, so a DSN passed the psql way is silently a different thing.
    # One tool, one way of being told where the server is.
    for tool in ("psql",):
        if not shutil.which(tool):
            # Not a failure of the backup. In a sandbox or on a machine without the client
            # tools there is nothing to restore WITH, and saying so beats a red line that
            # means "postgres is not installed here".
            print(f"SKIP  {tool} is not on PATH — cannot restore-test from this machine.")
            print("0 findings.")
            return 0

    path = newest_dump(a.dump)
    if not path:
        print("FAIL  no dump found in " + ", ".join(_dump_dirs()))
        print("      run data_agent/pg_backup.sh first")
        print("1 findings.")
        return 1

    size = os.path.getsize(path)
    print(f"dump    {path}\n        {size / 1e6:.2f} MB")

    try:
        want, trailer, unterminated = counts_in_file(path)
    except (EOFError, OSError) as exc:
        # A .gz cut off mid-write fails here, which is the other truncation shape.
        print(f"\nFAIL  the dump does not read to the end: {type(exc).__name__}: {exc}")
        print("1 findings.")
        return 1

    total = sum(want.values())
    print(f"        {len(want)} table(s), {total:,} data rows counted IN THE FILE")
    if not want:
        print("\nFAIL  the dump contains no COPY or INSERT data — schema only, restores nothing")
        print("1 findings.")
        return 1

    early = []
    if not trailer:
        early.append(f"no {TRAILER!r} trailer — the dump is TRUNCATED or pg_dump died")
    if unterminated:
        early.append(f"COPY block for {unterminated} is never terminated by a lone backslash-dot")
    if early:
        print()
        for e in early:
            print(f"FAIL  {e}")
        print("      Row counts below would still agree with themselves — a truncated dump is")
        print("      internally consistent. That is why these two checks exist.")
        print(f"{len(early)} findings.")
        return 1

    src = _dsn()
    admin = _swap_db(src, "postgres")
    scratch_dsn = _swap_db(src, a.scratch)

    _psql(admin, f'DROP DATABASE IF EXISTS "{a.scratch}"')
    r = _psql(admin, f'CREATE DATABASE "{a.scratch}"')
    if r.returncode != 0:
        print(f"\nFAIL  cannot create {a.scratch}: {r.stderr.strip()}")
        print("1 findings.")
        return 1
    print(f"scratch {scratch_dsn}")

    findings = []
    try:
        # ON_ERROR_STOP is the whole point: without it psql exits 0 having skipped every
        # statement that failed, and a half-restored database reports success.
        print("\nrestoring ...")
        cat = ["gzip", "-dc", path] if path.endswith(".gz") else ["cat", path]
        p1 = subprocess.Popen(cat, stdout=subprocess.PIPE)
        p2 = subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-q", "-d", scratch_dsn],
                            stdin=p1.stdout, capture_output=True, text=True)
        p1.stdout.close()
        p1.wait()
        err = (p2.stderr or "").strip()
        if p2.returncode != 0:
            findings.append("restore aborted")
            print(f"FAIL  psql exited {p2.returncode}")
            for line in err.splitlines()[:12]:
                print("      " + line)
        elif err:
            print("  psql stderr (non-fatal):")
            for line in err.splitlines()[:6]:
                print("      " + line)

        # THIRD ANCHOR, and the only one that looks outside the file. A table that exists and
        # holds rows in the live database and is ABSENT from the dump is a coverage failure
        # no amount of internal consistency reveals. Unlike row counts, the table SET does not
        # drift day to day, so this is safe to fail on. Schema-qualified, always: this
        # database has a `fundamentals` SCHEMA and a `public.fundamentals` TABLE, and the
        # unqualified name is how the O15 'correction' was made and retracted.
        r = _psql(src, """
            select table_schema||'.'||table_name
              from information_schema.tables
             where table_type='BASE TABLE'
               and table_schema not in ('pg_catalog','information_schema')
             order by 1""")
        if r.returncode == 0 and r.stdout.strip():
            live_tables = [t for t in r.stdout.strip().splitlines() if t]
            nonempty = {t: n for t, n in live_counts(src, live_tables).items() if n}
            absent = sorted(set(nonempty) - set(want))
            if absent:
                for t in absent:
                    findings.append(f"{t}: {nonempty[t]:,} rows live, ABSENT from the dump")
                    print(f"FAIL  {t} holds {nonempty[t]:,} rows and is not in the dump at all")

        got = live_counts(scratch_dsn, sorted(want))
        live = live_counts(src, sorted(want))

        print(f"\n{'table':<34}{'in file':>12}{'restored':>12}{'live now':>12}  verdict")
        for t in sorted(want):
            f_, g, l = want[t], got.get(t), live.get(t)
            if g is None:
                verdict, ok = "MISSING after restore", False
            elif g != f_:
                verdict, ok = f"MISMATCH ({g - f_:+,})", False
            else:
                verdict, ok = "ok", True
            if not ok:
                findings.append(f"{t}: {verdict}")
            drift = "" if l is None or l == f_ else f"  (live {l - f_:+,} since the dump)"
            fmt = lambda x: f"{x:,}" if isinstance(x, int) else "-"
            print(f"{t:<34}{fmt(f_):>12}{fmt(g):>12}{fmt(l):>12}  {verdict}{drift}")

        print("\n  'live now' is INFORMATION, not a test. The database keeps ingesting after a")
        print("  dump is taken, so drift there is expected; the pass/fail is file vs restored.")
    finally:
        if a.keep:
            print(f"\n--keep: {a.scratch} left in place. Drop it with:"
                  f"\n  psql -d '{admin}' -c 'DROP DATABASE \"{a.scratch}\"'")
        else:
            _psql(admin, f'DROP DATABASE IF EXISTS "{a.scratch}"')

    if findings:
        print(f"\nThe newest dump does NOT restore cleanly. Do not rely on it.")
        print(f"{len(findings)} findings.")
        return 1
    print(f"\nOK    every table restored with the row count the file claims "
          f"({total:,} rows across {len(want)} tables)")
    print("0 findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
