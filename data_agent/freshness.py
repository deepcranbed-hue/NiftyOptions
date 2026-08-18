#!/usr/bin/env python3
"""
freshness.py — what is stale, why it matters, and the command that fixes it.

WHY THIS EXISTS
---------------
Several inputs in this repo refresh manually or on an event rather than on a clock, and
"remember to run it" is not a control. It has already failed three times:

  * `download_screener.py` skipped every cached workbook and printed "All downloads
    complete". A deliberate refresh changed nothing and reported success.
  * `delivery_history.json` therefore sat frozen at 37 of 47 names for Q1 FY27, so the
    tracker quoted a +3.7% exit rate when the panel figure was +7.1% — and the only reason
    anyone noticed was a question about why a number looked odd.
  * `run_expectation_snapshot.sh` logged "OK snapshots 2 -> 4" twice in the same second
    for one real capture and one byte-identical duplicate (C36). Its proof of success was
    that the count went up, which is exactly what a duplicate write does.

Every one of those was a job that reported success while doing nothing. So staleness is
checked here, and — this is the part that matters — checked against the DATA wherever a
data test exists, not against the calendar. "Older than 90 days" is a guess about when
results season was. "The page scrape holds a quarter the panel does not" is a fact, and it
is the actual condition that made the exit rate wrong. Calendar age is the fallback only
where no data test is possible.

BLOCKING VS ADVISORY
--------------------
A checker that cries wolf gets ignored, which leaves you worse off than no checker. So
each entry declares whether staleness actually changes a number that gets read:

  blocking   a published figure is or will be wrong. Exits non-zero.
  advisory   a channel is degraded but something downstream routes around it. Reported,
             never fatal.

The export's 37-name quarterly gap is the worked example. It looks alarming and it is the
exact defect that caused the +3.7% error — but the quarterly series now comes from
`attributable_panel.json` (EPS x shares, 47 names) and the screen's gates read the ANNUAL
series, which is complete at 47. So it is advisory: real, worth fixing, not worth a weekly
alarm. Verified rather than assumed — that check is `check_export_quarters`.

USAGE
    python3 freshness.py                # full report; exit 1 if anything blocking is due
    python3 freshness.py --quiet        # only the lines that need action
    python3 freshness.py --all          # include advisory items in the exit code
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE) if os.path.basename(_HERE) == "data_agent" else _HERE
TABLES = os.path.join(ROOT, "data_agent", "fundamentals", "screener_page_tables.csv")
UNIVERSE = os.path.join(ROOT, "nifty-50-stock-list.csv")

OK, WARN, DUE = "OK", "WARN", "DUE"

# Indian filings: quarter ends 31-Mar/30-Jun/30-Sep/31-Dec, results land inside ~45 days
# (SEBI's limit for quarterly results). A quarter is only "expected" once that has passed.
_FILING_WINDOW_DAYS = 45
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _age_days(path: str) -> int | None:
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return None
    return (dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(p))).days


def _load(path: str):
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as fh:
            return json.load(fh)
    except Exception:
        return None


def _last_expected_quarter(today: dt.date) -> str:
    """The newest quarter-end whose filing window has closed. Data-independent, but a
    RULE rather than a guess: it is derived from the quarter-end calendar and SEBI's
    45-day limit, not from 'about three months ago'."""
    ends = [dt.date(y, m, d)
            for y in (today.year - 1, today.year)
            for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    past = [e for e in ends if (today - e).days > _FILING_WINDOW_DAYS]
    return max(past).isoformat() if past else ""


def _scrape_quarters() -> dict[str, int]:
    """period -> number of symbols with an EPS row. EPS is the metric the panel derives
    from, so counting it is counting usable coverage rather than mere presence."""
    if not os.path.exists(TABLES):
        return {}
    out: dict[str, set] = {}
    with open(TABLES, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("section") == "quarters" and r.get("metric") == "EPS in Rs":
                out.setdefault(r["period"], set()).add(r["symbol"])
    return {k: len(v) for k, v in out.items()}


# ---------------------------------------------------------------- data-driven checks
def check_panel_vs_scrape():
    """THE check. The quarterly series every growth number is now measured on is
    `attributable_panel.json`; the page scrape is its input. If the scrape holds a quarter
    the panel does not, the panel was not rebuilt and the exit rate is measured on a stale
    panel — which is precisely how +7.1% got published as +3.7%."""
    pn = _load("attributable_panel.json")
    sc = _scrape_quarters()
    if not pn or not sc:
        return WARN, "cannot compare (missing panel or page scrape)"
    ser = pn.get("series") or []
    if not ser:
        return DUE, "panel has no series at all"
    p_last, p_names = ser[-1]["period"], ser[-1].get("names", 0)
    s_last = max(sc)
    if s_last > p_last:
        return DUE, (f"scrape reaches {s_last}, panel only {p_last} — "
                     f"the panel was not rebuilt after the last scrape")
    if p_names < 45:
        return DUE, (f"both reach {p_last} but the panel covers {p_names} names — "
                     f"partial coverage reads as low growth")
    return OK, f"panel {p_last} on {p_names} names, level with the scrape"


def check_scrape_quarter():
    """Is the scrape itself behind the exchange? Independent of the panel: if the scrape
    never picked up the newest filed quarter, rebuilding the panel changes nothing."""
    sc = _scrape_quarters()
    if not sc:
        return DUE, "screener_page_tables.csv missing"
    want = _last_expected_quarter(dt.date.today())
    have = max(sc)
    if want and have < want:
        return DUE, (f"newest filed quarter is {want} (window closed) but the scrape "
                     f"stops at {have}")
    return OK, f"scrape holds {have}, {sc[have]} symbols (expected through {want})"


def check_export_quarters():
    """The Excel export's quarterly coverage. ADVISORY on purpose — and the reason is
    measured, not assumed: the quarterly series comes from the panel, and the screen's
    gates read the export's ANNUAL series, which is checked separately below."""
    d = _load("delivery_history.json")
    if not d:
        return DUE, "missing"
    hist = d.get("history") or {}
    q: dict[str, int] = {}
    for v in hist.values():
        for x in v.get("quarters", []):
            if x.get("net_profit") is not None:
                q[x["period"]] = q.get(x["period"], 0) + 1
    if not q:
        return DUE, "no quarterly rows"
    last = max(q)
    prev = sorted(q)[-2] if len(q) > 1 else last
    if q[last] < q[prev]:
        return WARN, (f"{last} covers {q[last]} names against {q[prev]} at {prev} — "
                      f"export is a quarter behind; panel covers it, annual gates do not "
                      f"use it")
    return OK, f"{last} on {q[last]} names"


