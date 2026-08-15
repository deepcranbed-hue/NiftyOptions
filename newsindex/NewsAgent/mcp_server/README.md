# News Intelligence Agent — MCP Server

An MCP server that exposes the **existing `market_scan.py` engine** (the Deterministic Core)
as tools, so any MCP client — Claude Desktop, Cowork, Claude Code — can drive the
Market-Knowledge-Graph workflow and get back a standardized **Market Intelligence Object
(MIO)**.

> **Nothing in the parent `newsindex/` project is modified.** This folder only *imports*
> `market_scan.py` and re-projects its outputs into the MIO format defined in
> [`../MARKET_INTELLIGENCE_OBJECT.md`](../MARKET_INTELLIGENCE_OBJECT.md).

## Files

| File | Role |
|---|---|
| `core.py` | Thin adapter — imports `market_scan.py`, holds a market snapshot, runs every engine output as JSON-safe data. |
| `mio_builder.py` | Assembles a schema-conforming MIO from the engine outputs; validates it against `../schemas/mio.schema.json`. |
| `server.py` | The FastMCP server — 13 tools mapping the blueprint's agents onto the engine. |
| `test_offline.py` | Smoke test on a mock snapshot (no network / no yfinance needed). |
| `requirements.txt` | `mcp` + `jsonschema` + the engine's existing deps. |
| `claude_desktop_config.example.json` | Example MCP client config. |

## How it maps to the blueprint

Each tool is a facade over an existing `market_scan.py` function — the Core produces every
number, the tool only shapes it:

| MCP tool | Blueprint agent | `market_scan.py` function(s) |
|---|---|---|
| `refresh_market_snapshot` / `snapshot_status` | Collector | `fetch_quotes`, `fetch_news`, `fetch_fii_dii`, `fetch_earnings` |
| `detect_regime` | Regime | `detect_ai_regime`, `market_regime`, `build_oil_regime` |
| `causal_engine` | Transmission + Impact | `build_causal_engine` |
| `driver_dominance` | Driver-Dominance | contribution decomposition from `build_causal_engine` |
| `transmission_map` | Transmission | `build_transmission_map` |
| `validate_relationships` | Validation | `build_cause_effect_scorecard` |
| `sector_intelligence` | Sector Intelligence | `build_sector_factor_model` |
| `company_intelligence` | Company Intelligence | `classify_company_news` |
| `market_themes` | (theme detection) | `detect_themes` |
| `standout_movers` | (movers) | `build_standout_movers` |
| `market_verdict` | (observed tape) | `build_verdict`, `market_regime` |
| `build_market_intelligence_object` | Orchestrator | assembles + validates the MIO |

## Setup

Use the engine's own virtualenv so its deps (yfinance, feedparser, pandas) are importable:

```bash
cd /Users/deepak/antigravity/NiftyOptions/newsindex
# reuse the existing venv, or create one:
python3.10 -m venv newsindex_env_3.10 && source newsindex_env_3.10/bin/activate
pip install -r requirements.txt                       # the engine's deps
pip install -r NewsAgent/mcp_server/requirements.txt  # + mcp, jsonschema
```

## Run / register

**In an MCP client (recommended).** Copy the block from
`claude_desktop_config.example.json` into your client's MCP config, fixing the two absolute
paths and pointing `command` at the venv python. Restart the client; the
`news-intelligence-agent` tools appear.

**Standalone (stdio).**
```bash
NEWSINDEX_HOME=/Users/deepak/antigravity/NiftyOptions/newsindex \
  newsindex_env_3.10/bin/python NewsAgent/mcp_server/server.py
```

`NEWSINDEX_HOME` tells the server where `market_scan.py` lives (defaults to the project root
two levels above the server file).

## Verify without network

```bash
NEWSINDEX_HOME=/Users/deepak/antigravity/NiftyOptions/newsindex \
  python NewsAgent/mcp_server/test_offline.py
```

Expected tail:
```
schema valid: True
ALL PASS
```

## Typical session (what a client would call)

1. `refresh_market_snapshot` — pull the live tape + news once.
2. `detect_regime` → `causal_engine` → `driver_dominance` — the why/how/what-drove-it.
3. `validate_relationships` — which textbook links held vs the tape today.
4. `sector_intelligence` / `company_intelligence` — where it lands.
5. `build_market_intelligence_object` — the single standardized object to hand downstream.

## Honesty / invariants carried from the framework

* **Numbers come from the Core.** Tools never invent a figure; they call `market_scan.py`.
* **`historical_reliability` is tagged `PRIOR`.** No ≥60-session calibration is wired into
  this server, so reliability is descriptive only (mirrors D-MA-04).
* **Horizons are separated and honest.** The engine is a ~1-session directional read, so the
  MIO fills `immediate`/`short` and leaves `medium`/`structural` Neutral with an explicit
  note rather than fabricating them.
* **Validation says "overridden + reason", never "failed".**

## Notes & next steps

* This is a **session-level** MIO (the engine is a daily aggregate). Per-event MIOs would
  require the event-formation agents (Detection/Normalization) from the blueprint on top of
  a streaming news feed.
* Wiring `calibrate.py` / `build_events.py` output into `historical_reliability` is the
  natural path to graduate reliability from `PRIOR` to `CALIBRATED`.
* The MCP tool surface is stable; internal engine functions can evolve without breaking
  clients as long as the MIO still validates.
