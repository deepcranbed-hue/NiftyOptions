#!/usr/bin/env python3
"""sync_ai_infra_bars_yf — daily bars for the AI-infrastructure theme names.

Thin wrapper over daily_bars.sync_symbols(), same as the Nifty 50 / IT / bank
syncs. All fetch, write, adjustment and verify logic lives there so this file
cannot drift from them.

WHY THE SYMBOL LIST IS NOT HARD-CODED HERE
------------------------------------------
It is read from ai_infra_theme.json, which is the curated dataset the view already
serves. A hard-coded copy would silently rot the moment a name is added to or
dropped from the theme, and the failure mode is invisible: the view would render a
company with no price history and no error anywhere. Reading the same file the UI
reads means the two cannot disagree.

SPLITS — READ THIS BEFORE TRUSTING A CHART
------------------------------------------
Incremental runs only rewrite the tail. When a vendor re-adjusts an entire history
for a split or bonus, an incremental run leaves a scale break at the join. Two names
in this set are affected right now:

    E2E         1:10 split, ex-date 2026-06-05
    TDPOWERSYS  subdivision approved at the AGM 2026-08-12, not yet effective

After any such event, re-run that name with --full. There is no way to detect this
automatically from the bars alone without also flagging every genuine gap-down.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.append(REPO_ROOT)

from daily_bars import sync_symbols

THEME_PATH = os.path.join(REPO_ROOT, "ai_infra_theme.json")


def theme_symbols(path=None):
    """-> (symbols, error). Never raises: a missing theme file skips the step."""
    try:
        with open(path or THEME_PATH) as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return [], f"{path or THEME_PATH}: {e}"
    syms = [c["symbol"].upper() for c in doc.get("companies", []) if c.get("symbol")]
    return sorted(set(syms)), None


def main():
    from bar_store import DB_PATH
    db = os.environ.get("OPTION_CHAINS_DB", DB_PATH)
    full = "--full" in sys.argv

    only = None
    if "--only" in sys.argv:
        only = {s.strip().upper() for s in sys.argv[sys.argv.index("--only") + 1].split(",")}

    syms, err = theme_symbols()
    if err:
        print(f"AI-infra theme unreadable, nothing to sync — {err}")
        return 0
    if only:
        missing = only - set(syms)
        if missing:
            print(f"not in the theme: {', '.join(sorted(missing))}")
        syms = [s for s in syms if s in only]

    print(f"database: {db}")
    print(f"AI-infra theme names ({len(syms)}), {'FULL' if full else 'incremental'}:")
    res = sync_symbols(syms, db, full=full)

    dead = [s for s, (n, t) in res.items() if t is None]
    print(f"\nwrote {sum(n for n, _ in res.values())} bars across {len(res)} symbols")
    if dead:
        print(f"NO DATA for {', '.join(dead)} — check the ticker in daily_bars.TICKER_ALTS.")
        # A dead ticker is a real defect (the view will render an empty chart), but it
        # must not abort a pipeline whose other 37 names succeeded.
    return 0


if __name__ == "__main__":
    sys.exit(main())
