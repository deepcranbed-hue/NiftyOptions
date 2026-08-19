#!/usr/bin/env python3
"""backfill_nse_participants -- deepen FII/DII participant history, and fix a
                                mislabelling that has been quietly distorting the analysis.

WHY THIS EXISTS
The FII tests in this repo are limited to 247 observations, which is not enough to say
whether FII positioning forecasts anything: at n=247 the smallest correlation detectable
with 80% power is about |r|=0.18, and effects well below that would still matter. NSE
publishes this data daily going back years, so the constraint is collection, not
statistics.

THE BUG THIS ALSO FIXES -- read this before trusting any past FII result.
NSE publishes TWO participant files each day, with nearly identical column headers:

    fao_participant_vol_DDMMYYYY.csv   contracts TRADED that day      -> a FLOW
    fao_participant_oi_DDMMYYYY.csv    contracts OUTSTANDING at close -> a POSITION

download_nse_participants.py fetches the VOL file, but writes it into columns named
idx_fut_long / idx_fut_short, which read like open interest. Anything that then computed
`idx_fut_long - idx_fut_short` and called it "FII positioning" was actually measuring
one day's net BUYING, not the standing book.

The data says so plainly. For FII index futures in the existing table:

    lag-1 autocorrelation of the "level" : +0.325     (a position would be ~0.95+)
    mean |day-on-day change| / std        :  0.88     (a position barely moves per day)

That is a stationary daily flow, not an inventory. Two consequences:
  * The "extreme positioning regime" question -- is the market different when FII are
    heavily long or short? -- CANNOT be answered with the vol file at all. A large
    trading day tells you activity, not direction of the book. It needs the OI file,
    which this repo has never collected.
  * Differencing a flow that is already stationary injects noise rather than removing a
    trend. The lag-1 autocorrelation of that difference is -0.357, which is close to the
    -0.5 you get from differencing white noise. Specifications built on the "change" and
    "acceleration" of this series were differencing a flow twice and thrice.

So this script collects BOTH series, into separate tables, correctly named.

    participant_flows  (existing, untouched)  <- fao_participant_vol_  : daily FLOW
    participant_oi     (new)                  <- fao_participant_oi_   : end-of-day POSITION

WHAT IT DOES DIFFERENTLY FROM THE EXISTING SCRIPT
  * walks an arbitrary date range instead of a hardcoded 365 days
  * fetches both series per date
  * resumable: skips dates already stored, and records confirmed non-trading days in
    nse_fetch_skips so a rerun does not re-request 2,000 holidays and weekends
  * survives a 403 with exponential backoff and a fresh session instead of aborting the
    whole run, so an overnight backfill does not die at hour two
  * parses defensively: NSE has changed header spellings and thousands separators over
    the years, so columns are matched case/space-insensitively and numbers are cleaned
    rather than passed to int() and hoped for
  * --probe prints the raw header of one file so the parse can be verified against
    reality before 2,000 requests are made

USAGE
    python -m data_agent.macro.backfill_nse_participants --probe
    python -m data_agent.macro.backfill_nse_participants --from 2018-01-01 --dry-run
    python -m data_agent.macro.backfill_nse_participants --from 2018-01-01 --delay 2.5
    ... --series oi          # only the missing positioning series
    ... --from 2018-01-01 --to 2019-12-31    # chunk it across sessions

RUNS ON YOUR MACHINE. It needs network access and the local option_chains.db.

BE A GOOD CITIZEN. Default delay is 2.5s with jitter, roughly 2-3 hours for a full
two-series backfill to 2018. Do not lower it to hammer a free public archive.
"""
from __future__ import annotations

# REPO ROOT ON sys.path — placed AFTER the __future__ import, which the language requires to
# be the first statement after the docstring. An earlier attempt put this block above the
# docstring and broke the file outright: SyntaxError, "from __future__ imports must occur at
# the beginning of the file".
#
# WHY IT IS NEEDED AT ALL. `python3 data_agent/macro/backfill_nse_participants.py` puts THIS
# FILE's directory on sys.path[0] — not the working directory — so `from db_config import ...`
# inside _resolve_db() raised ModuleNotFoundError on every run that let the resolver choose
# the database. The failure was invisible for two reasons: --help and --probe never reach
# _resolve_db, and passing --db short-circuits it BEFORE the import, which is how every
# successful manual run was done. So the script worked whenever a human typed a path and
# failed the moment a scheduler did the correct thing. participant_oi sat at 12-Aug because
# of this while participant_flows stayed current — its sibling download_nse_participants.py
# has carried this bootstrap all along.
import os as _os, sys as _sys
_RT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
_RT in _sys.path or _sys.path.insert(0, _RT)

