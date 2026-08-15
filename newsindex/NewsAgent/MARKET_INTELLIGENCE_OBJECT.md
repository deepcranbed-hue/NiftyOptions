# MARKET_INTELLIGENCE_OBJECT (MIO)

The MIO is the **single standardized object** every downstream agent consumes. It is the
system's public contract: the Macro, Sector, Company, Risk, Strategy, Portfolio and Execution
agents never re-read the news — they act on MIOs. Machine-readable schema:
[`schemas/mio.schema.json`](schemas/mio.schema.json).

## 1. Design intent

- **Self-contained.** A downstream agent can act on an MIO without touching the graph or the
  source news.
- **Explainable by construction.** The causal chain, confidence decomposition, and driver
  attribution are *required* fields, not optional annotations.
- **Horizon-separated.** Impact is four fields (immediate/short/medium/structural), never one.
- **Auditable.** Every MIO carries the graph and regime version that produced it, plus source
  provenance, so any call is reproducible and traceable.

## 2. Minimal example (the spec's shape, preserved)

```json
{
  "event": "US CPI Higher Than Expected",
  "theme": "Inflation",
  "regime": "Inflation Shock",
  "transmission": ["Bond Yield", "USD", "Equity Valuation"],
  "affected_sectors": ["Banks", "Auto", "Real Estate"],
  "affected_companies": ["HDFC Bank", "Maruti", "DLF"],
  "expected_direction": { "Bond Yield": "Up", "USD": "Up", "Nifty": "Down" },
  "dominant_driver_score": 0.81,
  "historical_reliability": 0.63,
  "today_confidence": 0.87
}
```

Every downstream agent can rely on at least these fields. The full object below is a strict
superset — the minimal shape validates against the full schema.

## 3. Full MIO (institutional form)

```json
{
  "mio_id": "mio_2026-07-15T13:32:05Z_uscpi",
  "as_of": "2026-07-15T13:32:05Z",
  "graph_version": "kg_2026-07-15.114",
  "regime_version": "reg_2026-07-15.07",
  "degraded": false,

  "event": {
    "event_id": "evt_us_cpi_beat_2026_07",
    "canonical_label": "US CPI Higher Than Expected",
    "class": "Economic",
    "novelty": 0.9,
    "surprise": 0.62,
    "member_items": [
      { "source": "Reuters", "source_tier": 1, "url": "…", "ts_event": "2026-07-15T12:30:00Z" }
    ]
  },

  "theme": "Inflation",
  "regime": { "active": ["Inflation", "Risk-Off"], "primary": "Inflation", "confidence": 0.71 },

  "shock_type": "demand",

  "transmission": [
    { "chain": ["US CPI", "Bond Yield", "USD", "USDINR", "FII Flows", "Nifty"],
      "score": 0.78, "sign": -1, "mechanism": "higher yields → stronger USD → FII outflow → Nifty down" }
  ],

  "affected_sectors": [
    { "sector": "Banks",       "direction": "Down", "score": 0.66, "mechanism": "valuation de-rating on higher discount rate", "horizon_bias": "short" },
    { "sector": "Auto",        "direction": "Down", "score": 0.54, "mechanism": "rate-sensitive demand + financing cost" },
    { "sector": "Real Estate", "direction": "Down", "score": 0.61, "mechanism": "mortgage rate sensitivity" }
  ],

  "affected_companies": [
    { "company": "HDFC Bank", "direction": "Down", "score": 0.6, "nifty_weight": 0.11,
      "exposure_vector": { "interest_rate_sensitivity": "high", "fx_exposure": "low" },
      "analogues": [ { "event_ref": "us_cpi_beat_2024_03", "reaction": "-1.8% next session" } ] },
    { "company": "Maruti", "direction": "Down", "score": 0.5, "nifty_weight": 0.02 },
    { "company": "DLF",    "direction": "Down", "score": 0.55, "nifty_weight": 0.004 }
  ],

  "expected_direction": { "Bond Yield": "Up", "USD": "Up", "Nifty": "Down" },

  "impact": {
    "immediate":  { "direction": "Down", "magnitude": 0.6, "unit": "pct_nifty", "note": "knee-jerk on print" },
    "short":      { "direction": "Down", "magnitude": 0.9, "unit": "pct_nifty" },
    "medium":     { "direction": "Down", "magnitude": 0.4, "unit": "pct_nifty", "note": "partial fade if data one-off" },
    "structural": { "direction": "Neutral", "magnitude": 0.0, "unit": "pct_nifty" }
  },

  "validation": [
    { "edge": "US CPI→Nifty", "expected_sign": -1, "observed_sign": -1, "status": "CONFIRMED" }
  ],

  "confidence": {
    "econ_rationale_stars": 5,
    "historical_reliability": 0.63,
    "today_confidence": 0.87
  },

  "driver_dominance": {
    "vector": { "US CPI": 0.34, "FII": 0.22, "Oil": 0.11, "Earnings": 0.19, "VIX": 0.14 },
    "dominant_driver": "US CPI",
    "dominant_driver_score": 0.34
  },

  "provenance": { "pipeline_run": "run_9182", "core_version": "core_1.4.0" }
}
```

## 4. Field contract (required vs optional)

| Field | Required | Notes |
|---|---|---|
| `mio_id`, `as_of`, `graph_version`, `regime_version` | ✅ | auditability |
| `event` (with `canonical_label`, `class`) | ✅ | one canonical event |
| `theme`, `regime` | ✅ | regime drives edge conditioning |
| `transmission[]` (chain + score + sign + mechanism) | ✅ | the "why/how", explainable |
| `affected_sectors[]`, `affected_companies[]` | ✅ | sub-sector granularity required |
| `expected_direction{}` | ✅ | the spec's core map |
| `impact{}` (four horizons) | ✅ | never blended |
| `confidence{}` (the triple) | ✅ | no direction without confidence |
| `driver_dominance{}` (sums ≈ 1.0) | ✅ | what actually moved the tape |
| `validation[]` | ⭕ | present when a relationship was checked vs tape |
| `shock_type` | ⭕ | present for market/commodity events |
| `provenance` | ✅ | reproducibility |

## 5. Emission gate

The Orchestrator **blocks** any MIO that fails `schemas/mio.schema.json` (missing causal chain,
missing confidence triple, dominance not summing to ≈ 1.0, direction without confidence). A
blocked MIO is logged, never silently coerced — a malformed intelligence object is treated as
a bug, not a warning.

## 6. Consumption

Downstream agents subscribe to the Intelligence Bus and filter MIOs by any field —
`today_confidence ≥ 0.7`, `class = Policy`, `affected_sectors contains Banks`, etc. See
[AGENT_INTERACTION.md](AGENT_INTERACTION.md) for each downstream agent's contract.
