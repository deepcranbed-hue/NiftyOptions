# Implementation Plan — Data Architecture Hardening (STORAGE LAYER ONLY)

**Goal:** harden the capture/bar storage layer — UTC normalization, snapshot identity,
multi-instrument readiness, scheduler correctness. This is a DATA ARCHITECTURE change
ONLY. All analytics and trading functionality is FROZEN and must produce byte-identical
outputs after this change.

---

## 0. FROZEN — do not modify, do not "improve while you're in there"

The following modules/paths must not change in logic, signature, or output:

- `rnd.py` / `rnd_fixed_patch.py` — RND, skew, expected move
- IV computation and `vol_surface.py`
- Strategy suggester, strike optimizer, payoff/strategy builder
- Portfolio & valuation: `legs_json`, `entry_capture_id`, the `(capture_id, expiry)`
  clean-room in `load_capture`, STALE-not-zero guard, P&L attribution
- Sentiment, sector rotation, complacency gauge, OI burst detector
- All frontend panels (except trivial timestamp display formatting, see 1.4)

`capture_id` remains an INTEGER PRIMARY KEY everywhere. Positions keep pointing at
integer `entry_capture_id`. No key-type changes anywhere.

**Acceptance for this section:** for existing NIFTY data, every analytics module
returns the same values before and after migration (golden-output check, see §8).

---

## 1. UTC normalization (highest priority — do first)

**Problem:** `price_bars.ts` stores IST (`...+05:30`), `captures.captured_at` stores
UTC (`...Z`). SQLite compares these as text → joins/ordering across tables are wrong.

1.1 Create ONE shared helper, e.g. `timeutil.py`:
```python
def to_db_ts(dt) -> str:
    """Canonical storage format: UTC, seconds precision, trailing Z.
    Example: 2026-07-03T09:11:00Z"""
```
Every writer (bar ingestion, capture writer, sweep job) MUST call this. No module
formats its own timestamps. Milliseconds allowed ONLY on `captures.fetched_at`.

1.2 Migration script `scratch_scripts/migrate_to_utc.py`:
- Copy the .db file to a timestamped backup FIRST.
- Scheduler must be stopped before running (assert no writer lock).
- Single transaction: parse every `price_bars.ts` and `captures` timestamp,
  convert to canonical UTC format, rewrite.
- Verify before COMMIT: row counts unchanged; spot-check known instants
  (e.g. the 2026-02-03 gap-day daily bar) map to correct UTC.
- On any verification failure: ROLLBACK and exit non-zero.

1.3 Fold §2 and §3 schema changes into this same migration (one schema-touch window).

1.4 Frontend: parse UTC from API, render in **IST (Asia/Kolkata) explicitly** — not
browser-local timezone. Display-only change.

---

## 2. Snapshot identity on `captures` (ADDITIVE columns)

```sql
ALTER TABLE captures ADD COLUMN exchange_code   TEXT NOT NULL DEFAULT 'NFO';
ALTER TABLE captures ADD COLUMN underlying      TEXT NOT NULL DEFAULT 'NIFTY';
ALTER TABLE captures ADD COLUMN snapshot_minute TEXT;          -- canonical UTC minute
ALTER TABLE captures ADD COLUMN status          TEXT NOT NULL DEFAULT 'complete';
ALTER TABLE captures ADD COLUMN trigger         TEXT NOT NULL DEFAULT 'manual';
-- backfill snapshot_minute = floor(captured_at to minute) for all existing rows, then:
CREATE UNIQUE INDEX ux_captures_snapshot
  ON captures (exchange_code, underlying, snapshot_minute);
```

Semantics (do not deviate):
- `capture_id` = glue: links chain_rows to header. Dumb integer. Never composite.
- `snapshot_minute` = name: the FLOORED SCHEDULER TRIGGER time (13:55:00 trigger,
  13:55:04 completion → snapshot_minute is 13:55). Query-facing key; joins to
  `price_bars.ts` at minute granularity.
