"""
tools.py — the Core tool registry the agents call.

These are exactly the tools the MCP server exposes (server.py), backed by the same
`mcp_server/core.py` functions, which wrap the unmodified `market_scan.py` engine. The
agent runtime binds each agent to a bounded subset of these tools.

Every entry is {name, description, parameters(JSONSchema), fn}. `fn(**args)` returns a
JSON-serializable Core result — numbers always come from here, never from the LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

# reuse the MCP server's Core adapter (no duplication, no edits to market_scan.py)
_MCP = Path(__file__).resolve().parents[1] / "mcp_server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

import core  # noqa: E402  (mcp_server/core.py)


_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}


def _reg(name: str, description: str, fn: Callable, parameters: dict | None = None) -> dict:
    return {"name": name, "description": description,
            "parameters": parameters or _EMPTY, "fn": fn}


# -- the registry ------------------------------------------------------------
REGISTRY: dict[str, dict] = {t["name"]: t for t in [
    _reg("refresh_market_snapshot",
         "Live-fetch every engine input (quotes, flows, news, earnings) and cache it.",
         lambda: core.refresh_snapshot()),
    _reg("snapshot_status",
         "Current cached snapshot's as-of time and item counts.",
         lambda: core.snapshot_summary()),
    _reg("market_verdict",
         "One-line observed-tape verdict (risk-on/off + key cross-asset prints).",
         lambda: core.market_verdict()),
    _reg("detect_regime",
         "Active regime: AI regime (Complement/Substitution/Neutral), observed tone, oil regime.",
         lambda: core.detect_regime()),
    _reg("causal_engine",
         "Run the causal engine: expected % move for Nifty/Bank Nifty, sentiment, conviction, chains.",
         lambda: core.causal_engine()),
    _reg("driver_dominance",
         "Decompose what drove the index today into shares summing to ~1.0.",
         lambda index="Nifty 50": core.driver_dominance(index),
         {"type": "object",
          "properties": {"index": {"type": "string", "default": "Nifty 50"}},
          "additionalProperties": False}),
    _reg("transmission_map",
         "Driver -> channel -> sector causal network (text lines).",
         lambda: core.transmission_map()),
    _reg("validate_relationships",
         "Expected vs observed per cross-asset rule; status CONFIRMED/WEAKENED/OVERRIDDEN.",
         lambda: core.validate_relationships()),
    _reg("sector_intelligence",
         "Per-sector net driver score + bullish/bearish verdict.",
         lambda: core.sector_intelligence()),
    _reg("company_intelligence",
         "Company-specific headlines classified pos/neg with sector + Nifty weight.",
         lambda: core.company_intelligence()),
    _reg("market_themes",
         "Active market themes from the playbook (name, why, hit count).",
         lambda: core.market_themes()),
    _reg("standout_movers",
         "Weight-adjusted biggest gainers/losers across the universe.",
         lambda top=4: core.standout_movers(top),
         {"type": "object",
          "properties": {"top": {"type": "integer", "default": 4}},
          "additionalProperties": False}),
    _reg("shock_type",
         "Classify the market/oil shock type: supply/demand/inventory/speculation/policy/none.",
         lambda: core.shock_type()),
]}


def spec(names: list[str]) -> list[dict]:
    """Tool specs (no fn) for the given tool names — what the LLM is shown."""
    return [{"name": REGISTRY[n]["name"],
             "description": REGISTRY[n]["description"],
             "parameters": REGISTRY[n]["parameters"]}
            for n in names if n in REGISTRY]


def call(name: str, arguments: dict | None = None) -> Any:
    """Execute a Core tool by name."""
    if name not in REGISTRY:
        return {"error": f"unknown tool: {name}"}
    try:
        return REGISTRY[name]["fn"](**(arguments or {}))
    except Exception as e:  # tools must never crash the agent loop
        return {"error": f"{type(e).__name__}: {e}"}
