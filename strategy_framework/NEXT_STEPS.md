# Next steps & handoff notes

Read this together with `README.md`, `SKILL.md`, `REFERENCE.md`, `ARCHITECTURE.md`
and `MACRO_SIGNALS_SPEC.md`. This file is the "where we left off" list — the open
threads that a fresh chat can't otherwise know about.

## First, on your machine (environment — not code)

1. **Restart uvicorn** and **reload the frontend** so all recent backend/UI changes are live.
2. **Rebuild the feature store** — Signal Test → Attribution → tick "rebuild all" → Backfill
   (or `api.features_backfill(force=True)`). This populates the new columns
   `sig_vwap_score`, `sig_vol_index_score`, `sig_rel_volume_score` and the `sig_*_ok`
   status flags the analytics fast path relies on.
3. Point at the **Google-Drive `option_chains.db`** (the one with real CRUDEOIL / USDINR /
   GIFTNIFTY and NIFTY/constituent **volume**). The sandbox can't reach that path, so the
   new signals can only be *validated* on your machine.

## Open work (pick up here)

- [ ] **Validate the nine weight-0.0 signals** (`vwap`, `vol_index`, `rel_volume`,
      `crude_energy`, `usdinr`, `global_gap`, `futures_basis`, `futures_calendar`,
      `futures_flow`) on the real DB. Note the expected overlaps: `futures_flow`
      direction vs `technical_momentum`; `futures_calendar` vs the calendar sub-term
      inside `futures_basis`. In Signal Test:
      Horizon map (colour by IC / Sharpe), Attribution (predictor = signal,
      target = `fwd_ret_*`, condition = `vix_regime`), Correlation (check overlap —
      crude/usdinr likely one factor; vol_index/breadth may overlap). **Then raise the
      weight in `config/settings.py`** for whichever earn it, and add the name to
      `strategy/regime.py::_DIRECTIONAL` so it actually enters the trade blend (the
      deliberate safety interlock — a non-zero weight alone does nothing until it's in
      that list). Re-normalise weights to ~1.0; re-run the backtest to confirm improvement.
- [ ] **Scoreboard on the feature-store fast path.** The Horizon map (`signal_effectiveness`)
      already reads precomputed `sig_*_score` from the store with a live fallback
      (`source` = auto/live/store; returns `source`/`from_store`). Apply the *identical*
      treatment to `signal_backtest_all` (the "All signals" scoreboard) — same `_store_scores`
      helper, same per-snapshot "if ts in store use it, else evaluate" pattern.
- [ ] **Streaming / per-tick incremental design (for a live feed).** Only when wiring to a
      live tick source: replace trailing-window recomputation with O(1) rolling accumulators
      (running Σprice·vol & Σvol for VWAP, Welford for realised vol, monotonic deque for
      rolling max). Does nothing for historical backtests — it's live-latency architecture.
- [ ] **(optional) Per-session budget reset.** The `max_rolls` adjustment budget is
      whole-trade. Making it reset per session would suit multi-day expiries — but the
      stop-loss-owns-exit change largely covers the "don't bail early" concern, so this is
      low priority now.
- [ ] **(optional) Trailing take-profit.** Currently take-profit is a fixed % of max credit.
      A trailing lock (e.g. once past 60%, exit if it gives back to 40%) would capture more
      of a run.
- [ ] **Pool across expiries.** Every analytic (scoreboard, Horizon map, Attribution,
      Correlation) runs on ONE expiry at a time (a dropdown selects it; default = latest
      completed). To judge a signal's real edge you want it pooled across several completed
      expiries with a per-cell stability marker (sign consistency) — a "signal discovery"
      view over all history, not one contract. Biggest validation upgrade once ≥2–3 expiries
      of data exist.
- [ ] **(optional, data)** Backfill the empty `global_cues` / `realized_metrics` tables so
      `global_momentum` / `vrp` come off their fallbacks.

## Recently added (this session, on top of the "done" list below)

- **Single source of truth for index-volume** — extracted the identical
  `Σ index_weightᵢ × volumeᵢ` per-minute loop (the NIFTY index bar carries volume=0) into
  ONE canonical `signals/index_volume.py::per_bar_index_volume`, now imported by
  `technical_momentum`, `vwap`, `rel_volume` (`vol_index` is exempt — it weights per-stock
  RETURNS, a different computation). `vwap` / `rel_volume` outputs verified byte-identical.
  `technical_momentum` now falls back to it when the NIFTY bar has no volume (participation
  arm live instead of inert; reports `detail.vol_source` = "nifty_bar" / "constituents(N)").
