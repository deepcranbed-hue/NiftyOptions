#!/usr/bin/env python3
"""
market_scan.py
--------------
Lightweight weekly/daily market scanner for Indian (Nifty) + global markets.

Pulls, with NO paid APIs:
  1. News headlines   -> RSS feeds (Economic Times, Moneycontrol, Business Standard, Reuters, CNBC)
  2. Nifty/stock prices -> yfinance (indices, key stocks, USDINR)
  3. Earnings calendar  -> NSE India public JSON endpoint (with graceful fallback)
  4. Macro / geopolitics -> Brent, WTI, Gold, US10Y via yfinance + keyword-tagged headlines

Output: a dated Markdown report + a CSV of headlines, saved to ./reports/

Design notes
------------
* Uses lightweight methods (RSS + yfinance) first because they are fast and rarely break.
* Playwright is OPTIONAL and only used for JS-heavy pages that block plain HTTP.
  It is disabled by default (USE_PLAYWRIGHT = False). See fetch_with_playwright().
* Everything is wrapped in try/except so one broken source never kills the whole run.

Install:
    pip install feedparser yfinance pandas requests
    # optional, only if you set USE_PLAYWRIGHT = True:
    pip install playwright && playwright install chromium
"""

from __future__ import annotations
import os
import re
import sys
import csv
import math
import datetime as dt
from pathlib import Path
import threading
import concurrent.futures as _futures     # parallel quote / earnings fetches

def call_with_timeout(func, args=(), kwargs={}, timeout=3.0):
    res = [None]
    err = [None]
    def worker():
        try:
            res[0] = func(*args, **kwargs)
        except Exception as e:
            err[0] = e
    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("execution timed out")
    if err[0]:
        raise err[0]
    return res[0]

# ---- third-party (see install note above) ----
import feedparser          # RSS parsing
import requests            # NSE endpoint + generic fetch
import pandas as pd

# Global requests monkey-patch to prevent yfinance / network connection hangs
try:
    _orig_session_request = requests.Session.request
    def _timeout_session_request(self, method, url, *args, **kwargs):
        if 'timeout' not in kwargs or kwargs['timeout'] is None:
            kwargs['timeout'] = 5.0  # 5-second allowance for all requests calls
        return _orig_session_request(self, method, url, *args, **kwargs)
    requests.Session.request = _timeout_session_request
except Exception:
    pass

try:
    import yfinance as yf
    HAVE_YF = True
except Exception:
    HAVE_YF = False

# Prompt-distillation few-shot exemplars (teacher-written). Optional import.
try:
    from desk_note_examples import fewshot_block, fewshot_style
    HAVE_FEWSHOT = True
except Exception:
    HAVE_FEWSHOT = False

# Full-article extraction (trafilatura + Playwright fallback). Optional.
try:
    from fetch_article import fetch_article, first_paragraph
    HAVE_FULLTEXT = True
except Exception:
    HAVE_FULLTEXT = False

# Event memory / historical analogues (built by build_events.py). Optional.
try:
    from build_events import load_event_stats, match_conditions, load_linkage_conf
    HAVE_EVENTS = True
except Exception:
    HAVE_EVENTS = False
    def load_linkage_conf():
        return {}

# Enrich top company stories with full-article figures (needs `pip install trafilatura`).
USE_FULLTEXT = True
FULLTEXT_MAX = 4   # only enrich the top N company stories per run (polite + fast)


# =========================================================================
# CONFIG  -- edit these to taste
# =========================================================================

USE_PLAYWRIGHT = False   # flip to True (and install playwright) for JS-heavy sites

REPORT_DIR = Path(__file__).resolve().parent / "reports"

# Tickers: yfinance symbols. name -> symbol
INDICES = {
    "Nifty 50":    "^NSEI",
    "Sensex":      "^BSESN",
    "Bank Nifty":  "^NSEBANK",
    "Nifty IT":    "^CNXIT",
    "India VIX":   "^INDIAVIX",
}

MACRO = {
    "Brent Crude":   "BZ=F",
    "WTI Crude":     "CL=F",
    "Gold":          "GC=F",
    "Silver":        "SI=F",        # safe-haven + industrial (solar/EV)
    "Copper":        "HG=F",        # 'Dr Copper' — global growth barometer
    "USD/INR":       "INR=X",
    "Dollar Index":  "DX-Y.NYB",   # DXY — key for FII flows / rupee
    "US 10Y Yield":  "^TNX",
    "Kospi":         "^KS11",       # Korea — global AI/chip bellwether (IT-fear proxy)
    "Phila Semi (SOX)": "^SOX",      # US semiconductor index — AI/chip cycle read
    "Nasdaq":        "^IXIC",
    "Dow Jones":     "^DJI",
}

# Sector proxies to SHOW the oil divergence live: producer up vs users down
SECTOR_PROXIES = {
    "ONGC (upstream/producer)":  "ONGC.NS",
    "BPCL (oil marketer)":       "BPCL.NS",
    "Asian Paints (oil user)":   "ASIANPAINT.NS",
    "IndiGo (aviation/fuel)":    "INDIGO.NS",
}

# Theme baskets — tracked so you can see the actual move behind a thematic story
THEME_STOCKS = {
    "Titan (jewellery)":       "TITAN.NS",
    "Kalyan Jew. (jewellery)": "KALYANKJIL.NS",
    "Muthoot (gold loan)":     "MUTHOOTFIN.NS",
    "Tata Motors (EV/auto)":   "TATAMOTORS.NS",
    "Ola Electric (EV)":       "OLAELEC.NS",
    "Kaynes (semiconductor)":  "KAYNES.NS",
    "CG Power (semi/electr.)": "CGPOWER.NS",
    "Dixon (EMS/China+1)":     "DIXON.NS",
}

# Thematic / structural stories — detected in headlines, with the reasoning attached.
# These explain the "why" behind group moves (jewellery round-trip, EV, chip war...).
THEMES = [
    {
        "name": "💍 Jewellery & Gold",
        "keywords": ["jewel", "titan", "kalyan", "senco", "gold price", "gold loan",
                     "muthoot", "manappuram", "hallmark", "gold duty", "gold import"],
        "why": ("Jewellers (Titan, Kalyan, Senco) track the gold-price trend, festive/wedding "
                "demand, and the structural shift from unorganised to organised retail. An "
                "import-duty cut earlier caused inventory write-downs (the crash) then a demand "
                "surge on cheaper gold (the rally) — hence the round-trip. Rising gold also lifts "
                "gold financiers (Muthoot, Manappuram) via higher loan value/AUM."),
    },
    {
        "name": "🔋 EV vs ICE",
        "keywords": ["ev", "electric vehicle", "ola electric", "ather", "battery", "charging",
                     "fame", "lithium", "e-2w", "e-scooter"],
        "why": ("Costly oil pressures ICE demand but accelerates EV adoption — tailwind for EV "
                "makers (Tata Motors, M&M, Ola Electric, Ather) and battery/ancillary plays. "
                "Watch FAME/PLI subsidy and charging-infra news; policy support is the swing factor."),
    },
    {
        "name": "🖥️ Semiconductors / India chip push",
        "keywords": ["semiconductor", "chip", "fab", "micron", "tata electronics", "kaynes",
                     "cg power", "foundry", "wafer", "osat", "atmp", "ism", "semicon"],
        "why": ("India's semiconductor mission (PLI for fabs — Micron Sanand, Tata-PSMC, CG Power, "
                "Kaynes) makes chip-ecosystem names sensitive to the global chip cycle AND govt "
                "incentives. Positive for OSAT/ATMP, equipment and EMS suppliers."),
    },
    {
        "name": "🇨🇳 China chip / US-China tech war",
        "keywords": ["china chip", "smic", "huawei", "chip design", "export curb", "export control",
                     "china+1", "rare earth", "gallium", "germanium", "nvidia china"],
        "why": ("Chinese chip-design advances (SMIC, Huawei) plus US export curbs drive 'China+1' "
                "supply-chain diversification — a structural positive for Indian EMS/electronics "
                "(Dixon, Kaynes, Amber). But watch China's rare-earth/gallium/germanium curbs as "
                "an input-cost risk for the same names."),
    },
    {
        "name": "🏦 RBI reserves / forward book / gold",
        "keywords": ["forex reserves", "foreign exchange reserves", "forward book",
                     "forward position", "gold reserves", "rbi gold", "import cover", "rbi reserves"],
        "why": ("RBI's FX toolkit — a large short **forward dollar book**, spot reserves, and steady "
                "gold buying — is how it defends the rupee without draining spot liquidity. Rolling or "
                "delivering forwards moves forward premia and banking-system liquidity, which feeds "
                "straight into **G-sec yields**. Gold accumulation diversifies reserves away from the "
                "dollar. _Exact figures are in the RBI Weekly Statistical Supplement (Fridays) — this "
                "tool does not fetch them live, so don't quote a specific number from here._"),
    },
    {
        "name": "💵 Corporate dollar bonds / ECBs",
        "keywords": ["dollar bond", "foreign currency bond", "masala bond", "external commercial",
                     "offshore bond", "overseas bond", "eurobond"],
        "why": ("Indian firms raising dollar/offshore bonds pull foreign capital in "
                "(rupee-supportive, adds to reserves) but raise external debt and hedging demand — "
                "which lifts **forward premia**. Cheaper offshore funding vs domestic rates also "
                "eases pressure on the local corporate-bond market and G-secs. A weaker rupee or "
                "higher US yields makes this window costlier."),
    },
    {
        "name": "🛒 Retail sales & consumer sentiment (IN + US)",
        "keywords": ["retail sales", "consumer sentiment", "consumer confidence", "consumer spending",
                     "michigan", "conference board", "durable goods", "festive demand",
                     "rural demand", "fmcg volume"],
        "why": ("Consumer data sets the rate path on both sides. Strong **US** retail sales/sentiment "
                "→ Fed higher-for-longer → firmer US yields & dollar → FII and G-sec pressure here. "
                "In **India**, festive/rural demand and FMCG volumes shape the RBI growth-inflation "
                "call and consumer-stock earnings. Weak sentiment → rate-cut hopes → bond-positive, "
                "supportive for rate-sensitives."),
    },
]

# ---------------------------------------------------------------------------
# CAUSAL / SENTIMENT ENGINE  (intuition for "how much % down", NOT a forecast)
# ---------------------------------------------------------------------------
# Approximate same-day sensitivity of each index to each driver, expressed as
# "% index move per unit of driver". These are ROUGH, HAND-SET starting points
# for building intuition — edit them, and ideally re-calibrate against history
# with a proper backtest. They are deliberately transparent, not a black box.
#
#   oil_pct / vix_pct / us10y_pct / dxy_pct / kospi_pct : per +1% in that driver
#   fii_kcr : per +₹1,000 cr of FII net cash flow (buying = +, selling = -)
#   geopolitics_hits : per war/Iran/Hormuz headline
# Sanity caps on driver moves for the expected-move contribution (#8): a single
# stale/outlier print (e.g. Kospi +9%) shouldn't dominate. Beyond the cap the
# contribution is clamped AND flagged. Caps are ~normal daily extremes.
DRIVER_CAPS = {
    "kospi_pct": 6.0, "sox_pct": 6.0, "oil_pct": 6.0,
    "vix_pct": 25.0, "dxy_pct": 2.0, "us10y_pct": 6.0,
}

SENSITIVITY = {
    # Calibration-informed (calibrate.py, ~3y) then VETTED:
    #  • DXY / US10Y / oil / Kospi adopted from the fit (right signs, informative)
    #  • VIX dampened vs the raw fit — it's coincident (derived from Nifty options),
    #    not a leading cause, so we don't let it dominate.
    #  • SOX kept a small POSITIVE by hand — the raw fit flipped it negative due to
    #    multicollinearity with Nasdaq/Kospi. For Indian IT specifically the AI signal
    #    is regime-dependent (threat vs deal-win) and is handled by ai_it_stance(), not
    #    this coefficient.
    "Nifty 50": {
        "oil_pct":         -0.02,
        "vix_pct":         -0.05,
        "us10y_pct":       -0.035,
        "dxy_pct":         -0.10,   # calibration: dollar strength is a big Nifty headwind
        "kospi_pct":        0.035,  # #1 weak/indirect transmission (Korea→Asia-tech→India) — cut
        "sox_pct":          0.035,  # weak/indirect — cut so it can't dominate the Nifty move
        "fii_kcr":          0.12,
        "geopolitics_hits":-0.05,
        "india_cpi_hot":   -0.10,   # hot India CPI -> RBI hawkish -> bearish
        "us_cpi_cool":      0.15,   # cool US CPI -> Fed-easing hopes -> risk-on tailwind
    },
    "Bank Nifty": {
        "oil_pct":         -0.04,
        "vix_pct":         -0.06,
        "us10y_pct":       -0.09,   # calibration: banks most rate-sensitive
        "dxy_pct":         -0.05,
        "kospi_pct":        0.04,   # weak transmission — cut
        "sox_pct":          0.02,
        "fii_kcr":          0.16,
        "geopolitics_hits":-0.06,
        "india_cpi_hot":   -0.15,   # banks most exposed to RBI staying hawkish
        "us_cpi_cool":      0.10,
    },
}

# Weak-transmission drivers: their DOMINANCE is capped (a Korean index shouldn't
# explain a third of the Nifty move). #1
WEAK_TRANSMISSION = {"kospi_pct", "sox_pct"}
MAX_WEAK_DOMINANCE = 0.10   # ≤10%: Kospi/SOX reach Nifty only via a long chain
                            # (Kospi→global tech→Indian IT→Nifty); rarely dominant
                            # unless IT leads the tape, so cap their explanatory share low

# A few marquee stocks worth eyeballing each week
STOCKS = {
    "Reliance":   "RELIANCE.NS",
    "HDFC Bank":  "HDFCBANK.NS",
    "ICICI Bank": "ICICIBANK.NS",
    "TCS":        "TCS.NS",
    "Infosys":    "INFY.NS",
    "Tata Steel": "TATASTEEL.NS",
}

# Indian IT pack — tracked separately for the "AI fear" watch
IT_STOCKS = {
    "TCS":          "TCS.NS",
    "Infosys":      "INFY.NS",
    "Wipro":        "WIPRO.NS",
    "HCL Tech":     "HCLTECH.NS",
    "Tech Mahindra":"TECHM.NS",
    "LTIMindtree":  "LTIM.NS",
    "Persistent":   "PERSISTENT.NS",
    "Coforge":      "COFORGE.NS",
}

