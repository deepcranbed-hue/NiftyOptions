# Reference — Strategy Desk Framework

Exact data shapes and contracts at every major boundary of the `strategy_framework`.
Direction convention everywhere: **+1 bullish NIFTY, −1 bearish, 0 neutral.**
All DB timestamps are UTC (`...Z`); IST (+5:30) is a display concern only.

## 1. Signal (signals/base.py)
Atomic unit every signal emits.
```json
{
  "name": "heavyweight_leadership",
  "score": -0.302,          // [-1, 1], + bullish
  "confidence": 0.658,      // [0, 1]
  "tag": "PRIOR",           // PRIOR | FITTED (D-MA-04)
  "status": "OK",           // OK | INSUFFICIENT_HISTORY | NO_DATA | STALE
  "detail": { }             // raw numbers behind the score (per-signal, see below)
}
```
Representative `detail` payloads:
```json
// heavyweight_leadership
{ "weighted_ret_pct": -0.19, "concentration": 1.0, "breadth": -1.0,
  "coverage_weight_pct": 24.8, "hv_vol_surge": 1.53,
  "leaders": [{"sym": "HDFCBANK", "contrib": -0.028, "ret_pct": -0.243, "vol_surge": 2.17}],
  "sector_tilt": [{"sector": "Financial Services", "contrib": -0.03}], "n_constituents": 3 }

// technical_momentum  (vol_source: "nifty_bar" if the NIFTY bar has volume, else
//                      "constituents(N)" from per_bar_index_volume — the canonical helper)
{ "trend_z": 0.079, "thrust_z": -0.007, "vol_ratio": 1.0, "vol_source": "constituents(47)",
  "ema_fast": 24274.0, "ema_slow": 24273.5, "atr_1m": 6.84, "n_bars": 120 }

// breadth_oi  (breadth is INDEX-WEIGHTED + equal-weighted; score uses weighted)
{ "breadth": {"score": 0.46, "net_breadth_weighted": 0.68, "net_breadth_unweighted": 0.33,
    "weight_vs_equal_divergence": 0.34, "avg_move_weighted_pct": 0.14, "n": 47},
  "oi": {"score": 0.12, "support": 24000, "resistance": 24600, "pcr": 1.08} }

// vrp
{ "rv_ann_pct": 5.96, "implied_pct": 12.0, "vrp_ratio": 2.013,
  "regime": "RICH", "implied_source": "vix" }

// skew_rnd
{ "rnd": {"mean": 24271.9, "sd": 123.8, "skew": -0.221, "p_above_spot": 0.537},
  "skew_proxy": {"rr_proxy": 0.118, "put_k": 24000, "call_k": 24500}, "engine": "rnd" }

// vwap  (volume, weight 0.0; spot vs SESSION VWAP; per-minute volume index-weighted
//        from constituents; reports weighted + unweighted VWAP; TWAP if no vol at all)
{ "vwap": 24010.0, "vwap_unweighted": 24008.5, "spot": 24045.0, "dist_pct": 0.146,
  "vwap_slope_pct": 0.02, "vol_source": "constituents (47)", "n_bars": 181,
  "convention": "spot above session VWAP = bullish" }

// vol_index  (volume, weight 0.0; index-weight × volume momentum from constituents)
{ "wv_return_pct": 0.29, "index_weighted_return_pct": 0.21, "volume_weighted_return_pct": 0.25,
  "weight_vs_volume_divergence_pct": 0.04, "vol_weighted": true, "index_weighted": true,
  "n_constituents": 47, "convention": "index-weight × volume up = bullish" }

// rel_volume  (volume, weight 0.0; NIFTY direction × participation; volume ESTIMATED
//              from constituents, index-weighted, since the index has none)
{ "recent_ret_pct": 0.22, "rel_volume_weighted": 1.45, "rel_volume_unweighted": 1.30,
  "participation_boost": 1.27, "vol_source": "constituents (47)",
  "convention": "move on high (weighted) constituent-volume = conviction" }

// crude_energy  (macro, weight 0.0; reads CRUDEOIL from price_bars; crude up = bearish)
{ "crude_ret_30m_pct": 0.652, "crude_ret_day_pct": 8.525,
  "convention": "crude up = bearish NIFTY", "n_streams": 2 }   // score -0.80, conf 0.82

// usdinr  (macro, weight 0.0; reads USDINR; rupee weak = bearish; overlaps global_momentum)
{ "usdinr_ret_30m_pct": 0.483, "usdinr_ret_day_pct": 6.187,
  "convention": "USDINR up (rupee weak) = bearish NIFTY", "n_streams": 2 } // score -0.95

// global_gap  (macro, weight 0.0; GIFTNIFTY vs spot; the forward/overnight read)
{ "gift_last": 24365.6, "spot": 24026.8, "gift_premium_pct": 1.41,
  "minutes_since_open": 180.0, "convention": "GIFT above spot = bullish next session",
  "n_streams": 2 }                                            // score +0.70, conf decays after open

// futures_basis  (futures, weight 0.0; NIFTY_FUT_1 vs spot; premium expand = bullish, discount = bearish)
{ "basis_pts": 41.5, "basis_pct": 0.171, "basis_z": 1.8, "basis_trend_30m_pts": 6.0,
  "calendar_far_minus_near_pts": 58.0, "regime": "PREMIUM",
  "convention": "premium expanding = bullish; discount = bearish" }   // score +0.69

// futures_calendar  (futures, weight 0.0; FUT_2 - FUT_1 term structure; steepening = bullish, backwardation = bearish)
{ "calendar_spread_pts": 58.0, "calendar_spread_pct": 0.24, "spread_z": 1.2,
  "spread_trend_30m_pts": 4.0, "far_volume_share": 0.22, "structure": "CONTANGO",
  "convention": "steepening contango = bullish; backwardation = bearish" }  // score +0.57

// futures_flow  (futures, weight 0.0; NIFTY_FUT_1 price x REAL traded volume; the only true index volume)
{ "fut_recent_ret_pct": 0.28, "fut_rel_volume": 1.55, "participation_boost": 1.33,
  "vol_source": "NIFTY_FUT_1 REAL traded volume", "thin_volume_move": false,
  "convention": "futures move on rising REAL volume = conviction; + = up" }  // score +0.90

// time_of_day (gate/modulator, score always 0)
{ "phase": "OPENING_DRIVE", "ist_time": "09:20", "minutes_since_open": 5,
  "momentum_multiplier": 1.30, "expected_move_mult": 1.35, "pin_risk": false, "expiry_day": false }
```

