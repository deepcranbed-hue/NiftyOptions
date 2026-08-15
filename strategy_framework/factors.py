"""
strategy_framework/factors.py
=============================
The INFERENCE ENGINE — generic aggregation of observations into beliefs.

Knowledge lives in the KNOWLEDGE BASE (knowledge/factor_map.yaml + evidence.json);
this module contains only the inference ALGORITHM. It never mentions a specific
factor or signal by name — adding a factor or reassigning a role is a YAML edit.

Vocabulary (the statistical model the engine implements):
  observations   = the signals (noisy sensors of the market)
  latent factors = hidden market properties (trend direction, dealer positioning, …)
  beliefs        = estimated market state: per factor an ESTIMATE plus two orthogonal
                   trust measures —
      confidence : do the sensors AGREE TODAY?      f(today's cross-sensor agreement)
      quality    : have these sensors HISTORICALLY  f(evidence.json — incremental IC
                   earned trust?                      from the research engine)
  High confidence + low quality = "everyone agrees, but history says these sensors
  aren't reliable" — exactly the state a plain vote hides.

Aggregation (PRIOR, transparent): estimate = role-weighted mean of voting members
(primary 1.0, supporting 0.6); agreement = share of voters on the estimate's side;
confidence = 0.2 + 0.5·agreement ± 0.1 for aligned/opposed confidence-modifiers.
Quality = mean historical |incremental IC| of the voting members, scaled by a PRIOR
(|iIC| ≈ 0.05 → full trust), flagged PROVISIONAL until the evidence spans ≥60 sessions,
and None (unmeasured) when no evidence has been written at all. NOT calibrated
probabilities — descriptive until evidence-based weights land.
"""
from __future__ import annotations
from dataclasses import dataclass

from . import knowledge as KB

_ROLE_WEIGHT = {"primary": 1.0, "supporting": 0.6}   # confidence/diagnostic never vote
_ROLES = ("primary", "supporting", "confidence", "diagnostic")
_QUALITY_IIC_SCALE = 0.05        # PRIOR: historical |incremental IC| at which quality → 1.0
_QUALITY_MIN_SESSIONS = 60       # below this the evidence is PROVISIONAL


@dataclass(frozen=True)
class FactorSpec:
    name: str
    label: str
    kind: str = "intraday"
    members: tuple = ()              # ((signal_name, role), ...)


def _load() -> tuple[list[FactorSpec], str]:
    raw = KB.load_factor_map()
    version = str((raw.get("_meta") or {}).get("version", "unversioned"))
    specs = [FactorSpec(name=k, label=v.get("label", k), kind=v.get("kind", "intraday"),
                        members=tuple((s, r) for s, r in v.get("signals", {}).items()))
             for k, v in raw.items() if not k.startswith("_")]
    return specs, version


FACTORS, MAP_VERSION = _load()


def _member_quality(name: str, ev: dict | None):
    """Historical trust for one sensor from the evidence store: |incremental IC| scaled
    by the prior (controls fall back to standalone |IC|). None = unmeasured."""
    if not ev:
        return None
    row = (ev.get("signals") or {}).get(name)
    if not row:
        return None
    v = row.get("incremental_ic")
    if v is None:
        v = row.get("ic")
    if v is None:
        return None
    return max(0.0, min(1.0, abs(v) / _QUALITY_IIC_SCALE))


def evaluate_factors(bundle, evidence: dict | None = None) -> list[dict]:
    """Aggregate a signal bundle into per-factor BELIEFS:
    {estimate, confidence, agreement, quality, quality_basis, members(with ✓/✗)}.
    Pass `evidence` (preloaded KB.load_evidence()) when calling in a loop."""
    ev = evidence if evidence is not None else KB.load_evidence()
    ev_sessions = (ev or {}).get("n_sessions")
    out = []
    for f in FACTORS:
        members, votes, mods, quals = [], [], [], []
        num = den = 0.0
        for name, role in f.members:
            s = bundle.get(name)
            ok = bool(s and getattr(s, "status", "") == "OK")
            score = float(getattr(s, "score", 0.0)) if ok else None
            members.append({"name": name, "role": role,
                            "score": (round(score, 3) if score is not None else None), "ok": ok})
            if not ok:
                continue
            w = _ROLE_WEIGHT.get(role, 0.0)
            if w > 0:
                num += w * score; den += w; votes.append(score)
                q = _member_quality(name, ev)
                if q is not None:
                    quals.append(q)
            elif role == "confidence":
                mods.append(score)
        est = (num / den) if den > 0 else None
        agreement = conf = None
        if est is not None:
            same = [v for v in votes if (v >= 0) == (est >= 0)]
            agreement = len(same) / len(votes) if votes else 0.0
            active = [m for m in mods if abs(m) > 0.15]
            adj = 0.0
            if active:
                aligned = sum(1 for m in active if (m >= 0) == (est >= 0))
                adj = 0.1 if aligned * 2 > len(active) else (-0.1 if aligned * 2 < len(active) else 0.0)
            conf = max(0.1, min(0.9, 0.2 + 0.5 * agreement + adj))
        quality = (round(sum(quals) / len(quals), 2) if quals else None)
        for m in members:
            m["agrees"] = bool(m["score"] is not None and est is not None
                               and (m["score"] >= 0) == (est >= 0))
        out.append({"name": f.name, "label": f.label, "kind": f.kind,
                    "estimate": (round(est, 3) if est is not None else None),
                    "confidence": (round(conf, 2) if conf is not None else None),
                    "agreement": (round(agreement, 2) if agreement is not None else None),
                    "quality": quality,
                    "quality_basis": (None if quality is None else
                                      ("provisional" if not ev_sessions or ev_sessions < _QUALITY_MIN_SESSIONS
                                       else f"{ev_sessions} sessions")),
                    "members": members})
    return out


def validate() -> dict:
    """Map sanity: roles valid, every member exists, one home per signal, all
    directional signals covered."""
    from .signals import registry as R
    seen: dict[str, str] = {}
    for f in FACTORS:
        for name, role in f.members:
            assert role in _ROLES, f"factor {f.name}: bad role {role}"
            assert name in R.BY_NAME, f"factor {f.name}: unknown signal {name}"
            assert name not in seen, f"{name} assigned to both {seen[name]} and {f.name}"
            seen[name] = f.name
    missing = [n for n in R.directional_names() if n not in seen]
    assert not missing, f"directional signals not assigned to any factor: {missing}"
    return {"n_factors": len(FACTORS), "n_assigned": len(seen)}