# LIVE Nifty 50 weights + constituents from the strategy_framework master
# (nifty-50-stock-list.csv, updated weekly) — so nothing drifts. Falls back to the
# built-in dict below only if the master isn't reachable (e.g. newsindex/ copied alone).
def _load_nifty_master():
    try:
        import importlib.util
        path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..",
            "strategy_framework", "config", "constituents.py"))
        spec = importlib.util.spec_from_file_location("nifty_constituents", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        w = dict(getattr(mod, "WEIGHTS_PCT", {}) or {})
        syms = set(getattr(mod, "SECTOR_OF", {}).keys()) or set(w.keys())
        return (w, syms) if w else (None, None)
    except Exception:
        return None, None


_LIVE_WEIGHTS, _LIVE_SYMS = _load_nifty_master()
HAVE_LIVE_WEIGHTS = _LIVE_WEIGHTS is not None

# Nifty 50 constituents (NSE symbols). Live from the master; fallback set below.
NIFTY50 = _LIVE_SYMS or {
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK","BAJAJ-AUTO",
    "BAJFINANCE","BAJAJFINSV","BEL","BHARTIARTL","CIPLA","COALINDIA","DRREDDY",
    "EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE","HEROMOTOCO","HINDALCO",
    "HINDUNILVR","ICICIBANK","INDUSINDBK","INFY","ITC","JIOFIN","JSWSTEEL",
    "KOTAKBANK","LT","M&M","MARUTI","NESTLEIND","NTPC","ONGC","POWERGRID",
    "RELIANCE","SBILIFE","SBIN","SHRIRAMFIN","SUNPHARMA","TCS","TATACONSUM",
    "TATAMOTORS","TATASTEEL","TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO",
}

# Nifty 50 weights (%). LIVE from the master; the dict below is only a fallback.
NIFTY50_WEIGHTS = _LIVE_WEIGHTS or {
    "HDFCBANK": 13.0, "ICICIBANK": 8.5, "RELIANCE": 8.5, "BHARTIARTL": 4.5,
    "INFY": 5.0, "TCS": 4.0, "ITC": 3.8, "LT": 3.8, "AXISBANK": 3.0,
    "KOTAKBANK": 3.0, "SBIN": 3.0, "BAJFINANCE": 2.6, "HINDUNILVR": 2.3,
    "M&M": 2.3, "MARUTI": 2.0, "SUNPHARMA": 1.8, "TATAMOTORS": 1.7, "NTPC": 1.6,
    "HCLTECH": 1.6, "TITAN": 1.3, "ULTRACEMCO": 1.2, "TATASTEEL": 1.2,
    "POWERGRID": 1.2, "ASIANPAINT": 1.1, "TECHM": 1.0, "JSWSTEEL": 0.9,
    "WIPRO": 0.7, "BAJAJ-AUTO": 0.9, "NESTLEIND": 0.9, "ONGC": 0.9,
    "COALINDIA": 0.8, "DRREDDY": 0.8, "CIPLA": 0.7, "BEL": 1.0,
    "GRASIM": 0.9, "HEROMOTOCO": 0.5, "EICHERMOT": 0.7, "BAJAJFINSV": 1.6,
    "SBILIFE": 0.8, "HDFCLIFE": 0.7, "JIOFIN": 0.9, "TATACONSUM": 0.7,
    "APOLLOHOSP": 0.9, "SHRIRAMFIN": 0.9, "TRENT": 1.2, "ADANIENT": 0.7,
    "ADANIPORTS": 0.9, "INDUSINDBK": 0.6, "SUNPHARMA": 1.8,
}

# Sector-representative proxies for the cause→effect scorecard (new sectors).
SECTOR_UNIVERSE = {
    "Bharti Airtel":  "BHARTIARTL.NS",   # telecom
    "UltraTech":      "ULTRACEMCO.NS",   # cement
    "NTPC":           "NTPC.NS",          # power
    "Power Grid":     "POWERGRID.NS",
    "Maruti":         "MARUTI.NS",        # auto PV (ICE)
    "M&M":            "M&M.NS",
    "Bajaj Auto":     "BAJAJ-AUTO.NS",    # auto 2W (ICE)
    "Hero MotoCorp":  "HEROMOTOCO.NS",
    "Tata Motors":    "TATAMOTORS.NS",    # auto EV/CV
    "Sun Pharma":     "SUNPHARMA.NS",     # pharma
    "Dr Reddy":       "DRREDDY.NS",
    "Cipla":          "CIPLA.NS",
    "HAL":            "HAL.NS",            # defence
    "Bharat Electronics": "BEL.NS",
    "DLF":            "DLF.NS",            # realty
    "Havells":        "HAVELLS.NS",        # consumer durables
    "SRF":            "SRF.NS",            # chemicals
    "L&T":            "LT.NS",             # capital goods
}

# Indian IT / AI-fear detector: a headline counts if it mentions an IT name
# or IT-services terms, especially alongside AI/chip worries.
IT_NAME_HINTS = [
    "tcs", "infosys", "infy", "wipro", "hcl", "tech mahindra", "ltimindtree",
    "coforge", "persistent", "mphasis", "it services", "indian it", "it stocks",
    "it sector", "nasscom", "it pack", "deal wins", "tcv",
]

# The AI signal for Indian IT is TWO-SIDED and regime-dependent:
#   OPPORTUNITY  -> AI deal wins, GenAI services revenue, AI transformation contracts
#   THREAT       -> AI automating services work, job cuts, pricing pressure, lower hiring
# A single coefficient can't sign this — the NEWS framing decides. ai_it_stance() below
# reads which way today's AI-for-IT headlines lean.
AI_IT_BULL = [
    "ai deal", "genai deal", "gen ai deal", "ai contract", "ai architecture",
    "ai transformation", "ai implementation", "ai platform", "ai services",
    "deal win", "wins deal", "bags deal", "bags order", "large deal", "mega deal",
    "multi-year deal", "big deal", "tcv", "order win", "new deal", "deal pipeline",
    "gcc", "ai-led growth", "ai revenue",
]
AI_IT_BEAR = [
    "job cut", "jobs cut", "job loss", "jobs lost", "layoff", "lay off", "headcount",
    "hiring freeze", "hiring slow", "slower hiring", "reduce hiring", "ai will replace",
    "replace jobs", "automation", "pricing pressure", "revenue risk", "attrition",
    "bench", "discretionary spend", "furlough", "margin pressure", "fewer jobs",
    "ai threat", "disrupt it", "cannibalis",
    # demand / guidance / earnings threats (e.g. IBM/Accenture warnings hit Indian IT)
    "guidance cut", "cuts guidance", "cut guidance", "lowers guidance", "weak guidance",
    "profit warning", "revenue miss", "misses estimates", "below estimates", "warning",
    "adr crash", "adrs crash", "crash", "slump", "spending cut", "budget cut",
    "deal delay", "demand slowdown", "weak demand", "downgrade", "cut target",
]

# Global IT peers whose results/guidance are a read-through for Indian IT services.
IT_PEERS = ["ibm", "accenture", "cognizant", "capgemini", "infosys adr", "wipro adr"]
IT_PEER_WARN = ["warning", "warns", "cuts guidance", "cut guidance", "guidance cut",
                "lowers guidance", "weak guidance", "profit warning", "revenue miss",
                "misses estimates", "below estimates", "disappoints", "ripples"]

# Company gazetteer for the COMPANY SUMMARY: (headline keyword, display, symbol, sector).
# Order matters — put more specific keywords first (e.g. "hdfc life" before "hdfc").
COMPANY_GAZETTEER = [
    ("hcl tech", "HCL Technologies", "HCLTECH", "IT"),
    ("hcltech", "HCL Technologies", "HCLTECH", "IT"),
    ("tech mahindra", "Tech Mahindra", "TECHM", "IT"),
    ("ltimindtree", "LTIMindtree", "LTIM", "IT"),
    ("infosys", "Infosys", "INFY", "IT"),
    ("wipro", "Wipro", "WIPRO", "IT"),
    ("coforge", "Coforge", "COFORGE", "IT"),
    ("persistent", "Persistent Systems", "PERSISTENT", "IT"),
    ("tcs", "TCS", "TCS", "IT"),
    ("hdfc life", "HDFC Life", "HDFCLIFE", "Insurance"),
    ("hdfc bank", "HDFC Bank", "HDFCBANK", "Bank"),
    ("icici prudential", "ICICI Prudential", "ICICIPRULI", "Insurance"),
    ("icici bank", "ICICI Bank", "ICICIBANK", "Bank"),
    ("kotak", "Kotak Mahindra Bank", "KOTAKBANK", "Bank"),
    ("axis bank", "Axis Bank", "AXISBANK", "Bank"),
    ("idbi", "IDBI Bank", "IDBI", "PSU Bank"),
    ("jio financial", "Jio Financial", "JIOFIN", "Financials"),
    ("reliance", "Reliance Industries", "RELIANCE", "Energy/Conglomerate"),
    ("kalyan", "Kalyan Jewellers", "KALYANKJIL", "Jewellery"),
    ("titan", "Titan", "TITAN", "Jewellery"),
    ("muthoot", "Muthoot Finance", "MUTHOOTFIN", "Gold financier"),
    ("tata motors", "Tata Motors", "TATAMOTORS", "Auto/EV"),
    ("ola electric", "Ola Electric", "OLAELEC", "EV"),
    ("maruti", "Maruti Suzuki", "MARUTI", "Auto"),
    ("tata steel", "Tata Steel", "TATASTEEL", "Metals"),
    ("jsw steel", "JSW Steel", "JSWSTEEL", "Metals"),
    ("hindalco", "Hindalco", "HINDALCO", "Metals"),
    ("vedanta", "Vedanta", "VEDL", "Metals"),
    ("dixon", "Dixon Tech", "DIXON", "EMS/Electronics"),
    ("kaynes", "Kaynes Tech", "KAYNES", "Semiconductor"),
    ("cg power", "CG Power", "CGPOWER", "Electricals"),
    ("bel", "Bharat Electronics", "BEL", "Defence"),
    ("hal", "Hindustan Aeronautics", "HAL", "Defence"),
    ("mazagon", "Mazagon Dock", "MAZDOCK", "Defence"),
    ("bhel", "BHEL", "BHEL", "Capital goods"),
    ("ongc", "ONGC", "ONGC", "Energy (upstream)"),
    ("bpcl", "BPCL", "BPCL", "Energy (OMC)"),
    ("nestle", "Nestle India", "NESTLEIND", "FMCG"),
    ("hindustan unilever", "Hindustan Unilever", "HINDUNILVR", "FMCG"),
    ("asian paints", "Asian Paints", "ASIANPAINT", "Paints"),
    ("dr reddy", "Dr Reddy's", "DRREDDY", "Pharma"),
    ("cipla", "Cipla", "CIPLA", "Pharma"),
    ("biocon", "Biocon", "BIOCON", "Pharma"),
    ("polycab", "Polycab", "POLYCAB", "Cables/Wires"),
    ("indigo", "InterGlobe (IndiGo)", "INDIGO", "Aviation"),
    ("sun pharma", "Sun Pharma", "SUNPHARMA", "Pharma"),
    # Telecom
    ("bharti airtel", "Bharti Airtel", "BHARTIARTL", "Telecom"),
    ("airtel", "Bharti Airtel", "BHARTIARTL", "Telecom"),
    ("vodafone idea", "Vodafone Idea", "IDEA", "Telecom"),
    ("indus towers", "Indus Towers", "INDUSTOWER", "Telecom"),
    # Cement
    ("ultratech", "UltraTech Cement", "ULTRACEMCO", "Cement"),
    ("grasim", "Grasim", "GRASIM", "Cement"),
    ("ambuja", "Ambuja Cements", "AMBUJACEM", "Cement"),
    ("shree cement", "Shree Cement", "SHREECEM", "Cement"),
    ("dalmia", "Dalmia Bharat", "DALBHARAT", "Cement"),
    # Power & Utilities
    ("ntpc", "NTPC", "NTPC", "Power"),
    ("power grid", "Power Grid", "POWERGRID", "Power"),
    ("tata power", "Tata Power", "TATAPOWER", "Power"),
    ("adani power", "Adani Power", "ADANIPOWER", "Power"),
    ("nhpc", "NHPC", "NHPC", "Power"),
    ("jsw energy", "JSW Energy", "JSWENERGY", "Power"),
    # Auto — split by type
    ("bajaj auto", "Bajaj Auto", "BAJAJ-AUTO", "Auto-2W"),
    ("hero moto", "Hero MotoCorp", "HEROMOTOCO", "Auto-2W"),
    ("tvs motor", "TVS Motor", "TVSMOTOR", "Auto-2W"),
    ("eicher", "Eicher Motors", "EICHERMOT", "Auto-2W/CV"),
    ("ashok leyland", "Ashok Leyland", "ASHOKLEY", "Auto-CV"),
    ("mahindra & mahindra", "M&M", "M&M", "Auto-PV/UV"),
    # Pharma
    ("dr. reddy", "Dr Reddy's", "DRREDDY", "Pharma"),
    ("divi", "Divi's Labs", "DIVISLAB", "Pharma"),
    ("lupin", "Lupin", "LUPIN", "Pharma"),
    ("aurobindo", "Aurobindo Pharma", "AUROPHARMA", "Pharma"),
    ("mankind", "Mankind Pharma", "MANKIND", "Pharma"),
    # Defence
    ("hindustan aeronautics", "Hindustan Aeronautics", "HAL", "Defence"),
    ("bharat dynamics", "Bharat Dynamics", "BDL", "Defence"),
    ("cochin shipyard", "Cochin Shipyard", "COCHINSHIP", "Defence"),
    ("bharat forge", "Bharat Forge", "BHARATFORG", "Defence/Auto"),
    # Realty
    ("dlf", "DLF", "DLF", "Realty"),
    ("godrej properties", "Godrej Properties", "GODREJPROP", "Realty"),
    ("oberoi realty", "Oberoi Realty", "OBEROIRLTY", "Realty"),
    ("prestige", "Prestige Estates", "PRESTIGE", "Realty"),
    ("lodha", "Lodha (Macrotech)", "LODHA", "Realty"),
    ("macrotech", "Lodha (Macrotech)", "LODHA", "Realty"),
    # Consumer Durables
    ("havells", "Havells", "HAVELLS", "Consumer Durables"),
    ("voltas", "Voltas", "VOLTAS", "Consumer Durables"),
    ("blue star", "Blue Star", "BLUESTARCO", "Consumer Durables"),
    ("crompton", "Crompton Greaves", "CROMPTON", "Consumer Durables"),
    ("whirlpool", "Whirlpool", "WHIRLPOOL", "Consumer Durables"),
    # Chemicals
    ("srf", "SRF", "SRF", "Chemicals"),
    ("pi industries", "PI Industries", "PIIND", "Chemicals"),
    ("deepak nitrite", "Deepak Nitrite", "DEEPAKNTR", "Chemicals"),
    ("aarti industries", "Aarti Industries", "AARTIIND", "Chemicals"),
    ("pidilite", "Pidilite", "PIDILITIND", "Chemicals"),
    ("tata chemicals", "Tata Chemicals", "TATACHEM", "Chemicals"),
    # Capital goods / infra
    ("larsen", "Larsen & Toubro", "LT", "Capital Goods"),
    ("l&t", "Larsen & Toubro", "LT", "Capital Goods"),
    ("siemens", "Siemens India", "SIEMENS", "Capital Goods"),
    ("abb india", "ABB India", "ABB", "Capital Goods"),
]

# General positive / negative headline words (company sentiment).
POS_WORDS = ["jump", "surge", "rally", "gain", "rise", "soar", "skyrocket", "rocket",
             "record", "strong", "beat", "wins", "win ", "bags", "deal win", "upgrade",
             "multibagger", "profit rise", "profit jump", "approval", "expansion", "order win",
             "rerating", "re-rating", "outperform", "highest ever"]
NEG_WORDS = ["fall", "drop", "slump", "plunge", "crash", "decline", "slide", "loss",
             "miss", "cut", "weak", "downgrade", "pressure", "selloff", "sell-off",
             "under pressure", "unfavourable", "unfavorable", "concern", "risk", "warns",
             "exit", "trims", "hit", "sinks", "tumble", "drag"]

# RSS feeds. Reuters/CNBC give global cues; ET/MC/BS give Indian cues.
RSS_FEEDS = {
    # India-first sources (weighted higher — see INDIA_FEEDS below)
    "ET Markets":        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "ET Stocks (India)": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "Moneycontrol":      "https://www.moneycontrol.com/rss/marketreports.xml",
    "Moneycontrol Biz":  "https://www.moneycontrol.com/rss/business.xml",
    "Business Standard": "https://www.business-standard.com/rss/markets-106.rss",
    "Livemint Markets":  "https://www.livemint.com/rss/markets",
    # Global cues (kept, but flagged as global so India sections can de-prioritise)
    "Reuters Markets":   "https://feeds.reuters.com/reuters/businessNews",
    "CNBC Markets":      "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
}
# Sources treated as India-relevant by default
INDIA_FEEDS = {"ET Markets", "ET Stocks (India)", "Moneycontrol",
               "Moneycontrol Biz", "Business Standard", "Livemint Markets"}

# News-quality weights (0-1): reputable wires/filings high, opinion/tips low.
SOURCE_WEIGHTS = {
    "Reuters Markets": 1.0, "CNBC Markets": 0.9, "Business Standard": 0.85,
    "ET Markets": 0.8, "ET Stocks (India)": 0.8, "Livemint Markets": 0.8,
    "Moneycontrol": 0.75, "Moneycontrol Biz": 0.75,
}
DEFAULT_SOURCE_WEIGHT = 0.5
# Headlines that look like tips/opinion (down-weighted — not hard market news)
OPINION_HINTS = ["should you buy", "experts recommend", "stocks to buy", "time to buy",
                 "multibagger", "buy or book profits", "do you own", "top picks",
                 "shares to buy", "stock to buy", "rally up to"]

# Headlines matching these get tagged as market-moving "macro/geopolitics"
MACRO_KEYWORDS = [
    "iran", "israel", "hormuz", "war", "ceasefire", "oil", "crude", "opec",
    # energy-corridor / new-front terms so a Houthi/Red-Sea/tanker story is tagged macro
    # even when it carries no other macro word (else it's dropped from §1b headlines)
    "houthi", "red sea", "tanker", "supertanker", "blockade", "sanction", "strait",
    "russia", "ukraine", "gulf", "middle east", "silver", "copper", "defence", "defense",
    "fed", "fomc", "powell", "rate", "inflation", "cpi", "wpi", "tariff",
    "trump", "ai ", "chip", "nvidia", "rbi", "gdp", "recession", "yield",
    # US labour / macro that moves Indian markets via FII + rate expectations
    "payroll", "jobs", "jobless", "unemployment", "nonfarm",
    "retail sales", "consumer sentiment", "consumer confidence", "consumer spending",
    "michigan", "conference board", "durable goods",
    # India flows / domestic
    "fii", "dii", "fpi", "sip", "mutual fund", "inflow", "outflow", "sebi",
    "rupee", "dollar index", "dxy", "kospi", "kosdaq",
    # RBI toolkit / bonds / external debt
    "forex reserves", "foreign exchange reserves", "forward book", "forward position",
    "gold reserves", "g-sec", "gsec", "bond yield", "10-year", "10 year",
    "dollar bond", "masala bond", "external commercial", "offshore bond",
]

# Government / policy push detector (sector tailwinds)
POLICY_HINTS = [
    "pli", "production linked", "budget", "subsidy", "incentive", "cabinet approves",
    "cabinet approved", "govt", "government", "ministry", "scheme", "defence",
    "defense", "railway", "semiconductor", "chip plant", "ethanol", "renewable",
    "solar", "capex push", "infrastructure", "make in india", "duty", "ban on import",
    "safeguard duty", "reforms", "gst", "disinvestment",
]

MAX_HEADLINES_PER_FEED = 12

# How many results-reporting companies to enrich with fundamentals per run.
# Each one is a yfinance lookup (~1s), so keep it sane. Set 0 to disable.
MAX_EARNINGS_ENRICH = 20

# Optional: use a LOCAL LLM (Ollama) to write a narrative summary of the news.
# Free, private, offline. Install: https://ollama.com  then `ollama pull llama3.1:8b`
# Leave False and you still get the rule-based verdict banner (no LLM needed).
USE_LOCAL_LLM = True
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"   # light & cool; prompt hardened + output cleaned for the 3B


# =========================================================================
# 1. PRICES
# =========================================================================

def _kw_match(text: str, keywords) -> bool:
    r"""Word-boundary keyword match. Single tokens use \b..\b so 'ev' won't fire
    inside 'revealed'/'Reeves'; multi-word phrases fall back to substring."""
    t = (text or "").lower()
    for k in keywords:
        k = k.strip().lower()
        if not k:
            continue
        if " " in k:
            if k in t:
                return True
        elif re.search(rf"\b{re.escape(k)}\b", t):
            return True
    return False


def _kw_hits(text: str, keywords) -> list:
    """Same boundary logic, but returns the list of matched keywords (for tags)."""
    t = (text or "").lower()
    hits = []
    for k in keywords:
        ks = k.strip().lower()
        if not ks:
            continue
        if (" " in ks and ks in t) or (" " not in ks and re.search(rf"\b{re.escape(ks)}\b", t)):
            hits.append(ks)
    return sorted(set(hits))


def is_foreign_desk(n: dict) -> bool:
    """ET/CNBC foreign-desk stories: reliable markers of non-India content."""
    t = n.get("title", "").lower()
    return (t.startswith("global market") or t.startswith("us stock market")
            or n.get("source") in ("Reuters Markets", "CNBC Markets"))


def is_india_relevant(n: dict) -> bool:
    """True for India-desk sources or headlines that name India explicitly."""
    if n.get("source") in INDIA_FEEDS and not is_foreign_desk(n):
        return True
    return _kw_match(n.get("title", ""),
                     ["india", "indian", "nifty", "sensex", "rbi", "sebi",
                      "rupee", "dalal", "fii", "dii", "sip"])


def _fetch_single_quote(name: str, sym: str) -> dict:
    """Fetch ONE symbol's quote, bounded to 3s (call_with_timeout). Never raises."""
    row = {"name": name, "symbol": sym, "last": None, "pct_change": None,
           "pct_intraday": None, "asof": None, "suspect": False}

    def _get_data():
        t = yf.Ticker(sym)
        last_val = prev_val = opn_val = hi_val = lo_val = asof_val = None
        try:
            fi = t.fast_info
            last_val = _fi_get(fi, "last_price", "lastPrice")
            prev_val = _fi_get(fi, "previous_close", "previousClose")
            opn_val  = _fi_get(fi, "open")
            hi_val   = _fi_get(fi, "day_high", "dayHigh")
            lo_val   = _fi_get(fi, "day_low", "dayLow")
        except Exception:
            pass
        if last_val is None or prev_val is None:
            hist = t.history(period="7d")
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                last_val, prev_val = float(closes.iloc[-1]), float(closes.iloc[-2])
                asof_val = closes.index[-1].date().isoformat()
                try:
                    opn_val = float(hist.loc[closes.index[-1], "Open"])
                except Exception:
                    pass
        return last_val, prev_val, opn_val, hi_val, lo_val, asof_val

    try:
        last, prev, opn, hi, lo, asof = call_with_timeout(_get_data, timeout=3.0)
        if asof:
            row["asof"] = asof
        # populate on SUCCESS (this block previously sat inside the except → prices never set)
        if last is not None and prev not in (None, 0) and not (
                math.isnan(last) or math.isnan(prev)):
            row["last"] = round(last, 2)
            row["pct_change"] = round((last - prev) / prev * 100, 2)     # vs prev close
            if opn and opn > 0 and not math.isnan(opn):
                row["pct_intraday"] = round((last - opn) / opn * 100, 2)
            # SANITY: a live price must sit inside today's [low, high]; else the bar is stale/bad.
            if hi and lo and hi > lo and (last > hi * 1.001 or last < lo * 0.999):
                row["suspect"] = True
    except Exception as e:
        row["error"] = f"timeout/error: {str(e)[:40]}"
    return row


def fetch_quotes(symbol_map: dict[str, str], max_workers: int = 10) -> list[dict]:
    """Return [{name, symbol, last, pct_change, ...}] using yfinance, fetched IN PARALLEL.

    Each symbol is bounded to 3s (call_with_timeout); a thread pool runs up to `max_workers`
    concurrently, so a full universe returns in seconds instead of minutes and rate-limited
    symbols time out together, not serially. Input order is preserved. Worst case ≈
    ceil(N/max_workers) × 3s (kept modest to avoid Yahoo burst-throttling).
    """
    if not HAVE_YF or not symbol_map:
        return []
    items = list(symbol_map.items())
    workers = max(1, min(max_workers, len(items)))
    with _futures.ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda it: _fetch_single_quote(it[0], it[1]), items))


def _fi_get(fi, *keys):
    """Read a value from yfinance fast_info across version key-name differences."""
    for k in keys:
        try:
            v = fi[k]
            if v is not None:
                return float(v)
        except Exception:
            continue
    return None


# =========================================================================
# 2. NEWS (RSS)
# =========================================================================

def fetch_news() -> list[dict]:
    """Pull recent headlines from all RSS feeds, tag macro-relevant ones."""
    items = []
    for source, url in RSS_FEEDS.items():
        try:
            import socket
            orig_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(5.0)  # 5-second allowance per RSS feed
            try:
                feed = feedparser.parse(url)
            finally:
                socket.setdefaulttimeout(orig_timeout)
            for entry in feed.entries[:MAX_HEADLINES_PER_FEED]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                blob = title + " " + entry.get("summary", "")
                tags = _kw_hits(blob, MACRO_KEYWORDS)
                items.append({
                    "source": source,
                    "title": title,
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "macro": bool(tags),
                    "tags": ",".join(tags),
                })
        except Exception as e:
            items.append({"source": source, "title": f"[feed error: {e}]",
                          "link": url, "published": "", "macro": False, "tags": ""})
    return _dedup_news(items)


def _dedup_news(items: list[dict]) -> list[dict]:
    """Collapse near-duplicate stories (ET Markets + ET Stocks often carry the
    same article) by a normalized-title key, keeping the first occurrence."""
    seen, out = set(), []
    for it in items:
        # full normalized title (safe: only collapses genuine duplicates)
        key = re.sub(r"[^a-z0-9]+", " ", it.get("title", "").lower()).strip()
        if key and key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# =========================================================================
# 3. EARNINGS CALENDAR (NSE public endpoint, graceful fallback)
# =========================================================================

def fetch_earnings() -> list[dict]:
    """
    NSE serves a corporate-events JSON, but it needs a browser-like session
    (cookie handshake). We try it; if it fails we return an empty list and
    the report notes to check the BSE/NSE calendar manually.
    """
    out = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        s = requests.Session()
        s.headers.update(headers)
        # prime cookies
        s.get("https://www.nseindia.com", timeout=10)
        url = "https://www.nseindia.com/api/event-calendar"
        r = s.get(url, timeout=10)
        if r.status_code == 200:
            for ev in r.json():
                out.append({
                    "company": ev.get("company", ""),
                    "symbol": ev.get("symbol", ""),
                    "purpose": ev.get("purpose", ""),
                    "date": ev.get("date", ""),
                })
    except Exception as e:
        out.append({"company": f"[NSE fetch failed: {str(e)[:60]}]",
                    "symbol": "", "purpose": "check nseindia.com / bseindia.com", "date": ""})
    return out


def fetch_fii_dii() -> list[dict]:
    """
    Daily FII/DII cash-market provisional figures from NSE.
    Endpoint needs the same browser-like cookie handshake as the calendar.
    Returns [] on failure (report notes to check manually).
    """
    out = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        s = requests.Session()
        s.headers.update(headers)
        s.get("https://www.nseindia.com", timeout=10)
        r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=10)
        if r.status_code == 200:
            for row in r.json():
                out.append({
                    "category": row.get("category", ""),
                    "date": row.get("date", ""),
                    "buy": row.get("buyValue", ""),
                    "sell": row.get("sellValue", ""),
                    "net": row.get("netValue", ""),
                })
    except Exception as e:
        out.append({"category": f"[NSE FII/DII fetch failed: {str(e)[:50]}]",
                    "date": "", "buy": "", "sell": "", "net": ""})
    return out


def cross_check_indices(quotes_idx) -> None:
    """
    Cross-check the yfinance index values against NSE's own allIndices API (the
    authoritative exchange source). If they disagree by >0.3%, the yfinance print
    is stale/wrong → flag it suspect. Catches wrong-but-in-range values the
    high–low range check can't. Fails soft if NSE is unreachable.
    """
    NSE_NAME = {"Nifty 50": "NIFTY 50", "Bank Nifty": "NIFTY BANK",
                "Nifty IT": "NIFTY IT", "Sensex": None}   # Sensex is BSE, skip
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
               "Accept": "application/json"}
    try:
        s = requests.Session(); s.headers.update(headers)
        s.get("https://www.nseindia.com", timeout=10)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=10)
        data = {d.get("index"): d for d in r.json().get("data", [])}
    except Exception:
        return
    for q in quotes_idx:
        nse_name = NSE_NAME.get(q["name"])
        if not nse_name or nse_name not in data:
            continue
        try:
            nse_last = float(data[nse_name].get("last"))
        except Exception:
            continue
        if q.get("last") and nse_last:
            diff = abs(q["last"] - nse_last) / nse_last * 100
            q["xcheck"] = {"nse": round(nse_last, 2), "diff_pct": round(diff, 2),
                           "ok": diff < 0.3}
            if diff >= 0.3:
                q["suspect"] = True


def is_it_ai_headline(n: dict) -> bool:
    """True if a headline is about Indian IT and/or AI-driven IT worries."""
    text = n.get("title", "")
    name_hit = _kw_match(text, IT_NAME_HINTS)
    ai_hit = _kw_match(text, ["ai", "chip", "genai", "llm"])
    ctx = _kw_match(text, ["it", "tech", "software", "services"])
    return name_hit or (ai_hit and ctx)


def ai_it_stance(news) -> dict:
    """
    Is today's AI news an OPPORTUNITY or a THREAT for Indian IT? Classifies the
    IT/AI headlines into deal-win (bullish) vs displacement (bearish) and returns
    a net stance. This is what flips the AI-for-IT read when, e.g., 'TCS wins big
    AI-architecture deal' lands — the same AI signal that's usually a threat.
    """
    bull, bear = [], []
    for n in news:
        if not is_it_ai_headline(n):     # strictly IT-relevant (avoids the pronoun 'it')
            continue
        title = n.get("title", "")
        if _kw_match(title, AI_IT_BULL):
            bull.append(n)
        elif _kw_match(title, AI_IT_BEAR):
            bear.append(n)
    score = len(bull) - len(bear)
    if score > 0:
        label = "🟢 Net positive — deal wins / AI opportunity outweigh concerns"
    elif score < 0:
        label = "🔴 Net negative — demand, guidance or AI-displacement concerns dominate"
    else:
        label = "🟡 Balanced / mixed this run"
    return {"bull": bull, "bear": bear, "score": score, "label": label}


def ai_fear_read(eng, quotes_idx) -> str:
    """
    Regime-dependent read of global-chip vs Indian-IT. The relationship is NOT
    fixed: Kospi/SOX up + Indian IT up = risk-on (moving together); but Kospi/SOX
    up + Indian IT DOWN = the AI-FEAR signal — the market treats the AI boom as a
    threat to the services model (clients fund AI infra, not contract renewals).
    """
    kospi = eng["drivers"].get("kospi_pct")
    sox = eng["drivers"].get("sox_pct")
    it = _pct_of(quotes_idx, "Nifty IT")
    if it is None:
        return ""
    gu = (kospi is not None and kospi > 1) or (sox is not None and sox > 1.5)
    gd = (kospi is not None and kospi < -1) or (sox is not None and sox < -1.5)
    gtxt = []
    if kospi is not None: gtxt.append(f"Kospi {kospi:+.1f}%")
    if sox is not None:   gtxt.append(f"SOX {sox:+.1f}%")
    gtxt = ", ".join(gtxt)
    if gu and it < -0.3:
        return (f"🔴 **AI-fear signal** — global AI/chip proxies up ({gtxt}) but **Indian IT down "
                f"(Nifty IT {it:+.1f}%)**. The market is reading the AI boom as a *threat* to the "
                f"services model, not a tailwind: clients are directing budgets to AI infrastructure "
                f"(chips, data centres) over renewing services contracts. Whether that spend converts "
                f"into deals may not show until Q2 results (cf. IBM's warning).")
    if gu and it > 0.3:
        return (f"🟢 **Risk-on, together** — global tech up ({gtxt}) and Indian IT up "
                f"(Nifty IT {it:+.1f}%): IT is riding global AI/chip sentiment, no divergence.")
    if gd and it < -0.3:
        return (f"🟠 Indian IT down *with* weak global tech ({gtxt}) — normal correlation, "
                f"not an IT-specific shock.")
    if gd and it > 0.3:
        return (f"🟢 Indian IT resilient (Nifty IT {it:+.1f}%) despite weak global tech ({gtxt}) — "
                f"IT-specific strength, e.g. deal wins.")
    return ""


def it_peer_readthrough(news) -> list[dict]:
    """Global IT-peer (IBM/Accenture/Cognizant) warnings — a leading read-through
    for Indian IT services demand/guidance."""
    hits = []
    for n in news:
        t = n.get("title", "")
        if _kw_match(t, IT_PEERS) and _kw_match(t, IT_PEER_WARN):
            hits.append(n)
    return hits


def _nifty_impact(sym: str) -> tuple[str, float | None]:
    """Classify a stock's index impact from its Nifty weight."""
    wt = NIFTY50_WEIGHTS.get((sym or "").replace(".NS", "").strip())
    if wt is None:
        return "Negligible", None
    if wt >= 5:
        return "High", wt
    if wt >= 2:
        return "Medium", wt
    if wt >= 0.8:
        return "Low", wt
    return "Negligible", wt


