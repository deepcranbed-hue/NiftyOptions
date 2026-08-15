#!/usr/bin/env python3
"""ai_infra_call_log — append-only record of every call the AI-infra view has made.

WHY THIS EXISTS
---------------
ai_infra_theme.json holds only the CURRENT call. When a stance is revised the
previous one is overwritten, and with it the only record of what we believed and
when. That makes the view unfalsifiable: it can always claim to have been right,
because yesterday's opinion is gone.

This file is the correction. Every (symbol, kind, as_of) triple is written once and
never rewritten, so the question "what did we say on 2 August, and what did the
price do afterwards" has an answer that does not depend on anyone's memory.

The realized return is deliberately NOT stored. It is computed at read time from
price_bars, because a stored return is wrong the next day and nobody notices.

APPEND-ONLY, AND WHY THAT MATTERS MORE THAN IT SOUNDS
-----------------------------------------------------
A revised call does not replace its predecessor — both stay, with their own dates.
The 2026-08-02 lean on TDPOWERSYS was 'up' and it delivered +33% in eleven days;
the 2026-08-13 lean is 'sideways'. Keeping both is what turns a research view into
a track record. Deleting the first would be the single most tempting edit in this
codebase, which is why record() refuses to overwrite rather than trusting callers.

USAGE
-----
    python ai_infra_call_log.py                 # record today's calls from the theme
    python ai_infra_call_log.py --seed OLD.json # also fold in an older snapshot
    python ai_infra_call_log.py --list TDPOWERSYS
"""
from __future__ import annotations

import json
import os
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(_HERE, "ai_infra_call_history.json")
THEME = os.path.join(_HERE, "ai_infra_theme.json")


def load(path=None):
    try:
        with open(path or PATH) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {"version": 1, "calls": {}}
    doc.setdefault("calls", {})
    return doc


def save(doc, path=None):
    with open(path or PATH, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False, sort_keys=True)


def record(doc, symbol, kind, as_of, **fields):
    """Add one call. Never overwrites an existing (symbol, kind, as_of). -> bool added."""
    if not (symbol and kind and as_of):
        return False
    bucket = doc["calls"].setdefault(symbol, [])
    if any(c["kind"] == kind and c["as_of"] == as_of for c in bucket):
        return False
    bucket.append({"kind": kind, "as_of": as_of, **fields})
    bucket.sort(key=lambda c: (c["as_of"], c["kind"]))
    return True


def harvest(doc, theme_path, recorded_on=None):
    """Fold every call in one theme snapshot into the history. -> count added."""
    try:
        with open(theme_path) as f:
            theme = json.load(f)
    except (OSError, ValueError) as e:
        return 0, f"{theme_path}: {e}"

    rec = str(recorded_on or date.today())
    added = 0
    for c in theme.get("companies", []):
        sym = c.get("symbol")

        o = c.get("outlook_3m") or {}
        if o.get("stance") and o.get("as_of"):
            added += record(
                doc, sym, "stance", o["as_of"],
                value=o["stance"], conviction=o.get("confidence"),
                rationale=o.get("rationale"), watch=o.get("watch"),
                valid_till=o.get("valid_till"), first_recorded=rec)

        g = c.get("grade_12m") or {}
        if g.get("grade") and g.get("as_of"):
            v = g.get("valuation") or {}
            added += record(
                doc, sym, "grade", g["as_of"],
                value=g["grade"], conviction=g.get("conviction"),
                rationale=g.get("rationale"), watch=g.get("changes_if"),
                evidence_strength=g.get("evidence_strength"),
                priced_in=g.get("priced_in"), pe_at_call=v.get("pe_ttm"),
                price_at_call=v.get("last"),
                valid_till=g.get("valid_till"), first_recorded=rec)
    return added, None


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--theme", default=THEME)
    ap.add_argument("--seed", action="append", default=[],
                    help="older theme snapshot to fold in; repeatable")
    ap.add_argument("--list", metavar="SYMBOL")
    ap.add_argument("--recorded-on", default=None,
                    help="override today's date on newly added rows")
    args = ap.parse_args()

    doc = load()

    if args.list:
        for c in doc["calls"].get(args.list.upper(), []):
            print(f"{c['as_of']}  {c['kind']:<7} {c['value']:<9} "
                  f"conviction {c.get('conviction')}")
            print(f"           {(c.get('rationale') or '')[:140]}")
        return 0

    total = 0
    # Seeds first, so the oldest call in a symbol's history is the oldest we hold
    # rather than whichever file happened to be processed first.
    for s in args.seed:
        n, err = harvest(doc, s, args.recorded_on)
        print(f"seed {s}: +{n}" + (f"  ({err})" if err else ""))
        total += n
    n, err = harvest(doc, args.theme, args.recorded_on)
    print(f"{args.theme}: +{n}" + (f"  ({err})" if err else ""))
    total += n

    save(doc)
    calls = sum(len(v) for v in doc["calls"].values())
    print(f"\n{PATH}: {len(doc['calls'])} symbols, {calls} calls (+{total} this run)")
    dates = sorted({c["as_of"] for v in doc["calls"].values() for c in v})
    print(f"call dates on file: {', '.join(dates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
