# IMPORTANT: For any modifications to data connections or credential verification pathways,
# please refer to the NiftyOptions/dataconnection.md documentation.
import sys
import os
import json
import requests
# Add virtual environment site-packages to path so we can import exchange_calendars
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(__file__)), "scratch_scripts", "breeze_env", "lib", "python3.9", "site-packages"))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from duckduckgo_search import DDGS
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import math
from backend.quant.pipeline import run_pipeline
from google import genai
from async_lru import alru_cache
from backend.quant.rss_news import fetch_rss, GLOBAL_FEEDS
from backend.quant.filings import fetch_filings
from backend.quant.news_window import prepare_articles
from backend.quant.llm_tag import tag_batch as llm_tag_batch
from event_calendar import build_panel
from exchange_config import NIFTY_LOT_SIZE   # single source of truth for lot size
from backend.quant.global_cues import read_metals, run_global_cues_pipeline
from backend.quant.formulas import trace_metals, trace_flow, trace_bias, trace_complacency, trace_rnd, trace_sizing
from backend.quant.flows_fetcher import fetch_nse_cash_sync, fetch_amfi_sip_sync, fetch_sector_fpi_sync
from flows import flow_bias
import backend.quant.state_manager as state_manager
from backend.timeutil import to_db_ts, to_db_minute
from backend.timeutil import to_db_ts, to_db_minute

app = FastAPI()

# Enable CORS for the frontend dev server just in case, though Vite proxy handles it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data Agent HTTP surface (/api/data-agent/*) — button + natural-language commands.
try:
    from backend.data_agent_routes import router as _data_agent_router
    app.include_router(_data_agent_router)
except Exception as _e:  # pragma: no cover
    print(f"[DataAgent] routes not mounted: {_e}")

# AI-infrastructure theme view (/api/ai-infra-theme) — India-listed AI-infra
# beneficiaries; dataset lives in ai_infra_theme.json at the repo root.
try:
    from backend.ai_infra_routes import router as _ai_infra_router
    app.include_router(_ai_infra_router)
except Exception as _e:  # pragma: no cover
    print(f"[AIInfra] routes not mounted: {_e}")

# Sector Intelligence view (/api/sector-view/{sector}) — Nifty Bank first; IT/Financials later.
# Serves data_agent/fundamentals/bank_view.json; ?quotes=true refreshes live NSE prices.
try:
    from backend.sector_view_routes import router as _sector_view_router
    app.include_router(_sector_view_router)
except Exception as _e:  # pragma: no cover
    print(f"[SectorView] routes not mounted: {_e}")

# Nifty 50 scan (/api/nifty50-view) — returns + sector-relative pricing for all
# constituents; computed on demand from yfinance, cached 30 min in .state/.
try:
    from backend.nifty50_routes import router as _nifty50_router
    app.include_router(_nifty50_router)
except Exception as _e:  # pragma: no cover
    print(f"[Nifty50] routes not mounted: {_e}")

# Shock-Recovery view (/api/shock-recovery) — VIX-filtered mean-reversion dip-buy flagger.
try:
    from backend.shock_recovery_routes import router as _shock_recovery_router
    app.include_router(_shock_recovery_router)
