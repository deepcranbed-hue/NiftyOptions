"""sync_coverage.py — which symbols does the daily pipeline actually maintain?

THE QUESTION THIS ANSWERS
-------------------------
Every audit so far checks whether the bars we HAVE are correct. None checks whether
a symbol has anyone responsible for it. Those are different failures, and the second
one is quieter: an orphaned symbol never errors, it just stops moving. INDIAVIX sat
on a Breeze path that had stopped writing daily bars and nobody noticed until the
staleness check happened to flag it.

So: read every symbol list the daily chain declares, read every symbol present in
price_bars, and diff them.

  COVERED   a daily job names this symbol
  ORPHANED  the symbol has bars but no job writes it — it will go stale silently
  DECLARED  a job names it but there are no bars — a fetch that has never worked

The middle one is the point. Anything listed there is a series slowly dying.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_FETCH = os.path.join(_ROOT, "data_agent", "fetching")
sys.path.insert(0, _FETCH)
sys.path.append(_ROOT)


def _list_literal(path, var):
    """Pull a top-level list literal out of a script without importing it."""
    try:
        src = open(path).read()
    except OSError:
        return []
    m = re.search(rf"^{var}\s*=\s*\[(.*?)\]", src, re.S | re.M)
    if not m:
        return []
    out = []
    for tok in m.group(1).replace("\n", " ").split(","):
        tok = tok.strip().strip("'\"")
        if tok and not tok.startswith("#"):
            out.append(tok)
    return out


def _dict_keys(path, var):
    try:
        src = open(path).read()
    except OSError:
        return []
    m = re.search(rf"^{var}\s*=\s*\{{(.*?)^\}}", src, re.S | re.M)
    if not m:
        return []
    return re.findall(r'"([A-Z0-9_&\-]+)"\s*:', m.group(1))


def declared():
    """{symbol: [jobs that write it]} — read from the scripts, not assumed."""
    owners = {}

    def add(syms, job):
        for s in syms:
            owners.setdefault(s.upper(), []).append(job)

    csv_path = os.path.join(_ROOT, "nifty-50-stock-list.csv")
    try:
        with open(csv_path, newline="") as f:
            add([r["Symbol"].strip() for r in csv.DictReader(f) if r.get("Symbol")],
                "sync_nifty50_bars_yf")
    except OSError:
        pass
    add(["NIFTY"], "sync_nifty50_bars_yf")

    try:
        from daily_bars import INDEX_TICKERS
        add([s for s in INDEX_TICKERS if s.startswith("NIFTY") and s != "NIFTY"],
            "sync_sectors_yf")
        add(["BANKNIFTY", "INDIAVIX"], "sync_sectors_yf")
    except Exception:
        pass

    add(_list_literal(os.path.join(_FETCH, "sync_bank_bars_yf.py"), "BANKS"),
        "sync_bank_bars_yf")
    add(_list_literal(os.path.join(_FETCH, "sync_it_bars_yf.py"), "IT_STOCKS"),
        "sync_it_bars_yf")
    add(_list_literal(os.path.join(_FETCH, "sync_finnifty_bars_yf.py"), "FINNIFTY"),
        "sync_finnifty_bars_yf")
    add(["CRUDEOIL"], "sync_crudeoil_yf")
    add(_dict_keys(os.path.join(_FETCH, "sync_commodities.py"), "SYMBOLS_MAP"),
        "sync_commodities")
    # Breeze, from backend/main.py — futures daily, everything else is 1m there.
    add(["NIFTY_FUT_1", "NIFTY_FUT_2"], "sync_nifty50_to_now (Breeze, futures)")
    return owners


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    db = args.db
    if not db:
        from bar_store import DB_PATH
        db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)

    owners = declared()
    con = sqlite3.connect(db)
    present = {r[0]: (r[1], r[2][:10]) for r in con.execute(
        "select symbol, count(*), max(ts) from price_bars where timeframe='1d' "
        "group by 1")}
    newest = max(v[1] for v in present.values()) if present else "-"
    con.close()

    covered = sorted(s for s in present if s in owners)
    orphaned = sorted(s for s in present if s not in owners)
    declared_only = sorted(s for s in owners if s not in present)

    print(f"database: {db}")
    print(f"freshest daily bar anywhere: {newest}\n")
    print(f"COVERED   {len(covered):>3} symbols have a daily job")
    print(f"ORPHANED  {len(orphaned):>3} symbols have bars but NO job writes them")
    print(f"DECLARED  {len(declared_only):>3} symbols are named by a job but have no bars\n")

    if orphaned:
        print("ORPHANED — these will go stale silently, nothing refreshes them:")
        for s in orphaned:
            n, last = present[s]
            print(f"   {s:16}{n:>6} bars   last {last}")
        print()
    if declared_only:
        print("DECLARED but empty — a fetch that has never produced a bar:")
        for s in declared_only:
            print(f"   {s:16} named by {', '.join(owners[s])}")
        print()

    dupes = {s: j for s, j in owners.items() if len(j) > 1 and s in present}
    if dupes:
        print("MULTIPLE OWNERS — two jobs write the same symbol; last run wins:")
        for s, j in sorted(dupes.items()):
            print(f"   {s:16} {', '.join(j)}")
        print()

    if not orphaned and not declared_only and not dupes:
        print("Every symbol has exactly one owner and every owner produces bars.")


if __name__ == "__main__":
    main()
