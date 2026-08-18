#!/usr/bin/env python3
"""sync_all.py — THE sync. One file, one command, nothing else to remember.

    python data_agent/sync_all.py                 # everything that needs no token
    python data_agent/sync_all.py --breeze-token XYZ   # ... plus the Breeze steps
    python data_agent/sync_all.py --dry-run       # show the plan, run nothing

WHY THIS FILE EXISTS
--------------------
There were two syncs. `/api/sync-all-data` ran Breeze 1m, futures, commodities,
F&O, the Postgres macro pull and the option-chain capture. `/api/data-agent/run`
ran the orchestrator plus `sync_all_auxiliary`, which is where all seven Yahoo
daily scripts live. Neither was complete, both were partly the other, and which
one you happened to run decided which half of the database got refreshed.

They were not even independent: sync-all-data imported `_do_run` from the other
endpoint at step 4.5. It was one sync that had absorbed half of its replacement
and stopped.

THE BUG THAT MATTERED
---------------------
`/api/sync-all-data` validated the Breeze session token first and raised a 400 if
it had expired — before running anything. But the `_yf` scripts need no token at
all; they fetch from Yahoo. So a dead Breeze login blocked the refresh of every
equity and sector daily bar, which is most of what the analysis in this repo
actually reads.

That is fixed here structurally rather than by remembering not to do it. Each step
DECLARES the credential it needs. Steps needing nothing run FIRST and cannot be
gated by a credential they never use. A missing credential SKIPS its steps with a
printed reason; it does not abort the run. Only a step that actually fails is a
failure.

The Kite/Zerodha validation is gone. It set a `skip_commodities` flag that the old
code then never read — commodities go through Upstox regardless — so its only real
effect was the ability to 400 the entire sync over a credential no step used.

CREDENTIALS
-----------
Resolved in order: command line, environment, then today's cached session file
that the UI writes (breezesession/session_YYYY-MM-DD.json). The cache is what lets
this run from cron without the frontend.

Breeze api_key/api_secret come from BREEZE_API_KEY / BREEZE_API_SECRET only. They
are deliberately NOT defaulted here: they are currently hardcoded as literals in
backend/main.py, and copying them into a second file would double the number of
places a committed secret has to be rotated out of.

VERIFICATION IS PART OF THE RUN
-------------------------------
The old endpoint ended with a hand-rolled audit: six hardcoded symbols, counted
against a hardcoded Google Drive path, wrapped in a try/except that appended the
failure to a log array and returned success anyway. The real audit already exists.
It runs here, and its findings can fail the run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# THE TWO VENVS ARE NOT INTERCHANGEABLE.
#
# The first version of this file resolved ONE interpreter for everything and put
# data_agent/breeze_env first. That silently broke the three Postgres steps, because:
#
#   data_agent/breeze_env      breeze_connect, yfinance      — no psycopg, no dotenv
#   breeze_env                 breeze_connect, yfinance,
#                              psycopg, psycopg2, dotenv
#
# backend/main.py had this right and I did not read it carefully enough: it invoked
# ./data_agent/breeze_env for the fetching steps and breeze_env for
# the macro ones. That split is preserved here per step rather than flattened.
#
# breeze_env is currently a superset and could run everything, but
# the two carry different yfinance builds, and the daily bars are the thing this
# repo is most sensitive about. Each step keeps the interpreter it is known to work
# under.
VENVS = {
    # fetching, quality: needs yfinance + breeze_connect
    "data": ["data_agent/breeze_env/bin/python",
             "breeze_env/bin/python"],
    # macro: needs psycopg + dotenv, which only the scratch venv has
    "macro": ["breeze_env/bin/python",
              "data_agent/breeze_env/bin/python"],
}

# Zero, as it should be. The two findings this used to tolerate were GOLD and
# COPPER daily staleness, caused by instrument keys pointing at contracts that had
# stopped printing. Per-contract storage fixed the cause rather than the symptom:
# the rolling names are now derived from live contracts and cannot go stale while
# the contracts update.
#
# Do not raise this to silence findings. A non-zero baseline authorises a number of
# unexplained problems, and the number only ever grows.
AUDIT_BASELINE = 0

# HERE on sys.path so credentials/expiries resolve under any interpreter.
sys.path.insert(0, HERE)
from credentials import load_dotenv  # noqa: E402


class Step:
    """One unit of sync. `needs` is the credential name, or None for no credential.

    `argv` is a callable so a step's arguments can depend on resolved credentials
    without any step having to look them up itself.
    """

    def __init__(self, sid, title, script, argv=None, needs=None, phase="fetch",
                 enabled=True, note="", pause_after=0, repeat=None, venv="data",
                 runner=None):
        # `runner` overrides the interpreter for this step. Default None means "the venv's
        # python", which is every step but the backup — pg_backup.sh is bash.
        self.runner = runner
        self.venv = venv
        self.sid = sid
        self.title = title
        self.script = script            # repo-relative, or None for a python -c step
        self.argv = argv or (lambda c: [])
        self.needs = needs
        self.phase = phase
        self.enabled = enabled
        self.note = note
        # The old endpoint slept 3s after the Breeze constituent and F&O steps.
        # Undocumented, but Breeze rate-limits and this was presumably why —
        # preserved rather than quietly dropped.
        self.pause_after = pause_after
        # A step that runs once per item (option-chain expiries, FRED series).
        self.repeat = repeat


# Expiry questions go to data_agent/expiries.py, which asks Breeze WHAT is listed
# and then defers to fetching/universe.py for WHICH of those to pull. Neither the
# ">= today" filter nor the 2-day rollover is reimplemented here — universe.py has
# owned both since it was written, and a third copy is a third thing to disagree.
from expiries import active as active_expiries  # noqa: E402


def chain_expiries(args, python, creds):
    """Which expiries the chain step captures.

    --expiry is optional. Given none, universe.active_option_expiries picks the
    current expiry and adds the next once we are inside ROLL_AHEAD_DAYS — the same
    rule the orchestrator already follows, rather than a second opinion about it.
    """
    if args.expiry:
        return [args.expiry]
    exps, err = active_expiries(args.symbol, "options", python=python,
                                session_token=creds.get("breeze"),
                                api_key=creds.get("breeze_key"),
                                api_secret=creds.get("breeze_secret"))
    if err:
        print(f"    expiry lookup failed: {err}")
        return []
    print(f"    expiries to capture: {', '.join(e[:10] for e in exps) or 'none'}")
    return exps


def _fo_snippet(creds):
    """The F&O contract step, as backend/main.py runs it.

    main.py calls `_do_run` in-process from the FastAPI worker. From a standalone
    script the equivalent is a subprocess that imports the same function, so the
    behaviour stays identical rather than becoming a second implementation.
    """
    return [
        "-c",
        "import json;"
        "from backend.data_agent_routes import _do_run, RunReq;"
        "r=_do_run(RunReq(broker='breeze',token=%r,api_key=%r,api_secret=%r,"
        "mode='fo',timeframe='1m'));"
        "print('F&O:', r.get('saved_total',0), 'bars across', r.get('targets',0), 'targets')"
        % (creds.get("breeze"), creds.get("breeze_key"), creds.get("breeze_secret")),
    ]


def build_steps(args):
    """The whole pipeline, in run order.

    ORDERING RULE: no-credential steps first. This is the fix for the gating bug,
    and it only holds as long as nothing needing a token is inserted above them.
    """
    fo = os.path.join("data_agent", "fetching")
    mac = os.path.join("data_agent", "macro")
    qua = os.path.join("data_agent", "quality")
    since = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    steps = [
        # ---- Phase 1: daily bars. No credential. Runs even if every broker is down.
        Step("sectors", "Sector indices (NIFTY*, BANKNIFTY)",
             f"{fo}/sync_sectors_yf.py"),
        Step("nifty50", "Nifty 50 constituents + NIFTY",
             f"{fo}/sync_nifty50_bars_yf.py"),
        Step("banks", "Bank stocks", f"{fo}/sync_bank_bars_yf.py"),
        Step("it", "IT stocks", f"{fo}/sync_it_bars_yf.py"),
        Step("finnifty", "FinNifty constituents", f"{fo}/sync_finnifty_bars_yf.py"),
        # Reads its symbol list from ai_infra_theme.json rather than a hard-coded
        # copy, so adding a name to the theme view is enough to start collecting its
        # bars. Overlaps the sets above for BHARTIARTL / LT / POWERGRID; sync_symbols
        # is an upsert, so the second write is a no-op rather than a conflict.
        Step("ai-infra", "AI-infrastructure theme names (daily bars)",
             f"{fo}/sync_ai_infra_bars_yf.py"),
        Step("crude", "CRUDEOIL — WTI in USD", f"{fo}/sync_crudeoil_yf.py"),
        # Long clean metal history. The MCX series cannot be rebuilt backwards, so
        # these carry the depth (2018 onward) while GOLD/SILVER/COPPER carry the
        # Indian contract. Bare name = MCX INR, _USD = international.
        Step("metals-usd", "GOLD_USD / SILVER_USD / COPPER_USD (Yahoo)",
             f"{fo}/sync_metals_usd_yf.py"),

        # Upstox handles its own auth via upstox_auth.py, so there is no token to
        # declare here. If that auth has expired the step fails and says so.
        Step("commodities", "MCX commodities, USDINR, GIFTNIFTY (Upstox)",
             f"{fo}/sync_commodities.py"),

        # Every symbol this writes — NIFTY, NIFTYIT, INDIAVIX, BANKNIFTY, USDINR —
        # is already written by a step above. It is listed rather than deleted so
        # the plan shows the decision instead of hiding it.
        Step("india-indices", "Indian index dailies", f"{mac}/download_india_indices.py",
             enabled=False,
             note="redundant: all 5 symbols are owned by sectors/nifty50/commodities"),

        # ---- Phase 2: intraday and contracts. Needs a Breeze session.
        Step("breeze-1m", "Nifty 50 constituents + indices, 1m (Breeze)",
             f"{fo}/sync_nifty50_to_now.py",
             argv=lambda c: [c["breeze"]], needs="breeze", pause_after=3),
        Step("futures", "NIFTY_FUT_1 / NIFTY_FUT_2, 1m (Breeze)",
             f"{fo}/download_nifty_futures.py",
             argv=lambda c: [c["breeze"]], needs="breeze"),
        Step("fo", "Futures and option contract bars (Breeze)",
             None, argv=_fo_snippet, needs="breeze+keys", pause_after=3),

        # Single-stock futures at 1d. Placed HERE, last among the Breeze steps, for two
        # reasons.
        #
        # SEQUENCING: every Breeze call in this plan is now contiguous, so the broker sees
        # one unbroken run of traffic from one client rather than Breeze and Upstox work
        # interleaved. The run loop is already strictly sequential — a blocking
        # subprocess.run per step, no ThreadPoolExecutor anywhere in the fetch path — and
        # the script itself walks 50 symbols one at a time with a 0.8s pause between calls.
        # pause_after=3 matches the other Breeze steps.
        #
        # TIMING: Breeze publishes the 1d bar for a session in an overnight batch around
        # 23:30-00:00 IST, verified by querying the endpoint directly on 2026-08-17 and
        # getting bars terminating at 08-14 while 1m bars for 08-17 were already stored. So
        # this collects the PREVIOUS session and belongs in a morning run. An afternoon sync
        # will fetch nothing new and report success, which is why freshness.py allows exactly
        # one session of lag before calling it overdue.
        #
        # A missed day cannot be recovered: Breeze serves no history for settled contracts.
        Step("stock-futures", "Single-stock futures, 1d (Breeze)",
             f"{fo}/download_stock_futures.py",
             argv=lambda c: ["--live", "--session-token", c["breeze"]],
             needs="breeze+keys", pause_after=3),

        # The option-chain capture takes an expiry and a date window, so unlike
        # everything else it is not a plain "refresh to now". It stays opt-in.
        # `repeat` carries the auto-rollover: within 2 days of the given expiry the
        # next one is captured as well.
        Step("chains", "Option chain captures",
             os.path.join("scratch_scripts", "fetch_historical_option_chain.py"),
             argv=lambda c: [c["breeze"], "@item", args.symbol, args.interval,
                             args.start_date, args.end_date],
             needs="breeze+keys",
             repeat=lambda p, c: chain_expiries(args, p, c)),

        # ---- Phase 3: macro, to Postgres. These need psycopg and dotenv, which
        # only breeze_env has — hence venv="macro".
        Step("macro", "US10Y / NASDAQ / CRUDE from FRED", f"{mac}/us10y.py",
             argv=lambda c: ["--series", "@item", "--since", since], phase="macro",
             venv="macro", repeat=lambda p, c: ["US10Y", "NASDAQ", "CRUDE"]),
        Step("india-rates", "India rates (IN10Y_INDEX)", f"{mac}/ingest_india_rates.py",
             phase="macro", venv="macro"),
        Step("us-stocks", "US tech + ADRs (ACN, CTSH, CRM, INFY_ADR)",
             f"{mac}/download_us_stocks.py",
             argv=lambda c: ["--since", since], phase="macro", venv="macro"),
        Step("flows", "FII / DII daily cash flows", f"{mac}/download_fii_dii.py",
             phase="macro", venv="macro"),

        # ---- Two tables a daily brief reads that had NO OWNER in this plan.
        #
        # Found on 2026-08-18: after a clean full sync, participant_oi was still 4 completed
        # sessions behind and the US indices 6, because nothing here ran their downloaders.
        # `flows` covers fii_dii_flows (cash) and was mistaken for covering the participant
        # tables too — different script, different table, adjacent name. A brief quoted FII
        # index-futures positioning from 12-Aug as if it were current on 18-Aug.
        #
        # sync_coverage.py had ALREADY been reporting the US symbols as orphaned on every run.
        # The finding was in the audit output and nobody read it, which is its own lesson: an
        # audit that reports into a log nobody opens is not a control either.
        # TWO SERIES, TWO SCRIPTS, and the first version of this step covered only one.
        # download_nse_participants.py writes participant_flows (traded VOLUME) and nothing
        # else; participant_oi (POSITIONS) comes from backfill_nse_participants --series oi.
        # The register already distinguishes them — "participant_flows is traded VOLUME,
        # participant_oi is POSITION" — and the step title claiming both was wrong. OI is the
        # FII positioning series every hedge-ratio question rests on, so a step that quietly
        # refreshed only volume left the more important half four sessions stale.
        Step("participants", "NSE F&O participant traded volumes",
             f"{mac}/download_nse_participants.py", phase="macro", venv="macro"),
        # A backfill run daily, so bounded like us-indices. Its --from defaults to 2018-01-01
        # and it sleeps 2.5s between requests by design ("do not lower this to be rude"), so
        # an unbounded window would be both slow and impolite.
        Step("participants-oi", "NSE F&O participant OPEN INTEREST (positions)",
             f"{mac}/backfill_nse_participants.py",
             argv=lambda c: ["--series", "oi", "--from", since],
             phase="macro", venv="macro"),

        # A BACKFILL script run daily, so it MUST be bounded. Its --from defaults to
        # 2018-01-01, which would re-download eight years every morning. --since keeps it to
        # the recent window; --replace is deliberately NOT passed, so existing rows stand.
        Step("us-indices", "US indices (DJIA, SP500, NASDAQ, NDX100, SOX, VIX_US)",
             f"{mac}/backfill_us_indices.py",
             argv=lambda c: ["--from", since], phase="macro", venv="macro"),

        # ---- Phase 3.5: make the second copy of each store current.
        #
        # THE TWO STORES FLOW IN OPPOSITE DIRECTIONS, so these are not two instances of one
        # operation — see the direction table in CLAUDE.md:
        #   SQLite market data   Drive is authoritative   -> refresh the local MIRROR
        #   PostgreSQL           localhost is authoritative -> dump to DRIVE
        # Getting either backwards destroys data, and each wrong direction is the correct
        # operation for the other store.
        #
        # Placed AFTER every write and BEFORE verify: the mirror should be current when
        # anything reading it runs, and a backup should capture what was actually ingested.
        # Deliberately before verify rather than after — an audit finding is not a reason to
        # have no backup of the day's data.
        Step("mirror", "Refresh the repo-local SQLite mirror from Drive",
             f"{qua}/refresh_mirror.py", phase="macro"),
        Step("pg-backup", "Dump Postgres (macro + fundamentals) to Drive",
             os.path.join("data_agent", "pg_backup.sh"),
             runner=["/bin/bash"], phase="macro"),

        # ---- Views. Derived pages built from what the phases above just ingested.
        #
        # AFTER `mirror`, and that ordering is load-bearing rather than tidy. gold_cycles is
        # a READER, so it takes resolve_db_path() — the repo-local mirror — and running it
        # before the refresh would render YESTERDAY's data under today's date. That is the
        # quietest kind of wrong: the page looks perfect and is a day stale. Any view added
        # here inherits the same rule.
        #
        # It is also a live C39 tripwire. The page multiplies by USDINR, so a repeat of the
        # vendor scale flip moves every INR level tenfold and still draws a smooth chart —
        # so the generator refuses on any USDINR bar outside 20-200 and exits non-zero. A
        # sync that fails here is telling you the FX series is broken, not the view.
        Step("gold-view", "Gold view — INR landed-cost cycle page",
             os.path.join("backend", "quant", "gold_cycles.py"), phase="macro"),

        # ---- Phase 4: verification. The reason a bad sync now fails loudly.
        Step("audit", "Daily bar integrity audit", f"{qua}/daily_bar_audit.py",
             phase="verify"),
        Step("coverage", "Symbol ownership and orphan check", f"{qua}/sync_coverage.py",
             phase="verify"),
        # CLAUDE.md forbids hardcoding a database path, and on 2026-08-17 that rule was
        # broken by two new WRITERS two days after it was written — one of which put a month
        # of futures into the read-only mirror and printed success (C37). The rule was
        # documented and mandatory and still broken, because nothing failed when it was.
        # Now something does.
        Step("db-paths", "No writer hardcodes a database path", f"{qua}/db_path_audit.py",
             phase="verify"),
    ]
    return steps


def interpreter(kind="data"):
    for rel in VENVS[kind]:
        cand = os.path.join(REPO_ROOT, rel)
        # A venv's python is a symlink; exists() follows it, so a venv whose base
        # interpreter was removed fails here rather than at run time.
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand, True
    return sys.executable, False


# .env reading lives in credentials.py, so this file and the fetching scripts read
# secrets the same way. sys.path is set above for the same reason bar_store is
# importable from here.


def _cached_token(folder, key):
    """Today's session file, written by the UI when it validates a token."""
    path = os.path.join(REPO_ROOT, folder,
                        f"session_{datetime.now().strftime('%Y-%m-%d')}.json")
    try:
        with open(path) as f:
            tok = json.load(f).get(key)
        return tok if tok and len(tok) > 5 and tok != "undefined" else None
    except (OSError, ValueError):
        return None


