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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true", help="write; default is dry-run")
    ap.add_argument("--drop-intraday", action="store_true",
                    help="delete rows in the 1d table carrying a real intraday time "
                         "(minute bars written with timeframe='1d')")
    ap.add_argument("--fold-ts", action="store_true",
                    help="collapse symbols carrying two ts spellings onto the canonical one")
    ap.add_argument("--fold-exchange", action="store_true",
                    help="collapse symbols stored under two exchanges onto one")
    args = ap.parse_args()
    db = args.db
    if not db:
        from bar_store import DB_PATH
        db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    print(f"database: {db}")
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
