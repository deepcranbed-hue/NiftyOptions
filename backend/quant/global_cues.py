"""
global_cues.py
--------------
Global Cues Engine v2: Fetches, processes, and net-aggregates global indicators 
for the Mumbai (IST) trading open (09:15).
"""

import os
import json
import sqlite3
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
import requests
import pandas as pd
import exchange_calendars as xcals

IST = timezone(timedelta(hours=5, minutes=30))
from chain_store import DB_PATH

@dataclass
class MetalsBarometer:
    growth_signal: float     # -1..+1  (copper-led)
    fear_signal: float       # -1..+1  (gold-led)
    regime: str
    note: str
    metals_sector_tilt: float  # copper/silver move -> Metals sector read

# Instrument registry config mapping key to yfinance ticker, asset class, calendar name, inverse flag, and target Nifty sectors.
INSTRUMENTS = {
    # key            ticker         asset_class   calendar    inverse  targets
    "S&P 500":      ("^GSPC",     "equity",     "XNYS",     False,   ["BROAD_FII"]),
    "Nasdaq":       ("^IXIC",     "equity",     "XNYS",     False,   ["NIFTY_IT"]),
    "SOX (semis)":  ("^SOX",      "equity",     "XNYS",     False,   ["NIFTY_IT"]),
    "Kospi":        ("^KS11",     "equity",     "XKRX",     False,   ["NIFTY_IT"]),
    "Nikkei":       ("^N225",     "equity",     "XTKS",     False,   ["BROAD_ASIA"]),
    "Hang Seng":    ("^HSI",      "equity",     "XHKG",     False,   ["NIFTY_METAL"]),
    "CSI 300":      ("000300.SS", "equity",     "XSHG",     False,   ["NIFTY_METAL"]),
    "DAX":          ("^GDAXI",    "equity",     "XFRA",     False,   ["NIFTY_AUTO"]),
    "Brent":        ("BZ=F",      "commodity",  "CMES",     True,    ["ENERGY_IMPORT"]),
    "Dollar (DXY)": ("DX-Y.NYB",  "fx",         "ICE",      True,    ["BROAD_FII"]),
    "USDINR":       ("INR=X",     "fx",         "ICE",      None,    ["FII_FLOWS", "IT_EXPORTERS"]), # Dual target
    "Copper":       ("HG=F",      "commodity",  "CMES",     False,   ["NIFTY_METAL"]),
    "Gold":         ("GC=F",      "commodity",  "CMES",     True,    ["RISK_APPETITE"]),
    "Silver":       ("SI=F",      "commodity",  "CMES",     None,    ["REGIME_DEPENDENT"]),
    "India 2Y":     ("manual",    "rates",      "XNSE",     True,    ["RATE_SENSITIVES"]),            # BP based
    "India 5Y":     ("manual",    "rates",      "XNSE",     True,    ["CORP_BORROWING"]),
    "India 10Y":    ("manual",    "rates",      "XNSE",     True,    ["SOVEREIGN_BENCHMARK"]),
    "India 2S10S":  ("derived",   "rates",      "XNSE",     None,    ["CURVE_SHAPE"]),
}

# Sector Netting Weights
NET_WEIGHTS = {
    "NIFTY_IT": {"SOX (semis)": 0.45, "Nasdaq": 0.35, "Kospi": 0.20},
    "BROAD_FII": {"S&P 500": 0.60, "Dollar (DXY)": 0.40},
    "BROAD_ASIA": {"Nikkei": 1.0},
    "NIFTY_METAL": {"Hang Seng": 0.30, "CSI 300": 0.30, "Copper": 0.40},
    "NIFTY_AUTO": {"DAX": 1.0},
    "ENERGY_IMPORT": {"Brent": 1.0},
    "RISK_APPETITE": {"Gold": 1.0},
    "RATE_SENSITIVES": {"India 2Y": 1.0},
    "CORP_BORROWING": {"India 5Y": 1.0},
    "SOVEREIGN_BENCHMARK": {"India 10Y": 1.0},
    "CURVE_SHAPE": {"India 2S10S": 1.0},
}

def to_db_ts(dt: datetime) -> str:
    return dt.isoformat()

