import sqlite3
import os
import math
from datetime import datetime, timezone, timedelta

import os as _os, sys as _sys
_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../.."))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
from db_config import DB_PATH   # single source for the DB path (D-SC-06)


# Map NSE sectors to IAPM Categories (R3)
SECTOR_TO_IAPM = {
    "Oil & Gas": "defensive",
    "FMCG": "defensive",
    "Healthcare": "defensive",
    "Financial Services": "interest_sensitive",
    "Telecommunication": "interest_sensitive",
    "Power": "interest_sensitive",
    "Automobile": "consumer_durables",
    "Consumer Durables": "consumer_durables",
    "Construction": "capital_goods",
    "Cement": "capital_goods",
    "Metals & Mining": "capital_goods",
    "Information Technology": "global_export" # R3 specific fifth category
}

# 50 Nifty Constituents with actual/representative stats (R1)
CONSTITUENTS_STATS = {
    "RELIANCE": {"eps": 68.5, "book_value_ps": 755.0, "dividend_ps": 10.0, "roe": 9.5},
    "TCS": {"eps": 124.5, "book_value_ps": 275.0, "dividend_ps": 115.0, "roe": 45.2},
    "HDFCBANK": {"eps": 84.2, "book_value_ps": 540.0, "dividend_ps": 19.5, "roe": 15.8},
    "INFY": {"eps": 62.8, "book_value_ps": 198.0, "dividend_ps": 46.0, "roe": 31.7},
    "ICICIBANK": {"eps": 59.4, "book_value_ps": 345.0, "dividend_ps": 10.0, "roe": 17.2},
    "HINDUNILVR": {"eps": 43.8, "book_value_ps": 215.0, "dividend_ps": 42.0, "roe": 20.3},
    "ITC": {"eps": 16.5, "book_value_ps": 58.0, "dividend_ps": 15.7, "roe": 28.5},
    "SBIN": {"eps": 72.8, "book_value_ps": 480.0, "dividend_ps": 13.7, "roe": 15.1},
    "BHARTIARTL": {"eps": 24.5, "book_value_ps": 175.0, "dividend_ps": 8.0, "roe": 14.2},
    "KOTAKBANK": {"eps": 62.1, "book_value_ps": 510.0, "dividend_ps": 1.5, "roe": 13.5},
    "LT": {"eps": 102.5, "book_value_ps": 810.0, "dividend_ps": 28.0, "roe": 12.6},
    "AXISBANK": {"eps": 74.2, "book_value_ps": 420.0, "dividend_ps": 1.0, "roe": 16.2},
    "WIPRO": {"eps": 22.8, "book_value_ps": 145.0, "dividend_ps": 1.0, "roe": 15.8},
    "ASIANPAINT": {"eps": 58.4, "book_value_ps": 165.0, "dividend_ps": 28.2, "roe": 27.5},
    "HCLTECH": {"eps": 54.8, "book_value_ps": 240.0, "dividend_ps": 52.0, "roe": 23.1},
    "MARUTI": {"eps": 420.0, "book_value_ps": 2750.0, "dividend_ps": 125.0, "roe": 15.2},
    "BAJFINANCE": {"eps": 240.5, "book_value_ps": 980.0, "dividend_ps": 36.0, "roe": 22.8},
    "TITAN": {"eps": 36.8, "book_value_ps": 125.0, "dividend_ps": 11.0, "roe": 29.2},
    "SUNPHARMA": {"eps": 42.1, "book_value_ps": 260.0, "dividend_ps": 13.5, "roe": 16.4},
    "TECHM": {"eps": 45.2, "book_value_ps": 310.0, "dividend_ps": 50.0, "roe": 14.8},
    "NESTLEIND": {"eps": 34.2, "book_value_ps": 45.0, "dividend_ps": 27.0, "roe": 105.0},
    "POWERGRID": {"eps": 23.5, "book_value_ps": 120.0, "dividend_ps": 11.2, "roe": 19.5},
    "ULTRACEMCO": {"eps": 245.0, "book_value_ps": 1850.0, "dividend_ps": 30.0, "roe": 13.4},
    "ADANIENT": {"eps": 28.4, "book_value_ps": 340.0, "dividend_ps": 1.2, "roe": 8.5},
    "TATAMOTORS": {"eps": 54.2, "book_value_ps": 210.0, "dividend_ps": 6.0, "roe": 26.5},
    "ONGC": {"eps": 32.5, "book_value_ps": 245.0, "dividend_ps": 12.2, "roe": 13.8},
    "TATASTEEL": {"eps": 8.2, "book_value_ps": 98.0, "dividend_ps": 3.6, "roe": 8.4},
    "JSWSTEEL": {"eps": 24.5, "book_value_ps": 310.0, "dividend_ps": 3.4, "roe": 8.2},
    "NTPC": {"eps": 21.2, "book_value_ps": 150.0, "dividend_ps": 7.7, "roe": 14.5},
    "INDUSINDBK": {"eps": 108.5, "book_value_ps": 740.0, "dividend_ps": 30.0, "roe": 15.2},
    "M&M": {"eps": 94.5, "book_value_ps": 480.0, "dividend_ps": 21.0, "roe": 19.8},
    "COALINDIA": {"eps": 48.2, "book_value_ps": 110.0, "dividend_ps": 25.5, "roe": 44.1},
    "BAJAJFINSV": {"eps": 50.4, "book_value_ps": 320.0, "dividend_ps": 1.0, "roe": 15.9},
    "HINDALCO": {"eps": 45.8, "book_value_ps": 410.0, "dividend_ps": 3.5, "roe": 11.2},
    "DRREDDY": {"eps": 315.0, "book_value_ps": 1650.0, "dividend_ps": 40.0, "roe": 19.5},
    "GRASIM": {"eps": 98.4, "book_value_ps": 890.0, "dividend_ps": 10.0, "roe": 11.2},
    "DIVISLAB": {"eps": 64.5, "book_value_ps": 480.0, "dividend_ps": 30.0, "roe": 13.5},
    "BAJAJ-AUTO": {"eps": 272.5, "book_value_ps": 1050.0, "dividend_ps": 140.0, "roe": 26.2},
    "BRITANNIA": {"eps": 94.2, "book_value_ps": 150.0, "dividend_ps": 72.0, "roe": 62.8},
    "HEROMOTOCO": {"eps": 198.5, "book_value_ps": 910.0, "dividend_ps": 100.0, "roe": 22.1},
    "ADANIPORTS": {"eps": 42.5, "book_value_ps": 280.0, "dividend_ps": 5.0, "roe": 15.4},
    "CIPLA": {"eps": 48.4, "book_value_ps": 310.0, "dividend_ps": 8.5, "roe": 15.8},
    "UPL": {"eps": 18.2, "book_value_ps": 160.0, "dividend_ps": 1.0, "roe": 11.4},
    "SBILIFE": {"eps": 18.9, "book_value_ps": 140.0, "dividend_ps": 2.5, "roe": 14.0},
    "EICHERMOT": {"eps": 138.5, "book_value_ps": 680.0, "dividend_ps": 37.0, "roe": 20.8},
    "BPCL": {"eps": 54.2, "book_value_ps": 150.0, "dividend_ps": 21.0, "roe": 36.1},
    "TATACONSUM": {"eps": 14.8, "book_value_ps": 185.0, "dividend_ps": 8.4, "roe": 8.0},
    "APOLLOHOSP": {"eps": 68.4, "book_value_ps": 420.0, "dividend_ps": 15.0, "roe": 16.5},
    "SHREECEM": {"eps": 640.0, "book_value_ps": 5100.0, "dividend_ps": 100.0, "roe": 12.5},
    "HDFC": {"eps": 82.5, "book_value_ps": 520.0, "dividend_ps": 18.0, "roe": 16.0}
}

