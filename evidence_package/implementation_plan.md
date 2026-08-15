# Implementation Plan — Invariant Framework Corrective Action

Transition the cockpit card validation framework from a UI mock to a binding engine-side validator in `vrp_pipeline.py`.

## User Review Required

> [!IMPORTANT]
> - **Engine-Side Invariant Validation**: The backend `vrp_pipeline.py` will now compute actual card invariants (T-A to T-I) for each option chain slice and underlying price feed.
> - **Mock Cleanup**: The frontend `IntradayPanel.tsx` will delete all `checkInvariants` and `simulateInconsistency` code, rendering only the `invariants` block received directly from the backend API.
> - **Self-Audit List**: We will deliver a self-audit of the codebase for similar mock-check patterns.

---

## Proposed Changes

### 1. Backend Invariant Engine

#### [MODIFY] [vrp_pipeline.py](file:///Users/deepak/antigravity/NiftyOptions/backend/quant/vrp_pipeline.py)
- Refactor `decompose_skew()` to accept actual data structures (options chain records, spot prices, bid-ask quotes).
- Compute invariants T-A through T-I:
  - **T-A**: $\Delta RR_{float} = \Delta Call_{25} - \Delta Put_{25} \pm 0.05$ vpt.
  - **T-B**: $\Delta RR_{fixed} = (1 - \text{artifact\_share}) \times \Delta RR_{float}$ (signs must agree, else flag mixed).
  - **T-C**: $\Delta RR_{fixed} = \Delta Call_{fixed} - \Delta Put_{fixed}$.
  - **T-D**: IV changes in vol points (`vpt`), $| \Delta | > 10$ vpt flags.
  - **T-F**: OI-join strike alignment.
  - **T-I**: Same-strike put/call IV parity check.
- Embed the `invariants` result block in the return payload.

### 2. Frontend Cockpit Wiring

#### [MODIFY] [IntradayPanel.tsx](file:///Users/deepak/antigravity/NiftyOptions/src/components/IntradayPanel.tsx)
- Remove `simulateInconsistency` checkbox and `checkInvariants()` function.
- Read `invariants` state directly from the backend API payload. If `passed: false`, render the generic `DATA_INCONSISTENT` card dynamically displaying the violated invariant ID, measured parameters, and strict rules.

### 3. API Updates

#### [MODIFY] [main.py](file:///Users/deepak/antigravity/NiftyOptions/backend/main.py)
- Update `/api/realized-metrics` and options pricing calculation logic to trigger the backend invariant check on active session chains.

---

## Verification Plan

### Automated Tests
- Update `test_minute_analytics.py` to:
  - Pass a corrupted/poisoned options data array (violating T-B) and assert the backend returns `passed: false` with correct failure IDs.
  - Pass a valid options data array and assert `passed: true`.
- Run: `./scratch_scripts/breeze_env/bin/python scratch_scripts/test_minute_analytics.py`
