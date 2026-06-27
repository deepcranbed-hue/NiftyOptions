# NiftyOptions Quant Engine (REFERENCE)
// This document outlines the exact JSON dictionary shapes at every module boundary.

## 1. Article Object Shape

### Post-RSS / Post-Windowing
```json
{
  "title": "Example Article Headline",
  "published_at": "2026-06-25T12:00:00Z",
  "source": "FeedName"  // Added during RSS parsing
}
```

### Post-Gemini Tagging
```json
{
  "title": "Example Article Headline",
  "published_at": "2026-06-25T12:00:00Z",
  "source": "FeedName",
  "sentiment": 0.85,             // Float between -1.0 and 1.0
  "sectors_affected": ["IT", "FINANCE"], // Must match CANONICAL_SECTORS keys
  "confidence": 0.9              // Float between 0.0 and 1.0
}
```

## 2. Option Chain Object
```json
{
  "spot": 24500.0,               // Current NIFTY Spot Price
  "days": 5.0,                   // Days To Expiry
  "r": 0.0655,                   // Risk-free rate (optional, default 6.55%)
  "strikes": [24000, 24100, 24200], // Array of strike prices
  "call_ltp": [550.5, 470.0, 395.2], // Array of Call Last Traded Prices
  "put_ltp": [50.1, 75.2, 105.0]     // Array of Put Last Traded Prices (REQUIRED for RND)
}
```

## 3. Pipeline Result Object (`run_pipeline`)
```json
{
  "regime": {
    "dominant": "fear",          // From Driver enum (e.g. 'fear', 'fomo', 'complacency')
    "conviction": 0.85,            // Float (0 to 1) representing momentum strength
    "flipped_from": "fomo",      // Previous driver, if a transition just occurred
    "surfaces": ["NIFTY_IT", "NIFTY_FIN"], // List of corroborating sectors
    "vol_expansion": true          // Boolean indicating high vol state
  },
  "sector_sentiment": {
    "IT": 0.5,
    "FINANCE": -0.2
    // Key-value pairs for all canonical sectors
  },
  "sector_weights": {
    "IT": 13.5,
    "FINANCE": 33.2
    // Seed weights utilized during computation
  },
  "bias": 0.15,                    // Weighted aggregate sentiment score
  "coverage": 0.45,                // Percentage of NIFTY weight represented in recent news
  "momentum": 0.75,                // Heuristic momentum scalar
  "rnd": {
    "grid": [23000, 23100, ...],   // Array of spot prices for the distribution curve
    "dens": [0.001, 0.002, ...],   // Probability density at each grid point
    "p_below_spot": 0.48,          // Aggregate probability mass below current spot
    "p_above_spot": 0.52,          // Aggregate probability mass above current spot
    "sd": 450.5,                   // Standard deviation of the RND
    "skew": -0.15,                 // Skewness of the distribution
    "spot": 24500.0                // Echo of the provided spot
  },
  "comparison": {
    "match": false,
    "explanation": "Regime is bearish, but Options flow suggests bullish structure."
  },
  "suggestion": [
    "Consider Bear Call Spreads if fading the flow",
    "Consider Iron Condors if betting on reversion"
  ],
  "complacency": {
    "score": 65.0,
    "label": "NEUTRAL",
    "vol_state_hint": "range"
  },
  "sizing": {
    "approved": true,
    "lots": 2,
    "binding_constraint": "net_delta",
    "max_loss_per_lot_rs": 4500,
    "trade_max_loss_rs": 9000,
    "trade_risk_pct": 0.009
  },
  "articles": [
    // Array of Post-Gemini Tagging objects representing the exact feed used
  ]
}
```

## 4. Global Cues API Response (`/api/fetch-global-cues`)
```json
{
  "success": true,
  "cues": {
    "S&P 500": 1.2,
    "NASDAQ": 1.5,
    "US 10Y Yield": -0.05,
    "Dollar Index (DXY)": 0.2,
    "Brent Crude": 0.0,
    "Hang Seng": -1.1
  }
}
```

## 5. API Endpoints

- **`POST /api/run-pipeline`**
  - **Request Body:** `{ "chain": { ...Option Chain Object }, "prev_regime": "fomo" (optional), "half_life_hours": 12.0 }`
  - **Response:** `{ "success": true, "result": { ...Pipeline Result Object } }`

## 5. Sector Map Dictionary (`sector_map.py`)
```json
{
  "FINANCE": {
    "weight": 33.2,
    "keywords": ["bank", "nbfc", "hdfc", "sbi", "finance"]
  },
  // ... other canonical sectors
}
```

## 6. Risk Budget and Complacency (Data Shapes)

### ChainComplacencyInputs
```json
{
  "atm_iv": 0.093,
  "iv_percentile": 0.18,
  "put_oi_chg_pct_atm": 130.0,
  "put_call_oi_ratio": 0.92,
  "skew": -0.29,
  "vix": 12.9,
  "vix_chg_pct": -3.4
}
```

### RiskConfig
```json
{
  "capital": 1000000.0,
  "risk_per_trade_pct": 0.015,
  "max_portfolio_heat_pct": 0.06,
  "max_net_delta_units": 150.0,
  "max_net_vega_rupees": 50000.0,
  "max_drawdown_pct": 0.10,
  "lot_size": 65,
  "complacency_block": 70.0,
  "complacency_halve": 55.0
}
```

### Trade
```json
{
  "structure": "Bull put spread",
  "max_loss_pts": 56.0,
  "delta_per_lot": 18.0,
  "vega_per_lot": -1800.0,
  "is_premium_sell": true
}
```