def check_export_annual():
    """BLOCKING. This is what the quality screen's gates actually read. A short newest
    fiscal year silently shrinks the eligible universe instead of failing."""
    d = _load("delivery_history.json")
    if not d:
        return DUE, "missing"
    hist = d.get("history") or {}
    a: dict[str, int] = {}
    for v in hist.values():
        for s in v.get("series", []):
            a[s["period"]] = a.get(s["period"], 0) + 1
    if not a:
        return DUE, "no annual rows"
    last = max(a)
    univ: list[str] = []
    if os.path.exists(UNIVERSE):
        with open(UNIVERSE, newline="", encoding="utf-8") as fh:
            univ = [r["Symbol"].strip().upper() for r in csv.DictReader(fh) if r.get("Symbol")]
    # Name them. A recurring WARN that says "3 members missing" teaches you to skip the
    # line; one that says WHICH three stays actionable, and shows when the set changes.
    missing = sorted(set(univ) - set(hist)) if univ else []
    if a[last] < len(hist):
        return DUE, f"newest FY {last} covers {a[last]} of {len(hist)} names in the export"
    note = f"FY {last} complete on all {a[last]} export names"
    if missing:
        return WARN, note + (f"; never screened for want of 6+ years of annual history "
                             f"(_MIN_YEARS): {', '.join(missing)}")
    return OK, note


