#!/usr/bin/env python3
"""
lot_sizes.py — provide exchange lot sizes to the writer, and detect when the master goes stale.

WHAT THIS IS FOR, AFTER A CORRECTION IN FRAMING
-----------------------------------------------
`master_lots()` reads SecurityMaster.zip / FONSEScripMaster.txt — the exchange's own contract
list, via Breeze — and download_stock_futures.py calls it to stamp contract_size at capture
time. That is this file's primary job and it is not in question.

The verification half was originally framed as checking the lot size. That framing was wrong,
and the user said so: the master IS the exchange's list, so computing a GCD of open interest to
confirm what the master already states adds nothing. If the master says 500, the lot is 500.

What the arithmetic actually established was a DIFFERENT fact, and one the master cannot
express: **Breeze reports open interest and volume in SHARES, not in contracts.** The evidence
was that gcd(OI levels) equals gcd of the day-to-day OI CHANGES on 100/100 contracts, which a
coincidental common factor in the levels could not survive. That determination is what makes

    notional = open_interest x close

correct with no lot multiplier. Had OI been in contracts, Reliance's notional would have been
wrong by 500x. It was worth establishing once. It is now established, recorded here, and does
not need re-deriving nightly.

SO WHAT THE CHECK IS FOR NOW: A STALE MASTER
--------------------------------------------
One recurring failure remains, and nothing else can catch it. SecurityMaster.zip is a FILE ON
DISK with no freshness signal, carrying only the CURRENT lot. NSE revises lots to keep contract
value near Rs 5-10 lakh, and INFY (4.69), HDFCBANK (4.74) and ITC (4.80 lakh) currently sit
BELOW that floor — which is exactly the condition that triggers an increase. If a revision lands
and the zip is not re-downloaded, the writer stamps the old lot onto every new bar, silently and
indefinitely.

The bars are the only thing that can contradict the master. So the test is not "is the lot
right" — the master decides that — but "has the master gone stale", and it is the one question
the master cannot answer about itself. A contradiction between the two is reported; the lot is
never overridden.

    python3 lot_sizes.py                # is the master still consistent with the bars?
    python3 lot_sizes.py --notional     # OI notional per underlying, point-in-time

NO --write MODE
---------------
An earlier version populated contract_size by UPDATE. Wrong twice over: it targeted the
repo-local mirror, which db_config marks read-only by policy, and a later pass mutating rows is
the wrong place for the value anyway. The writer knows the contract it is fetching. This file
opens the database mode=ro and cannot write.
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

def _db() -> str:
    """READ path. The mirror is legitimate here — reading is what it exists for — but say
    which copy, because every level-dependent number inherits its staleness."""
    try:
        from db_config import resolve_db_path
        return resolve_db_path()
    except Exception:
        return os.path.join(_ROOT, "option_chains.db")


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
    ap.add_argument("--notional", action="store_true",
                    help="print OI notional per underlying on the latest date")
    a = ap.parse_args()

    from fetching.broker import BreezeBroker
    path = _db()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)   # read-only, enforced
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

    import datetime as _dt
    age = None
    if os.path.exists(MASTER_ZIP):
        age = (_dt.date.today()
               - _dt.date.fromtimestamp(os.path.getmtime(MASTER_ZIP))).days
    print(f"MASTER STALENESS CHECK  —  {os.path.basename(MASTER_ZIP)}"
          + (f", {age}d old" if age is not None else "")
          + f"  ·  {len(der)} contracts with bars")
    print(f"  reading (read-only): {path}")
    print(f"  the master decides the lot; this asks whether the master is still current\n")
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
    print(f"  DISAGREE   {len(disagree):4d}   master contradicts the bars -> REFRESH THE ZIP")
    print(f"  unlisted   {len(unlisted):4d}   no master row (settled contract?)")
    lvl = sum(1 for r in agree + disagree if r["gcd_diff"] == r["gcd"])
    print(f"\n  gcd(OI levels) == gcd(day-to-day OI changes) on {lvl}/{len(der)} contracts —")
    print(f"  a coincidental common factor in the levels cannot survive differencing, so")
    print(f"  OI is quoted in SHARES and notional is open_interest x close directly.")
    if disagree:
        print("\n  A CONTRADICTION MEANS THE MASTER IS PROBABLY STALE — re-download")
        print("  SecurityMaster.zip and re-run. If it persists after a refresh, the lot was")
        print("  revised mid-series and the older bars carry the older lot, correctly.")
        for r in disagree:
            print(f"    {r['underlying']} {r['expiry']}: {r['note']}")
    notes = [r for r in agree if r["note"]]
    if notes:
        print("\n  worth a look (written anyway, the master still governs):")
        for r in notes:
            print(f"    {r['underlying']} {r['expiry']}: {r['note']}")

    null = con.execute("""select count(*) from fo_price_bars
                          where instrument_type='FUT' and timeframe='1d'
                            and contract_size is null""").fetchone()[0]
    if null:
        print(f"\n  contract_size is NULL on {null:,} bars. This file will NOT write them:")
        print("  the writer sets it at capture time (download_stock_futures.py), and the")
        print("  database this resolves to may be the read-only mirror. Re-run the download")
        print("  against the primary to populate it.")

    rc = 1 if disagree else 0

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