- UNIQUE index = law: one snapshot per instrument per minute. A retry/duplicate
  trigger must collide here and be handled (skip + log), never create a second row.
- `status` ∈ {complete, calls_only, puts_only, failed, auth_failed}.
  A partial fetch (one of the two Breeze calls fails) MUST be written with the
  correct partial status — never stored looking complete. Consumers of RND/skew/PCR
  already assume full chains; the loader must serve them only `status='complete'`
  captures (implemented as a WHERE filter in the loader query — no analytics change).

`chain_rows`: NO new columns for underlying/timestamp — rows inherit everything via
`capture_id`. Do NOT denormalize snapshot_minute onto rows. Verify Breeze per-row
`ltt` is persisted in chain_rows (add column if currently dropped — it is market
data, not our clock).

`spot`: store once on the captures header (Breeze repeats it per row; header wins).

---

## 3. `price_bars` key extension (ADDITIVE)

- Extend natural key: `(exchange, symbol, timeframe, ts)`. Backfill existing rows
  with `exchange='NSE'`.
- Keep `INSERT OR REPLACE` semantics — bars are exchange-canonical and overwritable.
  (Contrast: captures are append-only observations. Preserve this asymmetry.)
- Corporate-actions readiness (single stocks like RELIANCE): bars store UNADJUSTED
  prices always. Add empty table
  `corporate_actions (exchange, symbol, ex_date, ratio, action_type)`.
  Adjustment happens downstream at feature time — NEVER by rewriting price_bars.

---

## 4. `instruments` reference table (NEW)

```sql
CREATE TABLE instruments (
  exchange_code TEXT NOT NULL,
  underlying    TEXT NOT NULL,
  lot_size      INTEGER,
  strike_step   REAL,
  tick_size     REAL,
  session_open  TEXT,   -- local exchange time, e.g. '09:15'
  session_close TEXT,   -- e.g. '15:30'  (MCX evening session later: '23:30')
  holiday_calendar TEXT, -- e.g. 'NSE_2026'
  PRIMARY KEY (exchange_code, underlying)
);
-- seed one row: ('NFO','NIFTY', 75, 50, 0.05, '09:15','15:30','NSE_2026')
```
No module is required to consume it yet EXCEPT the session gate (§5). It exists so
lot size / sessions become data, not hardcoded constants, when SENSEX/MCX arrive.

---

## 4b. Capture triggers are MANUAL (no autonomous scheduler)

Chain captures are user-initiated via UI buttons, not a background loop. Two triggers,
both writing the SAME capture path (merge → header → linked rows), distinguished only
by the `trigger` column:

- `trigger='manual'` — discretionary live press (user clicks 5–10×/day on volatile days).
- `trigger='eod'` — end-of-day closing-chain capture, loops tracked expiries.

RULES:
- The EOD button MUST call the LIVE chain endpoint and be run BEFORE market close
  (target 15:15–15:25 IST). After 15:30 the live endpoint returns a frozen chain and
  bid/ask no longer reflect a live instant → degraded for RND. Label the UI button
  "Capture closing chain (run before 15:30)".
- There is NO historical-chain reconstruction path in this phase. Do not wire EOD to
  the per-contract historical endpoint (lossy: premium OHLC, no bid/ask).
- `UNIQUE(exchange_code, underlying, snapshot_minute)` handles a manual press and the
  EOD loop colliding on the same minute → second one skips cleanly (no duplicate).
