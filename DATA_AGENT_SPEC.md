# Data Agent — Architecture Spec

**Status:** Design (v0.1). The data-health checker (`backend/quant/data_health.py`) is built + tested; the rest below is the plan to approve.
**Goal:** one agent that keeps every instrument you track in sync at 1-minute frequency, skips expired series, downloads from whichever broker you hand it a token for, and tells you — morning and evening, at the top of the sidebar — when data isn't up to the mark.

---

## What already exists (reuse, don't rebuild)

You already have most of the plumbing; the agent is an orchestration + quality + alerting layer on top:

- **Storage:** `bar_store` (`get_stored_symbols`, `get_bar_range`) over `price_bars(exchange, symbol, timeframe, ts, o,h,l,c,v)`.
- **Brokers:** Breeze (ICICI — `breeze_loader.py`, subprocess into `scratch_scripts/breeze_env`, `session_token`) and Kite (`/api/sync-kite-historical`).
- **Scheduler:** `active_schedulers` + `option_chain_sync_loop` + `/api/schedule/start|stop|status` — per-symbol async polling at an interval.
- **Sync endpoints:** `/api/sync-all-data`, `/api/sync-all-constituents`, `/api/save-breeze-chain`, `/api/bars/range`.
- **Exchange routing:** `resolve_exchange(symbol)` → NSE / NFO / MCX / CDS / NSEIX.

The agent coordinates these instead of you calling `/api/schedule/start` per symbol by hand.

---

## Package — `data_agent/` (repo root, importable like `strategy_framework`)

Organized into three concerns, each its own subpackage:

```
data_agent/
  __init__.py
  fetching/                 # WHERE data comes from
    universe.py             #   expiry-aware instrument list to keep in sync
    broker.py               #   one-broker-per-run adapter over Breeze / Kite
    orchestrator.py         #   the cycle: universe -> sync -> health -> retry
  quality/                  # IS the data good
    data_health.py          #   1-min coverage checker            (BUILT + TESTED)
  agent/                    # the local-LLM BRAIN
    local_llm.py            #   parse_intent() via Qwen + keyword  (BUILT + TESTED)
    control.py              #   maps parsed intents to fetch/health actions
    alerts.py               #   morning/evening health -> sidebar payload
```

Built + tested today: `quality/data_health.py` and `agent/local_llm.py` (see
`test_data_health.py`, 16/16). `fetching/*` and `agent/{control,alerts}.py` are the
approved-build-order next steps.

### `universe.py` — what to keep in sync (expiry-aware) — BUILT + TESTED
Builds the live instrument set with your exact expiry rules (14/14 tests, `test_universe.py`):
- **Equities:** the 50 constituents (`nifty-50-stock-list.csv`) + NIFTY index.
- **Futures:** NIFTY **near + next** expiry (always two) — `active_future_expiries()`.
- **Options:** the **current expiry only**, until we're within **2 days** of it (`ROLL_AHEAD_DAYS`) — then **also the next expiry**, so next-series data is already building before the current rolls off — `active_option_expiries()`. The 2-day boundary is exact: expiry−3 = current only, expiry−2 = current + next.
- **Cross-assets:** GOLD, SILVER, COPPER, CRUDEOIL, USDINR, GIFTNIFTY (already in `resolve_exchange`).

**Expiry rule (your requirement):** any series with expiry `< today` is *dropped* — the exchange returns nothing for it, so the agent never requests it and `data_health` never flags its absence. When the current expiry passes, `active_option_expiries` rolls "current" forward automatically. `is_expired(expiry, today)` is the same predicate the `data_health.coverage_report(is_expired=…)` hook accepts. Expiry lists come from the broker instrument master (Breeze/Kite) at runtime; the selection logic itself is pure dates and tests offline.

### `broker.py` — one broker per run (your requirement)
A thin adapter with a common interface (`fetch_1m(symbol, exchange, day)`, `fetch_range(...)`), backed by either Breeze or Kite. Key rules:
- The user supplies **one broker's token per run**. If only a Breeze token is given, the agent downloads via Breeze only; if only Kite, via Kite only. (Answer chosen: **user picks the broker per run**, so the start command names the broker + token.)
- Token lives in the agent's **in-memory session state only** — never written to disk/DB (security). It clears on restart; the agent asks for it again.
- If a requested instrument isn't offered by the chosen broker, it's reported as `UNAVAILABLE_ON_BROKER`, not a data gap.

### `orchestrator.py` — the loop
One coherent cycle instead of many manual schedules:
1. `universe.build()` → active, unexpired instruments.
2. For each, ensure today's 1-min bars exist: reuse `option_chain_sync_loop` / the sync endpoints via `broker.py`; backfill missing history through `/api/bars/range` gaps.
3. Run `data_health.coverage_report(is_expired=universe.is_expired)`.
4. **Re-pull** any `DEGRADED`/`NO_DATA` symbol-days (bounded retries, then give up and flag).
5. Persist the latest health snapshot (`.state/data_health.json`) for the sidebar + alerts.
Runs as a managed async task (same pattern as `active_schedulers`), started/stopped on command.

