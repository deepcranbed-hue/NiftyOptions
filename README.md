# NiftyOptions Quant Engine

## What it is
The NiftyOptions Quant Engine is an experimental decision-support system that synthesizes real-time options chain data and natural language news into a unified market view. It calculates the dominant market regime, constructs a weighted sector sentiment bias via Gemini tagging, extracts the actual Risk-Neutral Distribution (RND) from OTM options, and provides a suggested portfolio structure. It also includes an Economic Metals Barometer (Copper/Gold/Silver) to gauge global growth/fear, a Formula Overlay UI to trace how quant metrics are calculated in real-time, and a Custom Strike Evaluation engine that dynamically recalculates P&L arrays and Payoff curves on the fly when users customize strategy legs.

For a detailed technical breakdown of how this engine operates, see the [Root SKILL.md](./SKILL.md).

## Architecture
The quant engine uses a decoupled fast-path pipeline, fueled by 4 independent context pillars that run asynchronously:
1. **News & Regime Pillar**: RSS Feeds → Gemini Tagging → Regime Bias & Sentiment Weights
2. **Global Macro Pillar**: US Yields/Metals → Economic Cues → Macro Net Tilt
3. **Domestic Flows Pillar**: FPI/DII Cash & SIP Flows → Institutional Tilt
4. **Options Market Pillar**: Option Chain LTPs → Risk-Neutral Distribution (RND) → Probability Models

**Execution Flow**: `[4 Context Pillars] → `.state/` JSON Cache → Fast Path Quant Engine (pipeline.py) → React Frontend`
## Prerequisites
- **Node.js**: v18+ (verified to work with standard Vite setups).
- **Python**: v3.9+ (verified to work with standard Uvicorn/FastAPI setups).

## Environment Variables
Create a `.env` file in the root directory.
- `GEMINI_API_KEY` (Required): Your Google GenAI API key. If missing, the `gemini_tag_batch()` process will fail.

## Setup & Run (Fresh Clone)

### 1. Backend Setup
```bash
# Create and activate a virtual environment (optional but recommended)
python3 -m venv native_env
source native_env/bin/activate

# Install the Python dependencies
pip install -r backend/requirements.txt

# Note: For PostgreSQL database ingestion (Data Agent / Fundamentals), you must use the isolated breeze_env
# (located at breeze_env) since it contains the required `psycopg` dependencies.

# Start the FastAPI server
PYTHONPATH=./ uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### 2. Frontend Setup
```bash
# In a new terminal, install the Node dependencies
npm install

# Start the Vite development server
npm run dev
```

## Endpoints Overview
- **`POST /api/update-news`**: Fetches RSS news and tags with Gemini. Writes to `news_state.json`.
- **`POST /api/update-flows`**: Fetches domestic institutional flows (FPI/DII/SIP) and updates US Macro factors. Writes to `flows_state.json`, `events_state.json`, and `macro_state.json`.
- **`POST /api/update-cues`**: Fetches Global Cues (US 10Y Yields, Dollar Index, Metals Barometer). Writes to `cues_state.json`.
- **`POST /api/run-pipeline`**: The fast-path pipeline execution engine. Takes chain data, loads all cached state JSONs from `.state/`, computes the RND, optimizes strategies, and returns the pipeline output synchronously without invoking any LLMs.

### State Management (`.state/`)
To ensure the pipeline is ultra-fast, the system uses a decoupled state architecture. Background jobs (via endpoints like `/api/update-flows`) write structured JSON files into the `.state/` directory. The fast-path pipeline (`run_pipeline`) only performs reads on these files, ensuring no latency penalties when evaluating real-time option chains. *(Note: The Vite frontend is explicitly configured to ignore the `.state/` folder via `watch.ignored` to prevent backend updates from triggering full browser page reloads).*
## Validation Commands
- **Check Sector Integrity**: `python backend/quant/sector_tree.py`
- **Check RND Math**: `python debug_rnd.py`

## References
- **[SKILL.md](./SKILL.md)**: Rules, data-flow, and module specifics.
- **[REFERENCE.md](./REFERENCE.md)**: Exact API contracts and JSON object shapes.
- **[dataconnection.md](./dataconnection.md)**: Session validation, HTTP bypass architecture, and troubleshooting guidelines.
- **[strategy_framework/SKILL.md](./strategy_framework/SKILL.md)**: Separate directional-momentum strategy suggester + walk-forward backtest subsystem (Desk view). Home of the canonical per-minute index-volume reconstruction, the opt-in cost-edge "do-nothing" gate, and the descriptive MPS0 max-profit benchmark.

## Panels Overview
The frontend is broken down into modular UI panels that handle distinct layers of the options trading framework:

### Data Ingestion & Storage
- **11. Download NSE / NSESyncPanel**: Downloads end-of-day raw CSV chains from the NSE website. 
- **12. Fetch ICICI Breeze / BreezeSyncPanel**: Fetches live options chains directly from the ICICI Breeze API using your session key.
- **Price Chart / PriceChartPanel**: A TradingView lightweight chart displaying ground-truth 1m/1d price bars and resampling them dynamically. Handles overlays like capture markers.

### Core Analysis
- **1. Sentiment & Bias / SectorNewsPanel**: Displays Gemini-tagged news sentiment and macro sector tilt.
- **2. Market Breadth / ComplacencyPanel**: Tracks VIX, advance/decline ratios, and general market froth/complacency.
- **3. FII / DII Flow / FlowsPanel**: Analyzes institutional cash/derivative flows and their systemic weight.
- **4. Global Cues / GlobalCuesPanel**: Tracks US Yields, Dollar Index, and the Metals Barometer (Gold/Copper/Silver).
- **5. Event Calendar / EventCalendarPanel**: Highlights upcoming binary events (CPI, Fed, RBI) that dictate volatility premium.
- **6. Sector Performance / SectorEarningsPanel**: Pre-earnings and sector momentum tracking.
- **7. OI Positioning / OIPositioningPanel**: Displays support (Put OI walls) and resistance (Call OI walls) using Option Chain data.

### Trading & Execution
- **8. Strategy Suggester / StrategySuggesterPanel**: Takes all the above inputs, computes Risk-Neutral Distribution (RND), and outputs optimal options strategies (Credit Spreads, Iron Condors).
- **9. My Portfolio / PortfolioPanel**: Tracks saved option strategies and their current P&L.
- **10. Capture Compare / CaptureComparePanel**: Allows side-by-side comparison of historical database captures (chains) to track OI shifting over time.