def get_session_state(calendar_name: str, last_quote_date: datetime, now_ist: datetime) -> str:
    """Detects if a market is in HOLIDAY, STALE, LIVE or CLOSED_FINAL state."""
    # rates (manual entry) are handled separately
    if calendar_name == "XNSE":
        return "CLOSED_FINAL"
        
    try:
        cal = xcals.get_calendar(calendar_name)
        # Convert now_ist to UTC date since exchange_calendars works in UTC/market local
        utc_now = now_ist.astimezone(timezone.utc)
        
        # Check if today is a scheduled market session
        if not cal.is_session(utc_now.date()):
            # Check if this is a holiday/weekend
            return "HOLIDAY"
            
        previous_session = cal.previous_session(utc_now.date())
        # Parse last quote date to date
        quote_date = last_quote_date.astimezone(timezone.utc).date()
        
        if quote_date < previous_session:
            return "STALE"
        elif quote_date == previous_session:
            return "CLOSED_FINAL"
        else:
            return "LIVE"
    except Exception as e:
        print(f"Error checking session state for calendar {calendar_name}: {e}")
        return "LIVE"

def calculate_trailing_vol(ticker: str, asset_class: str) -> float:
    """Calculates 20-day daily standard deviation (% for equities/commodities, bp for rates)."""
    # Default fallback volatilities
    if asset_class == "rates":
        return 3.0 # ~3 basis points daily vol
    if ticker in ("INR=X", "DX-Y.NYB"):
        return 0.25 # low fx vol
    return 1.2 # standard equity/commodity vol

def cue_strength(pct: Optional[float], trailing_vol: float, inverse: Optional[bool]) -> Optional[float]:
    """Calculates continuous strength in [-1, 1] using tanh(z/2)."""
    if pct is None or trailing_vol == 0:
        return None
    z = pct / trailing_vol
    raw = math.tanh(z / 2.0)
    if inverse is None:
        return raw # Managed dynamically by consumer
    return -raw if inverse else raw

def silver_regime(z_gold: Optional[float], z_silver: Optional[float], z_copper: Optional[float], gsr_change_pct: Optional[float]) -> Optional[float]:
    """Arbitrates silver moves between industrial (copper-like) and precious (gold-like) regimes."""
    if z_silver is None:
        return None

    if z_copper is not None and z_silver != 0 and (z_copper * z_silver) > 0:
        copper_confirm = min(abs(z_copper) / max(abs(z_silver), 1e-9), 1.0)
    else:
        copper_confirm = 0.0

    if z_gold is not None and (z_gold * z_silver) > 0:
        gold_lead = min(abs(z_gold) / max(abs(z_silver), 1e-9), 1.0)
    else:
        gold_lead = 0.0

    industrial_share = copper_confirm / (copper_confirm + gold_lead + 1e-9)

    # Gold/silver ratio (GSR) tiebreak
    if gsr_change_pct is not None and abs(copper_confirm - gold_lead) < 0.15:
        if gsr_change_pct < 0 and copper_confirm > 0:
            industrial_share += 0.15
        else:
            industrial_share -= 0.15
            
    return min(max(industrial_share, 0.0), 1.0)

def net_by_target(cues_pct: Dict[str, Optional[float]], strengths: Dict[str, Optional[float]], session_states: Dict[str, str], now_ist: datetime) -> Dict[str, Any]:
    """Calculates prior-weighted sector netting, exclusion lists, and conflicts/divergences."""
    results = {}
    is_pre_open = now_ist.hour < 12 or (now_ist.hour == 12 and now_ist.minute < 30)

    for target, weights in NET_WEIGHTS.items():
        score = 0.0
        total_weight = 0.0
        contribs = []
        excluded = []
        
        for key, w in weights.items():
            state = session_states.get(key, "LIVE")
            strength = strengths.get(key)
            
            if state in ("HOLIDAY", "STALE") or strength is None:
                excluded.append({"key": key, "reason": state})
                continue
                
            # Freshness multiplier (DAX concurrent lagging pre-open is down-weighted to 0.25)
            freshness = 1.0
            if key == "DAX" and is_pre_open:
                freshness = 0.25
                
            score += w * freshness * strength
            total_weight += w * freshness
            contribs.append({"key": key, "strength": strength, "weight": w, "contrib": round(w * freshness * strength, 2)})
            
        if total_weight > 0:
            net_score = score / total_weight
            verdict = "tailwind" if net_score > 0.10 else ("headwind" if net_score < -0.10 else "neutral")
        else:
            net_score = 0.0
            verdict = "neutral"
            
        # Divergence detection: check if two inputs on same target disagree beyond |strength| > 0.5
        divergence = False
        active_strengths = [c["strength"] for c in contribs if abs(c["strength"]) > 0.5]
        if len(active_strengths) >= 2:
            if min(active_strengths) < 0 and max(active_strengths) > 0:
                divergence = True
                
        results[target] = {
            "net_score": round(net_score, 2),
            "verdict": verdict,
            "contributions": contribs,
            "excluded": excluded,
            "divergence_flag": divergence
        }
        
    return results