## 2. Decision (strategy/directional.py)
```json
{
  "now": "2026-07-06T04:45:00Z", "spot": 24380.0, "direction": 0,
  "regime": "NO_TRADE",         // TREND_UP | TREND_DOWN | RANGE | NO_TRADE
  "net_score": -0.0175, "net_confidence": 0.746,
  "action": "STAND_ASIDE",      // ACT | STAND_ASIDE
  "family": "stand_aside", "expected_move_pts": 298.9,
  "phase": "POWER_HOUR", "vrp_regime": "RICH",
  "edge_ratio": 0.8, "edge_cost_mult": 1.5, "cost_gated": true,  // cost-edge gate (Salov MPS):
                                // 1σ move ₹/lot vs mult × round-trip cost; flips ACT→STAND_ASIDE
                                // when edge_ratio < edge_cost_mult. Off (edge_cost_mult=0) by default.
  "reasons": ["phase=POWER_HOUR", "no edge: weak/mixed momentum"],
  "veto": {"veto": false, "reason": null},
  "contributions": {"heavyweight_leadership": {"score": -0.3, "eff_conf": 0.66, "status": "OK"}}
}
```

## 3. Structure (strategy/constructor.py)
Leg encoding matches the project convention `(side, strike, sign)` — compatible with `strategy_compare.py` / `portfolio.py`.
```json
{
  "family": "iron_condor",
  "legs": [
    {"side": "put",  "strike": 24000.0, "sign": 1},
    {"side": "put",  "strike": 24100.0, "sign": -1},
    {"side": "call", "strike": 24400.0, "sign": -1},
    {"side": "call", "strike": 24500.0, "sign": 1}
  ],
  "net_debit_pts": -66.1,        // + = debit paid, − = credit received
  "max_profit_pts": 66.1, "max_loss_pts": 33.9,
  "breakevens": [24033.9, 24466.1],
  "rupees": {"max_profit": 4957, "max_loss": 2543, "net_debit": -4957},
  "detail": {"atm": 24250.0, "grid_step": 50.0, "strikes": [24000, 24100, 24400, 24500]}
}
```

