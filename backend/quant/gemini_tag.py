"""
gemini_tag.py
-------------
Backend per-article tagger. Replaces the frontend keyword counter
(classifyHeadline in src/lib/analytics.ts): the React layer should STOP scoring
and just render what this returns.

Each article dict gains two keys the pipeline needs:
    sentiment        : float in [-1, +1]  (impact on Indian equities)
    sectors_affected : list[str] drawn ONLY from CANONICAL_SECTORS

Design:
* strict JSON out of Gemini (no prose, no fences); parsed defensively.
* CLOSED sector enum -> stable keys that join to index weights (no "Metal" vs
  "Metals" drift, no orphan sectors).
* async + bounded concurrency for a batch of headlines.
* keyword FALLBACK so a failed/rate-limited LLM call degrades gracefully
  instead of zeroing the whole batch.

Env: set GEMINI_API_KEY. Model string is configurable below.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

from .sector_map import CANONICAL_SECTORS

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              f"{GEMINI_MODEL}:generateContent")

_SYS = f"""You are a financial news tagger for an Indian-equity (NIFTY 50) system.
For the article, return ONLY a JSON object (no markdown, no prose, no code fences):
{{
  "sentiment": <float -1.0..1.0>,        // impact on INDIAN equities: -1 bearish, +1 bullish, 0 neutral
  "sectors_affected": [<subset of {CANONICAL_SECTORS}>],
  "confidence": <float 0.0..1.0>
}}
Rules:
- sentiment is the effect on INDIAN equities, not the foreign market itself
  (a KOSPI crash is bearish for India via IT/risk-appetite -> negative).
- sectors_affected MUST be drawn ONLY from this exact list: {CANONICAL_SECTORS}.
  Map synonyms: "metal"/"steel"->Metals, "bank"/"banking"->Banks, "IT/tech"->IT,
  "oil & gas"->Energy, "realty/real estate"->Realty, "FMCG/consumer"->FMCG.
- Do NOT invent numbers or sectors outside the list.
- If not market-relevant: sentiment 0.0, sectors_affected [].
Output the JSON object and nothing else."""


# ── strict JSON parse ─────────────────────────────────────────────────────────
def _parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):                       # strip stray fences
        text = re.sub(r"^```[a-z]*|```$", "", text, flags=re.I | re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)        # grab the JSON object
    obj = json.loads(m.group(0) if m else text)
    sent = max(-1.0, min(1.0, float(obj.get("sentiment", 0.0))))
    secs = [s for s in obj.get("sectors_affected", []) if s in CANONICAL_SECTORS]
    conf = float(obj.get("confidence", 0.5))
    return {"sentiment": sent, "sectors_affected": secs, "confidence": conf}


# ── keyword fallback (graceful degradation, never zeroes the batch silently) ──
_BULL = ("rally", "jumps", "surges", "gains", "rises", "rebound", "reclaim",
         "higher", "up", "power", "boost", "record", "outperform")
_BEAR = ("falls", "slides", "drag", "plunge", "meltdown", "sell-off", "selloff",
         "weak", "lower", "breaches", "tumble", "crash", "slump", "decline")
_SECT_KW = {
    "IT": ("it ", "infosys", "tcs", "wipro", "tech mahindra", "accenture"),
    "Banks": ("bank", "banking", "hdfc", "icici", "sbi", "axis", "kotak"),
    "Metals": ("metal", "steel", "tata steel", "jsw", "hindalco"),
    "Auto": ("auto", "maruti", "m&m", "mahindra", "eicher", "tata motors"),
    "Energy": ("oil", "gas", "reliance", "ongc", "crude", "brent"),
    "Pharma": ("pharma", "sun pharma", "cipla", "dr reddy"),
    "Telecom": ("airtel", "bharti", "telecom", "vodafone"),
    "FMCG": ("fmcg", "hindustan unilever", "hul", "itc", "nestle"),
}


def _fallback(text: str) -> dict:
    t = text.lower()
    score = sum(w in t for w in _BULL) - sum(w in t for w in _BEAR)
    sent = max(-1.0, min(1.0, score / 3.0))
    secs = [s for s, kws in _SECT_KW.items() if any(k in t for k in kws)]
    return {"sentiment": sent, "sectors_affected": secs, "confidence": 0.3}


# ── single + batch tagging ────────────────────────────────────────────────────
async def _tag_one(client: httpx.AsyncClient, article: dict, api_key: str) -> dict:
    text = f"Title: {article.get('title','')}\nBody: {article.get('description','')}"
    payload = {
        "system_instruction": {"parts": [{"text": _SYS}]},
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
    }
    try:
        r = await client.post(GEMINI_URL, params={"key": api_key}, json=payload,
                              timeout=20)
        r.raise_for_status()
        out = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        tags = _parse(out)
    except Exception:
        tags = _fallback(text)                        # degrade, don't zero
    return {**article, **tags}


async def gemini_tag_batch(articles: list[dict], api_key: str | None = None,
                           concurrency: int = 5) -> list[dict]:
    """Tag a batch with bounded concurrency. Returns articles + sentiment +
    sectors_affected (+ confidence). Use this from the FastAPI endpoint."""
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return [{**a, **_fallback(f"{a.get('title','')} {a.get('description','')}")}
                for a in articles]
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        async def bound(a):
            async with sem:
                return await _tag_one(client, a, api_key)
        return await asyncio.gather(*(bound(a) for a in articles))


# sync convenience for non-async callers / quick tests
def gemini_tag_batch_sync(articles, api_key=None, concurrency=5):
    return asyncio.run(gemini_tag_batch(articles, api_key, concurrency))


if __name__ == "__main__":   # offline: no key -> exercises the fallback path
    sample = [
        {"title": "Sensex jumps 790, banking and IT stocks power rally",
         "description": "Benchmarks rallied led by financials and IT."},
        {"title": "Nifty falls as IT and metal stocks drag markets",
         "description": "IT majors and steel names slid."},
        {"title": "Ahead of Market: 10 things to watch", "description": ""},
    ]
    for a in gemini_tag_batch_sync(sample):
        print(f"  sent {a['sentiment']:+.2f}  conf {a['confidence']:.1f}  "
              f"sectors {a['sectors_affected']}  | {a['title'][:45]}")