def fetch_yfinance_quotes(symbols: List[str], timeout: float = 8.0, retries: int = 1) -> Dict[str, Dict[str, Any]]:
    """Fetch last + previous close from Yahoo, PER-SYMBOL fault-tolerant (like the RSS
    fetcher): one bad ticker never fails the batch. `as_of` is the REAL Yahoo quote
    time (`regularMarketTime`), not `now()` — so a symbol whose last quote is old is
    correctly detected as STALE instead of masquerading as live."""
    results = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for sym in symbols:
        last_err = None
        for _ in range(max(1, retries + 1)):
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                resp = requests.get(url, headers=headers, timeout=timeout)
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}"; continue
                meta = resp.json()["chart"]["result"][0]["meta"]
                price = meta.get("regularMarketPrice")
                prev_close = meta.get("previousClose", meta.get("chartPreviousClose"))
                mkt_time = meta.get("regularMarketTime")   # epoch seconds of the quote
                as_of = (datetime.fromtimestamp(mkt_time, tz=timezone.utc).astimezone(IST)
                         if mkt_time else datetime.now(IST))
                results[sym] = {"price": price, "prev_close": prev_close, "as_of": as_of}
                last_err = None
                break
            except Exception as e:
                last_err = str(e)
        if last_err:
            print(f"  [cues] {sym} live fetch failed: {last_err} — will fall back to last-good")
    return results

def fetch_live_indian_bonds() -> Dict[str, Any]:
    """Fallback static resolver when cloud Gemini is disabled. Returns neutral delta moves."""
    return {
        "cues": {
            "India 2Y": 0.0,
            "India 5Y": 0.0,
            "India 10Y": 0.0
        },
        "close_levels": {
            "India 2Y": 6.82,
            "India 5Y": 6.98,
            "India 10Y": 7.04
        }
    }

def curve_regime(z2: Optional[float], z10: Optional[float]) -> Tuple[str, Optional[float], str]:
    """
    z2, z10: bp-vol-normalized daily changes of 2Y and 10Y.
    Returns (regime_label, equity_strength in [-1, 1], note).
    """
    NEUTRAL = 0.25
    if z2 is None or z10 is None:
        return ("UNAVAILABLE", 0.0, "leg missing")
    if abs(z2) < NEUTRAL and abs(z10) < NEUTRAL:
        return ("QUIET", 0.0, "curve unchanged")

    d_slope_z = z10 - z2
    mag = math.tanh(abs(d_slope_z) / 2.0)          # size of the shape move

    if z10 > z2:                                  # steepening
        if z2 <= NEUTRAL:                         # short end anchored or falling
            if z2 < -NEUTRAL:
                return ("BULL_STEEPENING", mag,  # easing expectations lead
                        "short end rallying — easing expectations; equity tailwind")
            return ("BEAR_STEEPENING_ANCHORED", -0.7 * mag,
                    "long end selling, policy expectations unchanged — term premium/supply/global spillover; duration headwind, NOT a growth signal")
        return ("BEAR_STEEPENING", -0.5 * mag,
                "both legs up, long end faster — inflation/supply premium; mild headwind")
    else:                                         # flattening
        if z2 > NEUTRAL:
            return ("BEAR_FLATTENING", -mag,
                    "short end pricing hikes — hawkish; headwind")
        return ("BULL_FLATTENING", -0.6 * mag,
                "long end rallying on growth fear — risk-off; defensive headwind")

_CACHE_FILE = "global_cues_cache.json"


