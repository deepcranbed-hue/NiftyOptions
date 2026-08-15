# Reference

This file documents the exact data shapes and contracts at every major module boundary in the Antigravity Nifty Options Quant system.

## 1. Article (Input / News Processing)
**Post-RSS / Pre-Gemini**:
```json
{
  "title": "KOSPI meltdown drags Indian indices...",
  "description": "Short snippet from RSS...",
  "published_at": "2026-06-23T14:00:00+00:00",
  "source": "News API"
}
```

**Post-Gemini Tagging**:
```json
{
  "title": "KOSPI meltdown drags Indian indices...",
  "description": "Short snippet from RSS...",
  "published_at": "2026-06-23T14:00:00+00:00",
  "source": "News API",
  "sentiment": -0.8,
  "sectors_affected": ["IT", "Banks"],
  "cluster_id": "a1b2c3d4e5f60718", // cross-feed dedup signature — syndications of one event collapse to one cluster
  "event_code": "US_FOMC", // optional
  "event_consensus": "Fed likely to hold" // optional
}
```

## 2. Option Chain (Input)
```json
{
  "strikes": [23750, 23800, 23850],
  "call_ltp": [478.60, 430.00, 383.85],
  "put_ltp": [3.95, 5.20, 6.30], // required for RND
  "put_oichg": [-500, 1000, 2500], 
  "spot": 24200.0,
  "days": 7.0,
  "r": 0.0655,
  "atm_iv": 15.0,
  "put_call_oi_ratio": 1.2,
  "iv_percentile": 85.0,
  "vix": 14.5,
  "vix_chg_pct": 2.5
}
```

## 3. Global Cues (Input / Output)
```json
{
  "name": "US10Y",
  "price": "4.25",
  "change_pct": 1.5,
  "bias": -1.0,
  "narrative": "Yields surging puts pressure on emerging market equities.",
  "metals_barometer": {
    "growth_signal": 0.5,
    "fear_signal": -0.2,
    "regime": "risk_on_growth",
    "note": "Copper firm, gold soft...",
    "metals_sector_tilt": 0.6,
    "formula_trace": {
      "formula": "growth = clip(cu×0.7 + ag×0.3)...",
      "subbed": "growth = clip(0.8×0.7 + ...)...",
      "meaning": "Copper/Silver track global growth optimism..."
    }
  }
}
```

## 4. Run Pipeline Result (The core output to frontend)

> **News meta-keys:** `sector_sentiment` (and `drilldown`) may carry non-sector meta keys — `__drilldown` and the hardened tagger's `__audit` trail — plus a per-sector `low_confidence` flag. Consumers MUST skip any `"__"`-prefixed key (skipping only `__drilldown` previously let `__audit` crash the sector render to a partial ~4-sector view). Full news-tagging shape: [backend/quant/REFERENCE.md](backend/quant/REFERENCE.md).

