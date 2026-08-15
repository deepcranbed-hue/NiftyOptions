"""
strategy_framework/gen_signal_docs.py
=====================================
GENERATE strategy_framework/SIGNALS.md from the registry — the single source.

Every signal's "how it's estimated" already lives on its SignalSpec.method (the same
text the UI breakdown shows). Rather than hand-maintain a doc that drifts the moment a
signal is added (exactly what happened to REFERENCE.md), we render the doc FROM the
registry. Re-run after any registry change:

    python -m strategy_framework.gen_signal_docs      # writes SIGNALS.md

The IntegrityAgent can also call check_current() to fail the build if SIGNALS.md is
stale relative to the registry.
"""
from __future__ import annotations
import os

_HERE = os.path.dirname(__file__)
_OUT = os.path.join(_HERE, "SIGNALS.md")

_CLASS_ORDER = ["regime", "position", "confirmation"]
_CLASS_TITLE = {
    "regime": "Regime signals — *what kind of market is this?* (never vote direction)",
    "position": "Directional / position signals — *which way is the edge?*",
    "confirmation": "Confirmation / execution signals — *is the move being accepted?*",
}


def render() -> str:
    from strategy_framework.signals import registry as R
    roster = R.roster()
    by_class: dict[str, list] = {}
    for s in roster:
        by_class.setdefault(s.get("signal_class", "position"), []).append(s)

    lines = [
        "# NIFTY signal reference — how each signal is estimated",
        "",
        "> AUTO-GENERATED from `strategy_framework/signals/registry.py` "
        "(`python -m strategy_framework.gen_signal_docs`). Do not edit by hand — edit the "
        "`SignalSpec.method` in the registry and regenerate. The registry is the single "
        "source; this file is a view of it.",
        "",
        f"**{len(roster)} signals** · "
        f"{sum(1 for s in roster if s['kind'] == 'directional')} directional · "
        f"{sum(1 for s in roster if s['blended'])} in the live blend.",
        "",
        "Columns: **weight** = live blend weight (0 = studied candidate, not yet voting); "
        "**horizon** = intraday vs slow (daily); **data** = data_ready.",
        "",
    ]

    for cls in _CLASS_ORDER + [c for c in by_class if c not in _CLASS_ORDER]:
        sigs = by_class.get(cls)
        if not sigs:
            continue
        lines.append(f"## {_CLASS_TITLE.get(cls, cls.title())}")
        lines.append("")
        for s in sorted(sigs, key=lambda x: (-x["weight"], x["name"])):
            wtag = f"weight **{s['weight']}**" if s["weight"] else "weight 0 (candidate)"
            flags = [wtag, f"family `{s['family']}`", f"kind `{s['kind']}`"]
            if s.get("horizon") and s["horizon"] != "intraday":
                flags.append(f"horizon `{s['horizon']}`")
            if not s.get("data_ready", True):
                flags.append("**data not ready** (pinned)")
            lines.append(f"### {s['label']}  ·  `{s['name']}`")
            lines.append("")
            lines.append(" · ".join(flags))
            lines.append("")
            lines.append(s["method"] or "_(no method description in the registry)_")
            lines.append("")
            if s.get("detail_keys"):
                lines.append(f"*Detail fields:* {', '.join('`%s`' % k for k in s['detail_keys'])}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write() -> str:
    doc = render()
    with open(_OUT, "w") as f:
        f.write(doc)
    return _OUT


def check_current() -> bool:
    """True if SIGNALS.md on disk matches what the registry would generate now."""
    if not os.path.exists(_OUT):
        return False
    with open(_OUT) as f:
        return f.read() == render()


if __name__ == "__main__":
    print("wrote", write())