# A CATALYST is genuinely price-moving; plain NEWS is informational (an analyst
# adding a stock, an appointment, a sponsorship) and rarely moves the tape.
_CATALYST_KW = {
    "results": ["result", "results", "earnings", "profit", "net profit", "pat", "revenue",
                "guidance", "beat", "beats", "miss", "misses", "margin", "margins"],
    "corp action": ["fundraise", "fundraising", "raises", "raise", "raised", "raising", "qip",
                    "buyback", "dividend", "stake", "acquire", "acquires", "acquisition",
                    "merger", "merges", "demerger", "order", "orders", "bags", "wins", "won",
                    "contract", "bond", "bonds", "ipo", "block deal"],
    "regulatory": ["ban", "bans", "penalty", "penalise", "fine", "fined", "probe", "raid",
                   "sebi", "cci", "recall", "approval", "approves", "usfda", "warning letter",
                   "downgrade", "downgrades", "upgrade", "upgrades"],
    "shock": ["profit warning", "guidance cut", "resign", "resigns", "fraud", "default"],
}
_NONCATALYST_KW = ["adds", "add", "initiates", "coverage", "appoint", "appoints", "appointed",
                   "appointment", "partnership", "collaborate", "collaboration", "sponsor",
                   "sponsors", "award", "awards", "campaign", "mou", "explore", "talks"]


def _catalyst_kind(tl: str):
    """Return ('Catalyst'|'News', reason). tl is a lowercased title."""
    for tag, kws in _CATALYST_KW.items():
        if _kw_match(tl, kws):
            return "Catalyst", tag
    if _kw_match(tl, _NONCATALYST_KW):
        return "News", "informational"
    return "News", "no explicit catalyst"


def classify_company_news(news) -> list[dict]:
    """
    Detect company-specific stories, classify positive/negative, and tag each with
    the company's sector + Nifty-weight impact. One (first) headline per company.
    """
    out, seen = [], set()
    for n in news:
        title = n.get("title", "")
        tl = title.lower()
        match = None
        for kw, disp, sym, sector in COMPANY_GAZETTEER:
            if _kw_match(title, [kw]):
                match = (disp, sym, sector)
                break
        if not match:
            continue
        disp, sym, sector = match
        if disp in seen:
            continue
        seen.add(disp)
        pos = sum(1 for w in POS_WORDS if w in tl)
        neg = sum(1 for w in NEG_WORDS if w in tl)
        sentiment = "pos" if pos > neg else "neg" if neg > pos else "neutral"
        impact, wt = _nifty_impact(sym)
        kind, why = _catalyst_kind(tl)
        out.append({
            "company": disp, "symbol": sym, "sector": sector, "sentiment": sentiment,
            "title": title, "link": n.get("link", ""), "source": n.get("source", ""),
            "nifty_wt": wt, "nifty_impact": impact, "src_weight": source_weight(n),
            "kind": kind, "kind_why": why,
        })
    # index importance first (weight × source quality), so heavyweight-stock news
    # leads over a big move in a tiny name (#4)
    out.sort(key=lambda c: ((c["nifty_wt"] or 0) * c["src_weight"], c["src_weight"]),
             reverse=True)
    return out


def build_catalysts(earnings) -> dict:
    """Upcoming results grouped: tomorrow vs next ~3 days. Nifty 50 names starred."""
    today = dt.date.today()
    tomorrow, next3, seen = [], [], set()
    for e in earnings:
        if "financial results" not in (e.get("purpose", "") or "").lower():
            continue
        try:
            d = dt.datetime.strptime(e.get("date", ""), "%d-%b-%Y").date()
        except Exception:
            continue
        sym = (e.get("symbol", "") or "").strip()
        if sym in seen:
            continue
        delta = (d - today).days
        star = " ⭐" if e.get("nifty50") else ""
        label = f"{e['company']} ({sym}){star}"
        if delta == 1:
            tomorrow.append(label); seen.add(sym)
        elif 2 <= delta <= 3:
            next3.append(label); seen.add(sym)
    return {"tomorrow": tomorrow, "next3": next3}


def is_policy_headline(n: dict) -> bool:
    """India government / policy / sector-push story (excludes foreign-desk noise)."""
    if is_foreign_desk(n) and not is_india_relevant(n):
        return False
    return _kw_match(n.get("title", ""), POLICY_HINTS)


# Inflation is NOT one-directional: hot India CPI is bearish for India, but a
# cooling US/global CPI is risk-on (Fed-easing hopes -> semis, FII flows, EM).
_CPI_TERMS = ["cpi", "inflation", "consumer price"]
_HOT_TERMS = ["accelerat", "exceed", "above forecast", "above estimate", "hotter",
              "rises", "rose", "surges", "quickens", "jumps", "picks up", "high"]
_COOL_TERMS = ["cool", "soft", "below forecast", "below estimate", "eases", "ease",
               "slows", "slow", "lower than", "misses", "decelerat", "falls", "fell",
               "drops", "weaker"]


def rbi_dovish(news) -> list[dict]:
    out = []
    for n in news:
        t = n.get("title", "").lower()
        if ("rbi" in t or "repo" in t or "mpc" in t or "reserve bank" in t) and any(
                k in t for k in ["rate cut", "repo cut", "cuts rate", "dovish", "easing",
                                 "ease", "liquidity", "lower rate", "rate reduction"]):
            out.append(n)
    return out


def pmi_strong(news) -> list[dict]:
    return [n for n in news if "pmi" in n.get("title", "").lower() and any(
        k in n.get("title", "").lower()
        for k in ["expand", "rises", "rose", "high", "beat", "strong", "accelerat", "improve"])]


def monsoon_read(news):
    good, bad = [], []
    for n in news:
        t = n.get("title", "").lower()
        if "monsoon" in t or "rainfall" in t or "imd" in t:
            if any(k in t for k in ["above normal", "good", "surplus", "normal", "abundant",
                                    "revival", "picks up"]):
                good.append(n)
            elif any(k in t for k in ["deficit", "below normal", "weak", "poor", "delay",
                                      "shortfall", "drought"]):
                bad.append(n)
    return good, bad


def gst_strong(news) -> list[dict]:
    return [n for n in news if "gst" in n.get("title", "").lower() and any(
        k in n.get("title", "").lower()
        for k in ["record", "high", "rises", "rose", "jump", "collection", "surges", "grows"])]


def india_cpi_hot(news) -> list[dict]:
    out = []
    for n in news:
        t = n.get("title", "").lower()
        if ("india" in t or "rbi" in t) and _kw_match(t, _CPI_TERMS) \
           and any(k in t for k in _HOT_TERMS):
            out.append(n)
    return out


def us_cpi_cool(news) -> list[dict]:
    out = []
    for n in news:
        t = n.get("title", "").lower()
        is_us = ("us " in t or "u.s" in t or t.startswith("us") or "fed" in t
                 or "america" in t or "federal reserve" in t)
        if is_us and _kw_match(t, _CPI_TERMS) and any(k in t for k in _COOL_TERMS):
            out.append(n)
    return out


def detect_themes(news) -> list[dict]:
    """
    Match headlines against the THEMES playbook. Returns only *active* themes
    (those with >=1 matching headline), each with its reasoning + the hits.
    """
    active = []
    for theme in THEMES:
        hits = [n for n in news if _kw_match(n.get("title", ""), theme["keywords"])]
        if hits:
            active.append({"name": theme["name"], "why": theme["why"], "hits": hits})
    return active


# ===========================================================================
# TRANSMISSION MAP — driver → channel → sector (causal network, extensible).
# Each driver fans out through multiple economic channels; each channel names
# beneficiaries (🟢) and losers (🔴). New drivers (tariffs, monsoon, fiscal) are
# added by defining their channels here — no per-sector rules needed.
# ===========================================================================
TRANSMISSION = {
    "oil_up": [
        ("① Upstream (producers)", ["ONGC, Oil India (higher realisations)"],
         [], "modified by windfall tax / royalty / gas-price formula — NOT a pure oil play"),
        ("② Downstream (OMCs)", [],
         ["BPCL/IOC/HPCL (crude = input)"],
         "but marketing margins, GRMs & retail-price policy can flip the sign — weak link"),
        ("③ Fuel consumers", [],
         ["aviation (IndiGo)", "paints", "tyres", "logistics", "cement (freight)"],
         "cost-push; offset by each firm's demand & pricing power"),
        ("④ Macro (inflation→rates & FX)", ["IT/pharma exporters (weaker rupee)"],
         ["Banks/NBFCs/realty (via RBI hawkishness)", "importers", "$-debt names"],
         "banks: +ve days–weeks via NIM, −ve later if growth slows"),
    ],
    "oil_down": [
        ("① Upstream (producers)", [],
         ["ONGC, Oil India (lower realisations)"],
         "cushioned by gas-price floor & policy — NOT a pure oil play"),
        ("② Downstream (OMCs)", ["BPCL/IOC/HPCL (cheaper input, wider margins)"],
         [], "size depends on retail-price pass-through policy"),
        ("③ Fuel consumers", ["aviation, paints, tyres, logistics (input relief)"],
         [], "helps most where fuel is a big cost line"),
        ("④ Macro (disinflation→rates & FX)", ["Banks, NBFCs, realty, autos (rate relief)"],
         ["IT/pharma exporters (stronger-rupee headwind)"],
         "disinflation gives RBI room — supportive for rate-sensitives"),
    ],
    "ai_infrastructure": ("AI infrastructure capex",
        ["EMS (Dixon, Kaynes)", "semis (CG Power)", "power (NTPC, Power Grid)",
         "telecom (Bharti)"], [], "structural — AI needs chips, data-centres, power, bandwidth"),
    "ai_productivity": ("AI productivity",
        ["Banks (HDFC, ICICI)", "insurance", "pharma (R&D)", "manufacturing"],
        ["firms slow to adopt AI"], "long-term ROE/margin gain, not via semis"),
    "ai_substitution": ("AI substitution",
        ["AI-platform sellers"], ["IT services (TCS, Infosys, Wipro)", "BPO", "consulting"],
        "the services threat — active only in the Substitution regime"),
}


# Time-horizon of each transmission channel — the causal graph mixes intraday
# effects (FII flow, earnings) with multi-year themes (AI productivity). Labelling
# the horizon stops a trader from acting on a 5-year theme intraday.
CHANNEL_HORIZON = {
    "ai_infrastructure": "months–years",
    "ai_productivity":   "years",
    "ai_substitution":   "quarters",
}
HORIZON_TABLE = [
    ("FII / DII flow", "intraday"),
    ("Earnings / results", "days"),
    ("Oil daily move", "days–weeks"),
    ("AI substitution", "quarters"),
    ("Oil price level (CAD/inflation)", "quarters–year"),
    ("AI infrastructure capex", "months–years"),
    ("AI productivity (ROE/margin)", "years"),
]


# Relationship confidence hierarchy: baseline economic link + the CURRENT MODIFIERS
# that can override it (OMCs/ONGC are policy/margin-modified, not pure oil plays).
# (proxy_short, base_sign_on_oil_UP, baseline_text, modifiers_text)
OIL_RELATIONSHIPS = [
    ("ONGC", +1, "Oil↑ ⇒ ONGC↑ (upstream realisations)",
     "windfall tax, royalty, gas-price formula, production, FII flow"),
    ("BPCL", -1, "Oil↑ ⇒ OMCs↓ (crude = input cost)",
     "marketing margins, GRMs, retail-price policy, inventory gains — NOT deterministic"),
    ("IndiGo", -1, "Oil↑ ⇒ IndiGo↓ (ATF cost)",
     "passenger demand, pricing power, load factor, earnings positioning"),
    ("Asian Paints", -1, "Oil↑ ⇒ Paints↓ (crude-derivative input)",
     "pricing power, volumes, festive demand"),
]


def build_relationship_hierarchy(quotes_macro, sector_quotes):
    """Baseline economic link → current modifiers → today's observed outcome.
    Separates the *relationship* from the *factors that override it* on a given day."""
    oil = _pct_of(quotes_macro, "Brent Crude")
    if oil is None or abs(oil) < 0.25:
        return []
    up = oil > 0
    px = {q["name"].split(" (")[0].strip().lower(): q["pct_change"]
          for q in sector_quotes if q.get("pct_change") is not None}
    rows = []
    for proxy, base_up, baseline, mods in OIL_RELATIONSHIPS:
        exp = base_up * (1 if up else -1)          # expected sign given today's oil direction
        a = px.get(proxy.lower())
        if a is None:
            outcome = "— (no data)"
        else:
            ok = (a == 0) or ((a > 0) == (exp > 0))
            outcome = f"{proxy} {a:+.1f}% → {'✔ Confirmed' if ok else '⚠️ Overridden'}"
        rows.append((f"Oil → {proxy}", baseline, mods, outcome))
    return rows


# ── SECTOR FACTOR MODEL ────────────────────────────────────────────────────
# Institutional approach: don't judge each driver in isolation — aggregate ALL
# active macro / flow / thematic factors into ONE net score per sector.
# Coefficients are HEURISTIC directional sensitivities (sign-consistent with
# economic theory + the calibrated index betas in SENSITIVITY), NOT regression
# betas — they weight direction & rough magnitude, not a point forecast.
# driver keys: percent moves except fii_kcr (₹'000 cr net) and the 0/1 flags.
SECTOR_FACTORS = {
    "Banks/Financials": {
        "india_cpi_hot": +0.10,   # PHASE 1: higher rates → NIM tailwind (banks + early)
        "us10y_pct":     +0.05,   # domestic yields drift up with global — NIM +ve first
        "fii_kcr":       +0.06,   # heavy FII ownership — flow sensitive
        "oil_pct":       +0.02,   # mild; oil hits banks only later via inflation
        "vix_pct":       -0.05,   # high-beta — risk-off hurts
    },
    "IT services": {
        "usdinr_pct":     +0.10,  # weaker rupee lifts export earnings
        "sox_pct":        +0.04,  # global chip/AI risk appetite
        "ai_substitution": -0.30, # regime-gated: services displacement (only if active)
        "us_cpi_cool":    +0.06,  # cooler US CPI → softer landing → client IT spend
        "fii_kcr":        +0.05,
    },
    "Auto": {
        "oil_pct":       -0.04,   # fuel cost / demand (ICE-weighted)
        "india_cpi_hot": -0.08,   # rate-sensitive discretionary demand
        "us10y_pct":     -0.03,
        "ev_theme":      +0.06,   # thematic: EV push (news-flagged)
        "fii_kcr":       +0.04,
    },
    "Pharma": {
        "usdinr_pct": +0.06,      # exporter — weaker rupee helps
        "vix_pct":    +0.02,      # mild defensive bid on risk-off
        "fii_kcr":    +0.03,
    },
    "Metals": {
        "dxy_pct":   -0.10,       # stronger dollar → commodity headwind
        "kospi_pct": +0.05,       # global-growth proxy
        "oil_pct":   +0.03,       # reflation read-through
        "fii_kcr":   +0.04,
    },
    "Energy (upstream)": {
        "oil_pct": +0.08,         # realisations
        "fii_kcr": +0.02,
    },
}

# Which sectors get an earnings kicker when a results/earnings headline tags them.
_SECTOR_KW = {
    "Banks/Financials": ["bank", "hdfc", "icici", "kotak", "axis", "sbi", "nbfc", "finance"],
    "IT services": ["tcs", "infosys", "wipro", "hcl", "tech mahindra", "it ", "software"],
    "Auto": ["auto", "maruti", "tata motors", "mahindra", "bajaj", "hero", "ev ", "electric vehicle"],
    "Pharma": ["pharma", "sun pharma", "cipla", "dr reddy", "divis", "drug"],
    "Metals": ["metal", "tata steel", "jsw", "hindalco", "vedanta", "coal"],
    "Energy (upstream)": ["ongc", "oil india", "reliance", "crude"],
}


def build_sector_factor_model(eng, quotes_macro, observed, news, ai_regime):
    """
    For each sector, aggregate every active driver into ONE net score, instead
    of judging drivers in isolation. Returns [{sector, rows:[(label,contrib)],
    net, verdict}]. Rows show only NON-trivial contributions (active drivers).
    """
    d = eng["drivers"]
    usdinr = eng["raw"].get("usdinr")
    vals = {
        "oil_pct":       d.get("oil_pct", 0.0),
        "us10y_pct":     d.get("us10y_pct", 0.0),
        "dxy_pct":       d.get("dxy_pct", 0.0),
        "kospi_pct":     d.get("kospi_pct", 0.0),
        "sox_pct":       d.get("sox_pct", 0.0),
        "vix_pct":       d.get("vix_pct", 0.0),
        "fii_kcr":       d.get("fii_kcr", 0.0),
        "usdinr_pct":    (0.0 if usdinr is None or (isinstance(usdinr, float) and math.isnan(usdinr))
                          else float(usdinr)),
        "india_cpi_hot": d.get("india_cpi_hot", 0),
        "us_cpi_cool":   d.get("us_cpi_cool", 0),
        "ai_substitution": 1.0 if ai_regime == "Substitution" else 0.0,
    }
    ev_theme = 1.0 if any(_kw_match(n.get("title", "") + " " + n.get("tags", ""),
                          ["ev ", "electric vehicle", "e-scooter", "ather", "ola electric"])
                          for n in news) else 0.0
    vals["ev_theme"] = ev_theme

    lbl = {"oil_pct": "Oil", "us10y_pct": "US10Y", "dxy_pct": "DXY", "kospi_pct": "Kospi",
           "sox_pct": "SOX", "vix_pct": "VIX", "fii_kcr": "FII flow", "usdinr_pct": "Rupee (USDINR)",
           "india_cpi_hot": "India CPI (rates)", "us_cpi_cool": "US CPI cooling",
           "ai_substitution": "AI substitution", "ev_theme": "EV theme"}

    out = []
    for sector, coefs in SECTOR_FACTORS.items():
        rows, net = [], 0.0
        for k, c in coefs.items():
            v = vals.get(k, 0.0)
            contrib = c * v
            if abs(contrib) < 0.005:      # skip inactive drivers
                continue
            rows.append((lbl.get(k, k), round(contrib, 3)))
            net += contrib
        # earnings kicker: sector-tagged results headline today
        ek = 0.0
        for n in news:
            t = (n.get("title", "") + " " + n.get("tags", "")).lower()
            if _kw_match(t, ["result", "earnings", "q1", "q2", "q3", "q4", "profit", "pat "]) \
               and _kw_match(t, _SECTOR_KW.get(sector, [])):
                ek += 0.05
        ek = min(ek, 0.15)
        if ek:
            rows.append(("Earnings headlines", round(ek, 3)))
            net += ek
        rows.sort(key=lambda r: -abs(r[1]))
        verdict = ("🟢 Bullish" if net > 0.10 else "🔴 Bearish" if net < -0.10 else "🟡 Neutral")
        out.append({"sector": sector, "rows": rows, "net": round(net, 3), "verdict": verdict})
    out.sort(key=lambda s: -s["net"])
    return out


def build_transmission_map(eng, quotes_macro, ai_regime, news) -> list[str]:
    """Render the causal network for today's active drivers (oil, AI/semis)."""
    L = []
    oil = eng["drivers"].get("oil_pct", 0.0)
    brent = eng.get("brent_price")
    if abs(oil) >= 0.4:
        key = "oil_up" if oil > 0 else "oil_down"
        lvl = f" (Brent ${brent:.0f})" if brent else ""
        L.append(f"### 🛢️ Oil {oil:+.1f}%{lvl} → {len(TRANSMISSION[key])} channels\n")
        for ch, win, lose, note in TRANSMISSION[key]:
            w = "🟢 " + ", ".join(win) if win else ""
            l = "🔴 " + ", ".join(lose) if lose else ""
            tail = f"  _{note}_" if note else ""
            L.append(f"- **{ch}** → {w}{' · ' if win and lose else ''}{l}{tail}")

    sox = eng["drivers"].get("sox_pct", 0.0)
    kospi = eng["drivers"].get("kospi_pct", 0.0)
    it_ai = any(is_it_ai_headline(n) for n in news)
    if abs(sox) >= 1 or abs(kospi) >= 1 or ai_regime != "Neutral" or it_ai:
        sub_active = "Substitution" in (ai_regime or "")
        L.append(f"\n### 🤖 AI / semis (SOX {sox:+.1f}%, regime: {ai_regime or 'Neutral'}) → 3 channels\n")
        for keyc in ("ai_infrastructure", "ai_productivity", "ai_substitution"):
            ch, win, lose, note = TRANSMISSION[keyc]
            active = ""
            if keyc == "ai_substitution":
                active = " ⚡ **ACTIVE**" if sub_active else " _(dormant — Complement regime)_"
            w = "🟢 " + ", ".join(win) if win else ""
            l = "🔴 " + ", ".join(lose) if lose else ""
            hz = CHANNEL_HORIZON.get(keyc, "")
            hz = f" ⏱️_{hz}_" if hz else ""
            L.append(f"- **{ch}**{active}{hz} → {w}{' · ' if win and lose else ''}{l}  _{note}_")

    # Time-horizon legend: the graph mixes intraday moves with multi-year themes —
    # separate them so a trader knows what's actionable *today* vs what's structural.
    if L:
        L.append("\n**⏱️ Time-horizon of each channel** (don't trade a 5-year theme intraday):\n")
        L.append("| Channel | Horizon |")
        L.append("|---|---|")
        for ch_lbl, hz in HORIZON_TABLE:
            L.append(f"| {ch_lbl} | {hz} |")
    return L


