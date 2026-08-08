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
from datetime import datetime, timedelta

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
    # LTIMindtree: L&T Infotech absorbed Mindtree in Nov-2022 and the NSE symbol
    # became LTIM. A plain LTIM.NS returned nothing on the full re-pull, so the
    # predecessors are tried too — whichever yields the most bars wins, and the run
    # prints which one, so a dead ticker is visible instead of a silent empty result.
    "LTIM": ["LTIM.NS", "LTIMINDTREE.NS", "LTI.NS", "MINDTREE.NS"],
    # Indices carry no '.NS' suffix.
    "NIFTY": ["^NSEI"],
    "BANKNIFTY": ["^NSEBANK"],
}

# Index tickers. Yahoo has renamed Indian index symbols more than once, and the
# ^CNX* family in the old sync_sectors_yf.py appears to be dead — every sector row
# in price_bars carries the Breeze 'Z' timestamp, not the format that script writes,
# so it has not been the source of this data. Each index therefore lists CANDIDATES;
# fetch_best() keeps whichever returns the most bars and reports the winner, so one
# run tells us which are alive instead of failing silently.
INDEX_TICKERS = {
    "NIFTY":        ["^NSEI"],
    "BANKNIFTY":    ["^NSEBANK"],
    "NIFTYIT":      ["^CNXIT", "NIFTY_IT.NS"],
    "NIFTYAUTO":    ["^CNXAUTO", "NIFTY_AUTO.NS"],
    "NIFTYFIN":     ["^CNXFIN", "NIFTY_FIN_SERVICE.NS"],
    "NIFTYFMCG":    ["^CNXFMCG", "NIFTY_FMCG.NS"],
    "NIFTYMETAL":   ["^CNXMETAL", "NIFTY_METAL.NS"],
    "NIFTYPHARMA":  ["^CNXPHARMA", "NIFTY_PHARMA.NS"],
    "NIFTYENERGY":  ["^CNXENERGY", "NIFTY_ENERGY.NS"],
    "NIFTYCONSUM":  ["^CNXCONSUM", "NIFTY_CONSUMPTION.NS"],
    "NIFTYINFRA":   ["^CNXINFRA", "NIFTY_INFRA.NS"],
    "NIFTYMEDIA":   ["^CNXMEDIA", "NIFTY_MEDIA.NS"],
    "NIFTYPSU":     ["^CNXPSUBANK", "NIFTY_PSU_BANK.NS"],
    "NIFTYREALTY":  ["^CNXREALTY", "NIFTY_REALTY.NS"],
    "INDIAVIX":     ["^INDIAVIX"],
}
TICKER_ALTS.update(INDEX_TICKERS)

# ONE SYMBOL = ONE INSTRUMENT, ONE VENUE, ONE CURRENCY.
#
# This is the convention every source has to be normalised onto, and CRUDEOIL is
# what happens without it. Two feeds wrote the same symbol:
#
#   1d  2018-01-02..2025-07-30   USD, NYMEX (Yahoo CL=F)   close -37.6..123.7
#   1d  2026-02-20..present      INR, MCX   (Upstox)       close 5886..9230
#   1m  2026-06-29..present      INR, MCX   (Upstox)
#
# giving an 84x "price move" on 2026-02-20 that is purely a change of currency, and
# a 6.5-month hole where neither feed wrote. impact_monitor.py reads CRUDEOIL with
# a 4% threshold, so that jump is a live false signal, and oil is the top-ranked
# macro factor in the Nifty view.
#
# Both feeds are the SAME commodity — CL=F is WTI and MCX crude is WTI-linked; the
# -37.63 print on 2020-04-20 is the WTI negative settlement, which Brent never had.
# So this is a currency/venue split, not an instrument mismatch:
#
#   CRUDEOIL      -> USD, NYMEX, from CL=F. Eight years, continuous. The macro series.
#   CRUDEOIL_MCX  -> INR, MCX. The tradeable contract, 1m and 1d.
#
# Nothing is currency-converted at WRITE time. Store native, convert at read using
# the USDINR series already in price_bars — same reason bar_store stores raw bars.
COMMODITY_TICKERS = {
    "CRUDEOIL": ["CL=F"],       # WTI, USD/bbl — the long macro history
    "BRENT": ["BZ=F"],          # only if a genuine Brent series is ever wanted
}
TICKER_ALTS.update(COMMODITY_TICKERS)

