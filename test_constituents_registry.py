"""
test_constituents_registry.py
=============================
Proves the NiftyStock registry (data_agent/constituents/registry.py), offline:

  1. Loaders read the real canonical files (symbols/sectors/weights/breeze map).
  2. validate() reproduces the alignment result (CSV==map==constituents, sum=100).
  3. require_files() passes when core files exist; missing_files() reports them.
  4. Files are NOT relocated — the canonical paths still point where the app reads.
"""
from __future__ import annotations
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []
def check(label, cond):
    results.append(bool(cond)); print(f"  [{'PASS' if cond else 'FAIL'}] {label}"); return cond
def hr(t): print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72)

spec = importlib.util.spec_from_file_location("reg", HERE + "/data_agent/constituents/registry.py")
R = importlib.util.module_from_spec(spec); sys.modules["reg"] = R; spec.loader.exec_module(R)

# ── 1. loaders ──────────────────────────────────────────────────────────────
hr("1. LOADERS over the canonical files")
syms = R.symbols()
check("symbols() returns 50", len(syms) == 50)
check("sectors_map() covers all 50", len(R.sectors_map()) == 50)
bm = R.breeze_map()
check("breeze_map() returns 50", len(bm) == 50)
check("breeze_code(ZOMATO) -> ZOMLIM (from JSON, not guessed)", R.breeze_code("ZOMATO") == "ZOMLIM")
check("weights() sums to ~100", abs(sum(R.weights().values()) - 100.0) < 0.05)

# ── 2. validate() ───────────────────────────────────────────────────────────
hr("2. validate() — Python port of the alignment check")
v = R.validate()
print("  result:", {k: v[k] for k in ("ok", "counts", "weights_sum")})
check("alignment ok (CSV==map==constituents, sum=100)", v["ok"] is True)
check("no errors", v["errors"] == [])
check("all three counts are 50",
      v["counts"]["csv"] == v["counts"]["breeze_map"] == v["counts"]["constituents"] == 50)

# ── 3. fail-fast guard ──────────────────────────────────────────────────────
hr("3. require_files() guard")
check("no core files missing", R.missing_files(core_only=True) == [])
try:
    R.require_files(); ok = True
except RuntimeError:
    ok = False
check("require_files() does not raise (all present)", ok)

# ── 4. files not relocated ──────────────────────────────────────────────────
hr("4. FILES STAY PUT (paths unchanged, app not broken)")
check("constituents.py still at strategy_framework/config",
      R.PATHS["constituents"].endswith("strategy_framework/config/constituents.py"))
check("CSV still at repo root", R.PATHS["csv"].endswith("/nifty-50-stock-list.csv"))
check("sync script still at fetching", R.PATHS["sync"].endswith("data_agent/fetching/sync_nifty50_to_now.py"))
check("all canonical paths exist on disk", all(os.path.exists(p) for k, p in R.PATHS.items() if k in R.CORE_FILES))

# ── 5. sector taxonomy single-source (membership reconciles to CSV) ─────────
hr("5. SECTOR TAXONOMY membership -> CSV (name bridge)")
members = R.taxonomy_members()
check("sector_tree has 50 companies", len(members) == 50)
unresolved = [n for n in members if R.name_to_ticker(n) is None]
check("every tree company resolves to a CSV ticker", unresolved == [])
check("short-form bridged (HCLTech -> HCLTECH)", R.name_to_ticker("HCLTech") == "HCLTECH")
check("auto-match works (Reliance Industries -> RELIANCE)", R.name_to_ticker("Reliance Industries") == "RELIANCE")
check("validate() confirms tree membership == CSV", v["sector_tree_membership_ok"] is True)
check("canonical sectors exposed", len(R.canonical_sectors()) >= 10)

# ── summary ─────────────────────────────────────────────────────────────────
hr("HOW IT FARES")
n_pass, n = sum(results), len(results)
print(f"  {n_pass}/{n} checks passed")
print("  one registry for the NIFTY-50 universe; files centralized in ACCESS, not moved")
sys.exit(0 if n_pass == n else 1)
