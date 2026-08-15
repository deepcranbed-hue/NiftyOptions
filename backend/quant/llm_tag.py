"""
llm_tag.py
----------
Provider-agnostic tagging. Reads llm_config to decide WHICH LLM to call, handles
both request styles (Gemini contents[] vs OpenAI/Qwen messages[]), and walks the
fallback chain on failure — finally falling back to the keyword heuristic so the
pipeline NEVER blocks. Records which provider actually ran (for provenance).

Swap models by editing llm_config.ACTIVE_PROVIDER (or env LLM_PROVIDER) — this
file does not change.
"""
from __future__ import annotations
import asyncio, json, os, re
import httpx

from .llm_config import active, fallback_providers, LLMProvider

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from entity_extract import extract_constituents, weighted_sector_hits
from .sector_map import CANONICAL_SECTORS
from .news_provenance import sector_keyword_hits, cluster_signature

# Expanded lexicon for the degraded keyword fallback (word-boundary matched).
_POS_RE = re.compile(
    r"\b(beats?|surges?|jumps?|record (?:high|profit|revenue|quarter)|upgrades?|rises?|gains?|soars?|rall(y|ies)|"
    r"rebounds?|climbs?|advances?|higher|outperforms?|multibagger|hits? (?:52-week )?high)\b", re.I)
_NEG_RE = re.compile(
    r"\b(miss(?:es)?|falls?|plunges?|downgrades?|cuts?|slumps?|slides?|slips?|"
    r"declines?|drops?|tumbles?|sinks?|crash(?:es)?|lower|loss(?:es)?|plummets?|dips?|hits? (?:52-week )?low)\b", re.I)

try:
    import spacy
    nlp = spacy.load("en_core_web_sm")
except ImportError:
    nlp = None

# Authoritative grading rubric — sent as a SYSTEM message on every scoring call
# (system role for OpenAI-style providers incl. Qwen/Ollama; systemInstruction for
# Gemini). This is the single source of truth for HOW sentiment is graded, so the
# scale is applied consistently no matter which article batch is in the user turn.
SYSTEM_PROMPT = (
    "You are a financial news tagger for an Indian-equity (NIFTY 50) system. You grade "
    "each article's sentiment as its expected effect on INDIAN equities.\n"
    "SENTIMENT GRADING SCALE — use the full continuous range in 0.1 increments "
    "(-1.0, -0.9, ..., 0.0, ..., 0.9, 1.0). Do NOT default to coarse -1, -0.5, 0, 0.5, 1 steps.\n"
    "  |0.9|-|1.0|  exceptional, market-moving surprises only\n"
    "  |0.5|-|0.8|  clear directional news (earnings beat/miss, rating action, guidance change)\n"
    "  |0.2|-|0.4|  mild or routine (in-line results, ordinary board meetings, sector drift)\n"
    "   0.0        purely factual/neutral, or direction genuinely unknown\n"
    "Reserve the extremes; most news lands in |0.2|-|0.6|. When the sign is genuinely "
    "ambiguous (e.g. a fund-raising intimation that could be dilutive or growth-positive), "
    "stay near 0.0 rather than guessing a direction.\n"
    "Return ONLY valid JSON exactly as the user instructs — no markdown, no prose, no code fences."
)

