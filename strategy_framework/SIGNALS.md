# NIFTY signal reference — how each signal is estimated

> AUTO-GENERATED from `strategy_framework/signals/registry.py` (`python -m strategy_framework.gen_signal_docs`). Do not edit by hand — edit the `SignalSpec.method` in the registry and regenerate. The registry is the single source; this file is a view of it.

**31 signals** · 22 directional · 3 in the live blend.

Columns: **weight** = live blend weight (0 = studied candidate, not yet voting); **horizon** = intraday vs slow (daily); **data** = data_ready.

## Regime signals — *what kind of market is this?* (never vote direction)

### Choppiness index  ·  `choppiness`

weight 0 (candidate) · family `volatility` · kind `gate`

CI = 100·log10(ΣTR / (maxHigh−minLow)) / log10(n) on NIFTY 1m. High (≥61.8) = choppy/range, low (≤38.2) = trending. A REGIME read (trend↔chop), non-directional — a range-based second opinion to Kaufman ER for the regime engine.

*Detail fields:* `choppiness_index`, `chop`, `regime`

### OI dispersion  ·  `oi_dispersion`

weight 0 (candidate) · family `gamma` · kind `gate`

OI-weighted standard deviation of strikes (√dispersion, in points). Small = OI crowded into a strike (pin); large = smeared across the chain (loose). Emits a non-directional TIGHTNESS score 0..1. Complements the center of gravity: COG says WHERE the mass is, dispersion says how TIGHT.

*Detail fields:* `oi_std_pts`, `cog`, `tightness`, `regime`

### OI entropy  ·  `oi_entropy`

weight 0 (candidate) · family `gamma` · kind `gate`

Shannon entropy of the OI distribution / log(N), in [0,1]. Low = everyone crowded into one strike (pin); high = inventory distributed. Emits CROWDING = 1 − entropy. Cousin of dispersion (both measure concentration) — the audit decides if both earn a place. Your 'pin strength = PPI + entropy + dispersion' idea, learned not hardcoded.

*Detail fields:* `entropy_norm`, `crowding`, `n_strikes`, `regime`

### Pin pressure (gamma)  ·  `pin_pressure`

weight 0 (candidate) · family `gamma` · kind `gate`

