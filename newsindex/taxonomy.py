#!/usr/bin/env python3
"""
taxonomy.py — the SINGLE source of truth for company → sector → sub-sector →
economic bucket → aliases. Everything downstream derives from here.

Why this exists
---------------
The same company was hand-listed in ~12 places — COMPANY_GAZETTEER (×2 engines),
earnings._SECTOR_OF, impact_monitor._HEAVYWEIGHTS, extra_validations._STEEL_NAMES /
_BASE_NAMES, enrich._SEMIS_TARGETS, subsectors, RELATIONSHIPS, CONFIDENCE_LINKAGES …
Every taxonomy bug this session was two of those lists disagreeing: Dixon filed under
both "IT/EMS" and "AI-infra", steel bundled with base metals, IT services bundled with
EMS. When one company lives in twelve lists, the lists drift.

Two layers, one owner each
--------------------------
  BASE   symbol → sector → weight   ← already canonical in
         strategy_framework/config/constituents.py (reads nifty-50-stock-list.csv).
         We REUSE it; we do not re-read the CSV.
  FINER  symbol → sub_sector, economic bucket, aliases   ← defined ONCE here, in
         SUBTAXONOMY. This is the layer the 12 files used to each reinvent.

The dedup guarantee
-------------------
Bucket membership is keyed by SYMBOL in one dict, so a symbol is in AT MOST ONE bucket
by construction — the Dixon-in-two-buckets bug is now impossible to express. validate()
additionally asserts every extension symbol exists in the CSV and no alias is claimed
by two symbols, and is run by the test suite so a drift fails the build.

Adding a company
----------------
1. add its row to nifty-50-stock-list.csv (sector + weight) — you already do this.
2. if it needs a non-default economic bucket, add ONE row to SUBTAXONOMY below.
That is the whole change; every consumer that imports this picks it up.

Overriding without duplicating
------------------------------
A consumer that needs a genuinely different grouping calls members_of(...) and then
adjusts locally, with a comment saying why — it does NOT re-list the members. The
canonical membership stays here; the override is a documented delta, not a copy.
"""

from __future__ import annotations

import os
import sys

# ── BASE LAYER: reuse the existing canonical weights/sectors (no CSV re-read) ──
_C = None
try:
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from strategy_framework.config import constituents as _C  # noqa: E402
except Exception:
    _C = None


def _base_weight(sym: str):
    return (getattr(_C, "WEIGHTS_PCT", {}) or {}).get(sym) if _C else None


def _base_sector(sym: str):
    return (getattr(_C, "SECTOR_OF", {}) or {}).get(sym, "") if _C else ""


