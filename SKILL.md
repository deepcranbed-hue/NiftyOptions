# NiftyOptions Quant Engine (Root SKILL)

## What it is
The NiftyOptions Quant Engine is a decision-support signal derived from real-time news and the active option chain. It calculates the dominant market regime, a weighted sector sentiment bias, the Risk-Neutral Distribution (RND), and provides a suggested portfolio structure. **This is a RELATIVE signal**, designed to frame the current market context based on options flow and news sentiment. It is NOT a calibrated probability/EV engine, and it is NOT a direct trade recommendation.

## Architecture & Data Flow
The core execution follows a strictly sequential call chain:
`RSS (feedparser) → news_window → gemini_tag → cache (JSON) → pipeline (regime / bias / RND / market_view) → harness`

*Note: Global Cues operate on a separate RSS-to-LLM path, independent of the main quant pipeline, backed by a local JSON cache (`global_cues_cache.json`).*

## Module Roles (backend/quant)
- **`pipeline.py`**: The main orchestrator that sequentially calls regime assessment, sentiment aggregation, momentum calculation, RND extraction, and market view construction.
- **`complacency.py`**: Calculates a 0-100 option chain complacency gauge based on IV, put OI, skew, and VIX.
- **`risk_budget.py`**: Portfolio sizing and risk gate that halts drawdown breaches and enforces net-Greek limits.
- **`decision_engine.py`**: Translates the raw index bias and regime coverage into an actionable stance (bullish/bearish/neutral/stand aside) and sizes conviction.
- **`gemini_tag.py`**: Batches news articles to the Google GenAI API to tag canonical sectors and score absolute sentiment (-1.0 to 1.0).
- **`global_cues.py` / `fetch_global_cues`**: Manages broad market global index dependencies via global RSS feeds (Yahoo, CNBC, Investing) and Gemini parsing, cached locally to prevent API spam.
- **`cache files`**: `sector_news_cache.json` and `global_cues_cache.json` are generated in the root directory to persist expensive LLM calls between runs.
- **`index_attribution.py`**: Calculates the weighted index bias by mapping canonical sector sentiment against the true NIFTY50 weightings.
- **`market_regime.py`**: Derives the dominant sentiment regime and conviction score via exponentially decayed article momentum.
- **`market_view.py`**: Synthesizes the RND metrics, regime momentum, and structure into a final human-readable comparison and suggestion.
- **`news_window.py`**: Filters raw RSS news against a moving temporal window (e.g., max 12 hours old) to prevent stale sentiment drift.
- **`rnd.py` & `rnd_check.py`**: Extracts the risk-neutral probability distribution from the live option chain using raw strike/LTP data.
- **`rss_news.py`**: The real-time live scraper fetching the raw feed from upstream sources.
- **`sector_map.py`**: The single source of truth for canonical NIFTY50 sector definitions and their exact index weights.
- **`strategy_probability.py`**: Utility functions for strategy EV and win-rate estimations.

## HARD RULES (Invariants)
1. **RND requires `put_ltp`:** To correctly calculate skew and densities, the RND engine requires OTM put legs. Falling back to call-only pricing results in mathematically invalid skew.
2. **Sentiment is computed once:** The frontend is strictly a render layer. All natural language processing and sentiment scoring must occur exclusively in the backend (`gemini_tag.py`).
3. **Strict Article Schema:** Every tagged article dictionary must possess a `sentiment` float and a `sectors_affected` list.
4. **Closed Sector Vocabulary:** The `sectors_affected` strings must strictly adhere to the keys exported by `sector_map.py` (the canonical NIFTY50 map).
5. **Vol State Source:** `vol_state` must come from the complacency gauge, not the driver label.
6. **Risk Gating:** No trade is sized without passing `risk_budget`; undefined-risk and drawdown-breach trades are vetoed.
5. **Temporal Windowing:** News must be windowed and strictly timestamped to decay impact correctly; yesterday's news must never contaminate today's regime.
6. **Weight Seeds:** Sector weights are hardcoded seeds derived from NSE. They must be refreshed directly from the exchange, not approximated.
7. **Relative Output:** All bias and probability outputs are strictly relative contextual markers, not predictive statistics.

## References
- **Exact Data Shapes & Contracts:** See [REFERENCE.md](./REFERENCE.md)
- **Quant Module Specifics:** See [backend/quant/SKILL.md](./backend/quant/SKILL.md)
- **API Contracts:** See [backend/api/SKILL.md](./backend/api/SKILL.md)
- **Frontend/UI:** See [frontend/SKILL.md](./frontend/SKILL.md)
