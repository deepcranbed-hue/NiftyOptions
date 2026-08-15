content = """
\"\"\"
decision_engine.py
------------------
The v1 synthesis: turn weighted news sentiment into a directional bias score.
\"\"\"

from __future__ import annotations

import math
from collections import defaultdict

try:
    from .sector_map import sector_weights as get_sw, sector_of as get_so, weights as get_wt
except ImportError:
    from sector_map import sector_weights as get_sw, sector_of as get_so, weights as get_wt

# Calibration parameters
LOGIT_A = 0.0
LOGIT_B = 2.5

# Gates / bands
COVERAGE_MIN = 0.50
MIN_ARTICLES = 8
MAX_SINGLE_STORY_SHARE = 0.40
NEUTRAL_BAND = 0.15

def index_bias(sector_sentiment_stats: dict[str, dict], sw: dict[str, float] | None = None) -> tuple[float, float, dict]:
    sw = sw or get_sw()
    
    # 1. No-news rule: renormalize over covered sectors
    covered_sectors = [s for s in sector_sentiment_stats if s in sw and sector_sentiment_stats[s]["combined"] != 0.0 or sector_sentiment_stats[s]["direct_n"] > 0 or sector_sentiment_stats[s]["derived_n"] > 0]
    
    total_articles = sum(v["direct_n"] + v["derived_n"] for v in sector_sentiment_stats.values())
    covered_weight = sum(sw[s] for s in covered_sectors)
    total_weight = sum(sw.values()) or 1.0
    
    if covered_weight == 0:
        return 0.0, 0.0, {"error": "INSUFFICIENT COVERAGE", "articles": total_articles}
        
    bias = sum(sector_sentiment_stats[s]["combined"] * sw[s] for s in covered_sectors) / covered_weight
    
    # 2. Fan-out logging (stub for cluster contribution, requires raw articles to be perfectly exact, but we just log)
    stats = {
        "articles": total_articles,
        "covered_weight_pct": covered_weight / total_weight,
        "direct_n": sum(v["direct_n"] for v in sector_sentiment_stats.values()),
        "derived_n": sum(v["derived_n"] for v in sector_sentiment_stats.values()),
    }
    return bias, covered_weight / total_weight, stats

def prob_up(bias: float, a: float = LOGIT_A, b: float = LOGIT_B) -> float:
    return 1.0 / (1.0 + math.exp(-(a + b * bias)))

def conviction(bias: float, coverage: float, regime_conviction: float) -> float:
    return abs(bias) * coverage * (0.5 + 0.5 * regime_conviction)

STRUCTURE_MATRIX = {
    (True,  "bearish"): "Bear put spread / long puts (defined-risk long vol)",
    (True,  "bullish"): "Bull call spread / long calls (defined-risk long vol)",
    (True,  "neutral"): "Long straddle / strangle (pure long vol)",
    (False, "bearish"): "Bear call spread (sell premium above spot)",
    (False, "bullish"): "Bull put spread (sell premium below spot)",
    (False, "neutral"): "Iron condor (defined-risk premium sell)",
}

def decide(sector_sentiment_stats: dict[str, dict], *, vol_expansion: bool, regime_conviction: float, base_units: float = 1.0) -> dict:
    bias, coverage, stats = index_bias(sector_sentiment_stats)
    
    if coverage < COVERAGE_MIN or stats.get("articles", 0) < MIN_ARTICLES:
        return {
            "action": "STAND ASIDE",
            "reason": f"INSUFFICIENT COVERAGE: coverage {coverage:.0%} (min {COVERAGE_MIN:.0%}), articles {stats.get('articles', 0)} (min {MIN_ARTICLES})",
            "index_bias": round(bias, 3), "coverage": round(coverage, 3)
        }

    p_up = prob_up(bias)
    if bias < -NEUTRAL_BAND:
        direction = "bearish"
    elif bias > NEUTRAL_BAND:
        direction = "bullish"
    else:
        direction = "neutral"

    conv = conviction(bias, coverage, regime_conviction)
    structure = STRUCTURE_MATRIX[(vol_expansion, direction)]
    size_mult = round(min(conv, 1.0) * base_units, 2)

    return {
        "action": "TRADE",
        "structure": structure,
        "direction": direction,
        "vol_state": "expansion (long vol)" if vol_expansion else "range (short vol)",
        "index_bias": round(bias, 3),
        "coverage": round(coverage, 3),
        "conviction": round(conv, 3),
        "size_mult": size_mult,
        "prob_up_pseudo": round(p_up, 3),
        "articles": stats["articles"]
    }
"""
with open("backend/quant/decision_engine.py", "w") as f:
    f.write(content)
