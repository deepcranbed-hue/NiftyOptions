# NiftyOptions — repo conventions

## MANDATORY: check the hypothesis register before any backtest, study, OR REPORTED DATA PROBLEM

**BEFORE reporting a data defect, mismatch, discrepancy or "problem", scan the symptom
index at the top of `StrategyBacktesting/Hypotheses.md` (§0).** The old trigger for
reading that file was "before any backtest", which is why four already-settled quirks
got re-reported as fresh findings on 2026-08-17 — the file was being consulted before
MODELLING and not before CLAIMING. Most data surprises in this repo are already
answered there. If the symptom genuinely is not listed, say "not in the symptom index"
out loud, and add a row once it is understood.

**A set difference or a threshold applied without the dimension that qualifies it is
the house failure mode.** It has produced a cross-join that omitted
`basis`/`time_period`/`section`, a Postgres table identified from an unqualified
`relname`, a lot-size alarm on ordinary price drift, and a two-store comparison that
could not tell "richer" from "wrong". Before reporting a mismatch, name the dimensions
you joined on and ask which one is missing.

**`StrategyBacktesting/Hypotheses.md` is the single source of truth for what has
already been tested.** Read it before designing a study, running a backtest, or
reporting that some signal predicts something.

It records ~42 tested hypotheses with verdicts, the statistic and the null each was
scored against, and links to the script and `*_result.json` that produced them —
plus a corrections table of 16 findings that were reported and later found wrong.

Three rules:

1. **Do not re-run a DEAD hypothesis** without stating what is different about the
   new test. Re-running tends to resurrect a dead idea: the second attempt finds a
   different threshold and reports it as new.
2. **A test is not finished until its row is in the register** — including when it
   fails. A register of successes only is a register that re-runs every failure.
3. **Follow the method standard in §0** (circular-shift nulls, max-|t| across the
   signal family, Newey-West, non-overlapping windows for multi-day horizons,
   publication lag as part of the signal). A result produced without them is not
   comparable to the rows already there.

Section 3 lists the data limitations that bound every conclusion in the repo —
notably that `chain_rows` bid/ask/IV are 100% empty, so slippage is assumed and
not measured, and that all FII conclusions are futures-only because cash history
is 37 rows.

## MANDATORY: single source of truth (DRY) — check before you write

**Before writing any new function, helper, or utility, search the codebase for
equivalent logic first** (`grep`/`rg` the concept, e.g. "index volume",
"round trip cost", "session VWAP"). If similar logic already exists:

1. Do NOT copy-paste or re-implement it.
2. Extract it into ONE canonical module and import it everywhere it's needed.
3. If your case genuinely needs a *different* computation, add a short comment
   stating why it is not a duplicate of the existing helper.

Shared utilities live in exactly one place and are imported, never duplicated.
Known canonical homes:

- **Contract params (lot size, strike step)**: `exchange_config.py` (repo root) →
  `NIFTY_LOT_SIZE`, `ExchangeConfig`. Every Python module imports the lot size from
  here — never hardcode `65`/`75`. The frontend no longer mirrors it: `CONFIG.lot_size`
  in `src/lib/constants.ts` is a BOOTSTRAP value only, overwritten at startup by
  `hydrateContractParams()` from `/api/strategy/config`. **When NSE revises the lot
  size, change `exchange_config.py` alone** — both tiers follow.
- **Signal roster (all signals, families, default weights)**:
  `strategy_framework/signals/registry.py` → `REGISTRY`, one `SignalSpec` per signal
  (name, compute fn, family, default weight, kind, blended, momentum_boost). The
  bundle, the regime blend (`_DIRECTIONAL`/`_MOMENTUM_FAMILY`), `SignalWeights`, the
  analytics roster (`_DIR_SIGNAL_NAMES`) and the signal-study tool ALL derive from it.
  Adding a signal = write its module in `signals/` + add ONE SignalSpec row. Never
  hardcode a signal list or weight roster anywhere else.
  **This extends across the API boundary.** The SignalSpec also carries the UI's
  `label`, `method` text and `detail_keys`; `registry.roster()` is served by
  `/api/strategy/config` and consumed by the single frontend module
  `src/lib/signalRoster.ts` (`useSignalRoster()`). No `.tsx` file may declare a
  signal name, label map or weight table — IntegrityAgent check
  `frontend_no_hardcoded_signal_roster` fails the build if one reappears. Adding a
  SignalSpec row lights the signal up in every UI view with zero frontend edits.
  (This is what went wrong before: the Signal view held its own 12-entry list while
  the registry had grown to 15 directional, so `futures_basis`/`_calendar`/`_flow`
  were invisible.)
