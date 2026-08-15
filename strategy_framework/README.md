# Directional-Momentum Strategy Framework

A self-contained framework that turns your minute-level signals into a **directional
options strategy suggestion on the NIFTY index**, and **walk-forward backtests** it —
without lookahead and without over-claiming edge on thin history.

It is an orchestration layer over the engines you already have (skew, RND, global
cues, flows, OI). It lives entirely in `strategy_framework/` and touches nothing else.

## What it does

1. **Reads every signal as-of a decision time** (backward join, D-MA-01 — no peeking
   at future bars or snapshots).
2. **Blends the signals** into a directional score + confidence, amplified in the
   high-momentum time-of-day windows.
3. **Classifies the regime** — TREND (directional) vs RANGE (premium harvest) vs
   NO_TRADE — instead of only "act or stand aside".
4. **Chooses an options structure** for the regime: directional spreads / long
   options in a trend; **iron condor / iron butterfly** in a range; nothing when
   there's no edge or an event looms.
5. **Backtests** it walk-forward and reports honest, clearly-caveated metrics.

## Signals (directional ones emit score ∈ [-1,+1], + = bullish NIFTY)

| Signal | Weight | What it reads |
|---|---|---|
| `heavyweight_leadership` | 0.28 | **all 50 constituents** weighted by free-float index weight → the ground-truth tape; detects when a volume-backed heavyweight (Reliance, HDFC Bank, ICICI…) or a sector is *leading* the index up/down. Reports concentration, breadth, sector tilt |
| `technical_momentum` | 0.24 | NIFTY 1m EMA trend + windowed thrust + **volume-building** participation. When the NIFTY bar has no volume it falls back to the constituent-reconstructed index volume (`per_bar_index_volume`) so the participation arm is live, not silently inert; reports `detail.vol_source` ("nifty_bar" / "constituents(N)") |
| `global_momentum` | 0.0‡ | metals barometer (copper vs gold), USDINR forex-flow tilt, overnight/session index drift; prefers live `global_cues_cache.json` |
| `breadth_oi` | 0.22 | constituent advance/decline breadth (**index-weighted** + unweighted, score uses weighted) + put/call **OI walls** (support/resistance) and their reinforcement |
| `skew_rnd` | 0.16 | RND mean-drift vs spot + risk-reversal skew (uses `backend/quant/rnd.py` if scipy present, else a premium proxy) |
| `vrp` | 0.10 | IV-vs-realized-vol regime — mostly a **structure** modulator (rich → sell premium, cheap → buy) |
| `vwap` | 0.0† | spot vs **session VWAP** (resets 09:15 IST) + slope; above = buyers in control = bullish. Index has no volume → per-minute volume **estimated from constituents, index-weighted** (Σ wᵢ·volᵢ); reports weighted + unweighted VWAP; TWAP only if no volume at all |
| `vol_index` | 0.0† | **index-weight × volume** momentum `Σ(wᵢ·volᵢ·retᵢ)/Σ(wᵢ·volᵢ)` (heavyweights trading heavily) — reports index-weighted, volume-weighted (unweighted) and their divergence |
| `rel_volume` | 0.0† | NIFTY direction **scaled by relative volume** — index has no volume, so estimated from constituents, **index-weighted** (a surge in a heavyweight counts more) + unweighted; heavy volume = conviction |
| `crude_energy` | 0.0† | `CRUDEOIL` 1m from `price_bars`; India imports oil so **crude up = bearish** (30m thrust + session carry) |
| `usdinr` | 0.0† | `USDINR` 1m; **rupee weak (USDINR up) = risk-off / bearish**. Overlaps `global_momentum` — treat as one macro factor |
| `global_gap` | 0.0† | `GIFTNIFTY` vs NIFTY spot; the **overnight-gap / forward** read (GIFT above spot ⇒ higher open); confidence decays after open |
| `futures_basis` | 0.0† | `NIFTY_FUT_1` − spot; **premium expanding = bullish, discount = bearish** (+ ~30m trend). The positioning/leverage read the cash tape can't see — a discount is the classic hedging/panic tell |
| `futures_calendar` | 0.0† | `NIFTY_FUT_2` − `NIFTY_FUT_1` term structure; **steepening contango = bullish, backwardation = bearish**. Roll pressure is a **volume** proxy (no OI) → confidence only |
| `futures_flow` | 0.0† | `NIFTY_FUT_1` price × its **REAL traded volume** — the only true NIFTY-level volume (cash index has none, so `rel_volume` only estimates it). Move on rising volume = conviction; thin-volume move flagged |
| `time_of_day` | modulator | IST session phase. Opening drive (09:15–09:45) & power hour (14:45–15:30) **amplify** momentum confidence & expected move; **expiry-close** flags pin/gamma risk |
| `earnings_events` | gate | event/earnings **veto** within the window; not directional |

