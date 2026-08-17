"""
data_agent/fetching/stock_futures.py
====================================
Roll-aware download plan for SINGLE-STOCK FUTURES on the Nifty 50 constituents.

Storage is NOT new. Bars land in `fo_price_bars` via `fo_bars.save_fo_bars`, which
already keys every row on (exchange, underlying, instrument_type, expiry, strike,
right, timeframe, ts). That answers the "by name or in a column?" question directly:

    EXPIRY IS A TYPED COLUMN and part of the primary key — authoritative.
    `symbol` (e.g. RELIANCE26AUG25_FUT) is a convenience label for logs and charts.

Both exist. The column is what you query; the symbol is what you read. Nothing here
invents a second convention — `price_bars` already carries a THIRD one for
commodities (GOLD_2026-10-05) and that split is exactly what this module avoids
extending.

────────────────────────────────────────────────────────────────────────────────
WHY A DERIVED CONTINUOUS SERIES, NEVER A STORED ONE

`price_bars` holds NIFTY_FUT_1 / NIFTY_FUT_2 — "front month" and "next month" as
fixed symbol names. That design has a measured defect in this repo. On 2026-07-28,
the July contract expired and NIFTY_FUT_1 silently became the August contract. The
basis (future − spot) jumped from +44 points to +135 in one session. Nothing about
the market changed; the symbol simply started pointing at a different contract one
month further out. A study that computed carry off NIFTY_FUT_1 across that date
measured a roll, not a premium.

So: per-contract series are PRIMARY and immutable. The continuous series is DERIVED
on demand by `front_series()`, which returns the roll dates and the price gap at each
roll so the caller can back-adjust deliberately instead of inheriting a discontinuity
by accident.

────────────────────────────────────────────────────────────────────────────────
THE ROLL, IN TWO SEPARATE DECISIONS

They are genuinely different questions and conflating them is the usual bug.

  1. WHAT TO DOWNLOAD.  NSE lists three serial monthly contracts. Fetch every
     unexpired one. When a contract expires, stop fetching it — but never delete it,
     and keep fetching for `_SETTLE_GRACE_DAYS` afterwards so the final session and
     settlement bar arrive. A new far-month appears automatically as the near one
     rolls off, because the ladder is computed from today's date, not stored.

  2. WHICH CONTRACT IS "FRONT".  Liquidity migrates to the next month several days
     BEFORE expiry, so a calendar roll tracks a contract that has already gone thin.
     Because `fo_price_bars` carries open_interest (it is 100% populated, unlike
     `chain_rows` bid/ask/iv), the roll can be decided on data: roll when the next
     month's OI overtakes the front month's. `method="calendar"` is kept for
     comparison, not because it is right.

EXPIRY CALENDAR
    NSE monthly F&O expiry is the LAST TUESDAY of the month (verified against
    2026-07-28, 2026-08-25, 2026-09-29 in this database). If that Tuesday is a
    holiday, expiry moves EARLIER to the previous trading day. There is no holiday
    list in this repo — `instruments.holiday_calendar` is the string 'NSE_2026' and
    nothing more — so the adjustment is derived from trading days actually observed
    in `price_bars`. For dates beyond observed history the nominal Tuesday is
    returned with `confirmed=False`, so a caller can tell a verified expiry from an
    extrapolated one instead of trusting both equally.
"""
from __future__ import annotations

import calendar
import csv
import os
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# NSE lists three serial monthly contracts for single-stock futures.
# TWO contracts: the current month and the one after. That is the whole ladder — a
# third serial month adds calls and carries almost no open interest, and the OI
# crossover that decides the roll only ever involves the near pair.
_LADDER = 2
# Keep pulling a contract briefly after expiry so the last session lands.
_SETTLE_GRACE_DAYS = 3
# Below this the OI comparison is two thin numbers; fall back to the calendar roll.
_MIN_OI_FOR_ROLL = 1000


def constituents(path: str = "nifty-50-stock-list.csv") -> list[str]:
    with open(os.path.join(_ROOT, path)) as f:
        return [r["Symbol"] for r in csv.DictReader(f)]


def _last_tuesday(y: int, m: int) -> date:
    return date(y, m, max(w[calendar.TUESDAY] for w in calendar.monthcalendar(y, m)
                          if w[calendar.TUESDAY]))