Pin Pressure Index = (CallOI+PutOI at ATM)/ATM straddle → pin STRENGTH in [0,1] (blended with OI concentration at the pin strike, so it's scale-free). Answers 'how hard is it to escape this strike', NOT 'which way' — a regime, not a vote. The controller uses the strength to damp directional trust (strong pin → expect range/reversion). Direction comes from the Position/Confirmation signals.

*Detail fields:* `ppi`, `pin_strike`, `dist_to_pin`, `pin_share`, `atm_straddle`, `pin_strength`, `regime`

### Straddle compression / expansion  ·  `straddle_flow`

weight 0 (candidate) · family `volatility` · kind `gate`

ATM straddle S=C+P and its change: compression (S↓) = premium selling / pinning / range; expansion (S↑) = a move brewing. A GATE, not a direction — a straddle is symmetric and carries no bull/bear sign. Meant to modulate downstream trust (Layer-0 regime), and to become a regime_by='straddle' axis so its weight is earned from the conditional study, not assumed.

*Detail fields:* `atm_straddle`, `prev_straddle`, `change_pct`, `regime`, `note`

## Directional / position signals — *which way is the edge?*

### Heavyweight leadership  ·  `heavyweight_leadership`

weight **0.56** · family `leadership` · kind `directional`

Weighted tape: Σ wᵢ·rᵢ across the 50 constituents (free-float weight × return). Score = tanh(weighted_ret% / 0.6). Confidence rises with weight coverage, heavyweight volume surge, and breadth agreement.

*Detail fields:* `weighted_ret_pct`, `concentration`, `breadth`, `coverage_weight_pct`, `hv_vol_surge`, `n_constituents`

### Breadth & OI positioning  ·  `breadth_oi`

weight **0.44** · family `internals` · kind `directional`

Constituent advance/decline breadth + option OI walls: support = max put-OI strike below spot, resistance = max call-OI above. Lean from spot position in the band, wall reinforcement, and put/call OI ratio.

*Detail fields:* `breadth`, `oi`

### Breadth quality (% above trend)  ·  `breadth_quality`

weight 0 (candidate) · family `internals` · kind `directional`

% of the 50 constituents trading above their own EMA20, index-weighted — how BROAD the move is, not just up/down. Broad participation above trend = durable; narrow/heavyweight-led = fragile. New information from the constituents, distinct from advance/decline breadth. Score = 2·(pct_above − 0.5).

*Detail fields:* `pct_above_ema_weighted`, `pct_above_ema_equal`, `n_constituents`, `narrow_vs_broad`, `read`

### Crude / energy  ·  `crude_energy`

weight 0 (candidate) · family `macro` · kind `directional` · horizon `slow` · **data not ready** (pinned)

Crude terms-of-trade tilt. India imports the bulk of its crude, so a crude spike is an inflation and current-account shock: rising crude → bearish NIFTY, falling crude → bullish.

### Dealer center (ΔOI centroid)  ·  `dealer_center`

weight 0 (candidate) · family `internals` · kind `directional`

ΔOI-weighted strike centroid — where NEW risk is being added, vs oi_migration's standing-OI mass. center = Σ strike·(ΔOI_c⁺+ΔOI_p⁺)/ΣΔOI⁺. Score: centroid above spot = higher prices being accepted; plus put-vs-call writing aggression (fresh put writing at/below spot = bullish underwriting). The dynamic support/resistance read — the centroid migrates WITH repricing while standing OI still points at the old range.

*Detail fields:* `dealer_center`, `spot`, `offset_pts`, `put_add_share`, `fresh_oi_added`, `window`, `read`

### Liquidity de-risk  ·  `derisk_liquidity`

weight 0 (candidate) · family `overlay` · kind `overlay`

Coincident liquidity-driven de-risk detector (max-drawdown insurance trigger). A RISK OVERLAY, not a directional vote: it estimates the probability that the tape is in a broad, liquidity-driven decline.

### Pre-open de-risk  ·  `derisk_preopen`

weight 0 (candidate) · family `overlay` · kind `overlay`

LEADING liquidity-derisk warning read before the Indian open, so it can arm BEFORE the drawdown — the companion derisk_liquidity is coincident and arms only once the session is already falling.

### Event / earnings gate  ·  `earnings_events`

weight 0 (candidate) · family `gate` · kind `gate`

A veto and a structure hint, not a direction. Binary events (CPI, RBI, Fed, large-cap earnings) inflate premium and inject gap risk that intraday momentum cannot forecast.

### Futures basis  ·  `futures_basis`

weight 0 (candidate) · family `participation` · kind `directional` · horizon `slow`

NIFTY futures basis (future − spot) — the positioning / leverage read the cash-tape signals cannot see. A widening premium signals long build-up; a discount signals short build-up or hedging pressure.

### Futures term structure  ·  `futures_calendar`

weight 0 (candidate) · family `participation` · kind `directional` · horizon `slow`

Term structure / roll pressure across near and next expiry — deliberately kept separate from futures_basis so the horizon map and attribution can decide which (if either) earns a non-zero weight.

### Future Flow Score  ·  `futures_flow`

weight 0 (candidate) · family `participation` · kind `directional`

Score = PRICE RETURN × RELATIVE FUTURES VOLUME, specifically clamp( tanh(ret_pct/0.12) × clamp(0.4 + 0.6·rel_vol, 0.3, 1.6) ), where ret_pct is the 15-bar NIFTY_FUT_1 return and rel_vol = mean(volume, last 15) / mean(volume, prior 15). Direction comes ENTIRELY from price — the volume term is a positive multiplier that scales magnitude and can never flip the sign; it is neutral (1.0) at rel_vol = 1.0. Not a duplicate of technical_momentum / rel_volume because the NIFTY index carries no volume, so those must ESTIMATE participation whereas the future's volume is directly observed. NOTE: this carries NO open interest — true long-build-up / short-covering flow needs OI, which is not yet in the feed (fo_price_bars.open_interest exists in the schema but is unpopulated).

*Detail fields:* `fut_recent_ret_pct`, `fut_rel_volume`, `participation_boost`, `vol_source`, `thin_volume_move`

### Futures OI regime  ·  `futures_oi_regime`

weight 0 (candidate) · family `derivatives` · kind `directional`

Positioning read from futures price × open interest (same engine as the Macro Shock view, backend.quant.intraday_oi). LONG BUILDUP (price↑ OI↑) and SHORT BUILDUP (price↓ OI↑) = conviction; SHORT COVERING / LONG UNWINDING = hollow; heavy OI on flat price = COILED; flat = churn. Feed now active (NIFTY_FUT_1 1m OHLCV+OI). Used as the OI-regime conditioner / reliability overlay, not a directional vote — hence weight 0.

*Detail fields:* `regime`, `lean`, `conviction`, `read`, `d_price_pct`, `d_oi_pct`

### Overnight gap / risk-off  ·  `global_gap`

weight 0 (candidate) · family `macro` · kind `directional` · horizon `slow` · **data not ready** (pinned)

The only signal that reads ACROSS sessions. Every other signal reads the intraday tape and is therefore blind to a move that happens overnight; this one reads the global risk-off state that produces the cash-index gap at the open.

### Global momentum / forex  ·  `global_momentum`

weight 0 (candidate) · family `macro` · kind `directional` · **data not ready** (pinned)

Cross-asset tilt: metals barometer (copper − gold), USDINR inverse (rupee weak → bearish), and overnight/session index drift. Prefers the live global-cues cache when fresh, else 1m bars.

*Detail fields:* `risk_appetite`, `broad_fii`, `metals_score`, `copper_pct`, `gold_pct`, `usdinr_pct`, `nifty_drift_pct`, `n_streams`, `source`

### Heavyweight leadership (persistent)  ·  `heavyweight_leadership_persistent`

weight 0 (candidate) · family `leadership` · kind `directional`

The signal-to-noise version of heavyweight leadership. Scores the t-statistic z = mean/(vol/√n) of the free-float-weighted per-bar constituent leadership over the window — sustained, low-noise leadership reads decisive; choppy leadership that averages ~0 reads NEUTRAL, so it stops flipping on minute noise. Same normalisation technical_momentum uses; the raw heavyweight_leadership lacks it. Confidence folds in consistency (fraction of bars agreeing with the mean) and cap-weighted breadth (how many heavyweights lead the same way). Candidate at weight 0 — validate side-by-side with the raw signal.

*Detail fields:* `z_tstat`, `mean_ret_pct`, `vol_ret_pct`, `consistency`, `breadth_weighted`, `n_bars`, `n_constituents`

### OI center-of-gravity migration  ·  `oi_migration`

weight 0 (candidate) · family `internals` · kind `directional`

OI-weighted mean strike (center of gravity) for calls and puts, and its movement vs the prior snapshot. Both centers drifting up = support/resistance rising = bullish; both down = bearish. Stronger than any single strike's OI. Confidence rises when the two sides agree.

*Detail fields:* `cog_call`, `cog_put`, `d_cog_call`, `d_cog_put`, `migration_pts`, `sides_agree`, `read`

### Skew / RND  ·  `skew_rnd`

weight 0 (candidate) · family `derivatives` · kind `directional` · horizon `slow`

Risk-neutral drift: RND mean vs spot (Breeden-Litzenberger via backend/quant/rnd.py when scipy present) + 25Δ risk-reversal. Falls back to a premium-based OTM put-vs-call proxy when IV is missing.

*Detail fields:* `engine`, `rnd`, `skew_proxy`

### Strike role change  ·  `strike_role_change`

weight 0 (candidate) · family `internals` · kind `directional`

Tracks the OI walls' EVOLUTION, not just their level. Resistance = biggest call-OI strike above spot, support = biggest put-OI below. Using ΔOI reconstructed from levels across captures (the oi_chg columns are empty), it flags role flips: resistance call OI unwinding + support put OI building → level turning from resistance into support (bullish); the mirror is bearish. Growth rates are relative to each wall's own OI so big and small walls read alike.

*Detail fields:* `resistance_strike`, `support_strike`, `resist_call_growth_pct`, `support_put_growth_pct`, `put_build_at_resistance_pct`, `read`, `window`

### Session phase  ·  `time_of_day`

weight 0 (candidate) · family `gate` · kind `gate`

Intraday session-phase modulator (IST). The tape is not stationary across the day: the 09:15-09:45 opening drive carries the largest directional bursts, midday chops, power hour trends again. Amplifies or damps momentum confidence.

### USDINR / rupee  ·  `usdinr`

weight 0 (candidate) · family `macro` · kind `directional` · horizon `slow`

Rupee as the fast proxy for foreign-flow direction and risk appetite: rupee weakness (USDINR up) → risk-off / FII outflow pressure → bearish NIFTY; rupee strength → inflow-supportive → bullish.

### Variance risk premium  ·  `vrp`

weight 0 (candidate) · family `volatility` · kind `directional` · horizon `slow`

VRP ratio = implied vol / realized vol. RV = annualised 1m close-to-close; implied from ATM IV or India VIX. Ratio ≥1.15 RICH (sell premium), ≤0.95 CHEAP (buy). Mostly a structure modulator.

*Detail fields:* `rv_ann_pct`, `implied_pct`, `vrp_ratio`, `regime`, `implied_source`

## Confirmation / execution signals — *is the move being accepted?*

### ADX / DMI trend strength  ·  `adx`

weight 0 (candidate) · family `trend` · kind `directional`

Wilder's DMI on NIFTY 1m. +DI vs −DI gives direction, ADX gives trend STRENGTH. Score = (+DI−−DI)/(+DI+−DI); confidence scales with ADX so a directional read is trusted only when a real trend exists (ADX>25). New maths (directional movement + ATR), distinct from EMA momentum and Kaufman ER.

*Detail fields:* `plus_di`, `minus_di`, `adx`, `read`

### Relative volume  ·  `rel_volume`

weight 0 (candidate) · family `participation` · kind `directional`

Score = clamp( tanh(r_NIFTY/0.12) × clamp(0.4 + 0.6·RV, 0.3, 1.6) ), where r_NIFTY is the 15-bar NIFTY index return in PERCENT and RV is the INDEX-WEIGHTED relative volume RV = Σᵢwᵢ·mean(volᵢ, last 15) / Σᵢwᵢ·mean(volᵢ, prior 15), reconstructed from the constituents because the index carries no volume of its own (shared index_volume.per_bar_index_volume). A heavyweight's volume surge therefore counts more than a small-weight name's. Direction comes ENTIRELY from the index price; the volume term is a positive multiplier, neutral (1.0) at RV = 1.0, that can never flip the sign. The unweighted ratio is reported in detail for divergence but does not feed the score.

*Detail fields:* `recent_ret_pct`, `rel_volume_weighted`, `rel_volume_unweighted`, `participation_boost`, `vol_source`

### Technical momentum  ·  `technical_momentum`

weight 0 (candidate) · family `trend` · kind `directional`

NIFTY 1m tape. trend_z = (EMA9−EMA21)/ATR; thrust_z = windowed log-return / its vol; vol_ratio = recent vs prior volume. Score = 0.6·tanh(trend)+0.4·tanh(thrust), scaled by participation.

*Detail fields:* `trend_z`, `thrust_z`, `vol_ratio`, `ema_fast`, `ema_slow`, `atr_1m`, `n_bars`

### Volume-weighted momentum  ·  `vol_index`

weight 0 (candidate) · family `leadership` · kind `directional`

Constituent volume × index-weighted momentum — 'where the heavyweight money moves'. NIFTY being free-float cap weighted, a move in a 10%-weight name swings the index far more than the same move in a 0.4% name. Weights per-stock RETURNS (not volume).

### VWAP position  ·  `vwap`

weight 0 (candidate) · family `trend` · kind `directional`

Session VWAP — the volume-weighted mean price since the open, the reference institutions trade around. Reads where spot sits relative to it. Index volume is reconstructed as Σ index_weightᵢ × volumeᵢ (signals/index_volume.py).
