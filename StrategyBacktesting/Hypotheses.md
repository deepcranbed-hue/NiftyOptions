# Hypotheses — single source of truth for backtesting results

**Read this before testing anything.** Most ideas in this file have already been
tested and killed. Re-running a dead hypothesis wastes a session and, worse,
tends to resurrect it — the second run finds a different arbitrary threshold and
reports it as new.

Every row links to the script that produced it and the result JSON it wrote. If a
row says DEAD, the burden is on the new test to explain what is different, not to
re-derive the same number.

- **Scope:** all hypothesis testing on this repo through 2026-08-13.
- **Location rule:** this file is the only place backtesting verdicts live.
  Scripts and `*_result.json` stay at repo root; verdicts are recorded here.
- **Update rule:** a test is not finished until its row is in the register below.

---

## 0. Method standard

Every result in this file was produced under these rules. A test that skips them
is not comparable to the rows here and should not be added.

| rule | why |
|---|---|
| **Null by circular shift, not permutation** | Plain permutation destroys autocorrelation and understates the null. Persistent regressors manufacture significance against a shuffled null. |
| **max-\|t\| across the whole signal family** | Testing 8 signals is 8 shots at one dataset. The yardstick is the distribution of the *largest* \|t\| under the null, with cross-correlation preserved (shift the block together). |
| **Newey-West standard errors (lag 5)** | OLS SEs are far too small on overlapping/persistent series. |
| **Non-overlapping windows for multi-day horizons** | Overlapping 20-day forward returns on daily data inflate n by ~20x. Report the non-overlapping n as the honest sample size. |
| **Publication lag is part of the signal** | NSE participant data for day D publishes after the close on D. Target must be D+1 or later. Anything that captures close(D)→open(D+1) is untradable by construction. |
| **Count tests and max tests answer different questions** | max-\|t\| is blind to a diffuse effect spread over many weak cells; a count test is blind to one strong effect. Run both when the alternative is unclear. |
| **Cross-sectional dependence breaks the 5% yardstick** | 11 sectors on shared regressors are not independent. Null median survivor count was 15, not the binomial 6.6. |
| **State the minimum detectable effect** | With n=247 and daily σ≈0.98%, min detectable r ≈ 0.179. Below that, a null means "cannot see", not "nothing there". |

---

## 1. Register

Verdicts: **DEAD** (no effect survives its null) · **SURVIVES** (clears its null; caveat noted) ·
**ARTIFACT** (effect real in data, caused by measurement flaw) · **RETRACTED** (reported, then failed a later test) ·
**MEASURED** (descriptive quantity, not a hypothesis) · **UNDERPOWERED** (cannot distinguish) · **OPEN** (untested)

### Sector rotation

