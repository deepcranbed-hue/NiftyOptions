# Antigravity Implementation Brief — Minute-Data Analytics v1
**Modules:** `alignment/` (new), `dispersion_engine.py` (new), `vrp_pipeline.py` (new) | **Consumers:** strategy suggester, complacency gauge, daily report
**Blast-radius tier:** Tier 2 — analytics/signal layer feeding decision support; no order path, human reviews before trade action.
**DECISIONS.md entries required:** D-MA-01 (bar-grid + as-of join contract), D-MA-02 (Ledoit-Wolf shrinkage + window choice), D-MA-03 (dispersion-richness definition), D-MA-04 (all thresholds marked PRIOR pending history), D-MA-05 (skew methodology: 25Δ via delta-space interpolation, fixed-offset deprecated, expiry-day splice), D-MA-06 (DTE validity matrix — per-signal, never a data filter), D-MA-07 (card-invariant rule: failed invariant → DATA_INCONSISTENT, never displayed numbers), D-MA-08 (OI flow three-state incl. writer buy-back + spread gate).

---

## 0. Data inventory and honest statistical posture

| Stream | Granularity | Depth | Statistical role |
|---|---|---|---|
| NIFTY index | 1-min | growing | Full use — realized vol, seasonality flags |
| 50 constituents | 1-min | ~94k stock-minutes over 5 days | **Primary asset** — correlation/dispersion engine |
| Option chain (index only) | **1-min snapshots (upgraded from 30-min)** | days of history, growing | Minute-cadence IV/skew/OI streams now first-class. **History depth, not cadence, remains the calibration constraint** — threshold discipline (D-MA-04) unchanged. |
| India VIX | 1-min | Jun 29 →, growing | Continuous model-free 30-day IV level — minute VIX−RV proxy (§3.3a); own intraday seasonality (decays into close on quiet days) needs same time-of-day treatment as volume |
| USDINR, gold, silver, copper (MCX/FX) | 1-min | Jun 29 →, growing | Cross-asset cockpit rows; reuse Global Cues sign logic (silver regime, USDINR dual read). **Different trading calendars** — see §1.5 |
| Sector news | event stream | ongoing | Veto/sizing input later; not in v1 scope |

**Hard rule (D-MA-04):** every threshold, percentile, window, and regime boundary in v1 ships tagged `PRIOR`. Nothing graduates to `FITTED` until ≥60 sessions of chain history exist. The UI must display the tag (same convention as `cue_betas` in Global Cues v2).

**Capture cadence: resolved.** Chain now captured at 1-min; the earlier 30-min→5-min action is superseded. Historical 30-min snapshots remain usable via the as-of join (the join is cadence-agnostic by construction).

---

## 1. PR-1 — Alignment layer (`alignment/`)

The shared foundation. All downstream results are silently corrupted if this is wrong, so it ships alone with its own fixtures.

### 1.1 Canonical bar grid
- Trading grid: 09:15–15:30 IST, 375 one-minute bars, NSE calendar-gated (reuse the `exchange_calendars` integration from Global Cues v2 — same holiday logic, same `session_state` enum).
- Every stream is projected onto this grid. Constituent bars missing a print (illiquid minute, halt) → `null` volume-bar, **not** forward-filled price. Returns computed only between actual prints; a stock halted 10:00–10:20 contributes no returns in that window rather than twenty zeros.
- Bad-tick filter: reject 1-min returns with |r| > 8 × trailing 60-min stock vol AND immediate full reversal next bar (classic bad print signature). Log rejections with provenance; never silently drop.

### 1.2 As-of join contract
```
join_asof(left=minute_grid, right=chain_snapshots, direction="backward")
```
- A chain snapshot timestamped 10:00 is visible to bars ≥ 10:00 only. Realized quantities paired with that snapshot are computed over windows **ending** at 10:00 (e.g., 09:30–10:00 RV), never spanning it.
- News events join the same way: last-known-at-or-before. No event may influence any bar earlier than its timestamp. (Carries forward the lookahead discipline from the news-sentiment work.)
- **Fixture test:** synthetic snapshot at 10:00 with a poisoned 10:00–10:30 window; test asserts the paired RV uses 09:30–10:00 and the poison is untouched.

