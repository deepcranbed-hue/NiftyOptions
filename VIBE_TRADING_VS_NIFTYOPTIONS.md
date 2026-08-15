# Vibe-Trading vs. NiftyOptions — Comparison & Reuse Assessment

*Prepared 2026-07-21. Compares the vendored open-source `Vibe-Trading-main/` (HKUDS Vibe-Trading, PyPI `vibe-trading-ai` v0.1.11, MIT) against your own NiftyOptions Quant Engine + Strategy Desk framework. The lens is practical: **what, if anything, can we pull into the NiftyOptions engine.***

---

## 1. Bottom line up front

They are **not competing products** and Vibe-Trading is **not a drop-in replacement** for your engine. They solve different problems:

- **NiftyOptions** is a purpose-built, single-underlying **NIFTY index-options desk**. Its entire edge lives in **real option-chain data** — actual OTM call/put LTPs, real OI walls, real skew — captured intraday at minute resolution, with weekly+monthly expiries, and turned into a Risk-Neutral Distribution, a directional-momentum regime read, priced multi-leg structures, and a no-lookahead walk-forward backtest with real ₹20/leg costs and desk-style position management.
- **Vibe-Trading** is a broad, multi-market **LLM research/agent framework** for equities/futures/forex/crypto. It backtests on **daily OHLCV bars**, ships 460+ cross-sectional alpha factors, an MCP/CLI agent surface, 12 broker connectors with live-trade safety rails, and multi-agent "swarm" research teams.

The single most important finding for your question: **Vibe-Trading's options engine does not use real option chains.** It *synthesizes* theoretical option prices from the underlying via Black-Scholes with an IV-smile approximation (`agent/backtest/engines/options_portfolio.py`), and its India support (`india_equity.py`) is **cash-equity delivery only** — the file explicitly states "F&O lot sizes are not modelled here." So it cannot reproduce, let alone improve on, the core of what NiftyOptions does. Your real-chain RND/OI/skew pipeline is the thing that makes NiftyOptions valuable, and Vibe-Trading has no equivalent.

**Recommendation:** keep NiftyOptions as the core. Treat Vibe-Trading as a **parts donor and reference design** — borrow specific, well-isolated components (backtest-metrics rigor, factor analysis, live-trade safety architecture, some data loaders, and ideas) rather than adopting or migrating onto it. Details and a ranked reuse list below.

---

## 2. What each system is

### NiftyOptions (yours)
A decision-support quant engine with two layers:

- **Quant engine** (`backend/quant/`) — fuses Gemini-tagged news + real option chains into a market regime, sector-sentiment bias, an extracted RND (from real OTM call/put LTPs), and suggested option structures (credit spreads, iron condors). FastAPI backend + React/Vite frontend. Strong provenance discipline (PRIMARY/FALLBACK/STALE), formula traceability, and hard invariants (RND needs put LTPs, sentiment computed once, closed sector vocabulary, event-proximity blocks on premium-selling, etc.).
- **Strategy Desk framework** (`strategy_framework/`) — ~2,800 lines that turn minute-level signals into a directional-momentum NIFTY-options suggestion and **walk-forward backtest** it. Regime classifier (TREND/RANGE/NO_TRADE) → priced structures → desk-style management (roll/defend/convert/stop) → cost-net metrics with an MPS0 max-profit benchmark. A single signal registry, index-weighted constituent-volume reconstruction, forecast-driven action optimizers (options + futures, advisory-first), and a mixed-instrument portfolio book.

Design values: **no lookahead, costs always charged, PRIOR-until-calibrated (≥60 sessions), single source of truth, relative signal (not a calibrated EV predictor).** Data is tiny and India-specific (one `option_chains.db`, ~1.3 MB, NSE CSV + ICICI Breeze).

### Vibe-Trading (vendored open source)
A "personal trading agent" framework (HKUDS, MIT license). Highlights from its own docs/code:

