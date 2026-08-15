"""
strategy_framework/knowledge/
=============================
The KNOWLEDGE BASE — what the engine knows, separated from how it infers.

Two artifacts, both DATA (not code), both regenerable by the research engine:

  * factor_map.yaml — the declarative map of latent market properties (factors) and
    the observation signals that estimate them, with roles. Domain knowledge: changes
    SLOWLY, and Part E (factor discovery) can regenerate it with zero code changes.
  * evidence.json  — per-signal historical evidence (IC, incremental IC, sample size,
    sessions) written by the research engine (run_signal_audit --write-kb). This is
    what BELIEF QUALITY is computed from: quality = "has this sensor historically
    earned trust", as opposed to confidence = "do the sensors agree today".

The inference engine (factors.py) READS both and contains no market knowledge of its
own — adding a factor or reassigning a role is a YAML edit, not a Python change.
"""
from __future__ import annotations
import json
import os

_HERE = os.path.dirname(__file__)
FACTOR_MAP_PATH = os.environ.get("NIFTY_FACTOR_MAP", os.path.join(_HERE, "factor_map.yaml"))
EVIDENCE_PATH = os.environ.get("NIFTY_EVIDENCE", os.path.join(_HERE, "evidence.json"))


def load_factor_map() -> dict:
    """The declarative factor map: {factor_name: {label, kind, signals: {name: role}}}."""
    import yaml
    with open(FACTOR_MAP_PATH) as f:
        return yaml.safe_load(f)


def load_evidence() -> dict | None:
    """Historical per-signal evidence written by the research engine, or None if the
    audit has never been run with --write-kb (belief quality is then unmeasured)."""
    if not os.path.exists(EVIDENCE_PATH):
        return None
    try:
        with open(EVIDENCE_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def write_evidence(payload: dict) -> str:
    """Persist the research engine's per-signal evidence (atomic-ish overwrite)."""
    with open(EVIDENCE_PATH, "w") as f:
        json.dump(payload, f, indent=1)
    return EVIDENCE_PATH
