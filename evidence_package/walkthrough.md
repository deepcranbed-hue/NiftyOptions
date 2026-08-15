# Walkthrough: Minute-Data Analytics v1

We have completed the full implementation of the Minute-Data Analytics v1 module, establishing a robust, dependency-free mathematical foundation for high-frequency pricing, correlation, dispersion, and volatility risk premium signals.

---

## 1. Changes Implemented

### A. Alignment Layer (`alignment/`)
- **Canonical Grid Projection**: Developed [alignment_engine.py](file:///Users/deepak/antigravity/NiftyOptions/backend/quant/alignment/alignment_engine.py) to map pricing bars to a 376-minute trading grid (09:15-15:30 IST). Halts or missing prints correctly yield `null` volume bars and `NaN` close values instead of forward-filled prices.
- **Bad-Tick Filtering**: Implements a rejection filter for $|r| > 8 \sigma_{60m}$ spikes accompanied by immediate full reversals next minute. Logs prints for debugging.
- **As-Of Join Contract**: Snapshot joins use a backward as-of contract to completely prevent lookahead bias.
- **Reconstruction Identity**: Developed [reconstruction.py](file:///Users/deepak/antigravity/NiftyOptions/backend/quant/alignment/reconstruction.py) to reconstruct Nifty index returns using constituent free-float weights (Sept 2025 rebalance) and runs OLS regression. Alerts on drift if $R^2 < 0.97$.

### B. Correlation & Dispersion Engine
- **Covariance Shrinkage**: Implemented covariance shrinkage in [dispersion_engine.py](file:///Users/deepak/antigravity/NiftyOptions/backend/quant/dispersion_engine.py) using pure `numpy` and `pandas` to keep the codebase lightweight and dependency-free.
- **Dispersion & Correlation Indicators**: Calculates average pairwise correlation $\bar{\rho}(t)$, variance-ratio effective correlation $\rho_{eff}(t)$, and cross-sectional dispersion $D(t)$.
- **Volume & Anomaly Detectors**: Integrates Amihud illiquidity breadth, EOD surges, opening attention leaders, and three flow-anomaly footprint detectors (Move Concentration, Pump-and-Reverse, and Cash-Options divergence).

### C. VRP Pipeline
- **Volatility Risk Premium**: Implemented [vrp_pipeline.py](file:///Users/deepak/antigravity/NiftyOptions/backend/quant/vrp_pipeline.py) to extract ATM IV, compute VRP, and expose VIX realized spreads with floating-vs-fixed RR skew decomposition.

### D. User Interface & Advanced Features (Cockpit Updates)
- **Index Move Attribution Strip**: Placed a standing attribution strip displaying point moves, top 5 stock point contributions, cumulative share, advancers/decliners, and broad/narrow chips.
- **Card-Invariant Quality Gates**: Implemented quality checks (T-A to T-I) to catch polluted option pricing records. If simulated check checks fail, the UI hides metrics and displays a `DATA_INCONSISTENT` warning banner.
- **Three-State OI Flows**: Categorizes skew flows into `new buying`, `writer buy-back`, and `repricing` alongside material quote-width spread gates.
- **DTE Expiry-Day Splice**: Swaps measuring chains automatically when DTE $< 2$ to prevent decay-day model instability.

---

## 2. Verification Results

- **Automated Tests**: Created and ran [test_minute_analytics.py](file:///Users/deepak/antigravity/NiftyOptions/scratch_scripts/test_minute_analytics.py) asserting:
  - Grid projection yields 376 bars and projects NaNs.
  - Spike + reversal ticks are rejected.
  - Backward as-of joins hide future snapshots.
  - OLS reconstruction regression computes correct R² values.
  - Ledoit-Wolf correlation and dispersion are stable.
  - Pump-and-reverse score triggers correctly.
  - **All test cases passed successfully**.
