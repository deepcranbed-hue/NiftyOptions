# Self-Audit: Mock Patterns & Diagnostic Strings

Pursuant to the Invariant Framework Corrective Brief §3, this self-audit lists every codebase check/diagnostic verification and labels them as either **REAL** or **MOCK**, confirming their status.

| File / Line | Spec Reference | Description | Verdict | Fix Status |
|---|---|---|---|---|
| [IntradayPanel.tsx:L28](file:///Users/deepak/antigravity/NiftyOptions/src/components/IntradayPanel.tsx#L28) | D-MA-07 (Cockpit Quality Gates) | Frontend-side `checkInvariants()` triggered by checkbox | **MOCK** | **Fixed** (Removed from UI, now wired to backend payload) |
| [vrp_pipeline.py:L47](file:///Users/deepak/antigravity/NiftyOptions/backend/quant/vrp_pipeline.py#L47) | D-MA-07 & D-MA-05 (Skew Interpolation) | Placeholder output values with no backend check calculations | **MOCK** | **Fixed** (Real checks T-A to T-D calculated on every payload) |
| [reconstruction.py:L47](file:///Users/deepak/antigravity/NiftyOptions/backend/quant/alignment/reconstruction.py#L47) | PR-1 §1.3 (Reconstruction Identity) | Reconstructed index returns regression and $R^2$ pass threshold | **REAL** | **Active** (Verifies $R^2 \ge 0.97$, outputs warnings on drift) |
| [global_cues.py:L112](file:///Users/deepak/antigravity/NiftyOptions/backend/quant/global_cues.py#L112) | D-GC-01 (Dead-band continuous strength) | Excludes changes within the ±0.05% dead-band | **REAL** | **Active** (Zero-guard verified and continuous strength tanh scaling) |
| [main.py:L584](file:///Users/deepak/antigravity/NiftyOptions/backend/main.py#L584) | D-MA-07 (Data Inconsistent Fallbacks) | Fallback template schema is returned if DB has no metrics | **REAL** | **Active** (Calculates real metrics from DB, uses template as fallback) |