except Exception as _e:  # pragma: no cover
    print(f"[ShockRecovery] routes not mounted: {_e}")

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
    opt_weights: Optional[dict] = None
    opt_bias: Optional[float] = None
    opt_min_pop: float = 0.0
    opt_allow_undefined: bool = False
    opt_cost_per_leg: float = 20.0
    opt_window_pts: int = 500
    opt_max_wing: int = 300
    opt_top_n: int = 6
    opt_max_loss_budget: float = 0.0
    opt_allow_bad_rnd: bool = False

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

        import httpx
        url = "http://localhost:11434/v1/chat/completions"
        body = {
            "model": "qwen2.5:7b",
            "messages": [
                {"role": "system", "content": "You are a professional financial strategy analyst."},
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=body, timeout=90)
            r.raise_for_status()
            response_text = r.json()["choices"][0]["message"]["content"]

        return {"success": True, "analysis": response_text}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def fetch_nse_option_chain(symbol="NIFTY"):
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
    symbol = symbol.upper().strip()
    if symbol in ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']:
        api_url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    else:
        api_url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
        
    try:
        response = session.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch option chain: {str(e)}")

@app.get("/api/fetch-chain")
def api_fetch_chain(symbol: str = "NIFTY"):
    return fetch_nse_option_chain(symbol)

@app.get("/api/fetch-breeze")
def api_fetch_breeze(session_token: str, expiry_date: str, symbol: str = "NIFTY"):
    import subprocess
    import json
    from backend.quant.breeze_loader import process_breeze_chain
    from datetime import datetime
    
    if not session_token or not expiry_date:
        raise HTTPException(status_code=400, detail="session_token and expiry_date are required")
        
    try:
        # Run isolated breeze script to comply with Strict Environment Isolation Rule
        breeze_symbol = BREEZE_SYMBOL_MAP.get(symbol.upper(), symbol)
        cmd = [
            "./scratch_scripts/breeze_env/bin/python",
            "scratch_scripts/fetch_breeze_json.py",
            session_token,
            expiry_date,
            breeze_symbol
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Breeze script failed: {result.stderr}")
            
        response = json.loads(result.stdout)
        
        if response.get("error"):
            raise HTTPException(status_code=500, detail=f"Breeze API Error: {response['error']}")
            
        if not response.get("Success"):
            raise HTTPException(status_code=500, detail=f"Breeze API returned no data: {response}")
            
        raw_data = response["Success"]
        
        # Calculate days to expiry
        exp_dt = datetime.fromisoformat(expiry_date.replace('Z', '+00:00'))
        now = datetime.now(exp_dt.tzinfo)
        diff = exp_dt - now
        days = max(diff.total_seconds() / 86400.0, 0.01)
        
        rows, spot_price = process_breeze_chain(raw_data, days_to_expiry=days)
        
        return {"success": True, "rows": rows, "spot": spot_price}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/backfill-breeze-historical")
def api_backfill_breeze_historical(session_token: str, expiry_date: str, symbol: str = "NIFTY", interval: str = "1hour", start_date: str = "", end_date: str = ""):
    import subprocess
    import json
    
    if not session_token or not expiry_date:
        raise HTTPException(status_code=400, detail="session_token and expiry_date are required")
        
    try:
        breeze_symbol = BREEZE_SYMBOL_MAP.get(symbol.upper(), symbol)
        cmd = [
            "./scratch_scripts/breeze_env/bin/python",
            "scratch_scripts/fetch_historical_option_chain.py",
            session_token,
            expiry_date,
            breeze_symbol,
            interval,
            start_date,
            end_date
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Historical backfiller failed: {result.stderr}")
            
        response = json.loads(result.stdout)
        if response.get("error"):
            raise HTTPException(status_code=500, detail=f"Backfiller Error: {response['error']}")
            
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class SyncAllRequest(BaseModel):
    breeze_session_token: str
    kite_access_token: str
    expiry_date: str
    symbol: str = "NIFTY"
    interval: str = "1minute"
    start_date: str = ""
    end_date: str = ""

@app.post("/api/sync-all-data")
def api_sync_all_data(req: SyncAllRequest):
    import subprocess
    import json
    
    # 1. Validate Breeze session token (via subprocess using custom get_customer_details endpoint)
    breeze_session_token = req.breeze_session_token
    breeze_token_sources = [("frontend", breeze_session_token)]
    
    try:
        from datetime import datetime
        import os
        today_str = datetime.now().strftime("%Y-%m-%d")
        breeze_session_file = f"breezesession/session_{today_str}.json"
        if os.path.exists(breeze_session_file):
            with open(breeze_session_file, "r") as bsf:
                bs_data = json.load(bsf)
                saved_breeze_token = bs_data.get("session_token")
                if saved_breeze_token and saved_breeze_token != breeze_session_token:
                    breeze_token_sources.append(("saved_session", saved_breeze_token))
    except Exception as bse:
        print(f"Failed to load saved Breeze session: {bse}")

    breeze_validation_success = False
    breeze_err_msg = ""
    
    for src, token in breeze_token_sources:
        if not token or len(token) < 5 or token == "undefined":
            continue
            
        breeze_check_code = f"""
from breeze_connect import BreezeConnect
try:
    breeze = BreezeConnect(api_key="999407AZb39Vu3D&9X405B977330807K")
    breeze.generate_session(api_secret="584F70+Z075364Cz35y6O9931Y16I387", session_token="{token}")
    res = breeze.get_customer_details(api_session="{token}")
    print("VALID")
except Exception as e:
    print(str(e))
"""
        cmd_breeze_val = ["./scratch_scripts/breeze_env/bin/python", "-c", breeze_check_code]
        breeze_val_res = subprocess.run(cmd_breeze_val, capture_output=True, text=True)
        if "VALID" in breeze_val_res.stdout:
            breeze_session_token = token
            breeze_validation_success = True
            
            # Save validated token to local cache file
            try:
                os.makedirs("breezesession", exist_ok=True)
                with open(breeze_session_file, "w") as bsf:
                    json.dump({"session_token": token, "validated_at": datetime.now().isoformat()}, bsf, indent=2)
            except Exception as bse:
                print(f"Failed to save validated Breeze session: {bse}")
            break
        else:
            breeze_err_msg = breeze_val_res.stdout.strip() or breeze_val_res.stderr.strip() or "Breeze connection test failed."

    if not breeze_validation_success:
        raise HTTPException(status_code=400, detail=f"Breeze Session Token is expired or invalid: {breeze_err_msg}")

    # 2. Validate Kite access token (via standard HTTP to bypass broken cryptography env packages)
    import urllib.request
    import urllib.error
    
    api_key = "x2ob63qqr9dhyj6o"
    req_url = "https://api.kite.trade/user/profile"
    
    kite_access_token = req.kite_access_token
    token_sources = [("frontend", kite_access_token)]
    
    # Add saved session token as fallback source
    try:
        from datetime import datetime
        import os
        today_str = datetime.now().strftime("%Y-%m-%d")
        session_file = f"zerodhasession/session_{today_str}.json"
        if os.path.exists(session_file):
            with open(session_file, "r") as sf:
                session_data = json.load(sf)
                saved_token = session_data.get("access_token")
                if saved_token and saved_token != kite_access_token:
                    token_sources.append(("saved_session", saved_token))
    except Exception as se:
        print(f"Failed to load saved session: {se}")

    validation_success = False
    last_err_msg = ""
    
    for src, token in token_sources:
        if not token or len(token) < 10 or token == "undefined":
            continue
        headers = {
            "X-Kite-Version": "3",
            "Authorization": f"token {api_key}:{token}"
        }
        try:
            http_req = urllib.request.Request(req_url, headers=headers)
            with urllib.request.urlopen(http_req, timeout=5) as response:
                kite_access_token = token # Use this validated token for subsequent steps!
                validation_success = True
                break
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            try:
                err_json = json.loads(err_body)
                last_err_msg = err_json.get("message", "Forbidden")
            except:
                last_err_msg = err_body
        except Exception as e:
            last_err_msg = str(e)
            
    # If Kite key is absent or invalid, skip Zerodha/commodity sync.
    skip_commodities = False
    if not validation_success:
        skip_commodities = True
        logs = ["Breeze session key validated. Kite token is invalid or absent; skipping Zerodha/commodities sync."]
    else:
        logs = ["Both Breeze and Kite session keys validated successfully."]
    
    # 3. Sync constituent stock levels
    logs.append("Syncing Nifty 50 constituent stocks price levels...")
    cmd_stocks = [
        "./data_agent/breeze_env/bin/python",
        "data_agent/fetching/sync_nifty50_to_now.py",
        breeze_session_token,
    ]
    stocks_res = subprocess.run(cmd_stocks, capture_output=True, text=True)
    if stocks_res.returncode != 0:
        logs.append(f"Constituents sync output: {stocks_res.stderr or stocks_res.stdout}")
    else:
        logs.append("Constituent stock levels synced successfully.")

    # 3.5 Sync 1m Nifty Futures
    logs.append("Syncing 1m Nifty Futures (NIFTY_FUT_1, NIFTY_FUT_2)...")
    import time
    time.sleep(3)
    cmd_futures = [
        "./data_agent/breeze_env/bin/python",
        "data_agent/fetching/download_nifty_futures.py",
        breeze_session_token
    ]
    fut_res = subprocess.run(cmd_futures, capture_output=True, text=True)
    if fut_res.returncode != 0:
        logs.append(f"Futures 1m sync output: {fut_res.stderr or fut_res.stdout}")
    else:
        logs.append("Nifty Futures 1m levels synced successfully.")

    # 4. Sync Commodity, Currency, and GIFT Nifty levels via Upstox
    logs.append("Syncing all commodity, currency, and GIFT Nifty levels (GOLD, SILVER, COPPER, CRUDEOIL, USDINR, GIFTNIFTY) via Upstox...")
    cmd_commodities = [
        "./data_agent/breeze_env/bin/python",
        "data_agent/fetching/sync_commodities.py"
    ]
    comm_res = subprocess.run(cmd_commodities, capture_output=True, text=True)
    if comm_res.returncode != 0:
        logs.append(f"Commodities sync output: {comm_res.stderr or comm_res.stdout}")
    else:
        logs.append("Commodity, currency, and GIFT Nifty price levels synced successfully via Upstox.")

    # 4.2 Sync Indian Index daily levels (NIFTY, BANKNIFTY) via yfinance
    logs.append("Syncing Indian Index daily levels (NIFTY 1d) via yfinance...")
    cmd_indices = [
        "./data_agent/breeze_env/bin/python",
        "data_agent/macro/download_india_indices.py"
    ]
    idx_res = subprocess.run(cmd_indices, capture_output=True, text=True)
    if idx_res.returncode != 0:
        logs.append(f"Index 1d sync output: {idx_res.stderr or idx_res.stdout}")
    else:
        logs.append("Indian Index 1d daily levels synced successfully.")

    # 4.5 Sync Futures and Option Contract bars via Data Agent
    logs.append("Syncing Futures and Option Contract bars via Data Agent...")
    try:
        from backend.data_agent_routes import _do_run, RunReq
        agent_req = RunReq(
            broker="breeze",
            token=breeze_session_token,
            api_key="999407AZb39Vu3D&9X405B977330807K",
            api_secret="584F70+Z075364Cz35y6O9931Y16I387",
            mode="fo",
            timeframe="1m"
        )
        import time
        time.sleep(3)
        agent_res = _do_run(agent_req)
        logs.append(f"Data Agent F&O Sync complete: {agent_res.get('saved_total', 0)} bars saved across {agent_res.get('targets', 0)} targets.")
    except Exception as da_err:
        import traceback
        traceback.print_exc()
        logs.append(f"Data Agent F&O Sync failed: {da_err}")

    # 4.8 Sync US Macro factors (US10Y, NASDAQ) from FRED to PostgreSQL
    logs.append("Syncing US Macro factors (US10Y, NASDAQ) from FRED to PostgreSQL...")
    try:
        import os
        from datetime import datetime, timedelta
        env_vars = os.environ.copy()
        if "DATABASE_URL" not in env_vars:
            env_vars["DATABASE_URL"] = "postgresql://localhost/niftyoptions"
        
        since_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        
        for factor_series in ["US10Y", "NASDAQ", "CRUDE"]:
            cmd_fred = [
                "scratch_scripts/breeze_env/bin/python",
                "data_agent/macro/us10y.py",
                "--series", factor_series,
                "--since", since_date
            ]
            fred_res = subprocess.run(cmd_fred, env=env_vars, capture_output=True, text=True)
            if fred_res.returncode != 0:
                logs.append(f"FRED {factor_series} sync warning: {fred_res.stderr or fred_res.stdout}")
        logs.append("US Macro factors synced successfully.")
    except Exception as fred_err:
        logs.append(f"US Macro factors sync failed: {fred_err}")

    # 4.8.5 Sync India macro factors (IN10Y_INDEX) to PostgreSQL
    logs.append("Syncing India Macro factors (IN10Y_INDEX) to PostgreSQL...")
    try:
        cmd_india = [
            "scratch_scripts/breeze_env/bin/python",
            "data_agent/macro/ingest_india_rates.py"
        ]
        india_res = subprocess.run(cmd_india, env=env_vars, capture_output=True, text=True)
        if india_res.returncode != 0:
            logs.append(f"India Macro factors sync warning: {india_res.stderr or india_res.stdout}")
        else:
            logs.append("India Macro factors synced successfully.")
    except Exception as india_err:
        logs.append(f"India Macro factors sync failed: {india_err}")

    # 4.9 Sync FII & DII daily flows from Upstox to PostgreSQL
    logs.append("Syncing FII & DII daily cash flows from Upstox to PostgreSQL...")
    try:
        cmd_flows = [
            "scratch_scripts/breeze_env/bin/python",
            "data_agent/macro/download_fii_dii.py"
        ]
        flows_res = subprocess.run(cmd_flows, env=env_vars, capture_output=True, text=True)
        if flows_res.returncode != 0:
            logs.append(f"FII/DII sync warning: {flows_res.stderr or flows_res.stdout}")
        else:
            logs.append("FII/DII daily cash flows synced successfully.")
    except Exception as flows_err:
        logs.append(f"FII/DII flow sync failed: {flows_err}")

    # 4.95 Sync US Tech Stocks & ADRs (ACN, CTSH, CRM, INFY_ADR) from yfinance to PostgreSQL
    logs.append("Syncing US Tech Stocks & ADRs from yfinance to PostgreSQL...")
    try:
        cmd_stocks_us = [
            "scratch_scripts/breeze_env/bin/python",
            "data_agent/macro/download_us_stocks.py",
            "--since", since_date
        ]
        stocks_us_res = subprocess.run(cmd_stocks_us, env=env_vars, capture_output=True, text=True)
        if stocks_us_res.returncode != 0:
            logs.append(f"US stocks sync warning: {stocks_us_res.stderr or stocks_us_res.stdout}")
        else:
            logs.append("US Tech Stocks & ADRs synced successfully.")
    except Exception as stocks_us_err:
        logs.append(f"US stocks sync failed: {stocks_us_err}")

    # 5. Sync Option Chain captures (linked to newly synced index levels)
    logs.append("Backfilling Option Chain captures (linking to spot index levels)...")
    
    expiries_to_sync = [req.expiry_date] if req.expiry_date else []
    
    # Auto-Rollover Logic: If the provided expiry is within 2 days from today, we also fetch the *next* expiry date
    if req.expiry_date:
        try:
            from datetime import datetime
            # req.expiry_date is expected to be "YYYY-MM-DD" or similar
            expiry_dt_str = req.expiry_date.split("T")[0]
            expiry_dt = datetime.strptime(expiry_dt_str, "%Y-%m-%d").date()
            today_dt = datetime.now().date()
            delta_days = (expiry_dt - today_dt).days
            
            if 0 <= delta_days <= 2:
                try:
                    expiries_data = api_exchange_expiries(req.symbol)
                    all_expiries = expiries_data.get("expiries", [])
                    for exp in all_expiries:
                        if exp[:10] > expiry_dt_str:
                            expiries_to_sync.append(exp)
                            logs.append(f"Expiry {req.expiry_date} is within 2 days. Added next expiry {exp} to auto-rollover sync list.")
                            break
                except Exception as ex:
                    logs.append(f"Failed to fetch next expiry for rollover: {ex}")
        except Exception as e:
            logs.append(f"Failed to calculate expiry delta for rollover: {e}")

    for exp_to_sync in expiries_to_sync:
        logs.append(f"Backfilling Option Chain for expiry {exp_to_sync}...")
        cmd_options = [
            "./data_agent/breeze_env/bin/python",
            "scratch_scripts/fetch_historical_option_chain.py",
            breeze_session_token,
            exp_to_sync,
            req.symbol,
            req.interval,
            req.start_date,
            req.end_date
        ]
        opt_res = subprocess.run(cmd_options, capture_output=True, text=True)
        if opt_res.returncode != 0:
            logs.append(f"Warning: Option Chain sync failed for {exp_to_sync}: {opt_res.stderr or opt_res.stdout}")
    # 6. Post-Sync Validation Audit
    logs.append("Running Post-Sync Data Validation Audit...")
    try:
        import sqlite3
        from datetime import datetime
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Audit SQLite
        lite_conn = sqlite3.connect("/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db")
        lite_cur = lite_conn.cursor()
        
        symbols_to_check = ["NIFTY", "NIFTYIT", "USDINR", "CRUDEOIL", "CRUDEOIL_MCX", "NIFTY_FUT_1", "NIFTY_FUT_2"]
        for sym in symbols_to_check:
            for tf in ["1d", "1m"]:
                lite_cur.execute("SELECT MAX(ts), COUNT(*) FROM price_bars WHERE symbol = ? AND timeframe = ?", (sym, tf))
                row = lite_cur.fetchone()
                max_ts, count = row if row else (None, 0)
                
                note = ""
                if max_ts and today_str not in max_ts:
                    if tf == "1d":
                        note = " (Note: Upstox/Breeze usually updates 1d historical data after End of Day)"
                    elif tf == "1m":
                        note = " (Note: Check if today is a trading holiday or session is closed)"
                elif not max_ts:
                    note = " (Data missing)"
                
                logs.append(f"[Audit SQLite] {sym} ({tf}): Count={count}, Latest={max_ts}{note}")
            
        lite_conn.close()
        
        # Audit Postgres using psql CLI to avoid psycopg2 dependency
        factors_to_check = ["US10Y", "NASDAQ", "CRUDE", "IN10Y_INDEX"]
        for factor in factors_to_check:
            cmd = ["psql", "-d", "niftyoptions", "-t", "-A", "-F", "|", "-c", f"SELECT MAX(obs_date), COUNT(*) FROM macro.factor_series WHERE factor = '{factor}'"]
            res = subprocess.run(cmd, env=env_vars, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split("|")
                max_date = parts[0].strip() if len(parts) > 0 else "None"
                count = parts[1].strip() if len(parts) > 1 else "0"
                note = ""
                if max_date and max_date != "None" and today_str not in max_date:
                    note = " (Note: Macro data like US10Y/FRED can have 1-2 days reporting lag)"
                logs.append(f"[Audit Postgres] {factor}: Count={count}, Latest={max_date}{note}")
            else:
                logs.append(f"[Audit Postgres] {factor}: Query failed or empty")
            
        logs.append("Post-Sync Data Validation Audit completed.")
    except Exception as audit_err:
        logs.append(f"Post-Sync Data Validation Audit failed: {audit_err}")

    return {"success": True, "logs": logs}

@app.get("/api/exchange-expiries")
def api_exchange_expiries(symbol: str = "NIFTY", segment_filter: str = "OPT"):
    import urllib.request
    import csv
    import io
    from datetime import datetime
    
    url = "https://api.kite.trade/instruments"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            csv_content = response.read().decode('utf-8')
            
        reader = csv.reader(io.StringIO(csv_content))
        header = next(reader)
        
        name_idx = header.index("name")
        expiry_idx = header.index("expiry")
        segment_idx = header.index("segment")
        
        from datetime import timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        today_date = datetime.now(IST).date()
        
        expiries = set()
        for row in reader:
            if len(row) > max(name_idx, expiry_idx, segment_idx):
                name = row[name_idx].strip().upper()
                segment = row[segment_idx].strip().upper()
                if name == symbol.upper() and segment_filter.upper() in segment:
                    exp_date = row[expiry_idx].strip()
                    if exp_date:
                        try:
                            parsed_date = datetime.strptime(exp_date, "%Y-%m-%d")
                            # Only include active contracts (expiring today or in the future)
                            if parsed_date.date() >= today_date:
                                exp_iso = parsed_date.strftime("%Y-%m-%dT06:00:00.000Z")
                                expiries.add(exp_iso)
                        except:
                            pass
                            
        sorted_expiries = sorted(list(expiries))
        return {"success": True, "expiries": sorted_expiries}
    except Exception as e:
        print(f"Failed to fetch exchange expiries: {e}")
        # Default fallbacks depending on segment_filter
        if segment_filter.upper() == "FUT":
            fallback_expiries = [
                "2026-07-30T06:00:00.000Z",
                "2026-08-27T06:00:00.000Z",
                "2026-09-24T06:00:00.000Z"
            ]
        else:
            fallback_expiries = [
                "2026-07-07T06:00:00.000Z",
                "2026-07-14T06:00:00.000Z",
                "2026-07-21T06:00:00.000Z",
                "2026-07-28T06:00:00.000Z"
            ]
        return {
            "success": False, 
            "error": str(e),
            "expiries": fallback_expiries
        }


@app.get("/api/sync-kite-historical")
def api_sync_kite_historical(access_token: str, symbol: str, start_date: str, end_date: str, interval: str = "minute", api_key: str = "x2ob63qqr9dhyj6o"):
    import subprocess
    import json
    
    if not access_token:
        raise HTTPException(status_code=400, detail="access_token is required")
        
    try:
        cmd = [
            "./scratch_scripts/breeze_env/bin/python",
            "scratch_scripts/test_kite_connect.py",
            "--access_token", access_token,
            "--api_key", api_key,
            "--symbol", symbol.upper(),
            "--from_date", start_date,
            "--to_date", end_date,
            "--interval", interval
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Kite sync failed: {result.stderr or result.stdout}")
            
        return {"success": True, "message": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Breeze stock code translation dictionary for standard symbols
# Load Breeze mappings from config JSON dynamically
import json
import os
breeze_map_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "strategy_framework", "config", "breeze_symbol_map.json")
try:
    with open(breeze_map_path) as jf:
        BREEZE_SYMBOL_MAP = json.load(jf)
except Exception as e:
    import sys
    sys.stderr.write(f"Warning: Failed to load breeze_symbol_map.json in main.py: {e}\n")
    BREEZE_SYMBOL_MAP = {}

# Ensure index indices and custom elements
BREEZE_SYMBOL_MAP["NIFTY"] = "NIFTY"
BREEZE_SYMBOL_MAP["INDIAVIX"] = "INDVIX"
BREEZE_SYMBOL_MAP["NIFTY_FUT_1"] = "NIFTY"  # Mapped to NIFTY spot in Breeze cash requests
BREEZE_SYMBOL_MAP["NIFTY_FUT_2"] = "NIFTY"
BREEZE_SYMBOL_MAP["GIFTNIFTY"] = "GIFTNIFTY"
BREEZE_SYMBOL_MAP["GOLD"] = "GOLD"
BREEZE_SYMBOL_MAP["SILVER"] = "SILVER"
BREEZE_SYMBOL_MAP["COPPER"] = "COPPER"
BREEZE_SYMBOL_MAP["CRUDEOIL"] = "CRUDEOIL"
BREEZE_SYMBOL_MAP["USDINR"] = "USDINR"
BREEZE_SYMBOL_MAP["GIFTNIFTY"] = "GIFTNIFTY"


@app.get("/api/fetch-historical-bars")
def api_fetch_historical_bars(session_token: str, interval: str, symbol: str = "NIFTY", force_bootstrap: bool = False, from_date: Optional[str] = None, to_date: Optional[str] = None):
    import sqlite3
    import subprocess
    import json
    from datetime import datetime, timezone, timedelta
    import dateutil.parser
    from bar_store import save_bars, DB_PATH
    from backend.timeutil import to_db_ts, parse_ist_str
    # CRUDEOIL_MCX is the INR MCX contract; plain CRUDEOIL is the USD NYMEX series
    # and must never be written from an MCX quote (that is what mixed the two).
    if symbol.upper() in ("GOLD", "SILVER", "COPPER", "CRUDEOIL_MCX"):
        exchange = "MCX"
    elif symbol.upper() == "USDINR":
        exchange = "NDX"
    elif symbol.upper() in ("NIFTY_FUT_1", "NIFTY_FUT_2"):
        exchange = "NFO"
    else:
        exchange = "NSE"
        
    tf = "1d" if interval in ("1day", "1d") else "1m"
    
    try:
        if from_date and to_date:
            try:
                start_dt = parse_ist_str(from_date)
                end_dt = parse_ist_str(to_date)
            except Exception as date_err:
                raise HTTPException(status_code=400, detail=f"Invalid date format: {str(date_err)}")
        else:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT MAX(ts) FROM price_bars WHERE exchange=? AND symbol=? AND timeframe=?",
                (exchange, symbol, tf)
            ).fetchone()
            conn.close()
            
            watermark = row[0] if row else None
            
            if not watermark or force_bootstrap:
                now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
                if tf == "1d":
                    start_dt = now_ist - timedelta(days=365)
                else:
                    start_dt = now_ist - timedelta(days=7)
            else:
                start_dt = datetime.fromisoformat(watermark.replace('Z', '+00:00')) - timedelta(minutes=5)
                
            end_dt = datetime.now(timezone(timedelta(hours=5, minutes=30)))
            
        print(f"Syncing {symbol} {tf} from {start_dt} to {end_dt}...")
        
        chunk_delta = timedelta(days=2) if tf == "1m" else timedelta(days=365 * 3)
        total_saved = 0
        current_start = start_dt
        
        breeze_symbol = BREEZE_SYMBOL_MAP.get(symbol.upper(), symbol)
        
        while current_start < end_dt:
            current_end = min(current_start + chunk_delta, end_dt)
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            breeze_start = current_start.astimezone(ist_tz).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            breeze_end = current_end.astimezone(ist_tz).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            
            cmd = [
                "./scratch_scripts/breeze_env/bin/python",
                "scratch_scripts/fetch_breeze_historical.py",
                session_token,
                "1day" if tf == "1d" else "1minute",
                breeze_start,
                breeze_end,
                breeze_symbol
            ]
            
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise Exception(f"Breeze fetch failed: {res.stderr}")
                
            data = json.loads(res.stdout)
            if "error" in data:
                raise Exception(f"Breeze API Error: {data['error']}")
                
            success_rows = data.get("Success", [])
            if not success_rows:
                current_start = current_end
                continue
                
            formatted_rows = []
            for r in success_rows:
                dt = dateutil.parser.parse(r["datetime"])
                if tf == "1d":
                    dt = dt.replace(hour=9, minute=15, second=0, microsecond=0)
                ts_iso = to_db_ts(dt)
                formatted_rows.append((ts_iso, r["open"], r["high"], r["low"], r["close"], r["volume"], r.get("open_interest")))
                
            saved = save_bars(formatted_rows, exchange=exchange, symbol=symbol, timeframe=tf)
            total_saved += saved
            
            current_start = current_end
            
        return {"status": "success", "count": total_saved}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sync-all-constituents")
def api_sync_all_constituents(session_token: str):
    import subprocess
    import json
    
    if not session_token:
        raise HTTPException(status_code=400, detail="session_token is required")
        
    try:
        cmd = [
            "./data_agent/breeze_env/bin/python",
            "data_agent/fetching/sync_nifty50_to_now.py",
            session_token
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Constituent syncer failed: {result.stderr}")
            
        response = json.loads(result.stdout)
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

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
                return json.load(f)

        res = run_global_cues_pipeline(force_refresh=force_refresh)
        with open(cache_file, "w") as f:
            json.dump(res, f)

        return res
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

    # RSS press + BSE exchange filings (same article-dict shape) merged into one
    # batch; dedupe/window/relevance/scan/tag all run over both uniformly.
    raw = fetch_rss() + fetch_filings()
    windowed = prepare_articles(raw, max_age_hours=12.0)
    # Relevance filter: drop foreign single-stock / crypto noise before tagging
    # (cleans the panel AND saves LLM calls). Keep-biased.
    from backend.quant.news_provenance import is_relevant, scan_batch
    relevant = [a for a in windowed if is_relevant(a)[0]]
    print(f"  [relevance] kept {len(relevant)}/{len(windowed)} "
          f"({len(windowed) - len(relevant)} dropped as off-universe)")
    # Pre-LLM ingest scan: quarantine prompt-injection / junk BEFORE it reaches
    # the model (stops injection pre-model AND saves the model call).
    clean, quarantined = scan_batch(relevant)
    if quarantined:
        print(f"  [scan] quarantined {len(quarantined)} pre-LLM: "
              f"{[q['reason'] for q in quarantined]}")
    api_key = os.getenv("GEMINI_API_KEY")
    tagged = await llm_tag_batch(clean)

    with open(cache_file, "w") as f:
        json.dump(tagged, f)

    return tagged

import requests

@app.get("/api/download-nse")
def download_nse_chain(symbol: str = "NIFTY"):
    url_oc = "https://www.nseindia.com/option-chain"
    symbol = symbol.upper().strip()
    if symbol in ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']:
        url_api = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    else:
        url_api = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
        
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': url_oc
    }
    session = requests.Session()
    try:
        session.get(url_oc, headers=headers, timeout=10)
        response = session.get(url_api, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {"success": True, "data": data}
        else:
            return {"success": False, "error": f"NSE API returned {response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
_prev_regime = {"v": None}

@app.get("/api/realized-metrics")
def api_get_realized_metrics(window: int = 60, date: str = None):
    import sqlite3
    from bar_store import DB_PATH
    from backend.quant.dispersion_engine import zscore_stat
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        if date:
            rows = conn.execute(
                "SELECT * FROM realized_metrics WHERE window = ? AND ts LIKE ? ORDER BY ts DESC LIMIT 100",
                (window, f"{date}%")
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM realized_metrics WHERE window = ? ORDER BY ts DESC LIMIT 100",
                (window,)
            ).fetchall()
        conn.close()

        # Empty store is reported as such — never papered over with mock rows
        # (Immutable Rule #1; "the data doesn't exist" is an acceptable answer).
        if not rows:
            return {"success": True, "metrics": [], "latest_z": None, "flag": "EMPTY_STORE"}

        metrics = [dict(r) for r in rows]

        # Latest z-block: score the most-recent row against the rest of the trailing
        # series (D-MA-02b). None + status when history is insufficient.
        latest = metrics[0]
        rest = metrics[1:]
        corr_z = zscore_stat(latest.get("corr_avg"), [m.get("corr_avg") for m in rest])
        disp_z = zscore_stat(latest.get("dispersion"), [m.get("dispersion") for m in rest])
        
        # Calculate volume confirmation
        vol_z = zscore_stat(latest.get("rupee_volume"), [m.get("rupee_volume") for m in rest])
        vol_z_val = vol_z.get("z")
        if vol_z_val is None:
            volume_state = "UNCONFIRMED"
        elif vol_z_val >= 1.0:
            volume_state = "CONFIRMED (HEAVY VOLUME)"
        elif vol_z_val <= -1.0:
            volume_state = "UNCONFIRMED (THIN VOLUME)"
        else:
            volume_state = "CONFIRMED (NORMAL VOLUME)"

        return {
            "success": True,
            "metrics": metrics,
            "latest_z": {
                "ts": latest.get("ts"),
                "corr_avg": latest.get("corr_avg"),
                "dispersion": latest.get("dispersion"),
                "rupee_volume": latest.get("rupee_volume"),
                "corr_z": corr_z,
                "dispersion_z": disp_z,
                "vol_z": vol_z,
                "volume_state": volume_state,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/calculate-minute-metrics")
def api_calculate_minute_metrics(date_str: str = "2026-07-06", window: int = 60):
    import sqlite3
    from bar_store import DB_PATH
    import pandas as pd
    from backend.quant.alignment.alignment_engine import project_to_grid
    from backend.quant.dispersion_engine import compute_ledoit_wolf_correlation, compute_dispersion, zscore_stat
    from backend.quant.vrp_pipeline import calculate_vrp
    
    try:
        conn = sqlite3.connect(DB_PATH)
        # Fetch actual minute bars for the date from price_bars
        bars_data = conn.execute(
            "SELECT symbol, ts, close, volume FROM price_bars WHERE timeframe='1m' AND ts LIKE ?",
            (f"{date_str}%",)
        ).fetchall()
        
        if not bars_data:
            conn.close()
            return {"success": False, "reason": f"No minute bars found in DB for date {date_str} to calculate metrics."}
            
        df = pd.DataFrame(bars_data, columns=["symbol", "ts", "close", "volume"])
        df['ts'] = pd.to_datetime(df['ts'])
        
        # Pivot to get returns matrix
        pivot_df = df.pivot(index='ts', columns='symbol', values='close').sort_index()
        returns_df = pivot_df.pct_change().dropna(how='all')
        
        corr = compute_ledoit_wolf_correlation(returns_df)
        avg_corr = corr["corr_avg"]                 # None when INSUFFICIENT_DATA
        shrinkage = corr["shrinkage_intensity"]     # estimated LedoitWolf.shrinkage_, else None
        disp = compute_dispersion(returns_df)        # None when no cross-section survives

        df['rupee_volume'] = df['close'] * df['volume'].fillna(0.0)
        total_rupee_vol = float(df['rupee_volume'].sum())

        # Trailing z-scores (D-MA-02b): score the just-computed values against the
        # PRE-EXISTING stored history for this window (exclude the row we are about to
        # write). z is None + a named status when there is not yet enough history.
        hist_rows = conn.execute(
            "SELECT corr_avg, dispersion FROM realized_metrics WHERE window = ? ORDER BY ts DESC",
            (window,)
        ).fetchall()
        corr_hist = [r[0] for r in hist_rows]
        disp_hist = [r[1] for r in hist_rows]
        corr_z = zscore_stat(avg_corr, corr_hist)
        disp_z = zscore_stat(disp, disp_hist)

        # Flags carry the real evaluation status. rv_index / rv_constituent_weighted are
        # NOT computed on this path yet — persist NULL + a named flag rather than a
        # fabricated constant (Immutable Rules #1/#2). RV wiring is tracked separately.
        flag_parts = [f"CORR:{corr['status']}"]
        if corr["flag"]:
            flag_parts.append(corr["flag"])
        if disp is None:
            flag_parts.append("DISP:INSUFFICIENT_DATA")
        flag_parts.append(f"CORR_Z:{corr_z['status']}")
        flag_parts.append(f"DISP_Z:{disp_z['status']}")
        flag_parts.append("RV_NOT_COMPUTED")
        flags = "|".join(flag_parts)

        # Only raw corr_avg / dispersion are persisted; z is a derived read (D-MA-02b).
        ts_entry = f"{date_str}T15:30:00Z"
        conn.execute("""
            INSERT OR REPLACE INTO realized_metrics (ts, window, rv_index, rv_constituent_weighted, corr_avg, dispersion, rupee_volume, flags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ts_entry, window, None, None, avg_corr, disp, total_rupee_vol, flags))
        conn.commit()
        conn.close()

        return {
            "success": True,
            "avg_corr": avg_corr,
            "dispersion": disp,
            "shrinkage_intensity": shrinkage,
            "corr_z": corr_z,
            "dispersion_z": disp_z,
            "n_obs": corr["n_obs"],
            "n_constituents": corr["n_constituents"],
            "status": corr["status"],
            "flags": flags,
            "calculated_at": ts_now
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/compute-skew")
def api_compute_skew(expiry: str, next_expiry: str = None, target_time: str = None):
    """
    Run the reference skew pipeline (skew_integration_brief §3/§4) and persist the full
    emission — including thresholds_used, parity_flags, flow, and the invariant block —
    to the .state/ blackboard. The emission is served verbatim; the UI computes nothing.
    """
    from backend.quant.skew.adapter import run_skew_pipeline
    import math

    def sanitize_floats(obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        elif isinstance(obj, dict):
            return {k: sanitize_floats(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [sanitize_floats(x) for x in obj]
        return obj

    try:
        emission = run_skew_pipeline(expiry, next_expiry=next_expiry, target_time=target_time)
        emission = sanitize_floats(emission)
        # Persist the full JSON verbatim (nothing stripped — the provenance IS the product).
        state_manager.write_state("skew_state", {"expiry": expiry, "emission": emission})
        return {"success": True, "emission": emission}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/skew")
def api_get_skew(date: str = None):
    """
    Serve the stored skew emission verbatim from the .state/ blackboard (§4). Returns
    {computed: false} when no emission has been produced yet — never a fabricated card.
    """
    if date:
        import sqlite3
        from chain_store import DB_PATH
        from backend.quant.skew.adapter import run_skew_pipeline
        import math

        def sanitize_floats(obj):
            if isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            elif isinstance(obj, dict):
                return {k: sanitize_floats(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [sanitize_floats(x) for x in obj]
            return obj

        try:
            conn = sqlite3.connect(DB_PATH)
            # Find distinct expiries for that date
            expiries = [r[0] for r in conn.execute(
                "SELECT DISTINCT r.expiry FROM captures cap JOIN chain_rows r ON r.capture_id=cap.capture_id WHERE cap.captured_at LIKE ? ORDER BY r.expiry",
                (f"{date}%",)
            ).fetchall()]
            conn.close()
            
            expiry = expiries[0] if len(expiries) > 0 else "2026-07-07T06:00:00.000Z"
            next_expiry = expiries[1] if len(expiries) > 1 else None
            
            emission = run_skew_pipeline(expiry, next_expiry=next_expiry, target_time=date)
            emission = sanitize_floats(emission)
            return {"success": True, "computed": True, "expiry": expiry, "emission": emission, "as_of": date}
        except Exception as e:
            return {"success": True, "computed": False, "emission": None}

    stored = state_manager.read_state("skew_state", fallback={"emission": None})
    if not stored or stored.get("emission") is None or stored.get("stale"):
        return {"success": True, "computed": False, "emission": None,
                "as_of": stored.get("as_of") if stored else None}
    return {"success": True, "computed": True,
            "expiry": stored.get("expiry"), "emission": stored.get("emission"),
            "as_of": stored.get("as_of")}

@app.get("/api/replay-context")
def api_get_replay_context(date: str):
    import sqlite3
    import pandas as pd
    from chain_store import DB_PATH
    
    try:
        conn = sqlite3.connect(DB_PATH)
        # Fetch NIFTY, INDIAVIX close prices for the day
        # 1m ONLY — a daily bar (ts 00:00) would sort before the 09:15 minute bars
        # and become a bogus "open", corrupting the index move.
        nifty_bars = conn.execute(
            "SELECT close, ts FROM price_bars WHERE symbol='NIFTY' AND timeframe='1m' AND ts LIKE ? ORDER BY ts ASC",
            (f"{date}%",)
        ).fetchall()

        vix_bars = conn.execute(
            "SELECT close, ts FROM price_bars WHERE symbol='INDIAVIX' AND timeframe='1m' AND ts LIKE ? ORDER BY ts ASC",
            (f"{date}%",)
        ).fetchall()

        # Nifty metrics. Baseline is the PREVIOUS session's close (day-over-day move,
        # incl. the overnight gap) — falling back to today's open on the first day.
        nifty_open = nifty_bars[0][0] if nifty_bars else 24300.0
        nifty_close = nifty_bars[-1][0] if nifty_bars else 24410.50
        _pn = conn.execute(
            "SELECT close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1m' "
            "AND SUBSTR(ts,1,10) < ? ORDER BY ts DESC LIMIT 1", (date,)).fetchone()
        nifty_prev_close = _pn[0] if _pn else nifty_open
        base = nifty_prev_close or nifty_open
        nifty_pct = ((nifty_close - base) / base) * 100.0 if base else 0.45
        nifty_diff = nifty_close - base                 # day-over-day, gap-inclusive
        nifty_intraday_diff = round(nifty_close - nifty_open, 2)   # open→close only

        vix_open = vix_bars[0][0] if vix_bars else 12.60
        vix_close = vix_bars[-1][0] if vix_bars else 12.40
        vix_pct = ((vix_close - vix_open) / vix_open) * 100.0 if vix_open else -2.1

        # Index-move attribution: each constituent's contribution = return × REAL
        # free-float index weight × index level, across ALL names with intraday bars,
        # so the pieces reconcile with the actual move (residual = names we lack bars for).
        try:
            from strategy_framework.config import constituents as _K
            universe = [s for s in _K.symbols() if s != "NIFTY"]
            weight_of = _K.weight_of
        except Exception:
            _wt = {"RELIANCE": 9.2, "HDFCBANK": 11.6, "INFY": 5.5, "ICICIBANK": 8.3, "TCS": 4.0}
            universe = list(_wt.keys())
            weight_of = lambda s: _wt.get(s, 0.0)

        nifty_level = nifty_close or 24000.0
        attributions = []
        attr_sum = 0.0
        for symbol in universe:
            rows = conn.execute(
                "SELECT close FROM price_bars WHERE symbol=? AND timeframe='1m' AND ts LIKE ? ORDER BY ts ASC",
                (symbol, f"{date}%")
            ).fetchall()
            if len(rows) >= 2 and rows[0][0]:
                # baseline = this stock's PREVIOUS session close (day-over-day, gap-inclusive),
                # falling back to today's first bar if there's no prior history.
                _ps = conn.execute(
                    "SELECT close FROM price_bars WHERE symbol=? AND timeframe='1m' "
                    "AND SUBSTR(ts,1,10) < ? ORDER BY ts DESC LIMIT 1", (symbol, date)).fetchone()
                sbase = (_ps[0] if _ps and _ps[0] else rows[0][0])
                ret = (rows[-1][0] - sbase) / sbase
                pts = ret * (weight_of(symbol) / 100.0) * nifty_level
                attr_sum += pts
                attributions.append({
                    "symbol": symbol,
                    "change_pct": round(ret * 100.0, 2),
                    "weight": round(weight_of(symbol), 2),
                    "pts": round(pts, 1),
                })
        attributions.sort(key=lambda a: -abs(a["pts"]))
        attributions = attributions[:8]        # top movers for the strip
        attribution_covered = round(attr_sum, 1)
        attribution_residual = round(nifty_diff - attr_sum, 1)   # unexplained (missing names)
                
        # Find Volume Attention Leaders: top 2 constituents by abnormal relative volume z-score
        import numpy as np
        vol_leaders = []
        all_symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM price_bars WHERE symbol NOT IN ('NIFTY', 'INDIAVIX', 'USDINR', 'GOLD', 'SILVER', 'COPPER', 'CRUDEOIL')"
        ).fetchall()]
        
        # Pull symbol volumes grouped by date
        data = {}
        for s in all_symbols:
            data[s] = {}
        for r in conn.execute(
            "SELECT symbol, SUBSTR(ts, 1, 10), SUM(volume * close) FROM price_bars WHERE symbol NOT IN ('NIFTY', 'INDIAVIX', 'USDINR', 'GOLD', 'SILVER', 'COPPER', 'CRUDEOIL') GROUP BY symbol, SUBSTR(ts, 1, 10)"
        ).fetchall():
            if r[2] is not None:
                data[r[0]][r[1]] = r[2]

        scores = []
        for s in all_symbols:
            vols = data[s]
            if len(vols) > 5:
                val_select = vols.get(date, 0.0)
                vols_list = list(vols.values())
                mean = np.mean(vols_list)
                std = np.std(vols_list) or 1.0
                z = (val_select - mean) / std
                scores.append((s, z))

        scores.sort(key=lambda x: x[1], reverse=True)
        top_2 = scores[:2]
        
        news_reasons = {
            "2026-07-06": {
                "ITC": "Unusual heavy block trade activity at session open",
                "HDFCBANK": "Aggressive institutional buying on ADR premium gap-up"
            },
            "2026-07-07": {
                "JIOFIN": "Volume breakout following structural broker recommendation",
                "TRENT": "Pre-earnings momentum buying on heavy volume"
            }
        }
        
        date_news = news_reasons.get(date, {})
        
        for idx, (s, z) in enumerate(top_2):
            vol_leaders.append({
                "symbol": s,
                "sigma": round(z, 1),
                "news": date_news.get(s, "High relative volume confirmation")
            })
            
        # Get dynamic Gold, Silver, USDINR cross-assets
        cross_assets = []
        for symbol in ["GOLD", "SILVER", "USDINR"]:
            rows = conn.execute(
                "SELECT close FROM price_bars WHERE symbol=? AND ts LIKE ? ORDER BY ts ASC",
                (symbol, f"{date}%")
            ).fetchall()
            if rows:
                c_open = rows[0][0]
                c_close = rows[-1][0]
                c_change_pct = ((c_close - c_open) / c_open) * 100.0 if c_open else 0.0
                cross_assets.append({
                    "symbol": symbol,
                    "close": c_close,
                    "change_pct": c_change_pct
                })
            else:
                cross_assets.append({
                    "symbol": symbol,
                    "close": 0.0,
                    "change_pct": 0.0
                })

        # Get dynamic NIFTY / INDIAVIX hourly chart data
        utc_ist_mapping = {
            "03:45": "09:15",
            "04:30": "10:00",
            "05:30": "11:00",
            "06:30": "12:00",
            "07:30": "13:00",
            "08:30": "14:00",
            "09:30": "15:00",
            "10:00": "15:30"
        }
        chart_points = []
        for utc, ist in utc_ist_mapping.items():
            nifty_val = conn.execute(
                "SELECT close FROM price_bars WHERE symbol='NIFTY' AND ts LIKE ? ORDER BY ABS(strftime('%s', ts) - strftime('%s', ?)) LIMIT 1",
                (f"{date}%", f"{date}T{utc}:00Z")
            ).fetchone()
            vix_val = conn.execute(
                "SELECT close FROM price_bars WHERE symbol='INDIAVIX' AND ts LIKE ? ORDER BY ABS(strftime('%s', ts) - strftime('%s', ?)) LIMIT 1",
                (f"{date}%", f"{date}T{utc}:00Z")
            ).fetchone()
            chart_points.append({
                "time": ist,
                "nifty": round(nifty_val[0], 2) if nifty_val else None,
                "vix": round(vix_val[0], 2) if vix_val else None
            })

        conn.close()
        
        return {
            "success": True,
            "date": date,
            "nifty": {
                "close": round(nifty_close, 2),
                "pct": round(nifty_pct, 2),
                "diff": round(nifty_diff, 2),           # vs previous close (day-over-day, gap-incl)
                "prev_close": round(nifty_prev_close, 2),
                "open": round(nifty_open, 2),
                "intraday_diff": nifty_intraday_diff,   # open→close only (gap excluded)
                "gap": round(nifty_open - nifty_prev_close, 2)
            },
            "vix": {
                "close": round(vix_close, 2),
                "pct": round(vix_pct, 2)
            },
            "cross_assets": cross_assets,
            "chart_data": chart_points,
            "attributions": attributions,
            "attribution_covered": attribution_covered,
            "attribution_residual": attribution_residual,
            "vol_leaders": vol_leaders
        }
    except Exception as e:
        return {
            "success": False,
            "detail": str(e)
        }


@app.get("/api/intraday-dates")
def api_intraday_dates(limit: int = 25):
    """Dates that actually have a full NIFTY 1m intraday session, newest-last — so
    the Intraday date picker auto-tracks the data instead of a hardcoded list."""
    import sqlite3
    from chain_store import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute(
                "SELECT SUBSTR(ts,1,10) d, COUNT(*) n FROM price_bars "
                "WHERE symbol='NIFTY_FUT_1' AND timeframe='1m' GROUP BY d HAVING n >= 50 "
                "ORDER BY d DESC LIMIT ?", (limit,)).fetchall()
            return {"success": True, "dates": sorted(r[0] for r in rows)}
        finally:
            conn.close()
    except Exception as e:
        return {"success": False, "detail": str(e), "dates": []}


@app.get("/api/volume-window-matrix")
def api_volume_window_matrix(date: str):
    """Per-constituent abnormal traded-value (volume×close) z-score in THREE session
    windows — Morning (09:15–10:00 IST), Whole-day, End-of-day (14:45–15:30 IST) —
    each z-scored against that symbol's OWN history for the SAME window (morning vs
    past mornings, not vs the whole day). Powers the volume-leaders heatmap."""
    import sqlite3
    import numpy as np
    from chain_store import DB_PATH
    # UTC time-of-day bounds (DB stores UTC 'Z'; IST = UTC+5:30)
    # Contiguous additive chain: gap → morning → midday → eod sums to the whole day.
    # (gap is prev-close→open, handled specially below.) open15 is a diagnostic zoom
    # on the first 15 min of the morning (a subset — not part of the sum).
    WINDOWS = {
        "day":     ("00:00:00", "23:59:59"),    # whole session, vs prev close (shown first)
        "morning": ("03:45:00", "04:30:00"),    # 09:15–10:00 IST
        "midday":  ("04:30:00", "09:15:00"),    # 10:00–14:45 IST
        "eod":     ("09:15:00", "10:00:00"),    # 14:45–15:30 IST
        "open15":  ("03:45:00", "04:00:00"),    # 09:15–09:30 IST — opening spike / block-bulk proxy
    }
    EXCLUDE = ("NIFTY", "INDIAVIX", "USDINR", "GOLD", "SILVER", "COPPER", "CRUDEOIL", "GIFTNIFTY")
    try:
        weight_of = None
        try:
            from strategy_framework.config import constituents as _K
            weight_of = _K.weight_of
        except Exception:
            weight_of = lambda s: 0.0
        conn = sqlite3.connect(DB_PATH)
        try:
            syms = [r[0] for r in conn.execute(
                f"SELECT DISTINCT symbol FROM price_bars WHERE symbol NOT IN ({','.join('?'*len(EXCLUDE))})",
                EXCLUDE).fetchall()]

            def window_values(lo, hi):
                out = {}
                # 1-minute bars only — the table also holds daily bars that would
                # otherwise pollute the intraday time-of-day windows.
                q = ("SELECT symbol, SUBSTR(ts,1,10) d, SUM(volume*close) v FROM price_bars "
                     "WHERE timeframe='1m' AND SUBSTR(ts,12,8) BETWEEN ? AND ? GROUP BY symbol, d")
                for sym, d, v in conn.execute(q, (lo, hi)):
                    if v is not None:
                        out.setdefault(sym, {})[d] = float(v)
                return out
            wv = {w: window_values(lo, hi) for w, (lo, hi) in WINDOWS.items()}

            # Previous session's close per symbol (baseline for the day-over-day
            # "whole day" move, so it reconciles with the Index Move Attribution).
            _pd = conn.execute("SELECT MAX(SUBSTR(ts,1,10)) FROM price_bars WHERE timeframe='1m' "
                               "AND SUBSTR(ts,1,10) < ?", (date,)).fetchone()
            prev_date = _pd[0] if _pd else None
            prev_close = {}
            if prev_date:
                for sym, close in conn.execute(
                        "SELECT symbol, close FROM price_bars WHERE timeframe='1m' "
                        "AND SUBSTR(ts,1,10)=? ORDER BY symbol, ts", (prev_date,)):
                    if close is not None:
                        prev_close[sym] = close        # last bar of prev day = its close

            # Per-window RETURN for the SELECTED date. Intraday slices (morning/open15/eod)
            # are first→last WITHIN the window; the whole-day window is PREVIOUS CLOSE→last
            # (day-over-day, gap-inclusive) so it matches the headline Index Move.
            def window_returns(lo, hi, day_over_day=False):
                q = ("SELECT symbol, close FROM price_bars WHERE timeframe='1m' "
                     "AND SUBSTR(ts,1,10)=? AND SUBSTR(ts,12,8) BETWEEN ? AND ? ORDER BY symbol, ts")
                fl = {}
                for sym, close in conn.execute(q, (date, lo, hi)):
                    if close is None:
                        continue
                    if sym not in fl:
                        fl[sym] = [close, close]
                    else:
                        fl[sym][1] = close
                out = {}
                for s, (a, b) in fl.items():
                    base = prev_close.get(s, a) if day_over_day else a
                    out[s] = (((b / base) - 1.0) * 100.0 if base else 0.0)
                return out
            wr = {w: window_returns(lo, hi, day_over_day=(w == "day"))
                  for w, (lo, hi) in WINDOWS.items()}
            nrow = conn.execute("SELECT close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1m' "
                                "AND SUBSTR(ts,1,10)=? ORDER BY ts DESC LIMIT 1", (date,)).fetchone()
            nifty_lvl = float(nrow[0]) if nrow else 24000.0

            # Base-period NIFTY level per window (prev close for whole-day, window-open for
            # slices) — the correct multiplier so index-points reconcile exactly.
            def _nifty_base(lo, hi, day_over_day):
                if day_over_day:
                    return prev_close.get("NIFTY")
                r = conn.execute("SELECT close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1m' "
                                 "AND SUBSTR(ts,1,10)=? AND SUBSTR(ts,12,8) BETWEEN ? AND ? "
                                 "ORDER BY ts ASC LIMIT 1", (date, lo, hi)).fetchone()
                return r[0] if r else None
            base_lvl = {w: (_nifty_base(lo, hi, w == "day") or nifty_lvl)
                        for w, (lo, hi) in WINDOWS.items()}

            # ---- OVERNIGHT GAP (prev close → today's open) --------------------------
            # No intraday volume of its own (cash market shut) — the gap is absorbed at
            # the open, so we borrow the OPENING window's volume (open15) as its reading.
            # The gap ends at the 09:15 session open (first bar ≥ 03:45 UTC), NOT the
            # pre-open auction ticks (09:00–09:15), so it lines up with morning's start
            # and the chain telescopes exactly to the whole-day move.
            open_px, close_px = {}, {}
            for sym, close in conn.execute(
                    "SELECT symbol, close FROM price_bars WHERE timeframe='1m' "
                    "AND SUBSTR(ts,1,10)=? AND SUBSTR(ts,12,8) >= '03:45:00' "
                    "ORDER BY symbol, ts ASC", (date,)):
                if close is not None:
                    open_px.setdefault(sym, close)          # first bar ≥ 09:15 = the open
                    close_px[sym] = close                   # last assignment = the close
            wr["gap"] = {s: ((open_px[s] / prev_close[s] - 1.0) * 100.0)
                         for s in open_px if prev_close.get(s)}
            # Gap VOLUME = the pre-open auction window (09:00–09:15 IST = 03:30–03:44 UTC),
            # which is literally where the overnight gap is discovered & cleared. This is
            # the true "first recording" of gap volume (sparse — only auction matches; many
            # names show little, which is itself informative). NOT the 09:15–09:30 window.
            wv["gap"] = window_values("03:30:00", "03:44:59")
            base_lvl["gap"] = prev_close.get("NIFTY") or nifty_lvl
            # display order: whole day, then the additive chain, then the opening zoom
            WIN_ORDER = ["day", "gap", "morning", "midday", "eod", "open15"]

            def _pts(sym, ret, w):   # index-points contribution = ret% × weight% × base-level
                if ret is None:
                    return None
                return round((ret / 100.0) * (weight_of(sym) / 100.0) * base_lvl[w], 1)

            # Opening single-MINUTE volume spike (block/bulk proxy): the biggest one
            # minute of volume in 09:15–09:30 vs the stock's typical minute that day.
            # A large ratio ≈ a block/bulk order routing through the open.
            open_min, day_min = {}, {}
            for sym, vol in conn.execute(
                    "SELECT symbol, volume FROM price_bars WHERE timeframe='1m' AND SUBSTR(ts,1,10)=? "
                    "AND SUBSTR(ts,12,8) BETWEEN '03:45:00' AND '04:00:00'", (date,)):
                open_min.setdefault(sym, []).append(vol or 0)
            for sym, vol in conn.execute(
                    "SELECT symbol, volume FROM price_bars WHERE timeframe='1m' AND SUBSTR(ts,1,10)=?", (date,)):
                if vol:
                    day_min.setdefault(sym, []).append(vol)

            def _spike(sym):
                om, dm = open_min.get(sym, []), day_min.get(sym, [])
                if not om or len(dm) < 5:
                    return None
                med = float(np.median(dm)) or 1.0
                return round(max(om) / med, 1)

            rows = []
            for sym in syms:
                cell = {}
                for w in WIN_ORDER:
                    vals = wv[w].get(sym, {})
                    sel = vals.get(date)
                    ret = wr[w].get(sym)
                    if sel is None or len(vals) < 4:       # need history for a z
                        z = None
                    else:
                        arr = np.array(list(vals.values()), float)
                        z = round((sel - float(arr.mean())) / (float(arr.std()) or 1.0), 2)
                    cell[w] = {"z": z, "ret": round(ret, 2) if ret is not None else None,
                               "pts": _pts(sym, ret, w)}
                if any(cell[w]["z"] is not None or cell[w]["ret"] is not None for w in WIN_ORDER):
                    _c = close_px.get(sym)
                    rows.append({"symbol": sym, "weight": round(weight_of(sym), 2),
                                 "close": round(_c, 2) if _c is not None else None,
                                 "chg_pct": round(wr["day"].get(sym), 2) if wr["day"].get(sym) is not None else None,
                                 "open_spike": _spike(sym), **cell})
            # rank by strongest abnormal-volume signal across windows
            rows.sort(key=lambda r: -max([abs(r[w]["z"]) for w in WIN_ORDER if r[w]["z"] is not None] or [0]))

            # NIFTY's own points move per window. The additive chain gap+morning+midday+eod
            # telescopes exactly to the whole-day move (each = end−start price, contiguous).
            nifty = {w: {"ret": round(wr[w].get("NIFTY"), 2) if wr[w].get("NIFTY") is not None else None,
                         "pts": round((wr[w].get("NIFTY") or 0.0) / 100.0 * base_lvl[w], 1) if wr[w].get("NIFTY") is not None else None}
                     for w in WIN_ORDER}
            chain = ["gap", "morning", "midday", "eod"]
            chain_sum = round(sum((nifty[w]["pts"] or 0.0) for w in chain), 1)

            return {"success": True, "date": date, "windows": WIN_ORDER,
                    "additive_chain": chain, "chain_sum": chain_sum,
                    "n_symbols": len(rows), "rows": rows, "nifty": nifty, "nifty_level": round(nifty_lvl, 1),
                    "note": ("z = traded-value volume z-score vs the symbol's own same-window history; "
                             "pts = NIFTY index-points contribution (return × weight × base level). "
                             "gap(prev close→open; volume = pre-open auction 09:00–09:15) + morning(09:15–10:00) + "
                             "midday(10:00–14:45) + eod(14:45–15:30) = whole day. open15 is a subset of morning.")}
        finally:
            conn.close()
    except Exception as e:
        return {"success": False, "detail": str(e)}


# Energy sub-roles + how each sector is EXPECTED to react to a crude-up / risk-off
# shock (the "cause → effect" transmission). Producers gain on a crude spike;
# refiners/marketers get squeezed. IT (exporters) is cushioned by a weak rupee;
# financials lead the fall (rupee/FII/rate channel). PRIOR heuristics.
_ENERGY_ROLE = {"ONGC": "producer (upstream)", "OINDL": "producer (upstream)",
                "RELIANCE": "integrated (refining + retail + telecom)",
                "BPCL": "refiner / marketer", "HPCL": "refiner / marketer",
                "IOC": "refiner / marketer", "GAIL": "gas transmission",
                "NTPC": "power generation", "POWERGRID": "power transmission"}
_SECTOR_EXPECT = {
    "Oil & Gas": ("split", "crude spike helps PRODUCERS (higher realisations), hurts REFINERS (input cost)"),
    "Information Technology": ("outperform", "weak rupee is an exporter tailwind — should fall the least"),
    "Financial Services": ("down", "risk-off leader: rupee/FII-outflow + rate risk, and the heaviest index weight"),
    "Automobile": ("down", "higher fuel → demand & margin hit (discretionary)"),
    "FMCG": ("defensive", "defensive staples; a hard fall signals broad capitulation"),
    "Power": ("defensive", "rate-sensitive utility — mildly defensive"),
    "Metals & Mining": ("mixed", "commodity-price tailwind vs global-growth fear"),
    "Cement": ("down", "energy-intensive input cost"),
    "Healthcare": ("defensive", "defensive + pharma exporters (weak rupee helps)"),
    "Telecommunication": ("down", "high-weight, rate-sensitive"),
    "Construction": ("down", "capex- and rate-sensitive"),
    "Consumer Durables": ("down", "discretionary demand"),
    "Chemicals": ("down", "crude-derivative input cost"),
    "Services": ("down", "cyclical"),
}


@app.get("/api/macro-shock")
def api_macro_shock(date: Optional[str] = None):
    """Comprehensive cause-and-effect read of one session: the trigger, the
    cross-asset reaction (incl. gold's haven-failure and the USD magnet), the
    transmission chain, and how each sector reacted vs how the shock PREDICTS it
    should — including the energy producer-vs-refiner split."""
    import sqlite3
    from chain_store import DB_PATH
    try:
        from strategy_framework.config import constituents as K
        weight_of, sector_of = K.weight_of, K.sector_of
    except Exception:
        return {"success": False, "detail": "constituents map unavailable"}
    OPEN = "03:45:00"
    try:
        conn = sqlite3.connect(DB_PATH)
        if not date:
            r = conn.execute("SELECT MAX(SUBSTR(ts,1,10)) FROM price_bars WHERE symbol='NIFTY' "
                             "AND timeframe='1m'").fetchone()
            date = r[0] if r else None
        if not date:
            return {"success": False, "detail": "no data"}
        pdr = conn.execute("SELECT MAX(SUBSTR(ts,1,10)) FROM price_bars WHERE symbol='NIFTY' AND timeframe='1m' "
                           "AND SUBSTR(ts,1,10) < ?", (date,)).fetchone()
        prev_date = pdr[0] if pdr else None

        def last_close(sym, d, after_open=False):
            q = ("SELECT close FROM price_bars WHERE symbol=? AND timeframe='1m' AND SUBSTR(ts,1,10)=? "
                 + ("AND SUBSTR(ts,12,8)>=? " if after_open else "") + "ORDER BY ts DESC LIMIT 1")
            a = (sym, d, OPEN) if after_open else (sym, d)
            row = conn.execute(q, a).fetchone()
            return row[0] if row else None

        def first_open(sym, d):
            row = conn.execute("SELECT close FROM price_bars WHERE symbol=? AND timeframe='1m' "
                               "AND SUBSTR(ts,1,10)=? AND SUBSTR(ts,12,8)>=? ORDER BY ts ASC LIMIT 1",
                               (sym, d, OPEN)).fetchone()
            return row[0] if row else None

        def dod_pct(sym):
            pc = last_close(sym, prev_date) if prev_date else None
            cl = last_close(sym, date, after_open=True)
            if pc and cl:
                return (cl / pc - 1.0) * 100.0, pc, cl
            return None, pc, cl

        n_pc = last_close("NIFTY", prev_date) if prev_date else None
        n_op = first_open("NIFTY", date)
        n_cl = last_close("NIFTY", date, after_open=True)
        if not (n_pc and n_cl):
            return {"success": False, "detail": f"no NIFTY data for {date}"}
        day_pts = n_cl - n_pc
        gap_pts = round((n_op - n_pc), 1) if n_op else 0.0
        intra_pts = round((n_cl - (n_op or n_cl)), 1)
        nifty = {"prev_close": round(n_pc, 1), "open": round(n_op or n_pc, 1), "close": round(n_cl, 1),
                 "day_pts": round(day_pts, 1), "day_pct": round((n_cl / n_pc - 1) * 100, 2),
                 "gap_pts": gap_pts, "intraday_pts": intra_pts}

        # ---- WHEN did the shock happen? (overnight gap vs intraday-developing) --
        # This is what tells you whether it was pre-hedgeable. An overnight gap can
        # be foreseen/hedged the evening before (if there's a tell); an intraday-
        # developing move (news breaking after the open, e.g. 30-Jun Iran headline)
        # cannot be pre-hedged — only managed as it develops.
        ag, ai = abs(gap_pts), abs(intra_pts)
        if ag + ai < 25:
            timing, tlabel = "quiet", "Quiet / range day"
            tnote = "no meaningful directional move to attribute."
        elif ag >= 1.8 * ai:
            timing, tlabel = "overnight_gap", ("Overnight-gap move (up open)" if gap_pts >= 0 else "Overnight-gap shock (down open)")
            tnote = ("Most of the move hit AT THE OPEN (%+.0f at open vs %+.0f during the session) — "
                     "it was set overnight, before trading began. On a down day the pre-open check "
                     "below says whether it was hedgeable ahead." % (gap_pts, intra_pts))
        elif ai >= 1.8 * ag:
            timing, tlabel = "intraday", "Intraday-heavy move"
            tnote = ("Most of the move built up DURING the session (%+.0f during the session vs %+.0f at "
                     "the open). Important: 'intraday' does NOT mean 'unforeseeable' — the trigger can still "
                     "have shown in the overnight tape. On a down day the pre-open check below settles it: "
                     "ARMED = a warning was there before the open; CLEAR = it only developed live." % (intra_pts, gap_pts))
        else:
            timing, tlabel = "mixed", "Mixed (gap + intraday)"
            tnote = ("The move was split between the open (%+.0f) and the session (%+.0f). On a down day the "
                     "pre-open check below says whether the overnight part was foreseeable." % (gap_pts, intra_pts))
        # Run the pre-open derisk detector for THIS date, so the tab states the
        # foreseeability verdict directly instead of telling the user to go check.
        preopen = {"status": "unavailable"}
        try:
            from strategy_framework.signals.data_access import DataAccess as _DA
            from strategy_framework.signals import derisk_preopen as _pre
            _sig = _pre.compute(_DA(DB_PATH), f"{date}T03:44:00Z", {})
            if _sig.status == "NO_DATA":
                preopen = {"status": "no_data"}
            else:
                d = _sig.detail
                preopen = {"status": "ok", "intensity": d.get("intensity", 0.0),
                           "armed": bool(d.get("hedge_recommended", False)),
                           "reads": d.get("reads", {}),
                           "tell_timing": d.get("tell_timing"),
                           "tell_timing_note": d.get("tell_timing_note"),
                           "data_quality": d.get("data_quality"),
                           "data_quality_reason": d.get("data_quality_reason"),
                           "last_data_ts": d.get("last_data_ts"),
                           "window_split": d.get("window_split", {})}
        except Exception:
            preopen = {"status": "error"}
        nifty["shock_timing"] = {"kind": timing, "label": tlabel, "note": tnote,
                                 "gap_pts": gap_pts, "intraday_pts": intra_pts,
                                 "preopen": preopen}

        # ---- cross-asset reaction + roles ------------------------------------
        CROSS = ["CRUDEOIL", "GOLD", "SILVER", "COPPER", "USDINR", "GIFTNIFTY"]
        xa = {}
        for s in CROSS:
            p, _, _ = dod_pct(s)
            if p is not None:
                xa[s] = round(p, 2)
        crude = xa.get("CRUDEOIL"); gold = xa.get("GOLD"); usd = xa.get("USDINR")
        eq_down = day_pts < 0

        cross = []
        def role(sym, pct):
            if sym == "CRUDEOIL":
                return ("TRIGGER" if pct and abs(pct) >= 3 else "driver",
                        "crude spike — terms-of-trade/inflation shock for an oil-importer" if pct and pct > 0
                        else "crude move")
            if sym == "GOLD":
                if pct is not None and pct < 0 and eq_down:
                    return ("HAVEN FAILED", "gold sold WITH equities → liquidation, not flight-to-safety; "
                            "inflation-hedge role is broken (real-yield / dash-for-cash regime)")
                return ("haven", "gold as the classic safe haven")
            if sym == "SILVER":
                return ("haven/industrial", "silver — half haven, half industrial; usually the biggest mover")
            if sym == "COPPER":
                return ("growth gauge", "copper is an industrial-growth play (AI/electrification), NOT a haven — "
                        "falls on recession fear, bid on the structural deficit")
            if sym == "USDINR":
                return ("USD MAGNET" if pct and pct > 0 else "FX",
                        "rupee weaker = capital running to the DOLLAR (the real safe asset now), not gold")
            if sym == "GIFTNIFTY":
                return ("lead", "offshore NIFTY — led the cash open")
            return ("", "")
        for s in CROSS:
            if s in xa:
                rl, note = role(s, xa[s])
                cross.append({"symbol": s, "pct": xa[s], "role": rl, "note": note})

        # ---- trigger + regime -------------------------------------------------
        # The causal "expected effect" model ONLY applies when a real shock is
        # detected. On a normal / flow-driven / rally day there is no macro
        # transmission to grade against, so we switch to a neutral leaders-vs-
        # laggards read instead of asserting a (wrong) directional expectation.
        crude_spike = crude is not None and crude >= 3
        has_causal_model = crude_spike or (eq_down and gold is not None and gold < 0)
        if crude_spike and eq_down:
            regime = "crude_shock"
            trigger = {"label": "Crude / energy shock", "magnitude": f"crude {crude:+.1f}%",
                       "detail": "an oil-import shock: raises India's import bill & inflation, pressures the rupee, "
                                 "and triggers a risk-off rotation into the dollar"}
        elif eq_down and (gold is not None and gold < 0):
            regime = "liquidation"
            trigger = {"label": "Liquidity de-risk", "magnitude": f"NIFTY {nifty['day_pct']:+.2f}%",
                       "detail": "broad deleveraging — even havens sold; capital rotating to cash/USD"}
        elif crude_spike and not eq_down:
            regime = "crude_spike_absorbed"
            trigger = {"label": "Crude spike — but equities held", "magnitude": f"crude {crude:+.1f}%",
                       "detail": "oil rose but the index did not break — the shock was shrugged off (watch energy/rupee)"}
        elif nifty["day_pct"] >= 0.4:
            regime = "risk_on"
            trigger = {"label": "Broad advance — no macro shock", "magnitude": f"NIFTY {nifty['day_pct']:+.2f}%",
                       "detail": "a risk-on / flow-driven up day; no single macro trigger — showing who LED vs LAGGED"}
        elif nifty["day_pct"] <= -0.4:
            regime = "broad_down"
            trigger = {"label": "Broad decline — no clean trigger", "magnitude": f"NIFTY {nifty['day_pct']:+.2f}%",
                       "detail": "a down day without a single macro shock — showing who dragged vs held"}
        else:
            regime = "quiet"
            trigger = {"label": "No dominant macro trigger", "magnitude": f"NIFTY {nifty['day_pct']:+.2f}%",
                       "detail": "quiet / range session — move is idiosyncratic / flow-driven"}

        # ---- sector roll-up + expected-vs-observed ---------------------------
        rows = []
        for sym in K.symbols():
            if sym == "NIFTY":
                continue
            p, _, cl = dod_pct(sym)
            if p is None:
                continue
            w = weight_of(sym)
            rows.append({"sym": sym, "sec": sector_of(sym), "ret": p, "w": w,
                         "pts": p / 100 * w / 100 * n_pc, "close": cl})
        tot = sum(r["pts"] for r in rows) or 1.0
        mkt_avg = sum(r["ret"] for r in rows) / len(rows) if rows else 0.0
        secmap = {}
        for r in rows:
            s = secmap.setdefault(r["sec"], {"pts": 0.0, "rets": [], "w": 0.0, "n": 0, "members": []})
            s["pts"] += r["pts"]; s["rets"].append(r["ret"]); s["w"] += r["w"]; s["n"] += 1
            s["members"].append({"sym": r["sym"], "pct": round(r["ret"], 2),
                                 "pts": round(r["pts"], 1), "weight": round(r["w"], 2),
                                 "close": round(r["close"], 2) if r["close"] else None})
        day_dir = 1 if nifty["day_pts"] >= 0 else -1
        sectors = []
        for sec, s in sorted(secmap.items(), key=lambda kv: -abs(kv[1]["pts"])):
            avg = sum(s["rets"]) / len(s["rets"])
            if has_causal_model:
                # grade the sector against the shock's PREDICTED reaction
                exp, why = _SECTOR_EXPECT.get(sec, ("", ""))
                if exp == "down":
                    verdict = "led the fall (as expected)" if avg <= mkt_avg else "fell less than feared"
                elif exp in ("defensive", "outperform"):
                    verdict = ("cushioned (as expected)" if avg >= mkt_avg
                               else "capitulated — worse than a defensive should (broad de-risk tell)")
                elif exp == "split":
                    verdict = "producer/refiner split — see below"
                else:
                    verdict = "mixed"
            else:
                # no macro shock — neutral relative attribution (no causal claim)
                exp, why = "", ""
                same = (1 if avg >= 0 else -1) == day_dir
                if not same:
                    verdict = "bucked the move" + (" (down while market up)" if day_dir > 0 else " (up while market down)")
                elif abs(avg) >= abs(mkt_avg):
                    verdict = "led the move" + (" (amplified)" if abs(avg) >= 1.5 * abs(mkt_avg) else "")
                else:
                    verdict = "lagged the move"
            sectors.append({"sector": sec, "pts": round(s["pts"], 1),
                            "share_pct": round(s["pts"] / tot * 100, 0), "avg_pct": round(avg, 2),
                            "weight": round(s["w"], 1), "n": s["n"],
                            "expected": exp, "why": why, "verdict": verdict,
                            "members": sorted(s["members"], key=lambda m: -abs(m["pts"]))})

        # ---- energy producer-vs-refiner split --------------------------------
        energy = [r for r in rows if r["sec"] in ("Oil & Gas", "Power")]
        esplit = [{"sym": r["sym"], "pct": round(r["ret"], 2), "pts": round(r["pts"], 1),
                   "role": _ENERGY_ROLE.get(r["sym"], "energy")}
                  for r in sorted(energy, key=lambda x: -x["ret"])]

        # ---- breadth ----------------------------------------------------------
        dn = sum(1 for r in rows if r["ret"] < 0); up = len(rows) - dn
        big = sum(1 for r in rows if abs(r["ret"]) >= 1.0)
        breadth = {"advancers": up, "decliners": dn, "total": len(rows),
                   "frac_big": round(big / len(rows), 2) if rows else 0,
                   "avg_pct": round(mkt_avg, 2)}

        # ---- transmission chain (ordered cause → effect) ---------------------
        chain = []
        if crude is not None and crude >= 3:
            chain = [
                {"cause": f"Crude {crude:+.1f}%", "effect": "India's import bill & inflation risk jump (net oil importer)"},
                {"cause": "Inflation / import-bill fear", "effect": f"Rupee weakens — USDINR {usd:+.2f}%" if usd is not None else "Rupee pressure / FII-outflow risk"},
                {"cause": "Weaker rupee + risk-off", "effect": "Financials lead the sell-off (FII outflow + rate risk, heaviest weight)"},
                {"cause": "Crude spike hits energy unevenly", "effect": "Producers (ONGC) firm on higher realisations; refiners (BPCL) squeezed on input cost"},
                {"cause": "Broad de-risk", "effect": "Even defensives (FMCG) and havens (gold) sell — capital rotates to the dollar, not gold"},
            ]

        conn.close()
        # "Who drove it": intraday futures price × ΔOI (conviction vs intraday/leverage churn)
        try:
            from backend.quant import intraday_oi as _ioi
            _oi_view = _ioi.analyze(DB_PATH, date)
        except Exception as _e:
            _oi_view = {"available": False, "note": f"analyzer error: {_e}"}
        return {"success": True, "date": date, "prev_date": prev_date, "nifty": nifty,
                "trigger": trigger, "regime": regime, "has_causal_model": has_causal_model,
                "crude_spike": bool(crude_spike), "cross_assets": cross, "transmission": chain,
                "sectors": sectors, "energy_split": esplit, "breadth": breadth,
                "intraday_oi": _oi_view,
                "context": {"mkt_avg_pct": round(mkt_avg, 2)}}
    except Exception as e:
        return {"success": False, "detail": str(e)}


@app.get("/api/earnings-headlines")
def api_earnings_headlines(date: Optional[str] = None):
    """Live earnings-season headlines (RSS), tagged to NIFTY constituents — the
    idiosyncratic-driver overlay for the Macro Shock tab."""
    try:
        from backend.quant.earnings_feed import fetch_earnings_headlines
        return fetch_earnings_headlines(date)
    except Exception as e:
        return {"success": False, "detail": str(e), "headlines": []}


@app.get("/api/skew/vocabulary")
def api_get_skew_vocabulary():
    """
    Serve the skew engine's VOCABULARY dict to the frontend.
    """
    from quant.skew.skew_engine import VOCABULARY
    return {"success": True, "vocabulary": VOCABULARY}

@app.get("/api/event-calendar")
async def api_event_calendar(force_refresh: bool = False):
    try:
        tagged = await get_tagged_news(force_refresh=force_refresh)
        panel = build_panel(tagged)
        return {"success": True, "panel": panel}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/flows")
def get_flows():
    cash_days, cash_stale = fetch_nse_cash_sync()
    sip_months, sip_stale = fetch_amfi_sip_sync()
    sector_fpi, fpi_stale = fetch_sector_fpi_sync()
    
    bias_res = flow_bias(cash_days, sip_months)
    return {
        "success": True,
        "cash_stale": cash_stale,
        "sip_stale": sip_stale,
        "fpi_stale": fpi_stale,
        "bias": bias_res,
        "sector_fpi": sector_fpi
    }

@app.get("/api/flows-history")
def get_flows_history(limit: int = 60):
    import sqlite3
    from chain_store import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT flow_date, fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net,
                   fii_idx_fut_net, fii_stk_fut_net, fii_idx_opt_net, fii_stk_opt_net
            FROM fii_dii_flows
            ORDER BY flow_date DESC
            LIMIT ?
        """, (limit,))
        rows = c.fetchall()
        
        # Return newest first or oldest first? The chart usually wants oldest first.
        # Let's reverse them so it's chronological
        rows.reverse()
        
        data = []
        for r in rows:
            data.append({
                "date": r[0][:10],
                "fii_buy": r[1],
                "fii_sell": r[2],
                "fii_net": r[3],
                "dii_buy": r[4],
                "dii_sell": r[5],
                "dii_net": r[6],
                "fii_idx_fut_net": r[7] if r[7] is not None else 0,
                "fii_stk_fut_net": r[8] if r[8] is not None else 0,
                "fii_idx_opt_net": r[9] if r[9] is not None else 0,
                "fii_stk_opt_net": r[10] if r[10] is not None else 0
            })
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if 'conn' in locals():
            conn.close()

@app.get("/api/money-sentiment")
def api_money_sentiment(market_cap_cr: float = 47_000_000.0,
                        move_pct: Optional[float] = None,
                        basis_discount: Optional[bool] = None,
                        date: Optional[str] = None):
    """Money-vs-Sentiment view AS-OF `date` (or latest). Notional evaporated vs real
    money withdrawn vs delivery-conviction, with a 4-quadrant regime. Reads delivery +
    FII/DII from .state caches; NIFTY move from price_bars (override with ?move_pct=)."""
    try:
        from backend.quant import money_sentiment as _ms
        try:
            from bar_store import DB_PATH as _BARS_DB
        except Exception:
            _BARS_DB = None
        return _ms.build_view(db_path=_BARS_DB, market_cap_cr=market_cap_cr,
                              move_pct=move_pct, basis_discount=basis_discount, date=date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/money-sentiment/fetch-delivery")
def api_fetch_delivery(date: str, market_cap_cr: float = 47_000_000.0,
                       move_pct: Optional[float] = None):
    """Download NSE delivery for `date` (server-side), store it, and return the
    refreshed Money-vs-Sentiment view. Runs where the app runs (NSE must be reachable)."""
    try:
        from backend.quant import money_sentiment as _ms
        res = _ms.fetch_and_store_delivery(date)
        if not res.get("ok"):
            return {"success": False, "error": res.get("error"), "delivery": res}
        try:
            from bar_store import DB_PATH as _BARS_DB
        except Exception:
            _BARS_DB = None
        view = _ms.build_view(db_path=_BARS_DB, market_cap_cr=market_cap_cr,
                              move_pct=move_pct, date=date)
        return {"success": True, "delivery": res, "view": view["view"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

_IMPACT_CACHE = {"ts": 0.0, "data": None}

@app.get("/api/impact-monitor")
def api_impact_monitor(use_llm: bool = False, refresh: bool = False):
    """Live impact-decipher for the top banner: chains + signed driver balance + net
    tilt + HIGH-impact headlines to flash. Cached ~90s so 5-min polling from multiple
    clients doesn't spam the LLM (pass refresh=true to force)."""
    import time as _t
    try:
        if not refresh and _IMPACT_CACHE["data"] and (_t.time() - _IMPACT_CACHE["ts"] < 90):
            return {"success": True, "cached": True, **_IMPACT_CACHE["data"]}
        from backend.quant import impact_monitor as _im
        try:
            from bar_store import DB_PATH as _BARS_DB
        except Exception:
            _BARS_DB = None
        news_state = state_manager.read_state("news_state")
        data = _im.run(_BARS_DB, news_state, use_llm=use_llm)
        _IMPACT_CACHE["data"] = data
        _IMPACT_CACHE["ts"] = _t.time()
        return {"success": True, "cached": False, **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/nifty-move")
def api_nifty_move():
    """Live NIFTY spot + change vs the PRIOR session's close (for the top ticker bar).
    Tries the local DB for a settled daily (1d) close first if the day has ended,
    then tries yfinance ^NSEI (live intraday quote), and finally falls back to 1m DB bars."""
    import sqlite3
    from datetime import datetime, timezone
    
    # ---- 1) Try local settled EOD daily close first ----
    try:
        from bar_store import DB_PATH
        con = sqlite3.connect(DB_PATH)
        mrow = con.execute("SELECT close, ts FROM price_bars WHERE symbol='NIFTY' "
                           "AND timeframe='1m' ORDER BY ts DESC LIMIT 1").fetchone()
        if mrow:
            m_close, m_ts = mrow[0], mrow[1]
            m_date = m_ts[:10]
            # Check if we have a 1d candle for this exact day (or newer)
            drow = con.execute("SELECT close, ts FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
                               "AND substr(ts,1,10) >= ? ORDER BY ts DESC LIMIT 1", (m_date,)).fetchone()
            if drow:
                spot = drow[0]
                sdate = drow[1][:10]
                prow = con.execute("SELECT close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
                                   "AND substr(ts,1,10) < ? ORDER BY ts DESC LIMIT 1", (sdate,)).fetchone()
                prev = prow[0] if prow else spot
                chg = spot - prev
                con.close()
                return {"success": True, "spot": round(float(spot), 2), "prev_close": round(float(prev), 2),
                        "chg_pts": round(chg, 1), "chg_pct": round(chg / prev * 100, 2),
                        "source": "local_1d_settled", "as_of": m_ts}
        con.close()
    except Exception:
        pass

    # ---- 2) online: yfinance ^NSEI (live-ish index quote) ----
    try:
        import yfinance as yf
        t = yf.Ticker("^NSEI")
        spot = prev = None
        try:
            fi = t.fast_info
            spot = fi.get("last_price") if hasattr(fi, "get") else getattr(fi, "last_price", None)
            prev = fi.get("previous_close") if hasattr(fi, "get") else getattr(fi, "previous_close", None)
        except Exception:
            pass
        if not (spot and prev):
            h = t.history(period="2d", interval="1d")
            if len(h) >= 2:
                spot = float(h["Close"].iloc[-1]); prev = float(h["Close"].iloc[-2])
        if spot and prev:
            chg = spot - prev
            return {"success": True, "spot": round(float(spot), 2), "prev_close": round(float(prev), 2),
                    "chg_pts": round(chg, 1), "chg_pct": round(chg / prev * 100, 2),
                    "source": "yfinance", "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    except Exception:
        pass
    # ---- 3) fallback: local price_bars DB (1m last tick) ----
    try:
        from bar_store import DB_PATH
    except Exception:
        return {"success": False, "detail": "no bars db"}
    try:
        con = sqlite3.connect(DB_PATH)
        srow = con.execute("SELECT close, ts FROM price_bars WHERE symbol='NIFTY' "
                           "AND timeframe='1m' ORDER BY ts DESC LIMIT 1").fetchone()
        if not srow:
            con.close(); return {"success": False, "detail": "no NIFTY bars"}
        spot, sts = srow[0], srow[1]
        sdate = sts[:10]
        prow = con.execute("SELECT close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
                           "AND substr(ts,1,10) < ? ORDER BY ts DESC LIMIT 1", (sdate,)).fetchone()
        if not prow:
            prow = con.execute("SELECT close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1m' "
                               "AND substr(ts,1,10) < ? ORDER BY ts DESC LIMIT 1", (sdate,)).fetchone()
        con.close()
        if not prow or not prow[0]:
            return {"success": True, "spot": round(spot, 2), "prev_close": None,
                    "chg_pts": None, "chg_pct": None, "as_of": sts}
        prev = prow[0]
        chg = spot - prev
        return {"success": True, "spot": round(float(spot), 2), "prev_close": round(float(prev), 2),
                "chg_pts": round(chg, 1), "chg_pct": round(chg / prev * 100, 2),
                "source": "local_1m_fallback", "as_of": sts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update-news")
async def api_update_news():
    try:
        tagged = await get_tagged_news(force_refresh=True)
        # We need to run the news part of the pipeline to get regime and bias
        from backend.quant.market_regime import assess_regime, Driver
        from backend.quant.sector_tagging import sector_sentiment_from_gemini
        from backend.quant.decision_engine import index_bias
        from backend.quant.sector_map import sector_weights
        from datetime import datetime, timezone
        
        now = datetime.now(timezone.utc)
        prev = Driver(_prev_regime["v"]) if _prev_regime["v"] else None
        
        # Format the articles dicts to match what assess_regime expects
        from backend.quant.pipeline import _to_articles
        arts = _to_articles(tagged)
        
        # Interpretation Layer: Republish Check
        from backend.quant.regime_synthesis import detect_republished
        repub_res = detect_republished(tagged)

        regime = assess_regime(arts, now=now, half_life_hours=12.0, prev_regime=prev)
        sect_sent = sector_sentiment_from_gemini(tagged, now=now, half_life_hours=12.0)
        sw = sector_weights()
        bias, coverage, _ = index_bias(sect_sent, sw)
        surfaces = regime.surfaces_by_driver.get(regime.dominant, set())
        momentum = min(1.0, regime.conviction * (0.5 + 0.5 * min(len(surfaces), 4) / 4))
        
        from backend.quant.vol_attribution import vix_from_news
        vix_res = vix_from_news(tagged, now=now)
        vix_dict = {
            "value": vix_res.value,
            "as_of": vix_res.as_of,
            "source": vix_res.source,
            "age_hours": vix_res.age_hours,
            "stale": vix_res.stale,
            "note": vix_res.note
        }
        
        news_state = {
            "regime": {
                "dominant": regime.dominant.value,
                "conviction": float(regime.conviction),
                "flipped_from": regime.flipped_from.value if regime.flipped_from else None,
                "surfaces": sorted(s.value for s in surfaces),
                "vol_expansion": False # Handled dynamically by quant
            },
            "sector_sentiment": {k: float(v.get("combined", 0.0)) for k, v in sect_sent.items() if not k.startswith("__")},
            "drilldown": sect_sent.get("__drilldown", {}),
            "bias": float(bias),
            "coverage": float(coverage),
            "momentum": float(momentum),
            "articles": tagged,
            "india_vix": vix_dict,
            "republish_check": repub_res
        }
        state_manager.write_state("news_state", news_state)
        return {"success": True, "state": news_state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update-flows")
def api_update_flows():
    try:
        cash_days, cash_stale = fetch_nse_cash_sync()
        sip_months, sip_stale = fetch_amfi_sip_sync()
        sector_fpi, fpi_stale = fetch_sector_fpi_sync()
        
        bias_res = flow_bias(cash_days, sip_months)
        
        # Interpretation Layer: Bond & FX integration.
        # Reuse the REAL USDINR + India 10Y already fetched by /api/update-cues
        # (stored in cues_state) — no separate CCIL/RBI source needed.
        from backend.quant.bond_cues import read_bonds, fii_disambiguation, BondDay
        cues_state = state_manager.read_state("cues_state") or {}
        _cl = cues_state.get("close_levels", {})   # levels
        _cp = cues_state.get("cues", {})           # daily change: 10Y in bp, USDINR in %
        bond_fx_stale = not cues_state             # True if update-cues hasn't run yet

        y10 = _cl.get("India 10Y")
        d10_bp = _cp.get("India 10Y") or 0.0       # 10Y daily change, basis points
        usdinr = _cl.get("USDINR")
        usdinr_pct = _cp.get("USDINR") or 0.0      # USDINR daily change, percent

        if y10:
            bond_day = BondDay(
                yield_10y=float(y10),
                prev_yield_10y=float(y10) - d10_bp / 100.0,   # bp -> % to recover prev close
                usdinr=float(usdinr) if usdinr else None,
                prev_usdinr=(float(usdinr) / (1.0 + usdinr_pct / 100.0)) if usdinr else None,
            )
        else:
            # cues_state empty -> run /api/update-cues first. Flag, don't fake.
            bond_day = BondDay(yield_10y=0.0, prev_yield_10y=0.0)
            bond_fx_stale = True
        bond_read = read_bonds(bond_day)
        rupee_weaker = usdinr_pct > 0              # USDINR up = rupee weaker

        # We need latest FII cash figure
        fii_cash = 0.0
        if cash_days:
            fii_cash = cash_days[-1].fii_cash

        fii_read = fii_disambiguation(
            fii_cash_cr=fii_cash,
            yield_change_bps=bond_read.get("change_bps", 0.0),
            rupee_weaker=rupee_weaker
        )
        
        flows_state = {
            "cash_stale": cash_stale,
            "sip_stale": sip_stale,
            "fpi_stale": fpi_stale,
            "trend": bias_res.get("trend", {}),
            "flow_tilt": bias_res.get("flow_tilt", 0.0),
            "sector_fpi": sector_fpi,
            "formula_trace": bias_res.get("formula_trace"),
            "bond_cues": bond_read,
            "bond_fx_stale": bond_fx_stale,
            "fii_disambiguation": fii_read
        }
        state_manager.write_state("flows_state", flows_state)
        
        # Event calendar state (we couple it here for now as a slow path, or create update-events)
        # but let's write events_state too
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            tagged_events = loop.run_until_complete(get_tagged_news(force_refresh=False))
        finally:
            loop.close()
        
        panel = build_panel(tagged_events)
        state_manager.write_state("events_state", panel)
        
        # Write the nested macro part directly to macro_state
        macro_state = panel.get("us_macro", {})
        state_manager.write_state("macro_state", macro_state)
        
        return {"success": True, "flows_state": flows_state, "events_state": panel, "macro_state": macro_state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update-cues")
def api_update_cues():
    try:
        cues = fetch_global_cues(force_refresh=True)
        if cues.get("success"):
            # Store full structure for curve_regime & flows hooks
            state_manager.write_state("cues_state", {
                "cues": cues.get("cues", {}),
                "close_levels": cues.get("close_levels", {}),
                "session_states": cues.get("session_states", {}),
                "cue_as_of": cues.get("cue_as_of", {}),
                "strengths": cues.get("strengths", {}),
                "curve_regime": cues.get("curve_regime", {}),
                "as_of": datetime.now().isoformat()
            })
        return cues
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/api/run-pipeline")
async def api_run_pipeline(req: PipelineRequest):
    try:
        # LOAD ALL DECOUPLED STATES
        news_state = state_manager.read_state("news_state")
        flows_state = state_manager.read_state("flows_state")
        events_state = state_manager.read_state("events_state")
        macro_state = state_manager.read_state("macro_state")
        cues_state = state_manager.read_state("cues_state")
        
        # Backward compat if force_news_refresh is true (e.g. from old frontend code)
        if req.force_news_refresh:
            await api_update_news()
            news_state = state_manager.read_state("news_state")
            
        res = run_pipeline(
            chain=req.chain,
            half_life_hours=req.half_life_hours,
            risk_cfg=req.risk_cfg,
            book=req.book,
            current_drawdown_pct=req.current_drawdown_pct,
            trade_max_loss_pts=req.trade_max_loss_pts,
            trade_delta=req.trade_delta,
            trade_vega=req.trade_vega,
            override_structure=req.override_structure,
            override_is_premium_sell=req.override_is_premium_sell,
            news_state=news_state,
            flows_state=flows_state,
            events_state=events_state,
            macro_state=macro_state,
            cues_state=cues_state,
            opt_weights=req.opt_weights,
            opt_bias=req.opt_bias,
            opt_min_pop=req.opt_min_pop,
            opt_allow_undefined=req.opt_allow_undefined,
            opt_cost_per_leg=req.opt_cost_per_leg,
            opt_window_pts=req.opt_window_pts,
            opt_max_wing=req.opt_max_wing,
            opt_top_n=req.opt_top_n,
            opt_max_loss_budget=req.opt_max_loss_budget,
            opt_allow_bad_rnd=req.opt_allow_bad_rnd
        )
        _prev_regime["v"] = res["regime"]["dominant"]
        
        # Attach chain_meta so the frontend can retain spot, pcr, and max_pain exactly as they were used
        res['chain_meta'] = req.chain
        
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


import tempfile
import shutil
import json
from backend.quant.nse_csv_loader import load_nse_csv, add_oi_change_pct, window_chain

@app.post("/api/upload-chain")
async def api_upload_chain(
    file: UploadFile = File(...),
    spot: float = Form(...),
    expiry: str = Form(...),
    vix: Optional[float] = Form(None),
    payload: str = Form(None)
):
    try:
        # Save uploaded file temporarily
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, file.filename)
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        # Save to DB losslessly first
        from chain_store import save_from_nse_csv, days_to_expiry
        cid = save_from_nse_csv(temp_path, expiry=expiry, spot=spot, vix=vix)
        
        # Derive days from the expiry date for analysis
        days = days_to_expiry(expiry)
        
        # Parse and prepare chain for the UI return (filtered/windowed as usual)
        chain = load_nse_csv(temp_path, spot=spot, days=days, oi_in_lakh=True)
        chain = add_oi_change_pct(chain)
        chain = window_chain(chain)
        chain["expiry"] = expiry

        # Clean up
        shutil.rmtree(temp_dir)
        
        # Calculate max pain
        def compute_max_pain(chain_dict):
            min_pain = float('inf')
            max_pain_strike = 0
            S = chain_dict["strikes"]
            C_OI = chain_dict["call_oi"]
            P_OI = chain_dict["put_oi"]
            for target_k in S:
                pain = 0
                for i, k in enumerate(S):
                    if k < target_k:
                        pain += (target_k - k) * C_OI[i]
                    if k > target_k:
                        pain += (k - target_k) * P_OI[i]
                if pain < min_pain:
                    min_pain = pain
                    max_pain_strike = target_k
            return max_pain_strike
        

        chain["max_pain"] = compute_max_pain(chain)
        
        # Build OptionRow structure for the frontend
        csv_rows = []
        for i, k in enumerate(chain["strikes"]):
            csv_rows.append({
                "strike": k,
                "call_ltp": chain["call_ltp"][i],
                "put_ltp": chain["put_ltp"][i],
                "call_oi": chain["call_oi"][i],
                "put_oi": chain["put_oi"][i],
                "call_oichg": chain["call_oi_chg_pct"][i],
                "put_oichg": chain["put_oi_chg_pct"][i],
                "iv": chain["call_iv"][i] if chain["call_iv"][i] is not None else 0.0
            })
        chain["rows"] = csv_rows
        print('CSV ROWS LENGTH:', len(csv_rows))
        print('CHAIN KEYS:', chain.keys())


        
        # Load state
        news_state = state_manager.read_state("news_state")
        flows_state = state_manager.read_state("flows_state")
        events_state = state_manager.read_state("events_state")
        macro_state = state_manager.read_state("macro_state")
        cues_state = state_manager.read_state("cues_state")
        
        # Parse payload if provided
        data = json.loads(payload) if payload else {}
        
        res = run_pipeline(
            chain=chain,
            half_life_hours=data.get("half_life_hours", 12.0),
            risk_cfg=data.get("risk_cfg"),
            book=data.get("book"),
            current_drawdown_pct=data.get("current_drawdown_pct", 0.0),
            trade_max_loss_pts=data.get("trade_max_loss_pts"),
            trade_delta=data.get("trade_delta"),
            trade_vega=data.get("trade_vega"),
            override_structure=data.get("override_structure"),
            override_is_premium_sell=data.get("override_is_premium_sell"),
            news_state=news_state,
            flows_state=flows_state,
            events_state=events_state,
            macro_state=macro_state,
            cues_state=cues_state,
            opt_weights=data.get("opt_weights"),
            opt_bias=data.get("opt_bias"),
            opt_max_loss_budget=data.get("opt_max_loss_budget", 0),
            opt_min_pop=data.get("opt_min_pop", 0.0),
            opt_cost_per_leg=data.get("opt_cost_per_leg", 5.0),
            opt_window_pts=data.get("opt_window_pts", 600),
            opt_max_wing=data.get("opt_max_wing", 500),
            opt_allow_undefined=data.get("opt_allow_undefined", False),
            opt_top_n=data.get("opt_top_n", 3),
            opt_allow_bad_rnd=data.get("opt_allow_bad_rnd", False)
        )
        res['chain_meta'] = chain
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from chain_store import (
    capture_options, load_capture, delete_capture,
    capture_live_options, load_live_capture, delete_live_capture, DB_PATH
)

@app.get("/api/captures")
def api_captures(mode: str = "historical"):
    try:
        if mode == "live":
            opts = capture_live_options()
        else:
            opts = capture_options()
        return {"success": True, "captures": opts}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/load-capture/{capture_id}")
def api_load_capture(capture_id: int, expiry: str = None, mode: str = "historical"):
    try:
        if mode == "live":
            cap = load_live_capture(capture_id)
        else:
            cap = load_capture(capture_id, expiry=expiry)
        if not cap:
            raise HTTPException(status_code=404, detail="Capture not found")
        return {"success": True, "capture": cap}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/captures/{capture_id}")
def api_delete_capture(capture_id: int, mode: str = "historical"):
    try:
        if mode == "live":
            success = delete_live_capture(capture_id)
        else:
            success = delete_capture(capture_id)
        if not success:
            raise HTTPException(status_code=404, detail="Capture not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

from pydantic import BaseModel
from typing import List, Tuple, Any

class CompareRequest(BaseModel):
    capture_a: int
    capture_b: int
    legs: Optional[List[List[Any]]] = None # [side, strike, sign]
    expiry_date: Optional[str] = None

from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class BreezeSaveRequest(BaseModel):
    rows: List[Dict[str, Any]]
    spot: float
    expiry: str
    vix: Optional[float] = None

@app.post("/api/save-breeze-chain")
async def api_save_breeze_chain(req: BreezeSaveRequest):
    try:
        from chain_store import save_live_from_json_rows, resolve_capture_vix
        from bar_store import get_latest_vix
        # VIX comes from the real store (latest captured India VIX tick), NOT a client
        # constant. Client value is a fallback only; if neither exists, persist NULL —
        # never a placeholder (capture brief §3). Source is recorded on the capture note.
        store_vix = None
        try:
            store_vix = get_latest_vix()
        except Exception:
            store_vix = None
        vix_val, vix_src = resolve_capture_vix(store_vix, req.vix)
        cid = save_live_from_json_rows(
            req.rows,
            expiry=req.expiry,
            spot=req.spot,
            vix=vix_val,
            note=f"breeze_api_live|vix_src={vix_src}",
            exchange_code="NFO",
            underlying="NIFTY",
            status="complete",
            trigger="manual"
        )
        return {"success": True, "capture_id": cid, "vix": vix_val, "vix_source": vix_src}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to save Breeze chain: {str(e)}")

@app.post("/api/admin/reinit-capture-view")
def api_reinit_capture_view():
    """
    Recreate the chain_snapshots view (DROP/CREATE) so the D-CAP-02 columns
    (quote_state, price, price_source, derived mid) exist on the live DB, then verify the
    columns and report distributions. Also answers capture-brief acceptance row 4:
    the historical MID_IS_LTP row count + a 10-row sample. Mutates only the view DDL.
    """
    import sqlite3
    from chain_store import init_db, DB_PATH
    try:
        init_db(DB_PATH)  # DROP VIEW IF EXISTS chain_snapshots + CREATE VIEW with new columns
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(chain_snapshots)")]
        required = ["quote_state", "price", "price_source", "mid", "ltp", "bid", "ask"]
        missing = [c for c in required if c not in cols]

        qs_counts, ps_counts, ltp_rows, sample = {}, {}, 0, []
        if not missing:
            qs_counts = {r["quote_state"]: r["n"] for r in conn.execute(
                "SELECT quote_state, COUNT(*) AS n FROM chain_snapshots GROUP BY quote_state")}
            ps_counts = {r["price_source"]: r["n"] for r in conn.execute(
                "SELECT price_source, COUNT(*) AS n FROM chain_snapshots GROUP BY price_source")}
            ltp_rows = ps_counts.get("LTP_RECENT", 0)   # historical MID_IS_LTP rows (acceptance row 4)
            sample = [dict(r) for r in conn.execute(
                "SELECT ts, strike, cp, bid, ask, ltp, mid, price, price_source, quote_state "
                "FROM chain_snapshots WHERE price_source = 'LTP_RECENT' LIMIT 10")]
        conn.close()
        return {"success": True, "view_recreated": True, "columns": cols,
                "missing_columns": missing, "quote_state_counts": qs_counts,
                "price_source_counts": ps_counts, "mid_is_ltp_rows": ltp_rows,
                "sample_ltp_rows": sample}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Background Option Chain Auto-Sync Scheduler
active_schedulers = {} # {symbol: (asyncio.Task, interval_minutes, started_at_iso)}
symbol_locks = {}

# Hardcoded static holiday list for NSE 2026
NSE_HOLIDAYS_2026 = {
    "2026-01-26", # Republic Day
    "2026-03-06", # Holi
    "2026-03-20", # Good Friday
    "2026-04-14", # Ambedkar Jayanti
    "2026-05-01", # Maharashtra Day
    "2026-10-02", # Gandhi Jayanti
    "2026-10-23", # Dussehra
    "2026-11-12", # Diwali
    "2026-12-25", # Christmas
}

def is_market_active(symbol: str = "NIFTY") -> bool:
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    
    # Check weekday (5=Saturday, 6=Sunday)
    if now_ist.weekday() >= 5:
        return False
        
    # Check holiday calendar
    date_str = now_ist.strftime("%Y-%m-%d")
    if date_str in NSE_HOLIDAYS_2026:
        return False
        
    # Standard NIFTY trading hours: 09:15 - 15:30 IST
    # No pre-open captures before 09:15
    market_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    if now_ist < market_start or now_ist > market_end:
        return False
        
    return True

class ScheduleRequest(BaseModel):
    session_token: str
    expiry_date: str
    symbol: str
    interval: int # minutes

class StopScheduleRequest(BaseModel):
    symbol: str

async def option_chain_sync_loop(session_token: str, expiry_date: str, symbol: str, interval: int):
    import subprocess
    import json
    import asyncio
    import time
    from datetime import datetime, timezone
    from backend.quant.breeze_loader import process_breeze_chain
    from chain_store import save_live_from_json_rows
    from backend.timeutil import to_db_ts, to_db_minute
    
    global symbol_locks
    lock = symbol_locks.setdefault(symbol, asyncio.Lock())
    
    print(f"Option Chain Auto-Sync started for {symbol} every {interval}m.")
    try:
        while True:
            # Epoch-aligned seconds alignment sleep
            period = interval * 60
            seconds_to_sleep = period - (time.time() % period)
            if seconds_to_sleep < 1.0:
                seconds_to_sleep += period
            await asyncio.sleep(seconds_to_sleep)
            
            # Check market session gate
            if not is_market_active(symbol):
                print(f"[{datetime.now()}] Market closed. Skipping auto-sync tick for {symbol}.")
                continue
                
            # Check overlap concurrency lock
            if lock.locked():
                print(f"[{datetime.now()}] Warning: Previous sync task for {symbol} still running. Skipping this tick.")
                continue
                
            async with lock:
                trigger_time = datetime.now(timezone.utc)
                captured_at_str = to_db_ts(trigger_time)
                snapshot_minute_str = to_db_minute(trigger_time)
                
                try:
                    breeze_symbol = BREEZE_SYMBOL_MAP.get(symbol.upper(), symbol)
                    cmd = [
                        "./scratch_scripts/breeze_env/bin/python",
                        "scratch_scripts/fetch_breeze_json.py",
                        session_token,
                        expiry_date,
                        breeze_symbol
                    ]
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    stdout, stderr = await proc.communicate()
                    
                    status = "complete"
                    raw_data = []
                    spot_price = 0.0
                    
                    if proc.returncode == 0:
                        response = json.loads(stdout.decode())
                        if response.get("error"):
                            err_str = str(response.get("error")).lower()
                            status = "auth_failed" if "session" in err_str or "token" in err_str else "failed"
                            print(f"[{datetime.now()}] Auto-sync {symbol} API returned error: {response['error']}")
                        else:
                            raw_data = response.get("Success", [])
                            status = response.get("status", "complete")
                    else:
                        status = "failed"
                        print(f"[{datetime.now()}] Auto-sync {symbol} script failed: {stderr.decode()}")
                        
                    if status in ("complete", "calls_only", "puts_only") and len(raw_data) > 0:
                        exp_dt = datetime.fromisoformat(expiry_date.replace('Z', '+00:00'))
                        diff = exp_dt - trigger_time.astimezone(exp_dt.tzinfo)
                        days = max(diff.total_seconds() / 86400.0, 0.01)
                        
                        rows, spot_price = process_breeze_chain(raw_data, days_to_expiry=days)
                    else:
                        rows = []
                        spot_price = 0.0
                        
                    cid = save_live_from_json_rows(
                        rows,
                        expiry=expiry_date.split('T')[0],
                        spot=spot_price,
                        vix=None,
                        captured_at=captured_at_str,
                        exchange_code="NFO",
                        underlying=symbol,
                        status=status,
                        trigger="manual", # Or EOD if specified
                        note=f"auto_sync_{interval}m"
                    )
                    print(f"[{datetime.now()}] Auto-sync saved {symbol} option chain. ID: {cid}, status: {status}")
                except Exception as loop_err:
                    print(f"[{datetime.now()}] Exception during auto-sync run: {loop_err}")
                    
    except asyncio.CancelledError:
        print(f"Auto-sync task for {symbol} was cancelled.")
        raise

@app.post("/api/schedule/start")
async def api_schedule_start(req: ScheduleRequest):
    global active_schedulers
    import asyncio
    from datetime import datetime
    
    if req.symbol in active_schedulers:
        task, _, _ = active_schedulers[req.symbol]
        task.cancel()
        
    loop = asyncio.get_running_loop()
    task = loop.create_task(
        option_chain_sync_loop(
            req.session_token,
            req.expiry_date,
            req.symbol,
            req.interval
        )
    )
    started_at = datetime.now().isoformat()
    active_schedulers[req.symbol] = (task, req.interval, started_at)
    
    return {"success": True, "message": f"Successfully scheduled auto-sync for {req.symbol} every {req.interval} minutes."}

@app.post("/api/schedule/stop")
async def api_schedule_stop(req: StopScheduleRequest):
    global active_schedulers
    if req.symbol in active_schedulers:
        task, _, _ = active_schedulers[req.symbol]
        task.cancel()
        del active_schedulers[req.symbol]
        return {"success": True, "message": f"Stopped auto-sync for {req.symbol}."}
    return {"success": False, "message": f"No active schedule found for {req.symbol}."}

@app.get("/api/schedule/status")
def api_schedule_status(symbol: str = "NIFTY"):
    global active_schedulers
    if symbol in active_schedulers:
        _, interval, started_at = active_schedulers[symbol]
        return {
            "active": True,
            "interval": interval,
            "started_at": started_at
        }
    return {"active": False}

@app.get("/api/nifty-history")
def api_get_nifty_history(session_token: str, from_date: str, to_date: str, interval: str = "1day"):
    import subprocess
    import json
    if not session_token or not from_date or not to_date:
        raise HTTPException(status_code=400, detail="Missing parameters")
        
    try:
        cmd = [
            "./scratch_scripts/breeze_env/bin/python",
            "scratch_scripts/fetch_historical.py",
            session_token,
            from_date,
            to_date,
            interval
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Breeze script failed: {result.stderr}")
            
        response = json.loads(result.stdout)
        
        if response.get("error"):
            raise HTTPException(status_code=500, detail=f"Breeze API Error: {response['error']}")
            
        return {"success": True, "data": response.get("data", [])}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class HistorySaveRequest(BaseModel):
    records: List[Dict[str, Any]]

@app.post("/api/save-nifty-history")
def api_save_nifty_history(req: HistorySaveRequest):
    try:
        from chain_store import save_daily_prices
        save_daily_prices(req.records)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/nifty-history-db")
def api_get_nifty_history_db(limit: int = 365):
    try:
        from chain_store import get_daily_prices
        data = get_daily_prices(limit=limit)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bars/symbols")
def api_get_bars_symbols():
    try:
        from bar_store import get_stored_symbols
        db_symbols = get_stored_symbols() or []
        
        # Load from nifty-50-stock-list.csv if present
        csv_symbols = []
        csv_path = "nifty-50-stock-list.csv"
        if os.path.exists(csv_path):
            import csv
            try:
                with open(csv_path, mode='r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    next(reader, None) # skip header
                    for row in reader:
                        if row and row[0]:
                            symbol_val = row[0].strip().upper()
                            if symbol_val:
                                csv_symbols.append(symbol_val)
            except Exception as csv_err:
                print(f"Error reading symbols CSV: {csv_err}")
                
        all_symbols = []
        seen = set()
        for sym in csv_symbols:
            if sym not in seen:
                seen.add(sym)
                all_symbols.append(sym)
        for sym in db_symbols:
            if sym not in seen:
                seen.add(sym)
                all_symbols.append(sym)
        if "NIFTY" not in seen:
            all_symbols.insert(0, "NIFTY")
            
        for sym in ["GOLD", "SILVER", "COPPER", "CRUDEOIL", "USDINR", "GIFTNIFTY"]:
            if sym not in seen:
                seen.add(sym)
                all_symbols.append(sym)
                
        return {"success": True, "symbols": all_symbols}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def resolve_exchange(symbol: str) -> str:
    sym = symbol.upper()
    if sym in ("GOLD", "SILVER", "COPPER", "CRUDEOIL"):
        return "MCX"
    elif sym == "USDINR":
        return "CDS"
    elif sym in ("NIFTY_FUT_1", "NIFTY_FUT_2"):
        return "NFO"
    elif sym == "GIFTNIFTY":
        return "NSEIX"
    return "NSE"

@app.get("/api/bars/range")
def api_get_bars_range(symbol: str = "NIFTY", tf: str = "1day"):
    try:
        from bar_store import get_bar_range
        exchange = resolve_exchange(symbol)
        range_info = get_bar_range(exchange=exchange, symbol=symbol, timeframe=tf)
        return {"success": True, **range_info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bars")
def api_get_bars(symbol: str = "NIFTY", tf: str = "1m", start: Optional[str] = None, end: Optional[str] = None):
    try:
        from bar_store import get_bars
        from backend.timeutil import to_db_ts
        start_utc = to_db_ts(start) if start else None
        end_utc = to_db_ts(end) if end else None
        
        exchange = resolve_exchange(symbol)
        bars = get_bars(exchange=exchange, symbol=symbol, timeframe=tf, start=start_utc, end=end_utc)
        return {"success": True, "data": bars}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bars/realized-vol")
def api_get_realized_vol(symbol: str = "NIFTY", days: int = 20):
    try:
        from bar_store import realized_vol
        exchange = resolve_exchange(symbol)
        res = realized_vol(exchange=exchange, symbol=symbol, days=days)
        if "error" in res:
            raise HTTPException(status_code=400, detail=res["error"])
        return {"success": True, "data": res}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/compare-captures")
def api_compare_captures(req: CompareRequest):
    try:
        from chain_compare import compare, liquidity_volume_analysis
        from strategy_compare import strategy_pnl_comparison, price_comparison
        
        chain_cmp = compare(req.capture_a, req.capture_b)
        liq_cmp = liquidity_volume_analysis(req.capture_a, req.capture_b)
        price_cmp = price_comparison(req.capture_a, req.capture_b)
        
        strat_cmp = None
        if req.legs and req.expiry_date:
            strat_cmp = strategy_pnl_comparison(req.legs, req.capture_a, req.capture_b, req.expiry_date)
            
        return {
            "success": True,
            "chain_comparison": chain_cmp,
            "liquidity_analysis": liq_cmp,
            "price_comparison": price_cmp,
            "strategy_comparison": strat_cmp
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

from pydantic import BaseModel
from backend.quant.recommended_strikes import recommend_strikes
from backend.quant.portfolio import add_position, get_open_positions, close_position
from strategy_compare import strategy_pnl_comparison

class RecommendStrikesReq(BaseModel):
    family: str
    capture_id: int
    expected_move: float

@app.post("/api/recommend-strikes")
def api_recommend_strikes(req: RecommendStrikesReq):
    try:
        from chain_store import load_capture
        import numpy as np
        
        cap = load_capture(req.capture_id)
        if not cap:
            raise Exception("Capture not found")
            
        spot = cap["spot"]
        strikes = cap["strikes"]
        
        K = np.array(strikes, float)
        coi = np.array(cap.get("call_oi", np.ones_like(K)), float)
        poi = np.array(cap.get("put_oi", np.ones_like(K)), float)
        
        below = K < spot
        above = K > spot
        support_wall = float(K[below][np.argmax(poi[below])]) if below.any() else spot
        resist_wall = float(K[above][np.argmax(coi[above])]) if above.any() else spot
        
        legs = recommend_strikes(req.family, spot, req.expected_move, support_wall, resist_wall, strikes)
        return {"success": True, "legs": legs}
    except Exception as e:
        return {"success": False, "detail": str(e)}

class PortfolioAddReq(BaseModel):
    legs: list
    expiry: str
    entry_capture_id: int
    source: str
    lots: int = 1
    lot_size: int = NIFTY_LOT_SIZE
    lineage: dict = None

@app.post("/api/portfolio/add")
def api_portfolio_add(req: PortfolioAddReq):
    try:
        pos_id = add_position(req.legs, req.expiry, req.entry_capture_id, req.source, req.lots, req.lot_size, req.lineage)
        return {"success": True, "position_id": pos_id}
    except Exception as e:
        return {"success": False, "detail": str(e)}

@app.get("/api/portfolio/list")
def api_portfolio_list():
    try:
        return {"success": True, "positions": get_open_positions()}
    except Exception as e:
        return {"success": False, "detail": str(e)}

@app.post("/api/portfolio/close/{pos_id}")
def api_portfolio_close(pos_id: str):
    try:
        res = close_position(pos_id)
        return {"success": res}
    except Exception as e:
        return {"success": False, "detail": str(e)}

@app.get("/api/portfolio/value")
def api_portfolio_value(capture_id: int):
    try:
        import sqlite3
        from chain_store import list_captures, DB_PATH
        positions = get_open_positions()
        valued = []
        for p in positions:
            # Find the latest capture that actually contains this position's expiry
            valid_caps = list_captures(expiry=p["expiry"])
            # If no valid caps found, fallback to the requested capture_id (which will likely fail and return 'N/A')
            latest_capture_id_for_pos = valid_caps[0]["capture_id"] if valid_caps else capture_id
            
            res = strategy_pnl_comparison(p["legs"], capture_id, latest_capture_id_for_pos, p["expiry"], lot_size=p["lot_size"])
            
            # Verify if the entry capture actually exists in the DB, else fallback to latest
            entry_cap_id = p["entry_capture_id"]
            if entry_cap_id:
                with sqlite3.connect(DB_PATH) as conn:
                    exists = conn.execute("SELECT 1 FROM captures WHERE capture_id=?", (entry_cap_id,)).fetchone()
                if not exists:
                    entry_cap_id = latest_capture_id_for_pos
            else:
                entry_cap_id = latest_capture_id_for_pos

            # Also fetch the original entry prices for the detailed view
            entry_res = strategy_pnl_comparison(p["legs"], entry_cap_id, entry_cap_id, p["expiry"], lot_size=p["lot_size"])
            res["entry_prices"] = entry_res.get("prices_a", [])
            
            p_valued = dict(p)
            p_valued["valuation"] = res
            valued.append(p_valued)
        return {"success": True, "positions": valued}
    except Exception as e:
        return {"success": False, "detail": str(e)}

@app.get("/api/fundamentals")
def api_get_fundamentals():
    try:
        from backend.quant.fundamentals import get_screened_constituents
        data = get_screened_constituents()
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fundamentals/update")
def api_update_fundamentals():
    try:
        from backend.quant.fundamentals import seed_reliance_fundamentals, get_screened_constituents
        seed_reliance_fundamentals() # Refreshes/seeds database records
        data = get_screened_constituents()
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def auto_expiry_cleanup_loop():
    # Background loop to safeguard expiry day closing price collection
    import asyncio
    import os
    import json
    import subprocess
    from datetime import datetime, timezone, timedelta
    
    # Sleep 30 seconds on startup to let server fully bind
    await asyncio.sleep(30)
    
    while True:
        try:
            # IST timezone
            IST = timezone(timedelta(hours=5, minutes=30))
            now_ist = datetime.now(IST)
            
            # Check after market close (from 15:40 IST to 23:59 IST)
            if now_ist.hour >= 15 and now_ist.minute >= 40:
                today_str = now_ist.strftime("%Y-%m-%d")
                
                # Fetch active NIFTY expiries dynamically from exchange list
                expiries_res = api_exchange_expiries(symbol="NIFTY")
                if expiries_res.get("success"):
                    active_expiries = [e[:10] for e in expiries_res["expiries"]]
                    
                    if today_str in active_expiries:
                        # Today is indeed a Nifty options expiry day!
                        # Verify if we already have the closing capture saved in Google Drive DB
                        from chain_store import DB_PATH
                        import sqlite3
                        
                        conn = sqlite3.connect(DB_PATH)
                        # Check if a Nifty capture exists between 15:00 and 15:35 IST (09:30 and 10:05 UTC)
                        has_close_cap = conn.execute("""
                            SELECT COUNT(*) FROM captures 
                            WHERE underlying='NIFTY' 
                            AND captured_at >= ? AND captured_at <= ?
                        """, (f"{today_str}T09:30:00Z", f"{today_str}T10:05:00Z")).fetchone()[0] > 0
                        conn.close()
                        
                        if not has_close_cap:
                            print(f"[{datetime.now()}] [AutoExpiryCleanup] Detected missing NIFTY closing capture on expiry day {today_str}. Triggering post-market settled sync...")
                            
                            # Read cached Breeze session token
                            breeze_session = ""
                            session_file = f"breezesession/session_{today_str}.json"
                            if os.path.exists(session_file):
                                with open(session_file, "r") as sf:
                                    session_data = json.load(sf)
                                    breeze_session = session_data.get("session_token")
                            
                            if breeze_session:
                                cmd = [
                                    "./scratch_scripts/breeze_env/bin/python",
                                    "scratch_scripts/fetch_historical_option_chain.py",
                                    breeze_session,
                                    f"{today_str}T06:00:00.000Z",
                                    "NIFTY",
                                    "1minute"
                                ]
                                res = subprocess.run(cmd, capture_output=True, text=True)
                                print(f"[{datetime.now()}] [AutoExpiryCleanup] Completed backfill: {res.stdout.strip()}")
                            else:
                                print(f"[{datetime.now()}] [AutoExpiryCleanup] No valid Breeze session token found for {today_str}. Skipping auto backfill.")
        except Exception as e:
            print(f"[{datetime.now()}] [AutoExpiryCleanup] Error: {str(e)}")
            
        # Sleep for 1 hour
        await asyncio.sleep(3600)

async def eod_audit_loop():
    """5 PM (17:00 IST) end-of-day DATA AUDIT. Once per day, after the 15:30 close, the
    data agent scans the EXISTING tables (price_bars for cash, captures/chain_rows for the
    option chain) via data_health.missing_report and writes the result to
    .state/last_audit.json. The sidebar badge / Data Agent panel reads that cached result,
    so 'is my data up to the mark?' is answered from the most recent EOD scan — no download,
    no new tables. Because it runs post-close, today is a completed session (never 'thin')."""
    import asyncio, os, json
    from datetime import datetime, timezone, timedelta

    await asyncio.sleep(30)                       # let the server bind
    IST = timezone(timedelta(hours=5, minutes=30))
    state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              ".state", "last_audit.json")
    os.makedirs(os.path.dirname(state_path), exist_ok=True)

    def _already_ran_today(day_str: str) -> bool:
        try:
            with open(state_path) as f:
                return json.load(f).get("audit_date") == day_str
        except Exception:
            return False

    while True:
        try:
            try:
                import agent_settings
                if not agent_settings.agent_enabled():
                    await asyncio.sleep(1800); continue          # agent switched off -> idle
            except Exception:
                pass
            now_ist = datetime.now(IST)
            today_str = now_ist.strftime("%Y-%m-%d")
            # fire once per day at/after 17:00 IST
            if now_ist.hour >= 17 and not _already_ran_today(today_str):
                from chain_store import DB_PATH
                from data_agent.quality.data_health import missing_report
                # run the DB scan OFF the event loop so it can never block API requests
                rep = await asyncio.to_thread(missing_report, DB_PATH)
                rep["audit_date"] = today_str
                rep["ran_at"] = datetime.now(timezone.utc).isoformat()
                with open(state_path, "w") as f:
                    json.dump(rep, f)
                print(f"[{datetime.now()}] [EODAudit] {today_str}: {rep['headline']}")
        except Exception as e:
            print(f"[{datetime.now()}] [EODAudit] Error: {e}")
        await asyncio.sleep(1800)                 # re-check every 30 min


@app.on_event("startup")
async def start_background_tasks():
    import asyncio
    asyncio.create_task(auto_expiry_cleanup_loop())
    asyncio.create_task(eod_audit_loop())




# ==========================================================================
# Strategy Desk — directional-momentum framework endpoints
# (facade lives in strategy_framework/api.py)
# ==========================================================================
try:
    from strategy_framework import api as _strat_api
    _STRAT_OK = True

    # The framework returns numpy scalars (float64 / bool_ / int64) and can emit
    # NaN/Inf. FastAPI's JSON encoder rejects those -> 500 Internal Server Error.
    # Coerce every facade method's output to JSON-safe natives at the boundary so
    # ALL /api/strategy/* routes are covered in one place (idempotent on clean data).
    import functools as _ft, math as _math

    def _strat_json(o):
        if isinstance(o, dict):
            return {k: _strat_json(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_strat_json(v) for v in o]
        if type(o).__module__ == 'numpy':
            o = o.tolist() if hasattr(o, 'tolist') else o
            if isinstance(o, (list, dict)):
                return _strat_json(o)
        if isinstance(o, float):
            return 0.0 if (_math.isnan(o) or _math.isinf(o)) else o
        return o

    def _strat_wrap(fn):
        @_ft.wraps(fn)
        def _w(*a, **k):
            return _strat_json(fn(*a, **k))
        return _w

    for _n in (
        "suggest", "add_suggested", "drawdown_insurance", "add_candidate",
        "get_portfolio", "add_position", "remove_position", "clear_portfolio",
        "config_summary", "list_expiries", "signal_backtest", "signal_backtest_all",
        "features_backfill", "features_view", "features_backfill_start",
        "features_backfill_status", "features_clear", "feature_names_list",
        "feature_window_audit", "set_momentum_window", "market_health",
        "signal_phase_grid", "signal_timeseries", "signal_regime_horizon",
        "attribution", "signal_correlation", "signal_horizon_curve",
        "signal_test_start", "signal_test_status", "chain_at", "simulate", "backtest",
    ):
        _f = getattr(_strat_api, _n, None)
        if callable(_f):
            setattr(_strat_api, _n, _strat_wrap(_f))
except Exception as _e:  # pragma: no cover
    _STRAT_OK = False
    print(f"[StrategyDesk] framework import failed: {_e}")


from typing import Optional, List, Dict, Any


class StratAddPosition(BaseModel):
    kind: str                       # option_strategy | future | stock
    symbol: Optional[str] = None
    entry_price: Optional[float] = None
    qty: Optional[int] = None
    lot_size: Optional[int] = None
    family: Optional[str] = None
    legs: Optional[List[Any]] = None
    entry_prices: Optional[Dict[str, Any]] = None
    label: Optional[str] = None
    exchange: Optional[str] = None       # NSE | BSE | NFO
    side: Optional[str] = None           # long | short (option leg / stock / future)
    expiry: Optional[str] = None         # futures contract expiry (from DB)


class StratBookBacktestReq(BaseModel):
    pos_id: str
    entry_ts: str
    expiry: Optional[str] = None         # option expiry OR futures/stock end date
    exit_mode: str = "manage"
    stop_loss: bool = False
    stop_loss_rupees: Optional[float] = None
    take_profit: bool = False
    take_profit_frac: float = 0.6


class StratBacktestReq(BaseModel):
    mode: str = "auto"              # auto | book
    expiry: Optional[str] = None
    exit_mode: str = "manage"       # manage | horizon | expiry
    hold: int = 2
    freq_minutes: Optional[float] = None   # entry cadence; None = auto
    roll_directional: bool = False         # also roll verticals/long options
    window_days: Optional[float] = None    # session-days window; None = all
    stop_loss: bool = False                # manage: cut early vs hold to expiry
    stop_loss_rupees: Optional[float] = None  # fixed ₹ stop; None = auto threshold
    cooldown_min: Optional[float] = None   # min minutes between adjustments
    max_rolls: Optional[int] = None        # exit after this many adjustments
    persist_near: Optional[int] = None     # confirmation snapshots before acting
    harvest: bool = False                  # opportunistic premium harvest on safe wing
    min_harvest_inr: float = 100.0         # min net ₹/lot to justify a harvest
    take_profit: bool = False              # book profit at a fraction of max credit
    take_profit_frac: float = 0.6          # e.g. 0.6 = close at 60% of max profit
    max_manage: Optional[int] = None       # monitoring checks spread across the trade
    min_edge_cost_mult: float = 0.0        # cost-edge gate: 1σ move must clear N× round-trip cost (0=off)
    mps_benchmark: str = "off"             # MPS0 %-of-max ceiling: off | gross | net


class StratId(BaseModel):
    id: str


@app.get("/api/strategy/config")
def strategy_config():
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.config_summary()


def make_json_serializable(val):
    import numpy as np
    if isinstance(val, dict):
        return {k: make_json_serializable(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [make_json_serializable(v) for v in val]
    elif isinstance(val, tuple):
        return tuple(make_json_serializable(v) for v in val)
    elif isinstance(val, np.bool_):
        return bool(val)
    elif isinstance(val, np.integer):
        return int(val)
    elif isinstance(val, np.floating):
        return float(val)
    elif isinstance(val, np.ndarray):
        return make_json_serializable(val.tolist())
    return val


@app.get("/api/strategy/market-health")
def strategy_market_health(as_of: Optional[str] = None):
    """Daily 0-100 market-health / trend gauge (index + constituent daily bars)."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return make_json_serializable(_strat_api.market_health(as_of))


@app.get("/api/strategy/market-state")
def strategy_market_state(now: Optional[str] = None):
    """Current market state grouped by class: regime / directional / execution, plus a
    transparent (flagged, unvalidated) regime-type read. Powers the 3-panel dashboard."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return make_json_serializable(_strat_api.market_state(now))


@app.get("/api/strategy/expiries")
def strategy_expiries():
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return make_json_serializable(_strat_api.list_expiries())


@app.get("/api/strategy/suggest")
def strategy_suggest(expiry: Optional[str] = None, now: Optional[str] = None,
                     min_edge_cost_mult: float = 0.0):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return make_json_serializable(_strat_api.suggest(expiry, now, min_edge_cost_mult=min_edge_cost_mult))


@app.post("/api/strategy/suggest/add")
def strategy_suggest_add(expiry: Optional[str] = None, now: Optional[str] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.add_suggested(expiry, now)


class HarvestCompareReq(BaseModel):
    expiry: Optional[str] = None
    window_days: Optional[float] = None
    exit_mode: str = "manage"
    freq_minutes: Optional[float] = None
    stop_loss: bool = False
    stop_loss_rupees: Optional[float] = None
    lam: float = 0.5
    risk_drift: float = 0.0
    max_harvests: Optional[int] = 2
    max_harvest_debt: Optional[float] = 100.0
    mps_benchmark: str = "net"


@app.post("/api/strategy/compare-harvest")
def strategy_compare_harvest(req: HarvestCompareReq):
    """A/B/C/D harvest experiment — run the same window four ways and compare."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.compare_harvest(
        req.expiry, req.window_days, req.exit_mode, req.freq_minutes,
        req.stop_loss, req.stop_loss_rupees, req.lam, req.risk_drift,
        req.max_harvests, req.max_harvest_debt, req.mps_benchmark)


class SimCompareReq(BaseModel):
    expiry: Optional[str] = None
    entry_ts: str
    family: Optional[str] = None
    legs: Optional[List[Any]] = None
    exit_mode: str = "manage"
    stop_loss: bool = False
    stop_loss_rupees: Optional[float] = None
    lam: float = 0.5
    risk_drift: float = 0.0
    max_harvests: Optional[int] = 2
    max_harvest_debt: Optional[float] = 100.0
    max_rolls: Optional[int] = None
    cooldown_min: Optional[float] = None
    persist_near: Optional[int] = None
    min_harvest_inr: float = 100.0


@app.post("/api/strategy/simulate-compare")
def strategy_simulate_compare(req: SimCompareReq):
    """Run the same entry four ways (A/B/C/D) and compare per-trade stats."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.simulate_compare(
        req.expiry, req.entry_ts, req.family, req.legs, req.exit_mode,
        req.stop_loss, req.stop_loss_rupees, req.lam, req.risk_drift,
        req.max_harvests, req.max_harvest_debt,
        req.max_rolls, req.cooldown_min, req.persist_near, req.min_harvest_inr)


@app.get("/api/strategy/drawdown-insurance")
def strategy_drawdown_insurance(date: Optional[str] = None,
                                expiry: Optional[str] = None,
                                now: Optional[str] = None):
    """Liquidity-derisk overlay + recommended tail hedge (max-drawdown insurance).
    Optional `date` replays a past session (e.g. 2026-07-08)."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.drawdown_insurance(date, expiry, now)


class StratCandidate(BaseModel):
    family: str
    expiry: Optional[str] = None
    now: Optional[str] = None
    exchange: Optional[str] = "NFO"


@app.post("/api/strategy/candidate/add")
def strategy_candidate_add(req: StratCandidate):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.add_candidate(req.family, req.expiry, req.now,
                                    req.exchange or "NFO")


@app.get("/api/strategy/portfolio")
def strategy_portfolio(expiry: Optional[str] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.get_portfolio(expiry)


@app.post("/api/strategy/portfolio/add")
def strategy_portfolio_add(req: StratAddPosition):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    fields = {k: v for k, v in req.dict().items() if k != "kind" and v is not None}
    return _strat_api.add_position(req.kind, **fields)


class FuturesActionReq(BaseModel):
    entry_ts: Optional[str] = None
    expiry: Optional[str] = None
    position_lots: int = 1
    lam: float = 0.5
    horizon_frac: float = 1.0
    max_lots: int = 2
    allow_reverse: bool = True
    risk_drift_frac: float = 1.0


@app.post("/api/strategy/futures-action")
def strategy_futures_action(req: FuturesActionReq):
    """Point-in-time futures optimizer: score HOLD/EXIT/ADD/REDUCE/REVERSE."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.futures_action_score(
        req.entry_ts, req.expiry, req.position_lots, req.lam, req.horizon_frac,
        req.max_lots, req.allow_reverse, req.risk_drift_frac)


@app.get("/api/strategy/instruments")
def strategy_instruments():
    """Exchanges, lot size, and expiries present in the DB — for the Desk Book dropdowns."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.instruments_meta()


@app.post("/api/strategy/book/backtest")
def strategy_book_backtest(req: StratBookBacktestReq):
    """Backtest ONE position picked from the Desk Book (routes by kind)."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.backtest_book_position(
        req.pos_id, req.entry_ts, req.expiry, req.exit_mode, req.stop_loss,
        req.stop_loss_rupees, req.take_profit, req.take_profit_frac)


@app.post("/api/strategy/portfolio/remove")
def strategy_portfolio_remove(req: StratId):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.remove_position(req.id)


@app.post("/api/strategy/portfolio/clear")
def strategy_portfolio_clear():
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.clear_portfolio()


@app.get("/api/strategy/signal-backtest")
def strategy_signal_backtest(signal: str, horizon_hours: float = 3.0,
                             expiry: Optional[str] = None,
                             window_days: Optional[float] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.signal_backtest(signal, horizon_hours, expiry, window_days)


@app.post("/api/strategy/features/backfill")
def strategy_features_backfill(expiry: Optional[str] = None, force: bool = False):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.features_backfill(expiry, force)


@app.get("/api/strategy/features")
def strategy_features(expiry: Optional[str] = None, limit: int = 500):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.features_view(expiry, limit)


@app.post("/api/strategy/features/backfill/start")
def strategy_features_backfill_start(expiry: Optional[str] = None, force: bool = False,
                                     lookback_min: Optional[int] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.features_backfill_start(expiry, force, lookback_min)


@app.post("/api/strategy/config/momentum-window")
def strategy_set_momentum_window(lookback_min: int):
    """Set the shared RETURN WINDOW for every price-return signal (persisted)."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.set_momentum_window(lookback_min)


@app.get("/api/strategy/signal-phase-grid")
def strategy_signal_phase_grid(expiry: Optional[str] = None, date: Optional[str] = None,
                               oi_symbol: str = "NIFTY"):
    """Signal × session-phase grid for one day (how each signal evolved intraday).
    `oi_symbol` chooses the positioning symbol (NIFTY, or a stock like RELIANCE)."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.signal_phase_grid(expiry, date, oi_symbol)


@app.get("/api/strategy/signal-regime-horizon")
def strategy_signal_regime_horizon(date_from: Optional[str] = None, date_to: Optional[str] = None,
                                   min_n: int = 20, regime_by: str = "tape_vol",
                                   horizons: str = "15,30,60", min_move_pts: float = 0.0):
    """Conditional-alpha matrix: signal IC by regime × forward horizon.
    `regime_by`: 'tape_vol'|'oi'|'none'. `horizons`: comma minutes (off minute bars).
    `min_move_pts`: economic dead band — drop forward moves smaller than this."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.signal_regime_horizon(date_from, date_to, min_n, regime_by,
                                            horizons, min_move_pts)


@app.get("/api/strategy/signal-timeseries")
def strategy_signal_timeseries(expiry: Optional[str] = None, window_days: Optional[float] = None,
                               date_from: Optional[str] = None, date_to: Optional[str] = None):
    """NIFTY level + every signal's score across captures (for a price+signal chart).
    `date_from`/`date_to` (YYYY-MM-DD) bound the replay so it doesn't load all dates."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.signal_timeseries(expiry, window_days, date_from, date_to)


@app.get("/api/strategy/features/window-audit")
def strategy_feature_window_audit(expiry: Optional[str] = None):
    """Return window the stored features were computed at, vs the active config."""
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.feature_window_audit(expiry)


@app.get("/api/strategy/features/backfill/status")
def strategy_features_backfill_status():
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.features_backfill_status()


@app.post("/api/strategy/features/clear")
def strategy_features_clear(expiry: Optional[str] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.features_clear(expiry)


@app.get("/api/strategy/feature-names")
def strategy_feature_names(expiry: Optional[str] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.feature_names_list(expiry)


@app.get("/api/strategy/attribution")
def strategy_attribution(predictor: str, target: str = "fwd_ret_60m_pct",
                         condition: Optional[str] = None, expiry: Optional[str] = None,
                         window_days: Optional[float] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.attribution(predictor, target, condition, expiry, window_days)


@app.get("/api/strategy/signal-correlation")
def strategy_signal_correlation(expiry: Optional[str] = None,
                                window_days: Optional[float] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.signal_correlation(expiry, window_days)


@app.get("/api/strategy/signal-horizon-curve")
def strategy_signal_horizon_curve(signal: str, expiry: Optional[str] = None,
                                  window_days: Optional[float] = None,
                                  sample_minutes: Optional[float] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.signal_horizon_curve(signal, expiry, window_days, sample_minutes)


@app.get("/api/strategy/signal-backtest-all")
def strategy_signal_backtest_all(horizon_hours: float = 3.0,
                                 expiry: Optional[str] = None,
                                 window_days: Optional[float] = None,
                                 sample_minutes: Optional[float] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.signal_backtest_all(horizon_hours, expiry, window_days, sample_minutes=sample_minutes)


@app.post("/api/strategy/signal-test/start")
def strategy_signal_test_start(kind: str = "all", signal: Optional[str] = None,
                               horizon_hours: float = 3.0, expiry: Optional[str] = None,
                               window_days: Optional[float] = None,
                               sample_minutes: Optional[float] = None,
                               vix_regime: Optional[str] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    params: dict = {"expiry": expiry, "window_days": window_days}
    if kind == "single":
        params["horizon_hours"] = horizon_hours
        params["signal"] = signal or "heavyweight_leadership"
    elif kind == "effectiveness":
        params["sample_minutes"] = sample_minutes          # no fixed horizon — it sweeps all
        if vix_regime:
            params["vix_regime"] = vix_regime
    else:
        params["horizon_hours"] = horizon_hours
        params["sample_minutes"] = sample_minutes
    return _strat_api.signal_test_start(kind, **params)


@app.get("/api/strategy/signal-test/status")
def strategy_signal_test_status():
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.signal_test_status()


@app.get("/api/strategy/chain")
def strategy_chain(expiry: Optional[str] = None, at: Optional[str] = None,
                   family: Optional[str] = None):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.chain_at(expiry, at, family)


class StratSimulate(BaseModel):
    expiry: Optional[str] = None
    entry_ts: str                          # UTC ISO, e.g. 2026-07-02T04:15:00Z
    family: Optional[str] = None           # None = use the framework suggestion
    legs: Optional[List[Any]] = None       # explicit [[side,strike,sign], ...]
    exit_mode: str = "manage"
    roll_directional: bool = False
    stop_loss: bool = False
    stop_loss_rupees: Optional[float] = None
    cooldown_min: Optional[float] = None
    max_rolls: Optional[int] = None
    persist_near: Optional[int] = None
    harvest: bool = False
    min_harvest_inr: float = 100.0
    take_profit: bool = False
    take_profit_frac: float = 0.6
    max_manage: Optional[int] = None
    proactive: bool = False                 # advisory forecast-driven action evaluator
    proactive_lambda: float = 0.5           # tail-aversion weight: score = E − λ·|CVaR10|
    proactive_horizon_frac: float = 1.0     # touch-window as a fraction of time-to-expiry
    proactive_min_edge: float = 5.0         # pts an action must beat HOLD by to be recommended
    proactive_risk_drift: float = 1.0       # 0=symmetric (conservative) tail, 1=trust the trend
    proactive_max_harvests: Optional[int] = None        # harvest budget: max harvests/day
    proactive_max_harvest_debt: Optional[float] = None  # harvest budget: max points sold away
    proactive_min_wing_buffer: Optional[float] = None   # harvest budget: keep ≥ this wing distance
    proactive_min_width: float = 200.0                  # vertical spreads: min long↔short gap (pts)
    harvest_gate: str = "off"                           # off | optimizer | budget | both (Strategy A/C/D)


@app.post("/api/strategy/simulate")
def strategy_simulate(req: StratSimulate):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.simulate(req.expiry, req.entry_ts, req.family, req.legs,
                               req.exit_mode, req.roll_directional, req.stop_loss,
                               req.stop_loss_rupees, cooldown_min=req.cooldown_min,
                               max_rolls=req.max_rolls, persist_near=req.persist_near,
                               harvest=req.harvest, min_harvest_inr=req.min_harvest_inr,
                               take_profit=req.take_profit, take_profit_frac=req.take_profit_frac,
                               max_manage=req.max_manage,
                               proactive=req.proactive, proactive_lambda=req.proactive_lambda,
                               proactive_horizon_frac=req.proactive_horizon_frac,
                               proactive_min_edge=req.proactive_min_edge,
                               proactive_risk_drift=req.proactive_risk_drift,
                               proactive_max_harvests=req.proactive_max_harvests,
                               proactive_max_harvest_debt=req.proactive_max_harvest_debt,
                               proactive_min_wing_buffer=req.proactive_min_wing_buffer,
                               proactive_min_width=req.proactive_min_width,
                               harvest_gate=req.harvest_gate)


@app.post("/api/strategy/backtest")
def strategy_backtest(req: StratBacktestReq):
    if not _STRAT_OK:
        raise HTTPException(503, "strategy framework unavailable")
    return _strat_api.backtest(req.mode, req.expiry, req.exit_mode, req.hold,
                               freq_minutes=req.freq_minutes,
                               roll_directional=req.roll_directional,
                               window_days=req.window_days,
                               stop_loss=req.stop_loss,
                               stop_loss_rupees=req.stop_loss_rupees,
                               cooldown_min=req.cooldown_min,
                               max_rolls=req.max_rolls,
                               persist_near=req.persist_near,
                               harvest=req.harvest,
                               min_harvest_inr=req.min_harvest_inr,
                               take_profit=req.take_profit,
                               take_profit_frac=req.take_profit_frac,
                               max_manage=req.max_manage,
                               min_edge_cost_mult=req.min_edge_cost_mult,
                               mps_benchmark=req.mps_benchmark)


@app.get("/api/participant-history")
def get_participant_history(limit: int = 30):
    import sqlite3
    from chain_store import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # We need to get all participants for the most recent `limit` distinct dates
        c.execute("""
            SELECT DISTINCT flow_date 
            FROM participant_flows 
            ORDER BY flow_date DESC 
            LIMIT ?
        """, (limit,))
        dates_res = c.fetchall()
        if not dates_res:
            return {"success": True, "data": []}
            
        oldest_date = dates_res[-1][0]
        
        c.execute("""
            SELECT flow_date, participant_type,
                   idx_fut_long, idx_fut_short, stk_fut_long, stk_fut_short,
                   idx_opt_call_long, idx_opt_call_short, idx_opt_put_long, idx_opt_put_short,
                   stk_opt_call_long, stk_opt_call_short, stk_opt_put_long, stk_opt_put_short
            FROM participant_flows
            WHERE flow_date >= ?
            ORDER BY flow_date ASC
        """, (oldest_date,))
        rows = c.fetchall()
        
        # Group by date
        data_by_date = {}
        for r in rows:
            date_str = r[0][:10]
            if date_str not in data_by_date:
                data_by_date[date_str] = {"date": date_str, "participants": {}}
                
            ptype = r[1]
            data_by_date[date_str]["participants"][ptype] = {
                "idx_fut_net": r[2] - r[3],
                "stk_fut_net": r[4] - r[5],
                "idx_opt_call_net": r[6] - r[7],
                "idx_opt_put_net": r[8] - r[9],
                "stk_opt_call_net": r[10] - r[11],
                "stk_opt_put_net": r[12] - r[13],
            }
            
        data = list(data_by_date.values())
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if 'conn' in locals():
            conn.close()


# ── DATA AGENT ROUTER ────────────────────────────────────────────────────
from backend.routes.data_agent_routes import router as data_agent_router
app.include_router(data_agent_router)

