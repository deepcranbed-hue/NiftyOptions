"""
evaluator.py — merges the deterministic + LLM evaluators into one scorecard.

Read-only: it consumes a saved MIO (dict) and the rendered report (str). It never imports or
modifies the engine, overlay, or agent runtime. The optional LLM client is the runtime's own
model-agnostic client, used only to *ask* the persona graders.

    scorecard = evaluate(mio, report_md, client=None)

Produces per-level scores, an overall grade, contradictions, and an advisory FEEDBACK list
(suggested prompt/rule changes) — advisory only; applying them is a separate, manual step, so the
existing pipeline is never touched by the evaluator.
"""
from __future__ import annotations

import datetime as _dt

import deterministic as D
import llm_evals as L


_GRADES = [(9.0, "Institutional Research"), (8.0, "Strong / Professional"),
           (7.0, "Solid"), (6.0, "Adequate"), (0.0, "Needs work")]


def _grade(score):
    for cut, label in _GRADES:
        if score >= cut:
            return label
    return "Needs work"


def _collect_feedback(det: dict, llm: dict) -> list[str]:
    """Advisory suggestions (feedback → prompt/rules). Nothing is applied automatically."""
    fb = []
    for c in det["contradiction"].get("contradictions", []):
        fb.append(f"[{c['severity'].upper()}] resolve contradiction: {c['message']}")
    if det["coverage"].get("missing_core"):
        fb.append("add coverage for core sectors: " + ", ".join(det["coverage"]["missing_core"]))
    cc = det["confidence"]
    if cc.get("verdict") == "too high":
        fb.append(f"lower reported confidence {cc.get('reported')}→{cc.get('recommended')} ({cc.get('reason')})")
    for r in det["historical"].get("diverged", []):
        fb.append(f"flag historical divergence in report: {r['edge']} (hist {r['hit_rate_pct']}% vs today {r['today']})")
    if det["news_quality"].get("flagged_background"):
        fb.append("filter background news from evidence: " + ", ".join(det["news_quality"]["flagged_background"]))
    for e in det["evidence"].get("unsupported", []):
        fb.append(f"add evidence or drop unsupported explanation: {e}")
    for t in det["report_language"].get("suggestions", []):
        if "appropriately probabilistic" not in t:
            fb.append("language: " + t)
    # LLM issues (when present)
    for k, res in llm.items():
        for i in res.get("issues", []) or []:
            fb.append(f"[{res.get('level', k)}] {i}")
        for m in res.get("missing", []) or []:
            if isinstance(m, dict):
                fb.append(f"[missing] {m.get('sector')}: {', '.join(m.get('missing_drivers', []))}")
    return fb


def evaluate(mio: dict, report_md: str = "", client=None) -> dict:
    det = D.run_all(mio, report_md)
    llm = L.run_all(client, mio, report_md)

    scored = [v["score"] for v in list(det.values()) + list(llm.values())
              if isinstance(v.get("score"), (int, float))]
    overall = round(sum(scored) / len(scored), 2) if scored else None

    return {
        "schema": "newsagent.eval.scorecard/v1",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "as_of": mio.get("as_of"),
        "mio_id": mio.get("mio_id"),
        "llm_graded": not (client is None or getattr(client, "is_deterministic", lambda: True)()),
        "overall_quality": {"score": overall, "grade": _grade(overall) if overall is not None else "n/a",
                            "n_levels_scored": len(scored)},
        "deterministic": det,
        "llm": llm,
        "feedback": _collect_feedback(det, llm),
    }