- **8 backtest engines** (ChinaA, GlobalEquity, IndiaEquity, Crypto, ChinaFutures, GlobalFutures, Forex, + a synthetic options engine), all on daily bars, with a serious metrics/validation layer (look-ahead guards, strict-OOS, turnover, Monte-Carlo permutation, cost stacks).
- **Alpha Zoo** — 460+ cross-sectional factors (qlib158, alpha101, gtja191, academic, fundamental) with IC/IR benchmarking, an AST purity gate, and lookahead sentinel tests.
- **23 market-data loaders** with ordered fallback (yfinance, stooq, yahoo, OKX/CCXT, akshare/baostock/tencent/sina/eastmoney, tushare, Longbridge, MT5, SEC EDGAR, local CSV/parquet, QVeris, etc.).
- **Agent surface** — MCP server (54 tools) + CLI + FastAPI web UI, 88 finance skills, 30 multi-agent "swarm" teams, a Shadow Account loop, scheduled research.
- **Live-trade safety** — 12 broker connectors behind a mandate gate, kill-switch, fail-closed defaults, and an audit ledger; read-only by default. Includes read-only India broker bridges (Shoonya/Dhan) for cash equities.

Design values: agent-first, multi-market, security-hardened, community-contributed. Backtesting is **cross-sectional / daily-bar equity-style**, not intraday options-chain.

---

## 3. Side-by-side

| Dimension | NiftyOptions (yours) | Vibe-Trading |
|---|---|---|
| **Primary purpose** | NIFTY index-options decision desk | Multi-market research/trading agent |
| **Instruments** | NIFTY options (weekly+monthly), NIFTY futures, constituent stocks | Equities, futures, forex, crypto; options only *synthetically* |
| **Options data** | **Real chain LTPs** (NSE CSV + ICICI Breeze), real OI, real skew | **Synthetic** BS prices from underlying + IV-smile approximation |
| **Time resolution** | Intraday, **minute bars**, as-of snapshots | **Daily OHLCV** bars |
| **RND / skew** | Extracted from real OTM call/put LTPs, calibrated & renormalized | None (no real chain) |
| **Backtest** | Event-driven, no-lookahead walk-forward, ₹20/leg, desk management, MPS0 ceiling | Vectorized daily engines, strict-OOS, MC permutation, turnover, cost stacks |
| **Factors/alphas** | ~15 bespoke directional-momentum signals (one registry) | **460+** cross-sectional alphas + IC/IR bench |
| **India market** | Native NIFTY F&O (core competence) | Cash-equity delivery only (`.NS`/`.BO`); **no F&O** |
| **News/sentiment** | Gemini tagging → sector tree → regime bias (a real strength) | Generic news headlines tool; not sector-attributed to an index |
| **Live execution** | None (advisory/decision-support only) | 12 connectors, mandate-gated, kill-switch, audit ledger (mostly read-only) |
| **Agent/automation** | FastAPI routes + React panels | MCP (54 tools) + CLI + swarm teams + scheduled research |
| **Data scale** | ~1.3 MB, one expiry, single underlying | Multi-market, many loaders, cache layer |
| **Stack** | FastAPI + React/Vite, SQLite, numpy/scipy optional | FastAPI + React 19, langchain/langgraph, duckdb, scipy/sklearn |
| **License** | Private/yours | **MIT** (permissive — reuse-friendly with attribution) |

---

## 4. The architectural gap that matters

Your engine's value is **market microstructure at the option-chain level, intraday**. Vibe-Trading's value is **cross-sectional factor research on daily equity bars, at breadth, wrapped in an agent**. The two barely overlap where it counts:

- Vibe-Trading has **no real options chain ingestion** and **no intraday** path. Its options engine is a teaching-grade BS synthesizer — fine for illustrating a covered call on daily AAPL bars, useless for pricing a real NIFTY iron condor off Thursday's actual put LTPs.
- Its India engine is **cash delivery** (T+1, circuit bands, STT/stamp/GST stack) — genuinely nice code, but it models buying RELIANCE.NS, not trading NIFTY F&O.
- Conversely, your engine has **no factor zoo, no cross-sectional IC/IR tooling, no multi-market breadth, no agent/MCP surface, and no live-execution safety layer** — because it never needed them.

So this is a "borrow parts, don't merge" situation. A migration would throw away your core and rebuild it worse.

---

## 5. What you can actually reuse (ranked)

Ranked by value-to-effort. All of Vibe-Trading is MIT, so lifting code is legally fine **provided you keep the MIT `LICENSE`/`NOTICE` attribution** for any files or substantial snippets you copy.

