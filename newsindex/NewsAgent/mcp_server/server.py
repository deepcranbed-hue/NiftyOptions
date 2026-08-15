"""
server.py — News Intelligence Agent MCP server.

Exposes the existing `market_scan.py` engine (the Deterministic Core) as MCP tools,
so any MCP client (Claude Desktop, Cowork, Claude Code, etc.) can drive the
Market-Knowledge-Graph workflow: collect a market snapshot, detect the regime, run
the causal engine, decompose driver dominance, validate relationships against the tape,
and assemble a standardized Market Intelligence Object (MIO).

Run:
    NEWSINDEX_HOME=/path/to/newsindex python server.py         # stdio transport
    (or configure it in your MCP client — see README.md)

Nothing in the parent project is modified; this server only imports and calls it.
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

import core
import mio_builder

mcp = FastMCP("news-intelligence-agent")


def _json(obj: Any) -> str:
    """Stable, readable JSON for tool output."""
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# L1 — Collection
# ---------------------------------------------------------------------------
@mcp.tool()
def refresh_market_snapshot() -> str:
    """Collector: LIVE-fetch every engine input (index/macro/stock/sector/theme quotes,
    FII/DII flows, news RSS, earnings) via the existing engine and cache it in-process.
    Call this first each session. Returns a summary with as-of time and item counts."""
    return _json(core.refresh_snapshot())


@mcp.tool()
def snapshot_status() -> str:
    """Return the current cached snapshot's as-of time and item counts without refetching.
    Triggers one live fetch only if no snapshot exists yet."""
    return _json(core.snapshot_summary())


# ---------------------------------------------------------------------------
# L2 — Regime & causal reasoning
# ---------------------------------------------------------------------------
@mcp.tool()
def detect_regime() -> str:
    """Regime Agent: which relationship regime is active. Returns the AI regime
    (Complement/Substitution/Neutral, decided from news + the chip/IT tape), the observed
    risk-on/risk-off tone of the tape, and the oil regime read."""
    return _json(core.detect_regime())


@mcp.tool()
def causal_engine() -> str:
    """Transmission + Impact core: run the causal engine. Returns the expected % move for
    Nifty / Bank Nifty (with a band), the sentiment label, conviction, driver values, the
    explicit cause->effect chains, and any dissenting drivers."""
    return _json(core.causal_engine())


@mcp.tool()
def driver_dominance(index: str = "Nifty 50") -> str:
    """Driver-Dominance Agent: decompose what actually drove the index today. Returns each
    driver's share of the move (shares sum to ~1.0) and names the dominant driver."""
    return _json(core.driver_dominance(index))


@mcp.tool()
def transmission_map() -> str:
    """Transmission Agent: the driver -> channel -> sector causal network (text lines),
    e.g. Oil -> inflation -> RBI -> yields -> banks; with beneficiaries and losers."""
    return _json(core.transmission_map())


@mcp.tool()
def validate_relationships() -> str:
    """Validation Agent: check each cross-asset rule's expected direction against the observed
    tape, index-weighted and regime-aware. Returns per-rule weighted agreement and a status
    (CONFIRMED / WEAKENED / OVERRIDDEN) — never 'rule failed'."""
    return _json(core.validate_relationships())


# ---------------------------------------------------------------------------
# L3 — Sector / company / theme intelligence
# ---------------------------------------------------------------------------
@mcp.tool()
def sector_intelligence() -> str:
    """Sector Intelligence Agent: for every sector, aggregate all active drivers into one
    net score with a bullish/bearish verdict and the contributing drivers."""
    return _json(core.sector_intelligence())


@mcp.tool()
def company_intelligence() -> str:
    """Company Intelligence Agent: company-specific headlines classified pos/neg/neutral,
    tagged with sector, Nifty weight/impact, and catalyst-vs-news kind."""
    return _json(core.company_intelligence())


@mcp.tool()
def market_themes() -> str:
    """Active market themes from the playbook (name, rationale, headline hit count)."""
    return _json(core.market_themes())


@mcp.tool()
def standout_movers(top: int = 4) -> str:
    """Weight-adjusted biggest gainers and losers across the fetched universe."""
    return _json(core.standout_movers(top))


@mcp.tool()
def market_verdict() -> str:
    """One-line observed-tape verdict (risk-on/off + the key cross-asset prints)."""
    return _json(core.market_verdict())


# ---------------------------------------------------------------------------
# L4 — Standardized output
# ---------------------------------------------------------------------------
@mcp.tool()
def build_market_intelligence_object(validate: bool = True) -> str:
    """Orchestrator: assemble the standardized Market Intelligence Object (MIO) for the
    current snapshot — event, regime, transmission, affected sectors/companies, horizoned
    impact, expected direction, the confidence triple, and driver dominance. If validate is
    true, also checks it against schemas/mio.schema.json and reports any errors."""
    mio = mio_builder.build_mio()
    result: dict[str, Any] = {"mio": mio}
    if validate:
        result["validation"] = mio_builder.validate_mio(mio)
    return _json(result)


if __name__ == "__main__":
    mcp.run()