## 4. Suggestion (strategy/suggester.py → GET /api/strategy/suggest)
```json
{
  "now": "...Z", "expiry": "2026-07-07T06:00:00.000Z", "tradeable": false,
  "note": "below conviction gate — ...",
  "decision": { /* §2 */ },
  "structure": { /* §3, the informational lean when gated */ },
  "signals": { "heavyweight_leadership": { /* §1 */ }, "...": {} },
  "candidates": [
    { "family": "bear_put_spread", "primary": true, "aligned": true,
      "rationale": "bearish, defined risk (debit vertical)", "structure": { /* §3 */ } }
  ],
  "diag": {"db_path": "...", "n_expiries": 1, "captures_for_expiry": 65}   // only on error/empty
}
```

## 5. Backtest request (POST /api/strategy/backtest)
```json
{
  "mode": "auto",                 // auto | book
  "expiry": "2026-07-07T06:00:00.000Z",  // null = latest with captures
  "exit_mode": "manage",          // horizon | expiry | manage
  "hold": 2,
  "freq_minutes": 60,             // null = auto; else entry cadence (min)
  "window_days": 4,               // N SESSION days incl. expiry; null = all
  "roll_directional": false,      // also roll verticals/long options
  "stop_loss": true,              // manage: cut early vs hold to expiry
  "stop_loss_rupees": 2000,       // fixed ₹ stop; null = auto (min of 2×credit, 60% max loss)
  "take_profit": true,            // book the gain, don't hold a winner to expiry
  "take_profit_frac": 0.6,        // close at this fraction of max credit (~50–60% typical)
  "cooldown_min": 15,             // min minutes between adjustments (null = default 15)
  "max_rolls": 2,                 // adjustments budget (fee limit; stop-loss owns the exit)
  "persist_near": 1,              // snapshots a strike must stay threatened before acting
  "harvest": false,               // opt-in: harvest premium on the safe wing in a trend
  "min_harvest_inr": 100,         // min net ₹/lot to justify a harvest
  "max_manage": 400,              // monitoring checks, spread across the whole window
  "min_edge_cost_mult": 0.0,      // cost-edge gate (Salov MPS): 0=OFF; else STAND_ASIDE when the
                                  //   1σ move ₹/lot < mult × round-trip cost (dropdown: 1/1.5/2/3×)
  "mps_benchmark": "off"          // MPS0 max-profit ceiling: off | gross (zero-cost) | net
                                  //   (charge avg round-trip/flip); path sampled at entry cadence
}
```

Same adjustment knobs apply to `POST /api/strategy/simulate` (walk one chosen position
forward). Iron-condor shorts are placed ~1σ (expected move) OTM at entry
(`condor_short_em_mult`). On a still-live expiry the final row is a provisional `MARK`
(not `SETTLE`).

## 6. Backtest result — auto mode
```json
{
  "mode": "auto", "expiry": "...", "exit_mode": "manage",
  "metrics": {
    "n_trades": 19, "n_sessions": 5, "sufficient": false,
    "hit_rate": 0.32, "avg_pnl_pts": -1.2,
    "total_pnl_rupees": -2429,        // NET of costs
    "total_cost_inr": 2920, "gross_pnl_rupees": 491, "avg_cost_per_trade_inr": 154,
    "mps0_max_rupees": 18420, "capture_pct": -13.2,  // only when mps_benchmark != "off":
                                      //   perfect-hindsight reversal ceiling + realised net ÷ ceiling
                                      //   (a skill score / ceiling, NOT a target — descriptive only)
    "max_drawdown_pts": -57.6, "profit_factor": 0.7, "sharpe_like_per_trade": -0.1,
    "elapsed_sec": 6.7, "captures_total": 65,
    "entry_stride": 1, "cadence_min": 1.0, "freq_minutes_effective": 60.0,
    "window_sessions": 4, "sessions_in_window": 4,
    "session_dates": ["2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"],
    "note": "DESCRIPTIVE ONLY — 5 sessions < 60 needed for edge inference (D-MA-04)"
  },
  "trades": [ /* §7 */ ],
  "n_decisions": 65
}
```

