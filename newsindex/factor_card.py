#!/usr/bin/env python3
"""
factor_card.py — the debugging VIEW of a factor.

A factor card shows everything about a factor on one screen: its identity (from
factors.py), whether it's active today, and — the important part — WHO it affects. That
last part is DERIVED live from the exposure registry, never stored on the factor:

    affected_companies(FACTOR_OIL_PRICE)  ==  "every company whose exposure mentions it"

So the card can never disagree with the exposure registry — it IS the registry, queried.
Add a paint company to hierarchy.EXPOSURES and it appears on the oil card automatically;
there is no second list to update. That is the whole point of factors-first: one fact,
one home, everything else a view.

Usage:
    import factor_card
    print(factor_card.render("FACTOR_OIL_PRICE", activation=+0.92))
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

import factors as F
import taxonomy as TAX
import hierarchy as H


def affected_companies(factor_id: str) -> list[dict]:
    """DERIVED: every company with a non-zero exposure to this factor. Computed from
    hierarchy.EXPOSURES — NOT a stored transmission list. Sorted by |exposure|."""
    out = []
    # union of every symbol that has any exposure (bucket members + per-company overrides)
    syms = set(H.COMPANY_EXPOSURES)
    for sym in TAX.SUBTAXONOMY:
        syms.add(sym)
    for sym in sorted(syms):
        e = H.exposures_of(sym)
        s = e.get(factor_id)
        if s:
            out.append({"symbol": sym, "exposure": s,
                        "sector": TAX.sector_of(sym), "subsector": TAX.subsector_of(sym),
                        "bucket": TAX.bucket_of(sym)})
    out.sort(key=lambda r: -abs(r["exposure"]))
    return out


def transmission(factor_id: str) -> dict:
    """DERIVED: which buckets/sectors inherit this factor, and net direction — computed
    from the affected companies, not authored on the factor."""
    by_bucket: dict[str, list] = {}
    for r in affected_companies(factor_id):
        by_bucket.setdefault(r["bucket"], []).append(r["exposure"])
    return {b: {"sign": "🟢+" if sum(v) > 0 else "🔴−", "n": len(v),
                "avg": round(sum(v) / len(v), 2)}
            for b, v in sorted(by_bucket.items(), key=lambda kv: -abs(sum(kv[1])))}


def affected_sectors(factor_id: str) -> list[dict]:
    """DERIVED one level up: aggregate company exposures into SECTOR profiles. No sector
    lists stored — add a company and the sector profile updates itself."""
    by_sec: dict[str, list] = {}
    for r in affected_companies(factor_id):
        by_sec.setdefault(r["sector"] or "—", []).append(r["exposure"])
    out = [{"sector": s, "avg_exposure": round(sum(v) / len(v), 2), "n": len(v)}
           for s, v in by_sec.items()]
    out.sort(key=lambda r: -abs(r["avg_exposure"]))
    return out


def affected_subsectors(factor_id: str) -> list[dict]:
    """DERIVED: subsector profiles. Add Petronet LNG to the ontology and 'Upstream'
    updates automatically — no subsector list to edit."""
    by_sub: dict[str, list] = {}
    for r in affected_companies(factor_id):
        by_sub.setdefault(r["subsector"] or "—", []).append(r["exposure"])
    out = [{"subsector": s, "avg_exposure": round(sum(v) / len(v), 2), "n": len(v)}
           for s, v in by_sub.items()]
    out.sort(key=lambda r: -abs(r["avg_exposure"]))
    return out


def measured_by(factor_id: str) -> list[str]:
    """Which observable series feed this factor (the reverse of OBSERVABLE_TO_FACTORS)."""
    return [obs for obs, m in F.OBSERVABLE_TO_FACTORS.items() if factor_id in m]


def render(factor_id: str, activation: float | None = None,
           triggered_by: list | None = None, validation: str = "") -> str:
    """The one-screen factor card. `activation` and `validation` are today's runtime
    state; everything else is identity + derived transmission."""
    f = F.factor(factor_id)
    if not f:
        return f"_unknown factor {factor_id}_"
    L = [f"### 🃏 {f.label}  `{f.id}`",
         f"_{f.cat} · {f.description or f.note}_\n"]
    # runtime state
    if activation is not None:
        state = "🟢 ACTIVE" if abs(activation) >= 0.15 else "⚪ quiet"
        L.append(f"**Activation:** {state}  ({activation:+.2f})"
                 + (f"  · triggered by {', '.join(triggered_by)}" if triggered_by else ""))
    # identity
    if f.mechanism:
        L.append(f"**Mechanism:** {f.mechanism}")
    obs = measured_by(factor_id) or list(f.measured_by)
    L.append(f"**Measured by:** {', '.join(obs) or '— (news-activated, no price series)'}"
             + (f"  · threshold {f.threshold:.0f}%" if f.threshold else ""))
    if f.triggers:
        L.append(f"**Typical triggers:** {', '.join(f.triggers)}")
    # DERIVED transmission
    tr = transmission(factor_id)
    if tr:
        L.append("\n**Transmission (derived from exposures — not stored):**")
        L.append("| Bucket | Dir | Avg exposure | N |")
        L.append("|---|---|---:|---:|")
        for b, d in tr.items():
            L.append(f"| {b} | {d['sign']} | {d['avg']:+.2f} | {d['n']} |")
    # DERIVED affected companies
    ac = affected_companies(factor_id)
    if ac:
        names = ", ".join(f"{r['symbol']} {r['exposure']:+.1f}" for r in ac[:10])
        L.append(f"\n**Affected companies ({len(ac)}):** {names}")
    if validation:
        L.append(f"\n**Validation:** {validation}")
    return "\n".join(L)


if __name__ == "__main__":
    # demo cards for the factors that caused the most trouble this session
    for fid, act in [("FACTOR_OIL_PRICE", +0.92), ("FACTOR_SEMI_CYCLE", -0.60),
                     ("FACTOR_US_TECH_SPENDING", -0.60)]:
        print(render(fid, activation=act))
        print()