def build_sector_impact(quotes_idx, quotes_macro, news, ai_regime="Neutral") -> list[str]:
    """
    Rule-based cross-asset -> Indian-sector translation. Deterministic, no LLM.
    Reads the day's oil / rupee / Kospi / rates moves and spells out likely
    sector winners & losers with the reasoning.
    """
    oil    = _pct_of(quotes_macro, "Brent Crude")
    gold   = _pct_of(quotes_macro, "Gold")
    usdinr = _pct_of(quotes_macro, "USD/INR")     # +ve = rupee WEAKER
    dxy    = _pct_of(quotes_macro, "Dollar Index")
    us10y  = _pct_of(quotes_macro, "US 10Y Yield")
    kospi  = _pct_of(quotes_macro, "Kospi")
    niftyit= _pct_of(quotes_idx, "Nifty IT")
    vix    = _pct_of(quotes_idx, "India VIX")
    lines = []

    # --- OIL (incl. EV vs ICE split) ---
    if oil is not None:
        if oil > 1:
            lines.append(
                f"**Oil ↑ ({oil:+.2f}%)** → 🟢 upstream producers (ONGC, Oil India — higher crude "
                f"realisations); 🔴 refiners/OMCs (BPCL/IOC/HPCL — crude is their *input* cost); "
                f"🔴 cost-push down the chain: transport & aviation (fuel), and crude-derivative "
                f"raw materials (paints — Asian Paints/Berger; tyres; plastics/packaging). Broadly "
                f"inflationary → rate-sensitive financials & consumer.")
            lines.append(
                f"**Autos split on oil** → 🔴 ICE-heavy names (Maruti, Hero, Bajaj) on fuel-cost/"
                f"demand worry; 🟢 EV plays (Tata Motors EV, M&M, Ola Electric, Ather) — costly "
                f"petrol makes EVs relatively cheaper to run, a structural nudge.")
        elif oil < -1:
            lines.append(
                f"**Oil ↓ ({oil:+.2f}%)** → 🟢 oil users: paints, OMCs, aviation, tyres, FMCG, "
                f"ICE autos (cheaper input/fuel); cools inflation → supportive for financials. "
                f"🔴 ONGC/Oil India (lower realisations); EV relative-economics edge narrows.")
        else:
            lines.append(f"**Oil flat ({oil:+.2f}%)** → neutral for energy-linked sectors.")

    # --- GOLD (jewellery + gold financiers) ---
    if gold is not None:
        if gold > 1:
            lines.append(
                f"**Gold ↑ ({gold:+.2f}%)** → 🟢 gold financiers (Muthoot, Manappuram) — higher "
                f"gold lifts loan value & AUM; jewellers (Titan, Kalyan, Senco) book inventory "
                f"gains, though very sharp spikes can dent volumes. A safe-haven bid also signals "
                f"risk-off.")
        elif gold < -1:
            lines.append(
                f"**Gold ↓ ({gold:+.2f}%)** → 🔴 gold financiers' collateral value softens; "
                f"🟢 jewellery footfalls/volumes as prices ease.")

    # --- RUPEE / USDINR (exporters) ---
    if usdinr is not None:
        if usdinr > 0.2:
            lines.append(
                f"**Rupee weaker (USDINR {usdinr:+.2f}%)** → 🟢 IT exporters (TCS, Infosys, Wipro, "
                f"HCL) & pharma exporters — every ₹ of depreciation lifts export earnings. "
                f"🔴 importers, oil marketers, and companies with $ debt.")
        elif usdinr < -0.2:
            lines.append(
                f"**Rupee stronger (USDINR {usdinr:+.2f}%)** → 🔴 headwind for IT & pharma export "
                f"earnings; 🟢 relief for importers & oil marketers.")

    # --- KOSPI / AI fear proxy ---
    if kospi is not None:
        if kospi < -1:
            it_s = f" (Nifty IT {niftyit:+.2f}% today)" if niftyit is not None else ""
            lines.append(
                f"**Kospi ↓ ({kospi:+.2f}%)** → global AI/chip jitters (Korea is the bellwether); "
                f"watch for sentiment spillover into Indian IT{it_s}.")
        elif kospi > 1:
            if ai_regime == "Substitution":
                lines.append(
                    f"**Kospi ↑ ({kospi:+.2f}%)** → chips up, but **AI-Substitution regime active** → "
                    f"capital favours AI-infra over IT services → 🔴 Indian IT *pressured*, not lifted.")
            else:
                lines.append(
                    f"**Kospi ↑ ({kospi:+.2f}%)** → AI/chip risk appetite improving; supportive for "
                    f"Indian IT (AI-Complement regime).")

    # --- RATES / DXY (FII flows, financials, G-secs, RBI toolkit) ---
    rate_bits = []
    if us10y is not None and us10y > 0.5: rate_bits.append(f"US10Y {us10y:+.2f}%")
    if dxy   is not None and dxy   > 0.3: rate_bits.append(f"DXY {dxy:+.2f}%")
    if rate_bits:
        lines.append(
            "**Rising " + " & ".join(rate_bits) + "** → transmission (not a direct hit): US yields ↑ "
            "→ **rate differential narrows** → **dollar firmer** → **FII/FPI outflow** → Indian G-sec "
            "demand ↓ / yields ↑ + weaker rupee → *then* 🔴 rate-sensitives (banks/NBFCs, realty, "
            "autos). Note: Indian G-secs don't always follow US10Y one-for-one — the link runs "
            "through the currency & capital-flow channel. If rupee weakness "
            "persists, watch for **RBI intervention** (spot dollar sales, forwards, or liquidity "
            "ops) — RBI has *historically* leaned on a large forward book, FX reserves and gold to "
            "smooth volatility, **but today's data does not confirm any intervention**. _[Actual "
            "reserve/forward figures: RBI Weekly Statistical Supplement, Fridays — not fetched here.]_")

    # --- INFLATION: two-sided (India hot = bearish; US cooling = risk-on) ---
    in_hot = india_cpi_hot(news)
    us_cool = us_cpi_cool(news)
    if in_hot:
        lines.append(
            f"**India CPI hot ({len(in_hot)} headline(s), above forecast)** → RBI stays cautious; "
            f"🔴 rate-sensitive financials, NBFCs, autos, real estate most exposed.")
    if us_cool:
        lines.append(
            f"**US CPI cooling ({len(us_cool)} headline(s))** → Fed-easing hopes → 🟢 risk-on: "
            f"semiconductors, EM equities and FII flows into India — a global tailwind that partly "
            f"offsets domestic inflation worries.")
    if not in_hot and not us_cool:
        infl = [n for n in news if "inflation" in n.get("tags", "") or "cpi" in n.get("tags", "")]
        if infl:
            lines.append(
                f"**Inflation in the headlines ({len(infl)} story/-ies)** → keeps RBI cautious; "
                f"🔴 rate-sensitive financials most exposed.")

    # --- India macro pulse (news-flag drivers: repo, PMI, monsoon, GST) ---
    if rbi_dovish(news):
        lines.append("**RBI dovish / repo-cut signal in news** → 🟢 rate-sensitives: banks, NBFCs, "
                     "autos, realty (lower funding cost / cheaper EMIs).")
    if pmi_strong(news):
        lines.append("**Strong PMI in news** → 🟢 capital goods, industrials, infra (activity momentum).")
    _mg, _mb = monsoon_read(news)
    if _mg:
        lines.append("**Good-monsoon signal** → 🟢 rural demand: FMCG, 2W (Hero/Bajaj), tractors "
                     "(M&M), agri-inputs; eases food inflation.")
    if _mb:
        lines.append("**Weak/deficit-monsoon signal** → 🔴 rural FMCG, tractors, agri; food-inflation risk.")
    if gst_strong(news):
        lines.append("**Strong GST collections in news** → 🟢 consumption momentum (durables, retail, autos).")

    # --- VIX ---
    if vix is not None and vix > 5:
        lines.append(f"**India VIX ↑ ({vix:+.2f}%)** → rising fear; expect wider swings, "
                     f"tougher for leveraged/high-beta names.")

    if not lines:
        lines.append("_Not enough price data this run to map sector impact._")
    return lines


def _last_of(quotes, name):
    for q in quotes:
        if q["name"] == name:
            return q.get("last")
    return None


def _level_of(quotes, name):
    """PRICE LEVEL with fallback, for level-dependent logic (oil bands, amplifiers).

    `last` and `pct_change` are filled by different code paths, so a quote can carry a
    valid % move while `last` is None. All level logic then went silently dormant — the
    Brent band table, the x1.4 amplifier and the level-scoped sectors vanished while the
    % move kept flowing, and the report merely said "n/a". previous_close is a fine proxy
    for WHICH $10 BAND we are in (a level is a slow variable).

    Returns (level, source) where source is 'last' | 'prev_close' | None.
    """
    for q in quotes:
        if q["name"] == name:
            v = q.get("last")
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v, "last"
            p = q.get("previous_close")
            if p is not None and not (isinstance(p, float) and math.isnan(p)):
                return p, "prev_close"
            return None, None
    return None, None


def _nifty_weight(sym):
    return NIFTY50_WEIGHTS.get((sym or "").replace(".NS", "").strip())


_NIFTY_RANKED = sorted(NIFTY50_WEIGHTS.items(), key=lambda kv: -kv[1])


def _nifty_rank(sym) -> int | None:
    s = (sym or "").replace(".NS", "").strip()
    for i, (k, _w) in enumerate(_NIFTY_RANKED, 1):
        if k == s:
            return i
    return None


def _weight_tag(sym) -> str:
    """Quantitative importance: weight %, rank, impact — instead of stars (#5)."""
    wt = _nifty_weight(sym)
    if wt is None:
        return "not in Nifty 50 · negligible index impact"
    rank = _nifty_rank(sym)
    impact, _ = _nifty_impact(sym)
    rk = f", ~rank #{rank}" if rank else ""
    return f"~{wt:.1f}% of Nifty{rk} · {impact} index impact"


def build_standout_movers(quote_lists, top=4):
    """Pool all fetched stocks, rank by % move, return (gainers, losers).
    Deduped by display name; only entries with a valid % change."""
    pool = {}
    for ql in quote_lists:
        for q in ql:
            if q.get("pct_change") is None or q.get("last") is None:
                continue
            pool[q["name"]] = q
    items = sorted(pool.values(), key=lambda q: q["pct_change"])
    losers = items[:top]
    gainers = list(reversed(items))[:top]
    return gainers, losers


def classify_oil_shock(news) -> str:
    """Classify WHY oil is moving — supply/demand/policy/inventory shocks transmit
    very differently (research: crude↔equity is conditional, not a fixed linear link)."""
    oil_news = [n for n in news if _kw_match(n.get("tags", ""), ["oil", "crude"])
                or _kw_match(n.get("title", ""), ["oil", "crude", "brent"])]
    T = " ".join(n.get("title", "") for n in oil_news).lower()
    if any(k in T for k in ["iran", "hormuz", "israel", "war", "sanction", "pipeline",
                            "strait", "supply", "attack"]):
        return ("🛢️ **Supply shock** (Iran/Hormuz/OPEC) → usually 🔴 bearish for India: import-cost "
                "up with **no growth offset**; amplifies inflation/CAD.")
    if any(k in T for k in ["opec", "production cut", "output cut"]):
        return "🛢️ **Policy shock** (OPEC cut) → price up on supply curbs; watch for demand pushback."
    if any(k in T for k in ["demand", "growth", "china stimulus", "recovery", "consumption"]):
        return "🛢️ **Demand shock** (global growth) → often 🟢 supportive for cyclicals/metals."
    if any(k in T for k in ["inventory", "stockpile", "stocks build", "draw", "eia"]):
        return "🛢️ **Inventory move** → usually temporary, weaker/short-lived transmission."
    return ""


RATE_SENSITIVE = {"hdfc bank", "icici bank", "kotak mahindra bank", "axis bank", "sbin",
                  "bajaj finance", "dlf", "godrej properties"}


def _override_analysis(short, actual, exp_sign, eng, observed, news):
    """
    Decompose a stock's move into the drivers RELEVANT TO THAT STOCK (#6 — no oil in
    a bank's stack) and find the DOMINANT one — so a stock moving against a rule is
    'overridden', not a 'broken' rule.
    """
    sl = short.lower()
    stack = [("Contradicted rule", round(exp_sign * 0.5, 1))]   # weak — it was overridden
    nifty = observed.get("nifty") or 0
    vix = observed.get("vix") or 0
    stack.append(("Market risk", 2 if (nifty > 0.1 or vix < -3)
                  else -2 if (nifty < -0.1 or vix > 3) else 0))
    fii = observed.get("fii")
    stack.append(("FII flow", -2 if (fii and fii < 0) else 1 if (fii and fii > 0) else 0))
    if sl in RATE_SENSITIVE:                                    # rates only for rate-sensitives
        u = eng["drivers"].get("us10y_pct", 0)
        stack.append(("Rates", -2 if u > 0.3 else 2 if u < -0.3 else 0))
    reason = reason_for_stock(short, news)
    if reason:
        # a stock bucking a macro rule WITH its own headline is usually news-driven,
        # so a company-specific catalyst weighs strongly
        if _kw_match(reason, POS_WORDS):
            ns = 4
        elif _kw_match(reason, NEG_WORDS):
            ns = -4
        else:
            ns = 3 if actual > 0 else -3   # present catalyst assumed to explain the move
        stack.append(("Company news", ns))
    aligned = [(n, s) for n, s in stack if s != 0 and (s > 0) == (actual > 0)]
    dominant = max(aligned, key=lambda x: abs(x[1]))[0] if aligned else "unclear / positioning"
    return stack, dominant, reason


# ONE canonical oil-band definition. Every display (exec summary, dashboard, §4
# amplifier, the level table) must use THIS, or the same $97 shows up as "inflation
# watch" in one place, "crisis zone" in another, and "$90-100 macro headwind" in a
# third — which is exactly the confusion this unifies away.
# (upper_bound, label, multiplier)
_OIL_BANDS = [
    (70,  "tailwind (cheap oil)",   0.6),
    (80,  "neutral",                1.0),
    (90,  "inflation watch",        1.4),
    (100, "macro headwind",         1.8),
    (999, "crisis (>$100)",         2.2),
]


def _oil_band_label(price) -> str:
    """Canonical band label for a Brent price. Used everywhere oil's level is shown."""
    if price is None:
        return "unknown"
    for hi, label, _ in _OIL_BANDS:
        if price < hi:
            return label
    return "crisis (>$100)"


def _oil_level_mult(price) -> float:
    """Level-dependent amplifier — same canonical bands as _oil_band_label."""
    if price is None:
        return 1.0
    for hi, _, mult in _OIL_BANDS:
        if price < hi:
            return mult
    return 2.2


def build_oil_regime(quotes_macro, news) -> list[str]:
    """
    Oil reaction is NON-LINEAR in the absolute Brent price, not just the % move.
    <$80 benign, $80-90 watch, $90-100 stress, >$100 crisis — each with escalating
    India-macro damage and intra-sector winners/losers.
    """
    price = _last_of(quotes_macro, "Brent Crude")
    pct = _pct_of(quotes_macro, "Brent Crude")
    if price is None:
        return []
    mideast = sum(1 for n in news if n.get("macro")
                  and _kw_match(n.get("title", "") + " " + n.get("tags", ""),
                                ["iran", "hormuz", "israel", "gulf", "middle east"]))
    pmove = f" ({pct:+.1f}% today)" if pct is not None else ""
    lines = []

    # ── (A) PRICE LEVEL — structural macro risk (where Brent SITS) ──────────
    bands = [
        ("<$70",    "🟢 Tailwind",      price < 70),
        ("$70–80",  "🟢 Neutral-benign", 70 <= price < 80),
        ("$80–90",  "🟡 Inflation watch", 80 <= price < 90),
        ("$90–100", "🟠 Macro headwind",  90 <= price < 100),
        (">$100",   "🔴 Crisis",         price >= 100),
    ]
    lines.append(f"**① Price LEVEL — structural (Brent ${price:.0f}):**")
    lines.append("")
    lines.append("| Brent level | Regime | |")
    lines.append("|---|---|---|")
    for rng, lab, active in bands:
        mark = " ← **now**" if active else ""
        lines.append(f"| {rng} | {lab} |{mark} |")
    lines.append("")

    # ── (B) TODAY'S MOVE — short-term trading effect (separate from level) ──
    if pct is not None:
        if abs(pct) < 0.3:
            mv = "flat — negligible sector impact today"
        elif pct > 0:
            mv = (f"↑ {pct:+.1f}% — small positive for upstream (ONGC), small negative "
                  f"for fuel users (OMCs, aviation, paints); limited macro impact on a one-day move")
        else:
            mv = (f"↓ {pct:+.1f}% — small positive for fuel users (OMCs, aviation, paints), "
                  f"small negative for upstream (ONGC); limited macro impact on a one-day move")
        lines.append(f"**② Today's MOVE — trading effect:** {mv}.")
        lines.append(f"*(Level = structural macro risk; move = short-term sector rotation. "
                     f"They are different questions and are read separately.)*")
        lines.append("")
    if price < 80:
        lines.append("**Level detail:** Limited inflation pass-through; CAD/rupee comfortable. "
                     "Oil *users* breathe easy (paints, OMCs, aviation, tyres, FMCG); upstream "
                     "(ONGC/Oil India) less exciting on realisations.")
    elif price < 90:
        lines.append("**Level detail:** Inflation & current-account pressure building. "
                     "🟢 ONGC, Oil India (realisations). 🔴 OMCs (BPCL/IOC/HPCL marketing margins), "
                     "paints (Asian Paints, Berger), aviation (IndiGo), tyres. Rupee & rate-sensitive "
                     "financials start to feel it.")
    elif price < 100:
        lines.append("**Level detail:** Serious CAD/inflation/rupee risk; RBI leans hawkish → "
                     "G-sec yields ↑. OMCs face **under-recovery** risk (govt may freeze retail "
                     "prices → marketing losses). Broad de-rating; defensive tilt (FMCG staples, "
                     "pharma). Only upstream energy & USD exporters cushioned.")
    else:
        lines.append("**Level detail:** Import-bill & fiscal shock, sharp rupee depreciation, FII "
                     "outflows. Heavy hit to OMCs, paints, aviation, autos, financials. Refuge: "
                     "ONGC/Oil India, defence, and USD earners (IT/pharma) via the weak rupee. "
                     "Watch for fuel-price caps / excise cuts.")
    if mideast and price >= 85:
        lines.append(f"⚠️ **Middle-East/Iran premium active** ({mideast} headline(s)) with Brent "
                     f"${price:.0f} — Strait of Hormuz (~20% of seaborne oil) is the tail risk; a "
                     f"disruption spikes oil + freight + tanker insurance and amplifies everything "
                     f"above. 🟢 Defence (HAL, BEL, BDL, Mazagon Dock), upstream energy; "
                     f"🔴 aviation, paints, OMCs.")
    shock = classify_oil_shock(news)
    if shock:
        lines.append(shock)
    # psychological round-number barriers
    for bar in (80, 90, 100):
        if abs(price - bar) <= 1.5:
            side = "just above" if price >= bar else "testing"
            lines.append(f"🎯 **Brent {side} the ${bar} barrier (${price:.0f})** — a round-number "
                         f"level markets watch; a decisive break tends to accelerate the move & "
                         f"sentiment.")
            break
    return lines


def reconcile_oil_proxies(quotes_macro, sector_quotes) -> str:
    """
    The oil→sector rule is ceteris-paribus. Check it against what the proxy stocks
    ACTUALLY did today, and flag when the tape contradicts the textbook (e.g. oil up
    but ONGC down / BPCL up) — usually broad-market rotation or stock-specifics
    overriding the oil channel. Prevents the report asserting a move that didn't happen.
    """
    oil = _pct_of(quotes_macro, "Brent Crude")
    if oil is None or abs(oil) < 0.3:
        return ""
    up = oil > 0
    # expected sign on an oil-UP day (flipped if oil is down)
    exp = {"ONGC (upstream/producer)": +1, "BPCL (oil marketer)": -1,
           "Asian Paints (oil user)": -1, "IndiGo (aviation/fuel)": -1}
    confirms, diverge = [], []
    for q in sector_quotes:
        e, a = exp.get(q["name"]), q.get("pct_change")
        if e is None or a is None:
            continue
        e = e if up else -e
        short = q["name"].split(" (")[0]
        if a == 0 or (a > 0) == (e > 0):
            confirms.append(f"{short} {a:+.1f}% ✓")
        else:
            diverge.append(f"{short} {a:+.1f}% ✗")
    if not confirms and not diverge:
        return ""
    note = "**Oil-channel check (rule vs actual):** " + "; ".join(confirms + diverge)
    if diverge:
        note += (". ⚠️ **Tape diverges from the textbook oil pattern today** — broad-market "
                 "direction or stock-specific/margin factors are overriding the oil channel, so "
                 "don't lean on the oil-sector read; check the stock-specific news for the reason.")
    else:
        note += ". (Tape confirms the oil-channel read.)"
    return note


# Cross-asset linkages to verify against the tape.
# (name, driver_key, threshold, [(proxy_short_name, sign_on_UP_move)])
RELATIONSHIPS = [
    ("Oil → producers vs users (dir. flips with oil)", "oil", 0.5,
        [("ONGC", +1), ("BPCL", -1), ("Asian Paints", -1), ("IndiGo", -1)]),
    ("Weak rupee → IT exporters up", "usdinr", 0.2,
        [("TCS", +1), ("Infosys", +1), ("Wipro", +1), ("HCL Tech", +1)]),
    ("Gold → financiers & jewellers up", "gold", 0.5,
        [("Muthoot", +1), ("Titan", +1), ("Kalyan Jew.", +1)]),
    ("Copper → base-metal producers up", "copper", 0.7,
        [("Tata Steel", +1)]),
    # SPLIT FROM the old "Indian IT/EMS" bundle. IT SERVICES and EMS are SIBLINGS with
    # OPPOSITE regime behaviour, not one basket: under AI-Substitution a chip rally is
    # capital rotating INTO infrastructure and OUT of services, so IT services is a
    # HEADWIND while EMS (Dixon/Kaynes/CG Power) is the BENEFICIARY. Bundling them under
    # one +1 sign — and flipping the whole bundle under the regime — pushed EMS the wrong
    # way. EMS now lives ONLY in extra_validations' "AI infrastructure (SOX) → EMS"
    # relationship (which does NOT flip), so it is no longer double-counted here.
    ("US semis (SOX) → Indian IT services", "sox", 1.5,
        [("TCS", +1), ("Infosys", +1), ("Wipro", +1), ("HCL Tech", +1)]),
    ("Kospi (AI proxy) → Indian IT", "kospi", 1.5,
        [("TCS", +1), ("Infosys", +1)]),
    ("Rising US yields → banks pressured", "us10y", 0.5,
        [("HDFC Bank", -1), ("ICICI Bank", -1)]),
    ("FII flow → financials (same direction)", "fii", 500,
        [("HDFC Bank", +1), ("ICICI Bank", +1)]),
    # --- new sectors ---
    ("Oil ↑ → ICE autos pressured (fuel/demand)", "oil", 0.7,
        [("Maruti", -1), ("Hero MotoCorp", -1), ("Bajaj Auto", -1)]),
    ("Oil ↑ → EV/CV plays (relative nudge)", "oil", 1.0,
        [("Tata Motors", +1), ("Ola Electric", +1)]),
    ("Weak rupee → pharma exporters up", "usdinr", 0.2,
        [("Sun Pharma", +1), ("Dr Reddy", +1), ("Cipla", +1)]),
    ("Geopolitics premium → defence up", "geo", 1,
        [("HAL", +1), ("Bharat Electronics", +1)]),
    ("Oil ↑ → chemicals input-cost pressure", "oil", 1.0,
        [("SRF", -1), ("Asian Paints", -1)]),
    ("Oil/diesel ↑ → cement freight cost", "oil", 1.0,
        [("UltraTech", -1)]),
]


# Linkages whose expected sign is AI-regime-dependent (SOX/Kospi → Indian IT).
AI_REGIME_LINKS = {"US semis (SOX) → Indian IT services", "Kospi (AI proxy) → Indian IT"}

# Economic-rationale score per linkage (1-5 ★): how ECONOMICALLY SOUND the relationship
# is, independent of today's hit-rate. High rationale + low hit-rate = "sound rule, noisy
# session"; low rationale + low hit-rate = "questionable rule". (#9)
# TRANSMISSION TYPE — how a driver reaches the target, so the report can explain WHY a
# link exists and distinguish a DIRECT causal chain from a mere PROXY/correlation.
#   supply_chain : direct causal chain (SOX → chip demand → EMS builds electronics)
#   spending_proxy : the driver is a *sentiment/demand proxy*, not a cause (SOX stands
#                    in for the US tech-spend cycle that actually drives Indian IT)
#   flow_currency : reaches India via FII flows / the rupee, not directly
#   incidental : statistical co-movement with NO economic transmission — both move on
#                the same global-risk factor. Kept only if history is significant, and
#                labelled so a reader never treats it as causal.
# (name -> (type, one-line transmission note))
RELATIONSHIP_TYPE = {
    "US semis (SOX) → Indian IT services":
        ("spending_proxy", "SOX is a PROXY for the US tech-spend cycle (SOX→US tech "
         "budgets→enterprise/cloud spend→Indian IT), NOT a supply chain. Second-order."),
    "AI infrastructure (SOX) → EMS":
        ("supply_chain", "DIRECT: SOX reflects chip demand; EMS (Dixon/Kaynes/CG Power) "
         "manufactures the electronics that use those chips. First-order supply chain."),
    "Kospi (AI proxy) → Indian IT":
        ("incidental", "Kospi (≈40% Samsung/SK Hynix) and Indian IT both move on global "
         "risk appetite — this is CORRELATION, not transmission. Kept only for its "
         "historical hit-rate; do not read it as causal."),
    "Kospi (AI proxy) → Indian EMS":
        ("supply_chain", "Samsung/SK Hynix dominate memory; Kospi is a real semiconductor "
         "supply-chain read for EMS/electronics — a first-order link, unlike Kospi→IT."),
    "Copper → base-metal producers up": ("supply_chain", "copper price → producer realisations"),
    "Rising US yields → banks pressured":
        ("flow_currency", "US10Y → rate differential → FII flows / rupee → Indian banks. Indirect."),
    "Oil → producers vs users (dir. flips with oil)":
        ("supply_chain", "crude price directly sets upstream realisations and OMC/user input cost"),
}


def relationship_type(name: str):
    """(type, note) for a relationship, or ('', '') if unclassified."""
    return RELATIONSHIP_TYPE.get(name, ("", ""))