### 1.3 Weights and the reconstruction identity (weight validation for free)
- NIFTY 50 free-float weights from the existing sector hierarchy config (Sept-2025 rebalance corrected). Stored with effective-date; weight changes are events, not overwrites.
- **Identity check, run daily:** reconstruct the index minute return as Σ wᵢ·rᵢ and regress against the actual index minute return. R² should exceed ~0.97 with near-zero intercept. This single test validates weights, bar alignment, and corporate-action handling simultaneously. A drifting R² is the earliest warning that a weight or symbol mapping went stale. Surface it as a data-quality metric for the DataQualityAgent.
- Deseasonalization: **deferred** (D-MA-04). Five days cannot estimate 13 half-hour buckets. Until ≥30 sessions accrue, tag the 09:15–09:45 and 15:00–15:30 buckets `SEASONAL_INFLATED` on every output rather than adjusting them. Fit the intraday profile later from accumulated data.

### 1.4 Storage (SQLite, WAL mode)
Tables: `minute_bars(symbol, ts, o,h,l,c,v, quality_flags)`, `chain_snapshots(ts, strike, expiry, cp, bid, ask, mid, iv_mid, oi, volume)`, `realized_metrics(ts, window, rv_index, rv_constituent_weighted, corr_avg, dispersion, flags)`. All IV computed from **mid**, never LTP (spread already captured — use it).

---

### 1.5 Per-stream calendars (cross-asset minute streams)
The single NSE grid does not cover the new streams. Per-stream session handling:
- **India VIX:** NSE hours, same grid as index/constituents.
- **USDINR (onshore):** 09:00–15:30 — 15 minutes ahead of the equity open; bars before 09:15 are pre-open context, not grid members.
- **MCX metals (gold/silver/copper):** trade until 23:30/23:55 IST. Intraday cockpit uses 09:15–15:30 overlap only; the **evening session feeds the next morning's opening context** (change since India prior 15:30 close — same convention as Global Cues §1.4), never today's intraday windows.
- Cockpit convention for all cross-asset rows: change measured **since India's prior 15:30 close**.

## 2. PR-2 — Constituent correlation / dispersion engine

### 2.1 Core quantities (rolling window, default 60-min, `PRIOR`)
- Standardize constituent 1-min returns within the window; estimate covariance via **Ledoit-Wolf shrinkage** (`sklearn.covariance.LedoitWolf`). Raw sample covariance at N=50, T=60 is mostly estimation noise; log the shrinkage intensity per window as a diagnostic (persistently high intensity ⇒ window too short or data too gappy).
- **Average pairwise correlation** ρ̄(t): weighted mean of off-diagonal correlations from the shrunk matrix.
- **Effective correlation** via the variance ratio: ρ_eff(t) = RV²_index / RV²_basket, where RV_basket = Σ wᵢ·RVᵢ (weighted average constituent vol). This is model-light and serves as a cross-check on ρ̄ — the two should co-move; divergence flags an alignment problem.
- **Dispersion** D(t): cross-sectional weighted stdev of constituent returns over the window (CSAD-style). High D with low ρ̄ = index pinned while names rotate; low D with high ρ̄ = everything moving together.
- All outputs as continuous z-scores against their own trailing distribution (tagged PRIOR at this depth) — no binary regime labels; same tanh-strength convention as Global Cues v2 so the suggester consumes one signal vocabulary.

### 2.2 Interpretation contract (what the suggester receives)
```json
{
  "ts": "...",
  "corr_z": 1.8,
  "dispersion_z": -1.2,
  "read": "correlation elevated — index RV mechanically supported; short-vol regime deteriorating",
  "confidence": "PRIOR",
  "flags": ["SEASONAL_INFLATED"]
}
```
Rising ρ̄ → index realized vol rises mechanically (index variance = Σ wᵢwⱼσᵢσⱼρᵢⱼ) → hostile to short-gamma structures. Falling ρ̄ with high dispersion → constituent moves cancel at index level → friendly to condor/fly. This is the engine's one-sentence job description.

### 2.3 Volume features (constituent OHLCV — rupee-normalized)
All volume quantities use **traded value (close × volume)**, never raw shares (incomparable across stocks). Four features, in priority order:

