"""daily_bar_audit.py — integrity checks for DAILY bars.

WHY A SEPARATE AUDIT
--------------------
data_health.py measures 1-MINUTE coverage and bar spacing: did we get enough bars
today, are they spaced as expected. Those are the right questions for intraday data
and the wrong ones for daily data — a daily series can be catastrophically wrong
while every count is perfect.

It was. Through every defect found in the 2026-08 pipeline rebuild, data_health
would have reported green:

  * TATAMOTORS held 2,126 bars — exactly matching its peers — with 427 of them
    dated on a weekend, because a UTC conversion moved every session back one day.
  * NIFTY held 22 sessions twice, once per vendor, under two ts spellings.
  * TRENT carried a 33% cliff on a day with no corporate action, which made its 1Y
    return read -42% instead of -13%.
  * Two symbols sat on a completely different price basis from the other 48.

None of that is a coverage problem, so none of it was visible. Below are the six
questions that would have caught it. Every one is here because it actually failed.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "data_agent", "fetching"))
sys.path.append(_ROOT)

CANON_TS = "T00:00:00"      # trading date, IST, no timezone conversion
GAP_FLAG = 0.15             # |1-day move| above this is a suspected corporate action
WEEKEND_TOLERANCE = 10      # NSE does run Muhurat and Budget-Saturday sessions
CALENDAR_MIN = 98.0         # % of reference sessions a healthy series must share
STALE_SESSIONS = 3


# A symbol can only be judged against a venue that keeps the same hours. MCX runs
# an evening session and trades some days NSE does not; NYMEX and GIFT Nifty follow
# US/global calendars. Comparing them to RELIANCE reports a permanent 84-96% overlap
# that means nothing — and a check that always fires is a check nobody reads.
REFERENCE_BY_EXCHANGE = {
    "NSE": "RELIANCE",
    "MCX": "GOLD",
    "NYMEX": "CRUDEOIL",
    "CDS": "USDINR",
    "NFO": "NIFTY_FUT_1",
}
CALENDAR_EXEMPT = {
    # Near-24h global session; no NSE-like trading calendar to compare against.
    "GIFTNIFTY",
    # MCX commodity series are SINGLE-CONTRACT, not continuous: they start when a
    # contract lists, end when it expires, and carry zero-volume prints in between.
    # Comparing their session set to another MCX symbol on a different contract cycle
    # measures the contract cycle, not data quality. Fixing this properly means
    # building continuous contracts — a real piece of work, not an audit tweak.
    "CRUDEOIL_MCX", "GOLD", "SILVER", "COPPER",
}

# Weekend rows are a defect for an exchange-session series and NORMAL for a 24/5 or
# global feed. Exempting is not the same as hiding: each entry names why.
WEEKEND_EXEMPT = {
    "USDINR": "Upstox GLOBAL_INDICATOR feed, not an NSE session series — FX quotes "
              "print outside Indian exchange hours",
    "GIFTNIFTY": "near-24h global session",
}

# Writers not yet migrated to the canonical daily format, with the reason. Anything
# NOT listed here that carries a foreign format is a real finding.
TS_FORMAT_EXEMPT = {
    "NIFTY_FUT_1": "Breeze futures path, deliberately deferred — futures storage needs "
                   "a per-contract vs rolling decision first",
    "NIFTY_FUT_2": "as NIFTY_FUT_1",
}


def _exchange_of(con, sym):
    r = con.execute("select exchange from price_bars where symbol=? and "
                    "timeframe='1d' limit 1", (sym,)).fetchone()
    return r[0] if r else "NSE"


def _reference_calendar(con, ref="RELIANCE"):
    return {r[0][:10] for r in con.execute(
        "select ts from price_bars where symbol=? and timeframe='1d'", (ref,))}


def audit(db, symbols=None, reference="RELIANCE"):
    """Returns (findings, stats). A finding is (symbol, check, detail)."""
    try:
        from daily_bars import VENDOR_ADJUSTMENTS, KNOWN_REAL_GAPS
        known = {(a["symbol"], a["boundary"]) for a in VENDOR_ADJUSTMENTS} | set(KNOWN_REAL_GAPS)
    except Exception:
        known = set()

    con = sqlite3.connect(db)
    if symbols is None:
        symbols = [r[0] for r in con.execute(
            "select distinct symbol from price_bars where timeframe='1d' order by 1")]
    ref_cal = _reference_calendar(con, reference)
    # Memoised: the per-venue peer lookup below runs once per symbol, and rebuilding
    # a calendar each time meant ~98 full scans of a 329MB table — the audit went
    # from seconds to over a minute the moment the sector indices gained 8 years.
    _cal_cache = {reference: ref_cal}

    def cal_for(peer):
        if peer not in _cal_cache:
            _cal_cache[peer] = _reference_calendar(con, peer)
        return _cal_cache[peer]

    findings = []

    exch_of = dict(con.execute(
        "select symbol, min(exchange) from price_bars where timeframe='1d' group by 1"))

    for sym in symbols:
        rows = [r[0] for r in con.execute(
            "select ts from price_bars where symbol=? and timeframe='1d'", (sym,))]
        if not rows:
            continue

        # 1. TIMESTAMP FORMAT. ts is part of the primary key, so two spellings of
        #    one session are two rows that never overwrite each other. This is the
        #    defect every other daily-bar problem grew out of.
        fmts = sorted({t[10:] for t in rows})
        if fmts != [CANON_TS] and sym not in TS_FORMAT_EXEMPT:
            findings.append((sym, "ts_format", f"{','.join(fmts)} (want {CANON_TS})"))

        # 2. WEEKEND DATES. A timezone-shifted series lands sessions on Sat/Sun.
        dates = {t[:10] for t in rows}
        wk = [d for d in dates if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5]
        if len(wk) > WEEKEND_TOLERANCE and sym not in WEEKEND_EXEMPT:
            findings.append((sym, "weekend_dates",
                             f"{len(wk)} sessions on Sat/Sun, e.g. {sorted(wk)[0]}"))

        # 3. CALENDAR OVERLAP. A uniform one-day shift still scores ~76%, because
        #    Tue-Fri land on other trading days and only Mondays fall out. The bar
        #    COUNT stays perfect, so this is the only test that sees it.
        peer = REFERENCE_BY_EXCHANGE.get(exch_of.get(sym, "NSE"), reference)
        if sym in CALENDAR_EXEMPT or sym == peer:
            continue
        peer_cal = cal_for(peer)
        win = {d for d in peer_cal if min(dates) <= d <= max(dates)}
        if win:
            ov = 100.0 * len(dates & win) / len(win)
            if ov < CALENDAR_MIN:
                findings.append((sym, "calendar", f"{ov:.1f}% overlap with {peer}"))

    # 3b. FORKED BY EXCHANGE. exchange is part of the primary key, so the same
    #     symbol stored under two exchanges is two parallel series that silently
    #     double every date. A hardcoded exchange="NSE" default did this to
    #     CRUDEOIL (NYMEX + NSE, 1,906 dates twice) and USDINR carries it too.
    for sym, n in con.execute(
            "select symbol, count(distinct exchange) c from price_bars "
            "where timeframe='1d' group by 1 having c>1"):
        exch = [f"{e}:{k}" for e, k in con.execute(
            "select exchange, count(*) from price_bars where symbol=? and "
            "timeframe='1d' group by 1", (sym,))]
        findings.append((sym, "forked_exchange", " + ".join(exch)))

    # 4. DUPLICATE SESSIONS. One trading date, two rows — anything joining on date
    #    double-counts. NIFTY had 22, and NIFTY is the reaction benchmark.
    for sym, n in con.execute(
            "select symbol, count(*) from (select symbol, substr(ts,1,10) d, count(*) c "
            "from price_bars where timeframe='1d' group by 1,2 having c>1) group by 1"):
        findings.append((sym, "duplicate_dates", f"{n} sessions stored twice"))

    # 5. PRICE CONTINUITY. An unexplained cliff means the stored history sits on a
    #    different scale from the new bars — a corporate action applied to one end
    #    only. Known breaks are suppressed so this stays signal, not daily noise.
    for sym in symbols:
        prev = None
        for ts, o, cl in con.execute(
                "select ts, open, close from price_bars where symbol=? and "
                "timeframe='1d' order by ts", (sym,)):
            if prev and o and prev > 0:
                r = o / prev
                if (r < 1 - GAP_FLAG or r > 1 + GAP_FLAG) and (sym, ts[:10]) not in known:
                    findings.append((sym, "price_gap", f"{ts[:10]} ratio {r:.4f}"))
            prev = cl

    # 6. STALENESS, measured against the freshest symbol in the table rather than
    #    the wall clock, so holidays and weekends never raise a false alarm.
    maxes = {s: m for s, m in con.execute(
        "select symbol, max(ts) from price_bars where timeframe='1d' group by 1")}
    if maxes:
        newest = max(v[:10] for v in maxes.values())
        for sym in symbols:
            if sym in maxes and maxes[sym][:10] < newest:
                behind = len({d for d in ref_cal if maxes[sym][:10] < d <= newest})
                if behind >= STALE_SESSIONS:
                    findings.append((sym, "stale",
                                     f"last {maxes[sym][:10]}, {behind} sessions behind"))
    con.close()
    return findings, {"symbols": len(symbols), "reference": reference}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--constituents-only", action="store_true",
                    help="limit to the Nifty 50 CSV + NIFTY")
    args = ap.parse_args()
    db = args.db
    if not db:
        from bar_store import DB_PATH
        db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)

    syms = None
    if args.constituents_only:
        with open(os.path.join(_ROOT, "nifty-50-stock-list.csv"), newline="") as f:
            syms = [r["Symbol"].strip() for r in csv.DictReader(f) if r.get("Symbol")]
        syms.append("NIFTY")

    findings, stats = audit(db, syms)
    print(f"daily-bar audit — {stats['symbols']} symbols, reference {stats['reference']}")
    if TS_FORMAT_EXEMPT or WEEKEND_EXEMPT or CALENDAR_EXEMPT:
        print(f"exempt: {len(TS_FORMAT_EXEMPT)} ts-format, {len(WEEKEND_EXEMPT)} weekend, "
              f"{len(CALENDAR_EXEMPT)} calendar (each with a documented reason in-file)")
    print(f"database: {db}\n")
    if not findings:
        print("PASS — no integrity problems found.")
        return 0
    by_check = {}
    for sym, check, detail in findings:
        by_check.setdefault(check, []).append((sym, detail))
    for check, items in sorted(by_check.items(), key=lambda x: -len(x[1])):
        print(f"{check}  ({len(items)})")
        for sym, detail in items[:12]:
            print(f"   {sym:14} {detail}")
        if len(items) > 12:
            print(f"   ... and {len(items) - 12} more")
        print()
    print(f"{len(findings)} findings. These are integrity failures, not coverage gaps —")
    print("data_health.py can report green while every one of them is present.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
