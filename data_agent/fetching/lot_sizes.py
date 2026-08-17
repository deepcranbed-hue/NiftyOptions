#!/usr/bin/env python3
"""
lot_sizes.py — populate fo_price_bars.contract_size from the exchange's own contract list,
and refuse to do it where an independent check disagrees.

WHY THIS FILE EXISTS
--------------------
`contract_size` was NULL on all 1,950 futures bars. Breeze's 1d historical response does
not carry lot size, so the downloader had nothing to write. The column existed and read as
if it were populated, which is the failure mode worth naming: a schema column is a promise,
not a fact.

TWO SOURCES, AND WHICH ONE IS AUTHORITATIVE
-------------------------------------------
  SecurityMaster.zip / FONSEScripMaster.txt   the exchange's contract list, via Breeze.
                                              FUTSTK rows, ShortName + ExpiryDate +
                                              LotSize. This is TRUTH.

  gcd(open_interest) per contract              an independent DERIVATION. Breeze reports OI
                                              and volume in SHARES, not contracts, so every
                                              OI observation is a multiple of the lot and
                                              their GCD is a multiple of it.

The master is authoritative and the derivation is the check — not the other way round. The
check is DIVISIBILITY, not the contract-value band: the band describes where lots sit AT
REVISION, and prices drift afterwards, so treating a drifted notional as an error is a false
alarm (APOLLOHOSP at Rs 11.2 lakh is drift, not a wrong lot). The band survives only as the
tie-break for a contract with no master row at all.

Both sources are kept because they fail differently. The master goes STALE — it is a
snapshot, ours is dated 30-Jul-2026, and it carries only the CURRENT lot, so it is simply
wrong about a historical bar that predates a revision. The GCD is computed from the bars
themselves and cannot go stale, only imprecise. Where they contradict each other this script
writes NOTHING for that contract and says so, because a silently-wrong lot is a 13x error in
notional (see the revision case below) and notional is what open item O12 turns on.

Current state: all 50 names agree, including ADANIENT's 309. That one is worth recording
because it is where the two sources genuinely interact: 309 = 3 x 103 and BOTH divide every
observation, so the GCD alone cannot choose. SEBI's minimum contract value settles it — NSE
sets lots so lot x price lands near Rs 5-10 lakh at revision, and 309 gives Rs 9.44 lakh
against 103's Rs 3.15 lakh. On today's prices 46 of 50 sit inside that band, 3 just below
(INFY 4.69, HDFCBANK 4.74, ITC 4.80 lakh, all of which have FALLEN since their last
revision) and APOLLOHOSP above at 11.16 lakh, having risen. Nothing sits at 2x or 3x the
band, which is what rules out the GCD being a multiple of the true lot across the board.

THE REVISION TRAP THIS GUARDS
-----------------------------
Lot sizes get revised — and the three names now under the Rs 5 lakh floor are exactly the
candidates for an increase at the next review. A per-SYMBOL GCD computed across a revision
returns the GCD of two different lots: 650 -> 700 yields gcd(650, 700) = 50, a plausible
round number that is wrong for both halves of the series and wrong by 13x on notional. So
the GCD is always computed per (symbol, expiry) and never per symbol, and a per-contract
GCD that changes mid-series is the revision FINGERPRINT rather than a bug. The current
one-month window contains no revision; the planned 12-month --expired backfill will.

    python3 lot_sizes.py                # report only, writes nothing
    python3 lot_sizes.py --write         # populate contract_size where both sources agree
    python3 lot_sizes.py --notional      # what O12 wanted: OI notional per underlying
"""
from __future__ import annotations

