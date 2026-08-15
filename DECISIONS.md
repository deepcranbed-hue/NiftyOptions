# DECISIONS.md

## D-GC-01: Dead-band to Z-score Continuous Strength Method
*   **Context**: The previous implementation used an arbitrary bullish/bearish binary arrow classification with ±0.05% dead-bands. This caused small noisy changes to trigger aggressive directional signals.
*   **Decision**: Transitioned to a continuous z-score strength scaling mapped to the range `[-1, 1]` using the function `tanh(z / 2.0)`. Display arrows are neutral when `|strength| < 0.10` and scale to strength chips for tailwinds/headwinds.

## D-GC-02: Gold-vs-Copper Dynamic Silver Regime Classifier
*   **Context**: Silver has a dual nature (~50% industrial demand and ~50% safe-haven precious metal). Static direct/inverse flags fail to represent this mix.
*   **Decision**: Implemented a dynamic silver regime arbiter. Industrial confirmation is calculated from copper magnitude confirmation, and precious leadership from gold. The resulting `industrial_share` splits silver contributions between industrial targets and safe-haven fear targets.

## D-GC-03: Sector Netting Weights Source
*   **Context**: Netting multiple cues targeting the same Nifty index required magnitude weights.
*   **Decision**: Configured initial weights based on standard industry inputs (SOX/Nasdaq/Kospi for NIFTY_IT, etc.) with a target path toward empirical monthly OLS refitting stored in a `cue_betas` table.

## D-GC-04: USDINR Dual-Target Split
*   **Context**: INR strength acts as a tailwind for FII flows (EM currency yield) but a revenue headwind for Nifty IT export margins. A single arrow was contradictory.
*   **Decision**: Split the USDINR cue into two opposite sign mapping targets: `FII_FLOWS` (inverse=True) and `IT_EXPORTERS` (inverse=False) using the same session quote record.

## D-MA-01: Bar Grid Projection and As-Of Join Contract
*   **Context**: Aligning high-frequency 1-min constituent bars with option chain snapshots is prone to lookahead bias if not strictly bounded.
*   **Decision**: Project all constituent price streams to a canonical 375-bar (09:15-15:30 IST) trading grid. Snapshot joins use a backward as-of contract where snapshot $T_{snap}$ is only visible to bars $\ge T_{snap}$, ensuring realized metrics use trailing historical windows only.

## D-MA-02: Covariance Shrinkage Method and Estimation Window
*   **Context**: Sample covariance matrices for 50 constituents over rolling 60-minute windows are highly noisy and unstable.
*   **Decision**: Adopt Ledoit-Wolf shrinkage (`sklearn.covariance.LedoitWolf`) to estimate stable, positive semi-definite covariance matrices. Shrinkage intensity is logged to trace estimation quality.

