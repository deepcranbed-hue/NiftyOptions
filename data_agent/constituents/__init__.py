"""
data_agent.constituents — the authoritative home for the NIFTY-50 universe.

Centralizes ACCESS to (does not relocate) the five canonical files: the CSV
registry, breeze_symbol_map.json, constituents.py, the sync script, and the
weekly validator. Provides symbols/sectors/weights/breeze-code loaders, a
fail-fast require_files() guard, and validate() (a Python port of the alignment
check) so drift can be surfaced by the health/alert layer.
"""
from .registry import (  # noqa: F401
    PATHS, CORE_FILES, symbols, sectors_map, weights, breeze_map, breeze_code,
    missing_files, require_files, validate,
    name_to_ticker, taxonomy, canonical_sectors, taxonomy_members,
)