import argparse
import io
import os
import random
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("requests not installed:  pip install requests")

# The Drive copy is the source of truth; <repo>/option_chains.db is a read-only mirror.
# This used to default to the MIRROR and rely on the operator exporting $SQLITE_DB_PATH on
# every invocation — which DATA_AGENT_DAILY_CHECKLIST.md duly did, twice. A manual export
# standing in for a control is the same defect class as C37: forget it once and the write
# lands in a copy while the run reports success. Resolved AFTER argument parsing so --help
# and --probe still work where Drive is unreachable.
def _resolve_db(explicit: str | None) -> str:
    if explicit:
        return explicit
    from db_config import resolve_writable_db_path
    return resolve_writable_db_path()


_BASE = "https://nsearchives.nseindia.com/content/nsccl"
_SERIES = {"vol": "participant_flows", "oi": "participant_oi"}

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# Logical column -> the header spellings NSE has used. Matching is done on a normalised
# key (lowercased, non-alphanumerics stripped) so "Future Index Long", "future index
# long " and "FutureIndexLong" all collapse to the same thing.
_COLMAP = {
    "idx_fut_long":       ["future index long", "futureindexlong"],
    "idx_fut_short":      ["future index short"],
    "stk_fut_long":       ["future stock long"],
    "stk_fut_short":      ["future stock short"],
    "idx_opt_call_long":  ["option index call long"],
    "idx_opt_call_short": ["option index call short"],
    "idx_opt_put_long":   ["option index put long"],
    "idx_opt_put_short":  ["option index put short"],
    "stk_opt_call_long":  ["option stock call long"],
    "stk_opt_call_short": ["option stock call short"],
    "stk_opt_put_long":   ["option stock put long"],
    "stk_opt_put_short":  ["option stock put short"],
    "total_long":         ["total long contracts", "total long"],
    "total_short":        ["total short contracts", "total short"],
}
_CLIENT_COL = ["client type", "clienttype"]

_DDL = """
CREATE TABLE IF NOT EXISTS participant_oi (
    flow_date TEXT NOT NULL, participant_type TEXT NOT NULL,
    idx_fut_long INTEGER, idx_fut_short INTEGER,
    stk_fut_long INTEGER, stk_fut_short INTEGER,
    idx_opt_call_long INTEGER, idx_opt_call_short INTEGER,
    idx_opt_put_long INTEGER, idx_opt_put_short INTEGER,
    stk_opt_call_long INTEGER, stk_opt_call_short INTEGER,
    stk_opt_put_long INTEGER, stk_opt_put_short INTEGER,
    total_long INTEGER, total_short INTEGER, updated_at TEXT,
    PRIMARY KEY (flow_date, participant_type)
);
CREATE TABLE IF NOT EXISTS nse_fetch_skips (
    flow_date TEXT NOT NULL, series TEXT NOT NULL, reason TEXT, checked_at TEXT,
    PRIMARY KEY (flow_date, series)
);
"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _num(v) -> int:
    """NSE has shipped '1,234', ' 1234 ', '-' and blanks in these columns."""
    if v is None:
        return 0
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "nan", "None"):
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _parse(text: str) -> list[dict]:
    """Return one dict per participant row, or [] if the file isn't what we expect.

    The header row is not always the second line -- NSE prepends a descriptive banner
    whose height has varied -- so the header is LOCATED by looking for the client-type
    column rather than assumed to sit at a fixed offset.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    hdr_i = None
    for i, ln in enumerate(lines[:6]):
        if any(_norm(c) in _CLIENT_COL for c in ln.split(",")):
            hdr_i = i
            break
    if hdr_i is None:
        return []
    import csv as _csv
    rows = list(_csv.reader(io.StringIO("\n".join(lines[hdr_i:]))))
    header = [_norm(c) for c in rows[0]]

    def find(cands):
        for c in cands:
            if _norm(c) in header:
                return header.index(_norm(c))
        return None

    ci = find(_CLIENT_COL)
    if ci is None:
        return []
    idx = {k: find(v) for k, v in _COLMAP.items()}
    out = []
    for r in rows[1:]:
        if len(r) <= ci or not r[ci].strip():
            continue
        who = r[ci].strip()
        if _norm(who) in ("", "total") and who.upper() != "TOTAL":
            continue
        rec = {"participant_type": who}
        for k, j in idx.items():
            rec[k] = _num(r[j]) if (j is not None and j < len(r)) else 0
        out.append(rec)
    return out


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:                                    # cookie handshake; archive 403s without it
        s.get("https://www.nseindia.com", timeout=12)
        time.sleep(1.5)
    except Exception as e:
        print(f"  warn: cookie handshake failed ({type(e).__name__}) -- continuing")
    return s


