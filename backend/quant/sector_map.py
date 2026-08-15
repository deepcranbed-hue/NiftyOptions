"""
sector_map.py
-------------
Superseded by sector_tree.py for 3-level hierarchy.
This file now acts as a compatibility wrapper to expose the required functions 
and constants for the rest of the pipeline, deriving them directly from sector_tree.py
to maintain a single source of truth.
"""

from __future__ import annotations
from collections import defaultdict
from .sector_tree import SECTOR_TREE, WEIGHTS, WEIGHTS_AS_OF

AS_OF = WEIGHTS_AS_OF

CANONICAL_SECTORS = list(SECTOR_TREE.keys())

def weights() -> dict[str, float]:
    return WEIGHTS.copy()

def sector_of() -> dict[str, str]:
    out = {}
    for sec, inds in SECTOR_TREE.items():
        for ind, comps in inds.items():
            for c in comps:
                out[c] = sec
    return out

def sector_weights() -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for sec, inds in SECTOR_TREE.items():
        for ind, comps in inds.items():
            for c in comps:
                out[sec] += WEIGHTS.get(c, 0.0)
    return dict(out)

def validate(enum=CANONICAL_SECTORS) -> dict:
    from .sector_tree import validate as tree_validate
    tv = tree_validate()
    
    sw = sector_weights()
    orphans = [s for s in enum if sw.get(s, 0.0) == 0.0]
    not_in_enum = [s for s in sw if s not in enum]
    
    assert not orphans, f"Orphans found: {orphans}"
    assert not not_in_enum, f"Not in enum found: {not_in_enum}"
    assert tv["ok"], "Tree validation failed: " + str(tv)
    
    return {
        "sector_weights": {s: round(sw.get(s, 0.0), 2) for s in enum},
        "total_weight_covered": round(tv["weights_sum"], 1),
    }

if __name__ == "__main__":
    rep = validate()
    print("Validation passed. Sector weights:")
    for s, w in sorted(rep["sector_weights"].items(), key=lambda kv: -kv[1]):
        print(f"  {s:<15} {w:5.2f}%")
