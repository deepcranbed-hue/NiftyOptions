"""
strategy_framework/strategy/blend.py
====================================
THE production blend math — one definition, used by both the live decision path
(`regime.classify`) and any offline evaluation (the Calibration Agent).

This exists so a calibration/backtest can never validate a *different* formula than
the one that actually trades. If you change how signals combine, you change it here
and both surfaces move together (CLAUDE.md DRY rule / SKILL.md HARD RULE 12).

    net_score = Σ(wᵢ · confᵢ · scoreᵢ) / Σ(wᵢ · confᵢ)      ← over LIVE signals
    net_conf  = Σ(wᵢ · confᵢ) / Σ(wᵢ over ALL names)         ← fixed denominator

The two denominators differ on purpose: the score self-normalises over whatever is
live (a NO_DATA signal drops out), while confidence divides by the FULL roster, so a
signal that is present-but-silent correctly lowers conviction.
"""
from __future__ import annotations


def effective_confidence(name: str, confidence: float, momentum_names, mom_mult: float = 1.0) -> float:
    """Time-of-day amplification: momentum-family signals get their confidence
    boosted in the opening drive / power hour (capped at 1.0)."""
    if name in momentum_names:
        return min(1.0, confidence * mom_mult)
    return confidence


def blend_net(names, wmap: dict, scores: dict, confs: dict,
              momentum_names=(), mom_mult: float = 1.0):
    """Confidence-weighted blend over `names`.

    names   : iterable of signal names to combine (the directional roster)
    wmap    : {name: static weight}
    scores  : {name: score in [-1,1]}      — missing/None = treated as absent
    confs   : {name: confidence in [0,1]}  — 0 (e.g. NO_DATA) drops it from the score

    Returns (net_score, net_confidence, contributions) where contributions[name] =
    {"score", "eff_conf"} for diagnostics.
    """
    num = wsum = conf_num = 0.0
    contributions = {}
    for name in names:
        sc = scores.get(name)
        if sc is None:
            sc = 0.0
        conf = confs.get(name) or 0.0
        conf = effective_confidence(name, conf, momentum_names, mom_mult)
        w = wmap.get(name, 0.0)
        eff_w = w * conf
        num += eff_w * sc
        wsum += eff_w
        conf_num += w * conf
        contributions[name] = {"score": round(float(sc), 3), "eff_conf": round(float(conf), 3)}
    net_score = (num / wsum) if wsum > 1e-9 else 0.0
    denom = sum(wmap.get(n, 0.0) for n in names) or 1.0
    net_conf = conf_num / denom
    return net_score, net_conf, contributions