**1. Backtest metrics & validation rigor — highest value, low risk.**
`agent/backtest/metrics.py` (~14 KB), `validation.py` (~16 KB), and the run-card/benchmark modules encode strict-OOS handling, turnover, drawdown, non-finite sanitization, and a Monte-Carlo permutation test. Your `strategy_framework/backtest/metrics.py` is already honest and cost-net, but you could adopt the **permutation/MC significance test** and the **NaN/Inf-safe JSON normalization** as ideas (or lift the functions directly). This strengthens your "descriptive-only until 60 sessions" story with an actual significance gate. *Integration: pure functions on returns arrays — copy into `strategy_framework/backtest/`, no dependency drag.*

**2. Factor analysis + Alpha Zoo on your 50 constituents — high value, medium effort.**
Your breadth/heavyweight-leadership signals already lean on constituent behavior. Vibe-Trading's `factor_analysis` (IC/IR, layered backtest) and the **alpha101 / qlib158** zoos are explicitly tagged for the `equity_in` (NSE/BSE) universe, so they compute on your Nifty-50 daily bars out of the box. This could feed **new cross-sectional signals** into your directional blend (e.g. constituent momentum/reversal factors as an input to `heavyweight_leadership` or a new SignalSpec). *Integration: run it offline as a research tool first; only promote a factor to a `SignalSpec` after it clears your PRIOR/attribution bar. Respect HARD RULE 13 (one registry).*

**3. Live-trade safety architecture — high value if/when you go executable — reference design.**
If NiftyOptions ever moves from advisory to placing orders (even paper), Vibe-Trading's `agent/src/trading/` (mandate gate, kill-switch, fail-closed, audit ledger, read-only default) and the **Shoonya/Dhan India connectors** are a proven blueprint — and Shoonya/Dhan are the right brokers for NSE F&O. Your `AGENT_CONTRIBUTOR_GUIDE.md`-style discipline maps cleanly onto their high-risk-surface rules. *Integration: adopt the pattern (mandate → gate → audit) rather than the code at first; the connectors are cash-equity-oriented and would need an F&O order path.*

**4. Data loaders & provenance patterns — medium value.**
Your `ARCHITECTURE.md` flags empty `global_cues` / `realized_metrics` / `minute_bars` tables forcing signals onto fallbacks. Vibe-Trading's loader layer (ordered fallback, completeness checks that **fail closed instead of silently shrinking the universe**, OHLC sanity guards at the loader boundary, `local` CSV/parquet loader) is a clean model for hardening your ingestion. The **fail-closed partial-fetch** discipline in particular is worth copying into your NSE/Breeze sync. *Integration: pattern-level; your data shapes differ.*

**5. Options pricing / Greeks reference — low/medium value.**
`options_portfolio.py`'s `bs_price` / `bs_greeks` are clean, well-guarded (handles T≤0, σ≤0, non-positive S/K) implementations. You already solve IV from LTP via `bs.py`, but their Greeks aggregation and edge-case guards are a useful cross-check for your payoff/analytics engine. *Integration: reference/verify against your own; don't replace real-chain pricing with synthetic.*

**6. Multi-agent "swarm" research patterns — low value now, interesting later.**
The 30 swarm presets (investment committee, risk committee, quant desk) are an agent-orchestration idea more than reusable code for you. Could inspire a future "desk review" agent over your suggestions, but not a near-term reuse.

---

## 6. What NOT to reuse / replace

- **Do not replace your real-chain RND/OI/skew pipeline with Vibe-Trading's synthetic options engine.** It would be a strict downgrade — synthetic BS prices discard exactly the microstructure your edge depends on.
- **Do not adopt its daily-bar backtest engines for the options desk.** Your walk-forward is intraday and management-aware; theirs is daily and cross-sectional. Different problem.
- **Do not migrate the project onto Vibe-Trading.** You'd inherit a large langchain/langgraph/duckdb dependency surface and lose your DRY single-registry discipline for no core-capability gain.
- **Mind the dependency and isolation rules.** Your `SKILL.md` "Environment Isolation Rule" warns that careless `pip install` (e.g. breaking `cryptography`) crashes Uvicorn. Vibe-Trading pulls a heavy dep tree (langchain 1.x, langgraph, duckdb, weasyprint, ccxt…). Any borrowed code should be **copied as standalone functions into an isolated module**, not installed as a dependency.

