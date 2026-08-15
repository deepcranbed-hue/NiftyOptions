# Antigravity Integration Brief — Adopting the Reference Skew Implementation
**Date:** 07-Jul-2026 | **Type:** Integration of supplied code — adopt, don't reimplement
**Files supplied:** `skew_engine.py`, `invariants.py`, `test_skew_invariants.py` (18/18 passing as delivered)
**References:** minute_analytics_v1_brief §3.4/§3.4a, invariant corrective brief, verification findings 07-Jul.
**Rule:** any deviation from the supplied files requires a stated reason traceable to a brief section, recorded in DECISIONS.md before the change is made.

---

## 1. Placement (target repo paths)

| Supplied file | Target path | Notes |
|---|---|---|
| `skew_engine.py` | `backend/quant/skew/skew_engine.py` | new package `backend/quant/skew/` with `__init__.py` re-exporting `decompose_skew`, `classify_configuration`, `flow_join` |
| `invariants.py` | `backend/quant/skew/invariants.py` | same package |
| `test_skew_invariants.py` | `backend/tests/test_skew_invariants.py` | must run in CI on every commit touching `backend/quant/` |

## 2. Deletions (same PR, not deferred)

- `backend/quant/vrp_pipeline.py :: decompose_skew` — the constants version (commit c3f784d5). Replace the function body with an import + delegation to `quant.skew.skew_engine.decompose_skew`, or delete and update call sites.
- `strike_estimation.py :: compute_strike_delta_and_interpolate` — merged-wing interpolator with the no-op parity loop. Delete outright; no call site may survive.
- Any remaining `iv_mid` **consumption** in the skew path. The column may stay in the schema for audit, but nothing computes from it — the engine inverts from mids. Grep proof required: no `iv_mid` reads inside `backend/quant/skew/`.

## 3. Wiring (the only new code Antigravity writes)

One thin adapter, `backend/quant/skew/adapter.py`, responsible for exactly four things:

1. **Chains:** load session-open and current snapshots for the measured expiry from the real store (`chain_snapshots`), as DataFrames with columns `strike, cp, bid, ask, mid, oi` (schema the engine expects; extra columns ignored). Open snapshot = first captured ≥ 09:15 IST; document the rule.
2. **Time inputs:** `T_open`, `T_curr` in year-fraction from actual snapshot→expiry timestamps (IST, to the second); `dte_days` likewise. No hand-set T.
3. **Expiry selection (D-MA-06):** if `dte_days < dte_splice_days`, pass `next_expiry_chain` from the store and stamp `expiry_measured` on the emission. If the next-expiry chain does not exist in the store, the emission gaps — the adapter must not fall back to the dying expiry.
4. **Auxiliary invariant inputs:** floating-leg deltas for T-A (from two consecutive engine runs' wing points), `oi_join` strikes (already emitted by the engine's flow block — pass through), `d_vix_vpt`/`d_atm_vpt` for T-H from the VIX minute stream, and the T-G recompute hook (`classify_fn=classify_configuration`, kwargs = the emission's own `configuration.inputs.measured`). Missing streams are passed as `None` → invariants report SKIPPED with the input named. Never fabricate an auxiliary input to force a PASS.

The adapter calls `invariants.evaluate(emission, ...)` and attaches the result to the emission **before** persistence. No emission reaches the store or the API without its invariant block.

## 4. Persistence & API surface

- Emissions persist to the `.state/` blackboard (skew pillar) and/or `realized_metrics` as the full JSON — including `thresholds_used`, `parity_flags`, `flow`, `invariants`, and all `status` fields. Nothing is stripped "for size"; the provenance IS the product.
- API endpoint serves the stored emission verbatim. The UI computes nothing.

## 5. UI wiring (`IntradayPanel.tsx`)

- Skew card renders exclusively from the emission: `invariants.passed == false` → existing DATA_INCONSISTENT presentation with `failures[].id/measured/rule`; `status == "EXPIRY_DEGENERATE"` → gap state with detail; `artifact_share.status` (QUIET / MIXED_REGIME / OK-with-value incl. negative) rendered as distinct states; `configuration` chip shows the label plus its `inputs.measured` values; every `PRIOR`-tagged threshold from `thresholds_used` badges its dependent number.
- No `checkInvariants`, no simulate toggles, no local recomputation. Grep gates from the corrective brief still apply.

## 6. Threshold registry governance

- `THRESHOLDS` is the single home for every parameter in the skew path. Adding a numeric literal anywhere else in `backend/quant/skew/` is a review-blocking defect (add a lint/grep rule: no float literals outside `THRESHOLDS`, tests, and math constants).
- Tags are load-bearing: `DERIVED` values (parity tolerance) must show `tol_source` on every flag they produce; `PRIOR` values surface their graduation path in the UI tooltip; overrides via `pr` emit tag `OVERRIDE`.
- DECISIONS.md: record **D-MA-09 — threshold registry with per-parameter provenance (PRIOR/STRUCT/DERIVED/OVERRIDE); spread-implied parity tolerance replaces fixed 0.50; spot dead-band flagged for z-score graduation at ≥30 sessions.**

## 7. Merge gates (all required, in order)

1. `pytest backend/tests/test_skew_invariants.py` — 18/18 on the target repo, unmodified tests. Any test edit requires a stated reason per the deviation rule.
2. Deletion greps pass (§2): no constants-`decompose_skew`, no merged-wing interpolator, no `iv_mid` reads in the skew package, no simulate toggles or `checkInvariants` in `.tsx`.
3. **Live negative path:** corrupt one leg of a real emission in a staging run → invariant block shows T-C FAILED with measured values → card renders DATA_INCONSISTENT. Screenshot NOT accepted; provide the emitted JSON and the corresponding UI state.
4. **Real-data run:** one full session's emissions from the actual store (user-exported snapshots, per the standing rule that verification inputs come from the user's store, not the implementer), with at least one emission showing DERIVED parity tolerances in `parity_flags` (i.e., real spreads flowed through).
5. `float`-literal lint on `backend/quant/skew/` clean per §6.

## 8. Explicit non-goals for this PR
No changes to correlation/dispersion engine, detectors, Global Cues, or cockpit layout beyond the skew card wiring. One concern per PR — this one is: real skew math, real invariants, mocks deleted.
