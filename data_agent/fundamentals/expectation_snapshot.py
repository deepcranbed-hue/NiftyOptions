#!/usr/bin/env python3
"""expectation_snapshot.py — capture what the market EXPECTS, before the print.

WHY THIS CANNOT WAIT
--------------------
Everything else in this repo can be rebuilt from history. This cannot. Consensus
estimates are revised continuously and the pre-announcement values are simply gone
afterwards — vendors serve the current number, not the number as of last Tuesday.
So the record either starts before a company reports or it starts a quarter late.

WHAT IT UNLOCKS
---------------
`earnings_reactions.json` measures how a stock MOVED on results day. It cannot say
why, because it has no idea what was expected. Trent is the case that motivated
this: revenue +18.5%, profit +22%, and the stock sold off — because analysts were
looking for 20-21% revenue growth, so a good absolute number was a miss relative to
expectation. Without a stored expectation, that quarter looks like an unexplained
negative reaction.

Pair a snapshot with the reaction the engine already measures and you get the thing
neither has alone:

    fundamentals (reported)  x  expectation (this file)  ->  reaction (measured)

After two earnings seasons that becomes testable: does "beat on absolute numbers,
miss versus expectation" reliably precede negative relative returns? That question
is answerable with data instead of reasoning — but only if the expectation side was
captured at the time.

WHAT IS CAPTURED, AND ITS LIMITS
--------------------------------
Yahoo's analyst fields, because they are free, already wired into this project
(nifty50_routes uses the same Ticker.info call), and available TODAY — which beats
a better source that arrives after the prints. They are coarse: a mean target and
an opinion count, not a full estimate distribution, and Yahoo's India coverage is
thinner than a terminal's.

The honest framing is that this is an expectation PROXY with its provenance
recorded, not a consensus feed. `source` and `captured_at` are stored on every row
so a better source can be added later without the two being confused. Forward P/E
is the most useful field here: it embeds the consensus forward EPS, so the change
in forward P/E across a print is a usable read on estimate revision.

APPEND-ONLY
-----------
Snapshots are never overwritten or de-duplicated by date. The file is a log; two
captures on the same day are two observations. Rewriting history is precisely the
failure this exists to prevent.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.append(_ROOT)

_CSV = os.path.join(_ROOT, "nifty-50-stock-list.csv")
_OUT = os.path.join(_ROOT, "expectation_snapshots.json")

# Same rename handling as everywhere else — a stale symbol silently returns nothing.
_TICKER_ALTS = {
    "ZOMATO": ["ETERNAL.NS", "ZOMATO.NS"],
    "TATAMOTORS": ["TMPV.NS", "TATAMOTORS.NS"],
}

_FIELDS = [
    # valuation the estimate is embedded in
    "trailingPE", "forwardPE", "priceToBook", "marketCap",
    # the analyst view
    "targetMeanPrice", "targetHighPrice", "targetLowPrice", "targetMedianPrice",
    "numberOfAnalystOpinions", "recommendationMean", "recommendationKey",
    # growth expectations
    "earningsGrowth", "revenueGrowth", "earningsQuarterlyGrowth",
    # where price sits when the expectation was taken
    "currentPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
]


def _tickers(symbol):
    return _TICKER_ALTS.get(symbol.upper(), [f"{symbol}.NS"])


def _snapshot_one(symbol):
    import yfinance as yf
    for tk in _tickers(symbol):
        try:
            t = yf.Ticker(tk)
            info = t.info or {}
            if not info.get("currentPrice") and not info.get("trailingPE"):
                continue
            row = {"symbol": symbol, "yahoo_ticker": tk}
            for f in _FIELDS:
                v = info.get(f)
                row[f] = round(float(v), 4) if isinstance(v, (int, float)) else v

            # Next scheduled results date, when Yahoo knows it — this is what makes
            # --upcoming able to catch a company BEFORE it reports.
            try:
                cal = t.calendar
                ed = None
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                if isinstance(ed, (list, tuple)) and ed:
                    ed = ed[0]
                row["next_earnings_date"] = str(ed)[:10] if ed else None
            except Exception:
                row["next_earnings_date"] = None
            return row
        except Exception:
            continue
    return {"symbol": symbol, "yahoo_ticker": None, "error": "no data"}


def _universe():
    with open(_CSV, newline="") as f:
        return [r["Symbol"].strip() for r in csv.DictReader(f) if r.get("Symbol")]


def _rows_hash(rows) -> str:
    """Content fingerprint of a capture. The only reliable proof a capture is NEW."""
    return hashlib.md5(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def _load():
    try:
        with open(_OUT) as f:
            return json.load(f)
    except Exception:
        return {"note": ("Append-only log of pre-announcement expectations. Never "
                         "rewrite entries — a revised snapshot is a new observation, "
                         "and overwriting is the failure this file exists to prevent."),
                "source": "yfinance Ticker.info (expectation PROXY, not a consensus feed)",
                "snapshots": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list; default is all 50")
    ap.add_argument("--upcoming", type=int, default=0, metavar="DAYS",
                    help="only capture names reporting within N days (needs a Yahoo "
                         "earnings date; names without one are captured anyway rather "
                         "than silently skipped)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="append even if identical to today's last capture")
    args = ap.parse_args()

    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else _universe())
    print(f"capturing expectations for {len(symbols)} symbols...")

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(_snapshot_one, symbols))

    now = datetime.now()
    if args.upcoming:
        cutoff = (now + timedelta(days=args.upcoming)).strftime("%Y-%m-%d")
        today = now.strftime("%Y-%m-%d")
        kept = []
        for r in rows:
            ed = r.get("next_earnings_date")
            # No date is NOT a reason to skip: a missing date is Yahoo's gap, and a
            # missed capture cannot be recovered. Better a redundant row than a hole.
            if not ed or ed in ("None", "") or today <= ed <= cutoff:
                kept.append(r)
        print(f"  --upcoming {args.upcoming}d: {len(kept)}/{len(rows)} kept")
        rows = kept

    ok = [r for r in rows if not r.get("error")]
    with_target = [r for r in ok if r.get("targetMeanPrice")]
    with_fwd = [r for r in ok if r.get("forwardPE")]
    print(f"  resolved {len(ok)}/{len(rows)}   "
          f"target price {len(with_target)}   forward P/E {len(with_fwd)}")

    for r in sorted(ok, key=lambda x: x["symbol"])[:8]:
        print(f"   {r['symbol']:12} fwdPE {str(r.get('forwardPE')):>8}  "
              f"target {str(r.get('targetMeanPrice')):>9}  "
              f"n={str(r.get('numberOfAnalystOpinions')):>4}  "
              f"reports {r.get('next_earnings_date')}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    doc = _load()

    # A COUNT THAT GROWS IS NOT PROOF A CAPTURE HAPPENED (correction C36).
    # On 2026-08-17 two wrapper instances started in the same second, each read the file
    # before the other wrote, and the log went 2 -> 4 with snapshot #4 byte-identical to
    # #3 — same captured_at, same md5 over rows. The wrapper reported OK twice because
    # its only test was that the count went up, which is exactly what a duplicate does.
    # An identical payload on the SAME DAY is a duplicate write, not an observation.
    # An identical payload on a LATER day is a real observation (a week with no revisions
    # is a finding), so the guard is deliberately scoped to the day and not to content
    # alone.
    prev = doc["snapshots"][-1] if doc.get("snapshots") else None
    if prev and not args.force:
        if (prev.get("captured_at", "")[:10] == now.strftime("%Y-%m-%d")
                and _rows_hash(prev.get("rows") or []) == _rows_hash(rows)):
            print(f"\nREFUSED — identical to snapshot #{len(doc['snapshots'])} captured "
                  f"{prev['captured_at']}: same day, same {len(rows)} rows, same hash.")
            print("  Nothing appended. This is a duplicate write, not a lost capture.")
            print("  Pass --force to record a deliberate second same-day capture.")
            return 2

    doc["snapshots"].append({
        "captured_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "yfinance Ticker.info",
        "n": len(rows),
        "rows": rows,
        "rows_md5": _rows_hash(rows),
    })
    with open(_OUT, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"\nappended snapshot #{len(doc['snapshots'])} -> {_OUT}")
    print("Re-run before each earnings season. The value is in the SEQUENCE:")
    print("one capture is a number, two around a print are a measurement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
