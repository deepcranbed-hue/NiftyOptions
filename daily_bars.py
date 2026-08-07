"""daily_bars.py — the ONE place daily (1d) equity bars are fetched and written.

WHY THIS MODULE EXISTS
----------------------
Daily bars used to arrive by two different routes, and the mismatch between them
caused every data bug in this table:

  Yahoo   -> ts '2018-01-01T00:00:00'    split+dividend adjusted   full history
  Breeze  -> ts '2025-08-06T00:00:00Z'   raw traded prices         ~1 YEAR ONLY

Because `ts` is part of the primary key (exchange, symbol, timeframe, ts), those
two strings are DIFFERENT KEYS FOR THE SAME SESSION. So the two feeds never
overwrote each other — they accumulated side by side. Consequences seen in this
database:

  * TATAMOTORS / ZOMATO held 246 bars instead of 2,126: re-added after a
    corporate-identity change, they were picked up by the Breeze path, which
    cannot serve more than a year of daily history.
  * NIFTY holds 22 duplicated sessions — the same day stored once by Yahoo
    ('T00:00:00Z') and once by Breeze ('T03:45:00Z', i.e. 09:15 IST). Any join
    on date double-counts them, and NIFTY is the benchmark in
    earnings_reaction_backfill.

Breeze remains correct for 1-minute bars, options chains and FUTURES daily bars
(Yahoo carries no NSE futures series). It is only removed from the EQUITY daily
path. Upstox-based downloads are untouched.

A NOTE ON bar_store.save_bars — DO NOT USE IT FOR 1d
----------------------------------------------------
save_bars routes ts through backend.timeutil.to_db_ts, which treats a naive
timestamp as IST and converts it to UTC. For a minute bar that is right. For a
daily bar, whose identity is its trading DATE, 2018-01-01 00:00 IST becomes
2017-12-31T18:30:00Z — the session silently moves to the previous calendar day,
and every Monday lands on a Sunday. write_daily() therefore inserts directly.

PRICE BASIS
-----------
auto_adjust=True, matching the 46 symbols already loaded that way (it is why
RELIANCE opens 2018 at ~405.71 rather than its ~918 traded price). A table on one
basis is worth more than a table that is half-adjusted.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

# The one true storage format for a daily bar: the trading date, no timezone
# conversion, no 'Z'. Matches the 46 correct symbols.
TS_FMT = "%Y-%m-%dT00:00:00"

# Yahoo tickers. Renames and demergers are exactly where the NSE symbol and the
# Yahoo ticker diverge, so a symbol may list several; the one returning the most
# rows wins. Extend rather than edit when NSE renames again.
TICKER_ALTS = {
    "TATAMOTORS": ["TATAMOTORS.NS", "TMPV.NS"],   # Oct-2025 demerger + rename
    "ZOMATO": ["ETERNAL.NS", "ZOMATO.NS"],        # renamed Eternal, 2025
    "ETERNAL": ["ETERNAL.NS", "ZOMATO.NS"],
    # Indices carry no '.NS' suffix.
    "NIFTY": ["^NSEI"],
    "BANKNIFTY": ["^NSEBANK"],
}

# Real listing dates. Requesting bars before these returns nothing, which would
# otherwise look like a fetch failure — so callers clamp instead of alarming.
LISTED_FROM = {
    "ZOMATO": "2021-07-23",      # Zomato IPO (renamed Eternal in 2025)
    "ETERNAL": "2021-07-23",
    "JIOFIN": "2023-08-21",      # demerged out of Reliance
    "MAXHEALTH": "2020-08-21",
}


def tickers_for(symbol):
    return TICKER_ALTS.get(symbol.upper(), [f"{symbol}.NS"])


def fetch_daily(ticker, start, end):
    """Daily bars from Yahoo as [(ts, o, h, l, c, v)], dates in IST."""
    import yfinance as yf
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), interval="1d",
                     auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return []
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df = df.droplevel(1, axis=1)

    # TIMEZONE — yfinance returns a tz-aware index for .NS tickers. Midnight IST
    # is 18:30 UTC on the PREVIOUS day, so taking the date off a UTC-expressed
    # timestamp yields 2017-12-31 for the 2018-01-01 session — a Sunday, when NSE
    # is shut. Convert to IST before taking the calendar date, never after.
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        try:
            idx = idx.tz_convert("Asia/Kolkata")
        except Exception:
            idx = idx.tz_localize(None)

    rows = []
    for when, (_, r) in zip(idx, df.iterrows()):
        try:
            rows.append((when.strftime(TS_FMT), float(r["Open"]), float(r["High"]),
                         float(r["Low"]), float(r["Close"]), float(r["Volume"])))
        except Exception:
            continue
    return rows


def fetch_best(symbol, start, end, log=print):
    """Try each candidate ticker; keep the one returning the most rows."""
    best, best_ticker = [], None
    for tk in tickers_for(symbol):
        try:
            rows = fetch_daily(tk, start, end)
        except Exception as e:
            log(f"   [{tk}] error: {str(e)[:70]}")
            continue
        log(f"   [{tk}] {len(rows)} bars")
        if len(rows) > len(best):
            best, best_ticker = rows, tk
    return best, best_ticker


def write_daily(rows, symbol, db, exchange="NSE", replace=False):
    """Insert daily bars directly. See the module docstring for why not save_bars."""
    con = sqlite3.connect(db)
    purged = 0
    if replace:
        purged = con.execute(
            "delete from price_bars where symbol=? and timeframe='1d'",
            (symbol,)).rowcount
    con.executemany(
        "INSERT OR REPLACE INTO price_bars"
        "(exchange, symbol, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?, ?, '1d', ?, ?, ?, ?, ?, ?)",
        [(exchange, symbol, r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows])
    con.commit()
    con.close()
    return len(rows), purged


def verify_daily(symbol, db, reference="RELIANCE", log=print):
    """Re-read from the DB and check THAT — not the rows held in memory.

    An earlier run passed its checks and still stored shifted dates, because the
    checks ran before the write and the write was what corrupted them.

    Three tests, because each catches what the others miss:
      format   — a stray 'Z' means a second key for the same session;
      weekend  — a shifted series lands on Sat/Sun. A few are real: NSE runs a
                 Muhurat session on Diwali and traded Saturday 2025-02-01 for the
                 Budget, so tolerate a handful and flag hundreds;
      calendar — overlap with a symbol known to be correct. A uniform one-day
                 shift still scores ~76%, because Tue-Fri sessions land on other
                 trading days; only Mondays fall off. So the bar COUNT looks
                 perfect and only this test fails. Threshold is deliberately 98%.
    """
    con = sqlite3.connect(db)
    got = sorted(r[0] for r in con.execute(
        "select ts from price_bars where symbol=? and timeframe='1d'", (symbol,)))
    ref = {r[0][:10] for r in con.execute(
        "select ts from price_bars where symbol=? and timeframe='1d'", (reference,))}
    con.close()
    if not got:
        log("   VERIFY: no rows found after write.")
        return False
    dates = {t[:10] for t in got}
    fmts = sorted({t[10:] for t in got})
    wk = [d for d in dates if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5]
    win = {d for d in ref if min(dates) <= d <= max(dates)}
    ov = 100.0 * len(dates & win) / len(win) if win else 0.0
    ok = ov >= 98 and len(wk) <= 10 and fmts == ["T00:00:00"]
    log(f"   VERIFY (read back): {len(got)} rows  {min(dates)} -> {max(dates)}  "
        f"fmt={','.join(fmts)}  weekend={len(wk)}  calendar={ov:.1f}%  "
        f"[{'OK' if ok else 'FAILED'}]")
    if not ok:
        log("      Stored dates do not match the exchange calendar — do NOT trust "
            "this series or re-copy the mirror.")
    return ok


def duplicate_dates(db, timeframe="1d"):
    """Sessions stored more than once — the signature of two feeds, two ts formats."""
    con = sqlite3.connect(db)
    rows = con.execute(
        "select symbol, count(*) from (select symbol, substr(ts,1,10) d, count(*) n "
        "from price_bars where timeframe=? group by 1,2 having n>1) group by 1 "
        "order by 2 desc", (timeframe,)).fetchall()
    con.close()
    return rows


def drop_intraday_duplicates(db, timeframe="1d"):
    """Remove the Breeze-origin half of a duplicated session.

    When one trading date holds two rows, they differ only by the time part of
    ts: the Yahoo row sits at midnight ('T00:00:00' / 'T00:00:00Z') and the
    Breeze row at the session time ('T03:45:00Z' = 09:15 IST). A daily bar has
    no intraday time, so the midnight row is the correct one to keep.

    Verified equal before deleting: NIFTY's pairs hold the same OHLC to float
    precision (25661.65 vs 25661.650390625), so this drops redundancy, not data.
    Returns [(symbol, rows_deleted, sample_ts)].
    """
    con = sqlite3.connect(db)
    dup_dates = con.execute(
        "select symbol, substr(ts,1,10) d from price_bars where timeframe=? "
        "group by 1,2 having count(*)>1", (timeframe,)).fetchall()
    out = {}
    for sym, d in dup_dates:
        rows = con.execute(
            "select ts from price_bars where symbol=? and timeframe=? "
            "and substr(ts,1,10)=?", (sym, timeframe, d)).fetchall()
        # Keep midnight; delete anything stamped with a real intraday time.
        victims = [r[0] for r in rows if not r[0][11:].startswith("00:00:00")]
        if len(victims) == len(rows):        # no midnight row — leave it alone
            continue
        for ts in victims:
            con.execute("delete from price_bars where symbol=? and timeframe=? "
                        "and ts=?", (sym, timeframe, ts))
            n, sample = out.get(sym, (0, ts))
            out[sym] = (n + 1, sample)
    con.commit()
    con.close()
    return [(k, v[0], v[1]) for k, v in sorted(out.items())]