---

## 7. Suggested path

1. **Lift the metrics rigor first** (Section 5.1) — smallest, safest, immediately strengthens your backtest credibility with an MC significance test and NaN/Inf-safe outputs.
2. **Trial the factor analysis offline** (5.2) on your Nifty-50 daily bars as a *research* tool; promote a factor into a `SignalSpec` only if it clears your attribution/PRIOR bar.
3. **Bookmark the safety architecture** (5.3) as the reference design for the day NiftyOptions goes executable, with Shoonya/Dhan as the F&O broker path.
4. **Harden ingestion** (5.4) using their fail-closed / completeness-check pattern to fill your empty cross-asset tables.
5. Keep everything else (synthetic options, daily engines, swarm) as **reference only**.

---

## 8. Deep dive: News & Sentiment (can we reuse anything?)

**Verdict: for the core news→sentiment→sector pipeline, no — NiftyOptions is already more advanced than anything Vibe-Trading ships.** Vibe-Trading's news/sentiment code is generic and equity/crypto-oriented; your engine is a disciplined, index-attributed sentiment machine. Adopting their sentiment *code* would be a downgrade. There are, however, three or four smaller, well-isolated things worth lifting.

### What Vibe-Trading actually has

| Component | What it is | Verdict vs yours |
|---|---|---|
| `agent/src/tools/stock_news_tool.py` | Plain headline fetcher — Eastmoney (China A-share) + Yahoo (US/HK). Returns `title/url/source/published/snippet`. | **Weaker.** No India, no sentiment score, no sector attribution. |
| `agent/src/skills/sentiment-analysis/SKILL.md` | Methodology doc (Chinese): fear-greed index, PCR, margin financing, northbound flow, social sentiment. Contrarian framing. | Knowledge, not code. A couple of borrowable *ideas*. |
| `agent/backtest/loaders/rsshub_events.py` | RSSHub-backed event provider with **point-in-time discipline** + pluggable scorer; normalizes to `date,event_type,score,source,summary`, attaches a PIT-safe `event_score` to price bars. | Best-engineered piece. Borrow the *pattern*, not the lexicon scorer. |
| `agent/src/skills/event-driven/SKILL.md` (+ `example_signal_engine.py`) | Methodology: event CSV → time decay → combine with technical signal. | You already do momentum decay; confirms your design. |
| `agent/src/skills/social-media-intelligence/SKILL.md` | Framework for extracting retail/social sentiment from Twitter/X, Reddit, Telegram, Discord. | A *new dimension* you don't have (see below). |

### Why yours is already ahead

Your `news_provenance.py` (source-trust tiering, prompt-injection/junk quarantine, foreign/crypto-noise relevance filter, cross-feed dedup, weighted-median), `sector_tagging.py` (tier-weighted median, low-confidence flags, `__audit` trail), and `sector_tree.py` (3-level Sector→Industry→Company with exact Nifty-50 weights and alias resolution), plus half-life news decay and Gemini tagging, together form a more disciplined and index-attributed sentiment engine than Vibe-Trading's lexicon scorer + generic headline fetch. Their model produces a per-*stock* headline list; yours produces a per-*sector*, weight-attributed, provenance-tagged bias that feeds the regime read.

### Borrowable items (ranked)

**1. Point-in-time "knowability" rule (`rsshub_events.py`) — correctness, low effort.**
Their rule: a publication after the close rolls to the *next* trading session's knowable date, and only items with `knowable_date <= as_of` are ever returned. You already enforce no-lookahead as-of joins (D-MA-01) in `strategy_framework`, but confirm your news pipeline applies the **after-close → next-session** roll for backtest alignment. If it doesn't, adopt the rule so after-hours news can't leak into the session it was published in.

**2. RSSHub as a feed catalog — highest practical value, low effort.**
You're already RSS→Gemini, so pointing at a self-hosted RSSHub instance simply widens your source set (many more India business/markets feeds) with no change to your tagging path. Sketch of the slot-in:

