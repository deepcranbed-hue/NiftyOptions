#!/usr/bin/env python3
"""fii_holding_backfill — per-stock FII/FPI shareholding -> fii_holdings.json.

WHY THIS IS SEPARATE FROM THE FLOW NUMBERS
The Nifty 50 view already carries index-level FII/DII cash flow (what foreign money did
across the market yesterday) and, when NSDL is reachable, sector-wise FPI. Neither tells
you anything about a SPECIFIC name. The stock-level question — "are foreigners adding to
or leaving THIS company?" — is answered by the quarterly shareholding pattern every
listed Indian company files, which is a stock, not a flow, and moves four times a year.

Keeping it in its own file makes that cadence honest: a holding figure is up to three
months stale by construction, and the UI labels the quarter it belongs to rather than
implying it is current.

WHAT IT WRITES  (same shape convention as earnings_reactions.json / pe_history.json)
    {"as_of", "source", "note", "holdings": {SYMBOL: {
        "latest_pct": 21.3, "period": "Jun 2026",
        "prev_pct": 22.1, "change_pp": -0.8,
        "change_4q_pp": -2.6, "direction": "trimming",
        "trend": [{"period": "Sep 2025", "pct": 23.9}, ...]}}}

`change_pp` is in PERCENTAGE POINTS, not percent: a move from 22.1% to 21.3% is -0.8pp,
which is a ~3.6% reduction in the stake. Conflating the two is the classic way to make a
small rebalance read as an exodus, so the field name carries the unit.

SOURCE
Postgres `fundamentals.shareholding` (isin, category, period_label, period_end, pct),
populated by download_fundamentals.py. Category naming varies by provider, so we match
case-insensitively against _FII_CATEGORIES and report which label matched per name — if
your provider spells it something else, add it there and nothing else changes.

There is deliberately NO scraping fallback. NSE's shareholding-pattern pages are
JS-rendered and rate-limited, and a half-working scraper that silently returns stale or
partial stakes is worse than an absent section: the UI simply hides the layer when this
file is missing.

USAGE
    python -m data_agent.fundamentals.fii_holding_backfill
    python -m data_agent.fundamentals.fii_holding_backfill --dry-run
    python -m data_agent.fundamentals.fii_holding_backfill --list-categories
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_CSV_PATH = os.path.join(_REPO_ROOT, "nifty-50-stock-list.csv")
_OUT_PATH = os.path.join(_REPO_ROOT, "fii_holdings.json")

# Matched case-insensitively, first hit wins. Providers spell this a dozen ways.
_FII_CATEGORIES = [
    "foreign institutional investors", "fii", "fpi",
    "foreign portfolio investors", "foreign portfolio investor",
    "foreign institutions", "foreign holding", "fii/fpi",
]

# A stake that moves less than this between filings is a rebalance, not a decision.
_FLAT_PP = 0.3
_MIN_QUARTERS = 2


def _symbols() -> list[str]:
    with open(_CSV_PATH, newline="") as f:
        return [(r.get("Symbol") or "").strip() for r in csv.DictReader(f) if (r.get("Symbol") or "").strip()]


def _connect():
    try:
        import psycopg
    except Exception:
        sys.exit('psycopg 3 is required: pip install "psycopg[binary]"')
    dsn = os.getenv("DATABASE_URL")
    try:
        return psycopg.connect(dsn) if dsn else psycopg.connect()
    except Exception as e:
        sys.exit(f"Postgres unreachable ({type(e).__name__}: {e}). "
                 "Set DATABASE_URL, or run this on the machine hosting the DB.")


def _list_categories(conn) -> None:
    rows = conn.execute(
        "SELECT category, COUNT(*) FROM fundamentals.shareholding "
        "GROUP BY category ORDER BY 2 DESC LIMIT 40").fetchall()
    print("categories present in fundamentals.shareholding:")
    for cat, n in rows:
        mark = " <-- matched as FII" if cat and cat.strip().lower() in _FII_CATEGORIES else ""
        print(f"  {n:>6}  {cat}{mark}")
    print("\nIf the FII line above is unmarked, add its exact spelling to _FII_CATEGORIES.")


def _direction(change_pp: float | None) -> str:
    if change_pp is None:
        return "unknown"
    if change_pp > _FLAT_PP:
        return "adding"
    if change_pp < -_FLAT_PP:
        return "trimming"
    return "flat"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print coverage, write nothing")
    ap.add_argument("--list-categories", action="store_true",
                    help="show the category labels your provider uses, then exit")
    args = ap.parse_args()

    conn = _connect()
    with conn:
        if args.list_categories:
            _list_categories(conn)
            return 0

        symbols = _symbols()
        holdings: dict = {}
        matched_labels: set = set()

        for sym in symbols:
            try:
                rows = conn.execute(
                    """
                    SELECT s.period_label, s.period_end, s.pct, s.category
                      FROM fundamentals.shareholding s
                      JOIN fundamentals.companies   x ON x.isin = s.isin
                     WHERE x.symbol = %s
                       AND LOWER(TRIM(s.category)) = ANY(%s)
                       AND s.pct IS NOT NULL
                     ORDER BY s.period_end
                    """,
                    (sym, _FII_CATEGORIES),
                ).fetchall()
            except Exception as e:
                print(f"  {sym}: query failed ({type(e).__name__})", file=sys.stderr)
                continue
            if len(rows) < _MIN_QUARTERS:
                continue

            trend = [{"period": (lbl or str(pe)), "pct": round(float(p), 2)}
                     for lbl, pe, p, _ in rows]
            matched_labels.update(r[3] for r in rows if r[3])
            latest, prev = trend[-1], trend[-2]
            change_pp = round(latest["pct"] - prev["pct"], 2)
            four_back = trend[-5] if len(trend) >= 5 else trend[0]
            holdings[sym] = {
                "latest_pct": latest["pct"],
                "period": latest["period"],
                "prev_pct": prev["pct"],
                "change_pp": change_pp,
                "change_4q_pp": round(latest["pct"] - four_back["pct"], 2),
                "quarters_span": len(trend),
                "direction": _direction(change_pp),
                "trend": trend[-8:],           # two years is plenty for a sparkline
            }

        print(f"Covered {len(holdings)}/{len(symbols)} names")
        if matched_labels:
            print(f"  matched category labels: {sorted(matched_labels)}")
        else:
            print("  NO rows matched. Run --list-categories to see what your provider calls it.")
        missing = [s for s in symbols if s not in holdings]
        if missing:
            print(f"  no FII holding for: {', '.join(missing)}")

        if args.dry_run:
            print("dry run — nothing written")
            return 0
        if not holdings:
            print("nothing to write — leaving any existing file untouched")
            return 1

        doc = {
            "as_of": date.today().isoformat(),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": "fundamentals.shareholding (quarterly filings)",
            "note": ("Quarterly FII/FPI shareholding, not a flow: it moves four times a year "
                     "and is up to a quarter stale by construction — the period label says "
                     "which filing it is. change_pp is in PERCENTAGE POINTS (22.1% -> 21.3% "
                     "is -0.8pp, a ~3.6% cut to the stake). Moves under "
                     f"{_FLAT_PP}pp are called flat rather than a direction."),
            "holdings": holdings,
        }
        with open(_OUT_PATH, "w") as f:
            json.dump(doc, f, indent=1)
        print(f"wrote {_OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
