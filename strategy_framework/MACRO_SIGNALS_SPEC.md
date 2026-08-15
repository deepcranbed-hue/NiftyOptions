# Macro / Risk-Off Signal Spec — NIFTY Options Framework

> **STATUS (implemented):** Steps 1 & 2 are **done** — with a simplification.
> The crude/USDINR/GIFT series are **already ingested into `price_bars`** (by
> `scratch_scripts/sync_all_commodities.py` + `download_gift_nifty.py`, symbols
> `CRUDEOIL` / `USDINR` / `GIFTNIFTY`), so the proposed new `global_cues` table
> in §1 turned out **unnecessary** — the signals read `price_bars` directly, the
> same way NIFTY bars are read. The three signals `crude_energy`, `usdinr`,
> `global_gap` (§2) are built, wired into `bundle.evaluate`, stored as
> `sig_*_score`, and visible in every Signal Test view — but ship at **weight
> 0.0** (evaluated, not yet trusted in the trade blend). **Step 3 (VIX-regime
> condition) is also done** — `vix_regime` is a feature-store column, an
> Attribution condition, and a Horizon-map filter. **Remaining: Step 4** —
> backfill on the real Drive DB, then raise weights for whichever signals prove
> edge. §1's table spec is retained below only as the reference for fields the
> signals expect; you do not need to build it.

**Purpose:** make the framework able to see the kind of move that hit on 8 Jul 2026 (crude +5–8% on the US–Iran shock → rupee to 95.55 → India VIX +~30% → 2% NIFTY fall) and reverse on 9 Jul.

**Root cause of the current blind spot:** the move was macro/overnight, but `global_cues`, `minute_bars`, and `realized_metrics` are empty, so the two signals that *should* have caught it — **Global momentum / forex** and **Variance risk premium** — are running on fallbacks. Fix data first, then add three signals, then add a VIX regime filter.

**Honest scope:** no signal predicts a Trump statement or a Hormuz strike. The goal is to (a) react fast and correctly once oil/rupee/VIX move, and (b) know which signals to trust in a risk-off regime — not to "predict the crash."

---

## Order of work