import argparse
import collections
import csv
import functools
import io
import math
import os
import sqlite3
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_AGENT)
# The repo root goes on the path too: importing `fetching` runs its __init__, which pulls in
# orchestrator, which imports chain_store from the root. Same three-entry path as
# download_stock_futures.py — copying the pattern rather than inventing a fourth one.
for _p in (_AGENT, _HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DB = os.path.join(_ROOT, "option_chains.db")
MASTER_ZIP = os.path.join(_ROOT, "SecurityMaster.zip")
MASTER_MEMBER = "FONSEScripMaster.txt"

# SEBI's minimum contract value for SINGLE STOCK derivatives. Index derivatives sit in a
# different, higher band — NIFTY's lot of 75 against 24,288 is Rs 18.2 lakh — which is why
# `instruments` carrying only the index lot was never going to answer this.
BAND_MIN = 500_000
BAND_MAX = 1_000_000


def _gcd(xs):
    return functools.reduce(math.gcd, xs)


def master_lots() -> dict[tuple[str, str], int]:
    """(breeze_code, expiry) -> lot size, from the exchange's contract list."""
    if not os.path.exists(MASTER_ZIP):
        sys.exit(f"missing {MASTER_ZIP} — this is the authoritative source, not optional")
    with zipfile.ZipFile(MASTER_ZIP) as z:
        txt = z.open(MASTER_MEMBER).read().decode("utf-8", "ignore")
    rows = list(csv.reader(io.StringIO(txt)))
    h = {k.strip('"').strip(): n for n, k in enumerate(rows[0])}
    for need in ("ShortName", "LotSize", "ExpiryDate", "InstrumentName"):
        if need not in h:
            sys.exit(f"scrip master has no {need!r} column — format changed, fix the parse")
    out: dict[tuple[str, str], int] = {}
    for r in rows[1:]:
        if len(r) <= 3 or r[1].strip('"') != "FUTSTK":
            continue
        try:
            out[(r[h["ShortName"]], r[h["ExpiryDate"]])] = int(r[h["LotSize"]])
        except ValueError:
            continue
    return out


def _iso(expiry: str) -> str:
    """'29-Sep-2026' -> '2026-09-29'. The master and the DB disagree on date format."""
    import datetime as dt
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(expiry.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return expiry.strip()[:10]


def derived_lots(con) -> dict[tuple[str, str], dict]:
    """(underlying, expiry_iso) -> gcd of OI, gcd of volume, and the price for the band test.

    Per CONTRACT, never per symbol. See the revision trap in the module docstring.
    """
    rows = con.execute("""select underlying, expiry, open_interest, volume, close, ts
                          from fo_price_bars
                          where instrument_type='FUT' and timeframe='1d'
                            and open_interest is not null
                          order by underlying, expiry, ts""").fetchall()
    acc: dict[tuple[str, str], dict] = {}
    for u, e, oi, vol, close, ts in rows:
        k = (u, str(e)[:10])
        a = acc.setdefault(k, {"oi": [], "vol": [], "close": None, "n": 0})
        a["oi"].append(int(oi))
        if vol:
            a["vol"].append(int(vol))
        a["close"] = close        # rows are ts-ordered, so this ends on the latest bar
        a["n"] += 1
    for k, a in acc.items():
        a["gcd_oi"] = _gcd(a["oi"])
        a["gcd_vol"] = _gcd(a["vol"]) if a["vol"] else None
        # Day-to-day OI CHANGES are also multiples of the lot, and differencing kills any
        # coincidental common factor in the levels. This is the test that makes
        # "OI is quoted in shares" a finding rather than a guess.
        d = [abs(a["oi"][i] - a["oi"][i - 1]) for i in range(1, len(a["oi"]))
             if a["oi"][i] != a["oi"][i - 1]]
        a["gcd_diff"] = _gcd(d) if d else None
    return acc


def best_divisor(g: int, price: float | None) -> int:
    """Largest divisor of g whose notional does not overshoot the band ceiling.

    The true lot L divides every OI, so L divides g — meaning g is a MULTIPLE of L and is
    only an UPPER BOUND. This picks among g's divisors using SEBI's contract-value band.
    Note it returns g itself whenever g's own notional is in range, so on clean data the
    rule does nothing; it exists for the case where g overshoots, which is the case that
    would otherwise pass silently.
    """
    if not price or price <= 0:
        return g
    divs = sorted((d for d in range(1, int(math.isqrt(g)) + 1) if g % d == 0), reverse=True)
    divs = sorted({d for d in divs} | {g // d for d in divs}, reverse=True)
    for d in divs:
        if d * price <= BAND_MAX:
            return d
    return divs[-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="populate contract_size where master and derivation agree")
    ap.add_argument("--notional", action="store_true",
                    help="print OI notional per underlying on the latest date")
    a = ap.parse_args()

    from fetching.broker import BreezeBroker
    con = sqlite3.connect(DB)
    mast = master_lots()
    # index the master by (code, iso expiry) and also by code alone, since a contract we
    # hold bars for may have settled and dropped out of the current master
    by_ce = {(c, _iso(e)): v for (c, e), v in mast.items()}
    by_code: dict[str, set] = collections.defaultdict(set)
    for (c, _), v in mast.items():
        by_code[c].add(v)

    der = derived_lots(con)
    agree, disagree, unlisted = [], [], []
    for (u, e), d in sorted(der.items()):
        code = BreezeBroker.code(u)
        lot = by_ce.get((code, e))
        src = "master(exact expiry)"
        if lot is None and len(by_code.get(code, ())) == 1:
            lot = next(iter(by_code[code]))
            src = "master(symbol, expiry absent)"
        g = d["gcd_oi"]
        rec = {"underlying": u, "expiry": e, "code": code, "master": lot,
               "gcd": g, "gcd_vol": d["gcd_vol"], "gcd_diff": d["gcd_diff"],
               "band_pick": best_divisor(g, d["close"]) if lot is None else None,
               "close": d["close"], "n": d["n"], "src": src, "note": ""}
        # THE TEST: does the master lot divide the observed OI series? Every OI is a
        # multiple of the true lot, so the true lot must divide their GCD. Equality is the
        # clean case. A GCD that is a proper MULTIPLE of the master means the series happens
        # to share an extra factor — unusual, not contradictory, lot stays the master. A GCD
        # the master does not divide at all IS a contradiction and blocks the write.
        if lot is None:
            unlisted.append(rec)
        elif g % lot:
            rec["note"] = f"master {lot} does not divide gcd {g} — contradiction"
            disagree.append(rec)
        else:
            if g != lot:
                rec["note"] = f"gcd is {g // lot}x the lot; series shares an extra factor"
            agree.append(rec)

    print(f"CONTRACT SIZE  —  master: {os.path.basename(MASTER_ZIP)}  "
          f"contracts with bars: {len(der)}\n")
    print(f"{'underlying':13}{'expiry':12}{'code':9}{'master':>8}{'gcd(OI)':>9}"
          f"{'gcd(dOI)':>9}{'lot x px':>12}  band")
    for r in agree + disagree + unlisted:
        lot = r["master"] or r["band_pick"] or r["gcd"]
        nt = (lot * r["close"]) if r["close"] else 0
        inb = "in" if BAND_MIN <= nt <= BAND_MAX else ("LOW" if nt < BAND_MIN else "HIGH")
        m = f"{r['master']:8d}" if r["master"] else f"{'--':>8}"
        gd = f"{r['gcd_diff']:9d}" if r["gcd_diff"] else f"{'-':>9}"
        print(f"{r['underlying']:13}{r['expiry']:12}{r['code']:9}{m}"
              f"{r['gcd']:9d}{gd}{nt:12,.0f}  {inb}")

    print(f"\n  agree      {len(agree):4d}   master lot divides the observed OI series")
    print(f"  DISAGREE   {len(disagree):4d}   nothing written for these")
    print(f"  unlisted   {len(unlisted):4d}   no master row (settled contract?)")
    lvl = sum(1 for r in agree + disagree if r["gcd_diff"] == r["gcd"])
    print(f"\n  gcd(OI levels) == gcd(day-to-day OI changes) on {lvl}/{len(der)} contracts —")
    print(f"  a coincidental common factor in the levels cannot survive differencing, so")
    print(f"  OI is quoted in SHARES and notional is open_interest x close directly.")
    if disagree:
        print("\n  DISAGREEMENTS — a revision inside the window, or a data defect. Resolve")
        print("  before writing; do NOT take the master on faith for historical bars.")
        for r in disagree:
            print(f"    {r['underlying']} {r['expiry']}: {r['note']}")
    notes = [r for r in agree if r["note"]]
    if notes:
        print("\n  worth a look (written anyway, the master still governs):")
        for r in notes:
            print(f"    {r['underlying']} {r['expiry']}: {r['note']}")

    if a.write:
        n = 0
        for r in agree:
            cur = con.execute("""update fo_price_bars set contract_size=?
                                 where instrument_type='FUT' and timeframe='1d'
                                   and underlying=? and substr(expiry,1,10)=?""",
                              (r["master"], r["underlying"], r["expiry"]))
            n += cur.rowcount
        con.commit()
        left = con.execute("""select count(*) from fo_price_bars
                              where instrument_type='FUT' and timeframe='1d'
                                and contract_size is null""").fetchone()[0]
        print(f"\n  wrote contract_size on {n:,} bars across {len(agree)} contracts; "
              f"{left:,} still NULL")
        if left:
            print("  (the remainder are the disagreements and unlisted contracts above — "
                  "left NULL on purpose)")
    else:
        print("\n  report only. Re-run with --write to populate contract_size.")

    if a.notional:
        d = con.execute("""select max(date(ts)) from fo_price_bars
                           where instrument_type='FUT' and timeframe='1d'""").fetchone()[0]
        print(f"\nOI NOTIONAL per underlying, front month, {d}")
        print("  This is TOTAL MARKET open interest, NOT the FII book. participant_oi is")
        print("  aggregate-only, so FII's share per underlying is unknown — see O12.")
        out = []
        for u in sorted({k[0] for k in der}):
            r = con.execute("""select open_interest, close from fo_price_bars
                               where instrument_type='FUT' and timeframe='1d'
                                 and underlying=? and date(ts)=?
                               order by expiry limit 1""", (u, d)).fetchone()
            if r:
                out.append((u, r[0] * r[1] / 1e7))
        out.sort(key=lambda x: -x[1])
        for u, v in out[:10]:
            print(f"   {u:13}{v:12,.0f} cr")
        print(f"   {'TOTAL':13}{sum(v for _, v in out):12,.0f} cr across {len(out)} names")


if __name__ == "__main__":
    main()
