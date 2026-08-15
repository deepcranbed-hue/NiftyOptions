"""
deterministic.py — rule-based evaluators that need NO LLM.

These grade the *reasoning quality* of a saved MIO + rendered report using only its own fields and
a reference taxonomy. They run offline, at zero cost, and form the backbone of the scorecard; the
LLM personas (llm_evals.py) add judgement on top. Every score is 0–10.

Levels implemented here:
  L4  Contradiction detection      (market bias vs forecast; canonical economic sign rules)
  L5  Coverage                     (NIFTY sector universe vs what the note covered)
  L6  Confidence calibration       (reported confidence vs relationship hold-rate + agreement)
  L8  Probability calibration      (turn the label into Bull%/Bear% from the driver vote)
  L11 Historical consistency       (today's relationship states vs their calibrated hit-rates)
  L12 News quality                 (did BACKGROUND / non-market-moving evidence leak in?)
  L13 Evidence / hallucination     (is every explanation backed by market / news / stated inference?)
  L14 Report quality (language)    (over-deterministic wording a probabilistic note should avoid)
"""
from __future__ import annotations

import re

import taxonomy as T


def _clamp(x, lo=0.0, hi=10.0):
    return max(lo, min(hi, x))


def _all_text(obj) -> str:
    """Flatten any nested MIO fragment to one lowercase string (for keyword scans)."""
    out = []
    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            out.append(o)
    walk(obj)
    return " ".join(out).lower()


# ---------------------------------------------------------------------------
def contradictions(mio: dict, report: str) -> dict:
    """L4 — flag internal disagreements: market bias vs forecast, and economic sign errors."""
    issues = []
    exp = mio.get("expected_direction", {}) or {}
    nifty = str(exp.get("Nifty 50", "")).lower()
    bias = ""
    mp = (mio.get("macro_dashboard", {}) or {}).get("market_phase", {}) or {}
    bias = str(mp.get("market_bias", "")).lower()
    # (a) headline market-bias vs the engine's directional lean
    if bias and nifty:
        bias_up = "bull" in bias
        bias_dn = "bear" in bias
        nifty_up = "up" in nifty
        nifty_dn = "down" in nifty
        if (bias_up and nifty_dn) or (bias_dn and nifty_up):
            issues.append({"severity": "high",
                           "message": f"Headline market bias ('{mp.get('market_bias')}') disagrees with "
                                      f"the Nifty forecast ('{exp.get('Nifty 50')}')."})
    # (b) canonical economic sign rules — scan transmission + macro text for inverted logic
    text = _all_text([mio.get("transmission"), mio.get("transmission_multihop"),
                      mio.get("macro_expectations"), mio.get("theme"), mio.get("event")])
    for ante, cons, sign, rule in T.CANONICAL_RULES:
        if any(a in text for a in ante) and any(c in text for c in cons):
            # both mentioned together; if the wrong-direction cue is co-present, flag for review
            wrong_cue = (" up" if sign < 0 else " down")
            if any((c + wrong_cue) in text for c in cons):
                issues.append({"severity": "medium",
                               "message": f"Possible economic inconsistency: {rule}"})
    high = sum(1 for i in issues if i["severity"] == "high")
    med = sum(1 for i in issues if i["severity"] == "medium")
    score = _clamp(10 - 4 * high - 2 * med)
    return {"level": "L4 Contradiction detection", "score": round(score, 1),
            "contradictions": issues,
            "summary": "no contradictions found" if not issues else f"{len(issues)} flagged"}


