import os
import json
import requests
from duckduckgo_search import DDGS
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import math
from backend.quant.pipeline import run_pipeline
from google import genai
from async_lru import alru_cache
from backend.quant.rss_news import fetch_rss, GLOBAL_FEEDS
from backend.quant.news_window import prepare_articles
from backend.quant.gemini_tag import gemini_tag_batch

app = FastAPI()

# Enable CORS for the frontend dev server just in case, though Vite proxy handles it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Gemini Client lazily or at startup
def get_ai_client():
    api_key = os.getenv("GEMINI_API_KEY", "AIzaSyCZBVv9LFOyb7nA7i2anvyivYKkYvTPLyk")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY environment variable is not configured.")
    return genai.Client(api_key=api_key)

class NewsHeadline(BaseModel):
    id: str
    time: str
    headline: str
    impact: str
    tags: list[str]
    rawText: str

from typing import Optional

class PipelineRequest(BaseModel):
    chain: dict
    prev_regime: Optional[str] = None
    half_life_hours: float = 12.0
    log_harness: bool = False
    expiry: str = ""
    risk_cfg: Optional[dict] = None
    book: Optional[list[dict]] = None
    current_drawdown_pct: float = 0.0
    trade_max_loss_pts: float = 0.0
    trade_delta: float = 0.0
    trade_vega: float = 0.0
    override_structure: Optional[str] = None
    override_is_premium_sell: bool = False
    force_news_refresh: bool = False

import harness

class DeskAnalysisRequest(BaseModel):
    chainRows: list
    spot: float
    maxPain: float
    pcr: float
    complacencyScore: float
    complacencyVerdict: dict
    globalCues: dict
    newsSentiment: dict
    traderOutlook: str
    capital: float

@app.post("/api/analyze-desk")
async def analyze_desk(req: DeskAnalysisRequest):
    try:
        client = get_ai_client()

        diff_sign = "+" if req.maxPain - req.spot > 0 else ""
        diff_val = round(req.maxPain - req.spot)
        
        prompt = f"""You are the Chief Quantitative Derivatives Strategist at an institutional Nifty 50 options desk.
Analyze the following live NIFTY options positioning chain, complacency gauge, global macroeconomic cues, and sector news sentiment.

=== CURRENT MARKET METRICS ===
• Estimated Nifty Spot: ₹{req.spot}
• Max Pain Strike: ₹{req.maxPain} (Diff: {diff_sign}{diff_val})
• Put-Call Ratio (OI): {req.pcr}
• Complacency Score: {req.complacencyScore}/100 ({req.complacencyVerdict.get('tone')}: {req.complacencyVerdict.get('msg')})
• Trader Outlook Input: {req.traderOutlook}
• Available Capital: ₹{req.capital}

=== GLOBAL MACRO CUES ===
{req.globalCues}

=== NET SECTOR SENTIMENT ===
{req.newsSentiment}

=== TOP OPTION CHAIN STRIKES (Sampled around spot) ===
{req.chainRows[:15]}

Provide a sharp, institutional trading desk memo formatted in crisp Markdown with the following sections:
1. **Executive Market Structure**: Immediate take on writer positioning, PCR tilt, and max pain gravity.
2. **Vol Complacency & Tail Risk**: Are option writers crowding cheap vol? Is owning optionality favored over selling?
3. **Sector & Global Interplay**: How US/Asian macro moves connect with today's domestic sector sentiment.
4. **Optimal Position Recommendations**: Suggest 2 exact option strategies (e.g. Iron Condor, Call Spread, Strangle) with recommended Nifty strike prices (rounded to 50s), DTE guidance, and risk/reward rationale.
5. **Desk Defense & Greeks Hedging**: Concrete rules for managing tested wings or delta spikes.

Keep the tone professional, objective, institutional, and actionable. Note that this is quantitative desk analysis, not retail financial advice."""

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )

        return {"success": True, "analysis": response.text}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def fetch_nse_option_chain():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }
    session = requests.Session()
    # Step 1: Hit main page to get cookies
    try:
        session.get("https://www.nseindia.com/option-chain", headers=headers, timeout=10)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to establish session with NSE: {str(e)}")
        
    # Step 2: Fetch Option Chain API
    api_url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    try:
        response = session.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch option chain: {str(e)}")

@app.get("/api/fetch-chain")
def api_fetch_chain():
    return fetch_nse_option_chain()

@app.get("/api/health")
def health():
    import datetime
    return {"status": "ok", "time": datetime.datetime.now().isoformat()}

