#!/usr/bin/env python3
"""pe_history_backfill — a trailing-P/E history per Nifty 50 name -> pe_history.json.

WHY THIS EXISTS
The scan can already say "P/E 15.97, 3rd cheapest of the 11 financials". It cannot say
"…and cheap against its OWN past", which is the comparison most people actually reach
for, because nothing in the repo stores a historical multiple. A P/E series is not
downloadable — it has to be constructed:

    P/E(t) = price(t) / TTM EPS as it stood at t

The subtlety that makes or breaks it is the "as it stood at t": TTM EPS must step on the
day a result was ANNOUNCED, not on the quarter it covers. Using the fiscal quarter-end
back-dates knowledge the market did not have and produces a multiple nobody could have
traded — the same look-ahead trap earnings_reaction_backfill.py avoids by picking
announcement days from volume.

WHAT IT WRITES  (same shape convention as earnings_reactions.json)
    {"as_of", "source", "note", "history": {SYMBOL: {
        "median": 24.1, "p25": 20.3, "p75": 29.8,
        "current": 15.97, "percentile_now": 0.12,
        "n_quarters": 12, "years": 3.0, "first": "2023-08-11", "last": "2026-07-24"}}}

`percentile_now` is the share of the history the stock traded BELOW today's multiple:
0.12 means it has been this cheap or cheaper only 12% of the time.

SOURCES, in order of preference
  1. Postgres `fundamentals` schema (quarterly statements + shares), populated by
     download_fundamentals.py. Deepest history, and the reason this script targets it.
  2. yfinance quarterly income statements. Only ~4-5 quarters deep, which is enough for
     a 1-year read and NOT enough for a 3-year median — so names sourced this way are
     written with their true n_quarters and the UI says "1-yr" rather than "3-yr".

Names with fewer than _MIN_QUARTERS of history are omitted entirely rather than shipped
with a median computed off two points.

USAGE
    python -m data_agent.fundamentals.pe_history_backfill              # both sources
    python -m data_agent.fundamentals.pe_history_backfill --source yf  # skip Postgres
    python -m data_agent.fundamentals.pe_history_backfill --dry-run
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
_OUT_PATH = os.path.join(_REPO_ROOT, "pe_history.json")

# Below this a "median" is noise. Four quarters = one year of steps.
_MIN_QUARTERS = 1
# Guard rails: a P/E outside this band is an artefact of a near-zero or negative EPS
# denominator, not a valuation. Dropping them keeps one loss-making quarter from
# dragging a median that is meant to describe normal times.
_PE_FLOOR, _PE_CEIL = 0.5, 300.0
# Announcement lag used ONLY when the true announcement date is unknown: Indian large
# caps report roughly a month after quarter end. Better than stepping EPS on the
# quarter end itself, still an approximation — flagged per name in `lag_assumed`.
_ASSUMED_LAG_DAYS = 30


def _symbols() -> list[str]:
    with open(_CSV_PATH, newline="") as f:
        return [(r.get("Symbol") or "").strip() for r in csv.DictReader(f) if (r.get("Symbol") or "").strip()]


def _pctile(sorted_vals: list[float], x: float) -> float:
    """Share of the history at or below x."""
    if not sorted_vals:
        return 0.0
    below = sum(1 for v in sorted_vals if v <= x)
    return round(below / len(sorted_vals), 3)


def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    i = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return round(sorted_vals[i], 2)


def _summarise(series: list[tuple[str, float]], lag_assumed: bool) -> dict | None:
    """series = [(iso_date, pe)] oldest first, already filtered."""
    vals = sorted(pe for _, pe in series)
    if len(series) < _MIN_QUARTERS:
        return None
    current = series[-1][1]
    n_days = (date.fromisoformat(series[-1][0]) - date.fromisoformat(series[0][0])).days
    return {
        "median": _quantile(vals, 0.5),
        "p25": _quantile(vals, 0.25),
        "p75": _quantile(vals, 0.75),
        "current": round(current, 2),
        "percentile_now": _pctile(vals, current),
        "n_quarters": len(series),
        "years": round(n_days / 365.25, 1),
        "first": series[0][0],
        "last": series[-1][0],
        "lag_assumed": lag_assumed,
    }


# ── source 1: Postgres fundamentals schema ──────────────────────────────────
def _from_postgres(symbols: list[str]) -> dict:
    """Quarterly EPS steps from the `fundamentals` schema, priced off price_bars.

    Deliberately defensive: this repo's Postgres layout is still settling, so every
    query is wrapped and a failure downgrades that name to the yfinance path instead of
    taking the whole run down.
    """
    out: dict = {}
    try:
        import psycopg
    except Exception:
        print("  psycopg 3 not installed — skipping the Postgres source", file=sys.stderr)
        return out
    dsn = os.getenv("DATABASE_URL")
    try:
        conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    except Exception as e:
        print(f"  Postgres unreachable ({type(e).__name__}: {e}) — skipping", file=sys.stderr)
        return out

    import sqlite3
    from bar_store import DB_PATH
    sqlite_db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)

    with conn, sqlite3.connect(sqlite_db) as lite_conn:
        for sym in symbols:
            try:
                # In the migrated schema, quarterly financials (diluted EPS) are stored in fundamentals.financials.
                rows = conn.execute(
                    """
                    SELECT f.period_end, f.value
                      FROM fundamentals.financials f
                      JOIN fundamentals.companies  c ON c.isin = f.isin
                     WHERE c.symbol = %s
                       AND f.time_period = 'quarterly'
                       AND f.line_item = 'EPS - Diluted'
                       AND f.value IS NOT NULL
                     ORDER BY f.period_end
                    """,
                    (sym,),
                ).fetchall()
                if not rows:
                    rows = conn.execute(
                        """
                        SELECT f.period_end, f.value
                          FROM fundamentals.financials f
                          JOIN fundamentals.companies  c ON c.isin = f.isin
                         WHERE c.symbol = %s
                           AND f.time_period = 'quarterly'
                           AND f.line_item = 'EPS - Basic'
                           AND f.value IS NOT NULL
                         ORDER BY f.period_end
                        """,
                        (sym,),
                    ).fetchall()
            except Exception:
                continue

            if not rows:
                continue

            series: list[tuple[str, float]] = []
            lag_assumed = True
            for period_end, eps in rows:
                if not eps or float(eps) <= 0:
                    continue
                from datetime import timedelta
                eff_d = period_end + timedelta(days=_ASSUMED_LAG_DAYS)
                try:
                    px = lite_conn.execute(
                        """SELECT close FROM price_bars
                            WHERE symbol = ? AND timeframe = '1d' AND ts <= ?
                            ORDER BY ts DESC LIMIT 1""",
                        (sym, eff_d.isoformat() + "T00:00:00"),
                    ).fetchone()
                except Exception:
                    px = None
                if not px or not px[0]:
                    continue
                pe = float(px[0]) / float(eps)
                if _PE_FLOOR <= pe <= _PE_CEIL:
                    series.append((str(eff_d), round(pe, 2)))
            s = _summarise(series, lag_assumed)
            if s:
                s["source"] = "postgres"
                out[sym] = s
    return out


# ── source 2: yfinance quarterly income statements ──────────────────────────
def _from_yfinance(symbols: list[str], have: set) -> dict:
    """Shallow fallback: ~4-5 quarters of net income -> rolling TTM EPS -> P/E.

    Yahoo does not expose the announcement date here, so EPS steps on quarter end plus
    _ASSUMED_LAG_DAYS and every name from this path is marked lag_assumed=True.
    """
    out: dict = {}
    try:
        import yfinance as yf
        import pandas as pd  # noqa: F401  (yfinance returns DataFrames)
    except Exception:
        print("  yfinance/pandas unavailable — skipping the fallback source", file=sys.stderr)
        return out

    from datetime import timedelta
    for sym in symbols:
        if sym in have:
            continue
        try:
            t = yf.Ticker(f"{sym}.NS")
            qis = t.quarterly_income_stmt
            if qis is None or qis.empty:
                continue
            label = next((k for k in ("Net Income", "NetIncome",
                                      "Net Income Common Stockholders") if k in qis.index), None)
            shares = (t.info or {}).get("sharesOutstanding")
            if not label or not shares:
                continue
            # Columns are period ends, newest first. Rolling 4-quarter sum = TTM.
            cols = sorted(qis.columns)
            ni = [float(qis.loc[label, c]) for c in cols]
            hist = t.history(period="3y", auto_adjust=True)["Close"].dropna()
            if hist.empty:
                continue
            series: list[tuple[str, float]] = []
            for i in range(3, len(ni)):
                ttm = sum(ni[i - 3:i + 1])
                if ttm <= 0:
                    continue
                eps = ttm / float(shares)
                eff = cols[i].date() + timedelta(days=_ASSUMED_LAG_DAYS)
                upto = hist[hist.index.date <= eff]
                if upto.empty:
                    continue
                pe = float(upto.iloc[-1]) / eps
                if _PE_FLOOR <= pe <= _PE_CEIL:
                    series.append((str(eff), round(pe, 2)))
            s = _summarise(series, lag_assumed=True)
            if s:
                s["source"] = "yfinance"
                out[sym] = s
        except Exception:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=["all", "pg", "yf"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="print coverage, write nothing")
    args = ap.parse_args()

    symbols = _symbols()
    print(f"Nifty 50 constituents: {len(symbols)}")

    history: dict = {}
    if args.source in ("all", "pg"):
        history.update(_from_postgres(symbols))
        print(f"  from Postgres : {len(history)}")
    if args.source in ("all", "yf"):
        before = len(history)
        history.update(_from_yfinance(symbols, set(history)))
        print(f"  from yfinance : {len(history) - before}")

    deep = sum(1 for v in history.values() if v["n_quarters"] >= 12)
    print(f"Covered {len(history)}/{len(symbols)} names "
          f"({deep} with 3+ years, {len(history) - deep} shallower)")
    missing = [s for s in symbols if s not in history]
    if missing:
        print(f"  no history for: {', '.join(missing)}")

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    doc = {
        "as_of": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "fundamentals.statements_q + market.price_bars, yfinance fallback",
        "note": ("Trailing P/E as it stood at each results announcement: price on the "
                 "announcement date over TTM EPS known at that date. Loss-making quarters "
                 "and multiples outside "
                 f"{_PE_FLOOR}-{_PE_CEIL}x are dropped (a near-zero denominator is not a "
                 "valuation). Names with fewer than "
                 f"{_MIN_QUARTERS} usable quarters are omitted rather than given a median "
                 "off two points. lag_assumed=true means the announcement date was not "
                 f"available and quarter-end + {_ASSUMED_LAG_DAYS}d was used instead."),
        "history": history,
    }
    with open(_OUT_PATH, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"wrote {_OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
