"""
global_cues.py
--------------
Global cues for a Mumbai (IST) session, time-bucketed correctly instead of
lumped as "overnight". Three roles relative to the NIFTY open (09:15 IST):

    US (S&P / Nasdaq / SOX)   close ~01:30 IST  -> OVERNIGHT, leads the open
    Asia (Nikkei/KOSPI/HSI)   open  ~05:30 IST  -> LIVE, leads/with the open
    Europe (FTSE/DAX/CAC)     open  ~12:30 IST  -> INTRADAY, concurrent (lags open)
    Brent / DXY / US10Y       ~24h electronic   -> CONTINUOUS

NUMBERS come from a QUOTES feed (yfinance / Alpha Vantage / broker), NOT parsed
from news text. `provider` is injectable so this is testable offline; a yfinance
adapter is shown at the bottom. Each cue is tagged with its session status
(LIVE / CLOSED_TODAY / PRE_OPEN) and a staleness flag, so a prior-close US print,
a live Korean print, and a not-yet-open European market are clearly distinct.

Only LEADING cues (US overnight + Asia live) feed magnitude_corroboration for a
pre-open signal; Europe is concurrent and tagged as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


# symbol -> (display, region_role, surface, session_start_ist, session_end_ist)
# session times in IST; US wraps past midnight (end < start).
INSTRUMENTS = {
    "^GSPC":  ("S&P 500",      "overnight",  "us_broad",      (19, 0), (1, 30)),
    "^IXIC":  ("Nasdaq",       "overnight",  "us_tech",       (19, 0), (1, 30)),
    "^SOX":   ("Phil. Semi",   "overnight",  "us_tech",       (19, 0), (1, 30)),
    "^N225":  ("Nikkei",       "live_lead",  "japan_equity",  (5, 30), (11, 30)),
    "^KS11":  ("KOSPI",        "live_lead",  "korea_equity",  (5, 30), (12, 0)),
    "^HSI":   ("Hang Seng",    "live_lead",  "hk_equity",     (6, 45), (13, 30)),
    "^FTSE":  ("FTSE 100",     "concurrent", "europe_equity", (12, 30), (21, 0)),
    "^GDAXI": ("DAX",          "concurrent", "europe_equity", (12, 30), (21, 0)),
    "^FCHI":  ("CAC 40",       "concurrent", "europe_equity", (12, 30), (21, 0)),
    "BZ=F":   ("Brent",        "continuous", "commodity_oil", (0, 0),  (23, 59)),
    "DX=F":   ("Dollar Index", "continuous", "fx",            (0, 0),  (23, 59)),
}

NIFTY_OPEN = (9, 15)


@dataclass
class Cue:
    symbol: str
    name: str
    region_role: str          # overnight | live_lead | concurrent | continuous
    surface: str
    pct_change: float
    status: str               # LIVE | CLOSED_TODAY | PRE_OPEN
    as_of: datetime
    stale: bool
    leads_open: bool          # is this known before/at the NIFTY open?


def _in_session(now_ist: datetime, start, end) -> bool:
    t = now_ist.hour * 60 + now_ist.minute
    s = start[0] * 60 + start[1]
    e = end[0] * 60 + end[1]
    return (s <= t <= e) if s <= e else (t >= s or t <= e)   # handle midnight wrap


def _status(now_ist, start, end) -> str:
    if _in_session(now_ist, start, end):
        return "LIVE"
    # session already happened today (ended earlier today) vs not opened yet
    t = now_ist.hour * 60 + now_ist.minute
    s = start[0] * 60 + start[1]
    return "CLOSED_TODAY" if t >= s or s > end[0] * 60 + end[1] else "PRE_OPEN"


def fetch_global_cues(provider, now_ist: datetime | None = None) -> list[Cue]:
    """provider(symbols) -> {symbol: {'pct': float, 'as_of': datetime}}."""
    now_ist = now_ist or datetime.now(IST)
    quotes = provider(list(INSTRUMENTS))
    open_min = NIFTY_OPEN[0] * 60 + NIFTY_OPEN[1]

    cues = []
    for sym, (name, role, surface, start, end) in INSTRUMENTS.items():
        q = quotes.get(sym)
        if not q:
            continue
        as_of = q["as_of"]
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=IST)
        as_of = as_of.astimezone(IST)
        status = _status(now_ist, start, end)
        age_min = (now_ist - as_of).total_seconds() / 60.0
        stale = (age_min > 20) if status == "LIVE" else (age_min > 24 * 60)
        # leads the open if it's US-overnight, or Asia that opened before 09:15,
        # i.e. anything whose latest print exists at/around the NIFTY open
        leads = role in ("overnight", "live_lead", "continuous")
        cues.append(Cue(sym, name, role, surface, q["pct"], status, as_of,
                        stale, leads))
    return cues


def to_surface_moves(cues: list[Cue], leading_only: bool = True) -> dict[str, float]:
    """Aggregate cue % moves into surface -> move for magnitude_corroboration.
    Averages instruments sharing a surface (e.g. Nasdaq + SOX -> us_tech)."""
    acc: dict[str, list[float]] = {}
    for c in cues:
        if leading_only and not c.leads_open:
            continue
        if c.stale:
            continue
        acc.setdefault(c.surface, []).append(c.pct_change)
    return {s: sum(v) / len(v) for s, v in acc.items()}


def group_by_role(cues: list[Cue]) -> dict[str, list[Cue]]:
    out: dict[str, list[Cue]] = {}
    for c in cues:
        out.setdefault(c.region_role, []).append(c)
    return out


# ── live adapter (wire in your app; not run offline) ──────────────────────────
def yfinance_provider(symbols):                      # pragma: no cover
    import yfinance as yf
    out = {}
    data = yf.download(symbols, period="2d", interval="1d", progress=False)["Close"]
    for s in symbols:
        col = data[s].dropna()
        if len(col) >= 2:
            pct = (col.iloc[-1] / col.iloc[-2] - 1) * 100
            out[s] = {"pct": float(pct), "as_of": datetime.now(IST)}
    return out


if __name__ == "__main__":
    from index_attribution import magnitude_corroboration

    # pre-open snapshot ~09:00 IST: US closed overnight, Asia live, Europe not open
    now = datetime(2026, 6, 25, 9, 0, tzinfo=IST)
    fake = {
        "^GSPC": {"pct": -1.4, "as_of": datetime(2026, 6, 25, 1, 30, tzinfo=IST)},
        "^IXIC": {"pct": -2.2, "as_of": datetime(2026, 6, 25, 1, 30, tzinfo=IST)},
        "^SOX":  {"pct": -8.0, "as_of": datetime(2026, 6, 25, 1, 30, tzinfo=IST)},
        "^N225": {"pct": -3.5, "as_of": datetime(2026, 6, 25, 8, 55, tzinfo=IST)},
        "^KS11": {"pct": -9.9, "as_of": datetime(2026, 6, 25, 8, 55, tzinfo=IST)},
        "^HSI":  {"pct": -2.1, "as_of": datetime(2026, 6, 25, 8, 55, tzinfo=IST)},
        "^FTSE": {"pct":  0.0, "as_of": datetime(2026, 6, 24, 21, 0, tzinfo=IST)},  # prior close, pre-open
        "BZ=F":  {"pct": -1.2, "as_of": datetime(2026, 6, 25, 8, 58, tzinfo=IST)},
        "DX=F":  {"pct":  0.3, "as_of": datetime(2026, 6, 25, 8, 58, tzinfo=IST)},
    }
    cues = fetch_global_cues(lambda s: fake, now_ist=now)

    print(f"GLOBAL CUES as of {now:%H:%M IST}  (NIFTY opens 09:15)\n")
    for role, group in group_by_role(cues).items():
        print(f"[{role}]")
        for c in group:
            lead = "→ leads open" if c.leads_open else "concurrent"
            flag = " STALE" if c.stale else ""
            print(f"  {c.name:<13} {c.pct_change:+5.1f}%  {c.status:<12} {lead}{flag}")
        print()

    sm = to_surface_moves(cues, leading_only=True)
    print("leading surface moves ->", {k: round(v, 1) for k, v in sm.items()})
    print(f"magnitude_corroboration = x{magnitude_corroboration(sm):.2f}")