@app.get("/api/fetch-sector-news")
def fetch_sector_news():
    try:
        api_key = "419d303da0f84c3d9c3d8644efaaa5e7"
        url = f"https://newsapi.org/v2/everything?q=India+market+sensex+nifty+sector&sortBy=publishedAt&language=en&apiKey={api_key}"
        response = requests.get(url)
        response.raise_for_status()
        from backend.quant.news_window import prepare_articles
        data = response.json()
        raw_articles = data.get("articles", [])
        filtered = prepare_articles(raw_articles, max_age_hours=72.0)
        
        lines = []
        for a in filtered[:15]:
            ts = a.get("published_at")
            iso = ts.isoformat() if hasattr(ts, "isoformat") else ts
            lines.append(f"[{iso}] {a.get('title', '')}")
            
        return {"success": True, "news": "\n".join(lines)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/fetch-global-cues")
def fetch_global_cues(force_refresh: bool = False):
    try:
        cache_file = "global_cues_cache.json"
        if not force_refresh and os.path.exists(cache_file):
            with open(cache_file, "r") as f:
                return {"success": True, "cues": json.load(f)}

        raw_cues = fetch_rss(feeds=GLOBAL_FEEDS, per_feed=5)
        # Sort by publishedAt just in case, taking latest 15
        articles = sorted(raw_cues, key=lambda a: a.get('publishedAt', ''), reverse=True)[:15]
        news_text = "\n".join([f"{a.get('source', 'News')}: {a.get('title', '')} - {a.get('description', '')}" for a in articles])
        
        client = get_ai_client()
        prompt = f"""Extract the overnight closing percentage changes for the following global indices/commodities based on this recent news.
Return ONLY valid JSON with no markdown formatting or backticks.
Keys must exactly match: "S&P 500", "NASDAQ", "US 10Y Yield", "Dollar Index (DXY)", "Brent Crude", "Hang Seng".
Values should be floats (e.g., 1.2, -0.5). If not found, use 0.0.

News:
{news_text}"""
        res = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        text = res.text.replace("```json", "").replace("```", "").strip()
        parsed_data = json.loads(text)

        with open(cache_file, "w") as f:
            json.dump(parsed_data, f)

        return {"success": True, "cues": parsed_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from dataclasses import is_dataclass, asdict
def sanitize_floats(obj):
    import math
    if is_dataclass(obj):
        obj = asdict(obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return float(obj)
    elif type(obj).__name__ in ('float64', 'float32', 'float16'):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return val
    elif isinstance(obj, dict):
        return {k: sanitize_floats(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_floats(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(sanitize_floats(v) for v in obj)
    return obj

async def get_tagged_news(force_refresh: bool = False):
    cache_file = "sector_news_cache.json"
    if not force_refresh and os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            return json.load(f)

    raw = fetch_rss()
    windowed = prepare_articles(raw, max_age_hours=12.0)
    api_key = os.getenv("GEMINI_API_KEY", "AIzaSyCZBVv9LFOyb7nA7i2anvyivYKkYvTPLyk")
    tagged = await gemini_tag_batch(windowed, api_key=api_key)

    with open(cache_file, "w") as f:
        json.dump(tagged, f)

    return tagged

_prev_regime = {"v": None}

@app.post("/api/run-pipeline")
async def api_run_pipeline(req: PipelineRequest):
    try:
        tagged = await get_tagged_news(force_refresh=req.force_news_refresh)
        
        # Use provided prev_regime from frontend if any, else use server state
        prev = req.prev_regime if req.prev_regime is not None else _prev_regime["v"]
        
        res = run_pipeline(
            articles=tagged,
            chain=req.chain,
            prev_regime=prev,
            half_life_hours=req.half_life_hours,
            risk_cfg=req.risk_cfg,
            book=req.book,
            current_drawdown_pct=req.current_drawdown_pct,
            trade_max_loss_pts=req.trade_max_loss_pts,
            trade_delta=req.trade_delta,
            trade_vega=req.trade_vega,
            override_structure=req.override_structure,
            override_is_premium_sell=req.override_is_premium_sell
        )
        _prev_regime["v"] = res["regime"]["dominant"]
        
        safe_res = sanitize_floats(res)
        
        if req.log_harness:
            spot = req.chain.get("spot", 0.0)
            logged = harness.log_signal(safe_res, spot=spot, expiry=req.expiry)
            safe_res["harness_id"] = logged["id"]
            
        return {"success": True, "result": safe_res}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


class SettleRequest(BaseModel):
    signal_id: str
    realized_close: float

@app.post("/api/settle")
def api_settle(req: SettleRequest):
    try:
        hit = harness.settle(req.signal_id, req.realized_close)
        return {"success": True, "settled": hit}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/harness/eval")
def api_harness_eval():
    try:
        res = harness.evaluate()
        return {"success": True, "evaluation": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