- IRREGULAR CADENCE: presses land at arbitrary times; some days only the EOD capture
  exists. Every time-series consumer (IV percentile, PCR trend, OI evolution) MUST
  iterate `ORDER BY snapshot_minute` over whatever captures exist — NEVER assume fixed
  spacing or a fixed count per day. (This is a read-pattern requirement on existing
  modules' queries only; no analytics logic changes.)

## 5. Session gate (applies only if an autonomous loop is later added)

NOTE: with manual-only triggers (§4b), the session gate is OPTIONAL for chains — a
user pressing at 16:00 just gets a frozen chain. Keep the gate logic available for a
future auto-loop, and OPTIONALLY warn in the UI if a live-chain press happens outside
09:15–15:30. The bar sync (§5.6) needs no gate — it's backfill.

5.1 Session gate: `is_market_active(instrument) -> bool`
- Weekday check + session window from `instruments` + STATIC holiday list
  `NSE_HOLIDAYS_2026` (~15 dates, hardcoded constant; holiday check is NOT optional).
- Exclude pre-open: no captures before 09:15 IST.
- Design: check every minute and skip if closed (simple), NOT "sleep until next
  trading morning" (avoids weekend/holiday wake-time math and stale-token traps).

5.2 Wall-clock alignment: capture cadence is CONFIGURABLE, default 5 minutes.
Replace sleep-after-completion with epoch alignment (correct for any period):
```python
period = capture_interval_minutes * 60   # 300 for 5m default
await asyncio.sleep(period - time.time() % period)
```
Triggers land on clean :00/:05/:10 boundaries. `snapshot_minute` = floored trigger
time (never completion time) and stays MINUTE-resolution format regardless of
cadence — no separate bucket format; shortening the period later is config-only.
Chains are unrecoverable (no historical chain endpoint), so each missed capture
loses `period` minutes of chain history permanently — reliability of this loop
(status writes, auth handling, no silent gaps) is the priority.

5.3 Overlap guard: `asyncio.Lock`; if previous capture still running when the next
minute fires → SKIP with a logged warning. Never run two capture jobs concurrently.

5.4 Auth path: Breeze session tokens expire daily. First capture of the morning must
refresh/validate auth; on failure write a `status='auth_failed'` capture row (or
alert) — never silently retry forever.

5.5 Scope note: §5.1–5.4 apply to the CHAIN capture loop only (chains are live-only
and unrecoverable, so they need the schedule). Bar downloads are USER-TRIGGERED
(button press) and incremental — see 5.6. No post-close sweep required for bars:
the overlap rule in 5.6 self-heals forming bars on the next press.

5.6 Bar sync (user button press, 1m + daily only) — incremental watermark pattern:
- Watermark = `SELECT MAX(ts) FROM price_bars WHERE exchange=? AND symbol=? AND
  timeframe=?` (scoped by ALL THREE; instant on the PK index; no state table).
- Range = `watermark − 5 min → now`. NEVER re-download the full series.
- First press for a new (instrument, timeframe) → bootstrap constants:
  daily = 1 year back, 1m = 7 days back (per-timeframe constants, not hardcoded
  inline).
- Chunk requests at ≤1000 candles (`get_historical_data_v2` cap). An EMPTY chunk
  (weekend/holiday range) must ADVANCE to the next window — never retry the same
  window (infinite-loop bug).
- The 5-min overlap + INSERT OR REPLACE makes the press idempotent and self-healing:
  the forming last bar from the previous press is overwritten on the next press,
  regardless of how much time has passed.
- Timezone conversion happens ONLY at the API boundary: DB watermark (UTC) →
  Breeze request format; Breeze response → `to_db_ts()` → DB. The DB never stores
  non-canonical timestamps.

---

## 6. Loader query audit ("latest capture" scoping)

Grep all loaders for latest-capture queries (`ORDER BY captured_at DESC LIMIT 1`
shapes). Each becomes:
```sql
WHERE exchange_code=? AND underlying=? AND status='complete'
ORDER BY snapshot_minute DESC LIMIT 1
```
with defaults `('NFO','NIFTY')` so current single-instrument behavior is IDENTICAL.
This is a query-layer change only; function signatures gain optional params with
defaults; no caller changes required now.

---

## 6b. UI — required fields per action (show only what the action needs)

Common instrument selector (drives all actions):
- `exchange_code` (NFO/NSE/BSE/MCX) · `underlying`/`symbol`. Lot size, strike step,
  tick, session hours are LOOKED UP from the `instruments` table, not entered.

Action A — Manual live chain capture (`trigger='manual'`):
- Inputs: instrument (fixed NIFTY/NFO for now), tracked-expiry set (multi-select of
  expiries to loop). Optional: strike range / count of strikes around ATM.
- On press: for each expiry → 2 calls (call+put) → merge → one capture row.
- Show after: snapshot_minute, spot, status (complete/partial), #rows, #expiries.
  A partial capture must be visibly flagged, not silent.

Action B — EOD closing chain capture (`trigger='eod'`):
- Same inputs as A; button labeled "Capture closing chain (run before 15:30)".
- Show a soft warning if pressed after 15:30 IST (data will be frozen, not live).

Action C — Underlying bar sync (button press, incremental):
- Inputs: instrument, timeframe (1m / daily). No date range field — range is derived
  from the watermark (§5.6). Optional "force bootstrap" toggle for first-time / re-pull.
- Show after: last watermark before, new watermark after, #bars added, #chunks,
  #empty windows skipped.

Field-display rules:
- Never ask the user for anything derivable (DTE, lot size, tick, range bounds) —
  compute or look up.
- Timestamps display in IST; storage is UTC (§1).
- Illiquid strikes (ltp=0 / empty ltt) shown as low-reliability, not blank/zero.

## 7. Explicit non-goals (do not build now)

- No DuckDB/Parquet export, no medallion silver/gold builders (future phase).
- No MCX/SENSEX/equity ingestion — only readiness (columns, instruments table).
- No expiry_type / days_to_expiry columns on raw tables (feature-layer concern).
- No changes to any FROZEN module in §0.

---

## 8. Acceptance criteria

1. All stored timestamps in `price_bars` and `captures` are canonical UTC
   (`...Z`, seconds precision); mixed-offset rows: zero.
2. `SELECT` joining captures.snapshot_minute to price_bars.ts on a known minute
   returns the matching bar.
3. Attempting to insert a second capture for the same
   (exchange, underlying, minute) fails on the unique index and is skipped cleanly.
4. Kill the puts fetch mid-capture (fault injection) → capture written with
   `status='calls_only'`; RND/analytics loader ignores it and serves the previous
   complete capture.
5. Scheduler fires at :00 boundaries; no captures outside 09:15–15:30 IST or on a
   listed holiday; concurrent trigger is skipped with a warning.
6. Bar sync button: with existing data, a press fetches only `MAX(ts) − 5 min → now`
   for that (exchange, symbol, timeframe) — verified via API call logs, no full
   re-download. Pressing twice in a row changes zero rows. A press after a
   multi-day gap fills the gap via chunked requests, skipping empty
   weekend/holiday windows without stalling. First press on a new instrument
   backfills exactly the bootstrap depth.
7. GOLDEN-OUTPUT CHECK: RND (skew, expected move), IV, strategy suggester outputs,
   and portfolio P&L for the live positions are numerically identical pre/post
   migration on the same NIFTY data. Any diff = migration bug, not a feature.
8. Backup file exists; migration is rerunnable (idempotent) and rolls back on
   verification failure.
9. Manual and EOD captures both write the correct `trigger` value; a same-minute
   collision between them is skipped by the unique index (no duplicate).
10. Time-series consumers read `ORDER BY snapshot_minute` and produce correct output
    on an irregular day (e.g. 3 manual presses + 1 EOD) with no fixed-cadence
    assumption.
11. Single-stock scope: `price_bars` accepts a stock symbol (e.g. RELIANCE) under the
    exchange-scoped key and syncs incrementally like NIFTY. Stock-OPTION chains, IF
    added, route through the SAME live `captures` path as NIFTY (no EOD reconstruction).
    Corporate-actions table exists; bars stored UNADJUSTED.

## Sequencing
backup → stop scheduler → migration (UTC + §2 + §3 + §4 schema, one transaction)
→ verify → golden-output check → deploy scheduler changes (§5) → loader audit (§6)
→ resume scheduler.
