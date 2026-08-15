# DATA SOURCES — the single-source-of-truth rule (MANDATORY)

This project has ONE editable master for the NIFTY-50 universe. Every other file that
mentions constituents, sectors, weights, or broker codes must **derive from it or be
validated against it** — never hand-maintained in parallel. This rule exists because
duplicated symbol lists silently drifted before.

> **Rule of thumb: read the data folder (`data_agent/constituents/`) first for any
> constituent/sector/weight/code naming. If you need the universe, import the
> registry — do not hardcode a list.**

---

## The masters (edit ONLY these)

| Concern | Master file | Who owns it |
|---|---|---|
| Symbols · sectors · weights | **`nifty-50-stock-list.csv`** (Symbol, Company Name, Sector, Weight) | human-editable |
| Sector taxonomy (3-level canonical hierarchy) | **`backend/quant/sector_tree.py`** (`SECTOR_TREE`) | human-editable (analytical) |

Everything else is **generated from** or **validated against** these:

| Derived / validated file | Relationship to a master |
|---|---|
| `strategy_framework/config/breeze_symbol_map.json` | **generated** by `generate_breeze_mappings.py` from NSE + SecurityMaster |
| `strategy_framework/config/constituents.py` | **derives** `WEIGHTS_PCT` / `SECTOR_OF` from the CSV at import (no hardcoded dict) |
| `scratch_scripts/sync_nifty50_to_now.py` | **loads** `breeze_symbol_map.json` dynamically (no hardcoded list) |
| `backend/quant/sector_map.py` | thin wrapper over `sector_tree.py` |

## The single gateway

All code should reach the universe through the registry, not by opening files itself:

```python
from data_agent.constituents import (
    symbols, sectors_map, weights, breeze_code,   # the universe
    taxonomy, canonical_sectors, name_to_ticker,  # the sector hierarchy
    require_files, validate,                       # guards
)
```

`require_files()` fails fast at startup if a core file is missing. `validate()` is the
Python port of the weekly alignment check — call it anywhere (health badge, CI).

## How the two masters relate

- The **CSV** is the operational universe (tickers, NSE sectors, index weights the
  quant signal blend uses).
- `sector_tree.py` is the **analytical taxonomy** for the news/attribution layer. It
  legitimately carries its **own weight snapshot** (dated free-float, sums ≈ 95.64, not
  100) — those are *not* the operational weights and must not be merged into the CSV
  blindly (doing so changes signal behavior and breaks the sum-to-100 invariant).
- What IS enforced single-source: **membership**. `sector_tree`'s 50 companies must
  reconcile to the CSV's 50 tickers. Names are bridged in exactly ONE place —
  `registry._NAME_OVERRIDES` (9 short-forms; the rest auto-match on Company Name).
  `validate()` fails if any tree company can't resolve to a CSV ticker or the sets
  differ.

## Change procedure (when NSE rebalances or a weight moves)

1. Edit **`nifty-50-stock-list.csv`** (add/remove rows, update Weight; keep Weight summing to 100).
2. Regenerate the Breeze map: `python scratch_scripts/generate_breeze_mappings.py`.
3. Update **`sector_tree.py`** membership if a company changed; add any new short-form
   to `registry._NAME_OVERRIDES` if it doesn't auto-match.
4. Run **`python -m data_agent.constituents.registry`** (or `validate()`), and the
   weekly `validate_constituents_alignment.py` — both must report OK.

Nothing else should be edited by hand. If you find a hardcoded constituent/sector list
anywhere else (e.g. `data_access.py`, `backfill_nifty50_minute_bars.py`), point it at
the registry instead of maintaining a copy.

## Open decision (weights)

The CSV weights are round PRIOR estimates (e.g. HDFCBANK 11.6). `sector_tree.py` has a
more accurate dated free-float snapshot (HDFC Bank 6.49, sum 95.64). They differ on
purpose today. If you want a **single weight source**, decide which is authoritative and
we make the other derive from it — this changes numbers the signals consume, so it's a
deliberate choice, not an automatic merge.