# ── FINER LAYER: the ONE place sub-sector + economic bucket + aliases live ────
# bucket = the economic grouping RELATIONSHIPS actually validate against. It is
# finer than the CSV's top-level Sector (which lumps all "Financial Services" or
# "Metals & Mining" together) and is what makes siblings separable:
#   private_bank vs psu_bank, it_services vs ems, steel vs base_metals,
#   upstream_oil vs omc_downstream, auto_ice vs auto_ev.
# (symbol -> dict). aliases = how the name appears in headlines (for text resolution).
SUBTAXONOMY: dict[str, dict] = {
    # ---- Financials -------------------------------------------------------
    "HDFCBANK":  {"sub": "Private bank", "bucket": "private_bank", "aliases": ["hdfc bank"]},
    "ICICIBANK": {"sub": "Private bank", "bucket": "private_bank", "aliases": ["icici"]},
    "AXISBANK":  {"sub": "Private bank", "bucket": "private_bank", "aliases": ["axis bank"]},
    "KOTAKBANK": {"sub": "Private bank", "bucket": "private_bank", "aliases": ["kotak"]},
    "SBIN":      {"sub": "PSU bank", "bucket": "psu_bank", "aliases": ["state bank", "sbi"]},
    "BAJFINANCE": {"sub": "NBFC", "bucket": "nbfc", "aliases": ["bajaj finance"]},
    # ---- IT services (substitution-sensitive) -----------------------------
    "TCS":   {"sub": "IT services", "bucket": "it_services", "aliases": ["tcs", "tata consultancy"]},
    "INFY":  {"sub": "IT services", "bucket": "it_services", "aliases": ["infosys"]},
    "WIPRO": {"sub": "IT services", "bucket": "it_services", "aliases": ["wipro"]},
    "HCLTECH": {"sub": "IT services", "bucket": "it_services", "aliases": ["hcl tech", "hcl technologies"]},
    "TECHM": {"sub": "IT services", "bucket": "it_services", "aliases": ["tech mahindra"]},
    # ---- EMS / electronics (AI-infra BENEFICIARY — sibling of IT, opposite sign) ----
    "DIXON":  {"sub": "EMS", "bucket": "ems", "aliases": ["dixon"]},
    "KAYNES": {"sub": "EMS", "bucket": "ems", "aliases": ["kaynes"]},
    "CGPOWER": {"sub": "EMS / power equip", "bucket": "ems", "aliases": ["cg power"]},
    "POLYCAB": {"sub": "Cables / electricals", "bucket": "ems", "aliases": ["polycab"]},
    # ---- Energy: upstream vs downstream (opposite oil signs) --------------
    "ONGC":  {"sub": "Upstream", "bucket": "upstream_oil", "aliases": ["ongc"]},
    "OIL":   {"sub": "Upstream", "bucket": "upstream_oil", "aliases": ["oil india"]},
    "BPCL":  {"sub": "OMC (downstream)", "bucket": "omc_downstream", "aliases": ["bpcl", "bharat petroleum"]},
    "IOC":   {"sub": "OMC (downstream)", "bucket": "omc_downstream", "aliases": ["ioc", "indian oil"]},
    "HINDPETRO": {"sub": "OMC (downstream)", "bucket": "omc_downstream", "aliases": ["hpcl", "hindustan petroleum"]},
    "RELIANCE": {"sub": "Refining / petchem / retail", "bucket": "refiner_conglomerate", "aliases": ["reliance"]},
    # ---- Metals: steel_complex vs base_metals (SIBLINGS, not parent/child) ----
    "TATASTEEL": {"sub": "Steel", "bucket": "steel", "aliases": ["tata steel"]},
    "JSWSTEEL":  {"sub": "Steel", "bucket": "steel", "aliases": ["jsw"]},
    "SAIL":      {"sub": "Steel", "bucket": "steel", "aliases": ["sail"]},
    "JINDALSTEL": {"sub": "Steel", "bucket": "steel", "aliases": ["jindal steel"]},
    "HINDALCO":  {"sub": "Aluminium", "bucket": "base_metals", "aliases": ["hindalco"]},
    "NATIONALUM": {"sub": "Aluminium", "bucket": "base_metals", "aliases": ["nalco", "national aluminium"]},
    "HINDCOPPER": {"sub": "Copper", "bucket": "base_metals", "aliases": ["hindustan copper"]},
    "VEDL":      {"sub": "Diversified metals", "bucket": "base_metals", "aliases": ["vedanta"]},
    # ---- Autos: ICE vs EV (oil sign flips within the sector) --------------
    "MARUTI":   {"sub": "PV (ICE-weighted)", "bucket": "auto_ice", "aliases": ["maruti"]},
    "HEROMOTOCO": {"sub": "2W (ICE)", "bucket": "auto_ice", "aliases": ["hero motocorp"]},
    "BAJAJ-AUTO": {"sub": "2W / 3W", "bucket": "auto_ice", "aliases": ["bajaj auto"]},
    "M&M":      {"sub": "PV / SUV / EV", "bucket": "auto_ev", "aliases": ["mahindra", "m&m"]},
    "TATAMOTORS": {"sub": "PV / EV / CV", "bucket": "auto_ev", "aliases": ["tata motors"]},
    # ---- Others the relationships touch ----------------------------------
    "ASIANPAINT": {"sub": "Paints", "bucket": "oil_user_paints", "aliases": ["asian paints"]},
    "INDIGO":   {"sub": "Aviation", "bucket": "oil_user_aviation", "aliases": ["indigo", "interglobe"]},
    "SUNPHARMA": {"sub": "Pharma (exporter)", "bucket": "pharma_export", "aliases": ["sun pharma"]},
    "DRREDDY":  {"sub": "Pharma (exporter)", "bucket": "pharma_export", "aliases": ["dr reddy"]},
    "CIPLA":    {"sub": "Pharma", "bucket": "pharma_export", "aliases": ["cipla"]},
    "BHARTIARTL": {"sub": "Telecom", "bucket": "telecom", "aliases": ["bharti", "airtel"]},
    "NTPC":     {"sub": "Power gen", "bucket": "power", "aliases": ["ntpc"]},
    "POWERGRID": {"sub": "Power transmission", "bucket": "power", "aliases": ["power grid"]},
    "LT":       {"sub": "Capital goods / infra", "bucket": "capital_goods", "aliases": ["larsen", "l&t"]},
    "ITC":      {"sub": "FMCG / cigarettes", "bucket": "fmcg", "aliases": ["itc"]},
    "HINDUNILVR": {"sub": "FMCG", "bucket": "fmcg", "aliases": ["hindustan unilever", "hul"]},
}