1. **Volume-confirmed correlation regime.** Emit `volume_state` (total constituent rupee volume vs same-time-of-day baseline, z-scored) alongside `corr_z`. Contract: corr spike + heavy volume = high-confidence risk-off; corr spike + thin volume = flag `LOW_VOLUME_UNCONFIRMED` (probable estimation noise or single-name artifact). The suggester receives the pair, never corr alone.
2. **Amihud illiquidity / gap-susceptibility breadth.** Per stock per window: `amihud = |r| / rupee_volume`. Aggregate: fraction of constituents whose Amihud z exceeds +1 (`illiquidity_breadth`). Rising breadth = same order flow moves prices more = elevated gap susceptibility — direct input to the gap-risk down-sizer when the premium-selling algo eventually activates.
3. **Sector rotation share.** Each sector's share of total rupee volume vs its trailing normal share; z-scored deltas surface as rotation chips on the sector panel. Flow fact, not price inference.
4. **News-burst volume validation (deferred to news-integration phase).** Abnormal volume in named constituents as confirmation of a genuine news event — immune to the price-ticker circularity, since a headline generated from a price move does not create anticipatory volume.

**Seasonality guard (stricter than for vol):** intraday volume is violently U-shaped (open bucket 3–5× midday). All abnormality measures are computed **relative to the same time-of-day bucket across available sessions** plus a rolling same-day baseline, tagged `SEASONAL_PARTIAL` until ≥30 sessions permit a fitted profile. An unadjusted volume z is forbidden — it fires every open.

**Out of scope (do not build):** minute-scale volume→direction prediction; signed order flow / VPIN-style toxicity (requires tick-level aggressor data — bar-level sign approximation is misleading, not merely noisy).

**Additional acceptance tests:**
| # | Case | Expected |
|---|---|---|
| 9 | 09:15 open bucket, normal day | volume abnormality ≈ 0 after time-of-day adjustment (regression: must not flag every open) |
| 10 | Single heavyweight 5σ volume, rest quiet | `LOW_VOLUME_UNCONFIRMED` not set incorrectly; sector rotation attributes to correct sector; corr window unpolluted (LW shrinkage intensity logged) |
| 11 | Shares-vs-value audit | grep test: no feature consumes raw `volume` without price multiplication |

### 2.4 Heavyweight volume momentum & opening attention leader

**Hypothesis (user-observed, status `PRIOR`):** abnormal EOD volume in index heavyweights persists into the next session's open. Volume autocorrelation is well-documented in microstructure literature, so the prior is reasonable — but the tradeable content is **volatility/gap information, not direction**. Encode as features + a logged hypothesis test, not as a directional signal.

**Feature A — EOD volume surge (computed 14:45–15:25, delivered before the close):**
- Per top-10-weight stock: rupee volume in the last 45 minutes vs that stock's own same-window baseline across available sessions, z-scored (`SEASONAL_PARTIAL` until ≥30 sessions).
- Aggregate: `eod_surge = max z among top-5 weights` plus count of heavyweights above +2z.
- **Consumer:** overnight gap-risk block, alongside Amihud breadth (§2.3.2). Amihud = market fragile; EOD surge = live flow present. Both elevated = worst overnight state for short-gamma carry. Surfaces at ~15:00 as a close-of-day sizing/hedging chip — actionable while the market is still open.
- **Logged hypothesis test (runs automatically, no trading dependency):** record (EOD surge z) → (next-open 15-min RV z, |gap|, open volume z) pairs per stock per day. After ≥60 sessions, report the rank correlation. Claim graduates from `PRIOR` only on that evidence. Explicit non-claim: no next-day *return* prediction is recorded or displayed.

**Feature B — opening attention leader (computed on the 09:15–09:45 window):**
- Rank by **abnormal** opening rupee volume: each stock's opening-window value vs its own opening baseline. Never raw volume (raw leader ≈ weights table: RIL/HDFCB daily — zero information).
- Output: top-3 abnormal leaders with z, sector, and same-window return.
- **News attribution (AttentionAgent task):** for each leader ≥ +2.5z, query the news store for stock + sector headlines from the prior 18 hours; render "volume leader: {stock} ({z}σ) — {headline}" or, if the store is empty, **"unexplained abnormal volume"** — the empty case is a stronger flag, not a suppressed row.
- **Circularity guard (hard rule):** the volume→news join is *attribution only*. It must never be logged, stored, or later counted as "news predicted volume" — volume was observed first. Direction of inference is one-way, mirror-image of the ticker-circularity rule in the sentiment system.
- Event exclusions for both features: F&O expiry days, index-rebalance effective dates, and stocks with same-day corporate actions are tagged `EVENT_VOLUME` and excluded from baselines (they would otherwise corrupt every z-score for weeks).