def _trading_days(db: str) -> set[date]:
    """Sessions actually observed. Used to move an expiry off a holiday — the only
    holiday source available, and it is the market's own record rather than a list
    someone typed."""
    try:
        con = sqlite3.connect(db)
        rows = con.execute(
            "SELECT DISTINCT substr(ts,1,10) FROM price_bars "
            "WHERE symbol='NIFTY' AND timeframe='1d'").fetchall()
        return {date.fromisoformat(r[0]) for r in rows}
    except Exception:
        return set()


def monthly_expiries(start: date, end: date, db: str | None = None) -> list[dict]:
    """Monthly expiry for every month in [start, end].

    Returns dicts with `expiry` (what to use), `nominal` (the last Tuesday),
    `adjusted` (moved off a holiday) and `confirmed` (inside observed history).
    An unconfirmed expiry is a guess about a future calendar and says so.
    """
    days = _trading_days(db) if db else set()
    have_upto = max(days) if days else None
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        nom = _last_tuesday(y, m)
        exp, adjusted = nom, False
        if days and nom <= (have_upto or nom):
            while exp not in days and exp > nom - timedelta(days=7):
                exp -= timedelta(days=1)
                adjusted = True
        out.append({"expiry": exp.isoformat(), "nominal": nom.isoformat(),
                    "adjusted": adjusted,
                    "confirmed": bool(days and have_upto and nom <= have_upto)})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def listed_contracts(today: date | None = None, ladder: int = _LADDER,
                     db: str | None = None) -> list[dict]:
    """The `ladder` unexpired monthly contracts NSE is quoting today.

    Computed from the date, never stored — which is what makes the roll automatic.
    The far month appears in this list the day the near one drops out of it.
    """
    today = today or date.today()
    exps = monthly_expiries(today.replace(day=1),
                            date(today.year + 2, today.month, 1), db=db)
    return [e for e in exps if date.fromisoformat(e["expiry"]) >= today][:ladder]


def download_plan(db: str, *, today: date | None = None, underlyings=None,
                  ladder: int = _LADDER, timeframe: str = "1m") -> list[dict]:
    """What to fetch right now, per (underlying, expiry), with a resume watermark.

    Includes recently-expired contracts for `_SETTLE_GRACE_DAYS` so the final
    session is not lost — the single most common way a futures history ends up
    with a truncated last contract.
    """
    today = today or date.today()
    unders = underlyings or constituents()
    live = listed_contracts(today, ladder, db=db)
    recent = [e for e in monthly_expiries(today.replace(day=1) - timedelta(days=95),
                                          today, db=db)
              if 0 <= (today - date.fromisoformat(e["expiry"])).days <= _SETTLE_GRACE_DAYS]
    wanted = {e["expiry"]: e for e in live + recent}

    con = sqlite3.connect(db)
    marks = {}
    try:
        for u, e, mx in con.execute(
                "SELECT underlying, expiry, MAX(ts) FROM fo_price_bars "
                "WHERE instrument_type='FUT' AND timeframe=? GROUP BY 1,2", (timeframe,)):
            marks[(u, e)] = mx
    except sqlite3.OperationalError:
        pass

    plan = []
    for u in unders:
        for exp, meta in sorted(wanted.items()):
            plan.append({
                "underlying": u, "expiry": exp, "timeframe": timeframe,
                "since": marks.get((u, exp)),               # None => full backfill
                "status": "expired-settling" if date.fromisoformat(exp) < today else "live",
                "expiry_confirmed": meta["confirmed"],
            })
    return plan