ECON_RATIONALE = {
    "Oil → producers vs users (dir. flips with oil)": 5,
    "Weak rupee → IT exporters up": 5,
    "Weak rupee → pharma exporters up": 5,
    "Gold → financiers & jewellers up": 4,
    "Copper → base-metal producers up": 4,
    "FII flow → financials (same direction)": 4,
    "Oil ↑ → ICE autos pressured (fuel/demand)": 4,
    "Geopolitics premium → defence up": 4,
    "Oil ↑ → chemicals input-cost pressure": 4,
    "US semis (SOX) → Indian IT services": 3,      # PROXY, not supply chain — see RELATIONSHIP_TYPE
    "Kospi (AI proxy) → Indian IT": 1,             # incidental correlation, NOT transmission (was 3)
    "Rising US yields → banks pressured": 3,   # indirect via currency/flows
    "Oil ↑ → EV/CV plays (relative nudge)": 3,
    "Oil/diesel ↑ → cement freight cost": 3,
}


def detect_ai_regime(news, quotes_idx=None, quotes_macro=None):
    """
    DATA-DRIVEN AI regime for Indian IT (Thread 2): decided from BOTH news and the
    price tape, before any rule is evaluated. Post-2023 SOX/Kospi→Indian-IT isn't fixed:
      • Complement   — AI lifts services (cloud demand, deal wins) → SOX↑ ⇒ IT↑
      • Substitution — AI displaces services (IBM/peer warnings, budgets to infra) → SOX↑ ⇒ IT↓
    The strongest signal is the tape: global chips UP but Indian IT DOWN = substitution live.
    Returns (regime, evidence[]).
    """
    sub_kw = ["consulting slowdown", "ai replacing", "replace outsourcing", "displacement",
              "budgets to infrastructure", "ai infrastructure", "deal delay", "discretionary spend",
              "lower consulting", "services pressure", "guidance cut", "revenue miss",
              "weak guidance", "adr crash", "furlough", "spending cut", "cannibalis",
              "crash", "rattles", "worst", "under pressure", "plunge", "tumble", "spooked",
              "selloff", "warning", "slump", "downgrade"]
    comp_kw = ["cloud demand", "deal win", "genai deal", "ai transformation", "ai contract",
               "large deal", "order win", "tcv", "ai services", "bags deal", "wins deal",
               "ai-led growth", "record deal"]
    sub = [n for n in news if is_it_ai_headline(n) and _kw_match(n.get("title", ""), sub_kw)]
    sub += [n for n in it_peer_readthrough(news) if n not in sub]     # peer warnings = substitution
    comp = [n for n in news if is_it_ai_headline(n) and _kw_match(n.get("title", ""), comp_kw)]
    sub_n, comp_n = len(sub), len(comp)

    if quotes_idx is not None and quotes_macro is not None:
        it = _pct_of(quotes_idx, "Nifty IT")
        sox = _pct_of(quotes_macro, "Phila Semi (SOX)")
        kospi = _pct_of(quotes_macro, "Kospi")
        chips_up = (sox is not None and sox > 1.5) or (kospi is not None and kospi > 1)
        chips_dn = (sox is not None and sox < -1.5) or (kospi is not None and kospi < -1)
        if it is not None and chips_up and it < -0.3:
            sub_n += 2      # chips up but IT down -> substitution, strongly
        elif it is not None and chips_up and it > 0.3:
            comp_n += 1
        elif it is not None and chips_dn and it < -0.3:
            comp_n += 1     # IT following chips down -> normal correlation

    # #7 confidence: a regime should be corroborated by MULTIPLE signals (IBM/peer +
    # Indian IT names), not one headline or price alone. news_sub/comp = headline counts.
    news_sub, news_comp = len(sub), len(comp)
    if sub_n > comp_n:
        conf = "confirmed" if news_sub >= 2 else "tentative"
        return "Substitution", sub[:3], conf
    if comp_n > sub_n:
        conf = "confirmed" if news_comp >= 2 else "tentative"
        return "Complement", comp[:3], conf
    return "Neutral", [], "confirmed"


def _driver_strength(dkey: str, v: float) -> str:
    a = abs(v)
    if dkey == "fii":
        return "Strong" if a >= 3000 else "Medium" if a >= 1000 else "Weak"
    if dkey == "geo":
        return "Strong" if a >= 3 else "Medium" if a >= 1.5 else "Weak"
    return "Strong" if a >= 2 else "Medium" if a >= 0.7 else "Weak"


def _reliab_band(hr: float):
    """Honest bands (50% = coin-flip): <55 Weak, 55-60 Limited, 60-70 Moderate, >70 Strong."""
    if hr > 70: return "🟢", "Strong"
    if hr >= 60: return "🟡", "Moderate"
    if hr >= 55: return "🟠", "Limited"
    return "🔴", "Weak"


def _reliability_str(hr: float, n: int) -> str:
    """Hit-rate with a 95% confidence band (#5), so the reader sees significance."""
    p = hr / 100.0
    ci = 1.96 * math.sqrt(max(p * (1 - p), 0) / n) * 100 if n else 0
    dot, band = _reliab_band(hr)
    return f"{dot} {hr:.0f}% ±{ci:.0f}% ({band}, n={n})"


def build_cause_effect_scorecard(eng, quotes_macro, stock_lists, ai_regime="Neutral") -> list[dict]:
    """
    Verify each cross-asset rule against the tape, WEIGHTED by index importance and
    AI-REGIME-AWARE (SOX/Kospi→IT flip under Substitution). Returns rich rows.
    """
    px, wt = {}, {}
    for ql in stock_lists:
        for q in ql:
            if q.get("pct_change") is not None:
                short = q["name"].split(" (")[0].strip().lower()
                px[short] = q["pct_change"]
                wt[short] = _nifty_weight(q.get("symbol")) or 0.3   # small default weight

    dv = {
        "oil": eng["drivers"].get("oil_pct"), "usdinr": eng["raw"].get("usdinr"),
        "kospi": eng["drivers"].get("kospi_pct"), "sox": eng["drivers"].get("sox_pct"),
        "us10y": eng["drivers"].get("us10y_pct"), "gold": _pct_of(quotes_macro, "Gold"),
        "copper": _pct_of(quotes_macro, "Copper"), "fii": eng["raw"].get("fii"),
        "geo": eng["drivers"].get("geopolitics_hits"),
    }
    rows = []
    for name, dkey, thr, proxies in RELATIONSHIPS:
        v = dv.get(dkey)
        if v is None or abs(v) < thr:
            continue
        sgn = 1 if v > 0 else -1
        flip = name in AI_REGIME_LINKS and ai_regime == "Substitution"
        checks, wsum, wok = [], 0.0, 0.0
        for short, base in proxies:
            a = px.get(short.lower())
            if a is None:
                continue
            b = -base if flip else base
            exp = b * sgn
            ok = (a == 0) or ((a > 0) == (exp > 0))
            w = wt.get(short.lower(), 0.3)
            checks.append((short, a, ok, exp, w))
            wsum += w; wok += w * ok
        if not checks:
            continue
        exps = {1 if e > 0 else -1 for _, _, _, e, _ in checks}
        exp_dir = ("↑" if exps == {1} else "↓" if exps == {-1} else "mixed")
        regime = ("AI-Substitution" if flip else
                  "AI-Complement" if name in AI_REGIME_LINKS else "")
        unit = "cr" if dkey == "fii" else "%"
        rows.append({
            "name": name, "dkey": dkey, "driver_val": v,
            "driver": f"{dkey} {v:+.0f}{unit}" if dkey == "fii" else f"{dkey} {v:+.1f}{unit}",
            "strength": _driver_strength(dkey, v), "expected": exp_dir, "regime": regime,
            "checks": checks, "wagree": round(wok / wsum * 100, 0) if wsum else 0,
            "c": sum(1 for c in checks if c[2]), "d": sum(1 for c in checks if not c[2]),
        })
    return rows


def reason_for_stock(short: str, news) -> str:
    """Find a headline that mentions this stock, to explain why it bucked the rule."""
    sl = short.lower()
    for n in news:
        if sl in n.get("title", "").lower():
            return n["title"]
    return ""


def build_metals_reaction(quotes_macro, news) -> list[str]:
    """Gold/Silver (safe-haven + hedge) vs Copper (growth barometer) — they diverge."""
    g = _pct_of(quotes_macro, "Gold")
    s = _pct_of(quotes_macro, "Silver")
    c = _pct_of(quotes_macro, "Copper")
    lines = []
    if g is not None or s is not None:
        gs = f"Gold {g:+.1f}%" if g is not None else "Gold n/a"
        ss = f"Silver {s:+.1f}%" if s is not None else "Silver n/a"
        gv = g or 0
        if gv > 0.5:
            dyn = ("Today's up-move looks like a **safe-haven / inflation-hedge bid** → "
                   "🟢 gold financiers (Muthoot, Manappuram — higher loan value/AUM), jewellers' "
                   "inventory gains (Titan, Kalyan), gold/silver miners.")
        elif gv < -0.5:
            dyn = ("Today's move is **down — NOT a safe-haven bid** (risk-on or firmer dollar/real "
                   "yields). 🔴 softer collateral for gold financiers; 🟢 mild relief for jewellery "
                   "volumes as prices ease. If geopolitics escalates, gold can resume its hedge role.")
        else:
            dyn = "Broadly flat — no clear haven signal today."
        lines.append(f"**{gs} / {ss}** — gold/silver are safe-haven + inflation hedges (and silver "
                     f"is *also* industrial: solar/EV). {dyn}")
    if c is not None:
        if c > 0.5:
            lines.append(f"**Copper {c:+.1f}%** — 'Dr Copper' up = global growth / China-demand "
                         f"optimism. In India, copper feeds **power & transmission, EV, capital "
                         f"goods, electricals, renewables** → 🟢 producers (Hindalco, Hind Copper, "
                         f"Vedanta), cables/wires (Polycab, KEI), electricals (ABB, Siemens, CG "
                         f"Power). (War doesn't directly help copper unless supply is disrupted.)")
        elif c < -0.5:
            lines.append(f"**Copper {c:+.1f}%** — down = growth / recession worry (esp. China). "
                         f"🔴 base-metal producers (Hindalco, Vedanta, Hind Copper), cables, "
                         f"infra-linked cyclicals.")
    if g is not None and c is not None and g > 0.3 and c < -0.3:
        lines.append("🔎 **Gold ↑ + Copper ↓ = classic risk-off / growth-fear combo** — safe-haven "
                     "bid alongside industrial-demand worry. Favours defensives over cyclicals.")
    return lines


def source_weight(n: dict) -> float:
    """News-quality weight for a headline: source reputation minus an opinion/tip penalty."""
    w = SOURCE_WEIGHTS.get(n.get("source", ""), DEFAULT_SOURCE_WEIGHT)
    if _kw_match(n.get("title", ""), OPINION_HINTS):
        w *= 0.5   # opinion/tip pieces carry less signal
    return round(w, 2)


def load_external_signals() -> dict:
    """
    Optional integration hook for your NiftyOptions quant stack. If a
    `signals.json` sits next to this script, its scores are folded into the
    scoreboard. Expected shape:
      {"Option chain": {"lean": "Bullish", "score": 0.28},
       "Momentum":     {"lean": "Bearish", "score": -0.41},
       "VRP":          {"lean": "...",     "score": ...}}
    (score in -1..+1). Missing file -> no external signals, shell still works.
    """
    p = Path(__file__).resolve().parent / "signals.json"
    if not p.exists():
        return {}
    try:
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items()
                if isinstance(v, dict) and "score" in v}
    except Exception:
        return {}


def _lean(score: float) -> str:
    if score <= -0.35: return "🔴 Bearish"
    if score <= -0.10: return "🟠 Mild bear"
    if score < 0.10:   return "🟡 Neutral"
    if score < 0.35:   return "🟢 Mild bull"
    return "🟢 Bullish"


def build_signal_scoreboard(eng: dict, comp_news: list, external: dict) -> dict:
    """
    Combine what we can compute now (Macro from the causal engine, News from
    weighted company sentiment) with any external quant signals (option chain,
    momentum, VRP...) supplied via signals.json. Combined = weighted average.
    """
    rows = []
    # Macro score: scale the Nifty expected move to -1..+1
    macro = max(-1.0, min(1.0, round(eng["indices"].get("Nifty 50", {}).get("total", 0.0) / 1.2, 2)))
    rows.append({"signal": "Macro", "score": macro, "weight": 0.45, "live": True})

    # News score = Importance(source) × Sentiment × Index-weight, averaged.
    # A negative HUL story (Medium index weight, reputable source) counts far more
    # than a negative smallcap tip. Index-weight tier -> factor:
    tier_factor = {"High": 1.0, "Medium": 0.7, "Low": 0.45, "Negligible": 0.2}
    num = den = 0.0
    for c in comp_news:
        imp = c.get("src_weight", 0.5)                       # source importance (0-1)
        iw = tier_factor.get(c.get("nifty_impact", "Negligible"), 0.2)
        s = 1 if c["sentiment"] == "pos" else -1 if c["sentiment"] == "neg" else 0
        w = imp * iw
        num += w * s; den += w
    news = round(num / den, 2) if den else 0.0
    rows.append({"signal": "News", "score": news, "weight": 0.25, "live": True})

    # External quant signals (optional)
    for name, v in external.items():
        try:
            rows.append({"signal": name, "score": max(-1.0, min(1.0, float(v["score"]))),
                         "weight": 0.30 / max(1, len(external)), "live": True,
                         "note": v.get("lean", "")})
        except Exception:
            continue

    tw = sum(r["weight"] for r in rows) or 1.0
    combined = round(sum(r["score"] * r["weight"] for r in rows) / tw, 2)
    return {"rows": rows, "combined": combined,
            "agreement": eng.get("agreement"), "conviction": eng.get("conviction")}


def build_horizon_view(eng: dict, news, earnings) -> list[tuple]:
    """
    Separate today's signals by TIME HORIZON so an intraday catalyst (oil spike)
    isn't conflated with a structural story (AI capex). Directional lean is
    modeled only for the intraday same-day drivers; longer horizons list what to
    watch (we don't have horizon-specific predictive models — honest by design).
    """
    d, raw = eng["drivers"], eng["raw"]
    cats = build_catalysts(earnings)
    themes = detect_themes(news)
    rows = []

    # 30-min microstructure — needs the quant feed
    rows.append(("⏱️ Next 30 min (micro)", "option chain, VWAP, OI, gamma, skew",
                 "→ feed via signals.json"))

    # Intraday — VIX, flows, news; lean = same-day engine read
    intr = []
    if raw.get("fii") is not None: intr.append(f"FII ₹{raw['fii']:+,.0f}cr")
    if d.get("vix_pct"):           intr.append(f"VIX {d['vix_pct']:+.1f}%")
    intr.append("news flow")
    rows.append(("📆 Intraday (today)", ", ".join(intr), eng["sentiment"]))

    # 1-3 days — oil, results, RBI/rupee
    days = []
    if abs(d.get("oil_pct", 0)) >= 1: days.append(f"oil {d['oil_pct']:+.1f}%")
    if cats["tomorrow"]:              days.append(f"{len(cats['tomorrow'])} results tomorrow")
    days.append("RBI / rupee")
    rows.append(("🗓️ 1–3 days", ", ".join(days), "watch"))

    # 1-4 weeks — inflation, Fed/US rates, DXY
    wk = ["inflation trajectory"]
    if d.get("us10y_pct"): wk.append("US rates / Fed")
    if d.get("dxy_pct"):   wk.append("DXY / rupee")
    rows.append(("📅 1–4 weeks", ", ".join(wk), "watch"))

    # 3-12 months — structural themes
    mo = ["AI / semiconductor cycle"] + [t["name"] for t in themes[:2]]
    mo.append("govt policy (PLI / capex)")
    rows.append(("📈 3–12 months (structural)", ", ".join(mo), "structural"))
    return rows


# Group drivers into stable THEMES (traders reason in themes, not single variables).
DRIVER_THEME = {
    "kospi_pct": "AI / global tech", "sox_pct": "AI / global tech",
    "oil_pct": "Oil / inflation", "india_cpi_hot": "Oil / inflation",
    "us_cpi_cool": "Global disinflation",
    "us10y_pct": "Rates / dollar", "dxy_pct": "Rates / dollar",
    "fii_kcr": "Flows", "geopolitics_hits": "Geopolitics", "vix_pct": "Risk appetite",
    "interaction": "Interaction",
}


def _interactions(drivers, news):
    """Driver INTERACTIONS (#12): two drivers pushing the same economic way reinforce
    each other — the combined signal is stronger than the sum. Returns (adj, notes)."""
    adj, notes = 0.0, []
    oil = drivers.get("oil_pct", 0); cpi = drivers.get("india_cpi_hot", 0)
    us10y = drivers.get("us10y_pct", 0); dxy = drivers.get("dxy_pct", 0)
    fii = drivers.get("fii_kcr", 0)
    if oil > 1 and cpi:
        adj -= 0.05
        notes.append("🔗 **Oil↑ + India-CPI-hot reinforcing** → inflation / RBI-hawkish signal "
                     "amplified (extra bearish for rate-sensitives).")
    if us10y > 0.5 and dxy > 0.3:
        adj -= 0.04
        notes.append("🔗 **US yields↑ + dollar↑** → EM/FII headwind amplified.")
    if fii < -0.5 and dxy > 0.3:
        adj -= 0.03
        notes.append("🔗 **FII selling + strong dollar** → foreign-exit pressure reinforced.")
    if drivers.get("us_cpi_cool") and drivers.get("kospi_pct", 0) > 1:
        adj += 0.03
        notes.append("🔗 **US-CPI-cool + chip risk-on** → global risk-appetite tailwind reinforced.")
    return round(adj, 3), notes


def build_causal_engine(quotes_idx, quotes_macro, flows, news, ai_regime="Neutral") -> dict:
    """
    Sentiment-driven cause->effect engine. Fires explicit causal chains, scores
    a sentiment label, and estimates an expected % move for Nifty & Bank Nifty
    from the SENSITIVITY coefficients. Transparent & tunable — intuition, NOT a
    forecast or trade signal.
    """
    raw = {
        "oil":    _pct_of(quotes_macro, "Brent Crude"),
        "vix":    _pct_of(quotes_idx, "India VIX"),
        "us10y":  _pct_of(quotes_macro, "US 10Y Yield"),
        "dxy":    _pct_of(quotes_macro, "Dollar Index"),
        "kospi":  _pct_of(quotes_macro, "Kospi"),
        "sox":    _pct_of(quotes_macro, "Phila Semi (SOX)"),
        "usdinr": _pct_of(quotes_macro, "USD/INR"),
        "fii":    _net_flow(flows, "FII"),
        "dii":    _net_flow(flows, "DII"),
    }
    # Weight geopolitics by relevance to India: Middle-East/Iran is oil-dominant
    # (high impact); Russia-Ukraine is currently muted for India (low weight).
    mideast = sum(1 for n in news if n.get("macro")
                  and _kw_match(n.get("title", "") + " " + n.get("tags", ""),
                                ["iran", "hormuz", "israel", "gulf", "middle east"]))
    ruua = sum(1 for n in news if n.get("macro")
               and _kw_match(n.get("title", "") + " " + n.get("tags", ""),
                             ["russia", "ukraine"]))
    war_generic = sum(1 for n in news if n.get("macro")
                      and _kw_match(n.get("tags", ""), ["war"]))
    geo = round(mideast * 1.0 + war_generic * 0.5 + ruua * 0.25, 2)  # RU-UA downweighted

    def _num(v):  # None or NaN -> 0.0 (NaN is truthy, so 'or 0.0' isn't enough)
        return 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

    drivers = {
        "oil_pct":          _num(raw["oil"]),
        "vix_pct":          _num(raw["vix"]),
        "us10y_pct":        _num(raw["us10y"]),
        "dxy_pct":          _num(raw["dxy"]),
        "kospi_pct":        _num(raw["kospi"]),
        "sox_pct":          _num(raw["sox"]),
        "fii_kcr":          _num(raw["fii"]) / 1000.0,
        "geopolitics_hits": geo,
        "india_cpi_hot":    1 if india_cpi_hot(news) else 0,   # binary flags
        "us_cpi_cool":      1 if us_cpi_cool(news) else 0,
    }

    # Oil is NON-LINEAR in its LEVEL, not just its % move: a +5% at $92 (CAD/inflation
    # stress, near the $90 barrier) bites more than +5% at $70. Scale the oil effect.
    brent_price, brent_src = _level_of(quotes_macro, "Brent Crude")
    oil_mult = _oil_level_mult(brent_price)

    # DATA-OUTAGE DETECTION. _num() maps None -> 0.0, which makes a FAILED FETCH
    # indistinguishable from "the driver did not move". A missing Brent quote then
    # reads as a calm 0.0% oil day and that lie propagates into every downstream
    # score. Record which CORE drivers had no data so the report can say so.
    _CORE = {"oil": "Brent", "vix": "India VIX", "us10y": "US 10Y", "dxy": "Dollar Index",
             "usdinr": "USD/INR"}
    missing_drivers = [lbl for k, lbl in _CORE.items()
                       if raw.get(k) is None
                       or (isinstance(raw.get(k), float) and math.isnan(raw.get(k)))]

    out = {"raw": raw, "drivers": drivers, "geo": geo, "indices": {}, "capped": [],
           "brent_price": brent_price, "oil_mult": oil_mult, "brent_src": brent_src,
           "missing_drivers": missing_drivers}
    for idx, coef in SENSITIVITY.items():
        contrib, detail = {}, {}
        for k, c in coef.items():
            mv = drivers.get(k, 0.0)
            cap = DRIVER_CAPS.get(k)
            is_capped = cap is not None and abs(mv) > cap
            mv_used = (cap * (1 if mv > 0 else -1)) if is_capped else mv   # #8 outlier cap
            lvl = oil_mult if k == "oil_pct" else 1.0                       # level amplifier
            contrib[k] = round(mv_used * lvl * c, 3)
            detail[k] = {"move": round(mv, 2), "used": round(mv_used, 2), "coef": c,
                         "contrib": contrib[k], "capped": is_capped, "level_mult": lvl}
            if is_capped and k not in out["capped"]:
                out["capped"].append(k)
        # #1 weak-transmission dominance cap: Kospi/SOX can't explain > MAX_WEAK_DOMINANCE
        # of the FINAL total. Solve: cap/(cap+others) = MAX → cap = MAX/(1-MAX) × others.
        for wk in WEAK_TRANSMISSION:
            if wk in contrib and contrib[wk] != 0:
                others = sum(abs(v) for kk, v in contrib.items() if kk != wk)
                cap_val = MAX_WEAK_DOMINANCE / (1 - MAX_WEAK_DOMINANCE) * others
                if abs(contrib[wk]) > cap_val:
                    contrib[wk] = round(cap_val * (1 if contrib[wk] > 0 else -1), 3)
                    detail[wk]["contrib"] = contrib[wk]
                    detail[wk]["dom_capped"] = True
        total = round(sum(contrib.values()), 2)
        band = round(0.20 + 0.05 * min(abs(drivers["vix_pct"]), 20), 2)
        out["indices"][idx] = {"contrib": contrib, "detail": detail, "total": total,
                               "lo": round(total - band, 2), "hi": round(total + band, 2)}

    # #12 interaction effects — add as an explicit, transparent term on Nifty
    inter_adj, inter_notes = _interactions(drivers, news)
    out["interactions"] = inter_notes
    if inter_adj and "Nifty 50" in out["indices"]:
        _ni = out["indices"]["Nifty 50"]
        _ni["contrib"]["interaction"] = inter_adj
        _ni["detail"]["interaction"] = {"move": 0, "used": 0, "coef": 0, "contrib": inter_adj,
                                        "capped": False, "level_mult": 1.0}
        _ni["total"] = round(_ni["total"] + inter_adj, 2)

    nt = out["indices"].get("Nifty 50", {}).get("total", 0.0)
    out["sentiment"] = ("🔴 Bearish" if nt <= -0.35 else
                        "🟠 Mildly bearish" if nt <= -0.10 else
                        "🟡 Neutral" if nt < 0.10 else
                        "🟢 Mildly bullish" if nt < 0.35 else "🟢 Bullish")

    # Conviction = how ALIGNED the drivers are (signal agreement), NOT p(outcome).
    nic = out["indices"].get("Nifty 50", {}).get("contrib", {})
    absum = sum(abs(v) for v in nic.values()) or 1.0
    same = sum(abs(v) for v in nic.values() if (v < 0) == (nt < 0) and v != 0)
    agreement = round(same / absum, 2)
    out["agreement"] = agreement
    out["n_bull"] = sum(1 for v in nic.values() if v > 0)     # #3 conviction breakdown
    out["n_bear"] = sum(1 for v in nic.values() if v < 0)
    out["sum_bull"] = round(sum(v for v in nic.values() if v > 0), 2)
    out["sum_bear"] = round(sum(v for v in nic.values() if v < 0), 2)
    out["conviction"] = ("High" if agreement >= 0.80 else
                         "Moderate" if agreement >= 0.65 else "Low")
    against = sorted((k for k, v in nic.items() if v != 0 and (v < 0) != (nt < 0)),
                     key=lambda k: -abs(nic[k]))
    out["dissenters"] = against

    # explicit cause -> effect chains (each tagged with the driver it maps to)
    d, chains = drivers, []
    if d["oil_pct"] > 1:
        chains.append((f"🛢️ Oil {d['oil_pct']:+.1f}% → import bill & inflation ↑ → RBI stays "
                       f"hawkish → bond yields ↑ → bank treasury MTM pressure → Bank Nifty ↓",
                       "oil_pct"))
        chains.append((f"🏗️ …**corporate-credit branch**: same inflation → G-sec/corporate-bond "
                       f"yields ↑ → **borrowing cost ↑** → capex plans deferred → 🔴 capital goods, "
                       f"engineering (L&T, ABB, Siemens), infra & real estate developers (rate-"
                       f"sensitive funding); a *funding-cost* channel distinct from demand",
                       "oil_pct"))
    elif d["oil_pct"] < -1:
        chains.append((f"🛢️ Oil {d['oil_pct']:+.1f}% → inflation eases → RBI room to cut → yields ↓ "
                       f"→ supportive for banks & rate-sensitives", "oil_pct"))
        chains.append((f"🏗️ …**corporate-credit branch**: softer yields → **cheaper funding** → "
                       f"capex revival → 🟢 capital goods, engineering (L&T, ABB, Siemens), infra "
                       f"& developers", "oil_pct"))
    if raw["fii"] is not None and raw["fii"] < 0:
        cush = f" ; DII ₹{raw['dii']:+,.0f}cr cushions" if raw["dii"] else ""
        chains.append((f"💸 FII net ₹{raw['fii']:+,.0f}cr → **large-caps with high foreign ownership** "
                       f"(banks, IT, Reliance) sold first → Bank Nifty & Nifty ↓{cush}", "fii_kcr"))
    elif raw["fii"] is not None and raw["fii"] > 0:
        chains.append((f"💸 FII net ₹{raw['fii']:+,.0f}cr → buying into high-foreign-ownership "
                       f"large-caps (banks, IT, Reliance) → Nifty ↑", "fii_kcr"))
    if d["vix_pct"] > 3:
        chains.append((f"😨 India VIX {d['vix_pct']:+.1f}% → risk-off → high-beta / leveraged names "
                       f"under pressure", "vix_pct"))
    if mideast:
        chains.append((f"🛡️ {mideast} Middle-East/Iran headline(s) → transmission: **1° Oil** ↑ "
                       f"(+ shipping/tanker-insurance) → **2° Inflation** → **3° RBI/rates** → banks & "
                       f"consumer; **defence** (HAL/BEL) is one branch, not the whole story",
                       "geopolitics_hits"))
        chains.append((f"🚢 …parallel **freight branch**: Hormuz/Red-Sea risk → container & tanker "
                       f"rates ↑ → import landed-cost ↑ → **input-cost squeeze** for import-reliant "
                       f"🔴 chemicals, auto-components, electronics/EMS (imported parts), pharma APIs "
                       f"— a cost channel *separate* from the crude-price move",
                       "geopolitics_hits"))
    if ruua:
        chains.append((f"🇷🇺 {ruua} Russia-Ukraine headline(s) → currently *muted* for India "
                       f"(downweighted); watch wheat/fertiliser/energy only", None))
    _sub = ai_regime == "Substitution"
    if d["kospi_pct"] < -1:
        chains.append((f"🇰🇷 Kospi {d['kospi_pct']:+.1f}% → global AI/chip fear → Indian IT "
                       f"sentiment drag", "kospi_pct"))
    elif d["kospi_pct"] > 1:
        if _sub:
            chains.append((f"🇰🇷 Kospi {d['kospi_pct']:+.1f}% → chips up, but **AI-Substitution "
                           f"regime active** → capital to AI infra, not services → Indian IT "
                           f"*pressured* (chips↑ ≠ services↑)", "kospi_pct"))
        else:
            chains.append((f"🇰🇷 Kospi {d['kospi_pct']:+.1f}% → global chip risk-on → supportive for "
                           f"Indian IT (AI-Complement regime)", "kospi_pct"))
    if d["sox_pct"] < -1.5:
        chains.append((f"🖥️ SOX (US semis) {d['sox_pct']:+.1f}% → AI/chip cycle wobble → pressure "
                       f"on Indian IT & electronics", "sox_pct"))
    elif d["sox_pct"] > 1.5:
        if _sub:
            chains.append((f"🖥️ SOX {d['sox_pct']:+.1f}% → **AI-infra capex ↑** → enterprise budget "
                           f"reallocated to chips/data-centres → **consulting/outsourcing ↓** "
                           f"(Substitution) → Indian IT services pressured", "sox_pct"))
        else:
            chains.append((f"🖥️ SOX {d['sox_pct']:+.1f}% → **AI-infra capex ↑** → enterprise AI "
                           f"adoption → **AI deal wins** (Complement) → Indian IT services tailwind (EMS handled separately)",
                           "sox_pct"))
    if raw["usdinr"] is not None and raw["usdinr"] > 0.2:
        chains.append((f"💱 Rupee weaker (USDINR {raw['usdinr']:+.1f}%) → IT & pharma export "
                       f"earnings ↑ (partial offset to the downside)", None))
    if d["india_cpi_hot"]:
        chains.append(("🇮🇳 India CPI hot (above forecast) → RBI stays hawkish → 🔴 rate-sensitive "
                       "financials, NBFCs, autos, realty", "india_cpi_hot"))
    if d["us_cpi_cool"]:
        chains.append(("🇺🇸 US CPI cooling → Fed-easing hopes → risk-on: 🟢 semis, EM equities & "
                       "FII flows (a global tailwind that partly offsets India's headwinds)",
                       "us_cpi_cool"))
    out["chains"] = chains
    return out