```
# Your existing pipeline (unchanged):
#   RSS feeds  →  get_tagged_news()  →  news_provenance hygiene  →  Gemini tag  →  sector_tagging  →  news_state.json
#
# Add RSSHub as just more feed URLs at the ingestion edge:
RSSHUB_BASE = os.getenv("RSSHUB_BASE_URL")  # self-hosted instance
EXTRA_FEEDS = [
    f"{RSSHUB_BASE}/moneycontrol/news/business",
    f"{RSSHUB_BASE}/livemint/markets",
    f"{RSSHUB_BASE}/nseindia/announcements",   # example routes — verify against RSSHub docs
]
# Merge EXTRA_FEEDS into your current RSS list BEFORE news_provenance dedup/quarantine.
# Nothing downstream changes: your tier-weighting, relevance filter, and Gemini
# tagging handle the new items exactly like existing feeds.
```

The only new infra is a self-hosted RSSHub container; the provenance layer already dedups and quarantines, so a noisier source can't hurt signal quality. Keep RSSHub routes in `config` (single source of truth), not hardcoded in the fetcher.

**3. PCR + VIX contrarian matrix (sentiment-analysis skill) → `complacency.py` — an idea.**
A concrete 2×2 you can graft onto data you already compute:

| | VIX low | VIX high |
|---|---|---|
| **PCR high** | Hedging demand (institutions protecting longs) | Capitulation → bullish-reversal tell |
| **PCR low** | Complacency → black-swan / pin risk | Conflicting — needs confirmation |

You already have PCR (from OI walls), India VIX, and `complacency.py`; this just formalizes the corner cases as a complacency refinement. Tag it `PRIOR` like everything else until it clears attribution.

**4. Optional NEW dimension — social/retail sentiment (speculative).**
You have **no** social-sentiment input today. Vibe-Trading's `social-media-intelligence` skill is a framework for quantifying retail chatter across Twitter/X (`$TICKER` cashtags, FinTwit), Reddit (r/wallstreetbets, r/options — "abnormal options chatter"), Telegram, and Discord. It treats retail sentiment mainly as a **contrarian indicator at extremes** (herd euphoria near tops, capitulation near bottoms) and quantifies four axes: discussion heat vs baseline, bullish/bearish ratio, sentiment intensity, and rate-of-change. For NIFTY this would mean an *India-specific* retail-froth overlay — X cashtags (`$NIFTY`, heavyweight tickers), Moneycontrol forums, Telegram trading groups — surfaced as a contrarian gate near sentiment extremes (e.g. dampen momentum-following when retail euphoria spikes).

Why it's speculative, not a quick win: (a) the skill's sources and tooling are US/crypto-centric — you'd need to source and validate Indian retail data yourself; (b) social feeds are noisy and manipulation-prone (pump groups, bots), which **cuts against your existing "drop foreign/crypto noise" discipline** in `news_provenance.py`; (c) it's a genuinely new data-ingestion + scoring subsystem, not a snippet. If pursued, gate it hard (contrarian-only, extremes-only) and keep it out of the core blend until it proves edge — exactly your PRIOR/attribution model.

### What NOT to borrow

- `stock_news_tool.py` — strictly weaker than your pipeline (no India, no attribution, no scoring).
- The composite 0–100 sentiment score with fixed China-market weights — wrong thresholds, contrarian, not sector-attributed.
- The default lexicon scorer in `rsshub_events.py` — your Gemini tagging is better; borrow the PIT *wrapper*, not the scorer.

### Net for news/sentiment

Keep your engine. The only meaningful pulls are the **PIT after-close roll** (correctness check), **RSSHub for broader India feeds** (easy win), and the **PCR+VIX matrix** into `complacency.py` (cheap refinement). Social/retail sentiment is a real gap but a project, not a borrow.

---

---

## 9. Deep dive: Signals & Backtesting (the "so many" question)

**Verdict: the ~462 "signals" are mostly not for you, and the backtest *engine* is a downgrade — but a few backtest *reporting* ideas were worth lifting, and have been.**

### The signals are cross-sectional equity factors, not index/options signals