def resolve_credentials(args):
    breeze = (args.breeze_token or os.environ.get("BREEZE_SESSION_TOKEN")
              or _cached_token("breezesession", "session_token"))
    return {
        "breeze": breeze,
        "breeze_key": os.environ.get("BREEZE_API_KEY"),
        "breeze_secret": os.environ.get("BREEZE_API_SECRET"),
    }


def missing_reason(step, creds, args):
    """Why this step cannot run, or None if it can."""
    if not step.enabled:
        return f"not run — {step.note}"
    if step.needs == "breeze" and not creds["breeze"]:
        return "no Breeze session token (--breeze-token, $BREEZE_SESSION_TOKEN, or today's cached session)"
    if step.needs == "breeze+keys":
        if not creds["breeze"]:
            return "no Breeze session token"
        if not (creds["breeze_key"] and creds["breeze_secret"]):
            return "no $BREEZE_API_KEY / $BREEZE_API_SECRET in the environment"
    if step.script and not os.path.exists(os.path.join(REPO_ROOT, step.script)):
        return f"script not found: {step.script}"
    return None


def run_step(step, creds, pythons, args):
    """Run one step, streaming its output. Returns (status, findings_or_None)."""
    python = pythons[step.venv]
    if step.script is None:
        cmd = [python] + step.argv(creds)
    else:
        argv = step.argv(creds)
        # A repeating step runs once per item, with @item substituted. us10y.py
        # takes one --series per invocation; the chain capture takes one expiry.
        if step.repeat:
            items = step.repeat(python, creds)
            if not items:
                return "SKIP", None
            worst = "OK"
            for item in items:
                print(f"    -> {item}")
                a = [item if t == "@item" else t for t in argv]
                r = subprocess.run([python, os.path.join(REPO_ROOT, step.script)] + a,
                                   cwd=REPO_ROOT)
                if r.returncode != 0:
                    worst = "FAIL"
            return worst, None
        runner = step.runner or [python]
        cmd = runner + [os.path.join(REPO_ROOT, step.script)] + argv

    if step.phase == "verify":
        # Captured, not streamed, so the finding count can be read back and still
        # shown to the user in full.
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.stderr:
            sys.stderr.write(r.stderr)
        m = re.search(r"^(\d+) findings\.", r.stdout, re.M)
        if r.returncode == 0:
            return "OK", None
        # A verify step exits non-zero for two very different reasons: it found
        # integrity problems, or it crashed. Only the first is a report; the second
        # is a step that did not run and must not be counted as verification.
        # Conflating them is how the old endpoint returned success while its audit
        # was dying in a try/except.
        if m:
            return "FINDINGS", int(m.group(1))
        return "FAIL", None

    r = subprocess.run(cmd, cwd=REPO_ROOT)
    return ("OK" if r.returncode == 0 else "FAIL"), None


