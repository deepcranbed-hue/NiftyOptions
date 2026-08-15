"""continuous.py — build a rolling futures series from per-contract series.

THE PROBLEM THIS SOLVES
-----------------------
`GOLD` held one contract's life under a rolling name. When the contract changed,
the same symbol silently started meaning a different instrument — and on
2026-07-01 it started meaning an OPTION, which nothing noticed for five weeks
because there was no per-contract record to compare against.

The model:

    GOLD_2026-08-27   one contract, one instrument, never re-pointed   <- stored
    GOLD_2026-10-29   the next contract, stored alongside it           <- stored
    GOLD              the rolling series, DERIVED from those           <- generated

A contract series is append-only and means exactly one thing forever. The
continuous series is a view that can be rebuilt from scratch at any time, so a bad
roll is a regeneration rather than a restore from backup.

WHY THE FULL EXPIRY DATE IN THE SYMBOL, NOT JUST THE MONTH
----------------------------------------------------------
`GOLD_2026-08` reads better, but the roll rule is date-based —
`universe.FUT_ROLL_AHEAD_DAYS` before expiry — so a month-keyed name needs a second
lookup to answer when to switch. That is a rule and its data living apart, which is
the split this repo keeps paying for. The date is in the name; the name is enough.

THE ROLL AND THE ADJUSTMENT
---------------------------
WHICH contract applies on a given date is `universe.roll_schedule()` — fixed offset
before expiry, deterministic, so the same backtest run a year apart selects the same
bars. An open-interest crossover re-decides history whenever OI is revised.

Contracts differ by carry, so joining them raw leaves a step that reads as a real
move. This applies RATIO back-adjustment: at each roll, the ratio of the two
contracts' closes on the last date they both traded scales all older history, so
percentage returns are continuous across the join.

The NEWEST segment is never adjusted. That matters — it means the latest bars are
real traded prices, so landed-parity checks against spot still work on the front of
the series. Only history is rescaled, and rescaled history no longer matches what
printed on the day. That is the accepted cost of a returns-continuous series.

WHAT THIS DOES NOT DO
---------------------
It cannot un-mix the past. Bars already stored under `GOLD` carry no contract label
and cannot be split retroactively — we do not know which contract each came from.
So the continuous series is: legacy bars as they are, and generated bars from the
cutover forward. Nothing pre-cutover is touched, and it remains an unlabelled
splice. Only new history gets the guarantee.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(os.path.dirname(_HERE))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from universe import _as_date, roll_schedule                       # noqa: E402

CONTRACT_RE = re.compile(r"^(?P<product>[A-Z0-9_]+?)_(?P<expiry>\d{4}-\d{2}-\d{2})$")

# Products stored per contract. MCX only for now — NIFTY_FUT_1/FUT_2 are the same
# anti-pattern but feed the backend analysis, so they move separately.
PER_CONTRACT = {"GOLD": "MCX", "SILVER": "MCX", "COPPER": "MCX",
                "CRUDEOIL_MCX": "MCX"}


def contract_symbol(product: str, expiry) -> str:
    """GOLD + 2026-08-27 -> 'GOLD_2026-08-27'."""
    return f"{product.upper()}_{_as_date(expiry).isoformat()}"


def parse_contract(symbol: str):
    """'GOLD_2026-08-27' -> ('GOLD', date(2026,8,27)); None if not a contract symbol."""
    m = CONTRACT_RE.match(symbol.upper())
    if not m:
        return None
    prod = m.group("product")
    if prod not in PER_CONTRACT:
        return None
    return prod, date.fromisoformat(m.group("expiry"))


def stored_contracts(db, product, timeframe="1d"):
    """{expiry_date: symbol} for every contract series present for `product`."""
    con = sqlite3.connect(db)
    rows = [r[0] for r in con.execute(
        # '_' is a single-char wildcard in LIKE, which is fine here: this is a
        # cheap prefilter and parse_contract() is what actually validates the name.
        "select distinct symbol from price_bars where timeframe=? and symbol like ?",
        (timeframe, product.upper() + "_%"))]
    con.close()
    out = {}
    for s in rows:
        p = parse_contract(s)
        if p and p[0] == product.upper():
            out[p[1]] = s
    return out


def _series(con, symbol, timeframe):
    """{ts: (ts, o, h, l, c, v, oi)} for one contract, keyed by the FULL timestamp.

    Keyed on ts, NOT on ts[:10]. The first version keyed by date, which is harmless
    at 1d — one bar per date is the definition — and destroys 1m: all 870 minutes of
    a session collapse to whichever one happened to be read last. It produced 30
    bars for five weeks of one-minute data, one per trading day, and wrote them.

    Segment bounds are still compared by date, since the roll schedule is date-based.
    That is the only place the date is derived, and it is derived from the ts rather
    than baked into the key.
    """
    out = {}
    for ts, o, h, l, c, v, oi in con.execute(
            "select ts, open, high, low, close, volume, open_interest "
            "from price_bars where symbol=? and timeframe=? order by ts",
            (symbol, timeframe)):
        out[ts] = (ts, o, h, l, c, v, oi)
    return out


def build(db, product, timeframe="1d", roll_ahead_days=None, log=print):
    """Assemble the ratio-adjusted continuous series for `product`.

    Returns (rows, notes). Rows are (ts, o, h, l, c, v, oi) ready for the store.
    Writes nothing — the caller decides, so this is safe to run and inspect.
    """
    contracts = stored_contracts(db, product, timeframe)
    if len(contracts) < 1:
        return [], [f"{product}: no per-contract series stored yet"]
    kw = {} if roll_ahead_days is None else {"roll_ahead_days": roll_ahead_days}
    segments = roll_schedule(sorted(contracts), **kw)

    con = sqlite3.connect(db)
    series = {exp: _series(con, sym, timeframe) for exp, sym in contracts.items()}
    con.close()

    # DROP UNTRADED MARKS.
    #
    # Inside its liquid window a real futures contract prints volume every session.
    # A zero-volume bar there is MCX carrying yesterday's price forward, not a price
    # anyone could have transacted at. Including them puts flat stretches and then
    # catch-up jumps into the continuous series — the shape that made this check
    # raise 8 gaps, every one of them a mark rather than a trade.
    #
    # A day with no trade is better absent than fabricated: a missing bar is visible,
    # a carried-forward one is not.
    #
    # volume IS NULL means unknown, not zero, so those are kept.
    def _traded(row):
        return row[5] is None or row[5] > 0

    # WHEN DID THE EARLIEST CONTRACT WAKE UP?
    #
    # universe.FUT_LIQUID_WINDOW_DAYS caps how far the oldest contract we hold can
    # reach back, but a fixed day-count cannot answer this. Gold expiries are two
    # months apart, so GOLD_2026-10-05 became the front month on 2026-08-06 — the day
    # the August contract died — while a 40-day rule said 2026-08-26 and produced
    # nothing at all.
    #
    # The contract's own volume knows. Before it is front it is quoted and barely
    # traded; when it takes over, volume steps up by an order of magnitude and stays
    # there. So: find the first date from which volume is durably a meaningful
    # fraction of the contract's own busiest days, and start there.
    #
    # Self-tuning, and it needs no knowledge of the product's expiry cycle — which is
    # the part I kept getting wrong by hand.
    def _front_from_registry(product, exp):
        """The day this contract became the FRONT month: the previous expiry + 1.

        Exact, not inferred. A volume heuristic cannot tell the front month from a
        busy second month, and for a bi-monthly product like gold the second month
        trades plenty — which is why GOLD's 1m series carried five weeks of
        second-month data and came out 46% untraded.

        The registry records every contract we have ever seen, so the previous
        expiry is a lookup rather than a guess. Returns None when no earlier
        contract is known, in which case the volume heuristic is all we have.
        """
        try:
            sys.path.insert(0, os.path.dirname(_HERE))
            import contract_registry as _reg
            known = sorted(c["expiry"] for c in _reg.load()["contracts"].values()
                           if c["product"] == product)
        except Exception:                                    # noqa: BLE001
            return None
        earlier = [e for e in known if e < exp.isoformat()]
        if not earlier:
            return None
        from datetime import timedelta as _td
        return (date.fromisoformat(earlier[-1]) + _td(days=1)).isoformat()

    def _activation(rows):
        vols = [r[5] for r in rows.values() if r[5]]
        if not vols:
            return None
        peak = sorted(vols)[int(len(vols) * 0.9)]        # 90th pct, not the max
        thresh = peak * 0.05
        active = sorted(t for t, r in rows.items() if r[5] and r[5] >= thresh)
        return active[0][:10] if active else None

    # Slice each segment to the dates that contract is responsible for.
    sliced, notes = [], []
    for idx, (a, b, exp) in enumerate(segments):
        s = series.get(exp, {})
        if idx == 0:                       # only the oldest contract needs this
            woke = _front_from_registry(product, exp) or _activation(s)
            if woke:
                # Replaces the FUT_LIQUID_WINDOW_DAYS cap outright, in either
                # direction. The cap is a guess about the product's expiry cycle;
                # volume is the contract telling us. An earlier first version only
                # let volume push the start LATER, so the 40-day guess still won
                # whenever it was too conservative — which for bi-monthly gold it
                # always is.
                if date.fromisoformat(woke) != a:
                    src = ("previous contract expired" if
                           _front_from_registry(product, exp) else "volume")
                    notes.append(f"   {contract_symbol(product, exp)}: front month "
                                 f"from {woke} ({src}; window guessed {a})")
                a = date.fromisoformat(woke)
        keep = {t: row for t, row in s.items()
                if a <= date.fromisoformat(t[:10]) <= b and _traded(row)}
        dropped = sum(1 for t, row in s.items()
                      if a <= date.fromisoformat(t[:10]) <= b and not _traded(row))
        if dropped:
            notes.append(f"   {contract_symbol(product, exp)}: dropped {dropped:,} "
                         f"zero-volume bars (carried-forward marks, not trades)")
        if not keep:
            notes.append(f"   {contract_symbol(product, exp)}: no bars inside "
                         f"{a}..{b} — segment skipped")
            continue
        sliced.append((exp, keep))
    if not sliced:
        return [], notes or [f"{product}: nothing to build"]

    # RATIO ADJUSTMENT, newest backwards. The newest segment is the anchor at 1.0.
    factors = {sliced[-1][0]: 1.0}
    for i in range(len(sliced) - 2, -1, -1):
        old_exp, old = sliced[i]
        new_exp, new = sliced[i + 1]
        common = sorted(set(old) & set(_series_all(series, new_exp)))   # shared ts
        if not common:
            factors[old_exp] = factors[new_exp]
            notes.append(f"   {old_exp} -> {new_exp}: contracts never traded on the "
                         f"same date, so no ratio could be measured; joined RAW. "
                         f"The step at this roll is real and will show in returns.")
            continue
        d = common[-1]
        old_close = old[d][4] if d in old else _series_all(series, old_exp)[d][4]
        new_close = _series_all(series, new_exp)[d][4]
        # `d` is a full ts now; show only the date part in the note below.
        if not old_close:
            factors[old_exp] = factors[new_exp]
            continue
        ratio = new_close / old_close
        factors[old_exp] = factors[new_exp] * ratio
        notes.append(f"   roll {old_exp} -> {new_exp} on {d[:10]}: "
                     f"{old_close:,.1f} -> {new_close:,.1f}  x{ratio:.6f}  "
                     f"(cumulative x{factors[old_exp]:.6f})")

    rows = []
    for exp, keep in sliced:
        f = factors[exp]
        for d in sorted(keep):
            ts, o, h, l, c, v, oi = keep[d]
            # OHLC scale; volume and open interest are counts, not prices.
            rows.append((ts, _m(o, f), _m(h, f), _m(l, f), _m(c, f), v, oi))
    rows.sort(key=lambda r: r[0])
    return rows, notes


def _series_all(series, exp):
    return series.get(exp, {})


def _m(v, f):
    return None if v is None else v * f


def write(db, product, timeframe="1d", dry=True, log=print):
    """Build and store the continuous series under the bare product name."""
    rows, notes = build(db, product, timeframe, log=log)
    for n in notes:
        log(n)
    if not rows:
        log(f"{product}: nothing to write")
        return 0
    lo, hi = rows[0][0][:10], rows[-1][0][:10]
    log(f"{product} {timeframe}: {len(rows):,} bars {lo} .. {hi}")
    if dry:
        log("   dry-run — nothing written")
        return 0
    if timeframe == "1d":
        from daily_bars import write_daily
        n, _ = write_daily(rows, product, db, exchange=PER_CONTRACT[product])
    else:
        from bar_store import save_bars
        n = save_bars(rows, exchange=PER_CONTRACT[product], symbol=product,
                      timeframe=timeframe, db=db)
    log(f"   wrote {n:,}")
    return n


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--product", default=None, help="default: all per-contract products")
    ap.add_argument("--timeframe", default="1d", choices=["1d", "1m"])
    ap.add_argument("--apply", action="store_true", help="write; default is dry-run")
    args = ap.parse_args()
    db = args.db
    if not db:
        from bar_store import DB_PATH
        db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    print(f"database: {db}\n")
    for product in ([args.product] if args.product else sorted(PER_CONTRACT)):
        write(db, product, args.timeframe, dry=not args.apply)
        print()
    if not args.apply:
        print("Dry run. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
