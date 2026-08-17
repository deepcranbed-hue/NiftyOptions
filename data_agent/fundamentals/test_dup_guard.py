#!/usr/bin/env python3
"""test_dup_guard.py — offline proof that the C36 duplicate write is now refused.

Runs against a TEMP COPY of expectation_snapshots.json with the network stubbed out, so it
neither hits yfinance nor touches the real append-only log.

Four cases, because the guard has to draw the line in exactly the right place:

  same day + identical rows            -> REFUSED, exit 2, nothing appended   (this is C36)
  same day + identical rows + --force  -> appended (a deliberate second capture is allowed)
  same day + rows differ               -> appended (an intraday revision IS an observation)
  later day + identical rows           -> appended (a week with no revisions is a FINDING,
                                          which is why the guard is scoped to the day and
                                          not to content alone)

    python3 test_dup_guard.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))      # .../fundamentals -> data_agent -> repo
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import expectation_snapshot as es                                    # noqa: E402

REAL = os.path.join(ROOT, "expectation_snapshots.json")


def _stub(rows):
    """Hand back one prepared row per symbol instead of calling Yahoo."""
    it = iter(rows)

    def one(sym):
        try:
            return next(it)
        except StopIteration:
            return {"symbol": sym, "error": "no data"}
    return one


def case(label, rows, when, want_rc, want_grew, force=False):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "snap.json")
    shutil.copy(REAL, path)
    before = len(json.load(open(path))["snapshots"])

    class FakeDT(datetime):
        @classmethod
        def now(cls):
            return when

    saved = (es._OUT, es._universe, es._snapshot_one, es.datetime, sys.argv)
    es._OUT = path
    es._universe = lambda: [r.get("symbol") or f"X{i}" for i, r in enumerate(rows)]
    es._snapshot_one = _stub(rows)
    es.datetime = FakeDT
    sys.argv = ["expectation_snapshot.py"] + (["--force"] if force else [])
    try:
        rc = es.main()
    finally:
        es._OUT, es._universe, es._snapshot_one, es.datetime, sys.argv = saved

    grew = len(json.load(open(path))["snapshots"]) - before
    shutil.rmtree(tmp, ignore_errors=True)
    ok = rc == want_rc and grew == want_grew
    print(f"  {'PASS' if ok else 'FAIL'}  {label}\n"
          f"          rc={rc} (want {want_rc})   appended={grew} (want {want_grew})")
    return ok


def main() -> int:
    if not os.path.exists(REAL):
        sys.exit(f"need {REAL} to copy from")
    log = json.load(open(REAL))["snapshots"]
    last = log[-1]
    same_day = datetime.fromisoformat(last["captured_at"]) + timedelta(minutes=1)
    next_week = datetime.fromisoformat(last["captured_at"]) + timedelta(days=7)
    same = last["rows"]
    nudged = [dict(r, targetMeanPrice=(r.get("targetMeanPrice") or 0) + 1) for r in same]

    print(f"\n=== C36 duplicate-write guard  ({len(log)} snapshots in the real log) ===\n")
    res = [
        case("same day, identical rows -> REFUSED", same, same_day, 2, 0),
        case("same day, identical rows, --force -> appended", same, same_day, 0, 1,
             force=True),
        case("same day, rows differ -> appended", nudged, same_day, 0, 1),
        case("a week later, identical rows -> appended", same, next_week, 0, 1),
    ]
    print(f"\n{sum(res)}/{len(res)} passed"
          f"{'' if all(res) else '  <-- the guard is wrong, not the test'}")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
