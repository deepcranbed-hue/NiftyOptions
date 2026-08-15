import asyncio
from datetime import datetime, timezone
from backend.quant.gemini_tag import gemini_tag_batch_sync
from backend.quant.sector_tagging import sector_sentiment_from_gemini
from backend.quant.decision_engine import decide

arts = [
    {"title": "HDFC Bank reports strong earnings, boosts sector", "description": "Bank beats estimates.", "published_at": "2026-07-02T10:00:00Z"},
    {"title": "IT index falls on global macro headwinds", "description": "Tech stocks slide.", "published_at": "2026-07-02T09:00:00Z"},
    {"title": "RBI cuts rates unexpectedly", "description": "Markets rally broadly across sectors.", "published_at": "2026-07-02T11:00:00Z"}
]

tagged = gemini_tag_batch_sync(arts)
# simulate gemini tag output for RBI cut as it falls back to 0 without API key
for t in tagged:
    if "RBI" in t["title"]:
        t["sentiment"] = 0.8
        t["sectors_affected"] = ["Financials", "Automobile", "Infrastructure & Capital Goods"]
        
sector_stats = sector_sentiment_from_gemini(tagged, now=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc))

import json
print("SECTOR STATS:\n", json.dumps(sector_stats, indent=2))

decision = decide(sector_stats, vol_expansion=True, regime_conviction=0.7)
print("DECISION:\n", json.dumps(decision, indent=2))