Weights, gates and constituent weights live in `config/` and are all tagged **PRIOR** —
judgement priors, not fitted parameters, until ≥60 sessions of history exist (D-MA-04).

† The nine extra signals (`vwap`, `vol_index`, `rel_volume`, `crude_energy`, `usdinr`,
`global_gap`, `futures_basis`, `futures_calendar`, `futures_flow`) ship at weight **0.0**:
they are computed, stored (`sig_*_score`) and
fully visible in the Signal Test views (scoreboard, Horizon map, Attribution,
Correlation), but do **not** move the trade blend yet. Validate their edge first, then
raise the weights in `config/settings.py` — "evaluate before you trust." The macro three
read `CRUDEOIL` / `USDINR` / `GIFTNIFTY` straight from `price_bars` (the commodity/FX
sync writes them there, so no separate `global_cues` table is needed); the volume three
read NIFTY / constituent bars. All return NO_DATA cleanly when their inputs are absent.
See `MACRO_SIGNALS_SPEC.md`.

‡ `global_momentum` is set to weight **0.0** because there is no data for it in the
current DB (it returns NO_DATA). Zeroing a *dead* signal matters beyond the score:
the score blend **self-normalises over the live signals**, so a NO_DATA signal's
weight is auto-redistributed for the SCORE — but `net_confidence` divides by the
**fixed sum of ALL weights**, so leaving a dead signal at a non-zero weight silently
deflates confidence and causes spurious NO_TRADEs. Its 0.18 was then **deliberately
redistributed** to the live signals (not proportionally — a proportional split is a
no-op; the biggest share went to the independent diversifiers `breadth_oi` +0.07 and
`skew_rnd` +0.04, with smaller bumps to leadership/trend/vrp), so the five active
weights again sum to **1.0**. All PRIOR — revisit with `signal_correlation` /
`signal_effectiveness` before locking, and reclaim weight for `futures_flow` once its
futures-volume data and edge are confirmed.

## Regime → structure logic

```
net_score, confidence  =  Σ weightᵢ · (confidenceᵢ · tod_multiplierᵢ) · scoreᵢ
tod_multiplier: OPENING_DRIVE ×1.30, POWER_HOUR ×1.20, EXPIRY_CLOSE ×1.35, MIDDAY ×0.80

event veto within window                         → NO_TRADE  (stand aside)

TREND   : |net_score| ≥ 0.15 AND confidence ≥ 0.35 AND
          (breadth agrees  OR  a heavyweight leads on ≥1.2× volume, conc ≥ 0.6)
   bullish + premium RICH  → bull_put_spread        bearish + RICH  → bear_call_spread
   bullish + premium CHEAP → long_call (if strong)  bearish + CHEAP → long_put
   bullish + fair          → bull_call_spread        bearish + fair → bear_put_spread

RANGE   : weak direction (|net_score| < 0.15) AND premium not cheap AND
          offsetting breadth (|breadth| < 0.34)
   very tight expected move / pin → iron_butterfly   (short ATM straddle + wings)
   otherwise                      → iron_condor      (short OTM put & call spreads;
                                                      shorts placed ~1σ = expected-move
                                                      OTM, `condor_short_em_mult`; an OI
                                                      wall is used only if it sits within
                                                      ±`condor_wall_tol` of that target —
                                                      so shorts aren't left near-ATM at
                                                      longer DTE)
   …but an EXPIRY_CLOSE pin overrides RANGE → stand aside (no short gamma into the print)

NO_TRADE: no directional edge and no clean range signature
```

