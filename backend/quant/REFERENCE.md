# backend/quant — Data Contracts (REFERENCE)

Representative shapes for the main quant boundaries. Keys marked *(nested)* live
under a parent object in the pipeline result.

## 1. `run_pipeline(...)` inputs

```python
run_pipeline(
  chain,            # dict: strikes[], call_ltp[], put_ltp[], call_oi[], put_oi[],
                    #       call_oi_chg_pct[], put_oi_chg_pct[], spot, days, r, pcr,
                    #       atm_iv, expiry   (days>0 — a T=0 chain is rejected)
  risk_cfg, book,               # risk gate + current positions
  news_state, flows_state, events_state, macro_state, cues_state,  # persisted state
  opt_weights, opt_bias, opt_min_pop, opt_allow_undefined, ...      # optimizer knobs
)
```
Exposed as `POST /api/run-pipeline` → `{ "success": true, "result": <below> }`.

## 2. `run_pipeline` result (top-level keys)

```jsonc
{
  "regime":    { "dominant": "RISK_OFF", "surfaces": ["DXY","Crude","US10Y"], ... },
  "momentum":  0.62,                       // news momentum 0..1
  "optimizer": { /* ranked strategy candidates + chosen */ },
  "complacency": { "components": [...] },
  "sizing":    { /* position sizing vs risk budget */ },
  "articles":  [ { "sentiment": 0.3, "sectors_affected": ["Financial Services"] } ],
  "cues":      { /* global-cues state — see §3 (this is `cues_state`) */ },
  "provenance": { /* §4 */ },
  "timestamps": { "news": "...", "flows": "...", "cues": "...", "chain": "..." },
  "chain_meta": { /* echo of the input chain */ },
  "conclusion": "...", "interpretations": { "breadth": "..." },
  "sector_sentiment": { "Financial Services": 0.2, ... }
}
```

## 3. Global cues (`global_cues.py` → `cues_state`, nested under `result.cues`)

```jsonc
{
  "cues":          { "DXY": -0.3, "Crude": 1.8, "US10Y": 2.1, ... },   // % / bp change
  "close_levels":  { "DXY": 104.2, "Crude": 82.5, ... },
  "session_states":{ "DXY": "LIVE", "Crude": "STALE", "DAX": "HOLIDAY",
                     "India 10Y": "CLOSED_FINAL" },   // LIVE|CLOSED_FINAL|STALE|HOLIDAY|ERROR
  "cue_as_of":     { "DXY": "2026-07-09T20:00:00+00:00", "Crude": "2026-07-07T18:30:00+00:00" },
  "strengths":     { "DXY": -0.42, "Crude": 0.71, ... },   // tanh(z/2), inverse-adjusted
  "curve_regime":  { "regime": "BULL_STEEPENER", "strength": 0.3, "note": "..." },
  "as_of": "2026-07-09T20:05:00+05:30"
}
```

**Session state** (`get_session_state`): `LIVE` = quote after the previous session;
`CLOSED_FINAL` = last session's close (normal when market closed); `STALE` = quote
*older* than the previous session (feed behind); `HOLIDAY` = not a session today;
`ERROR` = fetch failed. `STALE`/`HOLIDAY` cues are down-weighted ×0.25 in netting.

## 4. Provenance (`provenance.py`)

```jsonc
// one record per component
{ "component": "vix", "quality": "PARTIAL", "method": "news_sourced",
  "reason": "no live VIX; inferred from news", "detail": { "value": 13.4 } }

// rolled up: overall_provenance() → result.provenance
{ "overall": "PARTIAL", "headline": "DEGRADED", "degraded": ["vix","flows"],
  "records": [ /* the records above */ ] }
```

`quality ∈ { PRIMARY, PARTIAL, STALE, FALLBACK, UNAVAILABLE }` (rank in that order,
best→worst). Helpers: `primary()`, `partial()`, `stale()`, `fallback()`,
`unavailable()`, `state_provenance(component, age_seconds, budget_seconds, as_of)`
(→ PRIMARY within budget, else STALE with `age_s`/`as_of` in detail).

## 5. Cue-refresh endpoints

```
GET  /api/fetch-global-cues?force_refresh=bool   # fetch numbers (+ cache); does NOT write cues_state
POST /api/update-cues                            # force-fetch AND write cues_state (what the pipeline reads)
POST /api/run-pipeline                           # reads persisted state → result incl. provenance
```
To refresh the **provenance/freshness badges** you must `update-cues` (writes state)
then `run-pipeline` — a bare `fetch-global-cues` only updates the raw numbers.

## 6. Sector sentiment (`sector_tagging.py` → `sector_sentiment_from_gemini`)

Each canonical sector maps to a record (not a bare float), plus two `__`-prefixed
meta keys. **Consumers must skip ANY `"__"`-prefixed key** when iterating sectors.

```jsonc
{
  "Financials": {
    "combined": 0.21, "direct": 0.30, "derived": 0.12,   // score + its two legs
    "direct_n": 4, "derived_n": 2, "coverage": 0.63,
    "flag": "...",
    "low_confidence": false,          // true if <3 articles OR (PRIMARY weights & <15% coverage)
    "spread": 0.75,                   // max-min of contributing raw scores (disagreement)
    "estimator": "weighted_median",   // was weighted_mean (injection-fragile)
    "lambda": 0.7,
    "weights_as_of": "2026-06-30",    // real weights date (replaces hardcoded "2026-07-02")
    "weights_provenance": "PRIMARY",  // PRIMARY | UNAVAILABLE
    "as_of": "2026-07-18T06:00:00+00:00"   // actual run time, not a frozen string
  },
  // ... other sectors ...

  "__drilldown": { "Financials": { "Banks": { "HDFCBANK": 0.11, ... } } },
  "__audit": {                        // run-level trail — nothing dropped silently
    "as_of": "...", "n_in": 42, "n_scored": 33, "n_quarantined": 5,
    "quarantined": [ { "title": "...", "reason": "instruction_injection", "source": "..." } ],
    "n_clamped": 1,
    "tier_counts": { "established_wire": 20, "syndicated_pr": 3, "live_blog": 2 },
    "estimator": "weighted_median", "weights_as_of": "2026-06-30",
    "weights_provenance": "PRIMARY"
  }
}
```

Aggregation applies the `source_tier` multiplier (`news_provenance.py`), counts one
vote per article per sector, and clamps each score to `[-1,1]`. Tagged articles also
carry a `cluster_id` (`cluster_signature`) so cross-feed syndications of one event
collapse to a single cluster. Quarantine reasons: `role_token`,
`instruction_injection`, `zero_width_char`, `rtl_override`, `base64_blob`,
`anomalous_length`, `homoglyph_ratio`.