def _fmt_crore(v) -> str:
    """INR market cap -> readable ₹ crore / lakh crore."""
    if not v:
        return "—"
    cr = v / 1e7  # rupees -> crore
    if cr >= 1e5:
        return f"₹{cr/1e5:.2f}L Cr"
    return f"₹{cr:,.0f} Cr"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{v*100:+.1f}%"
    except Exception:
        return "—"


def enrich_earnings(earnings: list[dict]) -> list[dict]:
    """
    For companies reporting *Financial Results*, add a fundamentals snapshot
    (market cap, P/E, YoY revenue growth, YoY earnings growth) via yfinance.
    Non-results events pass through untouched. Fails soft per company.
    """
    if not HAVE_YF or MAX_EARNINGS_ENRICH <= 0:
        return earnings

    # Enrich results-reporters; process Nifty 50 names first so heavyweights
    # always get a fundamentals snapshot even if the calendar is long.
    candidates = [e for e in earnings
                  if "financial results" in (e.get("purpose") or "").lower()
                  and (e.get("symbol") or "").strip()]
    candidates.sort(key=lambda e: 0 if e["symbol"].strip() in NIFTY50 else 1)

    # pick the (deduped) reporters to enrich, heavyweights first, capped at MAX_EARNINGS_ENRICH
    seen, picked = set(), []
    for e in candidates:
        sym = e["symbol"].strip()
        e["nifty50"] = sym in NIFTY50
        if sym in seen:
            continue
        seen.add(sym)
        picked.append(e)
        if len(picked) >= MAX_EARNINGS_ENRICH:
            break

    def _enrich_single(e):
        """Query fundamentals for one reporter (3s bound); patch metrics onto the event in place."""
        sym = e["symbol"].strip()
        try:
            info = call_with_timeout(lambda: yf.Ticker(f"{sym}.NS").info, timeout=3.0)
            e["mktcap"] = _fmt_crore(info.get("marketCap"))
            pe = info.get("trailingPE")
            e["pe"] = f"{pe:.1f}" if isinstance(pe, (int, float)) else "—"
            e["rev_yoy"] = _fmt_pct(info.get("revenueGrowth"))
            e["profit_yoy"] = _fmt_pct(info.get("earningsGrowth")
                                       or info.get("earningsQuarterlyGrowth"))
        except Exception:
            e["mktcap"] = e["pe"] = e["rev_yoy"] = e["profit_yoy"] = "—"
        return e

    # .info is the heaviest / most-throttled yfinance call → keep the pool small
    if picked:
        workers = max(1, min(6, len(picked)))
        with _futures.ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_enrich_single, picked))
    return earnings


# =========================================================================
# OPTIONAL: Playwright fetcher for JS-heavy sites that block plain HTTP
# =========================================================================

def fetch_with_playwright(url: str) -> str:
    """
    Render a JS-heavy page and return its text. Only called if USE_PLAYWRIGHT.
    Requires: pip install playwright && playwright install chromium
    """
    from playwright.sync_api import sync_playwright  # local import so it's optional
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"))
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        text = page.inner_text("body")
        browser.close()
        return text


# =========================================================================
# REPORT
# =========================================================================

def _pct_of(quotes: list[dict], name: str):
    for q in quotes:
        if q["name"] == name:
            return q.get("pct_change")
    return None


def _net_flow(flows, category_contains):
    """Pull a net FII or DII number (₹ cr) out of the NSE flows payload."""
    for f in flows or []:
        if category_contains.lower() in (f.get("category", "").lower()):
            try:
                return float(str(f.get("net", "")).replace(",", ""))
            except Exception:
                return None
    return None


def market_regime(quotes_idx, quotes_macro, flows=None):
    """OBSERVED market state (what already happened). Returns (tone, observed dict).
    This is a fact about the tape — a distinct signal from the forward model."""
    o = {"nifty": _pct_of(quotes_idx, "Nifty 50"),
         "bank": _pct_of(quotes_idx, "Bank Nifty"),
         "vix": _pct_of(quotes_idx, "India VIX"),
         "oil": _pct_of(quotes_macro, "Brent Crude"),
         "dxy": _pct_of(quotes_macro, "Dollar Index"),
         "fii": _net_flow(flows, "FII"),
         "dii": _net_flow(flows, "DII")}
    score = 0
    if o["nifty"] is not None: score += 1 if o["nifty"] > 0 else -1
    if o["vix"]   is not None: score += -1 if o["vix"] > 3 else (1 if o["vix"] < -3 else 0)
    if o["oil"]   is not None: score += -1 if o["oil"] > 1.5 else 0
    if o["bank"]  is not None: score += 1 if o["bank"] > 0 else -1
    if o["dxy"]   is not None: score += -1 if o["dxy"] > 0.3 else 0
    if o["fii"]   is not None: score += 1 if o["fii"] > 0 else -1
    tone = "🟢 Risk-on" if score >= 2 else "🔴 Risk-off" if score <= -2 else "🟡 Mixed"
    return tone, o


def build_verdict(quotes_idx, quotes_macro, news, flows=None) -> str:
    """One-line OBSERVED read of the tape (used for the LLM context + banner)."""
    tone, o = market_regime(quotes_idx, quotes_macro, flows)
    nifty, bank, vix, brent = o["nifty"], o["bank"], o["vix"], o["oil"]
    dxy, fii, dii = o["dxy"], o["fii"], o["dii"]

    bits = []
    if nifty is not None: bits.append(f"Nifty {nifty:+.2f}%")
    if bank  is not None: bits.append(f"banks {bank:+.2f}%")
    if vix   is not None: bits.append(f"VIX {vix:+.2f}%")
    if brent is not None: bits.append(f"oil {brent:+.2f}%")
    if dxy   is not None: bits.append(f"DXY {dxy:+.2f}%")
    if fii   is not None: bits.append(f"FII ₹{fii:+,.0f}cr")
    if dii   is not None: bits.append(f"DII ₹{dii:+,.0f}cr")

    # thematic flags from headlines
    macro_news = [n for n in news if n.get("macro")]
    ai_hits  = sum(1 for n in macro_news if "ai" in n.get("tags", ""))
    war_hits = sum(1 for n in macro_news if any(t in n.get("tags", "")
                   for t in ("war", "iran", "hormuz")))
    it_hits  = sum(1 for n in news if is_it_ai_headline(n))
    themes = []
    if war_hits: themes.append(f"{war_hits} geopolitics")
    if ai_hits:  themes.append(f"{ai_hits} AI")
    if it_hits:  themes.append(f"{it_hits} Indian-IT")
    theme_s = ("; " + ", ".join(themes) + " headline(s)") if themes else ""

    return f"**Verdict — current tape: {tone}.** " + ", ".join(bits) + theme_s + "."


def llm_narrative(news, verdict: str = "", sector_lines=None, movers: str = "",
                  proxies: str = "") -> str | None:
    """
    Optional LOCAL Ollama summary — now grounded in the computed verdict + sector
    reads and INDIA-relevant headlines (not raw global feed noise), so a small
    model produces a focused India read. Returns None if disabled/unreachable.
    """
    if not USE_LOCAL_LLM:
        return None

    # Prefer India-relevant, market-moving headlines; fall back gracefully.
    india = [n for n in news if n.get("macro") and is_india_relevant(n)]
    if len(india) < 5:
        india += [n for n in news if n.get("macro") and not is_foreign_desk(n)]
    # de-dupe, keep order
    seen, picked = set(), []
    for n in india:
        if n["title"] not in seen:
            seen.add(n["title"]); picked.append(n)
    picked = picked[:12]
    # a little global context, clearly labelled
    globals_ = [n for n in news if n.get("macro") and is_foreign_desk(n)][:5]

    if not picked and not globals_:
        return None

    ind_s = "\n".join(f"- {n['title']}" for n in picked) or "- (few India headlines this run)"
    glo_s = "\n".join(f"- {n['title']}" for n in globals_)
    sec_s = "\n".join(f"- {l}" for l in (sector_lines or [])[:6])

    style = ""
    if HAVE_FEWSHOT:
        style = ("Here is an example of the STYLE ONLY (a different day — its numbers and company "
                 "names are NOT today's; never copy them):\n\n"
                 + fewshot_style(1) + "\n\n")

    # Short, strict prompt — small models follow tight rules better than long ones.
    prompt = (
        "You are an Indian equity markets analyst. Write ONE paragraph (4-5 sentences) on "
        "what is driving Indian markets today.\n"
        "RULES (follow exactly):\n"
        "1. Output ONLY the paragraph. No headings, no labels, no bullet points, no preamble.\n"
        "2. Do NOT write 'DESK NOTE', 'VERDICT', 'INPUT', or repeat any label from the data.\n"
        "3. Lead with India: Nifty, banks, IT, flows, rupee, oil.\n"
        "4. Name 1-2 movers from STANDOUT MOVERS and use the **index-impact label given for each** "
        "(large / moderate / limited / negligible). A small Nifty weight (under ~3%) means LIMITED "
        "index impact even on a big % move — NEVER call a sub-3% weight 'significant' or 'major'.\n"
        "5. Use ONLY the numbers in 'DATA FOR TODAY' below. The example is style-only — NEVER "
        "reuse its numbers or company names. Invent nothing. Do NOT state any RBI reserve figure.\n"
        "6. Neutral tone. No investment advice.\n"
        "7. For any sector/stock claim, use the SECTOR PROXY ACTUALS — state what the stock ACTUALLY "
        "did today. Do NOT assert a textbook link (e.g. 'oil up helps ONGC') if the actual move "
        "contradicts it; if the tape diverges from the rule, say so.\n\n"
        + style +
        "DATA FOR TODAY (use ONLY these numbers; do not echo the labels):\n"
        f"- Read: {verdict}\n"
        f"- Movers: {movers or '(none)'}\n"
        f"- Sector proxy actuals: {proxies or '(none)'}\n"
        f"- Sector impact (rule tendencies — verify vs actuals): {sec_s}\n"
        f"- India headlines: {ind_s}\n"
        f"- Global context: {glo_s}\n\n"
        "Now write only the paragraph:"
    )
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.2},
        }, timeout=90)
        if r.status_code == 200:
            return _clean_note(r.json().get("response", "").strip())
    except Exception as e:
        return f"_(local LLM summary skipped: {str(e)[:60]})_"
    return None


def _clean_note(text: str) -> str:
    """
    Safety net for small models: strip echoed scaffolding / preambles so the report
    never shows 'DESK NOTE:', 'VERDICT:', label lines, or 'Here is the note:' filler.
    """
    if not text:
        return text
    # if it echoed a 'DESK NOTE:' label, keep only what follows the last one
    parts = re.split(r"(?i)desk note\s*[:\-]\s*", text)
    if len(parts) > 1:
        text = parts[-1]
    skip = ("verdict:", "standout movers:", "india headlines:", "computed read:",
            "sector impact:", "global context:", "global cues", "cross-asset",
            "input:", "output:", "data for today", "- read:", "- movers:")
    kept = [ln for ln in text.splitlines()
            if not any(ln.strip().lower().startswith(s) for s in skip)]
    out = " ".join(l.strip() for l in kept if l.strip())
    # strip common preambles
    out = re.sub(r"^(sure[,!]?\s*|here(?:'s| is)[^:]*:\s*|desk note\s*[:\-]\s*|note\s*[:\-]\s*)",
                 "", out, flags=re.I).strip()
    return out


def _fmt_quote_row(q: dict) -> str:
    last = q["last"] if q["last"] is not None else "n/a"
    pct = q["pct_change"]
    if pct is None:
        arrow = ""
        pct_s = "n/a"
    else:
        arrow = "🔺" if pct > 0 else ("🔻" if pct < 0 else "▪️")
        pct_s = f"{pct:+.2f}%"
    return f"| {q['name']} | {last} | {arrow} {pct_s} |"


# Sector → representative proxy stocks (short display names) for the dashboard heat-map.
DASH_SECTORS = {
    "Banks":   ["HDFC Bank", "ICICI Bank"],
    "IT":      ["TCS", "Infosys", "Wipro", "HCL Tech"],
    "Auto":    ["Maruti", "M&M", "Bajaj Auto", "Hero MotoCorp", "Tata Motors"],
    "Energy":  ["ONGC", "BPCL", "Reliance"],
    "Metals":  ["Tata Steel"],
    "Pharma":  ["Sun Pharma", "Dr Reddy", "Cipla"],
    "Defence": ["HAL", "Bharat Electronics"],
}


def _dir3(x, band=0.10):
    return "🟢 Bull" if x > band else "🔴 Bear" if x < -band else "🟡 Neutral"


def _conf(mag, k, base=50, cap=95):
    return int(min(cap, base + abs(mag) * k))


_DRV_LBL = {"oil_pct": "Oil", "vix_pct": "VIX", "us10y_pct": "US10Y", "dxy_pct": "DXY",
            "kospi_pct": "Kospi", "sox_pct": "SOX", "fii_kcr": "FII", "geopolitics_hits": "Geo",
            "india_cpi_hot": "India CPI", "us_cpi_cool": "US CPI", "interaction": "Interaction"}


def _driver_why(k, dt_, drivers, observed):
    mv = dt_["move"]
    if k == "oil_pct":
        return ("small move — limited fuel/inflation bite, likely priced in" if abs(mv) < 1
                else "level-amplified inflation / CAD pressure")
    if k == "fii_kcr":
        dii = observed.get("dii")
        return ("FII selling but DII buying absorbed it (net liquidity OK)" if dii and dii > 0
                else "foreign selling pressured large-caps")
    if k in ("kospi_pct", "sox_pct"):
        return ("capped — weak/indirect transmission" if dt_.get("dom_capped")
                else "global chip / AI-risk signal")
    if k == "vix_pct":
        return "risk-on (fear easing)" if mv < 0 else "risk-off (fear rising)"
    if k == "india_cpi_hot":  return "hot CPI keeps RBI hawkish → rate-sensitives"
    if k == "us_cpi_cool":    return "cool US CPI → Fed-easing hopes, risk-on"
    if k == "us10y_pct":      return "US-rate move via currency / FII channel"
    if k == "dxy_pct":        return "dollar move → EM-flow signal"
    if k == "geopolitics_hits": return "geopolitics → oil / risk premium"
    if k == "interaction":    return "reinforcing drivers amplified the signal"
    return ""


def build_what_mattered(eng, observed):
    """Synthesis: which drivers actually controlled the tape today, and why."""
    det = eng["indices"].get("Nifty 50", {}).get("detail", {})
    tabs = sum(abs(d["contrib"]) for d in det.values()) or 1.0
    rows = []
    for k, d in sorted(det.items(), key=lambda kv: -abs(kv[1]["contrib"])):
        if d["contrib"] == 0 and d["move"] == 0:
            continue
        dom = abs(d["contrib"]) / tabs * 100
        influence = "🔥 High" if dom >= 25 else "Medium" if dom >= 12 else "Low"
        exp = "🔴 Bearish" if d["contrib"] < 0 else "🟢 Bullish" if d["contrib"] > 0 else "Neutral"
        rows.append((_DRV_LBL.get(k, k), exp, influence, dom, _driver_why(k, d, eng["drivers"], observed)))
    return rows


def build_theme_view(eng):
    """Group drivers into themes; name the dominant theme + supporting/counter drivers."""
    det = eng["indices"].get("Nifty 50", {}).get("detail", {})
    if not det:
        return None
    theme_net = {}
    for k, d in det.items():
        th = DRIVER_THEME.get(k, "Other")
        theme_net[th] = theme_net.get(th, 0) + d["contrib"]
    if not theme_net:
        return None
    dom_theme = max(theme_net, key=lambda t: abs(theme_net[t]))
    net_total = eng["indices"]["Nifty 50"]["total"]
    up = net_total >= 0
    support = [_DRV_LBL.get(k, k) for k, d in sorted(det.items(), key=lambda kv: -abs(kv[1]["contrib"]))
               if d["contrib"] != 0 and (d["contrib"] > 0) == up]
    counter = [_DRV_LBL.get(k, k) for k, d in sorted(det.items(), key=lambda kv: -abs(kv[1]["contrib"]))
               if d["contrib"] != 0 and (d["contrib"] > 0) != up]
    return dom_theme, round(theme_net[dom_theme], 2), support[:4], counter[:4]