1. ~~**Populate `global_cues`**~~ → **superseded.** The data is already in `price_bars` (`CRUDEOIL` / `USDINR` / `GIFTNIFTY`); the signals read it there. No new table needed. (`realized_metrics` is still empty and would improve `vrp`, but that's independent of these signals.)
2. ✅ **Add 3 signals**: `crude_energy`, `usdinr`, `global_gap` — **done** (`signals/*.py`, registered in `bundle.evaluate`, weight `0.0` in `config/settings.py`, in `_DIR_SIGNAL_NAMES`, friendly names in the UI, verified on a synthetic fixture: crude↑→−0.80, USDINR↑→−0.95, GIFT>spot→+0.70; 21/21 tests pass).
3. ✅ **Add VIX regime** — **done.** `vix_regime` (calm <13 / normal 13–16 / elevated 16–20 / stressed >20) is written to every feature-store row (`features/extractor.py::vix_regime`), selectable as a **Condition** in Attribution, and available as a **filter on the Horizon map** (`signal_effectiveness(vix_regime=…)`). Lets you ask "which signals survive elevated/stressed VIX."
4. ⬜ **Backfill** on the real Drive DB so `sig_*_score` + `vix_regime` populate with genuine values, then **raise the weights** in `config/settings.py` for whichever signals earn it in the Horizon map / Attribution.

---

## 1. `global_cues` table

One row per capture timestamp, aligned to the same `ts` grid as your option-chain snapshots so it joins cleanly into the feature store.

| column | type | meaning |
|---|---|---|
| `ts` | TIMESTAMP (PK) | capture time, aligned to snapshot grid |
| `brent` | REAL | Brent front-month, USD/bbl |
| `wti` | REAL | WTI front-month, USD/bbl |
| `brent_ret_30m` | REAL | Brent % change over trailing 30m |
| `brent_ret_1d` | REAL | Brent % change vs prior session close |
| `usdinr` | REAL | USDINR spot |
| `usdinr_ret_30m` | REAL | USDINR % change trailing 30m |
| `usdinr_ret_1d` | REAL | USDINR % change vs prior close |
| `india_vix` | REAL | India VIX level |
| `vix_chg_pct` | REAL | India VIX % change on the day |
| `gift_nifty` | REAL | GIFT Nifty last (NSE IX) |
| `gift_premium` | REAL | GIFT Nifty − last NIFTY spot (gap proxy) |
| `dxy` | REAL | US Dollar Index |
| `us_prev_close_ret` | REAL | S&P 500 (or Nasdaq) prior-session % change |
| `asia_risk` | REAL | optional: Nikkei/Hang Seng % change, session-to-date |
| `src_latency_s` | INT | staleness of the feed at capture (for confidence) |

**Notes**
- Keep raw levels *and* precomputed returns; signals read returns, dashboards read levels.
- `gift_premium` is your overnight-gap proxy during the pre-open / off-hours window — it's the only genuinely *forward-looking* field for the next session's open.
- `src_latency_s` feeds signal confidence — a stale macro feed should down-weight, not silently mislead.

### Data sources

| field(s) | source options | notes |
|---|---|---|
| `brent`, `wti` | market-data API (e.g. Yahoo `BZ=F`/`CL=F`, or your broker's commodity feed) | poll on the same cadence as your snapshot capture |
| `usdinr` | broker CDS segment (Kite) or an FX quote API | Kite gives USDINR futures; spot via FX API |
| `india_vix` | NSE / broker feed | you likely already ingest this for regime — surface it here |
| `gift_nifty`, `gift_premium` | NSE IX (GIFT) feed | key for pre-open gap; compute premium vs cached NIFTY spot |
| `dxy`, `us_prev_close_ret` | market-data API | daily-cadence fields; refresh at capture is fine |

Write a small `global_cues_fetcher` that runs on your existing capture schedule and upserts one row per `ts`. Respect the framework's no-lookahead rule: only store values known at or before `ts`.

---

## 2. New signals (drop into the SignalBundle pattern)

Each signal follows your existing shape: `compute(context) -> (score, confidence)`, score roughly in `[-1, +1]` (**+ = bullish NIFTY**, − = bearish), confidence in `[0, 1]`. Persisted as `sig_<name>_score` in the feature store. Sign convention below is chosen so all three read the same direction as your other signals.

### 2a. `crude_energy` → `sig_crude_score`
India is crude-import-driven, so **rising crude = bearish NIFTY**.

- **Inputs:** `brent_ret_30m`, `brent_ret_1d`, `brent` level.
- **Logic:**
  - `momentum = zscore(brent_ret_30m)` over a trailing window.
  - `score = -tanh(0.6*momentum + 0.4*zscore(brent_ret_1d))` — note the **minus**: crude up → score negative → bearish.
  - Optional level nudge: sustained Brent above a stress threshold (e.g. >$80) adds a small persistent bearish tilt.
- **Confidence:** rises with |momentum| and freshness (`src_latency_s` low); falls when crude is flat or the feed is stale.
- **Expected horizon:** medium (1h–EOD–next-day); crude repricing bleeds into NIFTY over hours, not minutes — the Horizon map will confirm.

### 2b. `usdinr` → `sig_usdinr_score`
**Rupee depreciation (USDINR up) = risk-off / bearish.**

- **Inputs:** `usdinr_ret_30m`, `usdinr_ret_1d`.
- **Logic:** `score = -tanh(0.6*zscore(usdinr_ret_30m) + 0.4*zscore(usdinr_ret_1d))` — minus so INR weakness → bearish.
- **Confidence:** from move magnitude + freshness.
- **Expected horizon:** short-to-medium; often co-moves with crude, so watch its correlation vs `crude_energy` in the Correlation view (likely overlapping — don't double-count when combining).

### 2c. `global_gap` → `sig_gap_score`
The 8 Jul move hit largely **overnight**; your other signals are intraday-tape based and can't see a gap forming. This one predicts the *next session's* direction.

- **Inputs:** `gift_premium`, `us_prev_close_ret`, `dxy` change, `vix_chg_pct`, optional `asia_risk`.
- **Logic:** weighted blend, e.g.
  `score = tanh(0.45*zscore(gift_premium) + 0.30*zscore(us_prev_close_ret) - 0.15*zscore(dxy_chg) - 0.10*zscore(vix_chg_pct))`
  (GIFT premium and US up → bullish; stronger dollar and rising VIX → bearish.)
- **Confidence:** highest pre-open when GIFT is live and inputs agree; low intraday once the gap is already in the cash index (it decays after open).
- **Expected horizon:** open / EOD / **next-day** specifically — this is the one signal that should light up the next-day column of the Horizon map.

---

## 3. India VIX as a **regime condition** (higher value than any single signal)

You already bucket Attribution by DTE. Add a VIX regime bucket so Attribution and the Horizon map can show *which signals survive a risk-off regime like 8 Jul.*

- Add `vix_regime` to each feature-store row from `india_vix`:
  - `calm` (<13), `normal` (13–16), `elevated` (16–20), `stressed` (>20).
- Expose `vix_regime` as a **Condition** option in Attribution (alongside DTE) and as a filter on the Horizon map.
- **Why:** yesterday's regime flipped from calm to elevated in one session. The value is discovering, e.g., "technical_momentum works in normal but inverts when stressed," or "skew_rnd only pays in elevated+." That mapping is what tells you when to trust what — more useful than adding a fifth reactive signal.

---

## 4. Wiring checklist

1. Create `global_cues` + fetcher; backfill what history you can (crude/USDINR/VIX are retrievable historically; GIFT premium only prospectively).
2. Register `crude_energy`, `usdinr`, `global_gap` in the SignalBundle with friendly names ("Crude / energy", "USDINR / rupee", "Overnight gap / global risk-off") so they appear in the Predictor/Target dropdowns.
3. Run the feature-store backfill so `sig_crude_score`, `sig_usdinr_score`, `sig_gap_score`, and `vix_regime` populate for historical captures.
4. Add `vix_regime` to the Attribution condition list and Horizon-map filter.
5. Validate: run the Horizon map filtered to `vix_regime = elevated/stressed` and confirm `global_gap` shows signal at next-day, `crude_energy`/`usdinr` at 1h–EOD.

---

## Step 4 — run on real data (handoff checklist)

Everything above is built; this is the part that runs **on your machine**, against
the Google-Drive `option_chains.db` that actually has the `CRUDEOIL` / `USDINR` /
`GIFTNIFTY` bars and the real VIX (the sandbox can't reach that file).

**A. Confirm the data is present.** In the DB the app uses:
```sql
SELECT symbol, COUNT(*), MIN(ts), MAX(ts)
FROM price_bars WHERE symbol IN ('CRUDEOIL','USDINR','GIFTNIFTY')
GROUP BY symbol;
```
If a row is missing, run the syncers first:
`python scratch_scripts/sync_all_commodities.py` and
`python scratch_scripts/download_gift_nifty.py`.

**B. Point the framework at that DB.** Either it resolves automatically (the Drive
path is the default when it exists) or set it explicitly:
`export NIFTY_DB="/Users/deepak/Library/CloudStorage/GoogleDrive-…/My Drive/option_chains.db"`.

**C. Rebuild the feature store** (so `sig_crude_energy_score`, `sig_usdinr_score`,
`sig_global_gap_score`, and `vix_regime` populate with genuine values). In the
Signal Test → Attribution tab tick **"rebuild all"** and click **Backfill
features**, or from Python:
`from strategy_framework import api; api.features_backfill(force=True)`.

**D. Validate edge before trusting.** In **Horizon map**: colour by IC, then by
Sharpe; filter **VIX regime = elevated/stressed**. Expect (spec hypothesis, not
guaranteed): `global_gap` strongest at **next-day**, `crude_energy` / `usdinr` at
**1h–EOD**. In **Attribution**: predictor = each macro signal, target =
`fwd_ret_60m_pct` / `fwd_ret_eod_pct`, condition = `vix_regime`. Also open
**Correlation** — `crude_energy` and `usdinr` will likely be highly correlated;
plan to treat them as one macro factor.

**E. Only then raise the weights.** For each signal that shows real, stable edge,
bump its weight in `config/settings.py` (`SignalWeights.crude_energy` /
`.usdinr` / `.global_gap`) from `0.0`, and **also add its name to
`strategy/regime.py::_DIRECTIONAL`** so it enters the trade blend (a non-zero
weight alone does nothing until it's in that list — that's the deliberate safety
interlock). Re-normalise so the weights still sum to ~1.0. Then re-run the
backtest to confirm the blend improved, not just changed.

**F. Pool across expiries before committing.** One expiry stays descriptive
(D-MA-04). Re-check the Horizon map / Attribution once you have ≥2–3 completed
expiries with the macro data before locking any weight.

---

## 5. Caveats

- **Single-expiry history** — every number stays descriptive until pooled across expiries. Prioritize the expiry-averaged grid with a stability marker before trusting any new signal's edge.
- **Correlation** — `crude_energy` and `usdinr` will likely overlap in risk-off; treat them as one macro factor when combining, not two independent bets.
- **Predict vs react** — only `global_gap` is forward-looking across sessions. The others react fast; they improve *response*, not foresight. That's the correct expectation for exogenous shocks.
- **Data > speed** — per your architecture doc, at current scale don't optimize compute. Filling `global_cues` / `realized_metrics` is the real lever.

---

## 6. Futures signals (NIFTY_FUT_1 / NIFTY_FUT_2)

Added once the two futures series (near = 30-Jul, far = 27-Aug, plus GIFTNIFTY) were captured into `price_bars` on exchange `NFO`. Schema is **OHLCV with no open interest** — so these are basis / term-structure / real-volume signals; there is no OI-based long/short-buildup or rollover. All three ship at **weight 0.0** (evaluate-before-you-trust) and auto-emit `sig_*_score`.

Why they're not covered by existing signals: `heavyweight_leadership` and `technical_momentum` read the *cash* tape; `breadth_oi` uses *option* OI (not futures); and `vwap`/`vol_index`/`rel_volume` **estimate** index volume from constituents because the cash index has none. The futures carry the actual positioning and the only true NIFTY-level traded volume.

### 6a. `futures_basis` → `sig_futures_basis_score`
- **Inputs:** `NIFTY_FUT_1` close, `NIFTY` spot; `NIFTY_FUT_2` for a small calendar tilt.
- **Logic:** basis = near − spot (points/%); score from basis vs its own trailing norm (z) + ~30m premium trend + a light calendar term. **Discount override:** future below spot forces a bearish score.
- **Sign:** premium expanding = bullish; discount = bearish (the hedging/panic tell, e.g. an 8-Jul-type selloff).
- **Horizon:** basis moves fast — expect short-to-medium.

### 6b. `futures_calendar` → `sig_futures_calendar_score`
- **Inputs:** `NIFTY_FUT_2` − `NIFTY_FUT_1` (term structure).
- **Logic:** spread vs its norm (z) + steepening/flattening trend; **backwardation override** (far < near) forces bearish. Roll pressure = far-vs-near **volume share** — confidence/context only, never direction.
- **Sign:** steepening contango = bullish; backwardation = bearish.
- **Overlap note:** shares the calendar sub-term inside `futures_basis` — don't double-count if both ever earn weight.

### 6c. `futures_flow` → `sig_futures_flow_score`
- **Inputs:** `NIFTY_FUT_1` price + its **REAL traded volume**.
- **Logic:** recent futures return × participation boost from real volume (recent window vs prior baseline); a price move on shrinking volume is flagged `thin_volume_move` and its confidence is cut.
- **Sign:** up on rising real volume = bullish conviction; + = up.
- **Overlap note:** *direction* correlates with `technical_momentum`; its unique contribution is genuine volume confirmation, not another momentum vote.

### Wiring / activation
1. Registered in `signals/bundle.py`, `config/settings.py` (weight 0.0, in `as_dict`), and the Signal Test list `api.py::_DIR_SIGNAL_NAMES`. `NIFTY_FUT_1/2` added to `CROSS_ASSET_SYMBOLS` so the analytics/backfill cache preloads them.
2. **Restart uvicorn** (code) → **force-rebuild the feature store** (`features_backfill(force=True)` — incremental skips existing rows, so a normal backfill will NOT add the new columns).
3. They populate only on an expiry whose snapshots overlap the period where the futures data actually exists (needs ≥3 varying points for IC/correlation).