Legs use the project convention `(side, strike, sign)` so structures are compatible
with `strategy_compare.py` / `portfolio.py`. Payoff math is pure numpy, and degenerate
structures (legs collapsing to one strike when spot is outside the band) are rejected.

## Transaction costs

Every option leg is charged **₹20 per transaction** (`config.CostModel.per_leg_inr`) —
once when opened, once when closed, and once each time an adjustment touches it. So a
4-leg iron condor costs ₹80 to open and ₹80 to close (₹160 round-trip); each roll that
touches N legs adds ₹20·N. All backtest P&L is reported **net of these costs in rupees**,
and `pnl_pts` is the net-of-cost points figure the metrics use. Optional per-leg slippage
(in points) can be added too.

## Cost-edge gate (do-nothing threshold)

After Salov's *Maximum Profit Strategy*: don't trade when the edge can't clear the
friction. `config.Gates.min_edge_cost_mult` (default **0.0 = OFF**) is a "do-nothing
threshold" — when on, `strategy/directional.decide()` (now taking optional `costs` /
`lot_size`) flips a would-be trade from **ACT → STAND_ASIDE** if the expected 1σ move
(₹/lot) is below `mult ×` the structure's round-trip cost. The `Decision` carries
`edge_ratio`, `edge_cost_mult`, `cost_gated`. Exposed via `api.suggest` / `api.backtest`
(param `min_edge_cost_mult`) and a **Cost-edge gate** dropdown in the desk (Off / 1× /
1.5× / 2× / 3×). PRIOR / descriptive.

## Position management (adjustment engine)

Rather than always flat-closing, once a structure is on the framework **manages it like a
desk, not a strike-follower** (`strategy/adjustment.py`, backtest `--exit manage`). The
guiding principle: a strong *confirmed* trend means the range thesis is dead, so **stop
defending and convert/exit** instead of chasing the move with the whole condor.

```
HOLD                 : thesis intact / short strikes safe
ROLL_UNTESTED_TOWARD : moderate lean, only NEAR — roll the untested (winning) wing toward
                       spot, collect premium + add delta; the tested wing is untouched
DEFEND_TESTED        : the tested wing is breached — roll it out/wider for room (+ tilt)
CONVERT_TO_VERTICAL  : strong CONFIRMED trend — drop the tested (losing) wing, keep the
                       untested (winning) wing as a directional credit spread riding the
                       trend (condor → bull-put / bear-call spread). The key anti-gamma-chase fix
HARVEST_WING         : (opt-in) in a trend, roll the over-safe wing toward spot to collect
                       fresh premium — only if net premium clears the ₹/leg cost
RECENTER             : range persists but spot drifted → re-establish on the new spot
EXIT                 : adjustment budget spent AND no stop-loss set — flatten
CLOSE                : expiry-close pin, or thesis broken
```

**Discipline gates** (so it doesn't gamma-chase; all tunable per run in the UI or
`adjustment.py` constants):

- **Cooldown** (`COOLDOWN_MIN`, 15m) — no re-adjust within the window unless truly breached.
- **Persistence** (`PERSIST_NEAR`, 1) — a lone "near" waits for confirmation before acting.
- **Max adjustments** (`MAX_ROLLS`, 2) — the budget is a **fee/anti-chase** limit, not a risk
  limit: once spent it stops *adjusting* but keeps *holding* if a **stop-loss** is active
  (the stop owns the exit); only `EXIT`s when no stop is set.
- **Band-edge guard** — a roll that would collapse a spread to one strike is skipped.

**Exits are bracketed** by two orthogonal triggers, checked across every session
(management is strided over the whole window, not just day 1): **take-profit** books the
gain at a configurable % of max credit; **stop-loss** caps the loss at a ₹ level. On a
still-live expiry the final row is labelled a provisional **MARK** (not SETTLE), since
there's no settlement data yet.

Adjustments are leg deltas (`close_legs` / `open_legs`) so the backtest realises P&L
leg-by-leg and charges ₹20 on exactly the legs touched.

## Forecast-driven optimizer (model predicts, optimizer chooses)

