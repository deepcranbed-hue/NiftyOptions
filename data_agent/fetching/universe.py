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

The expiry lists come from the broker instrument master at runtime (Breeze / Kite);
this module is pure date logic so it runs and tests fully offline.
"""
from __future__ import annotations

from datetime import date, datetime

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
        return datetime.strptime(s[:10], "%Y-%m-%d").date()


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