def coverage(mio: dict, report: str) -> dict:
    """L5 — which NIFTY sectors did the note actually cover?"""
    covered_text = _all_text([mio.get("sector_factor_library"), mio.get("affected_sectors"),
                              mio.get("affected_sectors_enriched"), mio.get("subsector_factors")])
    covered, missing, missing_core = [], [], []
    for name, (aliases, core) in T.NIFTY_SECTORS.items():
        if any(a in covered_text for a in aliases):
            covered.append(name)
        else:
            missing.append(name)
            if core:
                missing_core.append(name)
    core_total = sum(1 for _, (_, core) in T.NIFTY_SECTORS.items() if core)
    core_covered = core_total - len(missing_core)
    score = _clamp(10 * core_covered / max(1, core_total))
    return {"level": "L5 Coverage", "score": round(score, 1),
            "covered": covered, "missing": missing, "missing_core": missing_core,
            "summary": f"{core_covered}/{core_total} core sectors covered"}


def _hold_rate(mio: dict):
    held = broke = 0
    for v in mio.get("validation", []) or []:
        held += len(v.get("held", []) or [])
        broke += len(v.get("broke", []) or [])
    tot = held + broke
    return (held / tot if tot else None), held, broke


def confidence_calibration(mio: dict, report: str) -> dict:
    """L6 — is the reported confidence justified by how many relationships actually held?"""
    conf = mio.get("confidence", {}) or {}
    reported = conf.get("today_confidence")
    agreement = (mio.get("engine_stats", {}) or {}).get("agreement")
    hold, held, broke = _hold_rate(mio)
    if reported is None:
        return {"level": "L6 Confidence calibration", "score": None,
                "summary": "no reported confidence in MIO"}
    # recommended: half from driver agreement, half from relationship hold-rate (fallbacks safe)
    a = agreement if isinstance(agreement, (int, float)) else 0.5
    h = hold if hold is not None else a
    recommended = round(0.5 * a + 0.5 * h, 2)
    gap = reported - recommended
    score = _clamp(10 - abs(gap) * 40)         # 0.05 gap ~ -2 pts
    reason = (f"{held} proxy-checks held vs {broke} broke (hold-rate "
              f"{'n/a' if hold is None else f'{hold:.0%}'}); driver agreement {a:.0%}.")
    return {"level": "L6 Confidence calibration", "score": round(score, 1),
            "reported": round(reported, 2), "recommended": recommended,
            "verdict": ("too high" if gap > 0.08 else "too low" if gap < -0.08 else "well calibrated"),
            "reason": reason}


def probability_calibration(mio: dict, report: str) -> dict:
    """L8 — express the directional call as Bull% / Bear% from the driver vote + agreement."""
    es = mio.get("engine_stats", {}) or {}
    nb, nr = es.get("n_bull"), es.get("n_bear")
    if not isinstance(nb, int) or not isinstance(nr, int) or (nb + nr) == 0:
        return {"level": "L8 Probability calibration", "score": None,
                "summary": "no bull/bear vote in engine_stats"}
    bear = round(100 * nr / (nb + nr))
    bull = 100 - bear
    exp = str((mio.get("expected_direction", {}) or {}).get("Nifty 50", "")).lower()
    # score: does the reported label lean the same way as the probability tilt?
    tilt_dn = bear > bull
    label_dn = "down" in exp
    aligned = (tilt_dn == label_dn) or (bull == bear)
    return {"level": "L8 Probability calibration", "score": 10.0 if aligned else 6.0,
            "bull_pct": bull, "bear_pct": bear,
            "conviction": es.get("conviction"), "agreement": es.get("agreement"),
            "summary": f"Bull {bull}% / Bear {bear}% (from {nb} bull vs {nr} bear drivers)"}


def historical_consistency(mio: dict, report: str) -> dict:
    """L11 — where a relationship has a calibrated hit-rate, did today match or diverge?"""
    rows = []
    for v in mio.get("validation", []) or []:
        rel = v.get("reliability") or {}
        hr = rel.get("hit_rate_pct")
        state = v.get("state_label") or v.get("status")
        if hr is None or not state:
            continue
        historically_holds = hr >= 55
        today_holds = state in ("Confirmed", "Partially Confirmed") or v.get("status") == "CONFIRMED"
        diverged = historically_holds and not today_holds
        rows.append({"edge": v.get("edge"), "hit_rate_pct": hr, "n": rel.get("n"),
                     "today": state, "diverged": diverged})
    if not rows:
        return {"level": "L11 Historical consistency", "score": None,
                "summary": "no calibrated relationships to compare"}
    diverged = [r for r in rows if r["diverged"]]
    score = _clamp(10 * (1 - len(diverged) / len(rows)))
    return {"level": "L11 Historical consistency", "score": round(score, 1),
            "checked": len(rows), "diverged": diverged,
            "summary": f"{len(diverged)}/{len(rows)} relationships diverged from their history today"}


