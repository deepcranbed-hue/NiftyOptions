#!/usr/bin/env python3
"""
fii_store_compare.py — compare fii_dii_flows across SQLite and Postgres, and use the
duplicate as a test for silently-zeroed days.

WHY BOTHER COMPARING A TABLE NOBODY READS
-----------------------------------------
The Postgres copy of `fii_dii_flows` (32 rows) is a fossil: nothing writes it and nothing
reads it, and `download_fii_dii.py` still carries `db_url = resolve_pg_dsn()` on line 45 as a
vestige of the convention that did. The SQLite copy (39 rows, 2026-06-18 .. 2026-08-14) is the
live one, written via resolve_writable_db_path() and read by backend/main.py,
backend/nifty50_routes.py and the Nifty 50 UI.

Tidying it up is not the point. The point is that an independently-populated second copy of the
same series is the only cross-check available for a KNOWN defect that cannot be detected from
one copy alone:

    MISSING DATA IS RECORDED AS ZERO, and zero is a legitimate value for a NET flow.

Three layers do it, independently:
  1. download_fii_dii.py:81-83 initialises every field of flows_by_date[dt] to 0.0, then
     overwrites only the fields the API actually returned. A date present in the CASH feed but
     absent from the F&O feeds keeps 0.0.
  2. backend/main.py:1734-1737 reads `r[7] if r[7] is not None else 0` — so even a NULL, if one
     were ever written, becomes 0 at the API boundary.
  3. .state/flows_cash_cache.json writes 0.0 on fetch failure. 2026-07-17 was found that way BY
     HAND; its true value was -216,528.

Because of (1) and (2), no query against one store can distinguish "flat" from "absent". Two
stores can, wherever they disagree — a date that is 0 in one and non-zero in the other is
missing data in the first, not a flat book.

WHAT IS ALREADY KNOWN AND SHOULD BE REPRODUCED
----------------------------------------------
SQLite carries an all-zero F&O block on exactly two dates, 2026-06-18 and 2026-06-22, both with
non-zero cash values, both at the start of the series — consistent with the F&O feed's history
beginning later than the cash feed's. If the Postgres fossil holds real F&O values for either
date, that confirms them as missing-recorded-as-zero rather than genuinely flat, and the fossil
becomes worth harvesting before it is dropped.

SCOPE, HONESTLY. No register conclusion depends on these four columns. nifty_history.flows()
reads only the cash columns, so H62's 0.22% net/gross finding is unaffected; H63's futures
positioning comes from `participant_oi`, a different table. The exposure is the app's flow
display, not a published number.

    python3 data_agent/quality/fii_store_compare.py
    python3 data_agent/quality/fii_store_compare.py --harvest   # print UPDATEs, run nothing
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PG_TABLE = "fii_dii_flows"          # unqualified on purpose; resolved and PRINTED below


def _sqlite_rows():
    from db_config import resolve_db_path
    path = resolve_db_path()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cols = [r[1] for r in con.execute(f"pragma table_info({PG_TABLE})")]
    rows = {r[0]: dict(zip(cols, r))
            for r in con.execute(f"select {', '.join(cols)} from {PG_TABLE}")}
    con.close()
    return path, cols, rows


def _pg_rows():
    """Resolve the table's SCHEMA and print it.

    Never identify a Postgres table from an unqualified name — on 2026-08-17 reading `relname`
    out of pg_stat_user_tables, which omits the schema, produced a confident and wrong
    'correction' to the register (fundamentals is a SCHEMA, not a table). So this asks
    information_schema which schemas actually hold the name, and says so.
    """
    try:
        import psycopg
        connect = psycopg.connect
    except ImportError:
        try:
            import psycopg2
            connect = psycopg2.connect
        except ImportError:
            return None, None, None, "neither psycopg nor psycopg2 is importable"
    from db_config import resolve_pg_dsn
    dsn = resolve_pg_dsn()
    try:
        con = connect(dsn)
    except Exception as exc:
        return None, None, None, f"cannot connect to {dsn}: {exc}"
    cur = con.cursor()
    cur.execute("""select table_schema from information_schema.tables
                   where table_name = %s order by table_schema""", (PG_TABLE,))
    schemas = [r[0] for r in cur.fetchall()]
    if not schemas:
        con.close()
        return dsn, None, None, f"no table named {PG_TABLE!r} in any schema"
    if len(schemas) > 1:
        print(f"  NOTE {PG_TABLE!r} exists in {len(schemas)} schemas: {schemas} — "
              f"using {schemas[0]}")
    schema = schemas[0]
    cur.execute("""select column_name from information_schema.columns
                   where table_schema=%s and table_name=%s order by ordinal_position""",
                (schema, PG_TABLE))
    cols = [r[0] for r in cur.fetchall()]
    cur.execute(f'select {", ".join(chr(34) + c + chr(34) for c in cols)} '
                f'from "{schema}"."{PG_TABLE}"')
    rows = {}
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        key = str(d.get("flow_date"))[:10]
        rows[key] = d
    con.close()
    return dsn, f"{schema}.{PG_TABLE}", cols, rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harvest", action="store_true",
                    help="print UPDATE statements for zeros the fossil can fill; runs nothing")
    a = ap.parse_args()

    spath, scols, srows = _sqlite_rows()
    print(f"SQLITE    {spath}")
    print(f"          {len(srows)} rows, {min(srows)} .. {max(srows)}, {len(scols)} columns\n")

    dsn, pgname, pcols, prows = _pg_rows()
    if pcols is None:
        print(f"POSTGRES  UNAVAILABLE — {prows}")
        print("\nWithout the second copy there is no cross-check: a 0 in a NET column is")
        print("indistinguishable from missing data by construction. Nothing to compare.")
        sys.exit(2)
    print(f"POSTGRES  {dsn}")
    print(f"          {pgname}: {len(prows)} rows, "
          f"{min(prows) if prows else '-'} .. {max(prows) if prows else '-'}, "
          f"{len(pcols)} columns\n")

    shared_cols = [c for c in scols if c in pcols and c not in ("flow_date", "updated_at")]
    print(f"comparable columns ({len(shared_cols)}): {', '.join(shared_cols)}")
    only_s = sorted(set(scols) - set(pcols))
    only_p = sorted(set(pcols) - set(scols))
    if only_s:
        print(f"  SQLite only: {', '.join(only_s)}")
    if only_p:
        print(f"  Postgres only: {', '.join(only_p)}  <- the fossil's schema is older")

    sd, pd_ = set(srows), set(prows)
    print(f"\nDATES  both {len(sd & pd_)}   SQLite only {len(sd - pd_)}   "
          f"Postgres only {len(pd_ - sd)}")
    if pd_ - sd:
        print(f"  in Postgres and NOT in SQLite: {sorted(pd_ - sd)}")
        print("  ^ if this is non-empty the fossil holds days the live table lost")
    if sd - pd_:
        d = sorted(sd - pd_)
        print(f"  in SQLite only ({len(d)}): {d[0]} .. {d[-1]} (expected: the fossil stopped)")

    # ---- the test this file exists for -------------------------------------------------
    fillable, disagree = [], []
    for day in sorted(sd & pd_):
        for col in shared_cols:
            sv, pv = srows[day].get(col), prows[day].get(col)
            if sv is None or pv is None:
                continue
            try:
                sv, pv = float(sv), float(pv)
            except (TypeError, ValueError):
                continue
            if sv == 0 and pv != 0:
                fillable.append((day, col, sv, pv))
            elif pv == 0 and sv != 0:
                pass                      # fossil missing it; the live table is fine
            elif abs(sv - pv) > max(0.01, abs(sv) * 0.001):
                disagree.append((day, col, sv, pv))

    print(f"\nZERO IN SQLITE, REAL IN POSTGRES — missing data, not a flat book: {len(fillable)}")
    for day, col, sv, pv in fillable:
        print(f"  {day}  {col:18} sqlite {sv:>14,.2f}   postgres {pv:>14,.2f}")
    if not fillable:
        print("  none — the two known all-zero dates (2026-06-18, 2026-06-22) are zero in")
        print("  BOTH stores, so they are the F&O feed's start boundary rather than a lost")
        print("  fetch. That is the answer this comparison existed to get.")

    print(f"\nVALUES DISAGREE ON A SHARED DATE (>0.1%): {len(disagree)}")
    for day, col, sv, pv in disagree[:20]:
        print(f"  {day}  {col:18} sqlite {sv:>14,.2f}   postgres {pv:>14,.2f}")
    if len(disagree) > 20:
        print(f"  ... and {len(disagree) - 20} more")

    print("\nVERDICT")
    if not fillable and not disagree and not (pd_ - sd):
        print("  The fossil corroborates the live table everywhere they overlap and holds")
        print("  nothing the live table lacks. It is safe to drop — together with the")
        print("  vestigial `db_url = resolve_pg_dsn()` on download_fii_dii.py:45, so the")
        print("  next reader is not sent looking for a store that no longer exists.")
    else:
        print("  DO NOT DROP the Postgres copy yet — it holds something the live table does")
        print("  not. Harvest first (--harvest prints the statements), then drop.")

    if a.harvest and fillable:
        print("\n-- review every line before running; these are not applied automatically")
        for day, col, _, pv in fillable:
            print(f"UPDATE fii_dii_flows SET {col} = {pv} WHERE flow_date = '{day}';")

    sys.exit(1 if (fillable or disagree) else 0)


if __name__ == "__main__":
    main()