TAG_PROMPT = f"""You are a financial news tagger for an Indian-equity (NIFTY 50) system.
You will receive a JSON array of articles.
Return ONLY a JSON array of objects (no markdown, no prose, no code fences), one for each article.
Each object MUST echo the exact "id" of the article.
Each object MUST have:
{{
  "id": <int>,
  "sentiment": <float -1.0..1.0 in steps of 0.1, e.g., -1.0, -0.9, -0.8 ... 0.0 ... 0.9, 1.0>,
  "sectors_affected": [<subset of {CANONICAL_SECTORS}>],
  "confidence": <float 0.0..1.0>,
  "event_code": <str or null>,            // e.g. "IN_CPI", "RBI_MPC", "US_FOMC", "US_CPI", "US_NFP"
  "event_consensus": <str or null>,
  "us_factor": <str or null>,             // "inflation", "fed", "jobs", "oil", "us_tech", "vix"
  "surprise_direction": <str or null>,
  // ONLY if the article is a company earnings result:
  "earnings": <"beat" | "miss" | "inline">,
  "earnings_company": <str>
}}
Rules:
- sentiment is effect on INDIAN equities. Use a fine-grained scale in 0.1 increments (e.g., -1.0, -0.9, ..., 0.0, ..., 0.9, 1.0) to represent the exact strength of the impact. Do not limit yourself to coarse -1, -0.5, 0, 0.5, 1 steps.
- sectors_affected MUST be drawn ONLY from this exact list: {CANONICAL_SECTORS}.
Articles:
"""


def _parse(text: str) -> list[dict]:
    """Robust JSON array extraction (handles ```json fences / stray prose)."""
    if not text:
        return []
    s = re.sub(r"```json|```", "", text).strip()
    m = re.search(r"\[.*\]", s, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else []
    except Exception:
        return []


def _fallback(article: dict) -> dict:
    """Keyword heuristic — the final, always-available tagger (never an LLM).
    Now scores off a much wider lexicon AND emits coarse sectors from keywords,
    so degraded mode is informative instead of near-empty."""
    t = f"{article.get('title','')} {article.get('description','')}"
    pos = sum(1 for _ in _POS_RE.finditer(t))
    neg = sum(1 for _ in _NEG_RE.finditer(t))
    sent = 0.3 * (pos - neg)
    earn = "beat" if "beat" in t.lower() else "miss" if "miss" in t.lower() else None
    out = {"sentiment": max(-1.0, min(1.0, sent)),
           "sectors_affected": sector_keyword_hits(t),
           "confidence": 0.3, "_provider": "keyword_fallback"}
    if earn:
        out["earnings"] = earn
    return out


def _build_request(p: LLMProvider, prompt: str):
    """Return (url, params, headers, json_body) for the provider's request style."""
    key = os.getenv(p.api_key_env) if p.api_key_env else None
    headers = dict(p.extra_headers)
    params = {}
    if p.auth_style == "bearer" and key:
        headers["Authorization"] = f"Bearer {key}"
    elif p.auth_style == "query_key" and key:
        params["key"] = key

    if p.request_style == "gemini":
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "generationConfig": {"temperature": 0.0,
                                     "responseMimeType": "application/json"}}
    else:  # openai-compatible (Qwen, OpenRouter, local)
        body = {"model": p.model, "temperature": 0.0,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}]}
    return p.endpoint, params, headers, body


def _extract_text(p: LLMProvider, data: dict) -> str:
    """Pull the text out of either response shape."""
    try:
        if p.request_style == "gemini":
            return data["candidates"][0]["content"]["parts"][0]["text"]
        return data["choices"][0]["message"]["content"]
    except Exception:
        return ""


async def _tag_batch_chunk(client, chunk: list[dict], p: LLMProvider) -> list[dict]:
    """Tag a chunk of articles. Returns parsed dicts matched by id."""
    payload_articles = [{"id": a["id"], "title": a.get("title", ""), "body": a.get("description", "")} 
                        for a in chunk]
    prompt = TAG_PROMPT + json.dumps(payload_articles)
    
    url, params, headers, body = _build_request(p, prompt)
    r = await client.post(url, params=params, headers=headers, json=body, timeout=45)
    r.raise_for_status()
    
    parsed_array = _parse(_extract_text(p, r.json()))
    if not isinstance(parsed_array, list) or len(parsed_array) != len(chunk):
        raise ValueError(f"Array length mismatch: expected {len(chunk)}, got {len(parsed_array) if isinstance(parsed_array, list) else 'non-list'}")
        
    # Match tags by id to guarantee alignment safety
    tag_map = {item.get("id"): item for item in parsed_array if isinstance(item, dict) and "id" in item}
    
    results = []
    for a in chunk:
        tag = tag_map.get(a["id"])
        if not tag:
            raise ValueError(f"Missing id {a['id']} in LLM response")
        
        tag["_provider"] = p.name
        tag.setdefault("confidence", 0.8)
        # Filter sectors to canonical list
        if "sectors_affected" in tag and isinstance(tag["sectors_affected"], list):
            tag["sectors_affected"] = [s for s in tag["sectors_affected"] if s in CANONICAL_SECTORS]
            
        results.append({**a, **tag})
        
    return results


