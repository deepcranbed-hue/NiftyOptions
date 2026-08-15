"""
data_agent/fetching/universe.py
===============================
Expiry-aware instrument selection — WHICH series the data agent should be pulling
right now. Encodes the desk's rules:

  * NIFTY FUTURES : near + next expiry (always two).
  * NIFTY OPTIONS : the current expiry only, UNTIL we are within ROLL_AHEAD_DAYS
                    (2) of that expiry — then ALSO the next expiry, so next-series
                    data is already building before the current one rolls off.
  * EXPIRED series (expiry < today) are never requested (the exchange has no data
    for them, so the quality checker must never flag their absence either).

  * MCX COMMODITIES : stored per contract, so the rolling series is DERIVED. Front
                      month starts the day after the previous contract expires (a
                      contract_registry lookup, not a volume guess) and is left
                      FUT_ROLL_AHEAD_DAYS before its own expiry. See continuous.py.

Expiry lists come from data_agent/expiries.py (Breeze) or the Upstox instrument
master. Kite was removed on 2026-08-08 and is no longer a source anywhere.

This module is pure date logic — it runs and tests fully offline, and it must stay
that way. Vendors spell dates differently (Breeze says '25-Aug-2026'); converting
that belongs at the vendor boundary in expiries.py, not here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

ROLL_AHEAD_DAYS = 2   # start next option expiry this many days before current expiry


def _as_date(x) -> date:
    """Accept date | datetime | 'YYYY-MM-DD' | ISO ('...T..Z') -> date."""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    s = str(x).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        # SAFETY NET, NOT THE MECHANISM. Breeze speaks '25-Aug-2026'; expiries.py
        # normalises that at the vendor boundary so this module never has to know.
        # Accepting it here too means a new caller that forgets to normalise gets a
        # right answer instead of a crash — but if you find yourself relying on this
        # branch, the conversion is missing one level up.
        return datetime.strptime(s, "%d-%b-%Y").date()


def _unexpired_sorted(expiries, today: date) -> list[date]:
    """Sorted distinct expiry dates that are today or later (expired ones dropped)."""
    ds = sorted({_as_date(e) for e in expiries})
    return [d for d in ds if d >= today]


def is_expired(expiry, today: date | None = None) -> bool:
    today = today or date.today()
    return _as_date(expiry) < today


def active_future_expiries(expiries, today: date | None = None, n: int = 2) -> list[date]:
    """Near + next (first n unexpired). Fewer if the master lists fewer."""
    today = today or date.today()
    return _unexpired_sorted(expiries, today)[:n]


def active_option_expiries(expiries, today: date | None = None,
                           roll_ahead_days: int = ROLL_AHEAD_DAYS) -> list[date]:
    """Current expiry, plus the next expiry once we're within roll_ahead_days of it.

    today == expiry-3  -> [current]
    today == expiry-2  -> [current, next]   (start next series 2 days before)
    today == expiry    -> [current, next]
    """
    today = today or date.today()
    unexp = _unexpired_sorted(expiries, today)
    if not unexp:
        return []
    current = unexp[0]
    selected = [current]
    if (current - today).days <= roll_ahead_days and len(unexp) > 1:
        selected.append(unexp[1])
    return selected


FUT_ROLL_AHEAD_DAYS = 3    # continuous series leaves a futures contract this early

# A contract is only usable for the tail of its life.
#
# A futures contract is listed months before anyone trades it. Inside roughly a
# month of expiry it is the liquid front month and prints real volume every session;
# before that it is quoted, marked, and largely untraded — MCX carries the previous
# price forward on days with no trades.
#
# Those marks are not prices. Building a continuous series from them produces a line
# that looks like data and is actually the exchange repeating itself, punctuated by
# jumps whenever somebody finally trades. The first build did exactly this: GOLD's
# continuous series ran back to 2025-10-16 on a contract that did not become the
# front month until September 2026.
#
# So a contract contributes only from this many days before its own expiry.
FUT_LIQUID_WINDOW_DAYS = 40


def roll_schedule(expiries, today: date | None = None,
                  roll_ahead_days: int = FUT_ROLL_AHEAD_DAYS,
                  liquid_window_days: int = FUT_LIQUID_WINDOW_DAYS) -> list[tuple]:
    """Which contract the continuous series uses over time.

    -> [(from_date, to_date_inclusive, expiry), ...] covering the whole span, in order.

    A contract is used until FUT_ROLL_AHEAD_DAYS before its own expiry, then the
    next one takes over. Fixed-offset rather than an open-interest crossover: it is
    deterministic, so a backtest run today and the same backtest run next year pick
    the same bars. An OI rule re-decides history every time OI is revised.

    The last three sessions before an MCX expiry are thin and gappy, which is the
    other reason not to hold a contract to the end.

    Pure date logic — no database, no broker. Same contract as the rest of this
    module: the RULES live here, the data comes from elsewhere.
    """
    ds = sorted({_as_date(e) for e in expiries})
    if not ds:
        return []
    out, start = [], None
    for i, exp in enumerate(ds):
        switch = exp - timedelta(days=roll_ahead_days)     # last day on this contract
        # Never reach further back than the liquid window, even for the earliest
        # contract we hold. Without this the oldest contract's whole listed life —
        # months of untraded marks — becomes the head of the continuous series.
        # The cap applies ONLY to the first contract we hold. Capping every segment
        # start leaves HOLES: gold expiries are two months apart, so a 40-day window
        # cannot bridge them and Feb 2..Feb 23 would belong to no contract at all.
        # After a roll the next contract IS the front month and is liquid, however
        # far from its own expiry it happens to be.
        if start is None:
            start = exp - timedelta(days=liquid_window_days)
        if i == len(ds) - 1:
            out.append((start, date.max, exp))             # newest contract runs on
        else:
            out.append((start, switch - timedelta(days=1), exp))
            start = switch
    return [(a, b, e) for a, b, e in out if a <= b]


def contract_for(expiries, on: date, roll_ahead_days: int = FUT_ROLL_AHEAD_DAYS):
    """Which contract expiry the continuous series should use on `on`. None if unknown."""
    for a, b, exp in roll_schedule(expiries, roll_ahead_days=roll_ahead_days):
        if a <= on <= b:
            return exp
    return None


def build_universe(today: date | None = None, *,
                   stocks: list[str] | None = None,
                   index: str = "NIFTY",
                   future_expiries=None, option_expiries=None,
                   cross_assets=None) -> list[dict]:
    """Assemble the list of instruments to keep in sync as typed targets.

    Returns dicts: {kind, symbol, exchange, expiry?}. Option targets are one per
    active expiry (strike enumeration around ATM is a fetch-time detail).
    """
    today = today or date.today()
    out: list[dict] = []

    out.append({"kind": "index", "symbol": index, "exchange": "NSE"})
    for s in (stocks or []):
        out.append({"kind": "stock", "symbol": s.upper(), "exchange": "NSE"})

    for exp in active_future_expiries(future_expiries or [], today):
        out.append({"kind": "future", "symbol": index, "exchange": "NFO",
                    "expiry": exp.isoformat()})

    for exp in active_option_expiries(option_expiries or [], today):
        out.append({"kind": "option_expiry", "symbol": index, "exchange": "NFO",
                    "expiry": exp.isoformat()})

    for c in (cross_assets or ["GOLD", "SILVER", "COPPER", "CRUDEOIL", "USDINR", "GIFTNIFTY"]):
        out.append({"kind": "cross", "symbol": c.upper()})
    return out


if __name__ == "__main__":
    import json
    today = date(2026, 7, 7)
    fut = ["2026-07-31", "2026-08-28", "2026-09-25"]
    opt = ["2026-07-09", "2026-07-16", "2026-07-23"]     # weekly-ish
    print("today:", today)
    print("future ->", [d.isoformat() for d in active_future_expiries(fut, today)])
    print("option (far) ->", [d.isoformat() for d in active_option_expiries(opt, today)])
    print("option (2d before 07-09) ->",
          [d.isoformat() for d in active_option_expiries(opt, date(2026, 7, 7))])
    uni = build_universe(today, stocks=["TCS", "RELIANCE"], future_expiries=fut, option_expiries=opt)
    print("universe:", json.dumps(uni, indent=2))