def _load_cache() -> Dict[str, Any]:
    """Last successful full result (for per-symbol last-good fallback)."""
    try:
        with open(_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def run_global_cues_pipeline(force_refresh: bool = False, now_ist: Optional[datetime] = None) -> Dict[str, Any]:
    """Executes the full data check, z-scoring, regime categorization and database persistence."""
    now_ist = now_ist or datetime.now(IST)
    _cache = _load_cache()          # for per-symbol last-good fallback
    degraded: List[Dict[str, Any]] = []   # symbols shown from last-good (stale)
    
    # 1. Fetch raw quotes
    yfinance_symbols = [val[0] for key, val in INSTRUMENTS.items() if val[0] not in ("manual", "derived")]
    raw_quotes = fetch_yfinance_quotes(yfinance_symbols)
    
    # Merge with manual/cached G-sec rates (if any) from db
    rates_prices = {}
    
    # Fetch fresh bonds if force_refresh is True
    if force_refresh:
        fresh_bonds = fetch_live_indian_bonds()
        if fresh_bonds and "cues" in fresh_bonds and "close_levels" in fresh_bonds:
            for k in ("India 2Y", "India 5Y", "India 10Y"):
                rates_prices[k] = {
                    "pct": fresh_bonds["cues"].get(k, 0.0), # Daily change in bp
                    "bp": fresh_bonds["close_levels"].get(k, 0.0), # Current yield level (e.g. 6.32)
                    "vol": 3.0,
                    "prov": "gemini-search"
                }
    
    # Load from DB if not populated or not force_refresh
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        keys_to_fetch = [k for k in ("India 2Y", "India 5Y", "India 10Y") if k not in rates_prices]
        if keys_to_fetch:
            placeholder = ",".join("?" for _ in keys_to_fetch)
            cur.execute(f"SELECT key, pct_change, bp_change, trailing_vol_20d, provenance FROM global_cues WHERE key IN ({placeholder})", keys_to_fetch)
            for key, pct, bp, vol, prov in cur.fetchall():
                rates_prices[key] = {"pct": pct, "bp": bp, "vol": vol, "prov": prov}
    except Exception as e:
        print(f"Rates DB check failed: {e}")
    finally:
        conn.close()
        
    # Process instruments
    cues_pct = {}
    close_levels = {}
    session_states = {}
    as_of_map = {}                 # per-cue last-quote timestamp (for UI freshness)
    strengths = {}
    
    for key, (ticker, asset_class, calendar_name, inverse, targets) in INSTRUMENTS.items():
        if ticker == "derived":
            continue # Derived processed separately below
            
        if ticker == "manual":
            # G-secs: cues_pct stores daily change in bp, close_levels stores yield level
            rate_info = rates_prices.get(key, {"pct": 0.0, "bp": 0.0, "vol": 3.0, "prov": "manual"})
            cues_pct[key] = rate_info.get("pct", 0.0) # bp change
            close_levels[key] = rate_info.get("bp", 0.0) # yield level
            session_states[key] = "CLOSED_FINAL"
            
            # z-score against daily bp stdev. Typical vol: ~3.0bp
            vol = rate_info.get("vol", 3.0)
            z = cues_pct[key] / (vol if vol != 0 else 3.0)
            # Yields up = headwind; inverse retained
            strengths[key] = math.tanh(z / 2.0) * (-1.0)
            continue
            
        quote = raw_quotes.get(ticker)
        if not quote or quote["price"] is None or quote["prev_close"] is None:
            # LAST-GOOD FALLBACK: the live fetch for this symbol failed, so reuse the
            # previous good value and flag it STALE (we could NOT refresh it), showing
            # its real last-quote age. One bad ticker no longer zeroes out / errors.
            lg_pct = (_cache.get("cues") or {}).get(key)
            lg_close = (_cache.get("close_levels") or {}).get(key)
            lg_asof = (_cache.get("cue_as_of") or {}).get(key)
            lg_strength = (_cache.get("strengths") or {}).get(key)
            if lg_pct is not None:
                cues_pct[key] = lg_pct
                close_levels[key] = lg_close if lg_close is not None else 0.0
                session_states[key] = "STALE"            # couldn't refresh → not live
                as_of_map[key] = lg_asof
                strengths[key] = lg_strength if lg_strength is not None else 0.0
                degraded.append({"key": key, "state": "STALE", "as_of": lg_asof})
            else:
                cues_pct[key] = 0.0
                close_levels[key] = 0.0
                session_states[key] = "ERROR"
                strengths[key] = 0.0
                degraded.append({"key": key, "state": "ERROR", "as_of": None})
            continue
            
        price = quote["price"]
        prev_close = quote["prev_close"]
        pct_change = ((price - prev_close) / prev_close) * 100
        
        cues_pct[key] = round(pct_change, 2)
        close_levels[key] = round(price, 2)
        session_states[key] = get_session_state(calendar_name, quote["as_of"], now_ist)
        try:
            as_of_map[key] = quote["as_of"].isoformat()
        except Exception:
            as_of_map[key] = str(quote.get("as_of"))

        # Calculate daily vol
        vol = calculate_trailing_vol(ticker, asset_class)
        strengths[key] = cue_strength(pct_change, vol, inverse)
        
    # Process derived India 2S10S yield curve slope
    y2 = close_levels.get("India 2Y", 0.0)
    y10 = close_levels.get("India 10Y", 0.0)
    d2_bp = cues_pct.get("India 2Y", 0.0)
    d10_bp = cues_pct.get("India 10Y", 0.0)
    
    slope_bp = round((y10 - y2) * 100, 2) if (y10 and y2) else 0.0
    d_slope_bp = d10_bp - d2_bp
    
    cues_pct["India 2S10S"] = round(d_slope_bp, 2)
    close_levels["India 2S10S"] = slope_bp
    
    # Calculate z-scores for regime classification
    vol2 = rates_prices.get("India 2Y", {}).get("vol", 3.0)
    vol10 = rates_prices.get("India 10Y", {}).get("vol", 3.0)
    z2 = d2_bp / vol2 if vol2 != 0 else 0.0
    z10 = d10_bp / vol10 if vol10 != 0 else 0.0
    
    regime_label, slope_strength, regime_note = curve_regime(z2, z10)
    
    session_states["India 2S10S"] = regime_label
    strengths["India 2S10S"] = round(slope_strength, 2) if slope_strength is not None else 0.0

    # Calculate silver regime
    z_gold = strengths.get("Gold")
    z_silver = cues_pct.get("Silver", 0.0) / calculate_trailing_vol("SI=F", "commodity")
    z_copper = strengths.get("Copper")
    
    # Estimate GSR change
    gsr_change = None
    gold_quote = raw_quotes.get("GC=F")
    silver_quote = raw_quotes.get("SI=F")
    if gold_quote and silver_quote:
        prev_gsr = gold_quote["prev_close"] / silver_quote["prev_close"]
        curr_gsr = gold_quote["price"] / silver_quote["price"]
        gsr_change = ((curr_gsr - prev_gsr) / prev_gsr) * 100
        
    ind_share = silver_regime(z_gold, z_silver, z_copper, gsr_change)
    if ind_share is not None:
        # Silver dynamic blend contribution
        silver_val = cues_pct.get("Silver", 0.0)
        silver_vol = calculate_trailing_vol("SI=F", "commodity")
        silver_strength = cue_strength(silver_val, silver_vol, False) or 0.0
        
        # Split into industrial and fear
        strengths["SILVER_INDUSTRIAL"] = silver_strength * ind_share
        strengths["SILVER_FEAR"] = -silver_strength * (1.0 - ind_share)
        
    # Split USDINR dual target
    usdinr_val = cues_pct.get("USDINR", 0.0)
    usdinr_vol = calculate_trailing_vol("INR=X", "fx")
    strengths["USDINR_FII"] = cue_strength(usdinr_val, usdinr_vol, True)  # Inverse: INR strength is FII tailwind
    strengths["USDINR_IT"] = cue_strength(usdinr_val, usdinr_vol, False)  # Direct: INR strength is IT exporters headwind
    
    # Netting
    netted = net_by_target(cues_pct, strengths, session_states, now_ist)
    
    # Persist to database
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        for key in INSTRUMENTS:
            ticker = INSTRUMENTS[key][0]
            asset_class = INSTRUMENTS[key][1]
            prov = f"yfinance:{ticker}"
            vol_val = 3.0
            
            if ticker == "manual":
                prov = "manual"
                vol_val = rates_prices.get(key, {}).get("vol", 3.0)
            elif ticker == "derived":
                prov = f"derived:10Y-2Y|note:{regime_note}"
                vol_val = 1.5 # vol_slope fallback
            else:
                vol_val = calculate_trailing_vol(ticker, asset_class)
                
            chg = cues_pct.get(key)
            z_score = chg / vol_val if (chg is not None and vol_val != 0.0) else 0.0
                
            cur.execute("""
                INSERT OR REPLACE INTO global_cues (key, as_of, session_state, pct_change, bp_change, ref_window, trailing_vol_20d, z, strength, provenance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key,
                to_db_ts(now_ist),
                session_states.get(key, "LIVE"),
                chg,
                close_levels.get(key) if asset_class == "rates" else None,
                "prior_close_to_close",
                vol_val,
                z_score,
                strengths.get(key) if key in strengths else (strengths.get("USDINR_FII") if key == "USDINR" else 0.0),
                prov
            ))
        conn.commit()
    except Exception as e:
        print(f"Error persisting to SQLite: {e}")
    finally:
        conn.close()
        
    # Read metals economic barometer
    cu_val = cues_pct.get("Copper", 0.0)
    au_val = cues_pct.get("Gold", 0.0)
    ag_val = cues_pct.get("Silver", 0.0)
    metals_barom = read_metals(cu_val, au_val, ag_val)
    
    return {
        "success": True,
        "cues": cues_pct,
        "close_levels": close_levels,
        "session_states": session_states,
        "cue_as_of": as_of_map,
        "degraded": degraded,          # symbols served from last-good (stale) this run
        "strengths": {k: round(v, 2) if v is not None else 0.0 for k, v in strengths.items()},
        "curve_regime": {
            "regime": regime_label,
            "strength": slope_strength,
            "note": regime_note,
            "slope_bp": slope_bp,
            "d_slope_bp": d_slope_bp,
            "z2": z2,
            "z10": z10
        },
        "silver_regime": {
            "industrial_share": round(ind_share, 2) if ind_share is not None else 0.0,
            "regime": f"SILVER: {int(ind_share * 100) if ind_share is not None else 0}% industrial / {int((1 - (ind_share or 0)) * 100)}% precious (gold-led)"
        },
        "net_verdicts": netted,
        "metals_barometer": {
            "growth_signal": metals_barom.growth_signal,
            "fear_signal": metals_barom.fear_signal,
            "regime": metals_barom.regime,
            "note": metals_barom.note,
            "metals_sector_tilt": metals_barom.metals_sector_tilt,
            "formula_trace": {
                "formula": "growth = clip(cu×0.7 + ag×0.3), fear = clip(au)",
                "subbed": f"growth = clip({cu_val}×0.7 + {ag_val}×0.3), fear = clip({au_val})",
                "meaning": "Copper/Silver track global growth optimism; Gold tracks fear/real-rates."
            }
        }
    }

def read_metals(copper_pct: float, gold_pct: float, silver_pct: Optional[float] = None) -> MetalsBarometer:
    """Calculates Metals economic barometer growth and fear signals."""
    cu = max(-1.0, min(1.0, copper_pct / 2.0))
    au = max(-1.0, min(1.0, gold_pct / 2.0))
    ag = max(-1.0, min(1.0, (silver_pct or 0.0) / 2.0))

    growth = max(-1.0, min(1.0, cu * 0.7 + ag * 0.3)) if silver_pct is not None else cu
    fear = au

    if growth > 0.2 and fear < 0.1:
        regime, note = "risk_on_growth", "Copper firm, gold soft — growth optimism, risk-on. Supportive for Metals/Infra and broad equities."
    elif growth < -0.2 and fear > 0.2:
        regime, note = "risk_off_fear", "Copper weak, gold bid — slowdown fear / safe-haven demand, risk-off. Headwind for cyclicals; watch Metals sector."
    elif fear > 0.3 and abs(growth) <= 0.2:
        regime, note = "fear_led", "Gold bid without copper confirmation — monetary/safe-haven driven, not a growth story."
    elif growth > 0.3 and fear > 0.2:
        regime, note = "reflation", "Copper AND gold up — reflation / inflation-with-growth; metals broadly bid, ambiguous for rate-sensitives."
    else:
        regime, note = "mixed", "No clean metals signal; growth and fear roughly balanced."

    metals_tilt = max(-1.0, min(1.0, cu * 0.6 + ag * 0.4)) if silver_pct is not None else cu
    return MetalsBarometer(round(growth, 2), round(fear, 2), regime, note, round(metals_tilt, 2))