def build_executive_dashboard(eng, sb, observed, ai_regime, cats, news,
                              quotes_macro, stock_lists) -> list[str]:
    """Layer-1 trading-desk dashboard: regime, bias, conflict matrix, top drivers,
    sector heat-map, catalysts, conviction themes. All numbers, minimal prose."""
    L = []
    ni = eng["indices"].get("Nifty 50", {})
    fv = ni.get("total", 0.0)
    combined = sb["combined"]
    bias = ("🟢 Bullish" if combined > 0.15 else "🔴 Bearish" if combined < -0.15 else "🟡 Neutral")
    agree = eng.get("agreement")
    conf = f"{agree*100:.0f}%" if agree is not None else "n/a"

    L.append("## 📟 Executive dashboard\n")
    regime_tone = ("🟢 Risk-on" if observed.get("nifty") and observed["nifty"] > 0.1
                   else "🔴 Risk-off" if observed.get("nifty") and observed["nifty"] < -0.1
                   else "🟡 Mixed")
    L.append(f"**Market regime:** {regime_tone}  ·  **Trading bias:** {bias} ({combined:+.2f})  ·  "
             f"**Conviction:** {conf}  ·  **AI regime:** {ai_regime}")
    obs = observed.get("nifty")
    if obs is not None:
        gap = round(obs - fv, 2)
        resid = ("below" if gap < -0.05 else "above" if gap > 0.05 else "at")
        L.append(f"**Macro pricing gap (residual):** {gap:+.2f}% (observed {obs:+.2f}% vs "
                 f"macro-model {fv:+.2f}%) — price is {resid} what *macro drivers alone* explain. "
                 f"*NOT a buy/sell signal:* the residual is earnings, positioning, flows & "
                 f"liquidity the macro model doesn't capture, not mispricing.\n")

    # ---- Market Conflict Matrix ----
    fii = observed.get("fii")
    dii = observed.get("dii")
    net_flow = ((fii or 0) + (dii or 0)) / 1000.0   # #2 NET institutional flow (FII+DII)
    news_row = next((r for r in sb["rows"] if r["signal"] == "News"), {"score": 0})
    quant = [r for r in sb["rows"] if r["signal"] not in ("Macro", "News")]
    layers = [
        ("Price action", (obs or 0), _conf(obs or 0, 60)),
        ("Macro", fv, int((agree or 0.5) * 100)),
        ("News", news_row["score"], _conf(news_row["score"], 45)),
        ("Net flow (FII+DII)", net_flow, _conf(net_flow, 12)),
    ]
    L.append("### ⚖️ Market conflict matrix\n")
    L.append("| Layer | Direction | Score | Conviction |")   # #8 show the number
    L.append("|---|---|---|---|")
    bull = bear = 0
    for name, val, c in layers:
        d = _dir3(val)
        bull += "Bull" in d; bear += "Bear" in d
        L.append(f"| {name} | {d} | {val:+.2f} | {c}% |")
    if quant:
        qavg = sum(r["score"] for r in quant) / len(quant)
        d = _dir3(qavg); bull += "Bull" in d; bear += "Bear" in d
        L.append(f"| Quant (option-chain) | {d} | {qavg:+.2f} | fed |")
    else:
        L.append("| Quant (option-chain) | — not fed | — | — |")
    net = "🟢 Bullish" if bull > bear else "🔴 Bearish" if bear > bull else "🟡 Split"
    L.append(f"\n**Consensus:** {bull} bull / {bear} bear → **{net}**. "
             f"_Note: **net institutional flow {net_flow:+.2f}k cr** (FII {(fii or 0):+,.0f} + DII "
             f"{(dii or 0):+,.0f}) — foreign positioning ≠ net liquidity._\n")

    # ---- Top drivers ----
    if ni.get("detail"):
        lbl = {"oil_pct": "Oil", "vix_pct": "VIX", "us10y_pct": "US10Y", "dxy_pct": "DXY",
               "kospi_pct": "Kospi", "sox_pct": "SOX", "fii_kcr": "FII", "geopolitics_hits": "Geo",
               "india_cpi_hot": "IN-CPI", "us_cpi_cool": "US-CPI"}
        top = sorted(ni["detail"].items(), key=lambda kv: -abs(kv[1]["contrib"]))[:5]
        drv = " · ".join(f"{'↑' if d['contrib']>0 else '↓'} {lbl.get(k,k)} {d['contrib']:+.2f}"
                         for k, d in top if d["contrib"] != 0)
        L.append(f"### 🔝 Top drivers (contribution to Nifty)\n{drv}\n")

    # ---- What actually mattered today (synthesis) ----
    wm = build_what_mattered(eng, observed)
    if wm:
        L.append("### 🎯 What actually mattered today\n")
        L.append("| Driver | Expected | Actual influence | Why |")
        L.append("|---|---|---|---|")
        for name, exp, infl, dom, why in wm[:7]:
            L.append(f"| {name} | {exp} | {infl} ({dom:.0f}%) | {why} |")
    tv = build_theme_view(eng)
    if tv:
        dom_theme, tnet, support, counter = tv
        L.append(f"\n**Dominant theme: {dom_theme}** ({tnet:+.2f}) · "
                 f"Supporting: {', '.join(support) or '—'} · Counter: {', '.join(counter) or '—'}\n")
    if eng.get("interactions"):
        L.append("**Interactions:** " + " ".join(eng["interactions"]) + "\n")

    # ---- Sector heat-map ----
    px = {}
    for ql in stock_lists:
        for q in ql:
            if q.get("pct_change") is not None:
                px[q["name"].split(" (")[0].strip().lower()] = q["pct_change"]
    L.append("### 🎯 Sector bias (today)\n")
    L.append("| Sector | Bias | Avg move | Conviction |")
    L.append("|---|---|---|---|")
    sector_bias = []
    for sec, names in DASH_SECTORS.items():
        moves = [px[n.lower()] for n in names if n.lower() in px]
        if not moves:
            continue
        avg = sum(moves) / len(moves)
        same = sum(1 for m in moves if (m > 0) == (avg > 0))
        c = int(same / len(moves) * 100)
        # IT respects the AI regime label
        note = " (AI-Substitution)" if sec == "IT" and "Substitution" in ai_regime else ""
        L.append(f"| {sec}{note} | {_dir3(avg, 0.15)} | {avg:+.2f}% | {c}% |")
        sector_bias.append((sec, avg, c))

    # ---- Top catalysts ----
    cat_bits = []
    if us_cpi_cool(news): cat_bits.append("US CPI softer → risk-on")
    if india_cpi_hot(news): cat_bits.append("India CPI hot → RBI cautious")
    v = observed.get("vix")
    if v is not None and abs(v) > 3: cat_bits.append(f"VIX {v:+.1f}%")
    if it_peer_readthrough(news): cat_bits.append("IBM/peer warning weighs on IT")
    if fii is not None: cat_bits.append(f"FII ₹{fii:+,.0f}cr / DII ₹{observed.get('dii',0):+,.0f}cr")
    if cats.get("tomorrow"): cat_bits.append(f"{len(cats['tomorrow'])} results tomorrow")
    if cat_bits:
        L.append("\n### 📌 Top catalysts\n" + " · ".join("• " + c for c in cat_bits[:6]) + "\n")

    # ---- Highest-conviction themes (directional, not advice) ----
    ranked = sorted(sector_bias, key=lambda x: -abs(x[1]) * x[2] / 100)[:3]
    if ranked:
        th = " · ".join(f"{i+1}. {_dir3(a,0.15).split()[0]} {sec}"
                        for i, (sec, a, c) in enumerate(ranked))
        L.append(f"### 💡 Highest-conviction themes\n{th}")
    L.append("\n_Bias = Bullish / Neutral / Bearish. Conviction = signal strength / driver "
             "agreement, **not** a probability of profit. Directional context only — **not "
             "investment advice.**_\n\n---\n")
    return L