# Native currency per symbol, so a consumer can never silently mix two. Anything
# absent is INR (the NSE default).
NATIVE_CCY = {
    "CRUDEOIL": "USD", "BRENT": "USD",
    "CRUDEOIL_MCX": "INR", "GOLD": "INR", "SILVER": "INR", "COPPER": "INR",
    "USDINR": "INR",
}

# Real listing dates. Requesting bars before these returns nothing, which would
# otherwise look like a fetch failure — so callers clamp instead of alarming.
LISTED_FROM = {
    "ZOMATO": "2021-07-23",      # Zomato IPO (renamed Eternal in 2025)
    "ETERNAL": "2021-07-23",
    "JIOFIN": "2023-08-21",      # demerged out of Reliance
    "MAXHEALTH": "2020-08-21",
}


# VENDOR ADJUSTMENT DEFECTS — corrections to what Yahoo actually serves.
#
# auto_adjust=True is supposed to back-adjust ALL history before a split/bonus. For
# TRENT it does not: Yahoo applies the 1:2 bonus from 2026-01-01, five months before
# the true ex-date, leaving every earlier bar at raw scale. The result is a 33% cliff
# on a day with no corporate action, and no discontinuity on the day there was one.
#
# This is NOT a stitching artifact from incremental downloads — a full fresh
# --replace re-download reproduces it byte for byte. It is the vendor's data.
#
# Applied at FETCH time so the correction survives every future re-download rather
# than having to be re-applied to the database by hand.
VENDOR_ADJUSTMENTS = [
    {
        "symbol": "TRENT",
        "boundary": "2026-01-01",   # first bar Yahoo serves already adjusted
        "ratio": 2.0 / 3.0,         # 1:2 bonus -> price x 2/3
        "true_ex_date": "2026-06-04",
        "note": ("1:2 bonus, record/ex date 2026-06-04 (Trent's first). Yahoo adjusts "
                 "from 2026-01-01 instead. Verified against the traded price: our "
                 "2026-06-03 close 2834.21 x 1.5 = 4251 vs 4257.60 actually traded, so "
                 "post-boundary bars are correct and pre-boundary bars are raw. Scaling "
                 "pre-boundary prices by 2/3 makes the series continuous and moves the "
                 "1Y return from -42.1% (phantom) to -13.1% (real)."),
        # Prices only. Volume shows no step across the boundary (~0.4-1.3M either
        # side), and the reaction detector keys off volume, so leave it untouched.
        "prices_only": True,
    },
]


# REAL discontinuities that must NOT be scaled away and must NOT be re-flagged.
# Distinct from VENDOR_ADJUSTMENTS: those are the vendor getting an action wrong,
# these are actions the vendor correctly does not adjust for. A backstop that warns
# about the same known gap every single day is a backstop nobody reads.
KNOWN_REAL_GAPS = {
    # Genuine market history. Each verified before being silenced — an entry here
    # stops the audit ever asking again, so guessing would hide a future defect.
    ("HDFCAMC", "2020-03-23"): "COVID crash — Nifty fell ~13% in one session, its worst ever",
    ("LTIM", "2020-03-23"): "COVID crash — same session",
    ("BANDHANBNK", "2018-10-01"): "fell ~20% on the Gruh Finance merger announcement",
    ("CRUDEOIL", "2020-03-09"): "Saudi-Russia price war — WTI fell ~25% in a session",
    ("CRUDEOIL", "2020-04-22"): "rebound off the 2020-04-20 negative settlement (-$37.63)",
    # NOT whitelisted, deliberately: GOLD 2026-02-02 (0.8356) and SILVER 2026-02-02
    # (0.5977). Both look like MCX contract rolls rather than market moves, but that
    # is a guess and the whole point of this list is that entries are verified.
    ("SYNGENE", "2020-09-14"):
        "+18.5% on the COVID ELISA test-kit run — ICMR approval was announced that "
        "week and the stock gapped up. News, not a corporate action (an action would "
        "move price DOWN at these ratios).",
    ("GOLD", "2026-02-02"):
        "MCX contract roll, not a market move. The preceding bars are far-month "
        "prints with volume 0-2 and open==close (carried forward); 2026-02-02 is the "
        "first bar of the next contract at volume 83. Gold did not fall 16% that day.",
    ("SILVER", "2026-02-02"):
        "MCX contract roll — same signature: 450,414 at volume 0 then 269,201 at "
        "volume 8. A 40% single-day fall in silver did not happen.",
    ("TATAMOTORS", "2025-10-14"):
        "demerger — PV+JLR (TMPV) retained, TMLCV spun off 1:1, market split ~61/39 "
        "(ratio 0.6054). Yahoo does not adjust demergers, so the discontinuity is "
        "genuine and stays in the stored series; consumers apply the ratio at read "
        "time via _KNOWN_ACTIONS in data_agent/fundamentals/earnings_reaction_backfill.py.",
}


