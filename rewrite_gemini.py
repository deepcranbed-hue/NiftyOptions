import re

content = """
\"\"\"
gemini_tag.py
-------------
Backend per-article tagger. Replaces the frontend keyword counter.
\"\"\"
from __future__ import annotations
import asyncio
import json
import os
import re
import hashlib
import sys

import httpx

from .sector_map import CANONICAL_SECTORS

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
try:
    from entity_extract import extract_constituents, weighted_sector_hits
except ImportError:
    def extract_constituents(text, nlp=None): return []
    def weighted_sector_hits(hits): return {}

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = (f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent")

_SYS = f\"\"\"You are a financial news tagger for an Indian-equity (NIFTY 50) system.
For the article, return ONLY a JSON object:
{{
  "sentiment": <float -1.0..1.0>,
  "sectors_affected": [<subset of {CANONICAL_SECTORS}>],
  "confidence": <float 0.0..1.0>
}}
Rules:
- sentiment is the effect on INDIAN equities.
- sectors_affected MUST be drawn ONLY from this exact list: {CANONICAL_SECTORS}.
- Consumer is for consumer durables (paints, retail, apparel). FMCG is for fast-moving consumer goods (soaps, cigarettes, packaged food).
- Map synonyms: "metal"/"steel"->Metals, "IT/tech"->IT, "oil & gas"->Energy.
- Do NOT invent numbers or sectors outside the list.
- If not market-relevant: sentiment 0.0, sectors_affected [].
Output the JSON object and nothing else.\"\"\"

def _parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*|```$", "", text, flags=re.I | re.M).strip()
    m = re.search(r"\\{.*\\}", text, flags=re.S)
    obj = json.loads(m.group(0) if m else text)
    sent = max(-1.0, min(1.0, float(obj.get("sentiment", 0.0))))
    secs = [s for s in obj.get("sectors_affected", []) if s in CANONICAL_SECTORS]
    conf = float(obj.get("confidence", 0.5))
    return {"sentiment": sent, "sectors_affected": secs, "confidence": conf}

def _fallback(text: str) -> dict:
    return {"sentiment": 0.0, "sectors_affected": [], "confidence": 0.3}

async def _tag_one(client: httpx.AsyncClient, article: dict, api_key: str) -> dict:
    title = article.get('title', '')
    desc = article.get('description', '')
    text = f"Title: {title}\\nBody: {desc}"
    
    # 1. Hashing for clustering
    norm_headline = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
    cluster_id = hashlib.sha256(norm_headline.encode()).hexdigest()[:16]
    
    # 2. Entity match
    hits = extract_constituents(f"{title} {desc}")
    constituents_data = [{"symbol": h.symbol, "sector": h.sector, "weight": h.weight} for h in hits]
    
    # 3. LLM call
    payload = {
        "system_instruction": {"parts": [{"text": _SYS}]},
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
    }
    
    try:
        r = await client.post(GEMINI_URL, params={"key": api_key}, json=payload, timeout=20)
        r.raise_for_status()
        out = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        tags = _parse(out)
        llm_provider = "gemini"
    except Exception:
        tags = _fallback(text)
        llm_provider = "fallback"
        
    has_entity = len(constituents_data) > 0
    has_gemini = len(tags.get("sectors_affected", [])) > 0
    
    if has_entity and has_gemini:
        match_source = "both"
    elif has_entity:
        match_source = "entity"
    elif has_gemini:
        match_source = "llm"
    else:
        match_source = "none"
        
    return {
        **article,
        "sentiment": tags.get("sentiment", 0.0),
        "sectors_affected": tags.get("sectors_affected", []), # Only used if entity match missing
        "confidence": tags.get("confidence", 0.5),
        "constituents": constituents_data,
        "llm_sectors_json": tags.get("sectors_affected", []),
        "match_source": match_source,
        "cluster_id": cluster_id,
        "llm_provider": llm_provider
    }

async def gemini_tag_batch(articles: list[dict], api_key: str | None = None, concurrency: int = 5) -> list[dict]:
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        api_key = "dummy" # We want to test logic even without a real key
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        async def bound(a):
            async with sem:
                return await _tag_one(client, a, api_key)
        return await asyncio.gather(*(bound(a) for a in articles))

def gemini_tag_batch_sync(articles, api_key=None, concurrency=5):
    return asyncio.run(gemini_tag_batch(articles, api_key, concurrency))

if __name__ == "__main__":
    sample = [{"title": "HDFC Bank Q1 profit beats estimates on strong loan growth", "description": ""}]
    res = gemini_tag_batch_sync(sample)
    print(res)
"""
with open("backend/quant/gemini_tag.py", "w") as f:
    f.write(content)