- **Index-volume reconstruction** (`Σ index_weightᵢ × volumeᵢ` per minute — the
  NIFTY index carries no volume of its own): `strategy_framework/signals/index_volume.py`
  → `per_bar_index_volume`. Imported by `technical_momentum`, `vwap`, `rel_volume`.
  (`vol_index` is exempt — it weights per-stock *returns*, a different computation.)
- **Signal math**: reuse the existing engines (`rnd.py`, `skew/`, `global_cues.py`,
  `flows.py`, `sector_map.py`) rather than re-deriving.

Rationale: duplicated helpers drift apart silently — one gets a fix or a units
change and the others don't, producing signals that disagree for no visible reason.
One definition, imported, keeps them consistent by construction.

## Other invariants
See `strategy_framework/SKILL.md` → "HARD RULES" for the framework-specific
invariants (no lookahead, costs always charged, PRIOR-until-calibrated, etc.).


## MANDATORY: one file decides the databases (D-SC-06)

**Never hardcode a database path or DSN. Never `sqlite3.connect()` / `psycopg.connect()`
a literal.**

```python
# market data — chains, price bars, captures        (SQLite, Google Drive)
from db_config import DB_PATH, connect, resolve_db_path
from db_config import resolve_writable_db_path      # downloads / backfills

# macro + fundamentals                              (PostgreSQL, localhost)
from db_config import PG_DSN, connect_pg, resolve_pg_dsn
```

`db_config.py` at the repo root is the only place that decides either store.

**Two stores, split by domain — this is deliberate, not a migration in progress:**

| store | holds | written by |
|---|---|---|
| SQLite on Google Drive | `captures`, `chain_rows`, `price_bars` | the download pipeline |
| PostgreSQL `localhost/niftyoptions` | macro + fundamentals | `data_agent/macro/`, `data_agent/fundamentals/` |

Many `data_agent` scripts touch BOTH in one run — read chains from SQLite, write
fundamentals to Postgres. Do not consolidate one into the other without a decision;
`POSTGRES_MIGRATION_PLAN.md` scopes exactly this.

**THE TWO STORES COPY IN OPPOSITE DIRECTIONS. This is the single easiest thing to get
backwards, and getting it backwards destroys data in both cases.**

| store | authoritative copy | flows | the second copy is |
|---|---|---|---|
| SQLite market data | **Google Drive** | Drive → local | a read-only mirror, refreshed by `cp` |
| PostgreSQL macro + fundamentals | **localhost** | local → Drive | a point-in-time dump, written by `pg_backup.sh` |

Downloads land in Drive for SQLite and in localhost for Postgres, so "which side is fresh"
is reversed between them:

* SQLite — the download writes DRIVE. The repo file is a copy that goes stale the moment
  anything writes to Drive. Refresh it with
  `cp "$DRIVE/option_chains.db" ./option_chains.db`.
  **Copying the other way overwrites the source of truth with a stale mirror.**
* Postgres — the ingest writes LOCALHOST. Drive holds only dumps. Refresh Drive with
  `data_agent/pg_backup.sh`.
  **Copying the other way — restoring a dump over the live database — discards every ingest
  since that dump was taken.**

Each of those mistakes is the CORRECT operation for the other store, which is why naming the
direction beats remembering it. `resolve_writable_db_path()` enforces the SQLite half by
raising rather than falling back (see C37 for what happened when a writer bypassed it); the
Postgres half has no equivalent guard, because localhost is where the ingest belongs — the
exposure there is a careless restore, not a careless write.

**SQLite readers and writers differ, and the difference matters.** A reader may fall
back to the repo-local copy. A writer may NOT: `resolve_writable_db_path()` raises when
the Drive mount is absent, because a download that silently lands in the local copy
manufactures exactly the divergence this rule prevents.

**Do not switch SQLite to WAL.** The file lives on a synced directory; WAL's
`-wal`/`-shm` sidecars are independently uploaded and locked by the sync client, which
is how a synced SQLite database gets corrupted. `connect()` sets a 30s busy timeout and
leaves journal_mode at the rollback-journal default (POSTGRES_MIGRATION_PLAN.md §1.1).

Why a rule and not a preference: the Drive path had been pasted into six places
(`bar_store`, `chain_store`, `backend/quant/fundamentals`, `backend/shock_recovery_routes`,
a sixth resolver in `strategy_framework/config/settings`, and a bare relative
`"option_chains.db"` in `persistence.py` that bound to the working directory), three with
no fallback at all — plus 25 more in `data_agent/`, and the Postgres DSN had two
different defaults in circulation. Same defect class as the three copies of
`skew_engine.py` retired 2026-08-15; `DATA_SOURCES.md` already forbids it for
constituent data.

Check what resolves, for both stores: `python3 db_config.py`.