def check_snapshots():
    """Distinct captures, not row count. C36's duplicate grew the count without adding an
    observation, and the revisions channel gates on that count."""
    import hashlib
    d = _load("expectation_snapshots.json")
    snaps = (d or {}).get("snapshots") or []
    if not snaps:
        return DUE, "no snapshots at all"

    def h(rows):
        return hashlib.md5(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()

    keys = [(s.get("captured_at", "")[:10], h(s.get("rows") or [])) for s in snaps]
    distinct = len(set(keys))
    dups = len(keys) - distinct
    last = max(s.get("captured_at", "") for s in snaps)[:10]
    try:
        age = (dt.date.today() - dt.date.fromisoformat(last)).days
    except ValueError:
        return DUE, f"unparseable last capture {last!r}"

    msg = f"{distinct} distinct captures, last {last} ({age}d ago)"
    if dups:
        return DUE, msg + f" — {dups} DUPLICATE write(s) inflating the count (C36)"
    if distinct < 3:
        msg += f" — {3 - distinct} more before revisions become measurable"
    if age > 9:
        return DUE, msg
    return (OK if distinct >= 3 else WARN), msg


def check_fii_quarter():
    """FII shareholding is quarterly. Two things can be wrong: the file can be behind a
    closed filing window, and its period LABELS can be junk — three names carry
    non-quarter-end months, which is a vendor defect, not a staleness one."""
    d = _load("fii_holdings.json")
    if not d:
        return DUE, "missing"
    hold = d.get("holdings") or {}
    labels = [h.get("period") for h in hold.values() if h.get("period")]
    if not labels:
        return DUE, "no period labels"

    def parse(lbl):
        parts = str(lbl).split()
        if len(parts) == 2 and parts[0][:3] in _MONTHS:
            try:
                return dt.date(int(parts[1]), _MONTHS[parts[0][:3]], 1)
            except ValueError:
                return None
        try:
            return dt.date.fromisoformat(str(lbl)[:10]).replace(day=1)
        except ValueError:
            return None

    parsed = [(l, parse(l)) for l in labels]
    bad_month = sorted({l for l, p in parsed if p and p.month not in (3, 6, 9, 12)})
    dates = [p for _, p in parsed if p]
    if not dates:
        return DUE, f"no parseable labels (sample {labels[:3]})"
    newest = max(dates)
    today = dt.date.today()
    want = dt.date.fromisoformat(_last_expected_quarter(today)).replace(day=1)
    msg = f"newest label {newest.strftime('%b %Y')}, filed quarter expected {want.strftime('%b %Y')}"
    if len(bad_month) > 3:
        bad_month = bad_month[:3] + [f"+{len(bad_month) - 3} more"]
    if bad_month:
        msg += f"; NON-QUARTER labels: {', '.join(bad_month)}"
    if newest < want:
        return DUE, msg
    return (WARN if bad_month else OK), msg


def check_screen():
    """The screen itself. Its own `as_of` beats the file mtime — a reformat or a manual
    edit touches mtime without changing a number."""
    d = _load("quality_growth.json")
    if not d:
        return DUE, "missing"
    as_of = str(d.get("as_of", ""))[:10]
    try:
        age = (dt.date.today() - dt.date.fromisoformat(as_of)).days
    except ValueError:
        return DUE, f"unparseable as_of {as_of!r}"
    n = len(((d.get("screen") or {}).get("selected")) or d.get("screen") or [])
    msg = f"as_of {as_of} ({age}d), {n} entries in screen"
    if age > 8:
        return DUE, msg
    panel_age, screen_age = _age_days("attributable_panel.json"), _age_days("quality_growth.json")
    if panel_age is not None and screen_age is not None and panel_age < screen_age:
        return WARN, msg + " — but the panel is newer than the screen; re-run to pick it up"
    return OK, msg


def check_scrip_master():
    """SecurityMaster.zip — the exchange's contract list, refreshed monthly.

    BLOCKING, and the reason is that its staleness is written into stored data rather than
    just read. download_stock_futures.py stamps contract_size from this file at capture time,
    so a stale zip does not produce a wrong ANSWER — it produces wrong ROWS, permanently, and
    silently. Nothing downstream can tell a correctly-recorded old lot from a stale one.

    Two tests. Age, because the file carries no version. And coverage: the master must list
    an expiry at least as far out as the newest contract we hold bars for, since a master
    predating a new contract's introduction cannot know its lot at all.
    """
    p = os.path.join(ROOT, "SecurityMaster.zip")
    if not os.path.exists(p):
        return DUE, "MISSING — the writer has no lot sizes without it"
    age = _age_days("SecurityMaster.zip")
    msg = f"{age}d old (refresh monthly)"
    try:
        import zipfile, csv as _csv, io as _io
        with zipfile.ZipFile(p) as z:
            txt = z.open("FONSEScripMaster.txt").read().decode("utf-8", "ignore")
        rows = list(_csv.reader(_io.StringIO(txt)))
        h = {k.strip('"').strip(): n for n, k in enumerate(rows[0])}
        exps = set()
        for r in rows[1:]:
            if len(r) > 3 and r[1].strip('"') == "FUTSTK":
                exps.add(_master_iso(r[h["ExpiryDate"]]))
        far = max(exps) if exps else ""
        msg += f", lists {len(exps)} expiries out to {far}"
    except Exception as exc:
        return DUE, msg + f" — unreadable ({exc})"

    # THE DATA TEST, and the reason age alone is not enough. NSE introduces a new far month
    # as each near month settles. A master that predates a contract's introduction has no row
    # for it, so the writer stamps NULL or nothing — and no amount of the file being "only
    # 20 days old" fixes that. Read-only, and failures here are not fatal: an unreadable
    # database is a different problem from a stale master, and conflating them would report
    # the wrong fix.
    try:
        import sqlite3
        db = os.path.join(ROOT, "option_chains.db")
        if os.path.exists(db) and far:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            row = con.execute("""select max(substr(expiry,1,10)) from fo_price_bars
                                 where instrument_type='FUT' and timeframe='1d'""").fetchone()
            held = row[0] if row else None
            if held and held > far:
                return DUE, (msg + f" — but we hold bars for {held}, beyond the master's "
                                   f"furthest listing: that contract has no lot size")
            if held:
                msg += f"; covers the furthest contract held ({held})"
    except Exception:
        pass

    # A DUPLICATE ELSEWHERE IN THE TREE IS WORSE THAN AN OLD FILE, because the age reads
    # reassuring while a fresher copy sits unused. data_agent/SecurityMaster.zip was exactly
    # this on 2026-08-17: 15 days newer than the root copy every consumer reads. It happened
    # to be functionally identical — same 622 FUTSTK rows, same lots, same expiries, differing
    # only in option strikes that nothing reads from the master — so nothing was wrong. Next
    # time it need not be, and "18d old" would still have looked fine.
    dupes = []
    for base, _dirs, files in os.walk(ROOT):
        if any(x in base for x in ("_env", "node_modules", ".git", "_trash", "scratch")):
            continue
        if "SecurityMaster.zip" in files:
            q = os.path.join(base, "SecurityMaster.zip")
            if os.path.abspath(q) != os.path.abspath(p):
                dupes.append(q)
    newer = [q for q in dupes if os.path.getmtime(q) > os.path.getmtime(p)]
    if newer:
        rel = os.path.relpath(newer[0], ROOT)
        nd = (dt.date.today()
              - dt.date.fromtimestamp(os.path.getmtime(newer[0]))).days
        return DUE, (msg + f" — but {rel} is NEWER ({nd}d). Consumers read the root copy, so "
                          f"the fresher one is unused. Promote it or delete it; do not keep both")
    if dupes:
        msg += f"; {len(dupes)} older duplicate(s) in the tree"

    if age is not None and age > 31:
        return DUE, msg
    return OK, msg


def _master_iso(x: str) -> str:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(str(x).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return str(x)[:10]


def check_universe():
    """nifty-50-stock-list.csv — the index constituents, refreshed monthly.

    CALENDAR ONLY, and deliberately so: there is no honest data test. Every download in this
    repo takes its symbol list FROM this file, so checking the data against it is circular —
    a name that left the index still has bars, because we kept downloading it. The same
    circularity that blocks O10. Catching a real constituent change needs an external source
    (the NSE index factsheet), which nothing here fetches.

    It matters more than it looks. A stale list means every index aggregate is computed on
    yesterday's membership: the departed name still carries weight and its replacement is
    absent, and nothing in the numbers looks wrong.
    """
    n = 0
    p = os.path.join(ROOT, "nifty-50-stock-list.csv")
    if not os.path.exists(p):
        return DUE, "MISSING"
    with open(p, newline="", encoding="utf-8") as fh:
        n = sum(1 for r in csv.DictReader(fh) if r.get("Symbol"))
    age = _age_days("nifty-50-stock-list.csv")
    msg = f"{age}d old (refresh monthly), {n} symbols"
    if n != 50:
        return DUE, msg + " — expected 50"
    if age is not None and age > 31:
        return DUE, msg
    return OK, msg


def check_futures_bars():
    """Daily single-stock futures capture. BLOCKING, because a missed day is UNRECOVERABLE.

    Breeze serves no history for settled contracts, so the futures panel can only grow
    forward, one live contract at a time. A day the job does not run is a hole that cannot
    be backfilled at any price — the same irreplaceability that makes expectation_snapshots
    blocking. Nothing published depends on this table today (O12 is closed), but the whole
    reason for keeping the capture is prospective accumulation, and a silent stop destroys
    exactly that.

    THE TRADING CALENDAR IS OBSERVED, NOT ASSUMED. universe.py has no holiday table, and
    inventing one here would be a second calendar to maintain and get wrong — the defect
    download_stock_futures.py was written to avoid. Instead the reference is price_bars at
    1d, a different job hitting a different endpoint, whose distinct dates ARE the sessions
    that happened. That makes the test non-circular and self-adjusting across weekends and
    holidays with no list to maintain.

    ONE SESSION OF LAG IS CORRECT, NOT LATE. Breeze does not publish the 1d bar for the
    current session until an overnight batch around 23:30-00:00 IST — verified by querying
    the endpoint directly on 2026-08-17 and getting bars terminating at 08-14, while 1m bars
    for 08-17 were already present. So the daily job belongs in the MORNING, collecting the
    previous session; expecting today's bar during the day would fire a false alarm every
    afternoon.
    """
    import sqlite3
    db = os.path.join(ROOT, "option_chains.db")
    if not os.path.exists(db):
        return WARN, "no database reachable from here"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        fut = con.execute("""select max(date(ts)) from fo_price_bars
                             where instrument_type='FUT' and timeframe='1d'""").fetchone()[0]
        ref = [r[0] for r in con.execute(
            """select distinct date(ts) from price_bars where timeframe='1d'
               order by 1 desc limit 4""")]
    except Exception as exc:
        return WARN, f"cannot read the database ({exc})"
    if not fut:
        return DUE, "no 1d futures bars at all"
    if not ref:
        return WARN, f"futures reach {fut}; no price_bars reference to compare against"

    # one session of lag is expected; two means a run was missed
    allowed = ref[1] if len(ref) > 1 else ref[0]
    n = 0
    try:
        con2 = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        n = con2.execute("""select count(distinct underlying) from fo_price_bars
                            where instrument_type='FUT' and timeframe='1d'
                              and date(ts)=?""", (fut,)).fetchone()[0]
    except Exception:
        pass
    msg = f"latest {fut} on {n} symbols; sessions observed {ref[0]}, {ref[1] if len(ref)>1 else '-'}"
    if fut < allowed:
        return DUE, msg + f" — expected {allowed} by now (one session of Breeze batch lag)"
    if n and n < 45:
        return DUE, msg + " — partial: fewer than 45 symbols on the latest day"
    return OK, msg


# Market inputs a daily brief reads. Tolerance is in SESSIONS behind the observed calendar,
# not days — so weekends and holidays never trip it. `max_lag` of 1 means "yesterday's close is
# fine, the day before is not".
#
# fo_price_bars is deliberately ABSENT: check_futures_bars() already owns it. Two checks on one
# table is the duplication this repo keeps paying for.
MARKET_INPUTS = [
    ("price_bars NIFTY",     "select max(date(ts)) from price_bars where symbol='NIFTY' and timeframe='1d'", 1),
    ("price_bars INDIAVIX",  "select max(date(ts)) from price_bars where symbol='INDIAVIX' and timeframe='1d'", 1),
    ("price_bars CRUDEOIL",  "select max(date(ts)) from price_bars where symbol='CRUDEOIL' and timeframe='1d'", 1),
    ("price_bars USDINR",    "select max(date(ts)) from price_bars where symbol='USDINR' and timeframe='1d'", 1),
    ("price_bars NASDAQ",    "select max(date(ts)) from price_bars where symbol='NASDAQ' and timeframe='1d'", 2),
    ("participant_oi",       "select max(flow_date) from participant_oi", 1),
    ("participant_flows",    "select max(flow_date) from participant_flows", 1),
    ("fii_dii_flows",        "select max(flow_date) from fii_dii_flows", 1),
    ("global_cues",          "select max(as_of) from global_cues", 1),
]


def _completed_sessions(con):
    """Distinct 1d dates STRICTLY BEFORE today.

    TODAY IS EXCLUDED, and that is the whole point. The sync writes a daily bar for the
    CURRENT session while it is still running: on 2026-08-18 at 12:12 IST price_bars already
    held a bar dated 2026-08-18 with RELIANCE volume of 4.07m against 13.38m the day before.
    It is indistinguishable from a completed bar by shape.

    Counting it as a session made every other input read one session staler than it was —
    USDINR at 08-16 reported "2 behind" when the newest COMPLETED session was 08-17. A
    reference calendar must contain only finished days, or the yardstick moves at 09:15.

    Excluding today is also the safe direction after the close: the checker becomes one
    session more lenient for a few hours, rather than raising false alarms every morning."""
    today = dt.date.today().isoformat()
    return [r[0] for r in con.execute(
        "select distinct date(ts) from price_bars where timeframe='1d' and date(ts) < ? "
        "order by 1 desc limit 30", (today,))]


def _sessions_behind(con, latest):
    """Lag in COMPLETED sessions. The calendar comes from price_bars at 1d — a different job
    on a different endpoint — so this needs no holiday table and self-adjusts."""
    if not latest:
        return None
    return len([x for x in _completed_sessions(con) if x > str(latest)[:10]])


def partial_session_bars(con):
    """Symbols carrying a 1d bar dated TODAY. Not an error — the sync does this deliberately
    so an intraday view exists — but a bar dated today is a SNAPSHOT, not a close, and any
    number taken from it must say so."""
    today = dt.date.today().isoformat()
    try:
        n = con.execute("select count(distinct symbol) from price_bars "
                        "where timeframe='1d' and date(ts)=?", (today,)).fetchone()[0]
        last1m = con.execute("select max(ts) from price_bars where timeframe='1m'").fetchone()[0]
        return n, last1m
    except Exception:
        return 0, None


def market_inputs_state():
    """(name, latest, sessions_behind, max_lag) for every input a brief reads."""
    import sqlite3
    db = os.path.join(ROOT, "option_chains.db")
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = []
    for name, sql, tol in MARKET_INPUTS:
        try:
            latest = con.execute(sql).fetchone()[0]
        except Exception:
            latest = None
        out.append((name, latest, _sessions_behind(con, latest), tol))
    try:
        out.append(("cue_betas", None,
                    None if con.execute("select count(*) from cue_betas").fetchone()[0] else -1, 0))
    except Exception:
        out.append(("cue_betas", None, -1, 0))
    return out


def check_market_inputs():
    """Every table a daily brief reads, with its lag. BLOCKING.

    WHY BLOCKING, when none of these feeds a published research number: on 2026-08-18 a brief
    quoted FII positioning from participant_oi as current. It was 2026-08-12 — three sessions
    behind — in the same answer that listed three OTHER staleness caveats. Stating some dates
    and not others is worse than stating none, because the ones you do state imply the rest
    were checked.

    The harm is not that data goes stale, which is normal. It is that a stale number gets
    quoted UNDATED. `--market` prints the as-of table so a brief can carry the dates instead
    of assuming them.
    """
    st = market_inputs_state()
    if st is None:
        return WARN, "no database reachable from here"
    late = [(n, l, b, t) for n, l, b, t in st if b is not None and b >= 0 and b > t]
    # a partial bar is worth saying out loud, not failing on
    import sqlite3 as _sq
    _db = os.path.join(ROOT, "option_chains.db")
    _partial = 0
    if os.path.exists(_db):
        _partial, _ = partial_session_bars(_sq.connect(f"file:{_db}?mode=ro", uri=True))
    empty = [n for n, l, b, t in st if b == -1]
    ok_n = len(st) - len(late) - len(empty)
    msg = f"{ok_n}/{len(st)} current"
    if _partial:
        msg += f"; {_partial} symbols hold TODAY's partial bar (snapshot, not a close)"
    if empty:
        msg += f"; EMPTY: {', '.join(empty)}"
    if late:
        worst = max(late, key=lambda x: x[2])
        msg += ("; behind: " + ", ".join(f"{n} {b}s" for n, _, b, _ in sorted(late, key=lambda x: -x[2])[:4]))
        return DUE, msg
    return (DUE if empty else OK), msg


# ---------------------------------------------------------------- registry
CHECKS = [
    {"name": "attributable_panel.json", "cadence": "quarterly (results season)",
     "fn": check_panel_vs_scrape, "sev": "blocking",
     "fix": "python3 data_agent/fundamentals/attributable_panel.py",
     "why": "the quarterly series every growth and exit-rate number is measured on"},
    {"name": "screener_page_tables.csv", "cadence": "quarterly (results season)",
     "fn": check_scrape_quarter, "sev": "blocking",
     "fix": "python3 data_agent/fundamentals/screener_tables.py --basis auto   (~10 min)",
     "why": "the panel's only input; behind here means behind everywhere"},
    {"name": "delivery_history (annual)", "cadence": "yearly (May-Jun)",
     "fn": check_export_annual, "sev": "blocking",
     "fix": ("python3 data_agent/fundamentals/download_screener.py --force && "
             "python3 data_agent/fundamentals/delivery_history.py\n"
             "  # NOT AN ALIAS PROBLEM — an earlier version of this note said it was, wrongly.\n"
             "  # The workbooks exist and parse; delivery_history.build() returns None for them\n"
             "  # because of _MIN_YEARS = 6 (line ~224): TATAMOTORS has 2 annual P&L columns,\n"
             "  # NESTLEIND 4, JIOFIN 4. That gate is deliberate — 'fewer periods than this and\n"
             "  # consistency is an anecdote'. Nothing to fix unless the gate should change."),
     "why": "the quality screen's growth gates read this series, not the quarters"},
    {"name": "delivery_history (quarters)", "cadence": "quarterly (results season)",
     "fn": check_export_quarters, "sev": "advisory",
     "fix": ("python3 data_agent/fundamentals/download_screener.py --force && "
             "python3 data_agent/fundamentals/delivery_history.py"),
     "why": "panel routes around this; still the source of share counts"},
    {"name": "expectation_snapshots.json", "cadence": "weekly (cron, Mon 09:00)",
     "fn": check_snapshots, "sev": "blocking",
     "fix": "data_agent/fundamentals/run_expectation_snapshot.sh",
     "why": "the only forward-looking gate, and it gates the revisions channel"},
    {"name": "market inputs (brief)", "cadence": "daily",
     "fn": check_market_inputs, "sev": "blocking",
     "fix": ("python3 data_agent/sync_all.py --breeze-token <TOKEN>\n"
             "  # then: python3 data_agent/freshness.py --market   to see every as-of date\n"
             "  # cue_betas is EMPTY — no fitted US->India beta exists, so no brief may quote one"),
     "why": "a brief that quotes one stale input undated is worse than one that quotes none"},
    {"name": "fo_price_bars (FUT 1d)", "cadence": "daily, mornings",
     "fn": check_futures_bars, "sev": "blocking",
     "fix": ("python3 data_agent/fetching/download_stock_futures.py --live\n"
             "  # MORNINGS, not after close: Breeze publishes the 1d bar for a session in an\n"
             "  # overnight batch (~23:30-00:00 IST), so an afternoon run cannot see today"),
     "why": "settled contracts have no history — a missed day cannot be backfilled, ever"},
    {"name": "SecurityMaster.zip", "cadence": "monthly",
     "fn": check_scrip_master, "sev": "blocking",
     "fix": ("re-download the Breeze security master to the repo root, then\n"
             "  python3 data_agent/fetching/lot_sizes.py   # must report 0 DISAGREE"),
     "why": "contract_size is stamped from this AT CAPTURE — stale means wrong stored rows"},
    {"name": "nifty-50-stock-list.csv", "cadence": "monthly",
     "fn": check_universe, "sev": "blocking",
     "fix": "refresh from the NSE index factsheet (weights change with it)",
     "why": "defines the universe for every download and every index aggregate"},
    {"name": "fii_holdings.json", "cadence": "quarterly (filings)",
     "fn": check_fii_quarter, "sev": "advisory",
     "fix": "python3 data_agent/fundamentals/fii_holding_backfill.py",
     "why": "a flag, never a gate — but bad period labels misdate the change_pp"},
    {"name": "quality_growth.json", "cadence": "weekly, after the snapshot",
     "fn": check_screen, "sev": "blocking",
     "fix": "python3 data_agent/fundamentals/quality_growth.py   (~4 min)",
     "why": "the screen itself — stale means last week's names on this week's prices"},
    # calendar-only fallbacks: no data test exists for these
    {"name": "pe_history.json", "cadence": "at announcements", "max_age": 30,
     "sev": "advisory",
     "fix": "python3 data_agent/fundamentals/pe_history_backfill.py",
     "why": "point-in-time backtest only; keeps the walk-forward honest"},
    {"name": "nifty50_drivers.json", "cadence": "manual", "max_age": 45,
     "sev": "advisory",
     "fix": "edit by hand, or a patcher like add_it_deflation.py",
     "why": "curated commentary; does NOT gate selection"},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="only lines needing action")
    ap.add_argument("--all", action="store_true",
                    help="let advisory items affect the exit code too")
    ap.add_argument("--market", action="store_true",
                    help="print the as-of date of every market input, for pasting into a brief")
    a = ap.parse_args()

    if a.market:
        st = market_inputs_state()
        if st is None:
            print("no database reachable")
            raise SystemExit(2)
        print(f"MARKET INPUTS — as-of dates, {dt.date.today()}")
        print("Quote these WITH their dates. A number without one implies a currency it may "
              "not have.")
        import sqlite3 as _sq
        _db = os.path.join(ROOT, "option_chains.db")
        if os.path.exists(_db):
            _c = _sq.connect(f"file:{_db}?mode=ro", uri=True)
            _n, _last = partial_session_bars(_c)
            if _n:
                print(f"\n  *** {_n} symbols carry a 1d bar dated TODAY. Latest 1m bar "
                      f"{str(_last)[:16]}.")
                print("      That bar is an INTRADAY SNAPSHOT, not a close. Lag below is")
                print("      measured against completed sessions only, so today is excluded.")
        print()
        print(f"  {'input':26}{'as of':13}{'sessions behind':>16}")
        bad = 0
        for n, l, b, t in st:
            if b == -1:
                print(f"  {n:26}{'EMPTY':13}{'--':>16}   nothing fitted"); bad += 1; continue
            mark = "" if (b is not None and b <= t) else "   <-- STALE, say so or refresh"
            if mark:
                bad += 1
            print(f"  {n:26}{str(l)[:12]:13}{b if b is not None else -1:>16}{mark}")
        raise SystemExit(1 if bad else 0)

    print(f"FRESHNESS  {dt.date.today()}   data-driven where a data test exists, "
          f"calendar age otherwise\n")
    todo = []
    for c in CHECKS:
        if c.get("fn"):
            state, detail = c["fn"]()
            kind = "vs data"
        else:
            age = _age_days(c["name"])
            if age is None:
                state, detail = DUE, "MISSING"
            else:
                state = OK if age <= c["max_age"] else DUE
                detail = f"{age}d old (limit {c['max_age']}d)"
            kind = "calendar"
        if state != OK:
            todo.append((c, state))
        if a.quiet and state == OK:
            continue
        print(f"  [{state:4s}] {c['name']:28s} {c['cadence']:26s} {detail}")
        if not a.quiet:
            print(f"           {kind} · {c['sev']} · {c['why']}")

    blocking = [(c, s) for c, s in todo if c["sev"] == "blocking" and s == DUE]
    if not todo:
        print("\nNothing stale.")
        return

    print(f"\n{len(todo)} item(s) need attention — {len(blocking)} blocking:\n")
    for c, s in todo:
        print(f"  # [{s}/{c['sev']}] {c['name']}: {c['why']}")
        print(f"  {c['fix']}\n")
    print("Order matters: the scrape feeds the panel, the panel and the snapshot feed the")
    print("screen. Refresh inputs before re-running quality_growth.py.")
    if blocking or (a.all and todo):
        sys.exit(1)
    print("\nNothing BLOCKING — the advisory items degrade a channel that something")
    print("downstream routes around. Fix them, but not urgently.")


if __name__ == "__main__":
    main()
