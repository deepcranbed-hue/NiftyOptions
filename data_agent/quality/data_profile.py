"""data_profile.py — what is each series actually fit for?

A DIFFERENT QUESTION FROM THE AUDIT
-----------------------------------
`daily_bar_audit.py` asks "is this corrupt" — wrong timestamps, forked exchanges,
duplicate dates, scale breaks. It answered PASS all through the period when
CRUDEOIL_MCX's first 26 daily bars were exchange marks on an untraded contract, and
when SILVER's 40% one-day "crash" was a 3-lot print next to an 8-lot print. Nothing
was corrupt. The data was simply not what anyone assumed it was.

So this asks the other question: given what is actually in here, what analysis can
this series support? It reports and grades; it never writes.

WHAT IT MEASURES, AND WHY EACH ONE EARNED ITS PLACE
---------------------------------------------------
  bars, span        Length. 46 daily bars is a basis series, not a backtest.
  traded %          Fraction with real volume. GOLD's 1m is 46% untraded; MCX daily
                    carries the previous price forward on days with no trade, and a
                    return computed across two marks is noise.
  thin %            Bars that traded, but barely. Silver's phantom crash lived
                    entirely in prints under 10 lots.
  worst gap         Longest run of missing sessions — tells you whether a "5 year"
                    series is really continuous.
  verdict           BACKTEST / ANALYSIS / BASIS ONLY / TOO SHORT.

CROSS-CHECK
-----------
For anything with both an Indian and an international series, it correlates their
daily returns on genuinely traded bars. That single number caught more than every
other check combined: +0.558 across all bars, +0.985 once restricted to bars that
traded. If a pair ever drifts below about 0.9 on traded bars, something is wrong
with the instrument mapping — not with the market.
"""
from __future__ import annotations

import os
import sqlite3
import statistics as st
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "data_agent", "fetching"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# (indian_series, international_series, min_correlation, why_that_number)
#
# ONE THRESHOLD FOR EVERYTHING WOULD BE WRONG IN BOTH DIRECTIONS. Copper measured
# +0.880 on 2026-08-09 while the others sat at 0.93-0.99. Levels reconciled fine —
# average basis +1.1% with 2.1% standard deviation — so the data is sound; the
# returns are just noisier. Two plausible reasons, neither a defect: MCX copper
# references LME while COPPER_USD is COMEX, and MCX copper trades thinly (16 of 108
# daily bars under 10 lots, against 94 clean bars for gold).
#
# Loosening the global bar to 0.85 to quiet copper would have blinded the check on
# gold, where 0.88 WOULD mean something is wrong. So each pair carries the number it
# has earned, with the reason attached.
#
# These are expectations, not measurements. Revisit them when a pair has a year of
# traded bars rather than two months.
PAIRS = [
    ("CRUDEOIL_MCX", "CRUDEOIL", 0.90,
     "MCX crude is WTI-linked; levels reconcile to -0.3%"),
    ("GOLD", "GOLD_USD", 0.90, "bullion tracks COMEX closely"),
    ("SILVER", "SILVER_USD", 0.90, "bullion tracks COMEX closely"),
    ("COPPER", "COPPER_USD", 0.82,
     "MCX copper references LME, not COMEX; and it trades thin"),
]

THIN_LOTS = 10          # a daily bar at or below this traded, but not meaningfully
MIN_BACKTEST_BARS = 500  # ~2 years of sessions


def _rows(con, sym, tf):
    return con.execute(
        "select ts, close, volume from price_bars where symbol=? and timeframe=? "
        "order by ts", (sym, tf)).fetchall()


def _worst_gap(dates):
    """Longest run of consecutive weekdays with no bar. Crude but source-agnostic."""
    from datetime import date, timedelta
    if len(dates) < 2:
        return 0
    ds = sorted(date.fromisoformat(d) for d in dates)
    worst = 0
    for a, b in zip(ds, ds[1:]):
        missing = sum(1 for i in range(1, (b - a).days)
                      if (a + timedelta(days=i)).weekday() < 5)
        worst = max(worst, missing)
    return worst