## 7. Trade record (element of `trades[]`)
```json
{
  "session": "2026-07-06", "entry_ts": "...Z", "exit_ts": "...Z",
  "direction": 0, "regime": "RANGE",
  "entry_family": "iron_condor", "final_family": "iron_condor",
  "entry_legs": ["Buy put 24000", "Sell put 24100", "Sell call 24400", "Buy call 24500"],
  "entry_spot": 24380.0, "exit_spot": 24526.0, "net_score": 0.05,
  "gross_pnl_pts": -17.3, "cost_inr": 320, "n_adjustments": 1,
  "adjustments": [
    { "action": "CONVERT_TO_VERTICAL", // HOLD | ROLL_UNTESTED_TOWARD | DEFEND_TESTED |
                                       //   CONVERT_TO_VERTICAL(+LONG) | CONVERT_STRADDLE_* |
                                       //   HARVEST_WING | RECENTER | TAKE_PROFIT | STOP_LOSS | EXIT | CLOSE
      "touched": 2, "threatened": true, "rationale": "strong confirmed trend — drop tested wing, ride the retained credit spread ...",
      "orders": ["Buy to close put 24100", "Sell to close put 24000"], "at": "...Z" }
  ],
  "pnl_rupees": -1621,              // NET of costs
  "pnl_pts": -21.6, "won": false
}
```

## 8. Backtest result — book mode
```json
{
  "mode": "book", "expiry": "...",
  "series": [ {"ts": "...Z", "spot": 24268.5, "pnl_rupees": -4417, "net_delta": 76.7} ],
  "metrics": { "n_marks": 65, "start_pnl": -22781, "final_pnl": -4417,
               "best_pnl": 3225, "worst_pnl": -32026, "max_drawdown_rupees": -12455,
               "note": "book marked forward, no-lookahead; descriptive only (D-MA-04)" }
}
```

## 9. Portfolio (GET /api/strategy/portfolio)
```json
{
  "expiry": "...",
  "positions": [
    { "id": "3ba4dc08", "kind": "option_strategy", "label": "bull_call_spread 24300/24400",
      "payload": {"family": "bull_call_spread", "legs": [["call",24300.0,1],["call",24400.0,-1]],
                  "entry_prices": {"call:24300.0": 70.8, "call:24400.0": 38.0}, "lot_size": 65},
      "status": "open", "created_at": 1783453567.6 },
    { "id": "...", "kind": "future", "label": "NIFTY_FUT_1 · exp 2026-07-30",
      "payload": {"symbol": "NIFTY_FUT_1", "entry_price": 24200, "qty": 1, "lot_size": 65,
                  "exchange": "NFO", "expiry": "2026-07-30"} },
    { "id": "...", "kind": "stock", "label": "RELIANCE",
      "payload": {"symbol": "RELIANCE", "entry_price": 1420, "qty": 50} }
  ],
  "valuation": {
    "lines": [ {"id": "...", "label": "...", "kind": "future",
                "pnl_rupees": 1620, "delta_index_rupees": 75, "marked_live": true} ],
    "total_pnl_rupees": -4120,
    "net_delta_rupees_per_point": 77.7,     // ₹ P&L per +1 NIFTY point (stocks: beta≈1)
    "spot": 24268.5, "any_unmarked": false, "source": "capture", "as_of": "...Z"
  }
}
```

## 10. Add position (POST /api/strategy/portfolio/add)
```json
// future / stock
{ "kind": "future", "symbol": "NIFTY_FUT_1", "entry_price": 24200, "qty": 1, "lot_size": 65,
  "exchange": "NFO", "expiry": "2026-07-30" }
{ "kind": "stock",  "symbol": "RELIANCE", "entry_price": 1420, "qty": 50, "exchange": "NSE" }
// option strategy (or use POST /api/strategy/candidate/add with {"family": "..."} )
{ "kind": "option_strategy", "family": "bull_call_spread",
  "legs": [["call",24300.0,1],["call",24400.0,-1]], "entry_prices": {},
  "exchange": "NFO", "expiry": "2026-07-30", "lot_size": 65 }
// future  → { "kind":"future","symbol":"NIFTY_FUT_1","qty":1,"lot_size":65,
//            "exchange":"NFO","expiry":"2026-07-30" }
// stock   → { "kind":"stock","symbol":"RELIANCE","qty":50,"exchange":"NSE" }
```