def build_report(quotes_idx, quotes_macro, quotes_stk, it_quotes,
                 sector_quotes, theme_quotes, news, earnings, flows,
                 univ_quotes=None) -> str:
    univ_quotes = univ_quotes or []
    now = dt.datetime.now().strftime("%A, %d %B %Y %H:%M")
    L = []
    L.append(f"# Market Scan — {now}\n")

    # --- top-of-report verdict banner (rule-based, always on) ---
    verdict_str = build_verdict(quotes_idx, quotes_macro, news, flows)
    L.append(f"> {verdict_str}\n")
    # data-driven AI regime decided ONCE, up front, so every section is consistent
    ai_regime, ai_ev, ai_conf = detect_ai_regime(news, quotes_idx, quotes_macro)
    ai_regime_label = (f"Possible {ai_regime}" if ai_conf == "tentative" and ai_regime != "Neutral"
                       else ai_regime)
    sector_lines = build_sector_impact(quotes_idx, quotes_macro, news, ai_regime)
    gainers, losers = build_standout_movers(
        [quotes_stk, it_quotes, sector_quotes, theme_quotes, univ_quotes])

    def _mv_str(q):
        wt = _nifty_weight(q.get("symbol"))
        if wt is None:
            tag = "not in Nifty — negligible index impact"
        else:
            tier = "large" if wt >= 5 else "moderate" if wt >= 2 else "limited"
            tag = f"~{wt:.1f}% wt — {tier} index impact"
        return f"{q['name']} {q['pct_change']:+.1f}% ({tag})"

    movers_str = "; ".join(_mv_str(q) for q in (gainers[:3] + losers[:2]))
    proxy_str = "; ".join(
        f"{q['name'].split(' (')[0]} {q['pct_change']:+.1f}%"
        for q in sector_quotes if q.get("pct_change") is not None)
    # ---- core analytics up front (for the At-a-glance dashboard) ----
    eng = build_causal_engine(quotes_idx, quotes_macro, flows, news, ai_regime)
    ni = eng["indices"].get("Nifty 50")
    bn = eng["indices"].get("Bank Nifty")
    regime_tone, observed = market_regime(quotes_idx, quotes_macro, flows)
    comp = classify_company_news(news)
    external = load_external_signals()
    sb = build_signal_scoreboard(eng, comp, external)
    cats = build_catalysts(earnings)

    # ==== LAYER 1 — Executive dashboard (20-second trader view) ====
    L += build_executive_dashboard(
        eng, sb, observed, ai_regime_label, cats, news, quotes_macro,
        [quotes_stk, it_quotes, sector_quotes, theme_quotes, univ_quotes])

    # ==== LAYER 2/3 — supporting detail ====
    # ---- 📊 At a glance: three timelines, clearly separated ----
    L.append("## 📊 At a glance — observed vs forward\n")
    L.append("| Layer | Bias | Detail |")
    L.append("|---|---|---|")
    nifty_q = next((q for q in quotes_idx if q["name"] == "Nifty 50"), {})
    obits = []
    if observed["nifty"] is not None:
        intr = nifty_q.get("pct_intraday")
        intr_s = f" (intraday {intr:+.2f}% from open)" if intr is not None else ""
        obits.append(f"Nifty {observed['nifty']:+.2f}% vs prev close{intr_s}")
    if observed["bank"] is not None: obits.append(f"Bank Nifty {observed['bank']:+.2f}%")
    if observed["vix"] is not None:  obits.append(f"VIX {observed['vix']:+.2f}%")
    # data-quality flags
    warn = ""
    asof = nifty_q.get("asof")
    _tdy = dt.date.today()
    xc = nifty_q.get("xcheck")
    if nifty_q.get("suspect"):
        detail = ""
        if xc and not xc["ok"]:
            detail = f" (yfinance {nifty_q.get('last')} vs NSE {xc['nse']}, {xc['diff_pct']:.2f}% off)"
        warn = (" 🛑 **SUSPECT: Nifty price disagrees with NSE / is outside today's range — "
                f"stale/bad feed; do NOT trust this verdict**{detail}")
    elif asof and asof < _tdy.isoformat() and _tdy.weekday() < 5:
        warn = f" ⚠️ data as-of {asof} (prev session — pre-open or delayed feed)"
    elif xc and xc["ok"]:
        warn = f" ✓ NSE cross-check: {xc['nse']} (matches)"
    L.append(f"| **Current market** — observed (fact) | {regime_tone} | {', '.join(obits)}{warn} |")
    # any suspect quote across indices/macro -> top-level banner
    _suspects = [q["name"] for q in (quotes_idx + quotes_macro) if q.get("suspect")]
    if _suspects:
        L.append(f"\n> 🛑 **Data-quality warning:** these feeds look stale/out-of-range and may "
                 f"corrupt the verdict & model: **{', '.join(_suspects)}**. Verify before relying.")
    if ni:
        conv = f"{eng['agreement']*100:.0f}%" if eng.get("agreement") is not None else "n/a"
        L.append(f"| **Macro bias** — driver model | {eng['sentiment']} | "
                 f"fair-value drift {ni['total']:+.2f}% · conviction {conv} |")
    news_row = next((r for r in sb["rows"] if r["signal"] == "News"), None)
    if news_row:
        L.append(f"| **News bias** — company news | {_lean(news_row['score'])} | "
                 f"score {news_row['score']:+.2f} |")
    quant_rows = [r for r in sb["rows"] if r["signal"] not in ("Macro", "News")]
    if quant_rows:
        qavg = sum(r["score"] for r in quant_rows) / len(quant_rows)
        L.append(f"| **Quant bias** — option-chain/stat | {_lean(qavg)} | "
                 f"{', '.join(r['signal'] for r in quant_rows)} |")
    else:
        L.append("| **Quant bias** — option-chain/stat | — not fed | drop `signals.json` to enable |")
    L.append(f"| **➡️ Final trading bias** | **{_lean(sb['combined'])}** | combined {sb['combined']:+.2f} |")

    # the GAP: observed vs driver-implied fair value (both from prev close)
    if ni and observed["nifty"] is not None:
        gap = round(observed["nifty"] - ni["total"], 2)
        where = "BELOW" if gap < -0.05 else "ABOVE" if gap > 0.05 else "AT"
        tail = ("(macro model under-explains — flows/earnings doing the lifting)" if gap > 0.05
                else "(macro model over-explains — flows/earnings a drag)" if gap < -0.05 else "")
        L.append(f"\n**Macro pricing gap (residual):** Nifty **{observed['nifty']:+.2f}% observed** vs "
                 f"**{ni['total']:+.2f}% macro-implied** → **~{abs(gap):.2f}% {where}** the macro model "
                 f"{tail}. *Residual ≠ mispricing:* it is earnings/positioning/liquidity the macro "
                 f"model omits — not a buy/sell signal.")
    L.append("\n_Each layer answers a different question — what happened (observed, from prev close) "
             "vs what drivers / news / quant imply. Disagreement is information, not error. "
             "'Fair-value drift' = the same-day move the drivers imply, not a next-move forecast._\n")

    narrative = llm_narrative(news, verdict_str, sector_lines, movers_str, proxy_str)
    if narrative:
        L.append("> **What's driving it:** " + narrative.replace("\n", " ") + "\n")
    lbl = {"oil_pct": "oil", "vix_pct": "VIX", "us10y_pct": "US10Y", "dxy_pct": "DXY",
           "kospi_pct": "Kospi", "sox_pct": "SOX", "fii_kcr": "FII", "geopolitics_hits": "geo",
           "india_cpi_hot": "IN-CPI", "us_cpi_cool": "US-CPI"}

    L.append("## 🔗 Forward-driver model — macro fair-value drift\n")
    L.append("_The same-day Nifty move today's drivers imply (from prev close). Compare with the "
             "observed move in the dashboard above — the **gap** is the signal._\n")

    # #3 conviction breakdown (show the numbers behind the label)
    conv = (f"{eng['agreement']*100:.0f}% ({eng['conviction']})"
            if eng.get("agreement") is not None else "n/a")
    diss = ""
    if eng.get("dissenters"):
        diss = " — pulling the other way: " + ", ".join(lbl.get(d, d) for d in eng["dissenters"][:3])
    L.append(f"**Model lean: {eng['sentiment']}** · **Signal agreement: {conv}**{diss}\n")
    L.append(f"_Agreement = share of contribution pointing the model's way: **{eng.get('n_bull',0)} "
             f"bullish drivers (+{eng.get('sum_bull',0):.2f}) vs {eng.get('n_bear',0)} bearish "
             f"({eng.get('sum_bear',0):.2f})**. It's alignment, not a probability of the outcome._\n")

    if eng.get("capped"):
        capped = ", ".join(lbl.get(k, k) for k in eng["capped"])
        L.append(f"> ⚠️ **Outlier driver(s) capped:** {capped} exceeded the sanity band and were "
                 f"clamped so one stale print can't dominate — verify the feed.\n")

    L.append("**Macro fair-value drift (driver-implied, *not* a forecast):**\n")
    if ni: L.append(f"- **Nifty 50: ≈ {ni['total']:+.2f}%**  (range {ni['lo']:+.2f}% to {ni['hi']:+.2f}%)")
    if bn: L.append(f"- **Bank Nifty: ≈ {bn['total']:+.2f}%**  (range {bn['lo']:+.2f}% to {bn['hi']:+.2f}%)")

    L.append("\n**Cause → effect chains firing:**\n")
    nic = ni["contrib"] if ni else {}
    if eng["chains"]:
        for text, drv in eng["chains"]:
            c = nic.get(drv)
            ctag = f"  _(Nifty {c:+.2f}%)_" if c is not None else "  _(offset)_"
            L.append(f"- {text}{ctag}")
    else:
        L.append("_No strong triggers firing — drivers look benign this run._")

    # #2 contribution derivation table (move × coefficient = contribution)
    if ni and ni.get("detail"):
        # Driver-dominance table (#11): move × coef = contribution, + % of total force
        total_abs = sum(abs(d["contrib"]) for d in ni["detail"].values()) or 1.0
        L.append("\n**Driver dominance (move × sensitivity = contribution; dominance = share of "
                 "total force):**\n")
        L.append("| Driver | Move | × Coef | = Contribution | Dominance |")
        L.append("|---|---|---|---|---|")
        ordered = sorted(ni["detail"].items(), key=lambda kv: -abs(kv[1]["contrib"]))
        for i, (k, dt_) in enumerate(ordered):
            if dt_["contrib"] == 0 and dt_["move"] == 0:
                continue
            mv = f"{dt_['move']:+.2f}" + ("cr" if k == "fii_kcr" else "%")
            capnote = " ⚠cap" if dt_["capped"] else ""
            coefcell = f"{dt_['coef']:+.3f}"
            if dt_.get("level_mult", 1.0) != 1.0:   # oil level amplifier
                coefcell += f" ×{dt_['level_mult']:.1f}(lvl)"
            dom = abs(dt_["contrib"]) / total_abs * 100
            star = " ⭐" if i == 0 else ""
            L.append(f"| {lbl.get(k, k)}{star} | {mv}{capnote} | {coefcell} | "
                     f"{dt_['contrib']:+.3f}% | {dom:.0f}% |")
        if eng.get("oil_mult", 1.0) != 1.0 and eng.get("brent_price"):
            L.append(f"\n_Oil impact scaled ×{eng['oil_mult']:.1f} for its **level** "
                     f"(Brent ${eng['brent_price']:.0f}) — the same % move bites harder at higher "
                     f"prices / near the $90–100 barriers._")
        L.append(f"| **Net** |  |  | **{ni['total']:+.2f}%** | 100% |")
        _tv = build_theme_view(eng)
        if _tv:
            _th, _tn, _sup, _cnt = _tv
            L.append(f"\n**Dominant theme: {_th}** ({_tn:+.2f}) · Supporting: "
                     f"{', '.join(_sup) or '—'} · Counter: {', '.join(_cnt) or '—'} "
                     f"_(themes are more stable than single variables)._")
        if eng.get("interactions"):
            L.append("\n" + " ".join(eng["interactions"]))
    L.append("\n_Heuristic composite of rough, editable sensitivities (`SENSITIVITY` dict) — for "
             "building intuition on direction & rough magnitude only. **Not a prediction, not a "
             "trade signal, not investment advice.** For real probabilities, calibrate these "
             "coefficients against historical event→return data with a backtest._\n")

    # --- Company summary (stock drivers) --- (comp computed up front)
    if USE_FULLTEXT and HAVE_FULLTEXT and comp:
        import fetch_article as _fa
        if not getattr(_fa, "HAVE_TRAFILATURA", False):
            print("  ! USE_FULLTEXT is on but trafilatura isn't installed — "
                  "run: pip install trafilatura  (skipping extraction)")
        else:
            n = min(FULLTEXT_MAX, len(comp))
            print(f"  fetching full text for top {n} company stories...")
            got = 0
            for c in comp[:FULLTEXT_MAX]:
                try:
                    art = fetch_article(c["link"])
                    if art.get("body"):
                        c["facts"] = art.get("numbers", [])[:6]
                        c["snippet"] = first_paragraph(art["body"])
                        got += 1
                        print(f"    ✓ {c['company']} [{art.get('method')}] "
                              f"{len(art['numbers'])} figures")
                    else:
                        print(f"    – {c['company']} [no body / paywalled]")
                except Exception as e:
                    print(f"    – {c['company']} [{str(e)[:40]}]")
            print(f"  full-text: enriched {got}/{n}")
    L.append("## 🏢 Company summary (stock drivers)\n")
    if comp:
        def _cline(c):
            mark = {"pos": "🟢", "neg": "🔴", "neutral": "⚪"}[c["sentiment"]]
            kmark = ("⚡ **Catalyst**" if c.get("kind") == "Catalyst"
                     else "📄 News") + f" ({c.get('kind_why', '')})"
            base = (f"- {mark} {kmark} · **{c['company']}** — [{c['title']}]({c['link']})  \n"
                    f"    _{_weight_tag(c['symbol'] + '.NS')} · sector: {c['sector']}_")
            if c.get("facts"):
                base += f"  \n    _Key figures: {', '.join(c['facts'])}_"
            if c.get("snippet"):
                base += f"  \n    _{c['snippet']}_"
            return base
        # #8: single list ordered by INDEX IMPACT (weight), not by sentiment bucket —
        # a Nifty trader cares that HUL (2.3%) matters more than Hero (0.5%).
        ranked = sorted(comp, key=lambda c: (c["nifty_wt"] or 0), reverse=True)
        L.append("_Ranked by index impact (heavyweights first); 🟢 positive / 🔴 negative / "
                 "⚪ neutral. ⚡ **Catalyst** = genuinely price-moving (results, order, fundraise, "
                 "regulatory); 📄 News = informational (coverage, appointment, MoU) — rarely moves "
                 "the tape._\n")
        for c in ranked[:12]:
            L.append(_cline(c))
        _ncat = sum(1 for c in ranked[:12] if c.get("kind") == "Catalyst")
        L.append(f"\n_{_ncat} of {min(len(ranked),12)} shown are true catalysts. Index impact = how "
                 f"much the news likely moves NIFTY, read from the stock's index weight. 'High "
                 f"company / Negligible Nifty' = a stock-picker's play, not an index mover._")
    else:
        L.append("_No company-specific stories detected this run._")

    # --- Upcoming catalysts ---
    cats = build_catalysts(earnings)
    L.append("\n## 📅 Upcoming catalysts (earnings)\n")
    if cats["tomorrow"]:
        L.append("**Tomorrow:** " + ", ".join(cats["tomorrow"][:12]))
    if cats["next3"]:
        L.append("\n**Next ~3 days:** " + ", ".join(cats["next3"][:15]))
    if not cats["tomorrow"] and not cats["next3"]:
        L.append("_No results scheduled in the next ~3 days (full calendar in §11)._")
    L.append("\n_⭐ = Nifty 50 heavyweight (moves the index); others are stock-specific._")

    # --- Signal scoreboard (comp/external/sb computed up front) ---
    L.append("## 🧮 Signal scoreboard\n")
    L.append("| Signal | Lean | Score | × Weight | = Contribution |")
    L.append("|---|---|---|---|---|")
    _twsum = sum(r["weight"] for r in sb["rows"]) or 1.0
    for r in sb["rows"]:
        note = f" ({r['note']})" if r.get("note") else ""
        contrib = r["score"] * r["weight"] / _twsum
        L.append(f"| {r['signal']}{note} | {_lean(r['score'])} | {r['score']:+.2f} | "
                 f"{r['weight']:.2f} | {contrib:+.3f} |")
    L.append(f"| **Combined** | **{_lean(sb['combined'])}** | **{sb['combined']:+.2f}** | | "
             f"**{sb['combined']:+.2f}** |")
    conv2 = (f"{sb['agreement']*100:.0f}% ({sb['conviction']})"
             if sb.get("agreement") is not None else "n/a")
    L.append(f"\n**Conviction (driver agreement): {conv2}.** _Score −1 (bearish) … +1 (bullish)._")
    if not external:
        L.append("_Option-chain, momentum, VRP, IC/hit-ratio, RND etc. are **not fed yet** — drop a "
                 "`signals.json` (written by your NiftyOptions quant stack) beside this script to fold "
                 "them into the combined score. Shape is documented in `load_external_signals()`._")
    L.append("")

    # --- Historical analogues (empirical: what happened the next day after events like today's) ---
    L.append("## 📚 Historical analogues (empirical, next-day)\n")
    stats = load_event_stats() if HAVE_EVENTS else {}
    if stats:
        today_r = {
            "oil": eng["drivers"].get("oil_pct"), "vix": eng["drivers"].get("vix_pct"),
            "sox": eng["drivers"].get("sox_pct"), "dxy": eng["drivers"].get("dxy_pct"),
            "usdinr": eng["raw"].get("usdinr"), "kospi": eng["drivers"].get("kospi_pct"),
            "us10y": eng["drivers"].get("us10y_pct"),
        }
        fired = match_conditions(today_r)
        fired = [c for c in fired if c in stats]
        if fired:
            L.append("_Today's setup matches these historical conditions. Next-day index behaviour:_\n")
            L.append("| Condition | Index | N | Avg next-day | Fell % of time |")
            L.append("|---|---|---|---|---|")
            for cond in fired:
                desc = stats[cond]["description"]
                for tgt in ("Nifty 50", "Bank Nifty", "Nifty IT"):
                    t = stats[cond]["targets"].get(tgt)
                    if t:
                        L.append(f"| {desc} | {tgt} | {t['n']} | {t['mean']:+.2f}% | {t['hit_down']:.0f}% |")
            L.append("\n_Empirical, price-based conditions only (no FII/geopolitics in free history). "
                     "Low N = weak evidence. Past ≠ future — this is context, not a forecast._")
        else:
            L.append("_No strong historical condition triggered today (drivers are benign)._")
    else:
        L.append("_Event memory not built yet — run `python3 build_events.py` once to populate "
                 "`events.db`, then this section cites what the index did historically after days "
                 "like today._")

    # --- Signals by time horizon (don't mix intraday catalysts with structural stories) ---
    L.append("\n## ⏳ Signals by time horizon\n")
    L.append("| Horizon | Active drivers today | Lean / status |")
    L.append("|---|---|---|")
    for h, drv, ln in build_horizon_view(eng, news, earnings):
        L.append(f"| {h} | {drv} | {ln} |")
    L.append("\n_Separates intraday catalysts (oil spike, VIX, flows) from structural stories "
             "(AI capex, policy) so they aren't conflated. Directional lean is modeled only for "
             "intraday same-day drivers; longer horizons list **what to watch**, not a directional "
             "call — we don't fake a 1-month prediction._\n")

    # --- Standout movers (weight-adjusted) --- (gainers/losers computed above)
    def _mv(q, up):
        w = _nifty_weight(q.get("symbol"))
        wtag = f" · ≈{w:.1f}% of Nifty" if w else " · limited/no Nifty weight"
        return f"- {'🔺' if up else '🔻'} **{q['name']}** {q['pct_change']:+.2f}%{wtag}"

    L.append("## 📈 Standout movers (weight-adjusted)\n")
    if gainers or losers:
        L.append("_Top gainers:_")
        for q in gainers:
            L.append(_mv(q, True))
        L.append("\n_Top losers:_")
        for q in losers:
            L.append(_mv(q, False))
        L.append("\n_A big % move in a low-weight name (jewellery, EV, smallcaps) barely nudges the "
                 "index — a −4% HCL Tech (≈1.6%) or +3% Tech Mahindra (≈1.0%) moves Nifty far less "
                 "than a −1% HDFC Bank (≈13%). Weight matters as much as the move._\n")
    else:
        L.append("_No stock moves captured this run._\n")

    L.append("## 1. Indices\n")
    L.append("| Index | Last | Change |")
    L.append("|---|---|---|")
    L += [_fmt_quote_row(q) for q in quotes_idx]

    L.append("\n## 2. Macro / Global cues\n")
    L.append("| Instrument | Last | Change |")
    L.append("|---|---|---|")
    L += [_fmt_quote_row(q) for q in quotes_macro]

    # ---- 3. Domestic flows: FII / DII ----
    L.append("\n## 3. Domestic flows — FII / DII (cash, ₹ cr)\n")
    real_flows = [f for f in flows if f.get("date")]
    if real_flows:
        L.append("| Date | Category | Buy | Sell | Net |")
        L.append("|---|---|---|---|---|")
        for f in real_flows:
            net = f.get("net", "")
            try:
                net_f = float(str(net).replace(",", ""))
                net = f"**{net_f:+,.0f}**" + (" 🟢" if net_f > 0 else " 🔴")
            except Exception:
                pass
            L.append(f"| {f['date']} | {f['category']} | {f['buy']} | {f['sell']} | {net} |")
        L.append("\n_FII selling + DII buying = domestic (SIP) money absorbing foreign "
                 "outflows, a recurring support for Nifty. SIP/AMFI monthly data: check "
                 "amfiindia.com._")
    else:
        note = flows[0]["category"] if flows else "no data"
        L.append(f"_FII/DII feed unavailable this run ({note}). "
                 f"Check nseindia.com or moneycontrol.com/stocks/marketstats/fii_dii_activity._")

    # ---- 4. Indian IT / AI-fear watch ----
    L.append("\n## 4. Indian IT / AI-fear watch\n")
    nifty_it = _pct_of(quotes_idx, "Nifty IT")
    kospi = _pct_of(quotes_macro, "Kospi")
    usdinr = _pct_of(quotes_macro, "USD/INR")
    callout = []
    if nifty_it is not None: callout.append(f"**Nifty IT** {nifty_it:+.2f}%")
    if kospi is not None:    callout.append(f"**Kospi** {kospi:+.2f}% (global AI/chip proxy)")
    if usdinr is not None:
        tail = "tailwind" if usdinr > 0 else "headwind"
        callout.append(f"**USDINR** {usdinr:+.2f}% → export earnings {tail}")
    if callout:
        L.append(" · ".join(callout) + "\n")
    L.append("| IT stock | Last | Change |")
    L.append("|---|---|---|")
    L += [_fmt_quote_row(q) for q in it_quotes]
    # AI-fear regime read (Kospi/SOX up + IT down = threat, not tailwind)
    _fear = ai_fear_read(eng, quotes_idx)
    if _fear:
        L.append("\n" + _fear + "\n")

    # Global IT-peer read-through (IBM/Accenture warnings lead Indian IT)
    peers = it_peer_readthrough(news)
    if peers:
        L.append("\n⚠️ **Global IT-peer warning → negative read-through for Indian IT** "
                 "(peer guidance leads TCS/Infosys/Wipro):")
        for n in peers[:3]:
            L.append(f"- 🔴 **{n['source']}** — [{n['title']}]({n['link']})")

    # IT sentiment: opportunity (deal wins) vs threat (demand/guidance/AI displacement)
    stance = ai_it_stance(news)
    L.append(f"\n**IT sentiment (AI + demand) for Indian IT: {stance['label']}** "
             f"({len(stance['bull'])} positive vs {len(stance['bear'])} negative headline(s)).\n")
    if stance["bull"]:
        L.append("_Opportunity (AI deal wins / services):_")
        for n in stance["bull"][:4]:
            L.append(f"- 🟢 **{n['source']}** — [{n['title']}]({n['link']})")
    if stance["bear"]:
        L.append("_Threat (jobs / pricing / displacement):_")
        for n in stance["bear"][:4]:
            L.append(f"- 🔴 **{n['source']}** — [{n['title']}]({n['link']})")

    it_news = [n for n in news if is_it_ai_headline(n)]
    if it_news:
        L.append("\n**Other IT / AI headlines:**\n")
        for n in it_news[:8]:
            L.append(f"- **{n['source']}** — [{n['title']}]({n['link']})")
    else:
        L.append("\n_No other Indian-IT / AI headlines this run._")

    # ---- 5. Cross-asset -> sector impact map ----
    L.append("\n## 5. Cross-asset → sector impact map\n")
    L.append("_How today's oil / rupee / rates / Kospi moves likely flow into Indian sectors._\n")
    for line in sector_lines:
        L.append(f"- {line}")

    # transmission network: driver → channel → sector (multi-hop, extensible)
    tmap = build_transmission_map(eng, quotes_macro, ai_regime, news)
    if tmap:
        L.append("\n### 🕸️ Transmission map (driver → channel → sector)\n")
        L.append("_One driver fans out through several economic channels — banks aren't hit by oil "
                 "directly, but via inflation→RBI→rates; AI lifts power/telecom/banks (infra & "
                 "productivity) while it pressures IT services (substitution)._\n")
        L += tmap

    # oil price-LEVEL regime (non-linear: $80 vs $90 vs $100 behave differently)
    oil_regime = build_oil_regime(quotes_macro, news)
    if oil_regime:
        L.append("")
        for line in oil_regime:
            L.append(f"- {line}")

    # relationship confidence hierarchy: baseline link → current modifiers → today's outcome
    rel_rows = build_relationship_hierarchy(quotes_macro, sector_quotes)
    if rel_rows:
        L.append("\n### 🧭 Relationship confidence hierarchy (oil)\n")
        L.append("_The economic **baseline** is the textbook link. **Modifiers** are the real-world "
                 "factors (policy, margins, demand) that can override it — OMCs and ONGC are NOT pure "
                 "oil plays. **Today's outcome** is what the tape actually did. A relationship isn't "
                 "'broken' when overridden; a modifier simply dominated._\n")
        L.append("| Relationship | ① Baseline | ② Current modifiers | ③ Today's outcome |")
        L.append("|---|---|---|---|")
        for label, baseline, mods, outcome in rel_rows:
            L.append(f"| {label} | {baseline} | {mods} | {outcome} |")
        L.append("\n_BPCL = Oil + marketing margin + GRM + govt pricing + inventory. "
                 "ONGC = Oil + govt policy + gas price + production + FII flow. "
                 "The single-arrow oil→stock link is only the first term._")

    # metals: gold/silver (haven) vs copper (growth)
    metals = build_metals_reaction(quotes_macro, news)
    if metals:
        L.append("\n**Metals — haven vs growth:**\n")
        for line in metals:
            L.append(f"- {line}")
    # live proof of the oil divergence
    if any(q.get("last") is not None for q in sector_quotes):
        L.append("\n**Sector proxies today (watch the oil divergence):**\n")
        L.append("| Proxy | Last | Change |")
        L.append("|---|---|---|")
        L += [_fmt_quote_row(q) for q in sector_quotes]
        _recon = reconcile_oil_proxies(quotes_macro, sector_quotes)
        if _recon:
            L.append("\n" + _recon)

    # ---- SECTOR FACTOR MODEL: net score per sector (aggregate, not isolated) ----
    sfm = build_sector_factor_model(eng, quotes_macro, observed, news, ai_regime)
    if sfm:
        L.append("\n### 🧮 Sector factor model — net driver score per sector\n")
        L.append("_Institutional method: instead of judging each driver alone, **aggregate every "
                 "active macro, flow & thematic factor into one net score per sector**. When oil and "
                 "rates both touch banks, this answers the real question — the **net** effect. "
                 "Coefficients are heuristic directional sensitivities (sign-consistent with the "
                 "calibrated index betas), not point forecasts._\n")
        L.append("| Sector | Net score | View | Factor breakdown (active drivers) |")
        L.append("|---|---:|---|---|")
        for s in sfm:
            brk = ", ".join(f"{lab} {c:+.2f}" for lab, c in s["rows"]) or "—"
            L.append(f"| **{s['sector']}** | {s['net']:+.2f} | {s['verdict']} | {brk} |")
        L.append("\n_Net = Σ(driver move × sensitivity). >+0.10 Bullish · −0.10…+0.10 Neutral · "
                 "<−0.10 Bearish. Directional bias only — not investment advice._")

    # ---- Cause → effect scorecard: regime-aware (regime detected up front) ----
    scard = build_cause_effect_scorecard(
        eng, quotes_macro, [quotes_stk, it_quotes, sector_quotes, theme_quotes, univ_quotes],
        ai_regime=ai_regime)
    if scard:
        L.append("\n### 🔁 Cause → effect scorecard (rule vs tape)\n")
        if ai_regime != "Neutral":
            implies = "DOWN" if ai_regime == "Substitution" else "UP"
            L.append(f"_**Active AI regime: {ai_regime_label}** — under this regime SOX/Kospi↑ implies "
                     f"Indian IT **{implies}** (not the textbook 'IT up'). So IT falling on a "
                     f"chip-rally day is a *confirmed* regime signal, not a failed rule._\n")
        conf = load_linkage_conf()

        def _today_applic(r):
            """Is today's ENVIRONMENT suitable for applying this rule? (magnitude +
            regime activation) — separate from the rule's standing reliability."""
            regime_active = bool(r["regime"]) and "Substitution" in (r["regime"] or "")
            if regime_active:
                return "🟢 High (regime active)"
            s = r["strength"]
            if s == "Strong":
                return "🟢 High (big move)"
            if s == "Medium":
                return "🟡 Medium"
            return "🔴 Low (small move)"

        L.append("| Linkage | Driver (strength) | Regime | Expected | Actual (proxies, wt-✓) | "
                 "Agreement | Econ · Hist. reliability | Today |")
        L.append("|---|---|---|---|---|---|---|---|")
        L.append("_Econ = economic soundness (★, standing); Hist = statistical support (hit-rate + "
                 "95% band); **Today** = whether the environment suits the rule *now* (driver "
                 "magnitude + active regime). A ★★★★★ rule on a flat-driver day is still 🔴 Low today._\n")
        for r in scard:
            proxies = ", ".join(f"{s} {a:+.1f}%{'✓' if ok else '✗'}" for s, a, ok, _e, _w in r["checks"])
            agree = f"{r['wagree']:.0f}% wt" + (" ⚠️" if r["wagree"] < 50 else "")
            econ = "★" * ECON_RATIONALE.get(r["name"], 3)
            cf = conf.get(r["name"])
            stat = _reliability_str(cf["hit_rate"], cf["n"]) if cf else "— (build_events.py)"
            sect = r["name"].split("→")[-1].strip()[:14]
            # transmission TYPE — distinguishes a direct chain from a proxy/correlation,
            # so a high hit-rate on an 'incidental' link isn't mistaken for causation.
            _ty, _ = relationship_type(r["name"])
            _tymark = {"supply_chain": " ⛓️direct", "spending_proxy": " ↔proxy",
                       "flow_currency": " 💱indirect", "incidental": " ⚠️corr-only"}.get(_ty, "")
            L.append(f"| {r['name']}{_tymark} | {r['driver']} ({r['strength']}) | {r['regime'] or '—'} | "
                     f"{sect} {r['expected']} | {proxies} | {agree} | Econ {econ} · {stat} | "
                     f"{_today_applic(r)} |")

        # bucking-the-trend — MERGED per stock (#1 dedup), with contradicted rules + reason
        buck = {}
        for r in scard:
            for s, a, ok, e, _w in r["checks"]:
                if not ok:
                    buck.setdefault(s, {"a": a, "exp": e, "rules": []})
                    buck[s]["rules"].append(r["name"].split("→")[0].strip())
        if buck:
            L.append("\n**Driver-override analysis** — a *stronger driver dominated the rule*. "
                     "**Overridden ≠ broken**: the economic link still holds, another force just "
                     "controlled price discovery today.\n")
            for s, info in list(buck.items())[:6]:
                stack, dom, reason = _override_analysis(s, info["a"], info["exp"], eng, observed, news)
                stack_s = ", ".join(f"{n} {sc:+.0f}" for n, sc in stack)
                exp_dir = "↑" if info["exp"] > 0 else "↓"
                rtail = f' · headline: *"{reason[:50]}"*' if reason else ""
                L.append(f"- **{s}** {info['a']:+.1f}% · rule expected {exp_dir} → "
                         f"**overridden by {dom}**  \n    _driver stack: {stack_s}{rtail}_")
        L.append("\n_Agreement is **index-weighted**. Reliability includes a 95% band; <52% ≈ "
                 "coin-flip. 'Overridden' means a competing driver dominated — not that the "
                 "relationship broke._")
        L.append("_Stack weights (±0–4) are **heuristic ordinal scores** — they rank which force "
                 "dominated, not calibrated regression betas. Read them as ordering, not magnitude._")

    # ---- 6. Thematic, structural & macro plays ----
    L.append("\n## 6. Thematic, structural & macro plays\n")
    themes = detect_themes(news)
    if themes:
        L.append("_Stories moving specific baskets or the macro backdrop (RBI, bonds, consumer), "
                 "with the reasoning behind the move._\n")
        for t in themes:
            L.append(f"### {t['name']}\n")
            L.append(t["why"] + "\n")
            for n in t["hits"][:5]:
                L.append(f"- **{n['source']}** — [{n['title']}]({n['link']})")
            L.append("")
    else:
        L.append("_No thematic stories (jewellery/gold, EV, semiconductors, China chip) "
                 "flagged in today's headlines. Baskets shown below for reference._\n")
    if any(q.get("last") is not None for q in theme_quotes):
        L.append("**Theme basket today:**\n")
        L.append("| Name | Last | Change |")
        L.append("|---|---|---|")
        L += [_fmt_quote_row(q) for q in theme_quotes]

    # ---- 7. Policy / Government push ----
    L.append("\n## 7. Policy / Government push\n")
    policy_news = [n for n in news if is_policy_headline(n)]
    if policy_news:
        for n in policy_news[:12]:
            L.append(f"- **{n['source']}** — [{n['title']}]({n['link']})")
        L.append("\n_Policy tailwinds (PLI, budget, duties, capex, defence/railways/semis) "
                 "can drive sector-specific optimism regardless of the broad tape._")
    else:
        L.append("_No policy / government-push headlines flagged this run._")

    L.append("\n## 8. Key stocks\n")
    L.append("| Stock | Last | Change |")
    L.append("|---|---|---|")
    L += [_fmt_quote_row(q) for q in quotes_stk]

    # macro-tagged headlines first
    macro_news = [n for n in news if n.get("macro")]
    other_news = [n for n in news if not n.get("macro")]

    L.append("\n## 9. Market-moving headlines (macro / geopolitics / AI)\n")
    if macro_news:
        for n in macro_news[:20]:
            tag = f" _({n['tags']})_" if n["tags"] else ""
            L.append(f"- **{n['source']}** — [{n['title']}]({n['link']}){tag}")
    else:
        L.append("_No macro-tagged headlines pulled this run._")

    L.append("\n## 10. Other headlines\n")
    for n in other_news[:20]:
        L.append(f"- {n['source']} — [{n['title']}]({n['link']})")

    L.append("\n## 11. Earnings / corporate events\n")
    real = [e for e in earnings if e.get("date")]
    if real:
        enriched = [e for e in real if e.get("mktcap")]
        others = [e for e in real if not e.get("mktcap")]

        # Nifty 50 heavyweights first — these drive index-level optimism
        n50 = []
        seen50 = set()
        for e in enriched:
            sym = e["symbol"].strip()
            if e.get("nifty50") and sym not in seen50:
                seen50.add(sym)
                n50.append(e)
        if n50:
            L.append("### ⭐ Nifty 50 heavyweights reporting (index-mover watch)\n")
            L.append("| Date | Company | Symbol | Mkt Cap | P/E | Rev YoY | Profit YoY |")
            L.append("|---|---|---|---|---|---|---|")
            for e in n50:
                L.append(f"| {e['date']} | {e['company']} | {e['symbol']} | "
                         f"{e.get('mktcap','—')} | {e.get('pe','—')} | "
                         f"{e.get('rev_yoy','—')} | {e.get('profit_yoy','—')} |")
            strong = [e for e in n50 if _is_strong(e)]
            if strong:
                names = ", ".join(e["symbol"] for e in strong)
                L.append(f"\n_Going in with strong trailing growth (potential optimism "
                         f"drivers): **{names}**._")
            L.append("")

        if enriched:
            L.append("### Other results this window — with fundamentals\n")
            L.append("| Date | Company | Symbol | Mkt Cap | P/E | Rev YoY | Profit YoY |")
            L.append("|---|---|---|---|---|---|---|")
            for e in enriched:
                if e in n50:
                    continue
                L.append(f"| {e['date']} | {e['company']} | {e['symbol']} | "
                         f"{e.get('mktcap','—')} | {e.get('pe','—')} | "
                         f"{e.get('rev_yoy','—')} | {e.get('profit_yoy','—')} |")
            L.append("\n_Rev/Profit YoY = trailing growth from yfinance (latest reported, "
                     "not the quarter being announced)._\n")

        if others:
            L.append("### Other events / smaller names\n")
            L.append("| Date | Company | Symbol | Purpose |")
            L.append("|---|---|---|---|")
            for e in others[:40]:
                L.append(f"| {e['date']} | {e['company']} | {e['symbol']} | {e['purpose']} |")
    else:
        note = earnings[0]["company"] if earnings else "no data"
        L.append(f"_Earnings feed unavailable this run ({note}). "
                 f"Check nseindia.com or bseindia.com corporate calendar._")

    L.append("\n---\n_Generated by market_scan.py. Sources: RSS feeds + yfinance + NSE. "
             "Not investment advice._")
    return "\n".join(L)


def _is_strong(e: dict) -> bool:
    """Heuristic: trailing revenue AND profit both positive -> optimism candidate."""
    def pos(v):
        try:
            return float(str(v).replace("%", "").replace("+", "")) > 0
        except Exception:
            return False
    return pos(e.get("rev_yoy")) and pos(e.get("profit_yoy"))


# =========================================================================
# MAIN
# =========================================================================

def selftest(report: str) -> tuple[bool, list[str]]:
    """
    Pre-flight gate before publishing. Returns (passed, issues). Checks for the
    failure modes we've actually hit: NaN in the output, echoed prompt scaffolding,
    empty critical fields, and flags outlier drivers. Fast, no network.
    """
    issues = []

    # 1) NaN anywhere (word-bounded so 'Anant' etc. don't trip it)
    if re.search(r"\bnan\b", report, re.IGNORECASE):
        issues.append("NaN found in report (a feed value came back NaN)")

    # 1b) suspect/out-of-range feed (a stale index print can flip the verdict)
    if "SUSPECT" in report or "Data-quality warning" in report:
        issues.append("suspect/out-of-range price data (stale feed — verdict unreliable)")

    # 2) echoed prompt scaffolding in the desk note
    for tok in ("DESK NOTE", "DATA FOR TODAY", "INDIA HEADLINES:", "STANDOUT MOVERS:",
                "COMPUTED READ", "GLOBAL CUES (context"):
        if tok in report:
            issues.append(f"prompt scaffolding leaked into output: '{tok}'")

    # 3) empty critical fields
    if not re.search(r"Nifty 50 \| [\d,]+\.?\d*", report):
        issues.append("Indices table missing a numeric Nifty 50 value")
    if not re.search(r"Verdict\b", report):
        issues.append("Verdict banner missing")
    if "expected move today" in report and not re.search(
            r"Nifty 50: ≈ [+\-]\d", report):
        issues.append("Expected-move estimate missing/!=number")

    # 4) outlier drivers (warn, not hard fail) — surfaced by the report itself
    warn = []
    if "Outlier driver move" in report:
        warn.append("outlier driver flagged — verify the feed")

    passed = len(issues) == 0
    return passed, issues + [f"(warn) {w}" for w in warn]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="run pre-flight checks; exit non-zero if the report is unsafe to publish")
    args, _ = ap.parse_known_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")

    print("Fetching prices...")
    quotes_idx = fetch_quotes(INDICES)
    print("Cross-checking indices vs NSE...")
    cross_check_indices(quotes_idx)
    quotes_macro = fetch_quotes(MACRO)
    quotes_stk = fetch_quotes(STOCKS)
    it_quotes = fetch_quotes(IT_STOCKS)
    sector_quotes = fetch_quotes(SECTOR_PROXIES)
    theme_quotes = fetch_quotes(THEME_STOCKS)
    univ_quotes = fetch_quotes(SECTOR_UNIVERSE)

    print("Fetching FII/DII flows...")
    flows = fetch_fii_dii()

    print("Fetching news...")
    news = fetch_news()

    print("Fetching earnings calendar...")
    earnings = fetch_earnings()
    print("Enriching results with fundamentals...")
    earnings = enrich_earnings(earnings)

    report = build_report(quotes_idx, quotes_macro, quotes_stk, it_quotes,
                          sector_quotes, theme_quotes, news, earnings, flows,
                          univ_quotes=univ_quotes)

    md_path = REPORT_DIR / f"market_scan_{stamp}.md"
    md_path.write_text(report, encoding="utf-8")

    # CSV of all headlines for record-keeping
    csv_path = REPORT_DIR / f"headlines_{stamp}.csv"
    if news:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["source", "title", "link",
                                              "published", "macro", "tags"])
            w.writeheader()
            w.writerows(news)

    print(f"\nSaved report : {md_path}")
    print(f"Saved csv    : {csv_path}")
    if not HAVE_YF:
        print("NOTE: yfinance not installed -> price tables will be empty. "
              "Run: pip install yfinance")

    # pre-flight gate
    passed, issues = selftest(report)
    print("\n" + ("✅ SELFTEST PASSED — safe to publish" if passed
                  else "❌ SELFTEST FAILED — review before publishing"))
    for it in issues:
        print("   • " + it)
    if args.selftest and not passed:
        sys.exit(1)   # non-zero so a publish/cron pipeline can gate on it
    return md_path


if __name__ == "__main__":
    main()
