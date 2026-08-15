# Antigravity Brief — Verification Evidence Package (Skew Pipeline & Invariant Framework)
**Date:** 07-Jul-2026 | **Type:** Evidence generation — no new features
**Purpose:** produce a package that permits independent recomputation of the skew card's numbers and audit of the invariant framework by an external reviewer. Every artifact below will be arithmetically re-derived from raw inputs outside this codebase; the package must therefore be complete enough that the reviewer needs nothing else.
**References:** minute_analytics_v1_brief.md §3.4/§3.4a; invariant_framework_corrective_brief.md §2–4.

**General rules:**
- All artifacts are plain text (CSV/JSON/py), one file each, named exactly as below.
- Every timestamp exact to the second, IST, ISO-8601. "Approximately 10:30" is not a timestamp.
- Raw values as stored — no rounding, no reformatting, no summarization. If a value is null in the store, it is null in the export.
- Do not "clean up" anything for presentation. Discrepancies in the raw data are the point of the exercise.

---

## A. Raw recomputation inputs (highest priority — independent of all other items)

**A1 — `chain_open.csv`:** the full stored chain snapshot nearest 09:15:00 today (or the most recent complete session): one row per (strike, cp) with columns exactly: `snapshot_ts, expiry_ts, strike, cp, bid, ask, mid, iv_mid, oi, volume`. Include ALL strikes captured, both sides — no filtering to "relevant" strikes.

**A2 — `chain_afternoon.csv`:** same schema, one snapshot from mid-session (~13:30 or the minute used in A3).

**A3 — `card_readout.json`:** the skew card's exact displayed/emitted values for the A2 minute, as emitted by the engine (not transcribed from screen): RR_floating_level, dRR_floating, RR_fixed_level, dRR_fixed, artifact_share, put_leg_dIV_vpt, call_leg_dIV_vpt, anchor_strikes (the session-open fixed set), atm_strike, forward_used, expiry_measured, reference_window (which Δ window), and the full `invariants` block.

**A4 — `spot_series.csv`:** index spot at 09:15 and at the A2 minute (`ts, spot`), plus the r and T values the engine used for that minute (`rate_used, T_years_used`) — stated, not inferred.

Acceptance for section A: an external reviewer must be able to recompute every number in A3 from A1+A2+A4 alone. If any input needed for recomputation is missing from the package, the package fails.

## B. Engine source (verbatim)

**B1 — `strike_estimation.py.txt`:** the verbatim function(s) computing forward, per-strike IV, per-strike delta, and the 25Δ interpolation. Reviewer checks four properties: forward from parity (not spot), OTM-side smile only, delta uses σ(K) per strike (not flat ATM vol), interpolation in delta space between bracketing listed strikes.

**B2 — `invariant_engine.py.txt`:** the verbatim invariant-computation code producing the `invariants` payload block.

## C. Test suite (code, then output — in that order)

**C1 — `test_skew_invariants.py.txt`:** verbatim test code for tests 21–28 and 23a–23g. Assertions must consume computed values; fixtures must construct inputs, not expected outputs.

**C2 — `test_run.txt`:** full unedited runner output (pytest -v or equivalent) of C1, same commit as B1/B2. Include the commit hash in the header of every file in sections B and C.

## D. Payload proofs (three emissions, engine-produced)

**D1 — `payload_pass.json`:** one real emission with all invariants passed and the `checked` list complete.
**D2 — `payload_fail.json`:** the corrupted-fixture negative-path emission — legs deliberately inconsistent with dRR_fixed — showing `passed: false` with measured values and the violated rule, produced by the live pipeline (state the injection method in a comment field; no UI toggles may exist to produce this).
**D3 — `payload_skipped.json`:** an emission with one input deliberately absent, showing that invariant as `skipped` with the missing field named.

## E. Removal & audit evidence

**E1 — `grep_proof.txt`:** command + output showing `simulateInconsistency`, "Simulate Mid Pollution", "Simulate DTE" absent from the production build source; `checkInvariants` absent from all `.tsx`.
**E2 — `self_audit.md`:** the §3 audit list from the corrective brief — every guard/gate/check in the codebase with file:line, spec reference, real-or-mock verdict, fix status. If the claim is "no further mocks found," list every guard *inspected* with its verdict, so the claim is checkable rather than bare.

## F. Declarations (one file, `declarations.md`, plain statements — each will be tested against A–E)

1. Which skew convention the production card currently computes (25Δ per §3.4a / ATM±offset / other) and since which commit.
2. The strike set at which leg attribution is differenced (must be the session-open fixed anchors per T-C).
3. Whether the DTE<2 expiry splice is implemented and active, and which expiry the card measured today.
4. The dead-band values currently applied to each config-chip input, and confirmation the "unclassified" fallback and fifth configuration exist in code (file:line).
5. Whether 14-Jul chain capture was live from today's open (first captured snapshot timestamp for the 14-Jul expiry).

---

## Packaging & sequencing
Single archive or ordered message set, files named exactly as above, section A first — it is independent of all fixes and can ship immediately even if B–E are still in progress. Do not delay A waiting for the invariant PR: A verifies the computation layer as it exists today, which is precisely the question.

**Note on intent:** this package is not adversarial ceremony. The reviewer will recompute A3 from A1/A2/A4; where numbers match, the pipeline is *cleared* — evidence of correctness is the fastest path to unblocking further cockpit work. Where they diverge, the divergence localizes the bug to a specific step, which is cheaper for everyone than another review cycle on descriptions.