| # | hypothesis | verdict | key statistic | script / result |
|---|---|---|---|---|
| 1 | Intraday sector momentum persists | **DEAD** | mean persistence r = −0.055, inside noise | `rotation_probe.py` |
| 2 | Persistence exists at *some* lookback/forward pair | **DEAD** | best \|r\| 0.3357 vs null p95 0.3613, p = 0.137 over a 5×5 grid | `rotation_sweep.py` |
| 3 | The effect is diffuse, so use a count test not a max test | **SURVIVES** | mean persistence, share-negative and breach-count all p = 0.00 (300 perms) | `rotation_diffuse.py` |
| 4 | …but is it real, or thin prices in small buckets? | **ARTIFACT** | corr(bucket size, r) = +0.475; reversal shrinks as buckets widen; vanishes on published indices | `rotation_bucketsize.py` |
| 5 | Reversal survives an execution gap | SURVIVES (moot — see #4) | p = 0.00 at gaps 0/1/2, collapse 0.067 | `rotation_bounce.py` |
| 6 | Rotation is driven by macro (crude/USDINR/gold/VIX) | **DEAD** | 18.2% of effect survives controls; mean residual r = 0.0004 | `rotation_causes.py` |
| 7 | Rotation tracks daily FII/DII flow | **UNDERPOWERED** | n = 35, noise band 0.331 | `rotation_daily.py` |
| 8 | Sector returns track FII index/stock positioning | **UNDERPOWERED** | 15 "stars" vs 2.2 expected, but no null run on the family | `rotation_fii.py` |

**Bottom line:** intraday sector rotation is a small-bucket pricing artifact. Do not rebuild it.

### Macro → index

| # | hypothesis | verdict | key statistic | script / result |
|---|---|---|---|---|
| 9 | USDINR / crude / VIX / own-return predict 11 sectors | **DEAD** | 18 survivors vs null median 15, p95 21 → p = 0.225 | `horserace.py`, `horserace_null.json` |
| 10 | …at least crude does, on a count test | **SURVIVES (weak)** | crude count 16 vs null median 10, p = 0.03; but max-\|t\| p = 0.35 | `maxt_null.py` |
| 11 | High/rising crude predicts Nifty weakness | **DEAD** | crude high+rising: next-1d p = 0.979. Top quintile next-1d +0.009% vs +0.043% baseline | `crude_analogue.py` |
| 12 | Crude beta is stable | **MEASURED** | full-sample β = +0.0064; 2026 β = −0.039. Sign flips by regime | `crude_priced.py` |
| 13 | SP500 × crude *interaction* (not additive) predicts | **SURVIVES** | quadrant spread 0.44, p = 0.00; t(interaction) = 2.79; R² 7.21→7.63 | `regime_test.py` |
| 14 | US indices lead Nifty IT next session | **SURVIVES (weak)** | NASDAQ→IT intraday r = 0.066, p = 0.0025, n = 2005. Gap r = 0.146 but untradable | `us_leads_it.py` |
| 15 | Breadth modifies the index signal | **DEAD** | bullish/broad vs bullish/narrow spread 0.132, p = 0.196; t(interaction) = 1.20 | `breadth_modifier.py` |
| 16 | Velocity/acceleration adds over VIX level | **SURVIVES (weak)** | R² 31.83 → 33.13; t(accel) = −3.48. Sign is *mean-reverting*, not continuation | `velocity_test.py` |
| 17 | Index closes high in range despite weak breadth = bullish absorption | **DEAD** | 161 days, next-day −0.019% vs +0.046% baseline, p = 0.79. Sign flips across thresholds | 2026-08-13, this register |
| 17b | …but strong close **with** strong breadth | **SURVIVES** | +0.069 to +0.078pp over baseline at every threshold, win 59–61%, n = 434–627 | same |

**Bottom line:** the daily macro→index regression was retired at R² = 0.036. Macro explains; it does not forecast. Only the SP500×crude interaction and a weak US→IT lead survive.

### FII positioning

| # | hypothesis | verdict | key statistic | script / result |
|---|---|---|---|---|
| 18 | FII sector positioning predicts sector returns | **UNDERPOWERED** | 132 tests, 11 hits, min detectable r = 0.179 | `fii_battery.py` |
| 19 | FII positioning works within regimes | **DEAD** | 5-day spread p = 0.0375 but interaction p = 0.23 over 400 perms | `fii_regimes.py` |
| 20 | …and it is not just the expiry cycle | **DEAD (confounded)** | corr(position, day-of-cycle) = −0.529; residualised p = 0.32–0.57 | `fii_confound.py` |
| 21 | FII derivatives activity (8 signals) predicts next day | **DEAD** | max-\|t\| null: close-close p = 0.202, intraday p = 0.220 | `fii_deriv.py` |
| 22 | …it predicts the overnight **gap** | **SURVIVES, UNTRADABLE** | p = 0.022; but the file publishes *after* the close, so close(D)→open(D+1) is unreachable. Every reachable target dead (p = 0.11–0.66) | `fii_gap.py` |
| 23 | FII positioning works in shock regimes | **DEAD** | 0 of 28 subgroup tests cleared p<0.05; chance ≈ 1.4 | `fii_shock.py` |
| 24 | FII net short **level** predicts returns | **DEAD** | raw level r = −0.50 at 20d looked strong, but non-overlapping n = 12 → p = 0.635; split-half −0.28 vs −0.90; z-scored fails everywhere | 2026-08-13 |
| 25 | FII "know" the market falls | **DEAD** | same-day r = +0.690, next-day r = +0.007. Largest short build was March 2026 — the month it bottomed. Max bearish across futures/puts/calls on the exact low | `fii_shock.py` + daily March analysis |
| 26 | FII are always net short index futures | **SURVIVES (fact)** | 245/245 days net short; range −87,170 to −279,467; median −181,339 | `participant_oi` |
| 27 | The short is a hedge of their cash book | **DEAD** | ₹39,518 cr ≈ 1% of ~₹40 lakh cr FII-held Nifty 50 equity | 2026-08-13 |
| 28 | Position size tracks the futures premium (carry) | **SUGGESTIVE, n = 5** | carry 30.4% (May) → 3.4% (Jul); Pearson −0.616, Spearman −0.300 on 5 months. April contradicts | 2026-08-13 |

**Bottom line:** FII derivatives positioning has no predictive content at any horizon, in any regime, in any specification tested. It is best explained as the counterparty print of a structurally long retail book (retail 3.0 longs per short; FII hold 67.8% of all short OI), sized against the carry.

### Theta / premium selling

| # | hypothesis | verdict | key statistic | script / result |
|---|---|---|---|---|
| 29 | A variance risk premium exists | **MEASURED** | realised/implied = 0.673 across **all** VIX buckets — the edge does not concentrate | `theta_study.py`, `conditional_vrp.py` |
| 30 | VRP is wider after broad vs concentrated declines | **DEAD** | broad − concentrated = 0.044, p = 0.380 | `theta_study.py` |
| 31 | The tail is manageable | **MEASURED** | ratio > 1 on 20.8% of days; worst 1% = −70.96pp against +984.59pp gross wins | `theta_tail.py` |
| 32 | Path risk ≈ terminal risk | **DEAD** | median MAE 2.35% vs terminal 1.45%; breach 75.7% vs 44.7%. Concentrated vs broad p = 0.810 | `path_risk.py` |
| 33 | Overnight is the dominant risk | **MEASURED — regime-dependent** | full sample gap share 40.6%; **2026 YTD 71.8%**; 2023 28.3%. See correction C5 | `overnight_test.py` |
| 34 | Buying wings is cheap protection | **DEAD** | call wing keeps 78% of theta, cuts ES only 7%, and makes the worst case **worse** (−19.65 vs −17.17) | `wings_real.py`, `wings_pnl.py` |
| 35 | The wing premium is a fat-tail premium | **ARTIFACT** | it is put skew: put IV 14.89% at −2.6% OTM vs flat call side 12.56–12.91% | `smile.py` |
| 36 | Wider spreads pay better | **MEASURED** | total P&L rises 50→300 wide (645→784), then falls at 400. Worst case improves monotonically | `width_sweep.py` |
| 37 | Active management beats static | **MEASURED** | STATIC total 689.6, worst −269.1. H50-R+resell total 591.1 but **worst +9.7** | `mgmt_sweep.py`, `roll_backtest.py` |
| 38 | Rolling in the direction of spot helps | **MEASURED** | up-rolls +6.4 pts/roll, down-rolls −9.2 (1.44× asymmetry). A down-roll is a loss-crystallisation event by construction | `spread_roll.py`, `bullput_roll.py` |
| 39 | Regime conditioners pick good bull-put entries | **RETRACTED** | down-days-in-5 spread 0.0993 vs null median 0.0646, p95 0.1357 → **p = 0.194**. 3-day drift p = 0.335, last-day move p = 0.302 | `persistence.py` |
| 40 | Bear call is the bearish mirror of the bull put | **DEAD** | bear call −0.0346 mean / 63% win unconditionally, and **−0.0433 in its own bearish regime** vs bull put +0.0219 in its own. Switching (−0.0066) worse than always-bull-put (+0.0011) | `mirror.py` |
| 41 | A stop-loss / cooldown improves the book | **MEASURED** | see result files; cooldown keyed on observable inputs, not realised outputs | `stoploss.py`, `cooldown_chain.py`, `exit_early.py` |
| 42 | Bounce after MAE breach | **SURVIVES (weak)** | mae1 t = −3.70, ΔR² 0.392; mae2 t = −3.99 | `bounce_test.py` |
| 43 | An earnings print should be graded against **implied** EPS growth, not zero | **PRIOR — n = 2, NOT VALIDATED** | Apollo: implied 59.2% vs reported 38.4% → gap −20.8pp, stock −3.49%. Grasim: implied 101.0% vs 51.0% → −50.0pp, −3.79%. Both signs correct, but two observations is a demonstration, not evidence | `backend/quant/news_fundamentals.py` |
| 44 | Valuation state (P/E percentile, implied EPS growth, target upside) explains the cross-section of daily moves | **DEAD (one session, underpowered)** | 13 Aug close, n=49: corr with day return −0.110 (P/E pctile), +0.115 (implied growth), −0.057 (target upside). P/E sign flips to +0.299 over 3 sessions. Richest mover (LT, 75th pctile) rose most; largest drag (ICICI, 25th pctile) was cheap | 2026-08-13, `news_fundamentals.py` |
| 45 | The futures BASIS predicts the index | **DEAD (underpowered)** | NIFTY_FUT_1 vs spot, n=67 daily: r = +0.103 (1d, p=0.43), −0.114 (5d, p=0.57), −0.453 (10d, p=0.11). Terciles non-monotonic (cheap +0.158%, mid +0.508%, rich −0.141% fwd 5d). **Minimum detectable \|r\| at n=67 is 0.239** — this is 'cannot see', not 'nothing there' | 2026-08-13 |
| 46 | The closing auction (CAS, from 2026-08-03) has a directional bias | **PRIOR — 9/9 positive, n far too small** | jumps +201.0, +151.5, +54.5, +8.0, +13.7, +23.6, +21.5, +74.2, +42.5. Mean +65.6, median +42.5. Sign test p=0.002, but 9 sessions of a brand-new mechanism is a settling-in period, not a stable regime | 2026-08-13 |

| 47 | A weight-ordered quality screen (net-profit consistency ≥70%, 5y profit CAGR ≥10%, implied forward growth ≥15%) beats the index | **WEAK — +14.2pp over 5.4y, point-in-time; n = 1 start date** | Walk all 50 by descending weight. Today's data: 15 names, +175.7% vs index +63.9%, excess **+111.8pp** — INVALID, look-ahead. Point-in-time (data ≤ 2021-03-31, two legs only — no historical forward-P/E exists): 15 names, +78.1% (11.3% CAGR) vs +63.9% (9.6%), excess **+14.2pp**, 9/15 beat. **Look-ahead inflation +97.6pp, i.e. 87% of the headline edge was hindsight.** Survivorship uncontrolled (universe is today's Nifty 50); one start date, equal weight, no rebalancing, price return only | 2026-08-14, `data_agent/fundamentals/quality_growth.py` → `quality_growth.json` |

