content = """
\"\"\"
sector_tagging.py
-----------------
Build sector sentiment DIRECTLY from Gemini's per-article output,
separating direct (entity-matched) from derived (LLM-macro) sentiment.
\"\"\"
from __future__ import annotations
import math
from collections import defaultdict
from datetime import datetime, timezone

LAMBDA_DIRECT = 0.5

def _decay(age_h, half_life=12.0):
    return math.exp(-math.log(2) * max(age_h, 0.0) / half_life)

def sector_sentiment_from_gemini(articles: list[dict], now=None, half_life_hours: float = 12.0) -> dict[str, dict]:
    now = now or datetime.now(timezone.utc)
    
    # 1. Cluster deduplication
    clusters = {}
    for a in articles:
        ts = a.get("published_at")
        dt = datetime.fromisoformat(ts) if isinstance(ts, str) else (ts or now)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        cid = a.get("cluster_id", a.get("title", "")) # Fallback to title if cluster_id missing
        if cid not in clusters:
            clusters[cid] = {"article": a, "earliest_dt": dt}
        else:
            if dt < clusters[cid]["earliest_dt"]:
                clusters[cid]["earliest_dt"] = dt
                clusters[cid]["article"] = a
    
    deduped_articles = [c for c in clusters.values()]
    
    # Storage for components
    direct_num = defaultdict(float)
    direct_den = defaultdict(float)
    direct_n = defaultdict(int)
    direct_cov = defaultdict(float)
    
    derived_num = defaultdict(float)
    derived_den = defaultdict(float)
    derived_n = defaultdict(int)
    
    for c in deduped_articles:
        a = c["article"]
        dt = c["earliest_dt"]
        w = _decay((now - dt).total_seconds() / 3600.0, half_life_hours)
        s = float(a.get("sentiment", 0.0))
        
        entity_hits = a.get("constituent_sector_hits", {})
        gemini_sectors = set(a.get("sectors_affected", []) or [])
        
        # 1. Apply entity-weighted hits (DIRECT)
        for sec, wt in entity_hits.items():
            eff_w = w * wt
            direct_num[sec] += eff_w * s
            direct_den[sec] += eff_w
            direct_n[sec] += 1
            direct_cov[sec] += wt
            
        # 2. Apply LLM macro hits (DERIVED) for sectors not covered by entity match
        for sec in gemini_sectors:
            if sec not in entity_hits:
                derived_num[sec] += w * s
                derived_den[sec] += w
                derived_n[sec] += 1
                
    # Combine
    all_sectors = set(direct_den.keys()) | set(derived_den.keys())
    out = {}
    
    try:
        from backend.quant.sector_map import sector_weights
        sw = sector_weights()
    except Exception:
        sw = defaultdict(float)
        
    for sec in all_sectors:
        d_val = direct_num[sec] / direct_den[sec] if direct_den[sec] > 0 else 0.0
        r_val = derived_num[sec] / derived_den[sec] if derived_den[sec] > 0 else 0.0
        
        n_dir = direct_n[sec]
        n_der = derived_n[sec]
        
        if n_dir > 0 and n_der > 0:
            combined = LAMBDA_DIRECT * d_val + (1 - LAMBDA_DIRECT) * r_val
            flag = "both"
        elif n_dir > 0:
            combined = d_val
            flag = "direct_only"
        else:
            combined = r_val
            flag = "derived_only"
            
        cov_pct = direct_cov[sec] / sw.get(sec, 1.0) if sw.get(sec, 0.0) > 0 else 0.0
        
        out[sec] = {
            "combined": combined,
            "direct": d_val,
            "derived": r_val,
            "direct_n": n_dir,
            "derived_n": n_der,
            "coverage": round(min(1.0, cov_pct), 4),
            "flag": flag,
            "lambda": LAMBDA_DIRECT,
            "as_of": "2026-07-02"
        }
        
    return out

if __name__ == "__main__":
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    arts = [
        {"title": "Sensex jumps 790, banking and IT power rally",
         "published_at": "2026-06-25T04:00:00+00:00", "sentiment": 0.7,
         "sectors_affected": ["Financials", "IT"],
         "constituent_sector_hits": {"Financials": 6.29},
         "cluster_id": "c1"},
        {"title": "Indian, global stocks weak as AI-heavy Korea plunges 10%",
         "published_at": "2026-06-23T13:00:00+00:00", "sentiment": -0.7,
         "sectors_affected": ["IT"],
         "cluster_id": "c2"}
    ]
    res = sector_sentiment_from_gemini(arts, now=now)
    import json
    print("sector sentiment:", json.dumps(res, indent=2))
"""
with open("backend/quant/sector_tagging.py", "w") as f:
    f.write(content)
