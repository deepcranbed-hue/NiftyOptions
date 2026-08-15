"""split_mixed_symbols.py — separate feeds that were written under one symbol.

THE PROBLEM THIS EXISTS FOR
---------------------------
`price_bars` has no currency or venue column, so a symbol carries those implicitly.
When two feeds with different conventions write the same symbol, the series looks
continuous and is not — and nothing in the schema can object.

CRUDEOIL is the worked example:

    1d  2018-01-02..2025-07-30   USD, NYMEX (Yahoo CL=F)   close  -37.6 ..  123.7
    1d  2026-02-20..present      INR, MCX   (Upstox)       close 5886.0 .. 9230.0
    1m  2026-06-29..present      INR, MCX   (Upstox)

On 2026-02-20 the stored price rises 84x. No market did that; the currency changed.
`backend/quant/impact_monitor.py` reads CRUDEOIL against a 4% threshold, and oil is
the top-ranked macro factor in the Nifty view, so this is a live false signal rather
than a cosmetic blemish.

Both feeds are the SAME commodity. CL=F is WTI, MCX crude is WTI-linked, and the
-37.63 close on 2020-04-20 is the WTI negative settlement — Brent never printed
negative. So the fix is a clean split by currency, not a reconciliation of two
different oils.

WHAT IT DOES
------------
Moves the INR rows to CRUDEOIL_MCX and leaves the USD history as CRUDEOIL, using
the ts format as the discriminator ('Z' = the Upstox/Breeze writer, naive = Yahoo).
That is not a guess: the two formats partition the rows exactly along the price
break, which is checked before anything is written.

Idempotent, and --dry-run shows the plan. After running, the 2025-07-30..2026-02-20
hole is filled by the ordinary sync, because CRUDEOIL is now a normal Yahoo symbol:

    python data_agent/fetching/sync_commodities.py          # unchanged, INR side
    python -c "import sys; sys.path.insert(0,'data_agent/fetching'); \\
               from daily_bars import sync_symbols; sync_symbols(['CRUDEOIL'], DB, full=True)"
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "data_agent", "fetching"))
sys.path.append(_ROOT)

# (symbol, ts-suffix identifying the foreign feed) -> destination symbol
SPLITS = [
    {
        "symbol": "CRUDEOIL",
        # Per timeframe, WHICH rows are the foreign feed.
        #   1d — both feeds present; the ts suffix separates them exactly.
        #   1m — only the MCX feed ever wrote at this timeframe, so all of it moves.
        #        (Its suffix is the bar's own minute, 'T17:37:00Z', so a fixed-format
        #        match silently moves nothing — which is what the first run did.)
        "move_fmt": {"1d": "T00:00:00Z", "1m": "*"},
        "dest": "CRUDEOIL_MCX",
        "move_timeframes": ("1d", "1m"),
        "why": "INR MCX rows leaving the USD NYMEX history behind as CRUDEOIL",
    },
]


def _describe(con, sym, tf):
    r = con.execute("select count(*), min(ts), max(ts), min(close), max(close) "
                    "from price_bars where symbol=? and timeframe=?", (sym, tf)).fetchone()
    return r if r and r[0] else None


def run(db, dry=True):
    con = sqlite3.connect(db)
    for spec in SPLITS:
        sym, fmts, dest = spec["symbol"], spec["move_fmt"], spec["dest"]
        print(f"\n{sym} -> {dest}   ({spec['why']})")
        total = 0
        for tf in spec["move_timeframes"]:
            fmt = fmts[tf] if isinstance(fmts, dict) else fmts
            all_rows = fmt == "*"
            where_move = "1=1" if all_rows else "substr(ts,11)=?"
            where_keep = "1=0" if all_rows else "substr(ts,11)!=?"
            p_move = (sym, tf) if all_rows else (sym, tf, fmt)
            p_keep = (sym, tf) if all_rows else (sym, tf, fmt)
            rows = con.execute(
                "select count(*), min(ts), max(ts), min(close), max(close) from price_bars "
                f"where symbol=? and timeframe=? and {where_move}", p_move).fetchone()
            keep = con.execute(
                "select count(*), min(ts), max(ts), min(close), max(close) from price_bars "
                f"where symbol=? and timeframe=? and {where_keep}", p_keep).fetchone()
            if not rows or not rows[0]:
                print(f"   {tf}: nothing to move")
                continue

            # SAFETY: the formats must partition the data along the price break. If
            # the two groups overlap in value range, the format is not actually
            # tracking currency and this migration would scramble the series.
            if keep and keep[0]:
                moving_lo, moving_hi = rows[3], rows[4]
                keep_lo, keep_hi = keep[3], keep[4]
                if not (moving_lo > keep_hi or moving_hi < keep_lo):
                    print(f"   {tf}: REFUSING — value ranges overlap "
                          f"(moving {moving_lo:.1f}..{moving_hi:.1f}, "
                          f"keeping {keep_lo:.1f}..{keep_hi:.1f}). The ts format is "
                          f"not cleanly tracking the currency here; split by date "
                          f"instead after checking the data.")
                    continue
                print(f"   {tf}: move {rows[0]:>6} bars {rows[1][:10]}..{rows[2][:10]} "
                      f"close {moving_lo:.1f}..{moving_hi:.1f}")
                print(f"   {tf}: keep {keep[0]:>6} bars {keep[1][:10]}..{keep[2][:10]} "
                      f"close {keep_lo:.1f}..{keep_hi:.1f}")
            else:
                print(f"   {tf}: move {rows[0]:>6} bars {rows[1][:10]}..{rows[2][:10]} "
                      f"(no rows of the other format)")

            if not dry:
                n = con.execute(
                    "update or replace price_bars set symbol=? where symbol=? "
                    f"and timeframe=? and {where_move}",
                    (dest,) + p_move).rowcount
                total += n
        if not dry:
            con.commit()
            print(f"   moved {total} rows")
            for tf in spec["move_timeframes"]:
                for s in (sym, dest):
                    d = _describe(con, s, tf)
                    if d:
                        print(f"   after: {s:14} {tf}  {d[0]:>6} bars  "
                              f"{d[1][:10]}..{d[2][:10]}  close {d[3]:.1f}..{d[4]:.1f}")
    con.close()


def fold_forked_exchange(db, dry=True):
    """Collapse a symbol stored under two exchanges back onto one.

    exchange is part of the primary key, so a wrong exchange forks the series
    rather than updating it. A hardcoded exchange="NSE" default did this to
    CRUDEOIL: a full re-pull wrote 2,163 NSE rows beside 1,906 existing NYMEX
    rows, duplicating every date in the overlap with identical values.

    Keeps the exchange daily_bars.SYMBOL_EXCHANGE documents for the symbol (or the
    one holding the most rows), and only deletes a row from the losing exchange
    when the SAME ts already exists on the winner — so nothing unique is dropped;
    the rest is moved across.
    """
    from daily_bars import SYMBOL_EXCHANGE
    con = sqlite3.connect(db)
    forked = [r[0] for r in con.execute(
        "select symbol from price_bars where timeframe='1d' "
        "group by symbol having count(distinct exchange)>1")]
    if not forked:
        print("\nno symbol is forked across exchanges.")
        con.close()
        return
    for sym in forked:
        counts = dict(con.execute(
            "select exchange, count(*) from price_bars where symbol=? and "
            "timeframe='1d' group by 1", (sym,)))
        want = SYMBOL_EXCHANGE.get(sym.upper())
        keep = want if want in counts else max(counts, key=counts.get)
        losers = [e for e in counts if e != keep]
        print(f"\n{sym}: {counts}  ->  keep {keep}")
        for loser in losers:
            dupes = con.execute(
                "select count(*) from price_bars a where a.symbol=? and a.timeframe='1d' "
                "and a.exchange=? and exists (select 1 from price_bars b where "
                "b.symbol=a.symbol and b.timeframe='1d' and b.exchange=? and b.ts=a.ts)",
                (sym, loser, keep)).fetchone()[0]
            uniq = counts[loser] - dupes
            print(f"   {loser}: {dupes} duplicate ts (delete), {uniq} unique (move to {keep})")
            if not dry:
                con.execute(
                    "delete from price_bars where symbol=? and timeframe='1d' and "
                    "exchange=? and exists (select 1 from price_bars b where "
                    "b.symbol=price_bars.symbol and b.timeframe='1d' and "
                    "b.exchange=? and b.ts=price_bars.ts)", (sym, loser, keep))
                con.execute("update or replace price_bars set exchange=? where symbol=? "
                            "and timeframe='1d' and exchange=?", (keep, sym, loser))
        if not dry:
            con.commit()
            after = dict(con.execute(
                "select exchange, count(*) from price_bars where symbol=? and "
                "timeframe='1d' group by 1", (sym,)))
            print(f"   after: {after}")
    con.close()


def fold_ts_formats(db, dry=True, canon="T00:00:00"):
    """Collapse a symbol carrying two ts spellings of the same session onto one.

    Same root cause as fold_forked_exchange, different key column: ts is part of the
    primary key, so '...T00:00:00Z' and '...T00:00:00' are two rows for one trading
    day. The sector sync produced this at scale — BANKNIFTY and NIFTYIT each ended up
    with ~2,117 sessions stored twice.

    Keeps the canonical spelling. A non-canonical row is deleted only when the same
    DATE already exists in canonical form; otherwise it is rewritten to canonical, so
    no session is ever lost.

    The set arithmetic is done in Python on purpose. The obvious SQL — a correlated
    EXISTS over the same table — makes SQLite rescan a 329MB file per row and does
    not finish; pulling two date sets and diffing them is O(n).
    """
    con = sqlite3.connect(db)
    # Any symbol holding at least one non-canonical row — NOT just mixed ones.
    # Selecting on "more than one distinct format" missed GOLD, COPPER and INDIAVIX,
    # which are 100% 'T00:00:00Z': one distinct format, all of it wrong.
    mixed = [r[0] for r in con.execute(
        "select distinct symbol from price_bars where timeframe='1d' "
        "and substr(ts,11)!=? order by 1", (canon,))]
    if not mixed:
        print("\nevery 1d symbol is already on the canonical ts format.")
        con.close()
        return

    for sym in mixed:
        # rowid, not ts. The only index is (exchange, symbol, timeframe, ts); a
        # predicate without `exchange` cannot use its leftmost prefix, so deleting by
        # (symbol, ts) full-scans a 329MB table per row and never finishes. Deleting
        # by rowid is the fastest path SQLite has.
        rows = con.execute(
            "select rowid, ts from price_bars where symbol=? and timeframe='1d'",
            (sym,)).fetchall()
        canon_dates = {t[:10] for _, t in rows if t[10:] == canon}
        others = [(rid, t) for rid, t in rows if t[10:] != canon]
        if not canon_dates:
            # PURELY non-canonical — GOLD, COPPER and INDIAVIX are 100% 'T00:00:00Z'
            # because their writer never wrote a canonical row (stale feed, so no new
            # bars arrived after the format fix). There is nothing to collide with, so
            # every midnight-stamped row is simply rewritten. Intraday-stamped rows are
            # still excluded — those are minute bars, not a spelling difference.
            midnightish = {"T00:00:00Z"}
            rewritable = [(r, t) for r, t in others if t[10:] in midnightish]
            skipped = len(others) - len(rewritable)
            print(f"\n{sym}: {len(others)} rows, none canonical — rewriting "
                  f"{len(rewritable)} to {canon}"
                  + (f", {skipped} intraday-stamped left alone" if skipped else ""))
            if not dry:
                con.executemany("update or replace price_bars set ts=? where rowid=?",
                                [(t[:10] + canon, r) for r, t in rewritable])
                con.commit()
                after = dict(con.execute(
                    "select substr(ts,11), count(*) from price_bars where symbol=? and "
                    "timeframe='1d' group by 1", (sym,)))
                print(f"   after: {after}")
            continue
        # A non-canonical row is only the SAME session in another spelling if its
        # time component is midnight. A real intraday stamp means a MINUTE bar was
        # written with timeframe='1d' — LTIM carries 225 such rows at T09:15:00 and
        # T09:51:00. Rewriting those to T00:00:00 would not de-duplicate anything, it
        # would fabricate 225 daily bars out of intraday snapshots. Report, never touch.
        midnightish = {"T00:00:00", "T00:00:00Z"}
        same_session = [(r, t) for r, t in others if t[10:] in midnightish]
        intraday = [(r, t) for r, t in others if t[10:] not in midnightish]
        dupes = [(r, t) for r, t in same_session if t[:10] in canon_dates]
        uniq = [(r, t) for r, t in same_session if t[:10] not in canon_dates]
        fmts = sorted({t[10:] for _, t in others})
        print(f"\n{sym}: {len(rows)} rows, {len(canon_dates)} canonical dates, "
              f"{len(others)} in {','.join(fmts)}")
        if intraday:
            times = sorted({t[10:] for _, t in intraday})
            print(f"   !! {len(intraday)} rows carry an INTRADAY stamp ({','.join(times)}) — "
                  f"minute bars stored as 1d.")
            print(f"      NOT converted: rewriting them would fabricate daily bars. "
                  f"Delete them from the 1d table separately once confirmed.")
        if not same_session:
            continue
        print(f"   delete {len(dupes)} duplicate sessions, rewrite {len(uniq)} unique")
        if dry:
            continue
        con.executemany("delete from price_bars where rowid=?",
                        [(r,) for r, _ in dupes])
        con.executemany("update or replace price_bars set ts=? where rowid=?",
                        [(t[:10] + canon, r) for r, t in uniq])
        con.commit()
        after = dict(con.execute(
            "select substr(ts,11), count(*) from price_bars where symbol=? and "
            "timeframe='1d' group by 1", (sym,)))
        print(f"   after: {after}")
    con.close()


def drop_intraday_from_daily(db, dry=True):
    """Delete rows in the 1d table whose ts carries a real intraday time.

    A daily bar's ts is midnight by definition. LTIM holds 225 rows at T09:15:00 and
    T09:51:00 — minute bars written with timeframe='1d'. They cannot be converted
    (that would fabricate daily bars from intraday snapshots) and they cannot stay
    (they are not daily bars). Only dates with no legitimate daily bar are affected,
    which is reported before anything is deleted.
    """
    con = sqlite3.connect(db)
    rows = con.execute(
        "select rowid, symbol, ts from price_bars where timeframe='1d' "
        "and substr(ts,12,8) not in ('00:00:00')").fetchall()
    if not rows:
        print("\nno intraday-stamped rows in the 1d table.")
        con.close()
        return
    by_sym = {}
    for rid, sym, ts in rows:
        by_sym.setdefault(sym, []).append((rid, ts))
    for sym, items in sorted(by_sym.items()):
        have = {t[0][:10] for t in con.execute(
            "select ts from price_bars where symbol=? and timeframe='1d' "
            "and substr(ts,12,8)='00:00:00'", (sym,))}
        lost = sorted({t[:10] for _, t in items} - have)
        times = sorted({t[11:] for _, t in items})
        print(f"\n{sym}: {len(items)} intraday-stamped rows ({','.join(times)})")
        print(f"   {len(lost)} of those dates have NO proper daily bar — "
              f"they will simply be absent until the next sync fills them"
              + (f" (e.g. {', '.join(lost[:3])})" if lost else ""))
        if not dry:
            con.executemany("delete from price_bars where rowid=?",
                            [(r,) for r, _ in items])
            con.commit()
            left = con.execute("select count(*) from price_bars where symbol=? and "
                               "timeframe='1d'", (sym,)).fetchone()[0]
            print(f"   deleted {len(items)}; {left} daily bars remain")
    con.close()


def merge_symbols(db, old, new, timeframe, dry=True, tol=0.002):
    """Merge one symbol into another after PROVING they are the same series.

    CNXIT/NSEBANK are Yahoo TICKERS that were used as DB symbols by the Breeze 1m
    path, while the same indices are stored as NIFTYIT/BANKNIFTY everywhere else.
    One instrument, two names, split by timeframe — and repointing the orchestrator
    to the canonical names started a SECOND 1m series rather than moving the first.

    This refuses to guess. Before touching anything it checks that the two series
    agree on shared timestamps; a real disagreement means they are not the same
    thing and merging would fabricate a series that never traded.

    Where a timestamp exists on both sides the NEW row is dropped and the OLD row
    renamed, because in this case OLD is the superset. Values were verified
    identical first, so which side wins is immaterial — but the check runs anyway.
    """
    con = sqlite3.connect(db)
    # rowid, not (symbol, ts): the only index is (exchange, symbol, timeframe, ts)
    # and a predicate without `exchange` cannot use its leftmost prefix, so deleting
    # by key full-scans a 329MB table per row. 10,875 deletes never finished.
    a_rows = con.execute("select rowid, ts, close from price_bars where symbol=? and "
                         "timeframe=?", (old, timeframe)).fetchall()
    b_rows = con.execute("select rowid, ts, close from price_bars where symbol=? and "
                         "timeframe=?", (new, timeframe)).fetchall()
    a = {t: c for _, t, c in a_rows}
    b = {t: c for _, t, c in b_rows}
    b_rowid = {t: r for r, t, _ in b_rows}
    if not a:
        print(f"\n{old} has no {timeframe} rows — nothing to merge.")
        con.close()
        return
    shared = set(a) & set(b)
    only_old, only_new = set(a) - set(b), set(b) - set(a)
    bad = [t for t in shared if a[t] and b[t] and abs(a[t] / b[t] - 1) > tol]

    print(f"\n{old} -> {new}  ({timeframe})")
    print(f"   {old:12} {len(a):>6} bars   {new:12} {len(b):>6} bars")
    print(f"   shared {len(shared)}   only-{old} {len(only_old)}   only-{new} {len(only_new)}")
    print(f"   disagree beyond {tol:.1%} on shared timestamps: {len(bad)}")
    if bad:
        for t in sorted(bad)[:3]:
            print(f"      {t[:16]}  {a[t]} vs {b[t]}")
        print("   REFUSING — these are not the same series. Investigate before merging.")
        con.close()
        return
    if dry:
        print(f"   would delete {len(shared)} duplicate {new} rows, "
              f"then rename {len(a)} {old} rows -> {new}")
        con.close()
        return

    # Drop the colliding NEW rows first so the rename cannot hit a key conflict.
    con.executemany("delete from price_bars where rowid=?",
                    [(b_rowid[t],) for t in shared])
    con.execute("update or replace price_bars set symbol=? where symbol=? and "
                "timeframe=?", (new, old, timeframe))
    con.commit()
    after = con.execute("select count(*), min(ts), max(ts) from price_bars where "
                        "symbol=? and timeframe=?", (new, timeframe)).fetchone()
    left = con.execute("select count(*) from price_bars where symbol=? and "
                       "timeframe=?", (old, timeframe)).fetchone()[0]
    print(f"   after: {new} {after[0]} bars {after[1][:16]}..{after[2][:16]}; "
          f"{old} has {left} left")
    con.close()


from daily_bars import PLAUSIBLE_1M_FLOOR   # defined once, beside SYMBOL_EXCHANGE


def drop_implausible_1m(db, dry=True, symbols=None):
    """Delete 1m bars too cheap to be the instrument they are filed under.

    Deleted, not moved to a quarantine symbol: an option series stored under any
    name is still an option series in a table of commodity prices, and the next
    person to glob the symbol list finds it.

    Rows are written to a CSV beside the database first. That is not a hedge against
    the criterion being wrong — it is that 8,402 rows is too many to reconstruct if
    it is.
    """
    import csv as _csv
    con = sqlite3.connect(db)
    total = 0
    for sym, floor in sorted(PLAUSIBLE_1M_FLOOR.items()):
        if symbols and sym not in symbols:
            continue
        rows = con.execute(
            "select rowid, exchange, ts, open, high, low, close, volume, open_interest "
            "from price_bars where symbol=? and timeframe='1m' and close < ? "
            "order by ts", (sym, floor)).fetchall()
        if not rows:
            continue
        kept = con.execute(
            "select count(*) from price_bars where symbol=? and timeframe='1m' "
            "and close >= ?", (sym, floor)).fetchone()[0]
        print(f"{sym}: {len(rows):,} bars below {floor:,} "
              f"({rows[0][2]} .. {rows[-1][2]}), {kept:,} plausible bars remain")
        if dry:
            print(f"   dry-run — nothing deleted")
            total += len(rows)
            continue
        out = os.path.join(os.path.dirname(os.path.abspath(db)),
                           f"{sym}_1m_implausible_dropped.csv")
        with open(out, "w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["exchange", "ts", "open", "high", "low", "close",
                        "volume", "open_interest"])
            w.writerows(r[1:] for r in rows)
        # by rowid: a predicate omitting `exchange` cannot use the index, which is
        # keyed leftmost on it, and full-scans a 300MB+ table instead.
        con.executemany("delete from price_bars where rowid=?",
                        [(r[0],) for r in rows])
        con.commit()
        print(f"   deleted {len(rows):,}; saved to {out}")
        total += len(rows)
    con.close()
    if not total:
        print("No implausible 1m bars found.")
    return total



# Products whose DAILY bars are only meaningful when the contract traded.
UNTRADED_1D_SYMBOLS = ["GOLD", "SILVER", "COPPER", "CRUDEOIL_MCX"]


def drop_untraded_1d(db, dry=True, symbols=None, min_volume=0):
    """Delete daily bars on MCX products where nothing traded.

    MCX carries the previous price forward on a day with no trade, so an untraded
    session still produces a bar. It is a MARK, not a price, and a return computed
    across two marks is noise wearing the shape of data.

    Measured, not assumed. CRUDEOIL_MCX daily begins 2026-02-20 on a contract nobody
    had traded yet — February and March are 100% zero-volume — and those 26 bars
    dragged its correlation with WTI from +0.985 down to +0.558:

        from 2026-02-20  +0.558      Feb  100% zero-volume
        from 2026-05-01  +0.903      Mar  100% zero-volume
        from 2026-06-01  +0.974      Jun  median volume 1,816
        from 2026-06-29  +0.985      Aug  median volume 65,554

    A backtest over that window is not measuring the market.

    DAILY ONLY, DELIBERATELY. A zero-volume MINUTE is ordinary even in a liquid
    contract — no trade in that particular minute — and dropping those would shred
    the intraday series that algo work depends on. At daily scale, a whole session
    with no trade means the contract was not live yet.

    Contract series (GOLD_2026-10-05 and friends) are left untouched: they are the
    raw record of what the exchange published, and continuous.py already excludes
    untraded bars when deriving. Raw stays raw; the derived series is the clean one.
    """
    import csv as _csv
    con = sqlite3.connect(db)
    total = 0
    for sym in (symbols or UNTRADED_1D_SYMBOLS):
        # `volume = 0` alone is too narrow. SILVER's apparent 40% one-day crash sits
        # between a 3-lot print and an 8-lot print — both technically trades, neither
        # a price anyone could have transacted size at. So the cut is a threshold,
        # and the dry run shows what each one costs rather than picking for you.
        if dry:
            dist = con.execute(
                "select sum(coalesce(volume,0) = 0), sum(coalesce(volume,0) between 1 and 9), "
                "sum(coalesce(volume,0) between 10 and 99), sum(coalesce(volume,0) >= 100), "
                "count(*) from price_bars where symbol=? and timeframe='1d'",
                (sym,)).fetchone()
            print(f"{sym}: {dist[4]:,} daily bars —  0 vol: {dist[0]:,}   "
                  f"1-9: {dist[1]:,}   10-99: {dist[2]:,}   100+: {dist[3]:,}")
        rows = con.execute(
            "select rowid, exchange, ts, open, high, low, close, volume, open_interest "
            "from price_bars where symbol=? and timeframe='1d' "
            "and coalesce(volume, -1) >= 0 and coalesce(volume, 0) <= ? "
            "order by ts", (sym, min_volume)).fetchall()
        kept = con.execute(
            "select count(*) from price_bars where symbol=? and timeframe='1d' "
            "and (volume is null or volume > ?)", (sym, min_volume)).fetchone()[0]
        if not rows:
            print(f"   nothing at or below volume {min_volume} ({kept:,} bars kept)")
            continue
        print(f"   at min_volume={min_volume}: drop {len(rows):,} bars "
              f"({rows[0][2][:10]} .. {rows[-1][2][:10]}), {kept:,} traded bars remain")
        if dry:
            print("   dry-run — nothing deleted")
            total += len(rows)
            continue
        out = os.path.join(os.path.dirname(os.path.abspath(db)),
                           f"{sym}_1d_untraded_dropped.csv")
        with open(out, "w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["exchange", "ts", "open", "high", "low", "close",
                        "volume", "open_interest"])
            w.writerows(r[1:] for r in rows)
        con.executemany("delete from price_bars where rowid=?", [(r[0],) for r in rows])
        con.commit()
        print(f"   deleted {len(rows):,}; saved to {out}")
        total += len(rows)
    con.close()
    return total



def wipe_legacy_commodities(db, dry=True, symbols=None):
    """Delete the MCX product series outright, so they can only be re-derived.

    WHAT THIS DELETES: every row under GOLD, SILVER, COPPER and CRUDEOIL_MCX, both
    timeframes. NOT the per-contract series (GOLD_2026-10-05 and friends), NOT the
    USD series, NOT USDINR or GIFTNIFTY.

    WHY IT IS SAFE NOW, HAVING NOT BEEN EARLIER
    -------------------------------------------
    The argument against was a year of unrecoverable history. Measuring it removed
    the argument:

      * most of it never traded. GOLD had 72 zero-volume daily bars and 55 more
        under 10 lots; SILVER 77 and 64. Its famous 40% one-day "crash" was a 3-lot
        print followed by an 8-lot print on a contract nobody was trading.
      * what remained was an unlabelled splice across contracts, so returns across
        every roll were carry rather than market.
      * the long clean history now lives in GOLD_USD / SILVER_USD / COPPER_USD —
        2,162 daily bars each from Yahoo, rolled and back-adjusted, 2018 onward.

    So this deletes roughly four months of genuinely traded Indian daily bars, and
    replaces them with a series that is short but true. Everything written from here
    is contract-labelled, front-month only, volume-filtered and ratio-adjusted.

    AFTER THIS, RUN continuous.py FOR BOTH TIMEFRAMES. Until you do, these symbols
    are empty — that is deliberate. An empty series is visibly empty; a stale one
    looks fine.
    """
    import csv as _csv
    targets = symbols or ["GOLD", "SILVER", "COPPER", "CRUDEOIL_MCX"]
    con = sqlite3.connect(db)
    total = 0
    for sym in targets:
        rows = con.execute(
            "select rowid, exchange, symbol, timeframe, ts, open, high, low, close, "
            "volume, open_interest from price_bars where symbol=? order by timeframe, ts",
            (sym,)).fetchall()
        if not rows:
            print(f"{sym}: already empty")
            continue
        by_tf = {}
        for r in rows:
            by_tf.setdefault(r[3], []).append(r)
        desc = ", ".join(f"{tf} {len(v):,} bars {v[0][4][:10]}..{v[-1][4][:10]}"
                         for tf, v in sorted(by_tf.items()))
        print(f"{sym}: {desc}")
        if dry:
            total += len(rows)
            continue
        out = os.path.join(os.path.dirname(os.path.abspath(db)),
                           f"{sym}_legacy_wiped.csv")
        with open(out, "w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["exchange", "symbol", "timeframe", "ts", "open", "high",
                        "low", "close", "volume", "open_interest"])
            w.writerows(r[1:] for r in rows)
        con.executemany("delete from price_bars where rowid=?", [(r[0],) for r in rows])
        con.commit()
        print(f"   deleted {len(rows):,}; saved to {out}")
        total += len(rows)
    con.close()
    if dry:
        print(f"\n{total:,} rows would be deleted. Contract series are untouched.")
    else:
        print(f"\n{total:,} rows deleted. NOW REBUILD:")
        print("   python data_agent/fetching/continuous.py --apply")
        print("   python data_agent/fetching/continuous.py --timeframe 1m --apply")
    return total


MERGES = [
    # (old, new, timeframe) — verified supersets with identical values, 2026-08-08
    ("CNXIT", "NIFTYIT", "1m"),
    ("NSEBANK", "BANKNIFTY", "1m"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true", help="write; default is dry-run")
    ap.add_argument("--merge-index-names", action="store_true",
                    help="merge CNXIT->NIFTYIT and NSEBANK->BANKNIFTY at 1m, after "
                         "verifying the two series agree")
    ap.add_argument("--drop-intraday", action="store_true",
                    help="delete rows in the 1d table carrying a real intraday time "
                         "(minute bars written with timeframe='1d')")
    ap.add_argument("--fold-ts", action="store_true",
                    help="collapse symbols carrying two ts spellings onto the canonical one")
    ap.add_argument("--drop-implausible-1m", action="store_true",
                    help="delete 1m bars too cheap to be their own instrument "
                         "(a wrong MCX key returning option premium)")
    ap.add_argument("--min-volume", type=int, default=0,
                    help="with --drop-untraded-1d: drop daily bars at or below this "
                         "volume (0 = only genuinely untraded)")
    ap.add_argument("--wipe-legacy-commodities", action="store_true",
                    help="delete GOLD/SILVER/COPPER/CRUDEOIL_MCX entirely so they "
                         "can only be re-derived from contract series")
    ap.add_argument("--drop-untraded-1d", action="store_true",
                    help="delete MCX daily bars where volume is 0 (carried-forward "
                         "marks, not trades)")
    ap.add_argument("--fold-exchange", action="store_true",
                    help="collapse symbols stored under two exchanges onto one")
    args = ap.parse_args()
    db = args.db
    if not db:
        from bar_store import DB_PATH
        db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    print(f"database: {db}")
    if args.merge_index_names:
        for old, new, tf in MERGES:
            merge_symbols(db, old, new, tf, dry=not args.apply)
        if not args.apply:
            print("\n--dry-run (default). Re-run with --apply to write.")
        return
    if args.drop_intraday:
        drop_intraday_from_daily(db, dry=not args.apply)
        if not args.apply:
            print("\n--dry-run (default). Re-run with --apply to write.")
        return
    if args.fold_ts:
        fold_ts_formats(db, dry=not args.apply)
        if not args.apply:
            print("\n--dry-run (default). Re-run with --apply to write.")
        return
    if args.drop_implausible_1m:
        drop_implausible_1m(db, dry=not args.apply)
        if not args.apply:
            print("\nDry run. Re-run with --apply to delete.")
        return

    if args.wipe_legacy_commodities:
        wipe_legacy_commodities(db, dry=not args.apply)
        if not args.apply:
            print("Dry run. Re-run with --apply to delete.")
        return

    if args.drop_untraded_1d:
        drop_untraded_1d(db, dry=not args.apply, min_volume=args.min_volume)
        if not args.apply:
            print("\nDry run. Re-run with --apply to delete.")
        return

    if args.fold_exchange:
        fold_forked_exchange(db, dry=not args.apply)
        if not args.apply:
            print("\n--dry-run (default). Re-run with --apply to write.")
        return
    run(db, dry=not args.apply)
    if not args.apply:
        print("\n--dry-run (default). Re-run with --apply to write.")
    else:
        print("\nNext: re-sync CRUDEOIL from CL=F to fill the 2025-07-30..2026-02-20 hole,")
        print("then re-copy the mirror and re-run daily_bar_audit.py.")


if __name__ == "__main__":
    main()
