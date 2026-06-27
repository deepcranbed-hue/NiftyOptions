# API Layer Contract (backend/api/SKILL)

## FastAPI Endpoints (`backend/main.py`)

### `/api/run-pipeline` (POST)
- **Role:** Primary execution engine. Receives the `PipelineRequest` payload containing the option `chain` and `half_life_hours`.
- **Logic:** Internally calls `get_tagged_news()` to fetch and tag RSS articles, then passes them alongside the chain to `run_pipeline`.

### `/api/sector-news` (GET)
- **Role:** (TODO: create/refine) Will serve raw or tagged sector news explicitly to the frontend if decoupled from the pipeline execution.

### `/api/global-cues` (GET)
- **Role:** (TODO: create) Intended to fetch and serve global index parameters to the frontend independent of the options chain.

### `/api/settle` (POST)
- **Role:** (TODO: create) Designed to mark a specific `signal_id` as settled based on end-of-day PnL tracking.

### `/api/harness/eval` (POST)
- **Role:** (TODO: create) Endpoint to trigger the offline quantitative evaluation harness via the web interface.

## Caching Rule
The RSS fetching and Gemini tagging process (`get_tagged_news`) must be decorated with `@alru_cache(ttl=300)`.
- **Why:** To prevent rate-limiting the RSS upstream and burning unnecessary Gemini tokens if the user recalculates the pipeline rapidly (e.g., by adjusting the option chain slider). Only the news fetch is cached (~5 minutes); the pipeline math computes instantly on every request.

## Persistence Rules
- **`prev_regime` State:** The server maintains a global or session-level `_prev_regime` dictionary across requests. This ensures the regime calculation correctly applies hysteresis (e.g., requires a higher conviction threshold to flip the existing regime).
- **`signal_id` Tracking:** (Planned) Any signal generated must be tagged with a persistent ID to allow the `/settle` endpoint to calculate its exact historical outcome.

## No-Look-Ahead Rule
Logged spot prices and executed chain parameters must strictly reflect the exact state at request time. The API must never allow retrospective look-ahead data to taint the `harness` or regime logs.

## Dependencies & Environment Variables
- **Dependencies:** `fastapi`, `uvicorn`, `pydantic`, `google-genai`, `feedparser`, `httpx`, `async-lru`.
- **Env Vars:** 
  - `GEMINI_API_KEY` (Required for `gemini_tag_batch()` to function).
