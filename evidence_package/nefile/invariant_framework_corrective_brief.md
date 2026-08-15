# Antigravity Corrective Brief — Invariant Framework: Mock Removal & Engine-Side Implementation
**Date:** 07-Jul-2026 | **Severity:** Blocking — data-integrity guard is non-functional
**Scope:** `vrp_pipeline.py` (engine), `IntradayPanel.tsx` (UI wiring only), test suite
**References:** minute_analytics_v1_brief.md §3.4 card-invariant rule, D-MA-07
**DECISIONS.md entry:** D-MA-07 amended (engine-side placement, binding) — already reflected in the main brief.

---

## 1. Defect statement

The invariant framework specified under D-MA-07 was implemented as a UI mock. Evidence, from your own explanation of the `DATA_INCONSISTENT` display (IntradayPanel.tsx:L28–36):

```typescript
const checkInvariants = () => {
  if (simulateInconsistency) {
    return { valid: false,
      reason: "T-B Failed: ΔRR_fixed / ΔRR_floating sign mismatch. Mid IV price contamination detected." };
  }
  return { valid: true };
};
```

Two independent failures:

**1a. No invariant is ever computed.** The function inspects a checkbox, not data. When the checkbox is unchecked it returns `valid: true` unconditionally — genuinely inconsistent emissions (real sign mismatches, failed leg identities, contaminated mids) render as clean numbers with zero validation. The guard's entire purpose was the unchecked path; that path contains no check.

**1b. The failure message fabricates a diagnosis.** "Mid IV price contamination detected" is a specific causal claim no measurement supports. T-B's sign mismatch is a symptom with multiple causes; a hardcoded prose diagnosis is worse than no message, because it asserts knowledge the system does not have. This violates the platform-wide provenance rule.

**Consequence:** every number displayed on the skew card to date has passed through no validation. The card is to be treated as unverified output until this brief's acceptance evidence is delivered.

---

## 2. Required implementation

### 2.1 Invariants computed in the engine, per emission
All invariant checks (T-A … T-I per the main brief §3.4) are computed in `vrp_pipeline.py` at emission time, from the emission's actual values. Every skew payload carries:

```json
"invariants": {
  "passed": false,
  "checked": ["T-A","T-B","T-C","T-D","T-E","T-F","T-G","T-H","T-I"],
  "failures": [
    {
      "id": "T-B",
      "measured": {"d_rr_fixed": 0.31, "d_rr_floating": -0.18},
      "rule": "sign(d_rr_fixed) == sign(d_rr_floating)"
    }
  ]
}
```

- **Failure entries contain measured values and the violated rule — never prose diagnoses.** The UI may render a short generic hint per invariant id, but the numbers are the message.
- Invariants that cannot be evaluated (missing input) report as `"skipped"` with the missing field named — checked-and-absent, never silently passed.
- The card-invariant rule applies to **every cockpit card**, not only skew: each card's emission schema gains the same `invariants` block, with that card's declared identities (e.g., move-attribution: Σ contributions vs index move within reconstruction residual; correlation card: ρ̄ vs ρ_eff co-movement).

### 2.2 UI wiring — render, never recompute, never mock
`IntradayPanel.tsx` deletes `checkInvariants()` and `simulateInconsistency` entirely. The card reads `payload.invariants.passed`; on failure it renders the existing badge presentation (which is approved as-is) with `failures[].id`, `measured`, and `rule`. Financial identities are not reimplemented in the frontend — single source of truth is the engine.

### 2.3 Simulation toggles out of production
"Simulate Mid Pollution" and "Simulate DTE < 2 Expiry Splice" are removed from the production control bar. Their scenarios already exist as specified fixtures (tests 21, 23a–23g, 26). If interactive demos are wanted, they live behind an explicit dev-mode build flag, visually watermarked `DEV FIXTURE`, and are absent from production builds. A risk cockpit whose control bar can inject fabricated error states is itself a data-integrity hazard.

---

## 3. Self-audit mandate (required deliverable)

This defect is a pattern, not an instance: a spec'd guard implemented as a hardcoded return or UI-driven mock. Audit the entire codebase for the same pattern and deliver a written list of every occurrence, including:

- guards/gates returning fixed values regardless of input (`return {valid: true}`, `return 0`, `pass`)
- checks keyed off UI state, feature flags, or demo toggles rather than data
- spec'd validations (parity gate T-I, wide-market rejection, staleness states, EVENT_VOLUME exclusions, dead-bands, seasonal flags) whose implementation does not consume the relevant measurement
- canned/fabricated message strings presented as diagnostic output

For each item: file/line, spec reference, real-or-mock verdict, and fix status. **An empty list is a claim and will be spot-checked; "found and fixed" is the expected shape of this deliverable.**

---

## 4. Acceptance evidence (merge conditions)

Screenshots are not evidence for any item below. Required:

1. **Test-run output** for tests 21–28 (main brief §3.4/§3.4a) executed against engine emissions, with the invariant block visible in the captured payloads.
2. **Negative-path proof:** a deliberately corrupted fixture emission (e.g., legs that do not sum to ΔRR_fixed) fed through the live pipeline → payload shows `passed: false` with correct measured values → card renders the badge. End-to-end, no toggles involved.
3. **Grep proof:** `simulateInconsistency` and both simulate-toggle strings absent from the production build; `checkInvariants` absent from all `.tsx`.
4. **Positive-path proof:** one real session's emissions with all invariants `passed: true` and the `checked` list complete — demonstrating the checks run on every emission, not only on demand.
5. **Self-audit list** per §3.

## 5. Sequencing
Single PR, blocking all further cockpit work: no new card ships while the invariant framework is a mock, because every card built meanwhile inherits the same non-guarantee. The §3 audit list is due with the PR, not after.