def apply_vendor_adjustments(rows, symbol, log=print):
    """Repair known vendor mis-dated corporate actions. Returns rows, possibly scaled."""
    out = rows
    for adj in VENDOR_ADJUSTMENTS:
        if adj["symbol"] != symbol.upper():
            continue
        b, k = adj["boundary"], adj["ratio"]
        n = sum(1 for r in out if r[0][:10] < b)
        if not n:
            continue
        out = [((r[0], r[1] * k, r[2] * k, r[3] * k, r[4] * k, r[5])
                if r[0][:10] < b else r) for r in out]
        log(f"   vendor fix: scaled {n} bars before {b} by {k:.4f} "
            f"(true ex-date {adj['true_ex_date']})")
    return out


# EXCHANGE IS PART OF THE PRIMARY KEY — (exchange, symbol, timeframe, ts).
#
# So writing a symbol under the wrong exchange does not overwrite the existing
# series, it creates a SECOND one beside it. That is exactly what a hardcoded
# exchange="NSE" default did to CRUDEOIL: the full re-pull wrote 2,163 NSE rows
# next to the 1,906 existing NYMEX rows, duplicating 1,906 trading dates with
# identical values. Same failure as the ts-format split, one column over.
#
# Resolution order is: explicit argument, then whatever the symbol ALREADY uses in
# this database, then this map, then NSE. Adopting the stored exchange matters most
# — it means a symbol can never be forked by a default, whatever the map says.
SYMBOL_EXCHANGE = {
    "CRUDEOIL": "NYMEX",
    "BRENT": "NYMEX",
    "CRUDEOIL_MCX": "MCX", "GOLD": "MCX", "SILVER": "MCX", "COPPER": "MCX",
    "USDINR": "CDS",
    "NIFTY_FUT_1": "NFO", "NIFTY_FUT_2": "NFO",
}


def resolve_exchange(symbol, db=None, explicit=None, timeframe="1d"):
    """The exchange this symbol is already stored under, or its documented home."""
    if explicit:
        return explicit
    if db:
        try:
            con = sqlite3.connect(db)
            rows = [r[0] for r in con.execute(
                "select distinct exchange from price_bars where symbol=? and timeframe=?",
                (symbol, timeframe))]
            con.close()
            if len(rows) == 1:
                return rows[0]
            if len(rows) > 1:
                # Already forked. Prefer the documented one and let the audit shout.
                want = SYMBOL_EXCHANGE.get(symbol.upper())
                return want if want in rows else sorted(rows)[0]
        except Exception:
            pass
    return SYMBOL_EXCHANGE.get(symbol.upper(), "NSE")


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
    if best:
        best = apply_vendor_adjustments(best, symbol, log=log)
    return best, best_ticker


