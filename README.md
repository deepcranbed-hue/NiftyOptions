# NiftyOptions Quant Engine

## What it is
The NiftyOptions Quant Engine is an experimental decision-support system that synthesizes real-time options chain data and natural language news into a unified market view. It calculates the dominant market regime, constructs a weighted sector sentiment bias via Gemini tagging, extracts the actual Risk-Neutral Distribution (RND) from OTM options, and provides a suggested portfolio structure. 

For a detailed technical breakdown of how this engine operates, see the [Root SKILL.md](./SKILL.md).

## Architecture
`RSS Feed → Time Window Filter → Gemini Sector Tagging → Quant Pipeline (Regime, Bias, RND) → React Frontend`

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
- **`POST /api/run-pipeline`**: The primary execution engine. Expects chain data, fetches/tags RSS news, and returns the pipeline output.
- **`GET /api/sector-news`**: (TODO) Dedicated route for fetching raw/tagged news.
- **`GET /api/global-cues`**: (TODO) Dedicated route for broad market metrics.
- **`POST /api/settle`**: (TODO) Post-trade settlement tracker for signal evaluation.
- **`POST /api/harness/eval`**: (TODO) Offline historical evaluation trigger.

## Validation Commands
- **Check Sector Integrity**: `python backend/quant/sector_map.py`
- **Check RND Math**: `python debug_rnd.py`

## References
- **[SKILL.md](./SKILL.md)**: Rules, data-flow, and module specifics.
- **[REFERENCE.md](./REFERENCE.md)**: Exact API contracts and JSON object shapes.