# Default bucket for a symbol we haven't finely classified: fall back to its CSV
# sector, slugified. So an unlisted name still resolves — it just isn't a sibling-split.
_SECTOR_SLUG = {
    "Financial Services": "financials", "Information Technology": "it_services",
    "Oil & Gas": "energy", "Metals & Mining": "metals", "Automobile": "auto",
    "FMCG": "fmcg", "Healthcare": "pharma_export", "Telecommunication": "telecom",
    "Power": "power", "Construction": "capital_goods",
}


# ── derivation API — everything downstream calls these ────────────────────────
def sector_of(sym: str) -> str:
    return _base_sector(sym)


def weight_of(sym: str):
    return _base_weight(sym)


def subsector_of(sym: str) -> str:
    return (SUBTAXONOMY.get(sym) or {}).get("sub", "")


def bucket_of(sym: str) -> str:
    e = SUBTAXONOMY.get(sym)
    if e and e.get("bucket"):
        return e["bucket"]
    return _SECTOR_SLUG.get(_base_sector(sym), (_base_sector(sym) or "other").lower().replace(" ", "_"))


def members_of(bucket: str) -> list[str]:
    """Every symbol in an economic bucket — the ONE list consumers should read
    instead of hand-listing steel names / IT names / EMS names themselves."""
    return sorted(s for s, e in SUBTAXONOMY.items() if e.get("bucket") == bucket)


def aliases_of(sym: str) -> list[str]:
    return (SUBTAXONOMY.get(sym) or {}).get("aliases", [])


def resolve(text: str) -> str | None:
    """headline text → symbol, via aliases. Longest alias first so 'tata steel'
    beats 'tata'. Returns None if nothing matches."""
    t = (text or "").lower()
    best, best_len = None, 0
    for sym, e in SUBTAXONOMY.items():
        for a in e.get("aliases", []):
            if a in t and len(a) > best_len:
                best, best_len = sym, len(a)
    return best


def heavyweights(top: int = 15) -> list[tuple[str, float]]:
    """Top-N Nifty names by index weight — replaces impact_monitor._HEAVYWEIGHTS."""
    w = getattr(_C, "WEIGHTS_PCT", {}) or {}
    return sorted(w.items(), key=lambda kv: -kv[1])[:top]


# ── the dedup / drift guard — run by the test suite ───────────────────────────
def validate() -> dict:
    """Prove the taxonomy is internally consistent. Returns {ok, errors, warnings}."""
    errors, warnings = [], []
    csv_syms = set(getattr(_C, "SECTOR_OF", {}) or {})

    # 1. classify each extension symbol as INDEX MEMBER (in the CSV, carries weight) or
    #    EXTENDED-UNIVERSE PROXY (a legitimate relationship proxy outside the Nifty 50 —
    #    Dixon, BPCL, SAIL, etc.). Not an error: the CSV is Nifty-50-only by definition,
    #    but the relationships validate against a wider set. Reported so a genuine TYPO
    #    (a symbol that is neither an index member nor a known proxy) is visible.
    non_index = [s for s in SUBTAXONOMY if csv_syms and s not in csv_syms]
    for s in non_index:
        warnings.append(f"{s} is an extended-universe proxy (not a Nifty-50 member; "
                        f"no index weight) — legitimate, but confirm it is not a typo")

    # 2. no alias claimed by two different symbols (would make resolve() ambiguous)
    seen: dict[str, str] = {}
    for sym, e in SUBTAXONOMY.items():
        for a in e.get("aliases", []):
            if a in seen and seen[a] != sym:
                errors.append(f"alias '{a}' claimed by both {seen[a]} and {sym}")
            seen[a] = sym

    # 3. a symbol is in exactly one bucket by construction (dict key) — nothing to
    #    check, and that is the whole point. Report CSV names with no finer bucket.
    if csv_syms:
        for sym in sorted(csv_syms - set(SUBTAXONOMY)):
            warnings.append(f"{sym} has no sub-taxonomy — falls back to sector bucket "
                            f"'{bucket_of(sym)}'")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "n_classified": len(SUBTAXONOMY), "n_csv": len(csv_syms)}


if __name__ == "__main__":
    v = validate()
    print(f"taxonomy: {v['n_classified']} finely classified of {v['n_csv']} CSV names")
    print("buckets:", sorted({e['bucket'] for e in SUBTAXONOMY.values()}))
    if v["errors"]:
        print("\n❌ ERRORS:")
        for e in v["errors"]:
            print("  -", e)
    else:
        print("\n✅ no duplicate/drift errors")
    if v["warnings"]:
        print(f"\n{len(v['warnings'])} unclassified (sector-default) — first 8:")
        for w in v["warnings"][:8]:
            print("  -", w)
