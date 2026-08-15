# Quant Module Details (backend/quant/SKILL)

See `README.md` (module map + pipeline overview) and `REFERENCE.md` (data shapes).

## Execution Order (`run_pipeline`)
The pipeline runs in a strict sequential order:
0. **Complacency & Risk:** `complacency.py` and `risk_budget.py` act as the chain gauge and risk gate, respectively.
1. **Regime Assessment:** `assess_regime()` identifies the dominant sentiment driver from the latest news window.
2. **Entity & Sector Sentiment Aggregation:** `gemini_tag_batch()` pulls canonical sectors AND extracts named constituents (via `entity_extract.py`). `sector_sentiment_from_gemini()` aggregates these, weighting named constituents by their index weight (falling back to canonical tags for unnamed headlines). Aggregation now uses a **weighted MEDIAN** (not mean), applies the `source_tier` trust multiplier, counts one vote per article per sector, and clamps each score to `[-1,1]` — see "News provenance & tagging guardrails" below.
3. **Index Bias & Coverage:** `index_bias()` maps sector sentiment against NIFTY50 `sector_weights()` to compute a net float and % coverage.
4. **News Momentum:** Momentum scaling (0.0 to 1.0) is heuristically derived from conviction and corroborating news surfaces.
5. **RND Extraction:** `extract_rnd()` runs the raw option chain through the scipy trapezoid rule to generate the `grid` and `dens`.
6. **Market View Synthesis:** `compare_with_regime()` and `suggest_strategy()` finalize the output based on regime vs bias.

## Required Caller Inputs
When calling `run_pipeline(articles, chain, weights=None, prev_regime=None)`, the caller must supply:
- **`articles`**: A list of dictionaries, each containing at minimum `sentiment` (float) and `sectors_affected` (list of strings).
- **`chain`**: A dictionary containing `strikes`, `call_ltp`, `put_ltp`, `spot`, and `days`.
- **`prev_regime`** (Optional): A string representing the previous regime state to enable hysteresis.

## Mathematical Caveats
- **RND Integration:** `rnd.py` uses a NumPy version-safe `_trap` wrapper (`getattr(np, "trapezoid", getattr(np, "trapz", None))`). The algorithm calculates valid skew *only* when Out-Of-The-Money (OTM) Put prices are provided. Do NOT use call-put parity to synthesize missing puts if the market is illiquid.
- **Sector Source of Truth:** `sector_map.py` is the absolute source of truth. If a Gemini tag does not match a key in `sector_map.py`, its weighting is effectively zeroed (or grouped to OTHER).
- **Pseudo-Probability:** The `prob_up` calculation in `decision_engine.py` is a heuristic pseudo-probability mapping. It is not an empirically calibrated win rate.
- **Global Cues Timing:** `global_cues.py` provides EOD comparisons and intraday futures momentum, but it sits *outside* the main option flow pipeline.