The rule engine above answers *"which rule applies?"*. A parallel **optimizer** answers
*"given my forecast, which action has the best risk-adjusted outcome?"* — kept as two
separate problems (the model only predicts `{expected_move, confidence, σ}`; the optimizer
enumerates actions and scores them). Both use `risk_forecast.py` to integrate each
resulting position's payoff against the terminal-spot distribution N(spot+drift, σ) and
pick the highest **tail-aware** score:

```
score = E[P&L] − λ · |CVaR10|          (λ default 0.5)
```

- **Options** (`action_eval.py`): scores {HOLD, DEFEND_PUT, DEFEND_CALL, HARVEST_WING,
  CLOSE}. HOLD scored absolutely (not 0); an action must beat HOLD by `min_edge`. Harvest is
  **state-aware** — cumulative `harvest_debt_pts` carries a soft penalty + optional hard
  budget, so the optimizer sees the multi-step over-harvesting a one-step score misses. The
  **A/B/C/D experiment** (Always / Never / Optimizer-gated / Optimizer+budget) confirmed
  always-harvest is worse on net + drawdown.
- **Futures** (`futures_action_eval.py`): scores {HOLD, EXIT, ADD, REDUCE, REVERSE} for a
  signed linear position (`q·(S_T−S₀)·lot`). Honest finding: a lone NIFTY future's tail
  (σ≈60pts×65) dwarfs its edge, so at λ=0.5 it prefers **flat** unless conviction is high —
  it ADDs to a strong-up forecast only at λ≲0.3 or a shorter horizon (this is the objective
  working; tune with `λ` and `risk_drift_frac`). Runs **advisory-only** in the backtest
  (logs would-do + a shadow would-be equity; recorded P&L stays the plain 1-lot path) until
  validated. Point-in-time table via `/api/strategy/futures-action`.

## Drawdown insurance & macro overlays

- `derisk_liquidity.py` — coincident liquidity-derisk intensity (max-drawdown-insurance trigger).
- `derisk_preopen.py` — LEADING pre-open detector (GIFT gap + overnight crude/USD) → ARMED / CLEAR.
- tail hedge (`constructor.build_tail_hedge`) — long OTM put sized by intensity + put-spread reference.
- macro-shock cause & effect (`/api/macro-shock`) — cross-asset roles, transmission chain,
  sector expected-vs-observed, and shock-timing (overnight-gap vs intraday) with a pre-open verdict.

## Desk Book (mixed-instrument portfolio)

`portfolio/book.py` holds option strategies + **futures + stocks** in one book (each with
`exchange` + `expiry`; options priced from the chain at add time). Add by hand or push a
strike-adjusted structure from the Directional Suggester ("Add to Desk Book"), then pick one
position to backtest (`/api/strategy/book/backtest`, routes by kind). The DB's two real
futures series **`NIFTY_FUT_1`** (near, exp 2026-07-30) / **`NIFTY_FUT_2`** (next, 2026-08-27)
resolve MONTHLY last-Thursday expiries by rank; a future backtest walks the series' own 1m
bars. Dropdowns are data-driven from `instruments_meta()`. **Lot size = 65** (revised from 75,
effective 1-Jan-2026).

## Usage

```bash
# list expiries present in the DB
python strategy_framework/run_demo.py expiries

# one suggestion at the latest snapshot for the latest expiry
python strategy_framework/run_demo.py suggest
python strategy_framework/run_demo.py suggest --expiry 2026-07-14T06:00:00.000Z --now 2026-07-08T06:00:00Z

# walk-forward backtest (net of ₹20/leg costs)
python strategy_framework/run_demo.py backtest --exit horizon --hold 2   # mark-to-market
python strategy_framework/run_demo.py backtest --exit expiry             # hold to expiry
python strategy_framework/run_demo.py backtest --exit manage             # active adjustment

# tests
python -m pytest strategy_framework/tests/ -q
```

Point at a specific database with `NIFTY_DB=/path/to/option_chains.db`. By default it
resolves your live Google-Drive `option_chains.db`, falling back to the repo copy.

## MPS0 max-profit benchmark (capture %)