Vibe-Trading's "Alpha Zoo" is ~462 factors, but each one is a `compute(panel)` that takes a wide table (rows = dates, columns = stocks) and returns a score *per stock per day*, then ranks stocks against each other to go long the top / short the bottom. They are **daily frequency** and need a **universe of stocks**. That's a different paradigm from NiftyOptions (single underlying, intraday minute bars, option-chain microstructure). Inventory:

| Zoo | Count | What it is | Usable on NIFTY? |
|---|---|---|---|
| alpha101 | 101 | Kakushadze formulaic alphas (rank/ts_argmax/signed_power…) | Tagged `equity_in` → runs on Nifty-50 daily bars, but it's stock-*selection* |
| qlib158 | ~154 | Qlib Alpha158 rolling features (ma/std/beta/roc/corr/rsv/… × windows 5/10/20/30/60) | Feature inputs, not standalone signals |
| gtja191 | 191 | Guotai-Junan short-period alphas | **China-only** — not tagged for India |
| academic | ~12 | Fama-French (SMB/HML/RMW/CMA/MKT), Carhart momentum, BAB, 52-week-high, Amihud illiquidity, return-skew, short-term reversal | Standard risk factors (price-based proxies) |
| fundamental | 4 | PIT earnings yield, ROE, gross profitability, asset growth | Needs a fundamentals feed |

Caveat on the headline count: it's inflated. qlib158 is ~31 feature families swept across 5 windows; academic is textbook risk factors. So it's a few dozen ideas plus parameter sweeps, not 462 distinct edges. A handful of constituent factors (momentum, reversal, illiquidity, beta) *could* feed your `breadth_oi` / `heavyweight_leadership` as a daily research input on the Nifty-50 — an experiment, not a plug-in — but the whole zoo cannot replace an intraday single-index options desk.

### The backtest engine is theirs' weakness, not yours

Your `strategy_framework` walk-forward is **already better for your purpose**: event-driven, no-lookahead (D-MA-01 as-of joins), real ₹20/leg on entry/exit/every adjustment, take-profit/stop-loss brackets, desk-style management (roll/defend/convert), and the MPS0 perfect-hindsight capture% — a *more* sophisticated benchmark than anything in Vibe-Trading. Their 8 engines are daily-bar, cross-sectional equity; they don't model intraday option structures at all. Nothing in the engine is worth adopting.

### The backtest *methods/reporting* worth borrowing (and what was done)

- **P&L attribution by exit reason and by structure family** — their `by_exit_reason_stats` / `by_symbol_stats` group P&L by *why* a trade closed and *what* it was. Your `summarize()` pooled everything into one hit-rate. **✅ Implemented:** `strategy_framework/backtest/metrics.py` now returns `by_exit_reason` and `by_family` (net ₹, hit-rate, n, cost per bucket, sorted worst-first), and `walkforward.py` now tags every trade with an `exit_reason` (`TAKE_PROFIT` / `STOP_LOSS` / `CLOSE` / `EXIT` / `EXPIRY_MARK` / `EXPIRY_SETTLE` / `HORIZON`). The Desk panel (`DeskStrategyView.tsx`) renders both tables in the auto-backtest results.
- **`max_consecutive_loss`** — the worst losing streak, a risk stat plain drawdown doesn't convey. **✅ Implemented:** added to `summarize()` and shown as "Worst losing streak" in the Desk panel.
- **Per-run reproducibility "run card"** (`run_card.py`) — writes a JSON+MD card stamping config hash + signal-code hash + data sources + metrics per run. Fits your provenance/audit culture. *Not yet done — candidate.*
- **Buy-and-hold NIFTY benchmark alongside MPS0** — answers "did trading this beat just holding the index?", complementary to MPS0's "% of available move captured". *Not yet done — candidate.*

### What was deliberately skipped

Monte-Carlo permutation test and bootstrap Sharpe CI (you weren't interested); Sortino/Calmar (you intentionally don't annualize on thin data — correct); execution-turnover and the exposure-cap / causal-rebalance machinery (built for daily multi-asset books, irrelevant to a single options structure).

---

*Licensing: Vibe-Trading is MIT. Copying files or substantial snippets is permitted; retain the MIT license text and attribution (`LICENSE` / `NOTICE`) in any module you derive from it, and note the origin in a header comment consistent with your DRY conventions.*