- **`global_momentum` weight → 0.0** because there is no data for it (returns NO_DATA).
  Nuance: the score blend self-normalises over live signals, but `net_confidence` divides by
  the FIXED sum of ALL weights — so a dead signal left at non-zero weight silently deflates
  confidence and causes spurious NO_TRADEs. The six core weights now sum to 0.82 (fine —
  the blend normalises).
- **Cost-edge gate** ("do-nothing threshold", after Salov's Maximum Profit Strategy).
  `config.Gates.min_edge_cost_mult` (default 0.0 = OFF); `directional.decide()` (now taking
  optional `costs` / `lot_size`) flips ACT → STAND_ASIDE when the expected 1σ move (₹/lot) is
  below `mult ×` the structure's round-trip cost. `Decision` carries `edge_ratio` /
  `edge_cost_mult` / `cost_gated`. Exposed via `api.suggest` / `api.backtest`
  (`min_edge_cost_mult`) + a desk dropdown (Off / 1× / 1.5× / 2× / 3×). PRIOR / descriptive.
- **MPS0 "% of max profit" benchmark** in the backtest.
  `backtest/metrics.py::mps0_max_profit(prices, flip_cost_inr, lot_size)` — perfect-hindsight
  reversal ceiling via an O(n) two-state DP; `summarize()` optionally returns `mps0_max_rupees`
  + `capture_pct` (realised net ÷ ceiling). `walkforward.run` gains `mps_benchmark`
  (off / gross = zero-cost / net = charge avg round-trip); the path is sampled at the
  strategy's entry cadence (stride) so capture % is comparable. Exposed via `api.backtest`
  (`mps_benchmark`) + a desk dropdown; capture % shown as a chip. A ceiling / skill score,
  NOT a target — descriptive only.
- Volume signals `vwap` / `vol_index` / `rel_volume` — all now **index-weighted** (NIFTY is
  cap-weighted; a heavyweight's price/volume counts more) with weighted + unweighted variants
  reported. NIFTY index has no volume, so market volume is estimated from constituents.
- `breadth_oi` + the `breadth` feature — index-weighted advance/decline + `breadth_weighted`,
  reported alongside equal-weighted.
- Attribution view now has an **Expiry dropdown** (it previously defaulted silently to the
  latest completed expiry — "Session days" only trims within one expiry, it is NOT
  cross-expiry). Result header shows which expiry ran.
- Backfill uses the negative-caching BarCache with cross-asset symbols pre-loaded → no
  per-snapshot DB hits for `global_momentum` / macro signals.
- **Note on expiries:** the app defaults to the most recent *completed* expiry (e.g. 07-07).
  Select the live one (e.g. `2026-07-14T06:00:00.000Z`) in the dropdown to analyse its data;
  a still-live expiry ends in a provisional `MARK`, not `SETTLE`.

## What's already done (context)

- Signals: 6 core (weighted) + `vwap`, `vol_index`, `rel_volume`, `crude_energy`, `usdinr`,
  `global_gap`, `futures_basis`, `futures_calendar`, `futures_flow` (all weight 0.0,
  evaluate-first). Futures three read `NIFTY_FUT_1`/`NIFTY_FUT_2` from `price_bars` (NFO).
- Adjustment engine: convert-on-confirmed-trend, wing-level tilt/defend, HARVEST_WING,
  cooldown / persistence / max-adjustments gates, stop-loss-owns-exit, band-edge guard,
  take-profit, EM-aware condor placement (~1σ shorts), strided multi-session management,
  MARK-vs-SETTLE labelling. All tunable in the Desk Book UI.
- Analytics: scoreboard, single, attribution (+ Sharpe, signal→signal, VIX-regime),
  correlation matrix, Horizon map (+ VIX-regime filter + store fast path).
- Perf: scoped BarCache with negative-caching, cues-file memoisation, feature-store fast
  path. `bundle.evaluate` ~51ms→~6ms; Horizon map ~3.2s→~0.24s→~0.09s (off store).
- 26 tests pass (`python -m pytest strategy_framework/tests/ -q`).