async def tag_batch(articles: list[dict], concurrency: int = 5, batch_size: int = 10) -> list[dict]:
    if not articles:
        return []
        
    # Inject IDs internally for alignment safety
    for i, a in enumerate(articles):
        if "id" not in a:
            a["id"] = i
            
    # Chunk articles
    chunks = [articles[i:i + batch_size] for i in range(0, len(articles), batch_size)]
    
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(concurrency)
        
        async def process_chunk(chunk):
            async with sem:
                # 1. Fallback chain for the chunk
                for p in fallback_providers():
                    try:
                        # If local, we can do batch_size=1 inside to be completely safe? 
                        # But for now, we just chunk it normally
                        return await _tag_batch_chunk(client, chunk, p)
                    except Exception as e:
                        print(f"[{p.name}] Batch tagging failed for {len(chunk)} articles: {e}")
                        continue
                
                # 2. Keyword fallback if all LLMs fail
                return [{**a, **_fallback(a)} for a in chunk]
                
        chunk_results = await asyncio.gather(*(process_chunk(c) for c in chunks))
        
    # Flatten results
    results = [item for sublist in chunk_results for item in sublist]
    
    # ── entity extraction overlay ─────────────────────────────────────────────
    # Ensures constituents exist for the index attribution & earnings momentum logic
    final_results = []
    for r in results:
        text = f"Title: {r.get('title','')}\nBody: {r.get('description','')}"
        hits = extract_constituents(text, nlp)
        r["constituents"] = [
            {"symbol": h.symbol, "sector": h.sector, "weight": h.weight, "matched_text": h.matched_text}
            for h in hits
        ]
        r["constituent_sector_hits"] = weighted_sector_hits(hits)
        # Cross-feed dedup key: same company + event class + day => one cluster,
        # so syndicated re-runs of one event stop being counted N times.
        r["cluster_id"] = cluster_signature(r.get("title", ""), r["constituents"],
                                            r.get("published_at", ""))
        final_results.append(r)

    # ── fundamental overlay ───────────────────────────────────────────────────
    # Grades an earnings print against the growth ALREADY IN THE PRICE rather than
    # against zero. Apollo reported PAT +38.4% and fell 3.49% because the multiple
    # assumed ~59%; the tagger's rubric alone would have scored that clearly positive.
    # Deliberately OUTSIDE the LLM path: the arithmetic runs even when every provider
    # is down and the keyword fallback produced the sentiment. Never allowed to break
    # the pipeline — a fundamentals failure degrades to "no hurdle", not "no news".
    try:
        from .news_fundamentals import enrich_articles
        enrich_articles(final_results)
    except Exception as e:
        print(f"  [fundamentals] overlay skipped: {type(e).__name__}: {e}")

    return final_results


def tag_batch_sync(articles, concurrency: int = 5):
    return asyncio.run(tag_batch(articles, concurrency))


if __name__ == "__main__":
    # offline demo: no keys -> everything falls to keyword heuristic, pipeline still runs
    arts = [{"title": "Infosys beats Q1 estimates on strong deal wins",
             "description": "margins expand"},
            {"title": "Tata Motors misses profit view as costs rise",
             "description": ""}]
    out = tag_batch_sync(arts)
    for o in out:
        print(f"  provider={o.get('_provider'):16} sent={o.get('sentiment'):+.2f} "
              f"earnings={o.get('earnings','-')} | {o['title'][:45]}")