A perfect-hindsight ceiling to score the desk against, also after Salov's MPS.
`backtest/metrics.py::mps0_max_profit(prices, flip_cost_inr, lot_size)` computes the
best a reversal strategy *could* have made — an O(n) two-state DP over the price path —
and `summarize()` optionally returns `mps0_max_rupees` + `capture_pct` (realised net ÷
ceiling). `backtest/walkforward.run` gains `mps_benchmark`: `"off"` / `"gross"` (zero-cost
ceiling) / `"net"` (charge the desk's average round-trip cost per flip). The price path is
sampled at the strategy's **entry cadence** (stride), not every minute, so capture % is
apples-to-apples. Exposed via `api.backtest` (`mps_benchmark`) and an **MPS0 max-profit
benchmark** dropdown; capture % shows as a chip in the desk results. It is explicitly a
**perfect-hindsight ceiling / skill score, NOT a target** — descriptive only.

## Honesty about the data

You currently have ~4 sessions of minute history. The framework runs and the plumbing
is verified, but **every backtest metric is tagged DESCRIPTIVE ONLY** until ≥60 sessions
exist — do not read edge into 35 trades over 4 days. The intended use now is:

- run `suggest` live each snapshot as data accrues, and
- re-run `backtest` weekly; the metrics become meaningful as `n_sessions` climbs.

When history is deep enough, the PRIOR weights/thresholds in `config/settings.py` can be
calibrated (graduated to FITTED) against the walk-forward trade log.

## App integration (Strategy Desk panel)

The framework is wired into your app as a **Strategy Desk** panel (`src/components/
StrategyDeskPanel.tsx`, reachable at `/trade/desk`). Zones: live suggestion +
signal-contribution bars, the suggested payoff, a **ranked candidate list**, a
mixed portfolio (option strategies + futures + stocks) with combined P&L and net
delta, and a backtest strip toggling between the **auto** suggestion stream and
**your book**.

### Why the desk always shows something (candidates)

The conviction gate is deliberately conservative — on a weak/mixed read it returns
`NO_TRADE` and fires nothing. Rather than leave the desk blank, `strategy/
candidates.py` always returns a **ranked list of priced candidate structures** for
the current lean (directional verticals, long options, iron condor/butterfly,
straddle), each tagged with a rationale, an `aligned` flag, and which one the gate
would actually fire on (`primary`). So you always see what the analysis leans
toward and can add any candidate yourself — the gate governs auto-execution, not
what you're allowed to see.

Backend endpoints (added to `backend/main.py`, logic in `strategy_framework/api.py`):

```
GET  /api/strategy/suggest            → regime, signals, structure + RANKED CANDIDATES
POST /api/strategy/suggest/add        → add the gate-fired structure to the book
POST /api/strategy/candidate/add      → add a chosen candidate family {family}
GET  /api/strategy/portfolio          → positions + combined valuation (live→capture)
POST /api/strategy/portfolio/add      → add {kind: option_strategy|future|stock, …}
POST /api/strategy/portfolio/remove   → {id}
POST /api/strategy/backtest           → {mode: auto|book, exit_mode, expiry}
GET  /api/strategy/config             → weights / gates / costs in effect
```

Portfolio P&L is marked live-feed-first with a latest-capture fallback. Futures and
stocks are linear (delta-1); options are marked leg-by-leg from the chain. Net delta
is reported as rupees per +1 NIFTY point (stocks use a beta≈1 approximation, flagged).

## Signal validation (Signal Test view) & feature store

Before trusting a signal, validate it. The **Signal Test** panel (`/trade/signaltest`,
`src/components/SignalBacktestView.tsx`) has five modes:

- **All signals** — scoreboard ranking every signal by Sharpe / hit / IC / spread at one
  horizon, with an overlap/effective-n warning when sampling is finer than the horizon.
- **Single signal** — bucket chart, biggest misses, IC-vs-horizon curve.
- **Attribution** — relate any predictor (a signal or feature) to a forward return (or
  another signal — signal→signal), sliced by a **condition** (dte, VIX regime, …). Reports
  IC, **Sharpe**, hit, spread per bucket. Guards against predictor==target.
- **Correlation** — pairwise correlation of the six live signal scores + a count of
  *effectively independent* bets (are they really N independent bets or fewer?).
- **Horizon map** — every signal × every horizon (5m…3h, EOD, next-day) coloured by
  IC / Rank IC / Sharpe / Spread / Hit; ☆ = each signal's best horizon; VIX-regime filter.

These read from the **feature store** (`features/`): a sibling `snapshot_features.db` that
precomputes, per `(ts, expiry)`, every signal's score (+ an `_ok` status flag), chain
stats (RND, smile, solved IV, OI, max-pain, PCR), market state (VWAP, realised vol,
`vix_regime`), and **forward-return outcome labels**. Backfill it once ("rebuild all" in
the UI or `api.features_backfill(force=True)`); the analytics then **read precomputed
scores instead of re-evaluating** ("compute once, read many" — the Horizon map falls back
to live evaluation only for snapshots the store lacks).

## Performance

- **BarCache** bulk-loads all 1m bars for the expiry window once (bisect slices, not a
  query per snapshot), and **negative-caches** absent symbols so a missing GOLD/COPPER
  isn't re-queried every call. Threaded through the analytics, simulate and backtest.
- The cues file is memoised by mtime. Net effect on the profiled hot path: `bundle.evaluate`
  ~51ms → ~6ms; a full Horizon-map run ~3.2s → ~0.24s (→ ~0.09s off the feature store).
- **Backfill** uses the same scoped, negative-caching BarCache and now pre-loads the
  cross-asset/macro symbols too, so `global_momentum` / macro signals don't hit SQLite per
  snapshot. Backfill is also **incremental** (only new snapshots; `stride` samples coarser).
- IV is solved from option LTPs (`bs.py`, Newton + bisection) since the feed stores IV=0.

## Layout

```
strategy_framework/
  config/
    settings.py             # weights, gates (incl. min_edge_cost_mult), strike prefs,
                            #   ₹/leg costs, DB resolution
    constituents.py         # symbol-keyed NIFTY-50 index weights + sectors
  signals/
    data_access.py          # as-of DB reads (no lookahead)
    base.py                 # Signal / SignalBundle contract
    index_volume.py         # per_bar_index_volume: SINGLE canonical home for index-volume
                            #   reconstruction (Σ index_weightᵢ×volumeᵢ/bar; NIFTY bar has vol=0).
                            #   Imported by technical_momentum, vwap, rel_volume (vol_index is exempt)
    heavyweight_leadership.py   # 50-constituent weighted tape + sector leadership
    technical_momentum.py  global_momentum.py  breadth_oi.py
    vrp.py  skew_rnd.py  time_of_day.py  earnings_events.py
    vwap.py  vol_index.py  rel_volume.py          # volume signals (weight 0.0)
    crude_energy.py  usdinr.py  global_gap.py     # macro / risk-off (weight 0.0)
    futures_basis.py  futures_calendar.py  futures_flow.py   # NIFTY futures (weight 0.0)
    bundle.py               # evaluate all signals as-of a timestamp
  strategy/
    regime.py               # TREND / RANGE / NO_TRADE classifier → family
    directional.py          # wraps regime → Decision
    constructor.py          # family → priced legs + payoff (verticals, condor, butterfly,
                            #   straddle, strangle); from_legs() for adjustments
    adjustment.py           # manage an open position: tilt / defend / convert / harvest /
                            #   recenter / exit — with cooldown, max-rolls, persistence gates
    suggester.py            # top-level suggest() API
  portfolio/
    book.py                 # mixed book: option strategies + futures + stocks
    valuation.py            # mark to price → per-position + combined P&L, net delta
    context.py              # pricing context: live feed → latest-capture fallback
  backtest/
    walkforward.py          # auto suggestion stream; horizon | expiry | manage
    portfolio_bt.py         # mark the assembled book forward (book mode)
    metrics.py              # honest, caveated summary stats (net of ₹ costs)
  features/
    extractor.py            # as-of feature vector: signals, RND/skew/IV, market state,
                            #   vix_regime, forward-return labels (+ sig_*_ok flags)
    store.py                # snapshot_features.db (sibling); backfill / query / clear
  api.py                    # facade the FastAPI /api/strategy/* routes call
                            #   (suggest, backtest, simulate, signal-test analytics,
                            #    feature-store fast path — "compute once, read many")
  tests/                    # payoff, no-lookahead, adjustment, cost, valuation (26 tests)
  run_demo.py               # CLI: expiries | suggest | backtest
```