## 10a. instruments_meta()  (data-driven Desk Book dropdowns)
```json
{ "exchanges": ["NFO","NSE"], "lot_size": 65,
  "expiries": ["2026-07-30", ...],                 // option expiries (weekly+monthly)
  "futures_expiries": ["2026-07-31","2026-08-27"], // derived monthly (fallback)
  "symbols": ["RELIANCE","HDFCBANK", ...],         // tradable stocks (1m bars, ex index/cross-asset)
  "futures_symbols": [                             // the real series with rank+expiry
    {"symbol":"NIFTY_FUT_1","rank":1,"last_bar":"...","expiry":"2026-07-30"},
    {"symbol":"NIFTY_FUT_2","rank":2,"last_bar":"...","expiry":"2026-08-27"} ] }
```

## 10b. Futures action optimizer  (futures_action_eval / /api/strategy/futures-action)
```json
{ "best": "REVERSE", "position_lots": 1, "lambda": 0.5, "max_lots": 2,
  "allow_reverse": true, "horizon_frac": 1.0, "as_of": "...Z", "spot": 25000.0,
  "forecast": {"expected_move_pts": -51.0, "std_dev_pts": 60.0,
               "prob_up": 0.198, "prob_down": 0.802, "net_score": -0.85, "confidence": 0.82},
  "table": [ {"action":"REVERSE","target_lots":-1,"traded_lots":-2,
              "expected": 3275, "cvar10": -3569, "std": 3900, "cost_inr": 40,
              "kind":"distribution", "score_abs": 1491, "score": 9884}, ... ],
  "score_label": "risk-adj EV vs HOLD (₹)" }
// score = score_abs − HOLD.score_abs;  score_abs = expected − λ·|cvar10|.  EXIT row: kind:"realized".
```
Backtest advisory (in a future sim's `stats.advisory` / top-level `advisory_agreement`):
```json
{ "would_be_pnl_inr": 4120, "plain_pnl_inr": 2600, "edge_inr": 1520,
  "n_advisory_actions": 3, "max_lots": 2,
  "note": "optimizer ran advisory-only; recorded P&L is the plain 1-lot path." }
```
Each managed bar's `decision.advisory`: `{best, forecast, table, shadow_lots, would_be_pnl}`.

## 11. FastAPI endpoints
```
GET  /api/strategy/config             -> {version, db_path, lot_size, weights, gates, strikes, costs}
GET  /api/strategy/expiries           -> {expiries: [{expiry, n_captures, first, last}]}
GET  /api/strategy/instruments        -> §10a (Desk Book dropdowns)
GET  /api/strategy/suggest[?expiry&now] -> Suggestion (§4)
POST /api/strategy/suggest/add        -> {ok, position}          (adds the gate-fired structure)
POST /api/strategy/candidate/add {family[,expiry,now,exchange]} -> {ok, position}
GET  /api/strategy/portfolio[?expiry] -> §9
POST /api/strategy/portfolio/add      -> {ok, position}          (§10 body)
POST /api/strategy/portfolio/remove {id} -> {ok}
POST /api/strategy/portfolio/clear    -> {ok}
POST /api/strategy/backtest           -> §6 / §8                 (§5 body)
POST /api/strategy/book/backtest {pos_id,entry_ts,expiry,exit_mode,...} -> §6  (routes by kind; advisory in Manage)
POST /api/strategy/simulate-compare   -> {rows, timeline, order}  (A/B/C/D per-entry grid)
POST /api/strategy/compare-harvest    -> A/B/C/D window comparison
POST /api/strategy/futures-action     -> §10b (point-in-time HOLD/EXIT/ADD/REDUCE/REVERSE)
GET  /api/strategy/drawdown-insurance[?date&expiry&now] -> derisk overlay + tail hedge
GET  /api/macro-shock?date=…          -> cause-effect + shock-timing + pre-open verdict
```

## Invariants referenced
* **D-MA-01** backward as-of join (no lookahead).
* **D-MA-03** VRP ratio = IV_atm / RV_basket.
* **D-MA-04** PRIOR-tag until ≥60 sessions; backtest metrics "descriptive only".
* Cost model: ₹20 per leg per transaction (entry, exit, each adjustment).