def front_series(db: str, underlying: str, *, method: str = "oi",
                 timeframe: str = "1d") -> dict:
    """DERIVE the continuous front-month series. Returns the roll schedule and the
    price gap at each roll — it does NOT silently splice.

    method="oi"        roll on the session the next month's open interest exceeds
                       the front month's. This is where the liquidity actually is.
    method="calendar"  roll the day after expiry. Kept for comparison.

    `roll_gap` is (next_close − front_close) on the roll date: the artificial jump a
    naive spliced series would contain. Back-adjust by subtracting the cumulative gap
    from history, or don't — but do it knowingly. This is the defect NIFTY_FUT_1 has.
    """
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT expiry, substr(ts,1,10) d, close, open_interest oi FROM fo_price_bars "
        "WHERE underlying=? AND instrument_type='FUT' AND timeframe=? "
        "ORDER BY d, expiry", (underlying.upper(), timeframe)).fetchall()
    by_day = defaultdict(dict)
    for r in rows:
        by_day[r["d"]][r["expiry"]] = (r["close"], r["oi"])

    schedule, rolls, cur, cur_prev = [], [], None, None
    for d in sorted(by_day):
        exps = sorted(by_day[d])
        unexpired = [e for e in exps if e >= d]
        if not unexpired:
            continue
        front, nxt = unexpired[0], (unexpired[1] if len(unexpired) > 1 else None)
        pick = front
        if method == "oi" and nxt:
            f_oi, n_oi = by_day[d][front][1] or 0, by_day[d][nxt][1] or 0
            if n_oi > f_oi and n_oi > _MIN_OI_FOR_ROLL:
                pick = nxt
        if cur and pick != cur:
            a = by_day[d].get(cur, (None, None))[0]
            b = by_day[d].get(pick, (None, None))[0]
            rolls.append({"date": d, "from": cur, "to": pick,
                          "roll_gap": round(b - a, 2) if (a and b) else None})
        was_roll = bool(cur_prev is not None and pick != cur_prev)
        gap = rolls[-1]["roll_gap"] if (was_roll and rolls) else None
        cur = pick
        cur_prev = pick
        schedule.append({"d": d, "expiry": pick, "close": by_day[d][pick][0],
                         "oi": by_day[d][pick][1],
                         # Flagged ON the row, not only in `rolls`. A caller computing
                         # returns off `series` must drop the roll day, and having to
                         # join a separate list to find out which day that was is the
                         # step that gets skipped. One field, no join.
                         "is_roll": was_roll, "roll_gap": gap})
    n_roll = sum(1 for r in schedule if r["is_roll"])
    return {"underlying": underlying.upper(), "method": method,
            "n_days": len(schedule), "rolls": rolls, "series": schedule,
            "n_roll_days": n_roll,
            "raw_splice_note": (
                "This series is RAW — contracts are spliced end to end and nothing is "
                "back-adjusted, which is deliberate: the level on any given day is the "
                "real traded price of the contract that was liquid that day. The cost is "
                f"that the {n_roll} roll day(s) carry a FICTIONAL return — the jump from "
                "one contract to the next, not a market move. With monthly expiries that "
                "is ~12 of ~250 sessions a year, about 5% of daily observations, and they "
                "are large because a month of carry is embedded in each. Levels, open "
                "interest and notional are unaffected. RETURNS, volatility and anything "
                "cumulative must drop rows where is_roll is true, or add back roll_gap.")}


if __name__ == "__main__":
    db = os.path.join(_ROOT, "option_chains.db")
    today = date.today()
    print(f"today {today}\n")
    print("MONTHLY EXPIRY CALENDAR")
    for e in monthly_expiries(date(2026, 7, 1), date(2026, 12, 1), db=db):
        flag = "adjusted-off-holiday" if e["adjusted"] else ""
        print(f"   {e['expiry']}  nominal {e['nominal']}  "
              f"{'confirmed' if e['confirmed'] else 'EXTRAPOLATED'} {flag}")
    print("\nLADDER NSE IS QUOTING TODAY")
    for e in listed_contracts(today, db=db):
        print(f"   {e['expiry']}")
    plan = download_plan(db, today=today)
    print(f"\nDOWNLOAD PLAN: {len(plan)} (underlying, expiry) pairs "
          f"over {len(set(p['underlying'] for p in plan))} underlyings")
    for p in plan[:4]:
        print(f"   {p['underlying']:11s} {p['expiry']}  {p['status']:16s} since={p['since']}")
    print(f"   ... resume watermarks present for "
          f"{sum(1 for p in plan if p['since'])} of {len(plan)}")
    print("\nCONTINUOUS SERIES (derived) — NIFTY, 1m, OI roll")
    fs = front_series(db, "NIFTY", method="oi", timeframe="1m")
    print(f"   {fs['n_days']} sessions, {len(fs['rolls'])} roll(s)")
    for r in fs["rolls"]:
        print(f"   roll {r['date']}  {r['from']} -> {r['to']}  gap {r['roll_gap']} pts")