def write_daily(rows, symbol, db, exchange=None, replace=False):
    """Insert daily bars directly. See the module docstring for why not save_bars.

    exchange defaults to whatever this symbol already uses in the database rather
    than to 'NSE' — a wrong exchange forks the series instead of updating it.
    """
    exchange = resolve_exchange(symbol, db=db, explicit=exchange)
    con = sqlite3.connect(db)

    # PURGE FOREIGN TS FORMATS FIRST — the single most repeated failure in this table.
    #
    # ts is part of the primary key, so INSERT OR REPLACE cannot overwrite a row whose
    # ts is spelled differently. Writing '2018-01-02T00:00:00' over an existing
    # '2018-01-02T00:00:00Z' does not update it, it ADDS a second row for the same
    # session, and every date join downstream then double-counts.
    #
    # This has now happened three times: Breeze vs Yahoo on the constituents, the
    # tz-shifted backfill, and the sector indices (BANKNIFTY and NIFTYIT each ended up
    # with ~2,117 sessions stored twice). backfill_daily_bars refuses without
    # --replace; sync_symbols had no guard at all. So the check belongs HERE, at the
    # one place every daily write passes through, rather than in each caller.
    #
    # A differently-formatted row for the same symbol at 1d is always the same session
    # from an older writer, so removing it is a de-duplication, not a data loss.
    foreign = [f[0] for f in con.execute(
        "select distinct substr(ts,11) from price_bars where symbol=? and "
        "timeframe='1d' and exchange=? and substr(ts,11)!=?",
        (symbol, exchange, "T00:00:00"))]
    if foreign and not replace:
        n = con.execute(
            "delete from price_bars where symbol=? and timeframe='1d' and "
            "exchange=? and substr(ts,11)!=?",
            (symbol, exchange, "T00:00:00")).rowcount
        print(f"   {symbol}: purged {n} rows in a foreign ts format "
              f"({','.join(foreign)}) before writing")
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


def sync_symbols(symbols, db, *, full=False, from_date="2018-01-01",
                 overlap_days=5, log=print):
    """Incremental daily sync for any symbol set. The ONE routine every sync uses.

    symbols may be a list, or a {db_symbol: [yahoo_tickers]} mapping for names not
    already in TICKER_ALTS.

    Incremental by default: re-fetch a few sessions behind the stored watermark so
    late vendor corrections land, without re-pulling years. `full=True` re-pulls
    from from_date, which is what you need after a split or bonus — those make the
    vendor re-adjust the ENTIRE history while an incremental run only rewrites the
    tail, leaving a scale break at the join.

    Returns {symbol: (bars_written, ticker_used)}; ticker_used is None when nothing
    resolved, which is how a dead ticker announces itself instead of failing quietly.
    """
    if isinstance(symbols, dict):
        for sym, tks in symbols.items():
            TICKER_ALTS.setdefault(sym.upper(), tks)
        symbols = list(symbols)

    con = sqlite3.connect(db)
    floor_default = datetime.strptime(from_date, "%Y-%m-%d")
    end = datetime.now() + timedelta(days=1)
    out = {}
    for sym in symbols:
        sym = sym.upper()
        floor = floor_default
        if sym in LISTED_FROM:
            listed = datetime.strptime(LISTED_FROM[sym], "%Y-%m-%d")
            floor = max(floor, listed)
        wm = None
        if not full:
            r = con.execute("select max(ts) from price_bars where symbol=? "
                            "and timeframe='1d'", (sym,)).fetchone()
            wm = r[0][:10] if r and r[0] else None
        start = (max(floor, datetime.strptime(wm, "%Y-%m-%d") - timedelta(days=overlap_days))
                 if wm else floor)
        if start >= end:
            out[sym] = (0, "up-to-date")
            continue
        rows, ticker = fetch_best(sym, start, end, log=lambda *_: None)
        if not rows:
            log(f"  {sym:14} NO DATA — tried {', '.join(tickers_for(sym))}")
            out[sym] = (0, None)
            continue
        n, _ = write_daily(rows, sym, db)
        out[sym] = (n, ticker)
        log(f"  {sym:14} {n:>5} bars via {ticker}"
            f"{'  (full)' if full else f'  from {start.date()}'}")
    con.close()
    return out


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
