"""render.py — turn an evaluation scorecard dict into a readable markdown grade sheet."""
from __future__ import annotations


def _bar(score):
    if not isinstance(score, (int, float)):
        return "—"
    n = int(round(score))
    return "█" * n + "░" * (10 - n) + f" {score:.1f}"


def render(sc: dict) -> str:
    A, L = [], []
    A = L.append
    oq = sc.get("overall_quality", {})
    A("# Reasoning-Quality Scorecard\n")
    A(f"> as-of **{sc.get('as_of','?')}** · overall **{oq.get('score','n/a')}/10 — "
      f"{oq.get('grade','n/a')}** · LLM-graded: {sc.get('llm_graded')}\n")

    A("## Deterministic evaluators (offline, rule-based)\n")
    A("| Level | Score | Summary |")
    A("|---|---|---|")
    for res in sc.get("deterministic", {}).values():
        s = res.get("score")
        summ = res.get("summary") or res.get("verdict") or ""
        A(f"| {res.get('level')} | {_bar(s)} | {summ} |")
    A("")

    # contradictions detail
    cons = sc.get("deterministic", {}).get("contradiction", {}).get("contradictions", [])
    if cons:
        A("### Contradictions\n")
        for c in cons:
            A(f"- **{c['severity'].upper()}** — {c['message']}")
        A("")

    # confidence + probability detail
    cc = sc.get("deterministic", {}).get("confidence", {})
    if cc.get("reported") is not None:
        A(f"**Confidence calibration:** reported {cc['reported']} · recommended {cc['recommended']} "
          f"→ _{cc.get('verdict')}_ ({cc.get('reason')})\n")
    pc = sc.get("deterministic", {}).get("probability", {})
    if pc.get("bull_pct") is not None:
        A(f"**Probability calibration:** Bull {pc['bull_pct']}% / Bear {pc['bear_pct']}% "
          f"· conviction {pc.get('conviction')}\n")
    cov = sc.get("deterministic", {}).get("coverage", {})
    if cov.get("missing_core"):
        A(f"**Coverage gaps (core):** {', '.join(cov['missing_core'])}\n")

    # LLM section
    A("## Judgement evaluators (LLM personas)\n")
    llm = sc.get("llm", {})
    any_scored = any(isinstance(r.get("score"), (int, float)) for r in llm.values())
    if not any_scored:
        A("_Skipped — no LLM provider configured. Set `provider` in `agents/llm_config.json` "
          "(anthropic / openai / ollama) and re-run to activate the Chief Economist, Trading Desk "
          "and Portfolio Manager reviews._\n")
    else:
        A("| Evaluator | Score | Verdict / notes |")
        A("|---|---|---|")
        for res in llm.values():
            s = res.get("score")
            note = res.get("verdict") or res.get("summary") or (res.get("issues") or [""])[0] or ""
            A(f"| {res.get('level')} | {_bar(s)} | {str(note)[:80]} |")
        A("")

    # feedback
    fb = sc.get("feedback", [])
    A("## Feedback → prompt / rules (advisory — not auto-applied)\n")
    if not fb:
        A("_No changes suggested — the note passed all deterministic checks._")
    else:
        for f in fb:
            A(f"- {f}")
    A("")
    return "\n".join(L)