**Bottom line:** the VRP is real and flat across VIX. Nothing tested improves entry timing. Management (rolling, harvesting) improves the *worst case* far more than the mean.

---

## 2. Corrections and retractions

Every one of these was reported as a finding before being found wrong. They are
listed because the failure modes repeat.

| # | claim as first reported | what was actually true | cause |
|---|---|---|---|
| C1 | Crude β (2018–24) = +0.0371 | **+0.0088** | ffill across mismatched US/India holiday calendars manufactured fake zero-return days |
| C2 | VIX model R² = 33.01%, t = −26.6 | **R² = 7.21%** | same-day India VIX in a next-day model. VIX is computed from index options — mechanically inverse to same-day Nifty |
| C3 | Breadth ranged 0–1.7% | 0–100% | base taken from `iloc[0]`, NaN for late-listing symbols. Fixed with `bfill().iloc[0]`. Third instance of this bug class |
| C4 | All 106 gamma-break events "unconfirmed" | mixed | confirmation condition unsigned by break direction; fixed with `(breadth − 50) * dir` |
| C5 | "56% of path movement arrives overnight" | **39% full sample / 71.8% in 2026** | summed \|gaps\| over 6-day windows instead of decomposing squared returns. Also: the covariance term was dropped, so shares summed to 117% — gap and intraday are negatively correlated (−0.145) |
| C6 | Crude is "$9 stale" vs Brent | ~$3.6 | the database carries **WTI (CL=F), not Brent**. See `data_agent/quality/split_mixed_symbols.py` |
| C7 | Wings mispriced vs σ = VIX | no mispricing | India VIX is a model-free variance-swap-style aggregate (1/K² over the OTM strip), **not** a Black-Scholes single-strike IV. Redone by inverting BS per strike on 20,990 LTPs |
| C8 | +28% wing gap = fat-tail premium | **put skew** | see #35 |
| C9 | Buy call wings, they're "free" | they don't protect | see #34 |
| C10 | "Persistence discriminates ~2× better than magnitude" | fails its null at p = 0.194 | claimed before scoring. See #39 |
| C11 | "No anchor exists for the FII futures level" | it was in `.state/flows_cash_cache.json` as `fii_fut_net` | searched the DB and repo root, not `.state/` |
| C12 | 1d close ≠ 1m close is an alignment bug | **NSE methodology** | the official daily close is the last-half-hour VWAP; 1m bars are last-traded price. The two coincide exactly from **2026-08-03** onward, when the daily series began deriving from the 1m series |
| C13 | Daily-bar basis (+117 pts) disagrees with matched-minute (+57) → method bug | **sample period** | the two agree within ~10 pts on identical sessions (r = +0.37). Apr–Jun genuinely had a wider basis |
| C14 | corr(FII futures change, next-month return) = **+0.811** | **−0.515** | `groupby(...).agg('last')` on a frame queried without `ORDER BY` — "last" picked an arbitrary row per month |
| C15 | Drawdown futures P&L = +₹7,473 cr | **+₹5,180 cr** | anchor-free estimate assumed C = 0; actual 2-Jan position was −130,246 |
| C16 | `participant_oi` "has never been collected" | the table existed with 0 rows; backfilled 2026-08-13 | checked table existence, not row count |
| C17 | Portfolio CAGRs of 16.9% (look-ahead book) and 10.0% (walk-forward) | **20.8% and 11.3%** | averaged the per-holding CAGRs instead of compounding the portfolio's own total return. Mean-of-CAGRs is not the CAGR of the mean: +175.7% over 5.37y is 20.8%, not 16.9%. Index CAGR (9.6%) was unaffected, which is why the error survived a sanity check |
| C18 | Annual EPS series were sound because they came from Postgres | **Q1 FY27 sat inside every annual series** | `eps_cagr_backfill.py` had no `time_period = 'yearly'` filter, so a quarter landed in the annual query. TCS `eps_end` read ₹32.70 (one quarter) against ₹136.01 (FY26); every IT major showed a 3y CAGR of −32% to −34% |
| C19 | Stored EPS is a single well-defined series | **wrong by multiples for banks** | `_EPS_LABELS` matched nine variants ('eps', 'basic eps', 'diluted eps', …) with no consistent winner, so different years came from different definitions: HDFC Bank 2.1×, Kotak 6.8×, Bajaj Finance 10.1×. Fixed by DERIVING EPS as net profit ÷ adjusted shares and never reading a stored EPS label |
| C20 | A SQL comment is inert | **`%` in a comment silently killed every query** | the comment read '−32%.'; psycopg3 parsed `%` as a parameter placeholder and the failure was swallowed by `except Exception: continue`. Found by the user's other agent |

