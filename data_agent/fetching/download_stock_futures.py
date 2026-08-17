#!/usr/bin/env python3
"""
download_stock_futures.py — 1d single-stock futures for the Nifty 50, expiries from Breeze.

WHY THIS EXISTS RATHER THAN EXTENDING stock_futures.py
------------------------------------------------------
`stock_futures.py` plans the download and derives the continuous series, and its roll
logic is sound. But it sources expiries from `_last_tuesday()` — it COMPUTES the
calendar. `data_agent/expiries.py` was written specifically to stop that, and says so:

    expiries.py    WHERE the list comes from   (Breeze, a subprocess, a session)
    universe.py    WHICH ones we want          (pure date logic, offline-testable)
    Add a rule to universe.py. Add a source here. Never the other way round.

Computing the expiry is a guess about a calendar the exchange controls. NSE has moved
monthly expiry days before and will again; a holiday shifts one; a special settlement
inserts one. When that happens a computed expiry does not fail loudly — it fetches a
contract that does not exist, gets nothing back, and records a gap that looks like a
quiet market. So expiries come from the broker's own list here, and the date rule is
`universe.is_expired`, which is the one already used everywhere else.

THE LIVE / EXPIRED SPLIT
------------------------
Two different jobs, deliberately separate flags:

  --live      expiry >= today. Re-fetched every run; the last bars keep changing.
  --expired   expiry <  today. Fetched ONCE and never again — the contract is settled
              and its history is immutable. Re-downloading it every day is wasted
              quota against an unchanging answer.

`--expired` also takes `--since` so a backfill can be bounded. Default is 12 months,
which is the span the beta and hedge-ratio work needs.

THE FETCH WINDOW PER CONTRACT — ONE LIQUID MONTH, PLUS AN OVERLAP
-----------------------------------------------------------------
Each contract is fetched only over the span where it is the liquid one: from the
PREVIOUS contract's expiry until its own, plus a settlement grace. A monthly ladder
then tiles the calendar once instead of three or four times, which is the difference
between ~150 API calls and ~600.

But the window is deliberately widened by `_ROLL_OVERLAP_DAYS` BEFORE the previous
expiry, and that overlap is not padding — it is the only thing that makes the roll
measurable. `front_series(method="oi")` decides the roll by comparing the two
contracts' open interest ON THE SAME DAY, and `roll_gap` needs both closes on the roll
date. Fetch a strictly non-overlapping month per contract and neither is computable:
you are forced into a calendar roll and you inherit the price discontinuity blind,
which is exactly the NIFTY_FUT_1 defect this pipeline exists to avoid.

So the overlap buys the choice between an OI roll and a calendar roll, and the ability
to quantify what the splice costs. A week is enough — liquidity migrates over the last
few sessions before expiry, not over a month.

WHAT THIS IS FOR, AND WHAT IT IS NOT
------------------------------------
Built for open item O12: netting the FII derivatives book in NOTIONAL rather than in
contract counts. That needs, per underlying per day, open interest x lot size x price —
a point-in-time snapshot. It needs NO continuous series and NO roll adjustment, so
`front_series()` is not called here and the roll machinery sits unused for this purpose.

For BETAS, use spot from `price_bars`, not these futures. Futures returns carry roll
effects and basis noise that bias a beta estimate; the cash series does not.

    python3 download_stock_futures.py --live
    python3 download_stock_futures.py --expired --since 2025-08-01
    python3 download_stock_futures.py --live --only RELIANCE,HDFCBANK --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_AGENT)
for _p in (_AGENT, _HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# NOT os.path.join(_ROOT, "option_chains.db"). That path is the MIRROR — a manual copy
# kept so tooling without Drive access can READ. db_config.resolve_writable_db_path()
# exists to stop a writer touching it, and its docstring names this exact case: "If the
# Drive mount is missing - signed out, not yet synced, RUNNING IN A SANDBOX - a writer must
# FAIL rather than quietly write into the local read-only copy and manufacture the
# divergence this module exists to prevent." The first version of this file ignored that
# and put 1,950 bars in the mirror instead of the source of truth (C37). Resolved lazily so
# --dry-run still works where Drive is unreachable.
def writable_db() -> str:
    from db_config import resolve_writable_db_path
    return resolve_writable_db_path()

UNIVERSE = os.path.join(_ROOT, "nifty-50-stock-list.csv")

# Breeze throttles. The Nifty 50 x ~3 live contracts is ~150 calls; be unhurried.
_SLEEP = 0.8
_SETTLE_GRACE_DAYS = 3          # keep pulling briefly after expiry so the last bar lands
_ROLL_OVERLAP_DAYS = 7          # days BEFORE the prior expiry — makes the roll measurable
_FIRST_CONTRACT_LOOKBACK = 45   # no prior expiry exists for the oldest one
_NOT_YET_LIQUID_DAYS = 20       # far-month contract: short window so OI is visible

# Near + next only. Mirrors stock_futures._LADDER; a third serial month carries
# almost no open interest and the OI crossover that decides the roll only ever
# involves the near pair.
_LADDER = 2


def constituents(only: str | None = None) -> list[str]:
    with open(UNIVERSE, newline="", encoding="utf-8") as fh:
        syms = [r["Symbol"].strip().upper() for r in csv.DictReader(fh) if r.get("Symbol")]
    if only:
        want = {s.strip().upper() for s in only.split(",")}
        syms = [s for s in syms if s in want] or sorted(want)
    return syms


def breeze_expiries(symbol: str, session_token=None) -> tuple[list[str], str | None]:
    """Every futures expiry Breeze lists for this symbol. NEVER computed."""
    import expiries
    from fetching.broker import BreezeBroker
    breeze_symbol = BreezeBroker.code(symbol)
    return expiries.listed(breeze_symbol, product="futures", session_token=session_token)


def split_by_today(exps: list[str], today: date) -> tuple[list[str], list[str]]:
    """Live vs expired, using the repo's single date rule.

    `universe.is_expired` is the one comparison used everywhere else in this repo. It
    is imported rather than re-written as `expiry < today`, because a second copy of a
    one-line rule is still a second copy, and this file exists precisely because a
    third copy of the EXPIRY rule caused the problem it fixes.
    """
    from universe import is_expired
    live, dead = [], []
    for e in exps:
        d = _as_date(e)
        if d is None:
            continue
        (dead if is_expired(d, today) else live).append(e)
    return sorted(live, key=lambda x: _as_date(x)), sorted(dead, key=lambda x: _as_date(x))


def _as_date(x) -> date | None:
    s = str(x)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _client(session_token=None):
    from breeze_connect import BreezeConnect
    from credentials import breeze_creds, breeze_session_token
    api_key, api_secret = breeze_creds()
    b = BreezeConnect(api_key=api_key)
    b.generate_session(api_secret=api_secret,
                       session_token=session_token or breeze_session_token())
    return b


def fetch_one(b, symbol: str, expiry: str, start: date, end: date) -> list[tuple]:
    """1d bars for one contract. Returns rows shaped for fo_bars.save_fo_bars."""
    import dateutil.parser
    from fetching.broker import BreezeBroker
    breeze_symbol = BreezeBroker.code(symbol)
    res = b.get_historical_data_v2(
        interval="1day",
        from_date=start.strftime("%Y-%m-%dT00:00:00.000Z"),
        to_date=end.strftime("%Y-%m-%dT23:59:59.000Z"),
        stock_code=breeze_symbol,
        exchange_code="NFO",
        product_type="futures",
        expiry_date=expiry,
    )
    out = []
    for r in (res or {}).get("Success", []) or []:
        ts = dateutil.parser.parse(r["datetime"])
        out.append((
            ts.strftime("%Y-%m-%dT%H:%M:%S"),
            _f(r.get("open")), _f(r.get("high")), _f(r.get("low")), _f(r.get("close")),
            _f(r.get("volume")),
            # open interest is what O12 actually needs — carried explicitly, never dropped
            _f(r.get("open_interest") or r.get("oi")),
        ))
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--live", action="store_true", help="expiry >= today; re-fetch every run")
    g.add_argument("--expired", action="store_true",
                   help="expiry < today; immutable, fetch once")
    ap.add_argument("--only", help="comma-separated symbols")
    ap.add_argument("--since", help="YYYY-MM-DD lower bound for --expired (default 12 months)")
    ap.add_argument("--dry-run", action="store_true", help="resolve expiries, fetch nothing")
    a = ap.parse_args()

    today = date.today()
    since = _as_date(a.since) if a.since else (today - timedelta(days=365))
    syms = constituents(a.only)
    print(f"{'LIVE' if a.live else 'EXPIRED'} 1d single-stock futures — {len(syms)} symbols, "
          f"today {today}")
    if a.expired:
        print(f"  expired window: {since} .. {today}")

    from fo_bars import save_fo_bars

    db = None
    lots: dict = {}
    if not a.dry_run:
        db = writable_db()
        print(f"  writing to {db}")
        # contract_size at CAPTURE time, from the exchange's own contract list, so the
        # column is populated by the writer rather than by a later pass that mutates the
        # database. Breeze reports OI in SHARES, so notional never needs this — contract
        # counts do, which is what reconciling against participant_oi requires.
        try:
            from lot_sizes import master_lots, _iso
            from fetching.broker import BreezeBroker
            for (code, exp), lot in master_lots().items():
                lots[(code, _iso(exp))] = lot
            print(f"  lot sizes: {len(lots)} contracts from the scrip master")
        except Exception as exc:
            print(f"  NOTE: no lot sizes ({exc}) — contract_size will be NULL")

    b = None if a.dry_run else _client()
    plan, saved, empty, failed = [], 0, 0, 0

    for i, sym in enumerate(syms, 1):
        exps, err = breeze_expiries(sym)
        if err or not exps:
            print(f"[{i:2d}/{len(syms)}] {sym:12s} EXPIRY LOOKUP FAILED: {err or 'empty list'}")
            failed += 1
            continue
        live, dead = split_by_today(exps, today)
        # LIVE is capped at the ladder — near + next, TWO contracts. Breeze lists three
        # serial months (Aug/Sep/Oct on the first real run), and taking all of them was
        # a bug: `_LADDER = 2` lives in stock_futures.py and this file never read it.
        #
        # The cap comes from `universe.active_future_expiries`, which is the repo's
        # existing rule for exactly this ("near + next, always two"). The first version
        # of this line reimplemented the filter and got the count wrong — which is the
        # same failure `expiries.py` documents about ITS first version, and the reason
        # this module takes expiries from Breeze rather than computing them.
        from universe import active_future_expiries
        if a.live:
            keep = {d.isoformat() for d in active_future_expiries(exps, today, n=_LADDER)}
            want = [e for e in live if (_as_date(e) or today).isoformat() in keep]
        else:
            want = [e for e in dead if (_as_date(e) or today) >= since]
        print(f"[{i:2d}/{len(syms)}] {sym:12s} {len(exps):2d} listed, "
              f"{len(live)} live -> {len(want)} to fetch (ladder {_LADDER})  "
              f"{[str(_as_date(e)) for e in want][:4]}")
        # Previous expiry per contract, from Breeze's own sorted list — this is what
        # bounds the liquid window. Computed from the full list, not just `want`, so a
        # contract at the edge of the requested range still knows its predecessor.
        all_sorted = sorted([x for x in exps if _as_date(x)], key=lambda x: _as_date(x))
        prev_of = {e: (all_sorted[i - 1] if i else None)
                   for i, e in enumerate(all_sorted)}

        for e in want:
            ed = _as_date(e) or today
            pv = _as_date(prev_of.get(e) or "") if prev_of.get(e) else None
            liquid_from = (pv - timedelta(days=_ROLL_OVERLAP_DAYS)) if pv \
                else (ed - timedelta(days=_FIRST_CONTRACT_LOOKBACK))
            start = max(since, liquid_from)
            end = min(today, ed + timedelta(days=_SETTLE_GRACE_DAYS))

            # The FAR-MONTH contract has not become liquid yet: its prior expiry is
            # still in the future, so `liquid_from` lands AFTER today and the window
            # inverts. Caught by the offline ladder test, which produced a -2 day range.
            #
            # Do NOT skip it. The OI-crossover roll is decided by comparing the two
            # contracts on the same day, so the next month's data is needed BEFORE it
            # takes over — that is the whole signal. Give it a short recent window
            # instead, enough to watch its open interest build.
            if start >= end:
                start = max(since, end - timedelta(days=_NOT_YET_LIQUID_DAYS))
                if start >= end:
                    print(f"      {e[:10]}  skipped — no valid window "
                          f"(listed but no sessions yet)")
                    continue
            plan.append((sym, e, start.isoformat(), end.isoformat()))
            if a.dry_run:
                print(f"      {e[:10]}  window {start} .. {end}  "
                      f"({(end - start).days}d, prior expiry {pv or 'none'})")
                continue
            try:
                rows = fetch_one(b, sym, e, start, end)
            except Exception as exc:
                print(f"      {e[:10]} ERROR {exc}")
                failed += 1
                time.sleep(_SLEEP)
                continue
            if not rows:
                empty += 1
            else:
                from fetching.broker import BreezeBroker as _BB
                lot = lots.get((_BB.code(sym), (_as_date(e) or today).isoformat()))
                n = save_fo_bars(rows, db=db, underlying=sym, instrument_type="FUT",
                                 expiry=e, exchange="NFO", timeframe="1d",
                                 contract_size=lot)
                saved += n
                print(f"      {e[:10]}  {len(rows):4d} bars -> {n} saved")
            time.sleep(_SLEEP)

    print(f"\nplan: {len(plan)} contracts across {len(syms)} symbols")
    if plan and len(plan[0]) == 4:
        days = sum((_as_date(x[3]) - _as_date(x[2])).days for x in plan)
        print(f"  total calendar-days requested: {days:,}  "
              f"(avg {days/max(len(plan),1):.0f}d per contract — one liquid month "
              f"+ {_ROLL_OVERLAP_DAYS}d roll overlap)")
    if a.dry_run:
        print("--dry-run: nothing fetched.")
        return
    print(f"saved {saved:,} bars   {empty} contracts returned nothing   {failed} failures")
    if empty:
        print("  A contract returning nothing is worth a look: with expiries taken from")
        print("  Breeze rather than computed, an empty result means no trading, not a")
        print("  wrong date — which is the whole reason for sourcing them this way.")


if __name__ == "__main__":
    main()