## Provenance & Data Freshness (the trust layer)
- **Every input is labelled.** `provenance.py` attaches a `Provenance{component, quality, method, reason, detail}` to each component; `quality ∈ {PRIMARY, PARTIAL, STALE, FALLBACK, UNAVAILABLE}` (best→worst). `overall_provenance()` rolls them into `{overall, headline, degraded, records}` for the `ProvenanceBadge` UI. **Never silently upgrade** a FALLBACK/STALE value to PRIMARY — the badge must reflect reality.
- **Global-cues freshness is a separate per-ticker axis.** `get_session_state()` → `LIVE` / `CLOSED_FINAL` (last session's close — normal, not stale) / `STALE` (quote older than the previous session — feed behind) / `HOLIDAY` / `ERROR`. `STALE`/`HOLIDAY` cues are down-weighted ×0.25 in `net_by_target`. The cues output now also carries `cue_as_of` (per-ticker last-quote timestamp) for the UI.
- **Refresh wiring (important):** `GET /api/fetch-global-cues` updates the raw numbers only; `POST /api/update-cues` force-fetches **and writes `cues_state`** (the state `run_pipeline` reads). So refreshing the provenance/session badges requires `update-cues` → `run-pipeline`, not a bare fetch. The Global Cues panel's "forced refresh" now does exactly that (then re-runs the pipeline without switching tabs).

## News provenance & tagging guardrails (`news_provenance.py`)
Guardrails that sit **around** the LLM tagger — **zero extra model calls** (the
ingest scanner actually *saves* calls by quarantining junk pre-LLM):
- **Source trust tiers** — `source_tier(article) -> (tier, multiplier)`:
  `exchange_filing` 1.0 > `established_wire` 0.85 > `aggregator` 0.60 >
  `syndicated_pr` 0.35 > `live_blog` 0.20. Down-weighting PR-wire and price-ticker
  "live" blogs kills the circular price→sentiment loop (a "share price live" blog
  contributes at ~0.20, not 1.0).
- **Pre-LLM ingest scan** — `ingest_scan(article)` / `scan_batch(articles)`: cheap
  prompt-injection + junk detector. **Quarantines (never silently drops)** role
  tokens, "ignore previous instructions", zero-width/RTL/homoglyph obfuscation,
  base64 blobs, and oversized bodies — each excluded *with a reason*, not treated
  as a neutral 0.0. Run before the tagger so flagged items never cost a model call.
- **Relevance filter** — `is_relevant(article)` + `sector_keyword_hits(text)`:
  drops foreign single-stock / crypto noise while keeping India news, India/US
  macro, and global cues. The sector keyword map doubles as the coarse fallback
  tagger (see `llm_tag.py` degraded mode).
- **Cross-feed dedup** — `cluster_signature(title, constituents, published_at)` =
  `company | event-class | day`, so N syndications of one event ("TCS Q1 profit" ×4)
  collapse to one cluster. `llm_tag.py` stamps `cluster_id` on every tagged article.
- **Robust estimator** — `weighted_median(pairs)`; used by `sector_tagging` because
  a weighted mean is trivially moved by a single planted item.

`sector_sentiment_from_gemini()` runs `scan_batch` on entry (defense-in-depth),
flags thin reads with `low_confidence` (fewer than 3 articles or <15% coverage),
carries `spread`/`estimator` audit fields per sector, and emits a run-level
`__audit` block (in/scored/quarantined counts, quarantine reasons, tier mix, clamp
count). The old hardcoded `as_of: "2026-07-02"` is gone — output now carries dynamic
`weights_as_of` (real weights date) + `as_of` (run time) + `weights_provenance`.
`backend/main.py::get_tagged_news` also runs the relevance filter + `scan_batch`
before tagging, so the "Recent Processed Headlines" panel is clean and injection is
stopped before it reaches the model.

## Meta-key convention (`__`-prefixed keys)
Sector-sentiment dicts carry non-sector meta keys (`__drilldown`, `__audit`).
Consumers that iterate sectors (`decision_engine.index_bias`, the `backend/main.py`
sector serializer, `pipeline.py`) **must skip ANY `"__"`-prefixed key**, not just
`"__drilldown"`. (A hardcoded `__drilldown`-only filter previously KeyError'd on the
new `__audit` key and dropped the UI to a partial ~4-sector render.)

## TODOs
- **DSPy optimization**: DSPy optimization of the tagging prompt — enable once harness has N≥(few hundred) settled, labeled rows; one-time compile cost is many LLM calls, inference cost ~neutral.

## Validation Commands
- **Sector Map Verification:** `python backend/quant/sector_map.py` (Smoke tests canonical sectors to ensure 100% NIFTY weighting without orphans).
- **RND Debugging:** `python debug_rnd.py` (or `rnd_check.diagnose`).
- **Pipeline Harness Evaluation:** `python -m backend.quant.harness evaluate` (Runs the historical offline evaluation).
- **Adversarial ASR harness:** `python backend/quant/_redteam_asr.py` (measures attack-success-rate of prompt-injection / source-spoofing across control ablations — tier down-weighting, ingest scan, weighted median).
- **Sentiment audit demo:** `python backend/quant/_sentiment_audit_demo.py` (prints the `__audit` trail — quarantine reasons, tier mix, clamp count — for a sample batch).

* `nse_csv_loader.py`: Parses raw NSE Option Chain CSV files into the required framework `chain` dict. Exposes `load_nse_csv()`, `add_oi_change_pct()` (which derives percentage changes from absolute contract changes), and `window_chain()` (which filters out noisy deep OTM strikes before RND processing). Raw NSE CSV chains MUST be windowed around spot before the RND (deep-OTM noise inflates the move). spot+days are user-supplied; T=0 is never allowed. A FALLBACK RND on a noisy export is shown, not overridden.
