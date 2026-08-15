"""
llm_evals.py — judgement-based evaluators (need an LLM provider), reusing the runtime's own
model-agnostic client (agents/llm.py) READ-ONLY. In deterministic mode every one returns a clean
"skipped (needs LLM provider)" so the scorecard still renders offline.

The LLM here never predicts the market. It grades whether the report's REASONING would survive an
institutional review: economic validity, regime-awareness, missing factors, explainability, and
three personas — Chief Economist, Trading Desk, Portfolio Manager.

Each evaluator asks for STRICT JSON and normalises a 0–10 `score`.
"""
from __future__ import annotations

import json
import re


# ---------------------------------------------------------------------------
def _digest(mio: dict) -> str:
    """A compact, token-bounded view of the MIO for the grader."""
    def sect():
        out = []
        for s in (mio.get("sector_factor_library") or [])[:12]:
            out.append(f"{s.get('sector')}: {s.get('verdict')} ({s.get('score')})")
        return out

    def val():
        out = []
        for v in (mio.get("validation") or [])[:16]:
            out.append({"edge": v.get("edge"), "level": v.get("level"),
                        "state": v.get("state_label") or v.get("status"),
                        "override": v.get("override"), "why": v.get("reason_econ")})
        return out

    d = {
        "expected_direction": mio.get("expected_direction"),
        "confidence": (mio.get("confidence") or {}).get("today_confidence"),
        "engine_stats": mio.get("engine_stats"),
        "market_phase": (mio.get("macro_dashboard") or {}).get("market_phase"),
        "sectors": sect(),
        "transmission": [c.get("chain") for c in (mio.get("transmission") or [])[:8]],
        "relationships": val(),
        "metals": {k: (mio.get("metals_sentiment") or {}).get(k)
                   for k in ("overall_label", "composite", "coverage")},
        "semis_cause": (mio.get("semis_regime") or {}).get("primary_cause"),
    }
    return json.dumps(d, ensure_ascii=False, default=str)[:6000]


def _ask_json(client, system: str, user: str) -> dict:
    turn = client.chat(system, [{"role": "user", "content": user}], [])
    txt = (getattr(turn, "text", "") or "").strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return {"score": None, "raw": txt[:500]}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"score": None, "raw": txt[:500]}


def _norm(res: dict, level: str) -> dict:
    s = res.get("score")
    if isinstance(s, (int, float)):
        res["score"] = round(float(s), 1)
    res["level"] = level
    return res


_ECON_SYS = ("You are the Chief Economist of a global investment bank reviewing a market-intelligence "
             "report on Indian (NIFTY) markets. Do NOT predict the market. Evaluate whether the economic "
             "transmission mechanisms and conclusions are internally consistent, evidence-supported, and "
             "appropriate for India's macro environment. Identify inverted causal links, overconfident "
             "conclusions, unsupported assumptions and contradictions. Reply ONLY with JSON.")


def economic_reasoning(client, mio, report):
    user = ("Grade the ECONOMIC TRANSMISSION reasoning. JSON schema: "
            '{"score":0-10,"issues":["..."],"strong_points":["..."]}. '
            f"\nMIO digest:\n{_digest(mio)}")
    return _norm(_ask_json(client, _ECON_SYS, user), "L1 Economic reasoning")


def relationship_regime(client, mio, report):
    sys = ("You review market relationship validations. For each relationship say whether it holds "
           "ALWAYS, SOMETIMES, or is REGIME-DEPENDENT, and why. Reply ONLY with JSON.")
    user = ('JSON: {"score":0-10,"regime_dependent":[{"edge":"...","verdict":"always|sometimes|regime-dependent","reason":"..."}]}. '
            f"\nRelationships:\n{json.dumps([{'edge':v.get('edge'),'state':v.get('state_label'),'why':v.get('reason_econ')} for v in (mio.get('validation') or [])[:16]], default=str)[:4000]}")
    return _norm(_ask_json(client, sys, user), "L2 Relationship regime-awareness")


def missing_factors(client, mio, report):
    sys = ("You are a sector analyst. Given a sector and the drivers the report used, list IMPORTANT "
           "drivers it MISSED. Reply ONLY with JSON.")
    sectors = [{"sector": s.get("sector"),
                "factors": [f.get("factor") for f in (s.get("active_factors") or [])]}
               for s in (mio.get("sector_factor_library") or [])[:12]]
    user = ('JSON: {"score":0-10,"missing":[{"sector":"...","missing_drivers":["..."]}]}. '
            f"\nSectors & drivers used:\n{json.dumps(sectors, default=str)[:4000]}")
    return _norm(_ask_json(client, sys, user), "L3 Missing factors")


def explainability(client, mio, report):
    sys = ("You are a portfolio manager skimming a note. Judge whether a PM could understand WHY the "
           "report reached its directional view from the top drivers alone. Reply ONLY with JSON.")
    user = ('JSON: {"score":0-10,"pm_understands":true|false,"top3_drivers":["..."],"gap":"..."}. '
            f"\nExec view:\n{_digest(mio)[:3000]}\n\nReport excerpt:\n{(report or '')[:2500]}")
    return _norm(_ask_json(client, sys, user), "L7 Explainability")


_PERSONAS = {
    "chief_economist": ("Chief Economist",
        "You are the Chief Economist of a global investment bank. Do NOT predict the market. Judge "
        "whether the report's economic mechanisms, sector implications and conclusions are internally "
        "consistent, evidence-supported and appropriate for India's macro. Reply ONLY with JSON."),
    "trading_desk": ("Macro Trading Desk",
        "You are a macro trader on a NIFTY futures/options desk. Judge whether this report is ACTIONABLE: "
        "are the signals tradeable, and what critical inputs are missing (India 10Y, FII futures/OI, "
        "heavyweight contribution, gamma/positioning)? Do NOT predict the market. Reply ONLY with JSON."),
    "portfolio_manager": ("Portfolio Manager / CIO",
        "You are a portfolio manager. Judge whether this report would justify a change in portfolio "
        "allocation and whether the investment implications are clear. Do NOT predict the market. "
        "Reply ONLY with JSON."),
}


def persona(client, mio, report, key):
    name, sys = _PERSONAS[key]
    user = ('JSON: {"score":0-10,"verdict":"...","missing":["..."],"improvements":["..."]}. '
            f"\nMIO digest:\n{_digest(mio)}\n\nReport excerpt:\n{(report or '')[:3000]}")
    res = _norm(_ask_json(client, sys, user), f"Persona — {name}")
    res["persona"] = name
    return res


def run_all(client, mio, report="") -> dict:
    """All LLM evals; if no LLM provider, every one is cleanly skipped."""
    skipped = client is None or getattr(client, "is_deterministic", lambda: True)()
    keys = ["economic_reasoning", "relationship_regime", "missing_factors", "explainability",
            "chief_economist", "trading_desk", "portfolio_manager"]
    if skipped:
        return {k: {"level": k, "score": None,
                    "status": "skipped — needs an LLM provider (set provider in llm_config.json)"}
                for k in keys}
    out = {}
    for k, fn in (("economic_reasoning", economic_reasoning),
                  ("relationship_regime", relationship_regime),
                  ("missing_factors", missing_factors),
                  ("explainability", explainability)):
        try:
            out[k] = fn(client, mio, report)
        except Exception as e:
            out[k] = {"level": k, "score": None, "error": str(e)[:200]}
    for k in ("chief_economist", "trading_desk", "portfolio_manager"):
        try:
            out[k] = persona(client, mio, report, k)
        except Exception as e:
            out[k] = {"level": k, "score": None, "error": str(e)[:200]}
    return out
