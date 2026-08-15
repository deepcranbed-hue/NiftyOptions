"""
orchestrator.py — the deterministic controller that runs the agent pipeline.

Not an LLM. It owns the as-of clock, runs the agents in DAG order (definitions.PIPELINE),
collects each agent's output onto a shared context (the "intelligence bundle"), then
assembles the standardized Market Intelligence Object — numbers from the Core (via
mio_builder), narrative/structure enriched from the agents' outputs — and validates it.

Mirrors NewsAgent/ARCHITECTURE.md §4 (orchestration) and §7 (degradation): any agent that
fails silently degrades to its deterministic reduce, and the MIO is tagged accordingly.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# reuse the MCP server's Core + MIO assembler
_MCP = Path(__file__).resolve().parents[1] / "mcp_server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

import core           # noqa: E402
import mio_builder    # noqa: E402

# overlay economics layer (multi-hop chains, interactions, causal graph, ...)
_OVL = Path(__file__).resolve().parents[1] / "overlay"
if str(_OVL) not in sys.path:
    sys.path.insert(0, str(_OVL))
import enrich as overlay_enrich  # noqa: E402

import definitions    # noqa: E402
from llm import LLMClient  # noqa: E402


class Orchestrator:
    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient()

    def run(self, snapshot: dict | None = None) -> dict:
        """Run the full pipeline. If `snapshot` is given, inject it (offline/replay);
        otherwise the Collector agent live-fetches one."""
        if snapshot is not None:
            core.load_snapshot(snapshot)

        context: dict[str, Any] = {}
        trace: list[dict] = []

        import time
        for agent in definitions.PIPELINE:
            result = agent.run(self.client, context)
            trace.append({"agent": result["agent"], "mode": result["mode"],
                          "tools_called": result["tools_called"],
                          "error": result.get("error", "")})
            # expose each agent's output to downstream agents under a short key
            context[_key(agent.name)] = result["output"]
            if self.client.provider in ("openai", "anthropic"):
                time.sleep(5)

        # surface a single clear diagnostic if the LLM was configured but never reached
        llm_fallbacks = [t for t in trace
                         if t["mode"].startswith("fallback") and t.get("error")]
        llm_note = ""
        if not self.client.is_deterministic() and llm_fallbacks:
            llm_note = (f"LLM provider '{self.client.provider}' unreachable — all agents ran "
                        f"on the deterministic Core. First error: {llm_fallbacks[0]['error']}")

        # --- assemble the standardized MIO (numbers from the Core) ----------
        mio = mio_builder.build_mio()
        mio = self._enrich(mio, context)
        # --- apply the economics overlay (chains, interactions, causal graph) ---
        try:
            mio = overlay_enrich.enrich_mio(mio, core)
        except Exception as e:
            mio["overlay_error"] = f"{type(e).__name__}: {e}"
        # LLM re-rank of the (deterministically retrieved) reason-discovery candidates — retrieve
        # deterministically, then let the model synthesise/re-order. No-op in deterministic mode.
        if not self.client.is_deterministic():
            try:
                import sys as _sys
                from pathlib import Path as _P
                _ovl = str(_P(__file__).resolve().parents[1] / "overlay")
                if _ovl not in _sys.path:
                    _sys.path.insert(0, _ovl)
                import reason_discovery as _rd
                mio["reason_reranked"] = _rd.llm_rerank_all(self.client, mio)
            except Exception as e:
                mio["reason_rerank_error"] = f"{type(e).__name__}: {e}"
        validation = mio_builder.validate_mio(mio)

        degraded = any(t["mode"].startswith(("fallback", "deterministic")) for t in trace) \
            and not self.client.is_deterministic()
        mio["degraded"] = bool(degraded)

        return {
            "mio": mio,
            "validation": validation,
            "agent_trace": trace,
            "provider": self.client.provider,
            "llm_note": llm_note,
            "intelligence_bundle": context,
        }

    # -- enrich the Core-built MIO with agent narrative ---------------------
    def _enrich(self, mio: dict, ctx: dict) -> dict:
        # override reasons from the Validation agent
        val = (ctx.get("validation") or {}).get("statuses") or []
        reason_by_rule = {v["rule"]: v.get("reason", "") for v in val}
        for entry in mio.get("validation", []):
            if entry.get("status") == "OVERRIDDEN" and reason_by_rule.get(entry["edge"]):
                entry["reason"] = reason_by_rule[entry["edge"]]

        # shock type / regime transition from the Transmission & Regime agents.
        # COERCE against the schema enum: an LLM may emit free text ("Macro headwind"),
        # which must never reach the schema-validated field. Map/keep only valid values;
        # otherwise keep the deterministic Core value already in the MIO.
        _ALLOWED_SHOCK = {"supply", "demand", "inventory", "speculation", "policy", "none"}
        _SHOCK_ALIASES = {"macro": "policy", "macro headwind": "policy", "rates": "policy",
                          "monetary": "policy", "geopolitical": "supply", "war": "supply",
                          "sentiment": "speculation", "positioning": "speculation",
                          "risk-off": "none", "risk off": "none"}
        tr = ctx.get("transmission") or {}
        raw_shock = (tr.get("shock_type") or "").strip().lower()
        if raw_shock in _ALLOWED_SHOCK:
            mio["shock_type"] = raw_shock
        elif raw_shock in _SHOCK_ALIASES:
            mio["shock_type"] = _SHOCK_ALIASES[raw_shock]
        # else: leave the valid deterministic shock_type build_mio already set
        reg = ctx.get("regime") or {}
        if reg.get("transition") and reg["transition"] != "none":
            mio.setdefault("regime", {})["transition"] = reg["transition"]

        # canonical events from Normalization -> event member count
        canon = (ctx.get("normalization") or {}).get("canonical_events") or []
        if canon:
            mio["event"]["canonical_label"] = mio["event"]["canonical_label"]
            mio["event"]["normalized_from"] = len(canon)
        return mio


def _key(agent_name: str) -> str:
    """'Validation Agent' -> 'validation'; 'Driver-Dominance Agent' -> 'driver_dominance'."""
    n = agent_name.replace(" Agent", "").replace(" Engine", "")
    n = n.replace("-", " ").replace("Cross Asset Propagation", "cross_asset")
    return n.strip().lower().replace(" ", "_")