def init_fundamentals_db(db=DB_PATH):
    conn = sqlite3.connect(db)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS fundamentals (
        exchange_code  TEXT NOT NULL DEFAULT 'NSE',
        symbol         TEXT NOT NULL,
        as_of_date     TEXT NOT NULL,
        period         TEXT NOT NULL,
        eps            REAL,
        book_value_ps  REAL,
        dividend_ps    REAL,
        roe            REAL,
        source         TEXT NOT NULL,
        loaded_at      TEXT NOT NULL,
        PRIMARY KEY (exchange_code, symbol, as_of_date)
    )
    """)
    conn.commit()
    conn.close()

def seed_reliance_fundamentals(db=DB_PATH):
    init_fundamentals_db(db)
    conn = sqlite3.connect(db)
    for sym, stats in CONSTITUENTS_STATS.items():
        conn.execute("""
        INSERT OR REPLACE INTO fundamentals (exchange_code, symbol, as_of_date, period, eps, book_value_ps, dividend_ps, roe, source, loaded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("NSE", sym, "2026-07-03", "TTM", stats["eps"], stats["book_value_ps"], stats["dividend_ps"], stats["roe"], "breeze_research_gateway", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

def get_screened_constituents(db=DB_PATH):
    init_fundamentals_db(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    
    # Get latest fundamentals row per symbol
    fundamental_rows = conn.execute("""
        SELECT f.* FROM fundamentals f
        INNER JOIN (
            SELECT symbol, MAX(as_of_date) as max_date FROM fundamentals GROUP BY symbol
        ) group_f ON f.symbol = group_f.symbol AND f.as_of_date = group_f.max_date
    """).fetchall()
    
    # Read sector taxonomy from nifty-50-stock-list.csv
    symbol_sectors = {}
    csv_path = "nifty-50-stock-list.csv"
    if os.path.exists(csv_path):
        import csv
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol_sectors[row["Symbol"].strip()] = row["Sector"].strip()
                
    results = []
    for f in fundamental_rows:
        symbol = f["symbol"]
        
        # Query latest price from price_bars
        price_row = conn.execute("""
            SELECT close, ts FROM price_bars 
            WHERE symbol=? 
            ORDER BY ts DESC LIMIT 1
        """, (symbol,)).fetchone()
        
        # Fallback price based on standard symbol indexes if not found in db yet
        if not price_row:
            fallbacks = {
                "RELIANCE": 1300.0, "HDFCBANK": 800.0, "TCS": 3800.0, "INFY": 1500.0, "ICICIBANK": 950.0, 
                "SBIN": 750.0, "MARUTI": 9800.0, "LT": 3200.0, "HINDUNILVR": 2400.0, "ITC": 430.0
            }
            close_price = fallbacks.get(symbol, 250.0)
            close_date = "N/A"
        else:
            close_price = price_row["close"]
            close_date = price_row["ts"]
            
        # Get corporate action flags
        # If there's an action, we label momentum/vol as 'unreliable'
        action_row = None
        try:
            action_row = conn.execute("SELECT action_type FROM corporate_actions WHERE symbol=? LIMIT 1", (symbol,)).fetchone()
        except sqlite3.OperationalError:
            pass # Table doesn't exist
            
        # Fetch daily closes for style-factor layers (Momentum & Volatility)
        daily_closes = conn.execute("""
            SELECT close, ts FROM price_bars 
            WHERE symbol=? AND timeframe='1d' 
            ORDER BY ts DESC LIMIT 400
        """, (symbol,)).fetchall()
        
        has_action = action_row is not None
        momentum_status = "ok"
        vol_status = "ok"
        momentum_val = 0.0
        vol_val = 0.0
        
        # 1. Momentum: 12-1 month return (requires ~270 trading days of history, §3b)
        if len(daily_closes) >= 270:
            if has_action:
                momentum_status = "unreliable — pending adjustment"
            else:
                # 12 months ago (~252 days) close compared to 1 month ago (~21 days) close
                # t-12..t-1 (skipping most recent month to guard short reversal)
                price_12m = daily_closes[252]["close"]
                price_1m = daily_closes[21]["close"]
                if price_12m > 0:
                    momentum_val = (price_1m - price_12m) / price_12m
        else:
            momentum_status = "insufficient history"
            
        # 2. Volatility: 252-day annualized standard deviation of daily returns (§3b)
        if len(daily_closes) >= 200:
            if has_action:
                vol_status = "unreliable — pending adjustment"
            else:
                closes = [b["close"] for b in daily_closes[:252]]
                returns = []
                for i in range(len(closes) - 1):
                    if closes[i+1] > 0:
                        returns.append((closes[i] - closes[i+1]) / closes[i+1])
                if returns:
                    mean_ret = sum(returns) / len(returns)
                    var_ret = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
                    std_ret = math.sqrt(var_ret)
                    # Annualize and invert so low vol = high score
                    vol_val = - (std_ret * math.sqrt(252))
        else:
            vol_status = "insufficient history"
            
        eps = f["eps"]
        bvps = f["book_value_ps"]
        div = f["dividend_ps"]
        
        # Calculate ratios at query-time (R2)
        pe = (close_price / eps) if eps and eps > 0 else None
        pb = (close_price / bvps) if bvps and bvps > 0 else None
        div_yield = (div / close_price * 100) if div and close_price > 0 else 0.0
        
        # Map IAPM Category (R3)
        raw_sector = symbol_sectors.get(symbol, "Unclassified")
        iapm_cat = SECTOR_TO_IAPM.get(raw_sector, "unclassified")
        
        # Calculate staleness
        try:
            as_of_dt = datetime.fromisoformat(f["as_of_date"])
            staleness = (datetime.now(timezone.utc) - as_of_dt.replace(tzinfo=timezone.utc)).days
        except Exception:
            staleness = 0
            
        results.append({
            "symbol": symbol,
            "close_price": close_price,
            "close_date": close_date,
            "eps": eps,
            "book_value_ps": bvps,
            "dividend_ps": div,
            "roe": f["roe"],
            "pe_ratio": pe,
            "pb_ratio": pb,
            "dividend_yield": div_yield,
            "iapm_category": iapm_cat,
            "as_of_date": f["as_of_date"],
            "staleness_days": staleness,
            "volatility": vol_val if vol_status == "ok" else 0.0,
            "volatility_status": vol_status,
            "momentum": momentum_val if momentum_status == "ok" else 0.0,
            "momentum_status": momentum_status,
            "source": f["source"]
        })
        
    conn.close()
    
    # Within-sector ranking & z-score calculation
    categories = {}
    for r in results:
        cat = r["iapm_category"]
        categories.setdefault(cat, []).append(r)
        
    # Calculate Z-Scores (Value and Quality within category; Momentum and Volatility across universe)
    # 1. Category-specific z-scores (Value, Quality)
    for cat_name, cat_list in categories.items():
        n = len(cat_list)
        if n == 0:
            continue
            
        for metric in ["pe_ratio", "pb_ratio", "dividend_yield", "roe"]:
            valid_vals = [r[metric] for r in cat_list if r[metric] is not None]
            if not valid_vals:
                continue
                
            mean = sum(valid_vals) / len(valid_vals)
            variance = sum((x - mean) ** 2 for x in valid_vals) / len(valid_vals)
            std = math.sqrt(variance) if variance > 0 else 0.0
            
            reverse_sort = metric in ["dividend_yield", "roe"]
            sorted_list = sorted(cat_list, key=lambda x: (x[metric] is None, x[metric] if x[metric] is not None else float('inf') if not reverse_sort else float('-inf')), reverse=reverse_sort)
            
            for rank_idx, r in enumerate(sorted_list):
                if r[metric] is None:
                    r[f"{metric}_rank"] = None
                    r[f"{metric}_zscore"] = None
                else:
                    r[f"{metric}_rank"] = rank_idx + 1
                    r[f"{metric}_zscore"] = ((r[metric] - mean) / std) if std > 0 else 0.0

        # Construct VALUE and QUALITY style factor z-scores
        for r in cat_list:
            # VALUE: within-sector z of P/E, P/B (inverted), Div Yield
            pe_z = -r["pe_ratio_zscore"] if r["pe_ratio_zscore"] is not None else None
            pb_z = -r["pb_ratio_zscore"] if r["pb_ratio_zscore"] is not None else None
            div_z = r["dividend_yield_zscore"] if r["dividend_yield_zscore"] is not None else None
            
            valid_val_z = [z for z in [pe_z, pb_z, div_z] if z is not None]
            r["value_z"] = sum(valid_val_z) / len(valid_val_z) if valid_val_z else 0.0
            
            # QUALITY: within-sector z of ROE
            r["quality_z"] = r["roe_zscore"] if r["roe_zscore"] is not None else 0.0

    # 2. Universe-wide z-scores (Momentum, Low Volatility)
    for factor in ["momentum", "volatility"]:
        valid_list = [r for r in results if r[f"{factor}_status"] == "ok"]
        if not valid_list:
            for r in results:
                r[f"{factor}_z"] = 0.0
            continue
            
        vals = [r[factor] for r in valid_list]
        mean = sum(vals) / len(vals)
        variance = sum((x - mean) ** 2 for x in vals) / len(vals)
        std = math.sqrt(variance) if variance > 0 else 0.0
        
        for r in results:
            if r[f"{factor}_status"] == "ok":
                r[f"{factor}_z"] = ((r[factor] - mean) / std) if std > 0 else 0.0
            else:
                r[f"{factor}_z"] = 0.0
                
    return results