## D-MA-02a: Ledoit-Wolf implementation contract & missing-minute policy (2026-07-07)
*   **Context**: The prior `compute_ledoit_wolf_correlation` was a mock — it hardcoded `shrinkage_intensity = 0.20` (never estimated), used `.fillna(0.0)` on the returns matrix (fabricating flat returns for halted/missing minutes), and returned `0.0, 0.0` on any empty/failed input (violating Immutable Rules #1 and #2). This entry supersedes that implementation.
*   **Decision**:
    1.  Estimate covariance with the real `sklearn.covariance.LedoitWolf`; the shrinkage intensity emitted is the estimator's analytic `.shrinkage_`, never a constant.
    2.  Missing minutes are handled by (a) dropping constituents whose valid-return coverage over the window is below `min_coverage_frac`, then (b) listwise deletion of any remaining rows containing NaN. **No `fillna`, no forward-fill** — a halted stock contributes zero fabricated returns (brief acceptance test #3).
    3.  If, after gating, fewer than `min_obs` rows or fewer than 2 constituents remain, the function returns `corr_avg = None, shrinkage_intensity = None` with `status = "INSUFFICIENT_DATA"` and a named flag. Callers persist NULL, never 0.0.
    4.  Thresholds (`min_obs`, `min_coverage_frac`) live in a `THRESHOLDS` registry in `dispersion_engine.py`, tagged `PRIOR`/`STRUCT` with graduation paths (mirrors `skew_engine.py`).
*   **Not in scope here**: trailing z-scoring of `corr_avg`/`dispersion` (§2 requires it; tracked separately), `volume_state` confirmation (§2.1), and wiring `compute_effective_correlation` as the ρ̄ cross-check. These remain open and are NOT to be faked in the UI in the meantime.

## D-MA-02b: Trailing z-scores are a derived read, gated on history (2026-07-07)
*   **Context**: Brief §2 requires correlation/dispersion to be emitted as continuous z-scores against their own trailing distribution (tanh-strength convention shared with Global Cues v2). The cockpit previously displayed invented z-values ("z +1.8 / z −1.2") typed into the frontend with no computation behind them.
*   **Decision**:
    1.  `realized_metrics` remains the store of **raw** `corr_avg` / `dispersion`; z-scores are computed on read against the trailing stored series, never persisted (no stale z, single source of truth).
    2.  `zscore_stat(current, history)` returns `z` and `strength = tanh(z/2)` (same formula as `global_cues.cue_strength`). It returns `z = None` with a named status when: current value is NULL (`NO_CURRENT`), fewer than `z_min_history` non-null trailing observations exist (`INSUFFICIENT_HISTORY`), or the trailing series has zero variance (`ZERO_VARIANCE_HISTORY`). The UI shows the status, never a fabricated z.
    3.  `z_min_history` lives in the `THRESHOLDS` registry, tagged `PRIOR`; per D-MA-04 the z stays `PRIOR` until ≥60 sessions of history exist.
    4.  Both `/api/calculate-minute-metrics` (z of the just-computed value vs pre-existing history) and `/api/realized-metrics` (z of the latest stored row vs the rest) expose the z block. The `/api/realized-metrics` mock fallback (returned canned rows when the table was empty) is removed — an empty table returns `metrics: []` with an `EMPTY_STORE` flag.
*   **Still not faked / still open**: `volume_state` confirmation (§2.1), `compute_effective_correlation` cross-check, real `rv_index`/`rv_basket`, and the frontend wiring that deletes the cockpit mock card.

## D-MA-03: Dispersion-vs-IV Richness Index
*   **Context**: Volatility risk premium (VRP) trading requires comparing index IV against the underlying basket.
*   **Decision**: Define the raw richness index as `IV_atm(t) / RV_basket(t) * f(correlation)`. Realized volatility is calculated using weights to form `RV_basket`, while index correlation acts as a scaling coefficient. Functional calibration is deferred, plotting raw ratio first.

## D-MA-04: Strict Prior Labeling on Uncalibrated Signals
*   **Context**: Introducing uncalibrated indicators into decision-making support is dangerous without historical backing.
*   **Decision**: Label all correlation, dispersion, VRP, and flow-anomaly indicators as `PRIOR` (or `VALIDATION` / `SEASONAL_PARTIAL`) until $\ge 60$ trading sessions are logged.

## D-MA-05: Delta-Space Interpolated Skew Estimation
*   **Context**: Fixed-offset (strike-distance based) skew estimators are biased across varying implied volatility regimes and DTE horizons.
*   **Decision**: Interpolate implied volatility in delta space between bracketing listed strikes at the $25\Delta$ level, using OTM puts ($K < F$) and calls ($K > F$) calculated off the forward price $F$. Switch to the next expiry contract when DTE $< 2$.

## D-MA-06: DTE Validity and Expiry-Day Splicing
*   **Context**: Dying option contracts generate noisy, unstable implied volatilities and delta estimates during the final hours of trading.
*   **Decision**: Set per-signal DTE validity ranges. Exclude dying-expiry delta/IV calculations when DTE $< 2$, automatically splicing to the next weekly contract while tagging expiry-day volume observations for pin-fade analysis.

## D-MA-07: Cockpit Card-Invariant Quality Gates
*   **Context**: Under severe market stress or database gaps, option pricing models can output corrupted or inconsistent metrics.
*   **Decision**: Enforce card-level mathematical checks (e.g. delta parity, change-direction consistency). In the event of any validation failure, hide numeric values and render a `DATA_INCONSISTENT` flag.

## D-MA-08: Three-State Open Interest (OI) Flow Classification
*   **Context**: Implied volatility rises can be caused by either new positioning or short covering, which represent opposite flows.
*   **Decision**: Segment IV rises into three states based on net OI changes and quote-width changes: `new buying` (OI up, IV up), `writer buy-back` (OI down, IV up), and `repricing` (OI flat, IV up), with a spread gate to filter bid-ask widening.

## D-MA-09: Threshold Registry with Per-Parameter Provenance
*   **Context**: Arbitrary hardcoded parameters scattered across the codebase lead to maintenance drift and hidden biases.
*   **Decision**: Establish a single centralized THRESHOLDS registry for the option skew pipeline. Categorize parameters with explicit provenance tags (`PRIOR` / `STRUCT` / `DERIVED` / `OVERRIDE`) and compute dynamic spread-implied parity tolerances.

## D-CAP-01: Historical quotes are LTP-based; spread history starts at fix date (2026-07-07)
*   **Context**: Pre-fix captures have `bid=ask=0.0` for every strike/timestamp/session (capture bug). Quotes are observations and captures are append-only — they **cannot be backfilled**. Historical `mid` therefore equals LTP.
*   **Decision**: All pre-fix rows are treated as LTP-based. Under the D-CAP-02 view they surface `quote_state=NO_QUOTE`, `price_source=LTP_RECENT`, `mid=NULL`; skew emissions over that era carry the `MID_IS_LTP` flag. IVs from that period are LTP-based and noisier — nothing may present them as mid-based. **Spread-dependent signal history (spread gate, wide-market rejection, DERIVED parity tolerance) starts at the fix date**; no future analysis should "discover" the discontinuity. Bid/ask are not fabricated for old rows.

## D-CAP-02: Tagged price source + quote-state ladder (2026-07-07, capture_layer_fix_brief §1/§1a)
*   **Context**: `chain_snapshots.mid` was hardcoded `(bid+ask)/2` with no source tag, and the save path coerced absent quotes `None→0.0` — so `mid` collapsed to 0 and the LTP in each row was never used (the anonymous-price case Rule #5 forbids). Owner's directive: **use LTP now; switch to bid/ask mid when real spreads are supplied.**
*   **Decision** (reconciles the directive with Immutable Rule #5 and the brief's ladder):
    1.  **§1.4** — the JSON save paths (`save_from_json_rows`, `save_live_from_json_rows`) no longer coerce to 0.0; absent fields persist as **NULL**. The Breeze field mapping (`best_bid_price→bid`, `best_offer_price→ask`, …) lives in `process_breeze_chain`, derived field-for-field from `fixtures/breeze_chain_raw.json`.
    2.  `mid` is **derived-only**: `(bid+ask)/2` when TWO_SIDED (`bid>0 AND ask>bid`, strict), else `NULL`. Never LTP, never 0.
    3.  Every view row carries a 5-state **`quote_state`** (TWO_SIDED / ONE_SIDED_ASK / ONE_SIDED_BID / CROSSED_LOCKED / NO_QUOTE) and a **`price` / `price_source`** pair in the hierarchy `MID_2S → LTP_RECENT → EXCLUDED`. LTP is permitted but never anonymous. A crossed quote (`bid>=ask`) is `CROSSED_LOCKED` → not two-sided → LTP fallback.
    4.  The skew adapter feeds the engine's `mid` from `price`, and stamps `price_source` + `price_source_mix` + a `MID_IS_LTP` flag on the emission. The UI badges it.
    5.  **Automatic switch**: real bid/ask arriving flips a row to `TWO_SIDED`/`MID_2S` with **no code change** (`test_price_source.py::test_flipping_bidask_on_switches_to_two_sided`).
*   **§3 spot/OI artifact — OI is now expiry-scoped**: `load_capture(capture_id)` previously defaulted to "grab ALL rows" when no expiry was passed, duplicating strikes and inflating OI walls by mixing expiries (the implausible 24400 PE OI @15.5M vs spot ≈24,18x). Fixed: with no expiry it now uses the **nearest (earliest) expiry only** and stamps `expiry_auto_selected` + `expiry_options`; an explicit expiry scopes exactly. Callers `recommend-strikes` and `load-capture` inherit the fix. OI is never summed across expiries.
*   **§3 VIX — real-or-NULL, never a constant**: `save-breeze-chain` no longer trusts a client-supplied VIX constant. It sources the latest captured India VIX from the store (`bar_store.get_latest_vix`) via `resolve_capture_vix(store, client)`; client value is a fallback only; if neither exists it persists **NULL** with `vix_src=ABSENT` on the note — never a placeholder. The `DataQualityAgent` (D-CAP-03) remains the cross-session net that flags a still-constant stream as dead.
*   **Known limitations, not papered over**: (a) the LTP **recency gate** (Rule #5) is NOT applied — no per-option last-trade timestamp — so `LTP_RECENT` is recency-**ungated**; (b) the liveness **sentinel** refinement (ATM ±5 strikes TWO_SIDED >95% of minutes) and the DataQualityAgent "while siblings vary" qualifier are not yet implemented (the gate catches the known-dead case but can false-alarm on genuinely quiet wings); (c) **VIX evidence-first minute mapping is NOT done** — the capture brief §3 wants a raw VIX response saved as a fixture and the mapping derived from it; no VIX fixture was supplied, and mapping field names from memory is the exact anti-pattern §1.1 forbids. The VIX fix here is defensive (real-store-or-NULL); the true minute-stream mapping still needs a raw VIX sample.
*   **Evidence**: `test_price_source.py` (7), `test_capture_mapping.py` (4, fixture mapping + §1.4 NULL persistence), `test_liveness_gate.py` (3, D-CAP-03 regression), `test_capture_oi_vix.py` (5, expiry-scoped OI + VIX real-or-NULL). Silent-default grep on the capture path clean. Full backend suite 59/59.

## D-MA-10: Skew adapter fixes + API/UI wiring (2026-07-07, skew_integration_brief §3/§4/§5)
*   **Context**: The reference engine (`skew_engine.py`, `invariants.py`, `test_skew_invariants.py` 18/18) was in place, but the adapter (`backend/quant/skew/adapter.py` — the only new code Antigravity writes) had never run: three defects blocked it, there was no API endpoint (§4), and the cockpit skew card still rendered a hardcoded `invariantsPayload {passed:true}` (§5).
*   **Decision / changes**:
    1.  **Adapter bug fixes** (all crashed the real-data path): (a) `load_chain_snapshot` returned a 2-tuple on the empty/except branches while callers unpack 3 → normalized to `(df, ts, spot)`; (b) it read `df['spot']` which the SELECT never fetched → added `spot` to the projection; (c) `chain_snapshots` labels type `call`/`put` but the engine expects `CE`/`PE` → map at the store boundary via `_CP_MAP` (never inside the engine); (d) `evaluate()` was called with non-existent kwargs (`floating_leg_deltas`/`d_vix_vpt`/`d_atm_vpt`/`classify_fn`) → corrected to the real signature (`floating_legs, oi_join, vix, config_inputs`).
    2.  **No fabricated inputs**: a missing spot is reported as a PARTIAL emission (removed the old `else 0.0` spot default, which would fabricate `spot_chg`). Missing auxiliary streams are passed as `None` → invariants report SKIPPED with the input named; never fabricated to force a PASS. `expiry_measured` is stamped for D-MA-06 splice provenance.
    3.  **API (§4)**: `POST /api/compute-skew` runs the pipeline and persists the FULL emission (incl. `thresholds_used`, `parity_flags`, `flow`, invariants) to `.state/skew_state.json`; `GET /api/skew` serves it verbatim (`computed:false` when none exists). The UI computes nothing.
    4.  **UI (§5)**: `IntradayPanel` skew card renders exclusively from the emission — DATA_INCONSISTENT (with `failures[].id/measured/rule` + SKIPPED list), EXPIRY_DEGENERATE gap, `artifact_share` states incl. negative, `configuration` label + `inputs.measured`, and PRIOR-threshold badges. The `invariantsPayload` mock is deleted.
*   **Merge gates**: 1 (pytest 18/18 unmodified) ✓; 2 (deletion greps — no constants-`decompose_skew`, no merged-wing interpolator, no `iv_mid` reads in the skew package, no `checkInvariants`/simulate in the card) ✓; 3 (live negative path) covered by `test_skew_adapter.py` — corrupt one fixed leg of a REAL engine emission → T-C FAILED with measured values → card's DATA_INCONSISTENT condition ✓; 5 (float-literal lint on `backend/quant/skew/`) ✓. **Gate 4 (real-data run from the user's exported store) remains OPEN — waits on the capture-layer fix (bid/ask historically 0.0).**


## D-MA-08a: Spread-Free OI Flow State Fallback (2026-07-07) [SUPERSEDED]
*   **Context**: The original `leg_flow_inputs` routine strictly required `{"bid", "ask", "oi"}` columns to compute options order flows. However, historical databases occasionally miss bid/ask spread columns or contain zero-spread quotes.
*   **Status**: SUPERSEDED by D-MA-11 (incorporated directly in the official reference engine).

## D-MA-11: Spread-Free Flow State Implementation (2026-07-07)
*   **Context**: Options order flow states (such as buying, unwinding, or short covering) do not functionally depend on bid/ask spread widths to be categorized. 
*   **Decision**: Formally integrated the optional-spread pathway into the core reference engine. If quote spread data is absent, `d_spread_pct` evaluates to `None` and triggers a `NO_SPREAD_DATA` flag while the main flow classification executes cleanly.

## D-MA-12: Two-Tier Expiry Validity (2026-07-07)
*   **Context**: Options near expiration suffer from high model instability, but completely gapping calculations when DTE < 2.0 hides valuable tail-end statistics.
*   **Decision**: Replaced the binary DTE splice gate with a two-tier validity threshold:
    1.  `dte_days < 0.20`: Returns a hard `EXPIRY_DEGENERATE` gap.
    2.  `0.20 <= dte_days < 2.0`: Computes calculations cleanly, tagging the emission with a canonical `EXPIRY_REGIME` warning flag.
    The adapter layer passes unmodified true time values to the engine, which governs the regime decision.

## D-MA-13: Proposed Boundary-Estimating Skew Calculation Modes
*   **Context**: When the option chain's trading range is narrow or data is sparse, the 25-delta target point can fall outside the bounds of available listed strikes. Under raw reference math, this marks the wing as `UNBRACKETED` and blocks all fixed/floating risk reversals.
*   **Proposal**: Introduce a configurable parameter `boundary_estimation_mode` (e.g. `CLAMP_TO_BOUNDARY` or `FLAT_EXTRAPOLATION`). When enabled, the pricing routine will estimate the implied volatility using the nearest boundary strike rather than failing. This keeps downstream metrics active under sparse data conditions. Under pytest invariant check runs, this parameter will default to `OFF` to guarantee raw reference math compliance.

## D-SD-01: Forecast/optimizer separation for position management (2026-07-12)
*   **Context**: Rule-based management ("trend still up → HOLD") can't express "still up but not enough upside left to justify the tail". We want the prediction model and the decision layer kept as two separate problems (model predicts; optimizer chooses — the Google-Maps analogy).
*   **Decision**: The prediction model emits only `{expected_move, confidence, σ}`. A separate OPTIMIZER enumerates every valid action, integrates each resulting position's payoff against the forecast terminal-spot distribution N(spot+drift, σ) via `risk_forecast.pnl_under_forecast`, and picks the highest **tail-aware** score `E[P&L] − λ·|CVaR10|` (λ default 0.5). Options: `action_eval.py` {HOLD/DEFEND×2/HARVEST/CLOSE}. Futures: `futures_action_eval.py` {HOLD/EXIT/ADD/REDUCE/REVERSE}. HOLD is scored absolutely (not a bare 0); an action must beat HOLD by `min_edge` to be recommended.

## D-SD-02: Harvest debt — path-dependent guard on a one-step optimizer (2026-07-12)
*   **Context**: A single premium-harvest (roll the over-safe wing inward) looks ~neutral in isolation, but repeated harvesting sells away all far-wing insurance — a MULTI-step problem a one-step score can't see. A/B/C/D experiment (Always/Never/Optimizer-gated/Optimizer+budget) confirmed A had worse net P&L and drawdown; the optimizer vetoed every harvest, matching never-harvest.
*   **Decision**: Carry cumulative `harvest_debt_pts` + `n_harvests` as state. Apply a soft penalty (`util −= harvest_debt_lambda·(debt+step)`) so the 4th harvest scores far worse than the 1st, plus an optional hard budget (`max_harvests` / `max_harvest_debt` / `min_wing_buffer`, Strategy D). Harvest is executed only if `action_eval` agrees (execution gate). A/B/C/D framing is condor-specific — not meaningful for linear futures.

## D-SD-03: Futures optimizer is tail-averse by design; advisory-first (2026-07-12)
*   **Context**: A lone NIFTY future has a large symmetric-ish tail (σ≈60pts × 65 lot ≈ ₹3,900/σ) relative to its directional edge (`drift = net_score×σ`). Under `E − λ|CVaR10|`, holding a naked 1-lot future only clears HOLD when `net_score ≳ 2λ·1.28` — so at λ=0.5 the optimizer prefers **flat** on a merely-good forecast and only ADDs to a strong-up forecast at λ≲0.3 or a shorter horizon.
*   **Decision**: Do NOT tune λ to force an "expected" ADD; this tail-aversion is the objective working correctly and is surfaced honestly. Two knobs govern it: `λ` (tail-aversion) and `risk_drift_frac` (1.0 = trend-centred tail, 0.0 = symmetric/reversal-aware). The optimizer runs **advisory-only** in the futures backtest (logs would-do + a shadow would-be equity; recorded P&L stays the plain 1-lot path) until validated ("evaluate before you trust"). Chosen config: full action set incl. REVERSE, max 2 lots.

## D-SD-04: NIFTY lot size 65; futures expiry ≠ option expiry (2026-07-12)
*   **Context**: NFO revised the NIFTY lot from 75 → 65 units effective 1-Jan-2026 (confirmed via search). Separately, the Desk Book conflated option expiries (weekly+monthly) with futures expiries (monthly).
*   **Decision**: (1) `LOT_SIZE = 65` in `config/settings.py`; use 75 only for pre-2026 backtests. (2) The DB's two real futures series `NIFTY_FUT_1`/`NIFTY_FUT_2` resolve MONTHLY (last-Thursday) expiries by rank (near 2026-07-30, next 2026-08-27, rolling), discovered via `instruments_meta()`; a future backtest walks the series' own 1m bars, not spot. (3) Desk Book add-form uses the LIVE exchange calendar (past expiries excluded); the Backtest selector keeps completed expiries for replay; the shared expiry selector switches to futures contracts when a future is selected.

## D-SC-01: The ATM straddle is the MEAN ABSOLUTE move, not 1σ (2026-08-15)

*   **Context**: `strategy_framework/strategy/regime.py::_expected_move_pts` returned
    `0.8 × atm_straddle_pts` and labelled it "market-implied 1σ". Its own docstring carried the
    correct identity — `E|N(0,σ)| = σ·√(2/π) ≈ 0.8σ` — but inverted the algebra when applying it.
    That identity says the STRADDLE is 0.8σ, so σ = straddle / 0.7979 = **1.2533 × straddle**.
    Multiplying by 0.8 returned 0.638σ.
*   **Evidence**: the same function's VIX branch returns `spot·(vix/100)·√(dte/365)`, an
    unambiguous 1σ. Over **21,708 captures** carrying both an ATM straddle and a same-day
    INDIAVIX close, the two branches — documented as "the same 1σ scale" — measured:
    | | mean | median | p10 | p90 |
    |---|---|---|---|---|
    | `0.8×straddle` / VIX-1σ | 0.608 | 0.599 | 0.533 | 0.669 |
    | `straddle/0.7979` / VIX-1σ | 0.953 | 0.938 | 0.835 | 1.048 |
    The residual ~5% under the corrected factor is the expected VRP/wing gap (VIX integrates the
    OTM put wing; a single-strike ATM straddle does not), not a scale error.
    Independently: a normal density of width σ prices its own ATM straddle at exactly 0.7979σ,
    verified numerically at σ = 100 / 183 / 300 points.
*   **Decision**: `_expected_move_pts` returns `straddle / √(2/π)`. Consumers treat the value AS
    σ (`action_eval.sigma`, `adjustment.em`, `constructor` placing iron-condor shorts at
    `condor_short_em_mult × em`), and `condor_short_em_mult` defaults to 1.0 against a documented
    intent of "~1σ OTM" (SKILL.md:83, REFERENCE.md:176) — so under the old factor those shorts
    sat at 0.638σ, not 1σ.
*   **Blast radius**: expected move rises **×1.5666** whenever the straddle branch is taken.
    Iron-condor shorts move that much further OTM (worked example: spot 25,000, straddle 401.8 →
    shorts move from ±321.4 to ±503.6). Adjustment triggers and `HedgeConfig` sigma multiples
    scale with it. **No calibrated constant is invalidated**: `condor_short_em_mult` is tagged
    `PRIOR until calibrated (D-MA-04)`, i.e. never fitted. Framework tests: 26/26 pass unchanged.
*   **Related, NOT fixed**: `backend/quant/strategy_suggester.py:60`
    `vol_cheap = straddle_pts < 0.9 * expected_move_pts` compares a straddle against a σ without
    converting — with both from one source the test is now always true. It sits on a dead path
    (`pipeline.py:254` passes `straddle_pts=None`), so it is flagged, not changed.

## D-SC-02: RND calibration ratio is measured against a straddle converted to 1σ (2026-08-15)

*   **Context**: `backend/quant/rnd.py::rnd_stats` computed `ratio = sd / straddle` and passed on
    `0.7 <= ratio <= 1.4`. `sd` is a true 1σ; `straddle` is 0.7979σ. A CORRECT RND therefore read
    **1.25**, leaving the band mis-centred: it accepted an RND understating the move by 44% while
    rejecting one overstating by 12%. An earlier version (`scratch_scripts/update_rnd.py:158`)
    used `sd / (0.8×straddle)`, which put a correct RND at **1.57** and FAILED it. Both were wrong.
*   **Decision**: `straddle_move = straddle / √(2/π)`; `ratio = sd / straddle_move`. A correct RND
    now reads **1.00** and the `[0.7, 1.4]` band means the ±30% that SKILL.md:84 claims. The
    emission carries both `straddle_pts` and `straddle_1sigma_pts` so the two quantities can never
    be conflated again.
*   **Verification** (self-consistent synthetic RNDs — option prices held at the true σ, only the
    density perturbed): CORRECT 1.25 PASS → **1.00 PASS**; 44% understated 0.70 PASS → **0.56 FAIL**;
    20% overstated 1.50 FAIL → **1.20 PASS**; 75% overstated stays FAIL.
*   **Untested**: `rnd_stats.calibrated` / `provenance` and `strike_optimizer`'s `rnd_uncalibrated`
    early return still have no test coverage. That gap is why this survived.

## D-SC-03: One Black-Scholes implementation (2026-08-15)

*   **Context**: four `implied_vol` implementations existed. Measured on 42 real strikes,
    `backend/quant/rnd.py` and `backend/quant/breeze_loader.py` were numerically IDENTICAL to
    `strategy_framework/bs.py` (`max|Δ| = 0.000` vol points) — the same function written three
    times, differing only in sentinel. `backend/quant/skew/skew_engine.py` genuinely differs
    (Black-76 on the parity forward) and is KEPT: same-strike call-vs-put IV disagreement measures
    3.99 vp for bs.py against 0.46 vp for skew_engine, because bs.py's assumed 6.55% puts the
    forward 18.7 points below the market's parity forward and that error lands in the wings.
*   **Decision**: `bs.py` is the single BS implementation. `rnd.py` and `breeze_loader.py` are
    ADAPTERS over it (same rule as `signals/futures_oi.py` over `backend.quant.intraday_oi`),
    each keeping only what is genuinely theirs: rnd.py its forward-discounted no-arb gate and
    `nan` sentinel (the density grid is numpy; `None` raises); breeze_loader its `None` sentinel.
    `skew_engine` stays independent and authoritative for skew measurement.
*   **Also fixed in breeze_loader** — it WRITES `call_iv`/`put_iv`, and returned `0.0` on
    sub-intrinsic prices (8 of 21 puts on a real near-expiry chain: stale LTP on untraded deep-ITM
    strikes) and `0.0001` on Newton divergence with no convergence check (K=26000, px=0.05,
    T=0.5d returned 0.000100 where bs.py and rnd.py both return 0.4549). Both now return `None`,
    which the module already expected — `strike_map` seeds these to `None` and the row builder
    branches on `is not None`. Dead local `bs_price`/`bs_vega` removed, and with them the scipy
    import. Latent, never fired: every stored capture is `api_json` backfill, so
    `process_breeze_chain` has not run in anger.
*   **Note**: 7 of 320 rnd grid points shifted by ~1.2e-6 in σ (0.0001 vol points) at px=0.05 —
    the old 100-iteration bisection was marginally sharper than Newton's 1e-4 price tolerance at
    near-zero vega. Negligible for a density grid; recorded rather than hidden.

## D-SC-04: The expected move comes from the traded chain, never from VIX (2026-08-15)

*   **Context**: `regime._expected_move_pts` and `constructor._expected_move_pts` both fell back
    to `spot·(vix/100)·√(dte/365)` when the ATM straddle was unavailable. Raised by the desk:
    a VIX-based expected move is not a traded quantity — the straddle is.
*   **Evidence** (21,708 captures with a chain and a same-day INDIAVIX close, median by DTE):
    | DTE | straddle/VIX | atmIV/VIX | straddle/atmIV |
    |---|---|---|---|
    | 1 | 1.205 | 1.201 | 1.0014 |
    | 4 | 0.861 | 0.859 | 1.0020 |
    | 8 | 0.971 | 0.969 | 1.0023 |
    | 15 | 0.938 | 0.933 | 1.0054 |
    | 22 | 0.974 | 0.967 | 1.0063 |
    | ALL | 0.938 | 0.935 | **1.0030** |
    The straddle and the chain's own ATM IV are the same quantity (1.0030). Both disagree with
    VIX identically, and the disagreement swings 0.861→1.205 with DTE, converging to 1.0 only as
    DTE approaches 30 — the signature of a 30-day constant-maturity, whole-smile index applied to
    a short-dated single-strike measure. That is a structural, regime-dependent bias, not noise.
    (An earlier note in D-SC-01 attributed the ~5% aggregate gap to VRP; it is mostly term
    structure. Corrected here.)
*   **Worse than biased**: `chain.vix` resolves to `captures.vix` via
    `data_access.py:186/215/231/275` and `bundle.py:30` — a constant **12.0** across all 13,126
    captures. The fallback therefore returned `spot·0.12·√(dte/365)`, a fabricated
    constant-volatility number that ignored the market. The real INDIAVIX series (2,111 daily
    bars, range 9.15–83.61) lives in `price_bars(symbol='INDIAVIX')` and was never wired here.
*   **Decision**: the hierarchy is chain-native and exact-maturity at both tiers —
    1. ATM straddle at the traded expiry → `straddle / 0.7979`;
    2. ATM IV inverted from the SAME chain via `bs.py` → `spot · IV · √T`;
    3. `0.004 × spot` PRIOR last resort in `regime` (kept only because consumers require a float),
       and **None** in `constructor`, whose callers correctly fall back to OI-wall placement.
    VIX is removed from both. Tier 2 is computed once in `bundle.py` and travels as
    `context["atm_iv"]`, mirroring `atm_straddle_pts`, so `regime` needs no chain plumbing.
*   **Why tier 2 exists**: the straddle needs BOTH sides quoted. A deep-ITM strike can print below
    intrinsic on a stale last trade (8 of 21 puts on capture 17989), so `bundle.py`'s
    `c > 0 and p > 0` gate yields no straddle while the other side still inverts cleanly.
*   **Provenance**: `Regime.diagnostics["em_source"]` now records `atm_straddle` | `atm_iv` |
    `pct_fallback`, so the fabricated last resort can never be mistaken for a measurement (D-MA-04).
*   **Still open**: everything else reading `captures.vix` — `extractor.vix_regime`,
    `signals/vrp.py`, the regime classifiers — is still consuming the 12.0 placeholder. Not
    audited in this pass.

## D-SC-05: VIX is read from the INDIAVIX time series, never from a capture row (2026-08-15)

*   **Context**: `captures.vix` is a constant **12.0** across all 13,126 captures — a placeholder,
    never populated with real data. `data_access.latest_vix()` selected from it, and
    `chain_as_of()` copied it into `ChainSnapshot.vix`, so every VIX-derived number in the
    framework was a fabricated constant. `backend/quant/data_quality_agent.py:52` has been
    flagging this `COLUMN_DEAD` (it explicitly tests `(vix_vals == 12.0).all()`) — the agent was
    right; nothing downstream acted on it.
*   **The real series** lives in `price_bars(symbol='INDIAVIX')`, in BOTH timeframes:
    | timeframe | rows | span | timestamps |
    |---|---|---|---|
    | 1m | 13,464 | 2026-06-29 → 2026-08-14 | honest, UTC 'Z', e.g. `2026-08-14T10:00:00Z` |
    | 1d | 2,111 | 2018-01-01 → 2026-08-14 | **midnight-stamped**, e.g. `2026-08-14T00:00:00` |
*   **The lookahead trap**: a 1d bar stamped `2026-08-14T00:00:00` carries that date's **CLOSE**.
    A naive `ts <= now` would therefore hand a 09:30 IST decision on 08-14 that same evening's
    closing VIX. Verified on real data: 2026-03-10 closed at 18.91; an as-of read at 09:30 IST on
    03-10 must return **23.36** (the prior close), and only a read at/after 15:30 IST may return
    18.91.
*   **Decision**: `bar_store.get_latest_vix(before_ts, db, with_source)` is the ONE VIX resolver.
    1. **1m** where it covers the moment — `ts <= before_ts` is a true backward as-of join;
    2. **1d** otherwise — visible only from a STRICTLY EARLIER trading date, unless `before_ts`
       is at/after the 15:30 IST close (10:00 UTC), when that day's own close is legitimately known;
    3. **None** when nothing is knowable — never a fabricated fallback.
    `DataAccess.latest_vix()` / new `DataAccess.vix_as_of()` delegate to it, passing `self.db_path`
    explicitly (bar_store's own `DB_PATH` default points at a **Google Drive copy** of
    option_chains.db, not the repo file — a separate divergence worth resolving).
    `vix_as_of()` returns `(value, source)` so callers can record which series answered.
*   **Note**: `get_latest_vix` previously queried `timeframe='1m'` only. Before 2026-06-29 no 1m
    bars exist, so it returned None and `pipeline.py:186` silently fell through to
    `chain.get("vix")` — the 12.0 placeholder. The "prefer database-sourced VIX" comment there was
    therefore not doing what it claimed for any historical run.
*   **Retained deliberately**: `data_quality_agent.py` still reads `captures.vix` — it is checking
    that column's health, which is the one correct reason to read it.
*   **Tests**: `strategy_framework/tests/test_vix_source.py` (12). Mutation-checked — reverting to
    `captures.vix`, or using a naive `ts <= now` on daily bars, each turns 3 tests red.
*   **Still open**: consumers that take VIX as an *input* — `extractor.vix_regime`,
    `signals/vrp.py`, the complacency quadrant — now receive real values for the first time. Any
    threshold in them tuned against a constant 12.0 is uncalibrated and should be revisited.

## D-SC-06: One file decides the databases (2026-08-15)

*   **Context**: the Google Drive SQLite path was pasted verbatim into `bar_store.py:23`,
    `chain_store.py:23`, `backend/quant/fundamentals.py:6`, `backend/shock_recovery_routes.py:26`,
    and 25 files under `data_agent/`. `strategy_framework/config/settings.py` carried a SIXTH
    independent resolver, and `persistence.py:29` used a bare relative
    `sqlite3.connect("option_chains.db")` bound to the working directory. Three of the six had no
    fallback — which is why `bar_store.get_latest_vix()` could not open a database off the Mac.
    Separately, the Postgres DSN had **two different defaults** in circulation across
    `data_agent/`: `postgresql://localhost/niftyoptions` (14 sites) and
    `postgresql://postgres@localhost:5432/niftyoptions` (3).
*   **TWO STORES, SPLIT BY DOMAIN — deliberate, not a migration in flight** (confirmed with the
    desk 2026-08-15):
    | store | holds | written by |
    |---|---|---|
    | SQLite on Google Drive | `captures`, `chain_rows`, `price_bars` | the download pipeline |
    | PostgreSQL `localhost/niftyoptions` | macro + fundamentals | `data_agent/macro/` (13 of 19 files), `data_agent/fundamentals/` (19 of 31) |
    Many data_agent scripts touch BOTH in one run — read chains from SQLite, write fundamentals to
    Postgres. `pg_data/` is a live PG 18 cluster (clean shutdown 2026-08-01). `psycopg` is
    deliberately NOT in `backend/requirements.txt`: the backend and strategy_framework are
    SQLite-only.
*   **Decision**: `db_config.py` at the repo root is the single source for BOTH stores.
    SQLite: `DB_PATH` / `connect()` / `resolve_db_path()` / `resolve_writable_db_path()`.
    Postgres: `PG_DSN` / `connect_pg()` / `resolve_pg_dsn()`. Recorded in `CLAUDE.md` as
    mandatory: no path or DSN literals, no `sqlite3.connect()` / `psycopg.connect()` on a literal.
    SQLite order: `$NIFTY_DB` → `$OPTION_CHAINS_DB` → Google Drive (primary) → repo-local copy.
    Postgres order: `$DATABASE_URL` → `$NIFTY_PG_DSN` → `postgresql://localhost/niftyoptions`
    (the drift resolved to the 14-site variant).
*   **Readers vs writers (SQLite)**: `resolve_db_path()` may fall back to the repo-local copy;
    `resolve_writable_db_path()` may NOT — it raises when Drive is absent, so a download can never
    silently land in the copy.
*   **Correction (recorded, since the first version of this entry was misleading)**: the claim "no
    pragma was being set anywhere" was literally true but gave the wrong impression. Python's
    `sqlite3.connect()` defaults to `timeout=5.0`, i.e. `busy_timeout = 5000 ms` — verified. The
    change is **5s → 30s**, not 0 → 30s.
*   **And the pragma was initially inert**: `db_config.connect()` set 30s, but `chain_store._conn`,
    `bar_store._conn` and `DataAccess._conn` all called `sqlite3.connect()` directly, so they kept
    the 5s default. All three now route through `db_config.connect()`; verified in-process at
    30000 ms each, journal_mode still `delete` (rollback journal).
*   **`busy_timeout` is a tolerance, not a lock fix**: it changes "fail after 5s" into "wait up to
    30s". A lock held for 40s still raises `database is locked`. Contention is not only Drive —
    uvicorn, a data_agent download and a `run_*.py` study can all queue on the same file.
*   **SQLite pragmas**: `connect()` sets `busy_timeout = 30000` and deliberately leaves
    journal_mode at the rollback-journal default. **WAL must not be enabled** while the file is on
    a synced directory — the `-wal`/`-shm` sidecars are uploaded and locked independently by the
    sync client. Rationale per POSTGRES_MIGRATION_PLAN.md §1.1; no pragma was being set anywhere
    before this.
*   **Rewired**: `bar_store`, `chain_store`, `persistence`, `backend/quant/fundamentals`,
    `backend/shock_recovery_routes`, `strategy_framework/config/settings` (now delegates).
    All report the same resolved path; `get_latest_vix()` works with no explicit `db=` for the
    first time.
*   **data_agent rewired (35 files)**: classified first, then converted —
    | resolver | files | why |
    |---|---|---|
    | `resolve_db_path` | 20 | SQLite readers |
    | `resolve_writable_db_path` | 5 | SQLite WRITERS (`backfill_daily_bars`, `download_nse_participants`, `sync_india_macro`, `download_fii_dii`, `earnings_reaction_backfill`) — must never fall back to the local copy |
    | `resolve_pg_dsn` | 20 | Postgres readers/writers |
    | `resolve_pg_admin_dsn` | 4 | the `CREATE SCHEMA` scripts |
    (counts overlap: 13 files are dual-store and import two resolvers.)
    `os.getenv("OPTION_CHAINS_DB", <literal>)` / `os.getenv("DATABASE_URL", <literal>)` wrappers were
    collapsed — the resolvers already honour those env vars, so behaviour is preserved exactly.
    Verified: 35/35 compile, 35/35 bootstrap blocks execute and resolve to the repo root at both
    nesting depths (`../..`, `../../..`), zero DB literals remain outside `db_config.py`.
*   **The admin DSN is preserved, not flattened**: four scripts run `CREATE SCHEMA` and were already
    connecting as the `postgres` superuser for that privilege. Collapsing them onto the ordinary DSN
    would have silently removed a right they depend on, so `resolve_pg_admin_dsn()` keeps the split
    explicit — with the better fix documented in its docstring (grant CREATE to the ordinary role,
    fix schema ownership, then delete the function).
*   **Not verified here**: no script was RUN. This sandbox has neither the Drive mount nor a live PG
    instance, so the checks are static + import-level only. First real run should be a read-only one
    (`sector_scorecard.py` or `bank_snapshot.py`) before any writer.

## D-SC-03a: Remaining duplicate math retired (2026-08-15)

*   `backend/quant/rnd.py` — `bs_call` and a local `_norm_cdf` existed only to serve the bisection
    IV solver replaced under D-SC-03. Verified **zero call sites**, removed; the normal CDF now
    comes from `bs.ncdf`.
*   `backend/quant/strategy_probability.py` — a third `_norm_cdf` replaced by the same import.
    Its `implied_move()` docstring claimed the 1σ move "approximately equals the ATM straddle
    price"; it is **1.2533×** the straddle. The formula was right, the documentation was the
    D-SC-01 misconception written down a third time. Corrected.
*   `strategy_framework/features/extractor.py` — `_iv_skew_features_legacy` and
    `_iv_skew_features_legacy_impl` had zero call sites and `_legacy_impl` carried its own inline
    copy of the BS inversion `bs.iv_skew` owns. Retired to
    `_to_delete/retired_legacy_20260815/` (3,109 chars).
*   **Result**: exactly ONE normal-CDF definition in the tree (`bs.py:17 ncdf`), and two IV
    inverters — `bs.py` (Black-Scholes on spot) and `skew_engine.py` (Black-76 on the parity
    forward), both deliberate per D-SC-03.
*   118/118 tests pass; every rewired module imports clean.

## D-SC-07: §3/§5/§6 triage (2026-08-15)

*   **§3c empty bid/ask — NOT A BUG, closing it.** All 13,126 captures are
    `source='api_json'`, note `'Historical hourly backfill'`; a historical bars API does not
    return quote depth. `breeze_loader.py:85-86` maps `best_bid_price`/`best_offer_price`
    correctly but has never run in production. `chain_store.py:187-194` builds `chain_snapshots`
    with `CASE WHEN call_bid > 0 AND call_ask > call_bid ... ELSE NULL` plus MID_2S / LTP price
    tiers, i.e. it degrades correctly. **D-MA-09a already documents this era** ("all pre-fix rows
    are treated as LTP-based ... emissions carry the MID_IS_LTP flag"). Resolves itself when live
    capture starts. Removed from the open register.
*   **§3a `iv: 0.0` is TWO fabrication points, not one.** `breeze_loader.py:155` emits
    `row_iv = 0.0` when neither side solves — and `src/lib/analytics.ts:122` then does
    `iv: isNaN(closest.iv) ? 14.0 : closest.iv`, substituting a hardcoded **14.0%**. Changing the
    backend to `None` alone only moves which invented number surfaces (0.0 → NaN → 14.0), and the
    frontend's is worse because 14% looks plausible. Consumers: `ComplacencyPanel.tsx:92` renders
    it as a headline %, `App.tsx:384` feeds `calculateComplacency`, `App.tsx:636` as `atm_iv`.
    Fix must be end-to-end (backend None → UI renders an absent state), so it is a UI change, not
    a one-line backend edit. Still open.
*   **§5 Postgres — not verifiable from the sandbox** (no postgres binaries, no psycopg). What IS
    known: `pg_data/base` holds OIDs 1/4/5/16385 — template0, template1, postgres and exactly ONE
    user database, consistent with `niftyoptions`. The rewire is correctly positioned:
    `sector_scorecard.py:29` binds `resolve_pg_dsn` before the psycopg guard at :38-40, and the
    scripts fail fast with a clear install message rather than an opaque error.
    **24 of the 35 rewired scripts contain no write statement at all** — those are the safe first
    runs (`sector_scorecard.py`, `bank_snapshot.py`, `bank_price_audit.py`, …). Confirm
    `native_env` has psycopg before the first run.
*   **§6a newsindex duplication is LOAD-BEARING, not accidental.** `desk_note_examples.py` and
    `build_events.py` are byte-identical in `newsindex/` and `newsindex/NewsAgent/engine/`, and
    BOTH copies are imported — via bare `import build_events` from two different directories with
    sys.path manipulation (`build_events.py:47` explains it: "used to write events.db BESIDE
    ITSELF. Callers do a bare `import build_events`"). Same trap as the three `skew_engine.py`
    copies, but currently identical, so the exposure is future drift rather than present
    inconsistency. Not touched — it needs the bare-import pattern replaced first.
*   **§6b Vibe-Trading-main — NOT AN ISSUE, closing it.** A vendored MIT open-source checkout
    (HKUDS Vibe-Trading, PyPI `vibe-trading-ai` v0.1.11), 1,204 .py files, **zero imports from the
    app, zero files tracked in git**, already assessed in `VIBE_TRADING_VS_NIFTYOPTIONS.md`
    (2026-07-21). Its only real cost is dominating repo-wide greps. Worth moving outside the repo
    or adding to `.gitignore`; nothing to audit.
*   **Bug introduced and fixed the same day**: the data_agent DSN sweep's regex also rewrote
    **17 shell usage examples inside module docstrings** (`export DATABASE_URL=resolve_pg_dsn()`,
    which is not valid shell). Restored to literal example DSNs; verified 0 mangled examples and
    0 DSN/path literals in code outside docstrings.