def profile(con, sym, tf):
    rows = _rows(con, sym, tf)
    if not rows:
        return None
    vols = [(r[2] or 0) for r in rows]
    traded = sum(1 for v in vols if v > 0)
    thin = sum(1 for v in vols if 0 < v <= THIN_LOTS)
    known_vol = sum(1 for r in rows if r[2] is not None)
    d = {
        "symbol": sym, "timeframe": tf, "bars": len(rows),
        "first": rows[0][0][:10], "last": rows[-1][0][:10],
        "traded_pct": 100.0 * traded / len(rows) if known_vol else None,
        "thin_pct": 100.0 * thin / len(rows) if known_vol else None,
        "worst_gap": _worst_gap({r[0][:10] for r in rows}) if tf == "1d" else None,
    }
    usable = traded - thin if known_vol else len(rows)
    if tf == "1d":
        if usable >= MIN_BACKTEST_BARS:
            d["verdict"] = "BACKTEST"
        elif usable >= 120:
            d["verdict"] = "ANALYSIS"
        elif usable >= 20:
            d["verdict"] = "BASIS ONLY"
        else:
            d["verdict"] = "TOO SHORT"
    else:
        d["verdict"] = "INTRADAY" if usable > 5000 else "TOO SHORT"
    return d


def traded_return_corr(con, a, b, min_vol=THIN_LOTS):
    """Daily-return correlation on bars where BOTH sides genuinely traded."""
    def ser(sym):
        return {t[:10]: (c, v or 0) for t, c, v in _rows(con, sym, "1d")}
    A, B = ser(a), ser(b)
    days = sorted(set(A) & set(B))
    # An international continuous series has no meaningful per-bar volume filter;
    # only require the Indian side to have traded, since that is the doubtful one.
    keep = [d for d in days if A[d][1] > min_vol]
    if len(keep) < 15:
        return None, len(keep)
    ra, rb = [], []
    for x, y in zip(keep, keep[1:]):
        if A[x][0] and B[x][0]:
            ra.append(A[y][0] / A[x][0] - 1)
            rb.append(B[y][0] / B[x][0] - 1)
    if len(ra) < 15:
        return None, len(ra)
    ma, mb = st.mean(ra), st.mean(rb)
    cov = sum((p - ma) * (q - mb) for p, q in zip(ra, rb)) / len(ra)
    sa, sb = st.pstdev(ra), st.pstdev(rb)
    return (cov / (sa * sb) if sa and sb else None), len(ra)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--symbols", default=None, help="comma list; default is all")
    ap.add_argument("--contracts", action="store_true",
                    help="include per-contract series (hidden by default)")
    args = ap.parse_args()
    db = args.db
    if not db:
        from bar_store import DB_PATH
        db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    con = sqlite3.connect(db)
    print(f"database: {db}\n")

    try:
        from continuous import parse_contract
    except ImportError:
        def parse_contract(_):
            return None

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        syms = sorted(r[0] for r in con.execute(
            "select distinct symbol from price_bars"))
        if not args.contracts:
            syms = [s for s in syms if not parse_contract(s)]

    print(f"{'symbol':<18}{'tf':<4}{'bars':>8}  {'span':<24}"
          f"{'traded':>8}{'thin':>7}{'gap':>6}  verdict")
    print("-" * 92)
    for sym in syms:
        for tf in ("1d", "1m"):
            p = profile(con, sym, tf)
            if not p:
                continue
            t = f"{p['traded_pct']:.0f}%" if p["traded_pct"] is not None else "-"
            th = f"{p['thin_pct']:.0f}%" if p["thin_pct"] is not None else "-"
            g = str(p["worst_gap"]) if p["worst_gap"] is not None else "-"
            print(f"{sym:<18}{tf:<4}{p['bars']:>8,}  {p['first']} .. {p['last']}  "
                  f"{t:>8}{th:>7}{g:>6}  {p['verdict']}")

    print("\ncross-venue check — daily-return correlation on TRADED bars only")
    print("(each pair has its own floor — see PAIRS for why)")
    for ind, intl, floor, why in PAIRS:
        r, n = traded_return_corr(con, ind, intl)
        if r is None:
            print(f"   {ind:<16} vs {intl:<12} not enough overlapping traded bars ({n})")
            continue
        flag = "" if r >= floor else "   <-- INVESTIGATE"
        print(f"   {ind:<16} vs {intl:<12} {r:+.3f}  over {n:>3} days   "
              f"(floor {floor:.2f}){flag}")
        if r < floor:
            print(f"      expected >= {floor:.2f} because: {why}")
    con.close()


if __name__ == "__main__":
    main()