```json
{
  "regime": {
    "dominant": "geopolitics_oil",
    "conviction": 0.85,
    "flipped_from": "ai_semi",
    "surfaces": ["crude", "defense"],
    "vol_expansion": true
  },
  "sector_sentiment": {
    "Financials": 0.85,
    "Information Technology": -0.4
  },
  "drilldown": {
    "Financials": {
      "Private Banks": {
        "HDFC Bank": 0.85
      }
    }
  },
  "sector_weights": {
    "Banks": 0.35,
    "IT": 0.15
  },
  "bias": -0.45,
  "coverage": 0.60,
  "momentum": 0.75,
  "rnd": {
    "grid": [23000.0, 23050.0],
    "dens": [0.001, 0.002],
    "p_below_spot": 0.65,
    "p_above_spot": 0.35,
    "sd": 150.0,
    "skew": -0.25,
    "spot": 24200.0
  },
  "comparison": {
    "relation": "alignment", // alignment | divergence
    "news_state": "bearish_momentum",
    "market_state": "pricing_downside",
    "flow_divergence": null // string if divergence exists
  },
  "suggestion": {
    "action": "TRADE",
    "structure": "bear put spread",
    "why": "Aligned bearish sentiment with downside skew."
    // Note: The frontend dynamically injects `maxProfit`, `maxLoss`, `netPremium`, and `breakevens` into this object in real-time via `analytics.ts` based on the user's custom strike selections.
  },
  "complacency": {
    "score": 85.0,
    "vol_state_hint": "expansion",
    "warnings": ["Low IV but high skew"]
  },
  "sizing": {
    "approved": true,
    "qty_lots": 10,
    "reason": "Proceed",
    "portfolio_heat_pct": 0.05
  },
  "articles": [],
  "formulas": {
    "bias": {
      "formula": "bias = Σ(sectorᵢ_score × sectorᵢ_weight) / Σ(covered weight)",
      "subbed": "[Banks(-0.20 × 35.0%)] / 60.0% = -0.45",
      "meaning": "Index bias is the weighted sum of sentiment across active sectors."
    },
    "complacency": {},
    "rnd": {},
    "sizing": {}
  }
}
```

## 5. Event Calendar (Output)
```json
{
  "events": [
    {
      "code": "IN_CPI",
      "name": "India CPI (retail inflation)",
      "date": "2026-07-13",
      "impact": "high",
      "sector_focus": "Banks, Financials, Auto, FMCG",
      "consensus": "CPI seen easing to 4.1%",
      "source": "ET Markets",
      "stale": false
    }
  ],
  "proximity": {
    "nearest_high_impact": "IN_CPI",
    "name": "India CPI (retail inflation)",
    "days_away": 2,
    "consensus": "CPI seen easing to 4.1%",
    "action": "caution_downsize", // normal | caution_downsize | block_premium_sell
    "note": "India CPI (retail inflation) in 2d — downsize premium-selling..."
  },
  "us_macro": {
    "net_tilt": -0.5,
    "positive_forces": ["OIL falling (crude down 2% - via Reuters)"],
    "negative_forces": ["INFLATION hot (PCE at 4.1% - via WSJ)", "FED hawkish (hike on - via Bloomberg)"],
    "sector_notes": ["US Tech is weak -> drag on NIFTY IT."],
    "as_of": "2026-06-28T05:00:00Z",
    "has_data": true
  }
}
```

```json
"provenance": {
  "overall": "PRIMARY | PARTIAL | FALLBACK | STALE | UNAVAILABLE",
  "headline": "DATA QUALITY: DEGRADED — 1 of 5 components on fallback",
  "degraded": [
    {
      "component": "sentiment",
      "quality": "FALLBACK",
      "method": "keyword_fallback",
      "reason": "Gemini unavailable...",
      "detail": {}
    }
  ],
  "records": []
}
```


## 11. Vol Attribution
**VixReading (from news)**:
```json
{
  "value": 13.05,
  "as_of": "2026-06-27T04:30:00+00:00",
  "source": "HDFC Sky",
  "age_hours": 4.5,
  "stale": false,
  "note": "India VIX 13.05 from news (HDFC Sky), ~4h old..."
}
```

**Attribute Vol Output**:
```json
{
  "chain_atm_iv_pct": 17.0,
  "india_vix": 13.0,
  "iv_vs_vix_gap": 4.0,
  "days_to_expiry": 1,
  "primary_cause": "expiry_mechanics",
  "causes": [
    {
      "cause": "expiry_mechanics",
      "detail": "Chain ATM IV 17% >> India VIX 13%...",
      "harvestable": false,
      "warning": "Selling this IV = short gamma into the highest-gamma day..."
    }
  ],
  "sell_premium_verdict": "CAUTION — IV is elevated for mechanical/event reasons..."
}
```