def main():
    ap = argparse.ArgumentParser(
        description="The one sync. Runs every data step this repo has, in order.")
    ap.add_argument("--breeze-token", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan, including what would be skipped and why")
    ap.add_argument("--only", default=None, help="comma-separated step ids")
    ap.add_argument("--skip", default=None, help="comma-separated step ids")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the audit phase (not recommended)")
    ap.add_argument("--audit-baseline", type=int, default=AUDIT_BASELINE,
                    help="fail the run if the audit reports MORE findings than this")
    ap.add_argument("--strict", action="store_true",
                    help="treat a skipped step as a failure")
    # Option-chain capture arguments — only used by the `chains` step.
    ap.add_argument("--expiry", default=None)
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--interval", default="1minute")
    ap.add_argument("--start-date", default="")
    ap.add_argument("--end-date", default="")
    args = ap.parse_args()

    steps = build_steps(args)
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        steps = [s for s in steps if s.sid in want]
    if args.skip:
        drop = {s.strip() for s in args.skip.split(",")}
        steps = [s for s in steps if s.sid not in drop]
    if args.no_verify:
        steps = [s for s in steps if s.phase != "verify"]

    dotenv_keys = load_dotenv()
    pythons, venv_ok = {}, {}
    for kind in ("data", "macro"):
        pythons[kind], venv_ok[kind] = interpreter(kind)
    creds = resolve_credentials(args)

    print("=" * 72)
    print(f"sync_all — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"repo:        {REPO_ROOT}")
    print(f"python/data: {pythons['data']}")
    print(f"python/macro:{pythons['macro']}")
    for kind in ("data", "macro"):
        if not venv_ok[kind]:
            print(f"WARNING: no venv found for '{kind}' steps — using the launching "
                  f"interpreter.")
            print("         data steps need yfinance; macro steps need psycopg.")
    if dotenv_keys:
        print(f".env:        loaded {len(dotenv_keys)} keys ({', '.join(sorted(dotenv_keys))})")
    print(f"breeze:      {'present' if creds['breeze'] else 'ABSENT — Breeze steps will skip'}")
    if creds["breeze"] and not (creds["breeze_key"] and creds["breeze_secret"]):
        print("             (no BREEZE_API_KEY / BREEZE_API_SECRET — the F&O step will skip;"
              "\n              add them to .env, they are NOT there today)")
    print("=" * 72)

    results, findings_total = [], 0
    for step in steps:
        reason = missing_reason(step, creds, args)
        if reason:
            print(f"\n--- [{step.sid}] {step.title}\n    SKIP: {reason}")
            results.append((step, "SKIP", reason))
            continue
        if args.dry_run:
            venv_tag = os.path.relpath(pythons[step.venv], REPO_ROOT)
            print(f"\n--- [{step.sid}] {step.title}\n    would run: "
                  f"{step.script or 'python -c (F&O)'}\n    using:     {venv_tag}")
            results.append((step, "PLAN", ""))
            continue
        print(f"\n=== [{step.sid}] {step.title} " + "=" * max(0, 40 - len(step.title)))
        status, n = run_step(step, creds, pythons, args)
        if n:
            findings_total += n
        results.append((step, status, ""))
        if step.pause_after and status != "SKIP":
            time.sleep(step.pause_after)

    if args.dry_run:
        print("\nDry run — nothing was executed.")
        return 0

    print("\n" + "=" * 72)
    for step, status, reason in results:
        mark = {"OK": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip ",
                "FINDINGS": "AUDIT!"}.get(status, "  ?   ")
        print(f"[{mark}] {step.sid:14} {step.title}" + (f"  ({reason})" if reason else ""))
    print("=" * 72)

    failed = [s.sid for s, st, _ in results if st == "FAIL"]
    skipped = [s.sid for s, st, _ in results if st == "SKIP"]
    bad = bool(failed)
    if failed:
        print(f"FAILED: {', '.join(failed)}")
    if findings_total > args.audit_baseline:
        print(f"AUDIT: {findings_total} findings, above the accepted baseline of "
              f"{args.audit_baseline} — this sync introduced integrity problems.")
        bad = True
    elif findings_total:
        print(f"audit: {findings_total} findings, at or below the known baseline "
              f"({args.audit_baseline}) — no new damage.")
    if skipped and args.strict:
        print(f"STRICT: skipped {', '.join(skipped)}")
        bad = True
    if not bad:
        print("All steps completed and verification passed.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
