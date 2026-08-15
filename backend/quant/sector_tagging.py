"""
sector_tagging.py
-----------------
Build sector sentiment DIRECTLY from Gemini's per-article output,
separating direct (entity-matched) from derived (LLM-macro) sentiment.
Now includes 3-level drill-down for attribution.
"""
from __future__ import annotations
import math
import re
from collections import defaultdict
from datetime import datetime, timezone

from backend.quant.news_provenance import source_tier, scan_batch, weighted_median

LAMBDA_DIRECT = 0.5

def _decay(age_h, half_life=12.0):
    return math.exp(-math.log(2) * max(age_h, 0.0) / half_life)

def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))

def sector_sentiment_from_gemini(articles: list[dict], now=None, half_life_hours: float = 12.0) -> dict[str, dict]:
    now = now or datetime.now(timezone.utc)

    # Guardrail 0: pre-LLM ingest scan. Quarantine (don't drop) injection/junk.
    # Safe to run here too as defence-in-depth even if already run upstream.
    articles, quarantined = scan_batch(articles)

    clusters = {}
    for a in articles:
        ts = a.get("published_at")
        ts_clean = ts.replace("Z", "+00:00") if isinstance(ts, str) else ts
        dt = datetime.fromisoformat(ts_clean) if isinstance(ts_clean, str) else (ts_clean or now)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        # Fall back to the SAME normalization gemini_tag uses to build cluster_id,
        # so the dedup layers agree instead of double-counting near-duplicates.
        cid = a.get("cluster_id")
        if not cid:
            cid = re.sub(r"[^a-z0-9 ]", "", (a.get("title", "") or "").lower()).strip()
        if cid not in clusters:
            clusters[cid] = {"article": a, "earliest_dt": dt}
        else:
            if dt < clusters[cid]["earliest_dt"]:
                clusters[cid]["earliest_dt"] = dt
                clusters[cid]["article"] = a
    
    deduped_articles = [c for c in clusters.values()]

    # Collect per-article (score, effective_weight) pairs per sector so we can use
    # a ROBUST estimator (weighted median) instead of a fragile weighted mean.
    direct_pairs = defaultdict(list)   # sec -> [(score, eff_w), ...]
    derived_pairs = defaultdict(list)
    direct_n = defaultdict(int)
    derived_n = defaultdict(int)
    direct_cov = defaultdict(float)    # raw index-weight coverage (tier-independent)
    tier_counts = defaultdict(int)
    clamped_n = 0

    for c in deduped_articles:
        a = c["article"]
        dt = c["earliest_dt"]
        w = _decay((now - dt).total_seconds() / 3600.0, half_life_hours)

        # Guardrail 1: source-trust tier. Live-blogs / PR wire contribute less;
        # exchange filings full. Kills the circular price-ticker feedback loop.
        tier, tmult = source_tier(a)
        tier_counts[tier] += 1

        # Guardrail 2: defensive clamp — never let an out-of-range score through,
        # even if an upstream tagger was bypassed.
        raw_s = float(a.get("sentiment", 0.0))
        s = _clamp(raw_s)
        if s != raw_s:
            clamped_n += 1

        constituents = a.get("constituents", [])
        gemini_sectors = set(a.get("sectors_affected", []) or [])

        # 1. Entity-weighted hits (DIRECT). ONE vote per article per sector
        # (constituent index-weights summed) so a headline naming several stocks
        # in a sector is a single observation, not N independent votes.
        sec_wt = defaultdict(float)
        for c_data in constituents:
            sec_wt[c_data["sector"]] += c_data["weight"]
        matched_sectors = set(sec_wt.keys())
        for sec, wt_sum in sec_wt.items():
            direct_pairs[sec].append((s, w * wt_sum * tmult))
            direct_n[sec] += 1
            direct_cov[sec] += wt_sum

        # 2. LLM macro hits (DERIVED) — sector level, no company attribution
        for sec in gemini_sectors:
            if sec not in matched_sectors:
                derived_pairs[sec].append((s, w * tmult))
                derived_n[sec] += 1

    all_sectors = set(direct_pairs.keys()) | set(derived_pairs.keys())
    out = {}
    
    try:
        from backend.quant.sector_map import sector_weights
        from backend.quant.sector_tree import WEIGHTS_AS_OF
        sw = sector_weights()
        weights_provenance = "PRIMARY"
    except Exception:
        sw = defaultdict(float)
        WEIGHTS_AS_OF = "unknown"
        weights_provenance = "UNAVAILABLE"   # coverage falls to 0.0 — flagged, not silent

    MIN_ARTICLES = 3     # below this a single item can dominate / flip the mean
    MIN_COVERAGE = 0.15  # below this the score rests on a thin slice of sector weight

    for sec in all_sectors:
        # ROBUST estimator: weighted median instead of weighted mean, so a single
        # injected / poisoned item can no longer swing (or flip) the sector.
        d_val = weighted_median(direct_pairs[sec]) if direct_pairs[sec] else 0.0
        r_val = weighted_median(derived_pairs[sec]) if derived_pairs[sec] else 0.0

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
        cov_round = round(min(1.0, cov_pct), 4)

        n_total = n_dir + n_der
        thin = n_total < MIN_ARTICLES or (weights_provenance == "PRIMARY" and cov_round < MIN_COVERAGE)

        # Audit: spread = disagreement among the raw scores feeding this sector.
        raw_scores = [v for v, _ in direct_pairs[sec]] + [v for v, _ in derived_pairs[sec]]
        spread = round(max(raw_scores) - min(raw_scores), 4) if raw_scores else 0.0

        out[sec] = {
            "combined": combined,
            "direct": d_val,
            "derived": r_val,
            "direct_n": n_dir,
            "derived_n": n_der,
            "coverage": cov_round,
            "flag": flag,
            "low_confidence": thin,           # True => one item can dominate; treat as soft signal
            "spread": spread,                 # max-min of contributing raw scores (disagreement)
            "estimator": "weighted_median",   # was weighted_mean (injection-fragile)
            "lambda": LAMBDA_DIRECT,
            "weights_as_of": WEIGHTS_AS_OF,   # real weights date (was hardcoded "2026-07-02")
            "weights_provenance": weights_provenance,
            "as_of": now.isoformat(),         # actual run time, not a frozen string
        }
        
    # Build hierarchical drilldown (tier- and clamp-consistent with the scores above)
    drilldown = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for c in deduped_articles:
        a = c["article"]
        s = _clamp(float(a.get("sentiment", 0.0)))
        dt = c["earliest_dt"]
        w = _decay((now - dt).total_seconds() / 3600.0, half_life_hours)
        _, tmult = source_tier(a)
        for c_data in a.get("constituents", []):
            sec = c_data["sector"]
            ind = c_data.get("industry") or "Unknown"
            canon = c_data["symbol"]
            wt = c_data["weight"]
            drilldown[sec][ind][canon] += w * wt * tmult * s

    out["__drilldown"] = {sec: {ind: dict(comps) for ind, comps in inds.items()} for sec, inds in drilldown.items()}

    # Run-level audit trail (provenance + what got excluded, never silent).
    out["__audit"] = {
        "as_of": now.isoformat(),
        "n_in": len(articles) + len(quarantined),
        "n_scored": len(deduped_articles),
        "n_quarantined": len(quarantined),
        "quarantined": quarantined,
        "n_clamped": clamped_n,
        "tier_counts": dict(tier_counts),
        "estimator": "weighted_median",
        "weights_as_of": WEIGHTS_AS_OF,
        "weights_provenance": weights_provenance,
    }

    return out

if __name__ == "__main__":
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    arts = [
        {"title": "Sensex jumps 790, banking and IT power rally",
         "published_at": "2026-06-25T04:00:00+00:00", "sentiment": 0.7,
         "sectors_affected": ["Financials", "Information Technology"],
         "constituents": [{"symbol": "HDFC Bank", "sector": "Financials", "industry": "Private Banks", "weight": 6.49}],
         "cluster_id": "c1"},
    ]
    res = sector_sentiment_from_gemini(arts, now=now)
    import json
    print("sector sentiment:", json.dumps(res, indent=2))
