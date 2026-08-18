"""
data_agent/fetching/fo_bars.py
==============================
Typed store for FUTURES & OPTIONS 1-minute (and 1-day) bars — the derivatives
sibling of `bar_store` (equity/index cash stays in `price_bars`, untouched).

Contract identity is (underlying, expiry, strike, right, instrument_type) — stored
as TYPED COLUMNS so the strategy engine asks numeric questions ("ATM ± N strikes
for this expiry", "all CE", "next expiry") as plain SQL on indexes, instead of
`LIKE '%CE'` / substr() on an encoded symbol string.

Design notes (incl. review refinements):
  * FUTURES use SENTINELS strike=0.0, right='' (never NULL) so the composite PK
    stays unique and INSERT OR REPLACE is idempotent (SQLite treats NULLs in a PK
    as distinct -> would duplicate futures).
  * `symbol` is a CONVENIENCE column (e.g. NIFTY26JUL25500CE) for logs/charts/
    export/broker-mapping — NOT part of the key.
  * `contract_size` (lot size) is stored because it changes over time; keeping it
    on the bar avoids a later lookup when backtesting old series.
  * volume / open_interest use INTEGER affinity (they are integers).
  * `expiry` and `ts` are kept as TEXT ISO on purpose — they already sort/compare
    correctly in SQLite, and it keeps F&O consistent with `price_bars`, `universe`,
    and `data_health` (all ISO), avoiding a split-brain timestamp/date format for a
    negligible speed delta. (This is the one review suggestion I deliberately did
    not adopt — consistency > micro-optimization here.)

Indexes serve the two dominant access patterns: chain/strike-range scans, and
single-contract time-series (Greeks/IV/vol models). Pure SQLite — tests offline.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime

FUT, OPT = "FUT", "OPT"
_FUT_STRIKE = 0.0
_FUT_RIGHT = ""
_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fo_price_bars (
    exchange         TEXT NOT NULL,
    underlying       TEXT NOT NULL,
    instrument_type  TEXT NOT NULL,
    expiry           TEXT NOT NULL,
    strike           REAL NOT NULL DEFAULT 0,
    right            TEXT NOT NULL DEFAULT '',
    timeframe        TEXT NOT NULL,
    ts               TEXT NOT NULL,
    open  REAL, high REAL, low REAL, close REAL,
    volume        INTEGER,
    open_interest INTEGER,
    contract_size INTEGER,
    symbol        TEXT,
    PRIMARY KEY (exchange, underlying, instrument_type, expiry, strike, right, timeframe, ts)
);
"""
# chain / strike-range scans
_IX_CHAIN = ("CREATE INDEX IF NOT EXISTS ix_fo_chain "
             "ON fo_price_bars(underlying, expiry, right, strike, ts)")
# "all futures" / "all options for an expiry" without a strike filter
_IX_ALL = ("CREATE INDEX IF NOT EXISTS ix_fo_all "
           "ON fo_price_bars(underlying, instrument_type, expiry, ts)")
# single-contract history (Greeks / IV / vol models pull one strike over time)
_IX_TS = ("CREATE INDEX IF NOT EXISTS ix_fo_ts "
          "ON fo_price_bars(underlying, expiry, strike, right, timeframe, ts)")


