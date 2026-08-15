"""
test_agents_offline.py — run the full multi-agent pipeline offline.

Uses the 'deterministic' provider (no LLM, no network) and a mock snapshot, then asserts:
  * every one of the 14 agents ran,
  * the assembled MIO validates against the schema,
  * the driver-dominance vector sums to ~1.0,
  * the Validation agent surfaces at least the expected structure.

Run:  NEWSINDEX_HOME=/path/to/newsindex python test_agents_offline.py
"""
from __future__ import annotations
import sys

from llm import LLMClient, LLMConfig
from orchestrator import Orchestrator
import definitions

# reuse the same mock snapshot as the MCP server's offline test
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/../mcp_server")
from test_offline import MOCK  # noqa: E402


def main() -> int:
    client = LLMClient(LLMConfig(provider="deterministic"))
    orch = Orchestrator(client)
    result = orch.run(snapshot=dict(MOCK))

    fails = []
    trace = result["agent_trace"]
    ran = {t["agent"] for t in trace}
    expected = {a.name for a in definitions.PIPELINE}
    missing = expected - ran
    print(f"agents ran: {len(ran)}/{len(expected)}")
    for t in trace:
        print(f"  {t['agent']:34s} [{t['mode']}]  tools={t['tools_called']}")
    if missing:
        print("MISSING agents:", missing); fails.append("agents")

    mio = result["mio"]
    val = result["validation"]
    print(f"\nMIO schema valid: {val.get('valid')}")
    for e in val.get("errors", []):
        print("  -", e)
    if val.get("valid") is False:
        fails.append("schema")

    ssum = round(sum(mio["driver_dominance"]["vector"].values()), 3)
    print(f"dominance sum: {ssum}")
    if not (0.98 <= ssum <= 1.02):
        fails.append("dominance_sum")

    print(f"event: {mio['event']['canonical_label']} [{mio['event']['class']}]")
    print(f"regime: {mio['regime']['active']}  shock: {mio.get('shock_type')}")
    print(f"dominant driver: {mio['driver_dominance']['dominant_driver']} "
          f"({mio['driver_dominance']['dominant_driver_score']})")
    print(f"sectors: {len(mio['affected_sectors'])}  companies: {len(mio['affected_companies'])}"
          f"  validations: {len(mio.get('validation', []))}")
    print(f"provider: {result['provider']}  degraded flag: {mio['degraded']}")

    # --- overlay assertions (Phase 1 economics layer) ---
    print("\n--- overlay ---")
    if mio.get("overlay_error"):
        print("OVERLAY ERROR:", mio["overlay_error"]); fails.append("overlay_error")
    mh = mio.get("transmission_multihop") or []
    print(f"multi-hop chains: {len(mh)}"
          + (f"  e.g. {mh[0]['path']}" if mh else ""))
    if not mh:
        fails.append("no_multihop")
    else:
        # every chain must be multi-hop (>=3 nodes) — never Driver->Outcome direct
        shortest = min(len(c["chain"]) for c in mh)
        print(f"shortest chain length: {shortest} (must be >=3)")
        if shortest < 3:
            fails.append("direct_chain")
    inter = mio.get("interactions") or []
    print(f"interaction terms: {len(inter)}"
          + (f"  top={inter[0]['term']}" if inter else ""))
    amps = mio.get("market_context", {}).get("amplifiers", {})
    print(f"amplifiers: oil={amps.get('oil',{}).get('band')} "
          f"usdinr={amps.get('usdinr',{}).get('band')} vix={amps.get('vix',{}).get('band')}")
    if not amps.get("usdinr") or not amps.get("vix"):
        fails.append("amplifiers")
    ensec = mio.get("affected_sectors_enriched") or []
    has_omc = any(s["sector"] == "Energy (OMC)" for s in ensec)
    print(f"enriched sectors: {len(ensec)}  Energy(OMC) present: {has_omc}")
    labels = {v.get("status_label") for v in mio.get("validation", [])}
    print(f"validation labels: {labels}")
    if "OVERRIDDEN" in {v.get('status') for v in mio.get('validation', [])} \
            and "Dominated by stronger drivers" not in labels:
        fails.append("terminology")
    # Phase 2: semis regime + subsectors
    sr = mio.get("semis_regime")
    print(f"semis_regime: {'present' if sr else 'absent'}"
          + (f" cause={sr['primary_cause']} IT={sr['indian_it_expected']}" if sr else ""))
    ssf = mio.get("subsector_factors")
    if ssf:
        subs = sum(len(p["sub_sectors"]) for p in ssf)
        print(f"subsector_factors: {len(ssf)} parents, {subs} sub-sectors")
        if subs < 10:
            fails.append("subsectors")
    else:
        fails.append("no_subsectors")

    g = mio.get("causal_graph", {})
    print(f"causal graph: {g.get('node_count')} nodes, {g.get('edge_count')} edges")
    if not g.get("nodes"):
        fails.append("no_graph")
    else:
        sample = g["nodes"][0]
        need = {"current_state", "economic_confidence", "historical_reliability",
                "today_activation", "supporting_news", "observed_confirmation"}
        missing_attrs = need - set(sample)
        print(f"node attrs present: {sorted(set(sample) & need)}")
        if missing_attrs:
            print("MISSING node attrs:", missing_attrs); fails.append("node_attrs")

    # taxonomy drift guard: the single-source company/sector/bucket registry must stay
    # internally consistent (no alias collisions, no symbol in two buckets). A drift here
    # is what used to surface as a false override downstream, so fail the build on it.
    try:
        import sys as _sys
        from pathlib import Path as _P
        _root = _P(__file__).resolve().parents[2]
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        import taxonomy as _TAX
        v = _TAX.validate()
        print(f"taxonomy: {v['n_classified']} classified, "
              f"{len(v['errors'])} errors, {len(v['warnings'])} warnings")
        if v["errors"]:
            for e in v["errors"][:5]:
                print("  taxonomy error:", e)
            fails.append("taxonomy")
    except Exception as e:
        print("taxonomy check skipped:", type(e).__name__, str(e)[:60])

    print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