**Additional acceptance tests:**
| # | Case | Expected |
|---|---|---|
| 12 | Raw-volume audit | leader ranking consumes abnormal z, never raw rupee volume (RIL must not lead by default) |
| 13 | Expiry-day fixture | day tagged `EVENT_VOLUME`, excluded from surge/leader baselines |
| 14 | Leader at 3σ, news store empty | row renders "unexplained abnormal volume" — not suppressed |
| 15 | Hypothesis log | pairs table populates daily; no field for next-day return direction exists in the schema |

### 2.5 Flow-anomaly detectors (index-pushing footprints)

**Motivation:** SEBI's Jane Street order (Jul 2025) describes two mechanical patterns — intraday pump-and-reverse (aggressive heavyweight cash/futures buying lifting the index while larger bearish options positions build, then afternoon reversal) and expiry-day close marking (concentrated final-window flow pushing settlement toward strikes favoring an options book). Both leave footprints in constituent minute volume + index prints + chain OI.

**Hard framing rule:** OHLCV bars detect *footprints*, never actors. All outputs are labeled "flow-driven anomaly" / "index-pushing footprint" — the words "manipulation" or any entity attribution are forbidden in UI, logs, and reports. Purpose is **defensive**: distrust pins, widen wings, reduce size on high-anomaly days.

**Detector 1 — move concentration.** Per 30-min window: per-stock contribution wᵢ·rᵢ to index return (reuses reconstruction identity); Herfindahl H of positive-side (or negative-side) contributions; score = H × mean abnormal-volume z of the top-3 contributors. Broad move → low; 2–3 heavyweights on abnormal volume doing the whole move → high.

**Detector 2 — impact-reversal.** Signed-flow proxy per heavyweight: sign(bar return) × rupee volume (bar-level approximation — coarse, acknowledged). Score: concentrated-flow move in window W followed by ≥60% retracement within 30–90 min, weighted by the flow z. The pump-and-reverse day shape. Logged daily; distribution builds the baseline.