### `control.py` — manual + natural language (your requirement)
Parses user intent into actions. Examples:
- "**start downloading with my breeze token `<tok>`**" → `start(broker=breeze, token=…)`
- "sync TCS and its options" → `sync(symbols=[TCS], include_options=True)`
- "stop the data agent" → `stop()`
- "**is the data up to the mark?**" → `health()` → returns `data_health.summary`
- "backfill last 5 days for NIFTY" → `backfill(NIFTY, days=5)`
NL parsing can route through your existing Qwen tagger (a tiny intent+slots prompt) with a keyword fallback, mirroring the news pipeline's local-LLM approach. Manual buttons call the same underlying actions.

### `alerts.py` + sidebar badge (your requirement)
- A **scheduled task** runs the health check **pre-open (morning)** and **post-close (evening)**; each calls `data_health.alert_message(report, when="Morning"/"Evening")`.
- The payload (`level: ok|warn|alert`, `headline`, `detail`, `flagged[]`) drives a **`DataHealthBadge`** pinned at the **top of the sidebar** — green when all symbols are complete, amber for a few gaps, red when many. Clicking expands the flagged symbol-days.

---

## Data-health checker (built) — how "not up to mark" is decided

Per (exchange, symbol, day) it checks three things and returns the loudest status:

- **COVERAGE** — `stored bars ÷ expected bars` for the session; below 95% → `DEGRADED`, zero on an open day → `NO_DATA`.
- **FREQUENCY MAINTAINED** — it inspects the actual **bar spacing**, not just the count. If the dominant gap between bars ≠ the symbol's expected frequency → `WRONG_FREQ`. This catches 5-min data masquerading as 1-min, or clustered/irregular bars that still add up to ~375.
- **GAPS** — holes larger than one interval mid-session → `GAPS`.

**Per-symbol user-defined frequency (your requirement).** Every symbol is judged at **1-minute by default**, but you can override any symbol in `.state/data_freq_config.json` (`{"SOMEILLIQUID": 5}`). The checker then judges that symbol against *your* frequency — flagging `WRONG_FREQ` only when the data doesn't match what you defined, tagged `freq_source: "user"` so the alert reads e.g. *"TCS — spacing ~1m but expected 5m (user-defined)."*

Pre-open auction bars (09:07 IST) are excluded via a per-exchange **session window** (`NSE 03:45–10:00 UTC`) so a normal pre-open boundary isn't mistaken for a hole. On your current DB it catches the real gap — **NIFTY 2026-06-29 = 3/375 (1%) → DEGRADED** — while full 07-01…07-03 days pass clean. `alert_message()` turns any of these into the sidebar payload.

---

## API endpoints to add

```
POST /api/data-agent/start     {broker: "breeze"|"kite", token, symbols?, interval?}
POST /api/data-agent/stop
GET  /api/data-agent/status     -> {running, broker, universe_size, last_cycle}
GET  /api/data-agent/health     -> data_health.coverage_report(...)  (sidebar badge polls this)
POST /api/data-agent/command    {text}   -> control.py NL/manual action
POST /api/data-agent/backfill   {symbol, days}
```
All strategy-style responses run through the same numpy/NaN JSON sanitizer already added for the strategy routes, so the health payload never 500s.

---

## Security & correctness notes
- **Tokens are session-only**, never persisted; the agent re-prompts after restart.
- **No scraping** — data comes only through the broker APIs you authenticate to.
- **Expiry-safe:** expired options/futures are excluded from both download and health, so a rolled-off series never shows as "missing."
- **Timestamps UTC in DB, IST at the UI boundary**, consistent with the rest of the system.
- Health is **read-only over `price_bars`** — it never blocks or slows the trading path.

---

## Build order (once approved)
1. `data_health.py` ✅ (done + tested).
2. `universe.py` with the expiry predicate.
3. `broker.py` adapter (Breeze first, Kite second) + `/api/data-agent/start|stop|status`.
4. `orchestrator.py` cycle + re-pull of degraded symbols.
5. `/api/data-agent/health` + `DataHealthBadge` at the top of the sidebar.
6. `alerts.py` + morning/evening scheduled task.
7. `control.py` natural-language commands (Qwen intent + keyword fallback).

## Open choices for you
- Coverage threshold (95% now) and per-exchange expected minutes — tune to your vendor.
- Alert times (exact "morning"/"evening" clock times, IST).
- Whether to also add the deeper quality checks later (staleness, intraday gaps, flatline) — the checker is structured to extend into these.