def _store(conn, table: str, d: str, recs: list[dict]) -> int:
    cols = ["flow_date", "participant_type"] + list(_COLMAP) + ["updated_at"]
    now = datetime.now().isoformat(timespec="seconds")
    vals = [tuple([d, r["participant_type"]] + [r[k] for k in _COLMAP] + [now]) for r in recs]
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})", vals)
    conn.commit()
    return len(vals)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=None,
                    help="override the resolved primary; normally omit")
    ap.add_argument("--from", dest="start", default="2018-01-01")
    ap.add_argument("--to", dest="end", default=date.today().isoformat())
    ap.add_argument("--series", choices=["oi", "vol", "both"], default="both")
    ap.add_argument("--delay", type=float, default=2.5,
                    help="seconds between requests (jittered). Do not lower this to be rude.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="fetch one recent file per series and print its raw header")
    args = ap.parse_args()
    args.db = _resolve_db(args.db)

    sess = _session()

    if args.probe:
        for series in (["oi", "vol"] if args.series == "both" else [args.series]):
            for back in range(1, 12):            # walk back to find a trading day
                d = date.today() - timedelta(days=back)
                url = f"{_BASE}/fao_participant_{series}_{d.strftime('%d%m%Y')}.csv"
                r = sess.get(url, timeout=15)
                print(f"\n[{series}] {d} -> HTTP {r.status_code}  {url}")
                if r.status_code == 200:
                    for ln in r.text.splitlines()[:3]:
                        print("   ", ln[:180])
                    p = _parse(r.text)
                    print(f"    parsed {len(p)} rows; types: "
                          f"{[x['participant_type'] for x in p]}")
                    if p:
                        print(f"    FII idx_fut long/short: "
                              f"{[(x['idx_fut_long'], x['idx_fut_short']) for x in p if 'FII' in x['participant_type'].upper()]}")
                    break
                time.sleep(args.delay)
        return 0

    if not os.path.exists(args.db):
        return print(f"db not found: {args.db}") or 1
    conn = sqlite3.connect(args.db)
    conn.executescript(_DDL)

    d0, d1 = date.fromisoformat(args.start), date.fromisoformat(args.end)
    series_list = ["oi", "vol"] if args.series == "both" else [args.series]
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    days = [d for d in days if d.weekday() < 5]          # NSE is closed at weekends
    days.sort(reverse=True)                              # newest first: useful data early

    todo = []
    for series in series_list:
        table = _SERIES[series]
        have = {r[0] for r in conn.execute(f"SELECT DISTINCT flow_date FROM {table}")}
        skip = {r[0] for r in conn.execute(
            "SELECT flow_date FROM nse_fetch_skips WHERE series=?", (series,))}
        todo += [(series, d) for d in days
                 if d.isoformat() not in have and d.isoformat() not in skip]

    print(f"db      : {args.db}")
    print(f"range   : {d0} .. {d1}   ({len(days)} weekdays)")
    for series in series_list:
        table = _SERIES[series]
        n = conn.execute(f"SELECT COUNT(DISTINCT flow_date) FROM {table}").fetchone()[0]
        print(f"  {series:3s} -> {table:20s} have {n:5d} days")
    print(f"to fetch: {len(todo)} requests  (~{len(todo) * args.delay / 3600:.1f}h "
          f"at {args.delay}s)")
    if args.dry_run:
        print("dry run -- nothing fetched")
        return 0

    ok = miss = fail = 0
    backoff = args.delay
    for i, (series, d) in enumerate(todo, 1):
        ds = d.isoformat()
        url = f"{_BASE}/fao_participant_{series}_{d.strftime('%d%m%Y')}.csv"
        try:
            r = sess.get(url, timeout=20)
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(todo)}] {series} {ds} network {type(e).__name__}")
            time.sleep(min(backoff * 2, 120)); backoff = min(backoff * 2, 120)
            continue

        if r.status_code == 200:
            recs = _parse(r.text)
            if recs:
                n = _store(conn, _SERIES[series], ds, recs)
                ok += 1
                print(f"[{i}/{len(todo)}] {series} {ds} +{n} rows")
            else:
                # 200 but unparseable: record it so a rerun doesn't loop on it forever,
                # and say so loudly -- this is how a silent format change gets noticed.
                conn.execute("INSERT OR REPLACE INTO nse_fetch_skips VALUES (?,?,?,?)",
                             (ds, series, "unparseable", datetime.now().isoformat()))
                conn.commit(); fail += 1
                print(f"[{i}/{len(todo)}] {series} {ds} UNPARSEABLE -- run --probe, the "
                      f"header may have changed")
            backoff = args.delay
        elif r.status_code == 404:
            # A 404 MEANS TWO DIFFERENT THINGS AND ONLY ONE OF THEM IS PERMANENT.
            #
            # NSE publishes day D's participant file AFTER the close on D. Ask before that
            # and the archive answers 404 — identical to the 404 for a real holiday. Cache
            # the second and a rerun correctly skips a holiday forever; cache the first and
            # the session is skipped forever too, because `todo` is built by subtracting
            # nse_fetch_skips. That is exactly what happened: 2026-08-13 was recorded
            # 404_no_trading at 14:38 IST on 2026-08-13 — 52 minutes BEFORE the 15:30 close
            # — and participant_oi has been missing a trading day ever since while
            # participant_flows, fetched later, holds it. 16 of the 17 cached skips were
            # genuine holidays; this was the one that was not.
            #
            # Same family as the flows cache writing 0.0 on failure: an error state stored
            # as a legitimate value, indistinguishable afterwards. §0 already says
            # "publication lag is part of the signal" — the register knew, this code did not.
            #
            # So: only a date STRICTLY IN THE PAST may be cached as a non-trading day.
            # Today's 404 is "not yet", is not written, and the next run asks again.
            _today_ist = (datetime.now(timezone(timedelta(hours=5, minutes=30)))
                          .strftime("%Y-%m-%d"))
            if ds < _today_ist:
                conn.execute("INSERT OR REPLACE INTO nse_fetch_skips VALUES (?,?,?,?)",
                             (ds, series, "404_no_trading", datetime.now().isoformat()))
                conn.commit(); miss += 1
            else:
                miss += 1
                print(f"[{i}/{len(todo)}] {series} {ds} 404 but that is TODAY — NSE "
                      f"publishes after the close, so this is 'not yet', not 'never'. "
                      f"NOT cached; run again after 15:30 IST")
        elif r.status_code in (401, 403, 429):
            # Do NOT abort the run -- back off, re-handshake, and retry this date later.
            backoff = min(max(backoff * 2, 30), 600)
            print(f"[{i}/{len(todo)}] {series} {ds} HTTP {r.status_code} -- backing off "
                  f"{backoff:.0f}s and renewing session")
            time.sleep(backoff)
            sess = _session()
            todo.append((series, d))
            continue
        else:
            fail += 1
            print(f"[{i}/{len(todo)}] {series} {ds} HTTP {r.status_code}")

        time.sleep(backoff * random.uniform(0.8, 1.3))   # jitter: don't look robotic

    print(f"\nstored {ok} day-series, {miss} non-trading days, {fail} failures")
    for series in series_list:
        t = _SERIES[series]
        row = conn.execute(f"SELECT MIN(flow_date),MAX(flow_date),"
                           f"COUNT(DISTINCT flow_date) FROM {t}").fetchone()
        print(f"  {t:20s} {row[0]} .. {row[1]}   {row[2]} days")
    print("\nNOTE: participant_flows is VOLUME (a daily flow); participant_oi is OPEN "
          "INTEREST (a standing position). They are different questions -- do not mix "
          "them in one regressor.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