**The pattern in C1, C3, C5, C14:** a missing or misaligned value silently replaced
by a plausible one. Each was invisible until an arithmetic identity failed to close.
When a decomposition does not sum to 100%, or a reconstruction does not match a
known level, that is the bug announcing itself — do not round it away.

---

## 3. Data limitations that bound every result

| limitation | effect on conclusions |
|---|---|
| `chain_rows.call_iv / put_iv / call_bid / call_ask / put_bid / put_ask` are **100% empty** | slippage is unmeasurable. Every management result (#37, #38, #41) assumes 1-point slippage rather than measuring it. **Highest-value data fix in the repo.** |
| Option chain covers ~31 days of one calm regime (VIX 11.6–14.7) | the chain cannot contain a shock. Use the chain for execution/pricing; use the 8-year index history for regime questions. Never the reverse |
| `fii_dii_flows` covers **2026-06-18 → 2026-08-12** (37 rows) | FII **cash** flow cannot be tested across the drawdown. All FII conclusions in §1 are **futures only** |
| `participant_flows` is traded VOLUME, `participant_oi` is POSITION | `long − short` in the volume file is the one-day *change*, not the level. Verified: matches the level's daily change on 225/242 days; the 17 exceptions are all monthly expiries (settlement is not a trade) |
| 1-minute futures data starts **2026-06-29** | the wide-basis months (Apr–Jun, up to 30% annualised carry) exist only as daily bars and cannot be cross-validated. #28 rests on this |
| **MARKET-STRUCTURE BREAK on 2026-08-03: NSE launched the Closing Auction Session (CAS)** | Continuous trading now ends 15:15 IST; a 20-min auction sets the official close. Closes before and after that date are NOT methodologically comparable — before, the close was a last-30-min VWAP; after, it is the auction print (which is why the 1d bar and the last 1m bar diverged by up to ±20 pts before 3 Aug and are exactly equal after). Measured auction jump (15:15 print → close): mean \|10.2\| pts before, **mean \|65.6\| pts after, 9/9 POSITIVE (p=0.002), max +201**. On 3 Aug the auction alone was 51% of the day's +390. Any close-based study spanning 3 Aug 2026 inherits this discontinuity |
| **Only ONE `expectation_snapshots.json` capture exists** (2026-08-08), and **zero** earnings events fall after it | #43 cannot be backtested at all. Pre-print consensus is unrecoverable after the fact — it is revised continuously. `data_agent/fundamentals/expectation_snapshot.py` exists but was never added to `sync_all.py`, so it has only ever been run by hand. **Scheduling it is the single change that converts #43 from an untestable story into a measurable hypothesis** |
| Constituent weights in `nifty-50-stock-list.csv` are rounded | bottom-up point contributions carry ~11% residual against the actual index move. Rankings reliable, last decimal not |
| Capture pipeline has stopped twice (2026-08-10, 2026-08-13 at 11:59 IST) | any "how the session closed" read may be a mid-session snapshot. Check `max(ts)` before trusting a same-day number |
| `.state/flows_cash_cache.json` writes `0.0` on fetch failure | for a *net position* zero is a legitimate value, so failures are indistinguishable from real flat books. 2026-07-17 was 0.0; true value −216,528 (recovered from both directions and confirmed by `participant_oi`) |

---

## 4. Open — not yet tested

| # | hypothesis | why it matters | blocked by |
|---|---|---|---|
| O1 | FII **cash** selling predicts returns | the futures book has no foresight; cash might | needs cash history > 37 rows |
| O2 | Carry (futures basis) vs FII position, daily over a year | would separate "structural counterparty" from "directional bet" — currently n = 5 months | now unblocked: `participant_oi` backfilled to 245 days |
| O3 | EPS-revision direction leads the index | `expectation_snapshots.json` captures consensus **before** each print — an unused revision series already in the repo | nothing; ready to run |
| O4 | Does rolling rescue a bear call? | #40 tested static structures; the rolling rules were only testable on the 6-cycle chain window, which contains no bearish regime | needs chain history spanning a decline |
| O5 | Slippage, measured rather than assumed | every management conclusion turns on it | needs bid/ask capture |
| O6 | Does the hurdle gap predict the results-day reaction? | would validate #43, currently a mechanism with n = 2 | needs `expectation_snapshot.py` on the daily schedule, then ~20 prints (≈2 quarters) |
| O7 | Aggregate single-stock futures basis (and its cross-sectional dispersion) as a LEVERAGE gauge for Nifty | single-stock futures are retail/HNI-driven, unlike index futures where FII hold 67.8% of short OI — so the aggregate stock basis measures a different pool from #45 and is not redundant with it | needs `stock_futures.py` downloaded at 1d for 50 constituents × 3 contracts (~37.5k rows/yr); cash leg already present back to 2018 |
| O8 | Add a FLOW event class to the news tagger (index rejig, block deal, passive rebalance) | `event_code` is macro-only (IN_CPI, RBI_MPC, US_FOMC…). Two flow events moved index heavyweights in three days: the Rs 1,908 cr Pilani block in UltraTech, and the MSCI rejig worth ~$523m of outflow in Reliance. Flow events are dated in advance, mechanically sized, and carry no opinion — the highest-quality input the pipeline currently cannot represent | nothing; extend `llm_tag.py` event_code vocabulary + a forward-dated flow calendar |

---

## 5. Standing notes

- **The `cyclical` flag mislabels Dr Reddy's** (patent-exclusivity cliff, not a cycle). Known, unfixed.
- **Two-database rule:** chain for execution/pricing where it is authoritative; 8-year index history for regime and entry-timing questions.
- **Position advice:** this repo produces base rates and factual analysis. It does not produce recommendations on live positions.

---

*Last updated 2026-08-13 (news↔fundamentals bridge added). Add a row when a test finishes — including when it fails.
A register that only contains successes is a register that will re-run every failure.*