def init_fo(db: str) -> None:
    with sqlite3.connect(db) as c:
        c.execute(_SCHEMA)
        for ix in (_IX_CHAIN, _IX_ALL, _IX_TS):
            c.execute(ix)
        # lightweight migration for an older fo_price_bars (add convenience cols)
        cols = {r[1] for r in c.execute("PRAGMA table_info(fo_price_bars)").fetchall()}
        for col, decl in (("contract_size", "INTEGER"), ("symbol", "TEXT")):
            if col not in cols:
                try:
                    c.execute(f"ALTER TABLE fo_price_bars ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:
                    pass


# ── helpers ─────────────────────────────────────────────────────────────────
def _as_date(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    s = str(x).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _norm(instrument_type: str, strike, right):
    if instrument_type == FUT:
        return _FUT_STRIKE, _FUT_RIGHT
    r = (right or "").upper()
    r = "CE" if r in ("CE", "CALL", "C") else "PE" if r in ("PE", "PUT", "P") else r
    return float(strike), r


def contract_symbol(underlying: str, instrument_type: str, expiry,
                    strike=0, right="") -> str:
    """Human-readable contract id (convenience, not a key). Includes the EXACT
    expiry DAY so weekly options are unambiguous, with an underscore before the
    strike so date and strike never run together:
        option  -> NIFTY26JUL21_24000CE   (21-Jul-2026 weekly, 24000 Call)
        future  -> NIFTY26JUL30_FUT
    """
    d = _as_date(expiry)
    ymd = f"{d.year % 100:02d}{_MON[d.month - 1]}{d.day:02d}"   # e.g. 26JUL21
    u = underlying.upper()
    if instrument_type == FUT:
        return f"{u}{ymd}_FUT"
    return f"{u}{ymd}_{int(strike)}{(right or '').upper()}"


def _int_or_none(v):
    return int(round(v)) if v is not None else None


def resume_from(db, underlying, expiry, floor, *, instrument_type="FUT",
                timeframe="1d", overlap_days=5):
    """Where to start fetching a contract: just before what we already hold.

    THE SAME RULE THE REST OF THIS REPO ALREADY FOLLOWS, applied to fo_price_bars.
    `daily_bars.sync_symbols`, `sync_commodities._resume_from`, `sync_nifty50_bars_yf._watermark`
    and `backfill_daily_bars` each implement watermark + floor + overlap against
    price_bars.symbol. Futures could not reuse any of them because this table is keyed on
    (underlying, expiry, instrument_type, timeframe), not on a single symbol — so the CODE
    differs and the RULE does not.

    WHY THE OVERLAP IS NOT LAZINESS. Settled bars are immutable, so re-pulling them buys
    nothing on the face of it. What it buys is the boundary: the last few sessions are the ones
    a vendor revises or delivers late, and a watermark computed from a partially-written day
    would otherwise skip the remainder of it forever. Five days costs almost nothing and
    removes the whole class of off-by-one-session bugs. Same reasoning, same number, as
    daily_bars.

    WHY NOT A FIXED WINDOW. download_stock_futures.py originally re-fetched a flat 120-day
    window per contract every run, justified as "stateless self-healing" and "avoids timezone
    edge cases". Both are already what watermark-plus-overlap delivers — resuming from what is
    stored IS self-healing, and the overlap IS the boundary guard — so the fixed window bought
    nothing and cost a fifth convention. sync_commodities carried exactly this design once and
    its own docstring records the fix: "every run re-pulled a year of daily ... and rewrote rows
    that had not changed."
    """
    import sqlite3 as _sq
    from datetime import datetime as _dt, timedelta as _td

    def _d(x):
        return _dt.strptime(str(x)[:10], "%Y-%m-%d")

    # Returns a DATE for daily timeframes and a DATETIME for sub-daily ones, deliberately:
    # the caller needs the granularity the data has, and a single return type would force
    # one of the two cases to be wrong.

    try:
        con = _sq.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute(
            "select max(ts) from fo_price_bars where underlying=? and expiry=? "
            "and instrument_type=? and timeframe=?",
            (underlying, expiry, instrument_type, timeframe)).fetchone()
        con.close()
    except Exception:                                        # noqa: BLE001
        return floor                    # unreadable database -> take the whole window
    if not row or not row[0]:
        return floor                    # nothing stored for this contract yet

    # GRANULARITY MUST MATCH THE TIMEFRAME. A DATE watermark identifies a daily bar
    # completely — there is only one per day — but it identifies NONE of the ~375 minute
    # bars in a session. Truncating a 1m watermark to a date either re-pulls the whole day
    # or, worse, leaves the rest of a partially written day unreachable.
    #
    # THE GRANULARITY IS SET BY THE VENDOR, NOT BY CARE. An earlier version of this comment
    # called sync_commodities._resume_from "the weaker convention" for truncating a 1m
    # watermark to a date. That was wrong, and checking the two endpoints shows why:
    #
    #   Breeze   from_date="2026-08-18T10:01:00.000Z"                 accepts a DATETIME
    #   Upstox   /historical-candle/{key}/1minute/{to}/{frm}          DATES in the URL path
    #
    # sync_nifty50_to_now.py:161 resumes at the next MINUTE because Breeze lets it.
    # sync_commodities truncates to a date because Upstox offers nothing finer — its very
    # next line re-formats to "%Y-%m-%d" regardless, so a precise watermark would be
    # discarded on arrival. It pairs that date-ranged history call with a separate
    # fetch_1m_today() intraday endpoint, which is the correct way to handle a date-granular
    # API rather than a shortcut around one.
    #
    # So the rule here is: resume at the finest granularity THE SOURCE ACCEPTS. For Breeze
    # futures that is a timestamp, which is what the branch below returns.
    #
    # So: sub-daily resumes at the NEXT BAR, daily resumes at a date minus the overlap.
    # The overlap exists for vendor revisions of recent sessions, which is a daily-bar
    # concern; an intraday series has no equivalent, it just continues.
    if not timeframe.endswith("d"):
        unit = {"m": "minutes", "h": "hours"}.get(timeframe[-1], "minutes")
        n = int("".join(ch for ch in timeframe if ch.isdigit()) or 1)
        last = _dt.strptime(str(row[0])[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        return last + _td(**{unit: n})

    return max(_d(row[0]) - _td(days=overlap_days), _d(floor)).date()


def save_fo_bars(rows, *, db: str, underlying: str, instrument_type: str,
                 expiry: str, exchange: str = "NFO", timeframe: str = "1m",
                 strike=None, right=None, contract_size: int | None = None,
                 allow_today_1d: bool = False) -> int:
    """rows: iterable of (ts_iso, o, h, l, c, v, [open_interest]). Idempotent.
    OPT needs strike + right; FUT ignores them (sentinels applied)."""
    assert timeframe in ("1m", "1d"), "store only ground-truth timeframes"
    assert instrument_type in (FUT, OPT)

    # SAME RULE AS bar_store.save_bars: a 1d bar is never dated today. Applied at BOTH write
    # boundaries deliberately — one table guarded and the other not is how this repo ends up
    # with two conventions, which is the defect it keeps paying for. Breeze happens not to
    # publish an intraday daily bar today (fo_price_bars held 0 such rows when this was
    # added), so the guard is currently inert here; it exists so the rule does not depend on
    # a vendor continuing to behave.
    if timeframe == "1d" and not allow_today_1d:
        # IST, not host-local: this machine reports UTC, and "today" must mean the
        # trading day. The row's own date is already the session date, so it is compared
        # directly rather than after a UTC normalisation that would shift it back a day.
        import datetime as _dt
        _IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
        _today = _dt.datetime.now(_IST).date().isoformat()
        rows = [r for r in rows if str(r[0])[:10] < _today]
        if not rows:
            return 0
    init_fo(db)
    k_strike, k_right = _norm(instrument_type, strike, right)
    sym = contract_symbol(underlying, instrument_type, expiry, k_strike, k_right)
    exp = _as_date(expiry).isoformat()
    payload = [
        (exchange, underlying.upper(), instrument_type, exp, k_strike, k_right,
         timeframe, r[0], r[1], r[2], r[3], r[4],
         _int_or_none(r[5] if len(r) > 5 else None),
         _int_or_none(r[6] if len(r) > 6 else None),
         contract_size, sym)
        for r in rows
    ]
    with sqlite3.connect(db) as c:
        c.executemany(
            "INSERT OR REPLACE INTO fo_price_bars"
            "(exchange, underlying, instrument_type, expiry, strike, right, timeframe, ts,"
            " open, high, low, close, volume, open_interest, contract_size, symbol) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
    return len(payload)


def _rows(db, sql, args):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, args).fetchall()]


# ── queries ─────────────────────────────────────────────────────────────────
def get_strike_range(db, *, underlying, expiry, right, lo, hi, timeframe="1m", at_ts=None):
    sql = ("SELECT * FROM fo_price_bars WHERE underlying=? AND instrument_type='OPT' "
           "AND expiry=? AND right=? AND strike BETWEEN ? AND ? AND timeframe=?")
    args = [underlying.upper(), _as_date(expiry).isoformat(), (right or "").upper(),
            float(lo), float(hi), timeframe]
    if at_ts:
        sql += " AND ts=?"; args.append(at_ts)
    sql += " ORDER BY strike, ts"
    return _rows(db, sql, args)


def get_atm_chain(db, *, underlying, expiry, spot, n=5, timeframe="1m", at_ts=None):
    """ATM ± n strikes (both CE & PE) — the query almost every strategy needs.
    Finds the nearest available strike to `spot`, returns the n strikes either side."""
    exp = _as_date(expiry).isoformat()
    ks = [r["strike"] for r in _rows(db,
          "SELECT DISTINCT strike FROM fo_price_bars WHERE underlying=? AND instrument_type='OPT' "
          "AND expiry=? AND timeframe=? ORDER BY strike",
          [underlying.upper(), exp, timeframe])]
    if not ks:
        return []
    nearest = min(ks, key=lambda k: abs(k - spot))
    i = ks.index(nearest)
    lo, hi = ks[max(0, i - n)], ks[min(len(ks) - 1, i + n)]
    sql = ("SELECT * FROM fo_price_bars WHERE underlying=? AND instrument_type='OPT' "
           "AND expiry=? AND strike BETWEEN ? AND ? AND timeframe=?")
    args = [underlying.upper(), exp, lo, hi, timeframe]
    if at_ts:
        sql += " AND ts=?"; args.append(at_ts)
    sql += " ORDER BY strike, right, ts"
    return _rows(db, sql, args)


def get_option_chain(db, *, underlying, expiry, at_ts, timeframe="1m"):
    return _rows(db,
                 "SELECT * FROM fo_price_bars WHERE underlying=? AND instrument_type='OPT' "
                 "AND expiry=? AND ts=? AND timeframe=? ORDER BY strike, right",
                 [underlying.upper(), _as_date(expiry).isoformat(), at_ts, timeframe])


def get_future(db, *, underlying, expiry, timeframe="1m"):
    return _rows(db,
                 "SELECT * FROM fo_price_bars WHERE underlying=? AND instrument_type='FUT' "
                 "AND expiry=? AND timeframe=? ORDER BY ts",
                 [underlying.upper(), _as_date(expiry).isoformat(), timeframe])


def near_next(db, *, underlying, timeframe="1m"):
    rows = _rows(db,
                 "SELECT DISTINCT expiry FROM fo_price_bars WHERE underlying=? "
                 "AND instrument_type='FUT' AND timeframe=? ORDER BY expiry",
                 [underlying.upper(), timeframe])
    return [r["expiry"] for r in rows]


def stored_expiries(db, *, underlying, instrument_type, timeframe="1m"):
    rows = _rows(db,
                 "SELECT DISTINCT expiry FROM fo_price_bars WHERE underlying=? "
                 "AND instrument_type=? AND timeframe=? ORDER BY expiry",
                 [underlying.upper(), instrument_type, timeframe])
    return [r["expiry"] for r in rows]


# ── centralized expiry resolution ───────────────────────────────────────────
class ExpiryResolver:
    """One place to answer "which expiry?" as the universe grows to weekly/monthly,
    BankNifty, FinNifty — instead of scattering near/next logic across signals.

        ExpiryResolver(expiries, today).resolve("near" | "next" | "monthly" | "weekly" | "all")

    monthly = the last expiry within its calendar month; weekly = everything else.
    Returns ISO date strings (or a list for 'all'); None if nothing matches.
    """
    def __init__(self, expiries, today: date | None = None):
        self.today = today or date.today()
        self.all = sorted({_as_date(e) for e in expiries})
        self.unexpired = [d for d in self.all if d >= self.today]
        by_month = defaultdict(list)
        for d in self.all:
            by_month[(d.year, d.month)].append(d)
        self._monthlies = {max(v) for v in by_month.values()}

    def resolve(self, kind: str = "near"):
        kind = kind.lower()
        un = self.unexpired
        if kind == "all":
            return [d.isoformat() for d in un]
        if not un:
            return None
        if kind == "near":
            return un[0].isoformat()
        if kind == "next":
            return un[1].isoformat() if len(un) > 1 else None
        if kind == "monthly":
            m = next((d for d in un if d in self._monthlies), None)
            return m.isoformat() if m else None
        if kind == "weekly":
            w = next((d for d in un if d not in self._monthlies), None)
            return w.isoformat() if w else None
        raise ValueError(f"unknown expiry kind: {kind!r}")


if __name__ == "__main__":
    import os, tempfile
    db = os.path.join(tempfile.gettempdir(), "fo_demo.db")
    if os.path.exists(db):
        os.remove(db)
    for k in (24000, 24100):
        save_fo_bars([("2026-07-14T05:00:00Z", 100, 101, 99, 100.5, 1200.0, 45000.0)],
                     db=db, underlying="NIFTY", instrument_type=OPT,
                     expiry="2026-07-14", strike=k, right="CE", contract_size=75)
    save_fo_bars([("2026-07-14T05:00:00Z", 24050, 24060, 24040, 24055, 5000, 800000)],
                 db=db, underlying="NIFTY", instrument_type=FUT, expiry="2026-07-31",
                 contract_size=75)
    r = _rows(db, "SELECT symbol, strike, volume, contract_size FROM fo_price_bars ORDER BY symbol", [])
    print("stored:", r)
    er = ExpiryResolver(["2026-07-09", "2026-07-16", "2026-07-30", "2026-08-27"], date(2026, 7, 7))
    print("near:", er.resolve("near"), "next:", er.resolve("next"),
          "weekly:", er.resolve("weekly"), "monthly:", er.resolve("monthly"))
