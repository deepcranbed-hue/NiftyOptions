"""
bar_store.py
------------
Store NIFTY price bars (1-minute + daily) in SQLite and serve them chart-ready.

Principles (same as chain_store):
  * STORE ground truth only: raw 1m bars and raw 1d bars, exactly as downloaded.
  * DERIVE at query time: 5m/15m/60m are resampled from 1m on request — never
    stored (no duplicate truths to drift apart).
  * Lossless, idempotent inserts (INSERT OR REPLACE on the natural key).

Also computes REALIZED VOLATILITY from the bars — the input vol_attribution.py
has been missing (it compares implied IV vs realized; realized now measurable).
"""
from __future__ import annotations
import sqlite3, math
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

# IST timezone helper
IST = timezone(timedelta(hours=5, minutes=30))

from db_config import DB_PATH, connect as _db_connect   # single source for the DB path (D-SC-06) —
                                # Google Drive is the primary; repo-local is a copy


@contextmanager
def _conn(db=DB_PATH):
    # via db_config so the Drive-safe busy timeout applies (D-SC-06). Python's
    # sqlite3 default is only 5s; a Drive sync can hold the file longer than that.
    c = _db_connect(db)
    try: yield c; c.commit()
    finally: c.close()


def init_bars(db=DB_PATH):
    with _conn(db) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS price_bars (
            exchange  TEXT NOT NULL DEFAULT 'NSE',
            symbol    TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts        TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL, open_interest REAL,
            PRIMARY KEY (exchange, symbol, timeframe, ts)
        );
        CREATE INDEX IF NOT EXISTS ix_bars ON price_bars(exchange, symbol, timeframe, ts);
        """)
        # Migration: ensure open_interest exists if table already existed
        try:
            c.execute("SELECT open_interest FROM price_bars LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE price_bars ADD COLUMN open_interest REAL")


def save_bars(rows, *, exchange="NSE", symbol="NIFTY", timeframe="1m", db=DB_PATH,
              allow_today_1d=False) -> int:
    """rows: iterable of (ts_iso, o, h, l, c, v, [oi]). Idempotent.

    RULE: A 1d BAR IS NEVER DATED TODAY. Enforced here, at the single write boundary, rather
    than in each downloader — the same reasoning as db_config owning the DB path.

    WHY THE RULE. Vendors disagree about what a daily bar dated today means, and the
    disagreement is invisible in the stored row. Measured mid-session on 2026-08-18: 123 of
    125 India/Yahoo symbols already carried a bar dated today, against 3 of 25 MCX/Upstox and
    0 of 6 US indices. Yahoo's daily bar UPDATES through the session, Upstox's daily endpoint
    is end-of-day, and Breeze publishes in an overnight batch. So price_bars was holding
    half-finished bars beside absent ones for the same date, and any cross-sectional
    calculation on the latest date silently compared the two.

    One rule removes the whole class: a daily bar is written only once the session it
    describes has ended. Today's view comes from 1m bars, which is what they are for.

    `allow_today_1d=True` exists for a deliberate intraday snapshot and must be passed
    explicitly, so it appears at the call site rather than being the default anyone inherits.
    """
    assert timeframe in ("1m", "1d"), "store only ground-truth timeframes"
    from backend.timeutil import to_db_ts
    init_bars(db)

    if timeframe == "1d" and not allow_today_1d:
        # COMPARE SESSION DATES, NOT STORED TIMESTAMPS. `to_db_ts` normalises to UTC, so a
        # midnight-IST bar for today becomes 18:30 on the PREVIOUS UTC day and slips past a
        # naive comparison — the first version of this guard did exactly that and let both
        # test rows through. Same UTC-shift class as the TATAMOTORS weekend bars in
        # daily_bar_audit's docstring: "a UTC conversion moved every session back one day".
        #
        # The vendor's own date IS the session date, so compare that, against today in IST
        # rather than in whatever zone the host happens to run in (this machine reports UTC).
        import datetime as _dt
        _IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
        _today = _dt.datetime.now(_IST).date().isoformat()
        rows = list(rows)
        _kept = [r for r in rows if str(r[0])[:10] < _today]
        if len(_kept) != len(rows):
            print(f"   [1d rule] {symbol}: dropped {len(rows) - len(_kept)} bar(s) dated "
                  f"{_today} — a daily bar is written after its session closes, not during it")
        rows = _kept
        if not rows:
            return 0
    with _conn(db) as c:
        c.executemany(
            "INSERT OR REPLACE INTO price_bars(exchange, symbol, timeframe, ts, open, high, low, close, volume, open_interest) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(exchange, symbol, timeframe, to_db_ts(r[0]), r[1], r[2], r[3], r[4],
              r[5] if len(r) > 5 else None,
              r[6] if len(r) > 6 else None) for r in rows])
    return len(list(rows)) if not hasattr(rows, "__len__") else len(rows)



def get_bars(exchange="NSE", symbol="NIFTY", timeframe="1m", start=None, end=None, db=DB_PATH):
    """Chart-ready bars. '5m'/'15m'/'60m' are RESAMPLED from stored 1m."""
    base = "1m" if timeframe.endswith("m") else "1d"
    q = "SELECT * FROM price_bars WHERE exchange=? AND symbol=? AND timeframe=?"
    args = [exchange, symbol, base]
    if start: q += " AND ts>=?"; args.append(start)
    if end:   q += " AND ts<=?"; args.append(end)
    q += " ORDER BY ts"
    init_bars(db)
    with _conn(db) as c:
        rows = [dict(r) for r in c.execute(q, args).fetchall()]
    if timeframe in ("1m", "1d") or not rows:
        return rows
    # resample 1m -> Nm within each session (bucket by floor of minutes)
    n = int(timeframe[:-1])
    out, bucket = [], None
    for r in rows:
        # Convert UTC ts string (trailing Z) to timezone-aware UTC datetime, then convert to IST
        utc_ts = r["ts"].replace('Z', '+00:00')
        t = datetime.fromisoformat(utc_ts)
        t_ist = t.astimezone(IST)
        mins = t_ist.hour * 60 + t_ist.minute
        key = (t_ist.date(), (mins - (9 * 60 + 15)) // n)   # session-anchored at 09:15 IST
        if bucket is None or bucket["key"] != key:
            if bucket: out.append({k: bucket[k] for k in
                ("symbol", "timeframe", "ts", "open", "high", "low", "close", "volume", "open_interest")})
            bucket = {"key": key, "symbol": r["symbol"], "timeframe": timeframe,
                      "ts": r["ts"], "open": r["open"], "high": r["high"],
                      "low": r["low"], "close": r["close"],
                      "volume": r["volume"] or 0,
                      "open_interest": r.get("open_interest")}
        else:
            bucket["high"] = max(bucket["high"], r["high"])
            bucket["low"] = min(bucket["low"], r["low"])
            bucket["close"] = r["close"]
            bucket["volume"] += r["volume"] or 0
            if "open_interest" in r:
                bucket["open_interest"] = r["open_interest"]
    if bucket: out.append({k: bucket[k] for k in
        ("symbol", "timeframe", "ts", "open", "high", "low", "close", "volume", "open_interest")})
    return out


def get_stored_symbols(db=DB_PATH) -> list[str]:
    """Get all unique symbols that have price bars stored in the DB."""
    init_bars(db)
    with _conn(db) as c:
        rows = c.execute("SELECT DISTINCT symbol FROM price_bars ORDER BY symbol").fetchall()
        return [r["symbol"] for r in rows]


def get_bar_range(exchange: str, symbol: str, timeframe: str, db=DB_PATH) -> dict:
    """Get the min timestamp, max timestamp, and row count for a symbol/timeframe."""
    init_bars(db)
    # Map friendly interval names to stored schema names
    tf = "1d" if timeframe == "1day" else "1m" if timeframe == "1minute" else timeframe
    with _conn(db) as c:
        row = c.execute(
            "SELECT MIN(ts), MAX(ts), COUNT(*) FROM price_bars WHERE exchange=? AND symbol=? AND timeframe=?",
            (exchange, symbol, tf)
        ).fetchone()
        if row and row[2] > 0:
            return {
                "min_date": row[0],
                "max_date": row[1],
                "count": row[2]
            }
        return {
            "min_date": None,
            "max_date": None,
            "count": 0
        }


def get_latest_vix(before_ts=None, db=DB_PATH, with_source=False):
    """Last INDIAVIX value knowable at `before_ts`. THE single VIX source (D-SC-05).

    Read from price_bars, never from `captures.vix` — that column is a constant 12.0
    across all 13,126 captures (a placeholder, not data), so anything sourcing VIX from
    a capture row is reading a fabricated number.

    NO LOOKAHEAD, and the two timeframes need different rules:

      * 1m bars carry a real intraday timestamp ('2026-08-14T10:00:00Z'), so
        `ts <= before_ts` is a true backward as-of join. Preferred whenever the 1m
        series covers the moment.
      * 1d bars are stamped at MIDNIGHT of their trading date ('2026-08-14T00:00:00')
        but carry that date's CLOSE. A naive `ts <= before_ts` would therefore hand a
        09:30 IST decision that same evening's closing VIX. Daily bars are visible only
        from a STRICTLY EARLIER trading date — unless `before_ts` is at or after the
        15:30 IST close, when that day's own close is legitimately known.

    Returns the float, or (float, source) when `with_source=True` so callers can record
    which series answered ('1m' | '1d' | None).
    """
    init_bars(db)
    Q1M = ("SELECT close FROM price_bars WHERE symbol='INDIAVIX' AND timeframe='1m' "
           "{where} ORDER BY ts DESC LIMIT 1")
    Q1D = ("SELECT close FROM price_bars WHERE symbol='INDIAVIX' AND timeframe='1d' "
           "{where} ORDER BY ts DESC LIMIT 1")
    out, src = None, None
    with _conn(db) as c:
        if not before_ts:
            row = c.execute(Q1M.format(where="")).fetchone()
            if row: out, src = row[0], "1m"
            else:
                row = c.execute(Q1D.format(where="")).fetchone()
                if row: out, src = row[0], "1d"
        else:
            row = c.execute(Q1M.format(where="AND ts<=?"), (before_ts,)).fetchone()
            if row:
                out, src = row[0], "1m"
            else:
                # fall back to the daily series, applying the midnight-stamp rule
                try:
                    t = before_ts.replace("Z", "+00:00")
                    d = datetime.fromisoformat(t)
                    if d.tzinfo is None:
                        d = d.replace(tzinfo=timezone.utc)
                    ist = d.astimezone(IST)
                except Exception:
                    ist = None
                if ist is None:
                    row = None
                else:
                    after_close = (ist.hour, ist.minute) >= (15, 30)
                    cutoff = ist.strftime("%Y-%m-%dT00:00:00")
                    op = "<=" if after_close else "<"
                    row = c.execute(Q1D.format(where=f"AND ts{op}?"), (cutoff,)).fetchone()
                if row: out, src = row[0], "1d"
    return (out, src) if with_source else out


def realized_vol(exchange="NSE", symbol="NIFTY", days=20, db=DB_PATH):
    """Annualized close-to-close realized vol from DAILY bars — the input
    vol_attribution has been missing. Also intraday (Parkinson) from 1m ranges."""
    daily = get_bars(exchange, symbol, "1d", db=db)[-days - 1:]
    if len(daily) < 3:
        return {"error": "not enough daily bars"}
    rets = [math.log(daily[i]["close"] / daily[i - 1]["close"])
            for i in range(1, len(daily))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    ann = math.sqrt(var * 252) * 100
    return {"realized_vol_pct": round(ann, 2), "n_days": len(rets),
            "note": "close-to-close, annualized. Compare vs ATM IV: "
                    "IV >> realized = variance premium rich (selling favored); "
                    "IV < realized = vol underpriced."}


if __name__ == "__main__":
    import os, random
    if os.path.exists("bt.db"): os.remove("bt.db")
    # synthetic: one session of 1m bars + 30 daily bars
    random.seed(7); px = 24175.0; rows1m = []
    for i in range(375):
        h, m = divmod(9 * 60 + 15 + i, 60)
        o = px; px += random.gauss(0.1, 6); c = px
        rows1m.append((f"2026-07-03T{h:02d}:{m:02d}:00+05:30",
                       o, max(o, c) + 3, min(o, c) - 3, c, random.randint(50, 500) * 1000))
    save_bars(rows1m, timeframe="1m", db="bt.db")
    pd = 23500.0; rowsd = []
    for i in range(30):
        o = pd; pd += random.gauss(20, 120); c = pd
        rowsd.append((f"2026-06-{(i % 30) + 1:02d}T09:15:00+05:30",
                      o, max(o, c) + 60, min(o, c) - 60, c, None))
    save_bars(rowsd, timeframe="1d", db="bt.db")

    print(f"1m bars: {len(get_bars(timeframe='1m', db='bt.db'))}")
    print(f"5m resampled: {len(get_bars(timeframe='5m', db='bt.db'))} (should be ~75)")
    print(f"15m resampled: {len(get_bars(timeframe='15m', db='bt.db'))} (should be ~25)")
    rv = realized_vol(days=20, db="bt.db")
    print(f"realized vol: {rv['realized_vol_pct']}% over {rv['n_days']}d")
    os.remove("bt.db")
