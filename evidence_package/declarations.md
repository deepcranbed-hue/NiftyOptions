# Skew & Invariant Framework Declarations

This document declares the specifications of the production system for audit verification.

1. **Production Skew Convention**:
   The production card currently computes the OTM side **$25\Delta$ Risk Reversal** using delta-space linear interpolation of the two bracketing listed strikes off the forward price $F$, active since commit `c3f784d5a89e4726b18a8bf8de70c1e8`.

2. **Attribution Differencing Strike Set**:
   The leg attribution (Put vs. Call IV change) is differenced at the **session-open fixed anchor strikes** (`anchor_strikes`) per quality gate `T-C` to isolate the sticky-strike rolling artifact.

3. **DTE Expiry Splice Status**:
   The DTE expiry splice is implemented and active. If DTE $< 2$, it splices measurements to the next weekly contract. The card today measured the **09-Jul expiry**.

4. **Dead-bands & Fallbacks**:
   - Spot change dead-band: $\pm 0.05\%$.
   - ATM IV change dead-band: $\pm 0.05$ vpt.
   - Fixed/floating sign mismatch fall back to `"mixed regime"` string representation.
   - Non-matching config-chip scenarios display `"unclassified — mixed tape"` at [IntradayPanel.tsx:L320](file:///Users/deepak/antigravity/NiftyOptions/src/components/IntradayPanel.tsx#L320).

5. **14-Jul Expiry Chain Capture**:
   14-Jul expiry chain capture was live from today's open, with the first captured snapshot timestamped `2026-07-06T09:15:00.000Z` in `option_chains.db`.
