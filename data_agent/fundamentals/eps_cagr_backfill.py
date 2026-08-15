#!/usr/bin/env python3
"""eps_cagr_backfill — normalised multi-year EPS growth per Nifty 50 name -> eps_history.json.

WHY
The Expectation Gap currently compares embedded growth (what the price assumes) against
ONE year-on-year quarter of delivered growth. A single quarter is noise: Dr Reddy's
prints -69%, JSW Steel +113%, and those two readings drive the whole ranking. What the
gap actually wants on the other side is a NORMALISED rate — how fast earnings have
compounded over several years, through a cycle rather than at a point in it.

    CAGR = (EPS_end / EPS_start) ** (1 / years) - 1

THE UNITS TRAP, which matters more than the arithmetic:
Embedded growth from trailing/forward P/E is a TOTAL change between two EPS figures,
not an annual rate. A CAGR is per year. Comparing "+103% embedded" against "+15% CAGR"
subtracts a total from a rate and overstates the gap. This file therefore reports the
CAGR *and* the horizon it covers, so the consumer can annualise the embedded figure
before differencing. The scan does exactly that.

WHAT IT WRITES  (same convention as earnings_reactions.json / pe_history.json)
    {"as_of", "source", "note", "history": {SYMBOL: {
        "cagr_3y_pct": 14.5, "cagr_5y_pct": 11.2,
        "eps_start": 10.1, "eps_end": 15.2, "years": 3.0,
        "n_years": 4, "first": "2023-03-31", "last": "2026-03-31",
        "series": [{"period": "2023-03-31", "eps": 10.1}, ...],
        "sign_change": false, "source": "postgres"}}}

GUARDS
  * A CAGR needs BOTH endpoints positive. Loss years make it undefined, not negative —
    a swing from -5 to +10 is not "growth of X%", and raising a negative to a
    fractional power is a complex number. Those names get cagr=None and sign_change=true.
  * Fewer than _MIN_YEARS usable observations -> omitted entirely rather than shipped
    with a two-point "trend".

SOURCES, in order
  1. Postgres `fundamentals.financials` (annual EPS), the same table pe_history_backfill
     uses. Column names are probed at runtime because this schema is still settling.
  2. yfinance annual income statement / shares outstanding as a fallback.

USAGE
    DATABASE_URL=... python -m data_agent.fundamentals.eps_cagr_backfill
    ... --dry-run          # print coverage, write nothing
    ... --probe            # show what columns fundamentals.financials actually has
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from credentials import load_dotenv
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_CSV_PATH = os.path.join(_REPO_ROOT, "nifty-50-stock-list.csv")
_OUT_PATH = os.path.join(_REPO_ROOT, "eps_history.json")

_MIN_YEARS = 3          # need at least 3 observations for a 2-year span
_EPS_FLOOR = 0.01       # below this the base is too small for a meaningful ratio


def _symbols() -> list[str]:
    with open(_CSV_PATH, newline="") as f:
        return [(r.get("Symbol") or "").strip()
                for r in csv.DictReader(f) if (r.get("Symbol") or "").strip()]


def _cagr(eps_start: float, eps_end: float, years: float):
    """Annualised growth, or None when the ratio is undefined.

    Both endpoints must be positive: a move from a loss to a profit is a turnaround,
    not a growth rate, and (negative)**(1/n) is not a real number.
    """
    if years <= 0 or eps_start is None or eps_end is None:
        return None
    if eps_start < _EPS_FLOOR or eps_end < _EPS_FLOOR:
        return None
    try:
        return round(((eps_end / eps_start) ** (1.0 / years) - 1.0) * 100.0, 1)
    except Exception:
        return None


def _summarise(series: list[tuple[str, float]]) -> dict | None:
    """series = [(iso_date, eps)] oldest first."""
    if len(series) < _MIN_YEARS:
        return None
    d0, d1 = date.fromisoformat(series[0][0]), date.fromisoformat(series[-1][0])
    span = (d1 - d0).days / 365.25
    if span <= 0:
        return None
    sign_change = any(e < _EPS_FLOOR for _, e in series)

    def window(n_years: float):
        """CAGR over the last n_years of the series, if it reaches back that far."""
        target = d1.toordinal() - n_years * 365.25
        older = [(dt, e) for dt, e in series
                 if date.fromisoformat(dt).toordinal() <= target + 200]
        if not older:
            return None, None
        st_d, st_e = older[-1]
        yrs = (d1 - date.fromisoformat(st_d)).days / 365.25
        return _cagr(st_e, series[-1][1], yrs), round(yrs, 1)

    c3, y3 = window(3.0)
    c5, y5 = window(5.0)
    return {
        "cagr_3y_pct": c3, "cagr_3y_years": y3,
        "cagr_5y_pct": c5, "cagr_5y_years": y5,
        "cagr_full_pct": _cagr(series[0][1], series[-1][1], span),
        "eps_start": round(series[0][1], 2), "eps_end": round(series[-1][1], 2),
        "years": round(span, 1), "n_years": len(series),
        "first": series[0][0], "last": series[-1][0],
        "sign_change": sign_change,
        # Full series, not the last 6. n_years already reports the true length, so a
        # 6-point slice made a 10-year history look like a 6-year one to every
        # consumer — including the multi-year bar chart this feeds.
        "series": [{"period": d, "eps": round(e, 2)} for d, e in series],
    }


# ── source 1: Postgres fundamentals.financials ──────────────────────────────
_EPS_LABELS = ["diluted eps", "basic eps", "eps", "earnings per share",
               "eps (diluted)", "eps (basic)", "diluted eps (rs)",
               "eps - diluted", "eps - basic"]


def _probe(conn) -> None:
    for q, label in (
        ("SELECT column_name FROM information_schema.columns "
         "WHERE table_schema='fundamentals' AND table_name='financials' "
         "ORDER BY ordinal_position", "columns in fundamentals.financials"),
        ("SELECT DISTINCT line_item FROM fundamentals.financials "
         "WHERE line_item ILIKE '%eps%' OR line_item ILIKE '%earnings per%' LIMIT 30",
         "EPS-like line_item values"),
        ("SELECT DISTINCT time_period FROM fundamentals.financials LIMIT 10",
         "time_period values"),
    ):
        print(f"\n--- {label} ---")
        try:
            for r in conn.execute(q).fetchall():
                print("   ", r[0])
        except Exception as e:
            print(f"    query failed: {type(e).__name__}: {e}")


def _from_postgres(symbols: list[str], probe: bool) -> dict:
    out: dict = {}
    try:
        import psycopg
    except Exception:
        print("  psycopg 3 not installed — skipping Postgres", file=sys.stderr)
        return out
    dsn = os.getenv("DATABASE_URL")
    try:
        conn = psycopg.connect(dsn) if dsn else psycopg.connect()
    except Exception as e:
        print(f"  Postgres unreachable ({type(e).__name__}: {e}) — skipping", file=sys.stderr)
        return out
    with conn:
        if probe:
            _probe(conn)
            return out
        for sym in symbols:
            try:
                rows = conn.execute(
                    """
                    SELECT f.period_end, f.value, f.basis
                      FROM fundamentals.financials f
                      JOIN fundamentals.companies c ON c.isin = f.isin
                     WHERE c.symbol = %s
                       AND LOWER(TRIM(f.line_item)) = ANY(%s)
                       AND f.value IS NOT NULL
                       -- WITHOUT THIS the June-quarter row joins the annual series.
                       -- Indian IT reports Q1 in July, so by August TCS/INFY/HCLTECH/
                       -- TECHM/WIPRO each had a quarterly EPS (TCS Rs 32.70) sitting
                       -- next to a full year (Rs 136.01). Every IT major then showed a
                       -- 3-year EPS CAGR near -32 percent. The number is not small, it is a
                       -- quarter being compounded as if it were a year.
                       AND f.time_period = 'yearly'
                     ORDER BY f.period_end,
                       -- Deterministic basis: consolidated is the group's real economics
                       -- and is what the multiple is quoted against. Without this ORDER,
                       -- a period with both bases kept whichever row the planner emitted
                       -- last, so the same query returned different EPS between runs.
                       CASE WHEN f.basis = 'consolidated' THEN 0 ELSE 1 END
                    """,
                    (sym, _EPS_LABELS),
                ).fetchall()
            except Exception as e:
                print(f"  [-] Query failed for {sym}: {type(e).__name__}: {e}")
                continue
            # Collapse to one observation per period_end (annual), newest wins.
            byd: dict = {}
            for pe, v, _basis in rows:
                if pe is None or v is None:
                    continue
                d = pe.isoformat() if hasattr(pe, "isoformat") else str(pe)[:10]
                # setdefault, not assignment: the ORDER BY above puts consolidated
                # first, so the first row for a period wins and standalone only fills
                # a genuine gap. Assignment would let the last row win and undo it.
                byd.setdefault(d, float(v))
            series = sorted(byd.items())
            s = _summarise(series)
            if s:
                s["source"] = "postgres"
                out[sym] = s
    return out


# ── source 2: yfinance annual income statement ──────────────────────────────
def _from_yfinance(symbols: list[str], have: set) -> dict:
    out: dict = {}
    try:
        import yfinance as yf
    except Exception:
        print("  yfinance unavailable — skipping fallback", file=sys.stderr)
        return out
    for sym in symbols:
        if sym in have:
            continue
        try:
            t = yf.Ticker(f"{sym}.NS")
            stmt = t.income_stmt
            if stmt is None or stmt.empty:
                continue
            label = next((k for k in ("Diluted EPS", "Basic EPS") if k in stmt.index), None)
            series = []
            if label:
                for col in sorted(stmt.columns):
                    v = stmt.loc[label, col]
                    if v == v and v is not None:          # NaN-safe
                        series.append((str(col.date()), float(v)))
            else:
                ni = next((k for k in ("Net Income", "Net Income Common Stockholders")
                           if k in stmt.index), None)
                shares = (t.info or {}).get("sharesOutstanding")
                if not ni or not shares:
                    continue
                for col in sorted(stmt.columns):
                    v = stmt.loc[ni, col]
                    if v == v and v is not None:
                        series.append((str(col.date()), float(v) / float(shares)))
            s = _summarise(series)
            if s:
                s["source"] = "yfinance"
                out[sym] = s
        except Exception:
            continue
    return out


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=["all", "pg", "yf"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="show fundamentals.financials columns and EPS line_item values, then exit")
    args = ap.parse_args()

    symbols = _symbols()
    if args.probe:
        _from_postgres(symbols, probe=True)
        return 0

    print(f"Nifty 50 constituents: {len(symbols)}")
    history: dict = {}
    if args.source in ("all", "pg"):
        history.update(_from_postgres(symbols, probe=False))
        print(f"  from Postgres : {len(history)}")
    if args.source in ("all", "yf"):
        before = len(history)
        history.update(_from_yfinance(symbols, set(history)))
        print(f"  from yfinance : {len(history) - before}")

    with3 = sum(1 for v in history.values() if v.get("cagr_3y_pct") is not None)
    undef = [k for k, v in history.items() if v.get("cagr_3y_pct") is None]
    print(f"Covered {len(history)}/{len(symbols)}; {with3} have a usable 3-year CAGR")
    if undef:
        print(f"  CAGR undefined (loss year or sub-floor EPS): {', '.join(sorted(undef))}")
    missing = [s for s in symbols if s not in history]
    if missing:
        print(f"  no EPS history: {', '.join(missing)}")

    if args.dry_run:
        print("dry run — nothing written")
        return 0
    if not history:
        print("nothing to write — leaving any existing file untouched")
        return 1

    doc = {
        "as_of": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "fundamentals.financials (annual EPS), yfinance fallback",
        "note": ("Normalised EPS growth: compound annual rate across reported annual EPS. "
                 "A CAGR is a PER-YEAR rate, whereas embedded growth from trailing/forward "
                 "P/E is a TOTAL change between two EPS figures — annualise the embedded "
                 "number before differencing them or the gap is overstated. A CAGR requires "
                 "both endpoints positive: names with a loss year carry cagr=None and "
                 f"sign_change=true rather than a fabricated rate. Fewer than {_MIN_YEARS} "
                 "annual observations are omitted entirely."),
        "history": history,
    }
    with open(_OUT_PATH, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"wrote {_OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
