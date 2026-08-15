"""
test_evals_offline.py — offline, deterministic tests for the evaluation layer.

Self-contained: builds a synthetic MIO that deliberately contains a contradiction, a coverage gap,
a background-news leak, an over-certain report line and an overridden relationship, then asserts the
evaluators catch each. No network, no LLM. Run:  python test_evals_offline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluator
import render


def _synthetic_mio():
    return {
        "as_of": "2026-07-16T00:00:00+00:00", "mio_id": "test",
        "expected_direction": {"Nifty 50": "Up"},                       # forecast UP...
        "macro_dashboard": {"market_phase": {"market_bias": "🔴 Bearish"}},  # ...bias BEARISH => contradiction
        "confidence": {"today_confidence": 0.90},                        # high...
        "engine_stats": {"agreement": 0.55, "n_bull": 6, "n_bear": 4, "conviction": "Low"},
        "sector_factor_library": [                                       # only 2 sectors => coverage gap
            {"sector": "Banks / Financials", "verdict": "🔴 Bearish", "score": -0.3,
             "active_factors": [{"factor": "Yield curve"}]},
            {"sector": "IT Services", "verdict": "🔴 Bearish", "score": -0.5,
             "active_factors": [{"factor": "AI regime"}]},
        ],
        "validation": [
            {"edge": "Weak rupee → IT exporters up", "level": "Macro", "state_label": "Overridden",
             "state": "🔄", "status": "OVERRIDDEN", "held": [], "broke": [{"name": "TCS", "pct": -0.6}],
             "override": "AI-services pricing", "override_evidenced": False,
             "reason_econ": "IT export tailwind swamped by AI-services pricing.",
             "reason": "dominated by stronger driver",
             "reliability": {"hit_rate_pct": 62.0, "n": 1300}},         # hist 62% but broke today => divergence
            {"edge": "Groww launches copper trading platform → metals", "level": "Sector",
             "state_label": "Overridden", "state": "🔄", "status": "OVERRIDDEN",
             "held": [], "broke": [{"name": "Tata Steel", "pct": 0.1}],
             "reason_econ": "background item", "reason": "n/a"},        # background-news leak
        ],
        "transmission": [{"chain": ["Fed cut", "USD up"]}],             # economic inconsistency
        "theme": "Fed cut expected; USD up on the day",
    }


def _report():
    return ("## Executive Summary\n- Market is Bullish today and the rally will definitely continue.\n"
            "- Banks look Bearish.\n")   # bare directional calls + 'will definitely'


def main() -> int:
    mio, rep = _synthetic_mio(), _report()
    sc = evaluator.evaluate(mio, rep, client=None)     # deterministic-only

    det = sc["deterministic"]
    # L4 contradiction: bias vs forecast (high) + Fed-cut→USD-up inconsistency
    cons = det["contradiction"]["contradictions"]
    assert any(c["severity"] == "high" for c in cons), "should catch bias/forecast contradiction"
    assert any("USD" in c["message"] or "Fed" in c["message"] for c in cons), "should catch Fed-cut→USD sign error"

    # L5 coverage: core sectors missing
    assert det["coverage"]["missing_core"], "should report missing core sectors"

    # L6 confidence: reported 0.90 far above recommended => too high
    assert det["confidence"]["verdict"] == "too high", det["confidence"]

    # L8 probability: bull/bear split present
    assert det["probability"]["bull_pct"] is not None

    # L11 historical: the 62% relationship broke today => divergence
    assert det["historical"]["diverged"], "should flag historical divergence"

    # L12 news quality: 'groww' background leak flagged
    assert det["news_quality"]["flagged_background"], "should flag background news"

    # L14 language: over-certain term + bare directional calls
    assert det["report_language"]["overcertain_terms"], "should flag 'will definitely'"
    assert det["report_language"]["bare_directional_calls"] >= 1

    # LLM all skipped (deterministic mode)
    assert all(r.get("score") is None for r in sc["llm"].values()), "LLM evals should skip offline"

    # overall + feedback + render
    assert isinstance(sc["overall_quality"]["score"], (int, float))
    assert sc["feedback"], "should produce advisory feedback"
    md = render.render(sc)
    assert "Reasoning-Quality Scorecard" in md and "Feedback" in md

    print("overall:", sc["overall_quality"]["score"], sc["overall_quality"]["grade"])
    print("feedback items:", len(sc["feedback"]))
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