## 12. NSE CSV Loader
**`load_nse_csv(path: str, spot: float, days: float, lot_size: int = 65)`**:
Reads an NSE export CSV (with thousands-separators, blank markers like '-') and constructs the standard `chain` dictionary (schema 2) used by the pipeline. `spot` and `days` are required parameters.

**`add_oi_change_pct(chain: dict)`**:
Computes `call_oi_chg_pct` and `put_oi_chg_pct` arrays in-place from absolute `_chg` and current `_oi`.

**`window_chain(chain: dict, band_pts: float = 1200)`**:
Returns a new filtered chain retaining only strikes within `band_pts` of `spot`. Critical to strip deep OTM noise before passing to RND generation.

## 13. Flows State (`.state/flows_state.json`)
```json
{
  "success": true,
  "bias": {
    "trend": 0.5,
    "confidence": 0.8
  },
  "cash_stale": false,
  "sip_stale": false,
  "fpi_stale": false,
  "sector_fpi": {
    "Banks": 1500.0,
    "IT": -200.0,
    "FMCG": 50.0
  },
  "flow_tilt": 0.65,
  "formula_trace": [
    "cash_flow = FPI(1200) + DII(800) = 2000 Cr",
    "sip_flow = 19000 Cr / 20 days = 950 Cr/day",
    "Net flow tilt = +0.65 (Bullish)"
  ],
  "bond_cues": null,
  "fii_disambiguation": "FPIs buying financials, selling IT."
}
```

## 14. Cues State (`.state/cues_state.json`)
```json
{
  "us10y": {
    "name": "US10Y",
    "price": "4.25",
    "change_pct": 1.5,
    "bias": -1.0,
    "narrative": "Yields surging puts pressure on emerging market equities."
  },
  "dxy": {
    "name": "DXY",
    "price": "104.50",
    "change_pct": 0.2,
    "bias": -0.5,
    "narrative": "Strong dollar reduces foreign inflows."
  },
  "metals": {
    "growth_signal": 0.5,
    "fear_signal": -0.2,
    "regime": "risk_on_growth",
    "note": "Copper firm, gold soft...",
    "metals_sector_tilt": 0.6,
    "formula_trace": {
      "formula": "growth = clip(cu×0.7 + ag×0.3)...",
      "subbed": "growth = clip(0.8×0.7 + ...)...",
      "meaning": "Copper/Silver track global growth optimism..."
    }
  }
}
```

## 15. Breadth Interpretation Output
Returned dynamically as part of `pipelineRes.interpretations.breadth` from `/api/run-pipeline`.
```json
{
  "regime": "ROTATION_UP_INDEX",
  "read": "HDFCBANK (-0.4%) and INFY (-1.2%) are dragging, but RELIANCE (0.5%) and SBIN (1.1%) are holding the index up. Narrow breadth driving index gains."
}
```

## 16. Session Cache Schema (Local Fallback Stores)

### Zerodha Kite Session (`zerodhasession/session_YYYY-MM-DD.json`)
```json
{
  "user_type": "individual/res_no_nn",
  "email": "user@email.com",
  "user_name": "Full Name",
  "user_id": "ABC123",
  "api_key": "x2ob63qqr9dhyj6o",
  "access_token": "PDOd2Y9w0eSr8ERgHnrgAtULDnuAxfD9",
  "public_token": "mq65pDBCHkZq7eo...",
  "login_time": "2026-07-07 10:13:14"
}
```

### ICICI Breeze Session (`breezesession/session_YYYY-MM-DD.json`)
```json
{
  "session_token": "56232089",
  "validated_at": "2026-07-07T13:29:46.123456"
}
```

## Environment Isolation Rule
**CRITICAL**: NEVER use `pip install` globally when working in this project. All new GenAI scripts or external tools (like CMBS Frameworks) must be run in completely isolated virtual environments to prevent catastrophic dependency conflicts (e.g., breaking `cryptography`) that crash the Uvicorn server.
