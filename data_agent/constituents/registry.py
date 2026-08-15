"""
data_agent/constituents/registry.py
===================================
The one authoritative home for the NIFTY-50 constituent universe. It does NOT move
the underlying files (they're referenced by hard-coded paths across the app —
`strategy_framework.config.constituents` is imported by 6+ signals, the CSV/JSON
are read by many modules, the sync script is invoked by two endpoints). Relocating
them would break all of that. Instead this centralizes ACCESS + VALIDATION in one
place so the data agent has a single source of truth to read from.

Canonical files (categorized as the review concluded):
  CORE (app breaks without them):
    * nifty-50-stock-list.csv                          -> symbols + sectors
    * strategy_framework/config/breeze_symbol_map.json -> NSE ticker -> Breeze code
    * strategy_framework/config/constituents.py        -> free-float index weights
    * data_agent/fetching/sync_nifty50_to_now.py       -> Breeze collector (1m + futures)
    * data_agent/fetching/sync_nifty50_bars_yf.py      -> the ONE daily-bar writer (Yahoo)
  QA-ONLY (runtime-optional):
    * scratch_scripts/validate_constituents_alignment.py

Provides: symbols(), sectors_map(), weights(), breeze_map(), breeze_code(),
a fail-fast require_files() startup guard, and validate() — a Python port of the
weekly alignment check so drift can be surfaced in the health/alert layer, not just
by a cron script. Pure file IO — tests offline.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# ── canonical paths (one place that names every file) ───────────────────────
PATHS = {
    "csv":          os.path.join(_ROOT, "nifty-50-stock-list.csv"),
    "breeze_map":   os.path.join(_ROOT, "strategy_framework", "config", "breeze_symbol_map.json"),
    "constituents": os.path.join(_ROOT, "strategy_framework", "config", "constituents.py"),
    "sector_tree":  os.path.join(_ROOT, "backend", "quant", "sector_tree.py"),
    "sync":         os.path.join(_ROOT, "data_agent", "fetching", "sync_nifty50_to_now.py"),
    "daily_sync":   os.path.join(_ROOT, "data_agent", "fetching", "sync_nifty50_bars_yf.py"),
    "validator":    os.path.join(_ROOT, "scratch_scripts", "validate_constituents_alignment.py"),
}
CORE_FILES = ("csv", "breeze_map", "constituents", "sync")   # validator + sector_tree are QA/analytical

# The ONE place display-names in sector_tree.py are reconciled to CSV tickers.
# 41/50 auto-match on normalized Company Name; these 9 short-forms need an explicit
# bridge. If you rename a tree leaf, add it here — nowhere else.
_NAME_OVERRIDES = {
    "TCS": "TCS", "HCLTech": "HCLTECH", "ONGC": "ONGC", "Power Grid": "POWERGRID",
    "Sun Pharma": "SUNPHARMA", "Adani Ports": "ADANIPORTS",
    "HDFC Life Insurance": "HDFCLIFE", "Max Healthcare": "MAXHEALTH",
    "Eternal": "ZOMATO",
}


# ── loaders ─────────────────────────────────────────────────────────────────
def symbols() -> list[str]:
    """The 50 tickers, from the CSV registry (the app's canonical list)."""
    with open(PATHS["csv"], newline="") as f:
        return [r["Symbol"].strip().upper() for r in csv.DictReader(f) if r.get("Symbol")]


def sectors_map() -> dict[str, str]:
    with open(PATHS["csv"], newline="") as f:
        return {r["Symbol"].strip().upper(): (r.get("Sector") or "").strip()
                for r in csv.DictReader(f) if r.get("Symbol")}


def breeze_map() -> dict[str, str]:
    """NSE ticker -> Breeze short code (authoritative JSON)."""
    with open(PATHS["breeze_map"]) as f:
        return {str(k).upper(): v for k, v in json.load(f).items()}


def breeze_code(symbol: str) -> str:
    return breeze_map().get(symbol.upper(), symbol.upper())


def _constituents_module():
    spec = importlib.util.spec_from_file_location("_constituents_reg", PATHS["constituents"])
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def weights() -> dict[str, float]:
    """Free-float index weights (%) from constituents.py (which reads the CSV)."""
    return dict(_constituents_module().WEIGHTS_PCT)


# ── name <-> ticker bridge (for the sector_tree taxonomy) ───────────────────
import re as _re


def _norm_name(s: str) -> str:
    s = _re.sub(r"\b(Ltd\.?|Limited|India|Industries|Company|Corporation|Enterprise|"
                r"Enterprises|Laboratories|Motors|Insurance|Financial Services|Services)\b",
                "", s or "", flags=_re.I)
    return _re.sub(r"[^A-Za-z0-9]", "", s).upper()


def _csv_name_index() -> dict[str, str]:
    idx = {}
    with open(PATHS["csv"], newline="") as f:
        for r in csv.DictReader(f):
            sym = (r.get("Symbol") or "").strip().upper()
            nm = r.get("Company Name") or ""
            if sym:
                idx[_norm_name(nm)] = sym
    return idx


def name_to_ticker(name: str) -> str | None:
    """Resolve a sector_tree display name to a CSV ticker (auto-match, else override)."""
    if name in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[name]
    return _csv_name_index().get(_norm_name(name))


def _sector_tree_module():
    spec = importlib.util.spec_from_file_location("_sector_tree_reg", PATHS["sector_tree"])
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def taxonomy() -> dict:
    """The canonical 3-level sector hierarchy (the ONE taxonomy source)."""
    return _sector_tree_module().SECTOR_TREE


def canonical_sectors() -> list[str]:
    return list(taxonomy().keys())


def taxonomy_members() -> list[str]:
    out = []
    for _sec, inds in taxonomy().items():
        for _ind, comps in inds.items():
            out.extend(comps)
    return out


# ── fail-fast guard ─────────────────────────────────────────────────────────
def missing_files(core_only: bool = True) -> list[str]:
    keys = CORE_FILES if core_only else tuple(PATHS)
    return [k for k in keys if not os.path.exists(PATHS[k])]


def require_files() -> None:
    """Call at startup: raise loudly if any CORE config file is absent, instead of
    letting modules degrade silently (empty sectors, silent sync failures)."""
    miss = missing_files(core_only=True)
    if miss:
        raise RuntimeError(
            "NiftyStock: missing core constituent file(s): "
            + ", ".join(f"{k} -> {PATHS[k]}" for k in miss))


# ── validation (Python port of validate_constituents_alignment.py) ──────────
def validate() -> dict:
    """Cross-check that CSV, breeze_map.json and constituents.py agree, and that
    index weights sum to 100.0. Returns a structured result the alert layer can use.
    """
    errors: list[str] = []
    miss = missing_files(core_only=True)
    if miss:
        return {"ok": False, "errors": [f"missing: {miss}"], "checked": PATHS}

    csv_syms = set(symbols())
    map_syms = set(breeze_map())
    w = weights()
    con_syms = set(w)

    if csv_syms != map_syms:
        errors.append(f"CSV vs breeze_map differ: csv_only={sorted(csv_syms - map_syms)} "
                      f"map_only={sorted(map_syms - csv_syms)}")
    if csv_syms != con_syms:
        errors.append(f"CSV vs constituents differ: csv_only={sorted(csv_syms - con_syms)} "
                      f"constituents_only={sorted(con_syms - csv_syms)}")
    wsum = round(sum(w.values()), 4)
    if abs(wsum - 100.0) > 0.01:
        errors.append(f"weights sum to {wsum}, expected 100.0")

    # sector_tree MEMBERSHIP must reconcile to the CSV (the taxonomy is analytical
    # and may carry its own weights, but its 50 companies must be the CSV's 50).
    tree_ok = None
    tree_note = None
    if os.path.exists(PATHS["sector_tree"]):
        try:
            members = taxonomy_members()
            resolved = {name_to_ticker(n) for n in members}
            unresolved = sorted(n for n in members if name_to_ticker(n) is None)
            resolved.discard(None)
            tree_ok = (not unresolved) and (resolved == csv_syms)
            if unresolved:
                errors.append(f"sector_tree names not resolvable to a CSV ticker "
                              f"(add to _NAME_OVERRIDES): {unresolved}")
            elif resolved != csv_syms:
                errors.append(f"sector_tree membership != CSV: tree_only="
                              f"{sorted(resolved - csv_syms)} csv_only={sorted(csv_syms - resolved)}")
            # its weight snapshot legitimately differs — report, don't fail
            tw = round(sum(_sector_tree_module().WEIGHTS.values()), 2)
            tree_note = (f"sector_tree carries its own weight snapshot summing {tw} "
                         f"(!= CSV 100.0) — analytical, not the operational weights")
        except Exception as e:
            tree_note = f"sector_tree check skipped: {e}"

    return {"ok": not errors, "errors": errors,
            "counts": {"csv": len(csv_syms), "breeze_map": len(map_syms),
                       "constituents": len(con_syms), "sector_tree_members": len(taxonomy_members())
                       if os.path.exists(PATHS["sector_tree"]) else 0},
            "weights_sum": wsum,
            "sector_tree_membership_ok": tree_ok, "sector_tree_note": tree_note}


if __name__ == "__main__":
    print("core files present:", not missing_files())
    r = validate()
    print(json.dumps(r, indent=2))
    print("sample breeze codes:", {s: breeze_code(s) for s in ("RELIANCE", "ETERNAL", "TMPV")})
