"""
reason_discovery.py — SHIM.

The implementation moved to the shared home: newsindex/reason_discovery.py, so that
market_scan.py can use it too (it previously had its own hardcoded ±2 heuristic
override analysis instead). This shim re-exports everything, so the existing
`import reason_discovery` call sites in overlay/enrich.py and agents/orchestrator.py
keep working with no change.

Do NOT add logic here. Edit the canonical module.
"""

from __future__ import annotations

import sys
import importlib.util as _ilu
from pathlib import Path

# newsindex/ is two levels up from NewsAgent/overlay/
_SHARED = Path(__file__).resolve().parents[2]
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

# Load under an explicit name so we can never re-import this shim by accident.
_spec = _ilu.spec_from_file_location("_shared_reason_discovery",
                                     _SHARED / "reason_discovery.py")
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# re-export the public surface
CATALYSTS = _mod.CATALYSTS
discover = _mod.discover
build_queries = _mod.build_queries
gather_news = _mod.gather_news
llm_rerank = _mod.llm_rerank
llm_rerank_all = _mod.llm_rerank_all

__all__ = ["CATALYSTS", "discover", "build_queries", "gather_news",
           "llm_rerank", "llm_rerank_all"]
