#!/usr/bin/env python3
"""
interaction_discovery.py — learn NEW economic relationships without polluting the
production interaction model.

The split that matters
----------------------
    KNOWN interactions      Oil × CPI, Oil × Geopolitics
        → modelled, affect today's scores

    DISCOVERED candidates   Semis × Oil, AI × Copper, Defence × Steel
        → recorded ONLY, affect nothing

This module is the second box. It watches which narratives keep firing together, counts
recurrences, tracks average strength, and nominates a pair for promotion once it has
enough history. It NEVER feeds a score.

Why the separation is strict
----------------------------
Every weight in this engine is a judgement PRIOR, and today's session produced several
lessons about numbers that looked like evidence but were artifacts. An interaction
discovered by co-occurrence is exactly that risk: two narratives can co-fire for weeks
because they share a trigger word, not because they compound economically. Promotion is
therefore a deliberate human step, backed by a hit-rate, not an automatic one.

Usage
-----
    import interaction_discovery as idisc
    idisc.record(dispatch_out["relationships"])       # once per run
    print(idisc.report())                             # candidates + promotion readiness

CLI:
    python3 interaction_discovery.py            # show the candidate ledger
    python3 interaction_discovery.py --ready    # only pairs that meet the bar
"""

from __future__ import annotations

import json
import sys
import datetime as dt
from pathlib import Path

LEDGER = Path(__file__).resolve().parent / "interaction_candidates.json"

# Promotion bar. Deliberately conservative: a pair must recur across many sessions AND
# be strong when it fires. ~30 observations is also roughly where the audit module's
# Wilson interval starts to separate signal from chance.
MIN_COUNT = 30
MIN_AVG_STRENGTH = 0.45


def _load() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except Exception:
        return {}


def _save(db: dict) -> None:
    try:
        LEDGER.write_text(json.dumps(db, indent=2, sort_keys=True))
    except Exception:
        pass


def record(relationships: list[dict], as_of: str | None = None) -> dict:
    """Log today's UNMODELLED co-activations. Modelled pairs are ignored — they are
    already in the production model and need no discovery."""
    day = as_of or dt.date.today().isoformat()
    db = _load()
    added = 0
    for r in relationships or []:
        if r.get("known_interaction"):
            continue                                   # already modelled
        pair = r.get("pair")
        s = float(r.get("strength") or 0)
        if not pair or s <= 0:
            continue
        rec = db.setdefault(pair, {"count": 0, "sum_strength": 0.0,
                                   "first_seen": day, "last_seen": day, "days": []})
        if day in rec["days"]:
            continue                                   # one observation per session
        rec["count"] += 1
        rec["sum_strength"] += s
        rec["last_seen"] = day
        rec["days"] = (rec["days"] + [day])[-90:]      # keep a bounded window
        added += 1
    _save(db)
    return {"pairs_tracked": len(db), "recorded_today": added}


def candidates() -> list[dict]:
    db = _load()
    out = []
    for pair, r in db.items():
        n = r.get("count", 0)
        avg = (r.get("sum_strength", 0.0) / n) if n else 0.0
        ready = n >= MIN_COUNT and avg >= MIN_AVG_STRENGTH
        out.append({
            "pair": pair, "count": n, "avg_strength": round(avg, 3),
            "first_seen": r.get("first_seen"), "last_seen": r.get("last_seen"),
            "ready_for_review": ready,
            "status": ("🟢 meets the bar — review, backtest, then promote by hand"
                       if ready else
                       f"⏳ {n}/{MIN_COUNT} sessions" if avg >= MIN_AVG_STRENGTH else
                       f"⚪ weak when it fires (avg {avg:.2f} < {MIN_AVG_STRENGTH})"),
        })
    out.sort(key=lambda c: (-c["count"], -c["avg_strength"]))
    return out


def report(only_ready: bool = False) -> str:
    cs = [c for c in candidates() if c["ready_for_review"] or not only_ready]
    if not cs:
        return ("_No unmodelled co-activations recorded yet. Run the dispatcher across "
                "several sessions to accumulate candidates._")
    L = ["### 🔬 Interaction discovery — candidate relationships\n",
         "_Narrative pairs that keep firing together but are **not** in the production "
         "interaction model. Recorded only — these affect **no** score. Promotion to "
         "KNOWN_INTERACTIONS is a deliberate step after backtesting, because pairs can "
         "co-fire from shared trigger words rather than economic compounding._\n",
         "| Pair | Sessions | Avg strength | First seen | Status |",
         "|---|---:|---:|---|---|"]
    for c in cs:
        L.append(f"| {c['pair']} | {c['count']} | {c['avg_strength']:.2f} | "
                 f"{c['first_seen']} | {c['status']} |")
    return "\n".join(L)


if __name__ == "__main__":
    print(report(only_ready="--ready" in sys.argv))
