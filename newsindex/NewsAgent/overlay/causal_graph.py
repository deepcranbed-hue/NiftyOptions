"""
causal_graph.py — the Version-2.0 live causal market graph.

The review's headline ask: stop thinking "news → report" and build a live causal graph where
every node carries:
    * current state
    * economic confidence
    * historical reliability
    * today's activation strength
    * supporting news
    * observed market confirmation

This builds exactly that from the Core outputs + the canonical chain library. Nodes are the
economic entities along the active transmission chains; edges are the hops. Numbers
(activation, confirmation) come from the Core's driver values and the observed tape; economic
confidence / historical reliability are PRIOR until calibrated.
"""
from __future__ import annotations

import chains as chainlib
import common

# economic confidence per driver's pathway (prior strength of the textbook mechanism, 1-5)
_ECON_CONF = {
    "oil_pct": 5, "us10y_pct": 5, "dxy_pct": 4, "fii_kcr": 4, "vix_pct": 4,
    "sox_pct": 3, "india_cpi_hot": 5, "us_cpi_cool": 4, "geopolitics_hits": 3,
}

_DRIVER_LABEL = common.DRIVER_LABELS          # single source of truth (overlay/common.py)


def _activation(dominance_vec: dict, driver_label: str) -> float:
    """Today's activation strength = this driver's dominance share (0..1)."""
    return round(dominance_vec.get(driver_label, 0.0), 3)


def _supporting_news(news: list[dict], keywords: list[str]) -> list[str]:
    out = []
    for n in news or []:
        text = (n.get("title", "") + " " + n.get("tags", "")).lower()
        if any(k in text for k in keywords):
            out.append(n.get("title", ""))
    return out[:3]


# keyword hooks to attach supporting news to a driver root
_NEWS_HOOKS = {
    "oil_pct": ["oil", "crude", "brent", "opec", "hormuz"],
    "geopolitics_hits": ["iran", "israel", "hormuz", "war", "sanction", "gulf"],
    "sox_pct": ["semiconductor", "chip", "nvidia", "ai ", "sox"],
    "fii_kcr": ["fii", "foreign", "outflow", "inflow"],
    "us10y_pct": ["yield", "treasury", "10-year", "10y"],
    "dxy_pct": ["dollar", "dxy"],
    "vix_pct": ["volatility", "vix", "fear"],
    "india_cpi_hot": ["cpi", "inflation"],
    "us_cpi_cool": ["cpi", "inflation", "fed"],
}


def build(core, eng: dict, dominance: dict, regime: dict,
          validations: list[dict], news: list[dict]) -> dict:
    """Assemble the live causal graph.

    core       — the mcp_server core module (for oil_level etc.)
    eng        — full causal engine dict (drivers, contribs, brent_price)
    dominance  — driver_dominance() output (vector + dominant)
    regime     — detect_regime() output (ai_regime, observed_tone)
    validations— validate_relationships() output (observed confirmation)
    news       — the snapshot's news list
    """
    drivers = eng.get("drivers", {})
    dom_vec = dominance.get("vector", {})
    ai_regime = regime.get("ai_regime")
    observed_tone = regime.get("observed_tone")

    # observed confirmation lookup: sector/name -> status
    confirm_status = {}
    for v in validations or []:
        confirm_status[v.get("name", "")] = v.get("status")

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def _node(label: str, kind: str = "economic"):
        if label not in nodes:
            nodes[label] = {
                "id": label, "kind": kind,
                "current_state": None,
                "economic_confidence": None,
                "historical_reliability": None,
                "today_activation": 0.0,
                "supporting_news": [],
                "observed_confirmation": None,
            }
        return nodes[label]

    # market-node current states (from the tape)
    raw = eng.get("raw", {})
    market_state = {
        "Oil": _fmt(raw.get("oil"), "%"), "India VIX": _fmt(raw.get("vix"), "%"),
        "US 10Y Yield": _fmt(raw.get("us10y"), "%"), "Dollar Index": _fmt(raw.get("dxy"), "%"),
        "USDINR": _fmt(raw.get("usdinr"), "%"), "SOX / Semis": _fmt(raw.get("sox"), "%"),
        "FII Net Sell": _fmt(raw.get("fii"), "cr"), "FII Net Buy": _fmt(raw.get("fii"), "cr"),
    }

    # walk each active driver's chains
    for dkey, dval in drivers.items():
        branches = chainlib.expand(dkey, dval, regime=ai_regime)
        if not branches:
            continue
        label = _DRIVER_LABEL.get(dkey, dkey)
        activation = _activation(dom_vec, label)
        econ_conf = _ECON_CONF.get(dkey)
        support = _supporting_news(news, _NEWS_HOOKS.get(dkey, []))

        for br in branches:
            prev = None
            for i, node_label in enumerate(br["chain"]):
                nd = _node(node_label, kind="driver" if i == 0 else "economic")
                # root node gets the driver's activation + news + econ confidence
                if i == 0:
                    nd["today_activation"] = max(nd["today_activation"], activation)
                    nd["economic_confidence"] = econ_conf
                    nd["supporting_news"] = support or nd["supporting_news"]
                    if node_label in market_state and market_state[node_label]:
                        nd["current_state"] = market_state[node_label]
                else:
                    # downstream nodes inherit a decayed activation
                    nd["today_activation"] = max(nd["today_activation"],
                                                 round(activation * (0.85 ** i), 3))
                    if nd["economic_confidence"] is None:
                        nd["economic_confidence"] = econ_conf
                # observed confirmation if this node maps to a validated sector
                for name, status in confirm_status.items():
                    if node_label.split(" ")[0].lower() in name.lower():
                        nd["observed_confirmation"] = status
                if prev is not None:
                    edges.append({"source": prev, "target": node_label,
                                  "branch": br["branch"], "driver": label})
                prev = node_label

    # historical_reliability: PRIOR everywhere (no >=60-session calibration wired)
    for nd in nodes.values():
        nd["historical_reliability"] = {"value": None, "tag": "PRIOR"}

    return {
        "as_of_tone": observed_tone,
        "ai_regime": ai_regime,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def _fmt(v, unit):
    if v is None:
        return None
    return f"{v:+.1f}{unit}" if unit == "%" else f"₹{v:+,.0f}{unit}"


def to_mermaid(graph: dict, max_edges: int = 40) -> str:
    """Render the causal graph as a mermaid flowchart for the report."""
    L = ["```mermaid", "flowchart TD"]
    seen = set()
    for e in graph["edges"][:max_edges]:
        s = _mm_id(e["source"]); t = _mm_id(e["target"])
        L.append(f'    {s}["{e["source"]}"] --> {t}["{e["target"]}"]')
        seen.add(e["source"]); seen.add(e["target"])
    L.append("```")
    return "\n".join(L)


def _mm_id(label: str) -> str:
    return "n" + "".join(ch for ch in label if ch.isalnum())[:24]