**Detector 3 — cash-options divergence (the pattern's signature pair).** Join Detector 1 with the OI burst detector: concentration score elevated AND same-window chain shows opposing positioning build (put OI accumulation / call-writing bursts while spot is pushed up, or mirror). Requires 5-min chain cadence. Neither leg alone fires this — only the opposition.

**Detector 4 — expiry close-marking (settlement window).** NIFTY expiry settles on the last-30-min index VWAP → target window 15:00–15:30. Inputs: heavyweight abnormal-volume z in-window; index drift direction vs day-VWAP; drift target = nearest high-OI strike (from walls); wall-migration velocity (static wall + late concentrated flow toward it = pattern). Output: settlement-push score + direction + implicated strike. **Runs every expiry day at 14:45 with live updates; consumer = position manager (the live condor is the use case).**

**Composite:** daily `flow_anomaly` score (max of detectors, with contributors listed) on the cockpit; expiry days additionally show Detector 4 live. Every firing is auto-joined to the news store first — a genuine headline explaining the flow *demotes* the score (information-driven, not push); "no news + high score" is the interesting state. `EVENT_VOLUME` days (rebalance, block-deal disclosures) excluded from baselines but still displayed with their tag.

**Acceptance tests:**
| # | Case | Expected |
|---|---|---|
| 16 | Broad 0.6% move, all 50 names participating | concentration score low; no alert |
| 17 | Synthetic pump-reverse fixture (3 heavyweights, 3σ volume, full PM retracement) | Detectors 1+2 fire; report shows contributors and retracement stats |
| 18 | Concentration high + put-OI burst same window | Detector 3 fires; either leg alone does not |
| 19 | Expiry 15:05, drift toward 24,400 wall on heavyweight volume | Detector 4 emits score + strike; visible on cockpit within one bar |
| 20 | Vocabulary audit | grep: "manipulat" absent from all output strings, logs, and report templates |

### 2.6 Sector aggregation
Roll constituent minutes into sector vol/correlation via the sector hierarchy — sector-level RV and intra-sector ρ̄. This is the layer where sector-news bursts will later be tested against sector RV response (news→vol channel, not news→direction). v1 computes and stores; no news joins yet.

---

## 3. PR-3 — Dispersion-vs-IV richness signal + VRP pipeline (validation mode)

### 3.1 Index IV extraction (from 1-min chain snapshots, mid-based)
- ATM IV: vega-weighted average of the two straddles bracketing spot, nearest weekly expiry, from mid quotes. Reject snapshots where ATM spread > threshold (wide-market flag) — a 5-day sample cannot afford polluted points.
- Term handling: annualize with actual trading-time remaining (expiry-day afternoon IVs are dominated by pin dynamics — flag, don't delete).

### 3.2 The richness signal (dispersion-adjusted IV)
```
richness(t) = IV_atm(t) / RV_basket(t) · f(ρ̄(t))
```
Conceptually: index IV should be justified by constituent vol × prevailing correlation. IV rich relative to what dispersion-adjusted constituent vol supports ⇒ index vol expensive ⇒ seller-friendly, and vice versa. v1 ships the components (IV_atm, RV_basket, ρ̄, and the raw ratio) with **no threshold** — plot and accumulate. The functional form f(·) is explicitly deferred to calibration (D-MA-03); do not let an implementation agent invent one.

### 3.3 VRP pipeline — validation mode only
- VRP(t) = IV²_atm(t) − RV²_index(t→t+h), h matched to snapshot spacing, annualized consistently.
- Purpose at this depth: **sign and magnitude sanity across the 5 days** (does the recently-observed negative VRP reproduce from raw data?), end-to-end computation correctness, and forward accumulation. The regime gate for the premium-selling algo activates only after ≥60 sessions (D-MA-04); until then the panel shows VRP with the `PRIOR/VALIDATION` badge.

---

### 3.3a VIX-based minute VRP proxy (new stream)
`vix_rv_spread(t) = VIX(t) − annualized minute RV` — continuous, chain-independent, minute cadence.
- **Horizon mismatch stated on the card:** VIX is 30-day implied vs backward short-window realized; the spread is structurally positive most days (event premium in 30-day IV). Card label is **"VIX − realized"**, never "VRP". The chain-based same-expiry VRP (§3.3) remains the clean, slower measurement.
- VIX z-scores require time-of-day adjustment (VIX drifts down into close on quiet days) — same `SEASONAL_PARTIAL` regime as volume until ≥30 sessions.

**Card-invariant rule (applies to every cockpit card):** each card declares render-time invariants; any violation replaces the card's numbers with a `DATA_INCONSISTENT` badge + which invariant failed. **Placement (binding, per D-MA-07 review 07-Jul):** invariants are computed **in the signal engine from the emission's actual values** — every payload carries `invariants: {passed, failures: [{id, measured_values}]}`; the UI renders the verdict and never reimplements or mocks the checks. Failure messages display the measured values that failed, never canned diagnostic strings. A `checkInvariants()` that ignores data (e.g., returns a fixed result or keys off a UI toggle) is a spec violation, not a placeholder. **Simulation/fixture toggles are forbidden in the production control bar** — tests 21–28 are engine-level fixtures in the test suite; any demo injection lives behind a dev-mode flag. Merge evidence for this framework is the test-run output against engine emissions, not UI screenshots. Skew card invariants: (T-A) ΔRR_float = Δcall25 − Δput25 ±0.05 vpt; (T-B) ΔRR_fixed = (1−artifact_share)×ΔRR_float, and if fixed/floating ΔRR signs disagree render "mixed regime" — never a numeric artifact_share; (T-C) legs attributed at fixed strikes, ΔRR_fixed = Δcall_fixed − Δput_fixed; (T-D) IV changes in vol points only (`vpt`), grep-audit bans `%` on IV-change strings, |Δ| > 10 vpt flags; (T-E) every Δ labeled with its window, level vs change never unlabeled; (T-F) OI-join computed over the identical strike set and window as the leg attribution; (T-G) config chip recomputes from its three displayed inputs; (T-H) ATM ΔIV direction cross-checked vs India VIX Δ same window (persistent disagreement = mid contamination); (T-I) same-strike put/call IV parity gap > threshold flags the strike's mids before they feed any leg.

**Index move attribution strip (standing cockpit element, distinct from attention leaders):** the wᵢ·rᵢ decomposition (Detector 1's input) displayed always: index move in points → top-5 contributors in points, cumulative share, breadth (advancers/decliners within NIFTY 50), and a broad/narrow chip from the cumulative-share level (`PRIOR` cutoffs). Invariant: Σ all-50 contributions = index move within the reconstruction residual. Rationale: attention leaders rank volume abnormality; this strip ranks point contribution — a high-volume flat-price stock (block cross) leads one and vanishes from the other, and that divergence is itself displayed ("high volume, no direction").

### 3.4 Minute skew stream — decomposed, never a bare number (chain now 1-min)

Motivated by a live observation (06-Jul: RR steepening negative while spot rises — four candidate causes, three checks). The skew panel must always emit the decomposition, because a bare RR change is uninterpretable:

1. **Dual measurement:** floating-delta RR (25Δ call IV − 25Δ put IV) AND fixed-strike RR (same strikes as session open). Divergence between the two = sticky-strike delta-rolling artifact. Emit `artifact_share = 1 − (Δfixed / Δfloating)` with three guards: (a) |Δfloating| below dead-band → share **undefined**, card shows "quiet"; (b) Δfixed/Δfloating sign disagreement → render "mixed regime", never a numeric share (T-B); (c) share < 0 is valid and informative (rolling *masked* genuine repricing — floating understated the flow): display raw, never clamp, with both Δs shown beside it.
2. **Leg attribution — at the fixed (session-open) strike set, never floating strikes** (T-C; floating legs embed the artifact the card isolates — circular). Δput-side IV vs Δcall-side IV per window, in vpt. Put richening (hedging demand) vs call cheapening (overwriting supply) are opposite stories with identical RR prints.
3. **Flow join — three OI states + spread gate:** at the moving leg's strikes: OI↑+IV↑ = new buying; **OI↓+IV↑ = writer buy-back / short-covering — aggressive demand, often the most urgent flow state, NOT "no flow"**; OI flat+IV↑ = repricing. Additionally compute Δ(bid-ask spread) over the window from stored quotes: material spread widening means part of the mid-IV change is quote-width artifact — discount and flag `SPREAD_WIDENED` (direct measurement replaces the "market-maker widening" inference).
4. **Configuration chip — five named states, dead-banded inputs, honest fallback.** Each input (spot Δ, ATM ΔIV vpt, leg Δs) must clear its own dead-band before contributing; sub-dead-band inputs count as flat (a +0.05% spot print is not spot↑ — same defect family as the 0%-headwind bug). Named configurations: `spot↑ vol↑ putskew↑` "hedged rally (fragile)"; `spot↑ vol↓ callskew↓` "overwriting grind"; `spot↑ callskew↑` "call chase — upside tail risk"; `spot↓ vol↑ putskew↑` "orderly hedging"; **`spot↓ vol↑ callskew↑` "squeeze-risk-into-weakness"** — upside being paid for during a decline = coiled short-covering risk; rare, which is exactly why it must be named rather than left to fall through. Any combination not matching a named row, or with too many inputs sub-dead-band, renders **"unclassified — mixed tape"** — nearest-match forcing is forbidden. A chip that always answers trains false trust; "no clean read" is a valid, first-class output. Configurations remain descriptive positioning facts; no directional claim displayed.

**Acceptance tests:** (21) synthetic sticky-strike day: spot +0.8%, fixed-strike IVs unchanged → floating RR moves, `artifact_share ≈ 1`, panel labels it artifact; (22) put-leg richening fixture → attribution shows put side, configuration chip "hedged rally"; (23) RR change never displayed without leg attribution present in the same payload; (23a) |Δfloating| below dead-band → card "quiet", no share number; (23b) Δfixed > Δfloating fixture → negative share displayed raw with both Δs; (23c) OI↓ + IV↑ fixture → flow state "writer buy-back", not "repricing"; (23d) spread doubling over window → `SPREAD_WIDENED` flag, IV change discounted; (23e) spot +0.05% with genuine put richening → spot input counts flat, chip does NOT read "hedged rally"; (23f) spot↓ vol↑ callskew↑ fixture → "squeeze-risk-into-weakness"; (23g) non-matching combination → "unclassified — mixed tape", assert nearest-match code path does not exist.

### 3.4a Skew computation methodology (binding)

**Primary measure: 25Δ risk reversal.** Fixed-offset (ATM±200) is **deprecated for tracked series** — 200 points is a different standardized distance at every vol level and DTE, so the series drifts without any skew repricing. Permitted only as a legacy display row, labeled as such.

Computation per minute snapshot:
1. **Forward from parity:** F = K_atm + e^(rT)(C_mid − P_mid). All moneyness/delta off F, never spot.
2. **Smile from OTM side only:** puts for K < F, calls for K > F (ITM mids unreliable; parity gate T-I filters polluted strikes before entry).
3. **Per-strike delta with per-strike IV:** Δ(K) = N(d₁) using σ(K) from that strike's own mid IV — never flat ATM vol (misplaces the 25Δ strike by 1–2 strikes exactly when skew is steep).
4. **Interpolate in delta space:** locate the two listed strikes bracketing |Δ| = 0.25, linear-interpolate IV between them by delta. No parametric surface fits (SVI is EOD tooling; unstable at 375 fits/day).
5. **ATM anchor:** strike nearest F (record it; the fixed-strike series of §3.4 uses the session-open anchor set).

**DTE validity matrix (the "expiry switch" — per-signal, never a data filter):** all chain data is stored unconditionally; validity applies at signal computation. IV level / VRP / skew / RND: invalid at DTE < 2 — splice to next expiry when its chain exists, else the series **gaps** (no dying-expiry numbers displayed). OI / walls / max-pain / wall-migration: valid at all DTE, and DTE < 2 observations are tagged `EXPIRY_REGIME` — these are the premium training sample for pin-fade and Detector 4, never excluded. Settlement-window flow: exists only at DTE < 2. Historical strategy analysis for expiry E consumes only rows valid for their signal family — a blanket "drop last-2-day data" rule is forbidden (it would delete the expiry-day toolkit's calibration set while fixing nothing the per-signal rule doesn't).

**Expiry-day capture note:** on every expiry Tuesday, next-expiry chain capture must be live from the open — expiry day is the only observation of OI/IV migration from dying to next expiry, and any same-day roll analysis is blind without it.

**Expiry-day rule:** as T→0 delta degenerates toward a step function and the 25Δ strikes collapse into ATM — the dying expiry's 25Δ RR is noise. When DTE < 2 (config; or T < 4 trading hours), the skew series **switches to the next weekly expiry**, with the splice date stamped on the series and shown on the card (`measuring: 14-Jul expiry`).

**Parallel cross-check measure: standardized-moneyness skew.** IV evaluated at z = ln(K/F)/(σ_ATM√T) = ±1 (interpolated the same way). Comparable across time like the delta measure, no delta iteration, degrades gracefully near expiry. The pair (25Δ RR, z±1 skew) must co-move; divergence beyond tolerance = computation flag — same estimator-pair pattern as ρ̄ vs ρ_eff.

**Acceptance tests:** (24) synthetic flat smile → both measures ≈ 0 at all DTE; (25) synthetic steep-skew surface with known 25Δ strikes → interpolated RR within 0.1 vpt of analytic; (26) DTE=1 fixture → series reads from next expiry, splice stamped; (27) per-strike-IV audit: delta computation consumes σ(K), grep bans flat-vol delta; (28) 25Δ RR vs z±1 skew tracking correlation > threshold over any session, divergence flags not silences.

## 4. PR-4 (exploratory, not blocking) — heavyweight lead-lag
- Lagged cross-correlation of top-weight constituents (RIL, HDFCB, ICICI, INFY) vs index at 1-min, on returns, using Hayashi–Yoshida for the option-stream comparisons (Epps-effect mitigation) and plain CCF for stock↔index (synchronous grids).
- Deliverable is a **finding memo**, not a signal: at 5 days this answers "is there anything here worth building on" only. Any options-lead-spot result is presumed a timestamp bug until the as-of join is audited.

---

## 5. PR-5 — UI: Intraday tab & Scenario Cockpit

**Placement:** Structure workspace → new left tab **Intraday** (route `/structure/intraday`), between Vol & RND and Price Chart. Vol & RND gains the VRP validation card; Data & Ops → Health gains the reconstruction R² metric. No new workspace.

**Scenario Cockpit layout (top to bottom = decision funnel):**
1. **Header strip:** NIFTY level + day %, India VIX level + day %, composed verdict chip. Verdict is **rule-based from the cards below** (never LLM-generated numbers); e.g. "vol bid · correlation rising · rupee soft".
2. **Four state cards:** VIX−realized spread (§3.3a, labeled exactly that), ρ̄ with volume-confirmation state and LW shrinkage intensity, dispersion z, USDINR with dual read (FII/IT). Every uncalibrated quantity wears its `PRIOR`/`VALIDATION`/`SEASONAL_PARTIAL` badge.
3. **Day selector chips** (Jun 29 → today): replay mode — same layout renders any stored session for shape comparison.
4. **NIFTY and VIX stacked on a shared time axis** (never overlaid — one y-axis per chart). The stack exposes the three configurations: spot↓+VIX↑ orderly hedging; spot↓+VIX flat complacency; spot flat+VIX↑ event premium.
5. **Cross-asset rows** (gold/silver/copper/USDINR): reuse Global Cues computations — silver regime chip, USDINR dual arrow. No new signal logic; re-render existing state pillars.
6. **Sector strip:** realized vol bars + rupee-volume rotation z chips (§2.3.3).
7. **Volume intelligence row (time-gated, §2.3–2.4):**
   - 09:15–10:00: **opening attention leaders** — top-3 abnormal-volume stocks with z, sector, window return, and news attribution ("{stock} ({z}σ) — {headline}" or "unexplained abnormal volume").
   - 14:45–15:30: **EOD gap-risk block** — heavyweight surge chip (max z + count >2z) beside **Amihud illiquidity breadth** (§2.3.2); both elevated renders the combined warning state ("live flow + thin liquidity — elevated overnight gap risk").
   - Rest of session: Amihud breadth alone, compact.
8. **Skew decomposition card (§3.4):** current RR with `artifact_share`, leg-attribution mini-bars (Δput-side vs Δcall-side IV), OI-join state at the moving leg, and the named configuration chip ("hedged rally (fragile)" / "overwriting grind" / "call chase — upside tail risk"). Test 23 applies to this card: no RR without attribution in the same render.
9. **Flow-anomaly line:** daily composite score (§2.5); expiry days additionally show Detector 4 live from 14:45, adjacent to the EOD gap-risk block (they share the settlement window and the decision).
10. **Footer:** date range, bars/chain as-of timestamps (separate — different streams), reconstruction R², seasonal flags. "Explain scenario" button → AttentionAgent composes the one-paragraph regime read strictly from state-pillar values (narrow grounded task; no free market commentary).

**State rule:** the cockpit reads only from stored state pillars/`realized_metrics` — it computes nothing itself, so replay and live share one code path.

## 6. Acceptance tests

| # | Case | Expected |
|---|---|---|
| 1 | As-of poison fixture (§1.2) | 10:00 snapshot pairs with 09:30–10:00 RV only |
| 2 | Reconstruction identity, all 5 days | R² > 0.97, intercept ≈ 0; metric emitted to DataQualityAgent |
| 3 | Halted-stock fixture (20-min gap) | zero fabricated returns; correlation window handles missing rows without forward-fill |
| 4 | Bad tick (spike + full reversal) | rejected with provenance log; RV unaffected |
| 5 | LW shrinkage | matrix PSD every window; shrinkage intensity logged; ρ̄ and ρ_eff co-move (corr > 0.7 over sample) |
| 6 | Wide-market snapshot | ATM IV rejected with `WIDE_MARKET` flag, not silently included |
| 7 | Weekend/holiday | no grid emitted; calendar gating identical to Global Cues v2 |
| 8 | Every displayed threshold | carries `PRIOR` tag in API payload and UI |

---

## 7. Sequencing
1. **Day 0 (done):** chain capture at 1-min — superseded the original 30-min→5-min action.
2. **PR-1** alignment layer + reconstruction identity. Nothing else merges until test #2 passes on all 5 days.
3. **PR-2** correlation/dispersion engine.
4. **PR-3** IV extraction + richness components + VRP validation.
5. **PR-5** Intraday tab + Scenario Cockpit (§5) — after PR-2/PR-3 populate `realized_metrics`.
6. **PR-4** lead-lag memo, opportunistic.

The premium-selling algo itself (VRP-gated condor/fly with the gap-risk down-sizer) is **out of scope for v1** — it activates when D-MA-04 graduates its inputs from PRIOR to FITTED. v1's job is to make that future calibration trustworthy.