def news_quality(mio: dict, report: str) -> dict:
    """L12 — did BACKGROUND / non-market-moving items leak into the evidence the note used?"""
    ev = _all_text([mio.get("macro_expectations"), mio.get("metals_sentiment"),
                    mio.get("validation"), mio.get("theme")])
    flagged = sorted({kw.strip() for kw in T.BACKGROUND_NEWS if kw in ev})
    score = _clamp(10 - 3 * len(flagged))
    return {"level": "L12 News quality", "score": round(score, 1),
            "flagged_background": flagged,
            "summary": "no background/non-market-moving evidence detected" if not flagged
                       else f"{len(flagged)} background pattern(s) in evidence — review"}


def evidence_support(mio: dict, report: str) -> dict:
    """L13 — every override/explanation should be backed by market, news, or a STATED inference."""
    checked, unsupported = [], []
    for v in mio.get("validation", []) or []:
        if v.get("state") not in ("🔄", "⚠️") and v.get("status") not in ("OVERRIDDEN", "WEAKENED"):
            continue
        has_market = bool(v.get("held") or v.get("broke"))
        has_inference = bool(v.get("reason_econ") or v.get("reason"))
        evidenced = bool(v.get("override_evidenced"))
        row = {"edge": v.get("edge"),
               "market": has_market, "inference": has_inference, "evidenced_by_data": evidenced}
        checked.append(row)
        if not has_market and not has_inference:
            unsupported.append(v.get("edge"))
    if not checked:
        return {"level": "L13 Evidence / hallucination", "score": None,
                "summary": "no overridden relationships to audit"}
    score = _clamp(10 * (1 - len(unsupported) / len(checked)))
    return {"level": "L13 Evidence / hallucination", "score": round(score, 1),
            "audited": len(checked), "unsupported": unsupported,
            "summary": ("all explanations carry market/inference support"
                        if not unsupported else f"{len(unsupported)} unsupported explanation(s)")}


def report_language(mio: dict, report: str) -> dict:
    """L14 — grade the WRITING for over-determinism (a probabilistic note hedges its calls)."""
    low = (report or "").lower()
    hard = sorted({t for t in T.OVERCERTAIN_TERMS if t in low})
    # bare directional calls with no hedge word within the same line
    bare = 0
    for line in low.splitlines():
        if ("bullish" in line or "bearish" in line) and not any(h in line for h in T.HEDGE_WORDS):
            bare += 1
    score = _clamp(10 - 2 * len(hard) - 0.4 * bare)
    tips = []
    if hard:
        tips.append(f"remove absolutist terms: {', '.join(hard)}")
    if bare:
        tips.append(f"{bare} bare 'Bullish/Bearish' call(s) — prefer 'bullish bias' / add a probability")
    return {"level": "L14 Report quality (language)", "score": round(score, 1),
            "overcertain_terms": hard, "bare_directional_calls": bare,
            "suggestions": tips or ["language is appropriately probabilistic"]}


def run_all(mio: dict, report: str = "") -> dict:
    """Run every deterministic evaluator; return {level_key: result}."""
    return {
        "contradiction": contradictions(mio, report),
        "coverage": coverage(mio, report),
        "confidence": confidence_calibration(mio, report),
        "probability": probability_calibration(mio, report),
        "historical": historical_consistency(mio, report),
        "news_quality": news_quality(mio, report),
        "evidence": evidence_support(mio, report),
        "report_language": report_language(mio, report),
    }
