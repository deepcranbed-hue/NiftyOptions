# Data Agent — target architecture and migration plan

*Drafted 2026-08-08, after a day of fixing symptoms. This is the plan to stop them
recurring.*

## The design was already right

`data_agent/fetching/__init__.py` declares the layering, and `walkthrough.md` states
the goal: a **unified** Data Agent, one source of truth, one sync. Neither needs
changing. The problem is that the codebase does not follow them.

    universe   WHAT to fetch (expiry-aware instrument selection)
        |
    broker     WHERE from (Breeze / Kite / Yahoo / Upstox, one normalised Bar)
        |
    store      ONE writer per table
        |
    health     did it land correctly

## What actually exists

Every file that writes `price_bars` today:

| writer | goes through the store layer? |
|---|---|
| `fetching/daily_bars.py` | **is** the store layer for 1d |
| `fetching/sync_nifty50_bars_yf.py` | yes — and so do the other five `_yf` scripts |
| `fetching/backfill_daily_bars.py` | yes (tool, not scheduled) |
| `fetching/orchestrator.py` | no — writes via `bar_store.save_bars` |
| `fetching/sync_nifty50_to_now.py` | no — `save_bars` |
| `fetching/download_nifty_futures.py` | no — `save_bars` |
| `fetching/sync_commodities.py` | no — raw SQL |
| `macro/download_india_indices.py` | no — raw SQL |
| `backend/main.py` | no — raw SQL |

Six writers bypass the store. Every defect found on 2026-08-08 traces to one of
them, and the pattern is identical each time: a writer that does not share the
canonical path reintroduces a convention the canonical path had already fixed.

## The three rules that were violated

**1. One symbol = one instrument, one venue, one currency, one basis.**
`CRUDEOIL` held USD NYMEX and INR MCX bars. `GOLD` holds MCX futures while
`sync_commodities` and a rolled contract fight over it. Two feeds under one name
never merge — `price_bars` keys on `(exchange, symbol, timeframe, ts)`, so they
accumulate side by side and every query silently double-counts.

**2. One writer per (symbol, timeframe).**
`NIFTY`, `NIFTYIT`, `BANKNIFTY`, `INDIAVIX` and `USDINR` are each written by TWO
jobs today — `macro/download_india_indices.py` and the `_yf` scripts — reachable
from two different endpoints. Where both writers share `daily_bars`, the duplication
is merely wasteful. Where they do not, it is corruption: `download_india_indices`
wrote a `Z` timestamp, which would have re-duplicated ~2,117 sessions per symbol on
the next `/api/sync-all-data`.

**3. Writes go through the store, never raw SQL.**
`daily_bars.write_daily()` purges foreign timestamp formats, resolves the exchange
from what is already stored, and applies known vendor corrections. A raw
`INSERT INTO price_bars` gets none of that. Every one of the six bypassing writers
is one `strftime` away from re-creating a defect we have already paid to fix.

## Two entry points that each do half the job

    POST /api/sync-all-data          POST /api/data-agent/run
      Breeze 1m + futures              Breeze orchestrator (1m, chains, F&O)
      commodities (Upstox)             sync_all_auxiliary:
      index dailies (macro script)       commodities, crude USD, sectors,
      F&O contracts                      nifty50, banks, IT, finnifty
      MACRO -> Postgres
      option-chain captures

Overlap: commodities, and the index dailies by a different route. Neither is
complete. `/api/sync-all-data` refreshes no equity or sector daily bars — most of
what this repo's analysis reads.

## Target

**One entry point.** `POST /api/data-agent/run` becomes the only sync. It already
owns the orchestrator; it gains the macro and option-chain steps.
`/api/sync-all-data` becomes a thin alias, or is retired.

**One writer per table.**

| table | writer | used by |
|---|---|---|
| `price_bars` 1d | `daily_bars.write_daily()` | every daily sync |
| `price_bars` 1m | `bar_store.save_bars()` | Breeze/Upstox intraday |
| `fo_price_bars` | `fo_bars.save_fo_bars()` | orchestrator |
| `captures` / `chain_rows` | `chain_store` | chain sync |

**One owner per symbol**, declared in one place rather than scattered across seven
symbol lists. A single registry — symbol, source, exchange, currency, timeframes,
owning job — that `sync_coverage.py` reads instead of parsing each script.

**One verification step**, run at the end of every sync: `daily_bar_audit` +
`sync_coverage`, non-zero exit on findings.

## Migration, in dependency order

Each phase is independently shippable and leaves the system working.

**1. Stop the bleeding (done 2026-08-08).** `sync_commodities` and
`download_india_indices` now write the canonical timestamp. No further duplication
from the next sync.

**2. Deduplicate ownership.** Decide one owner for `NIFTY`, `NIFTYIT`, `BANKNIFTY`,
`INDIAVIX`, `USDINR` — the `_yf` scripts are the natural choice, since they already
use the store. Reduce `download_india_indices.py` to the symbols nobody else owns,
or retire it. *Low risk, removes the largest remaining duplicate-writer surface.*

**3. Route the remaining writers through the store.** `sync_commodities`,
`download_nifty_futures`, `sync_nifty50_to_now` and the `main.py` inline write all
call `write_daily()` for 1d instead of raw SQL or `save_bars`. This is where the
`to_db_ts` daily-shift bug finally dies, because nothing daily reaches it.

**4. Symbol registry.** One JSON or module listing every symbol with its source,
venue, currency and owning job. The seven hardcoded lists become views over it, and
`sync_coverage` stops guessing.

**5. Single entry point.** Move the macro and chain steps into `_do_run`, make
`/api/sync-all-data` an alias, then delete it once nothing calls it.

**6. Verification in the pipeline.** The wrapper already exits non-zero on a failed
child; add the audit as a final step so a sync that corrupts data fails loudly
instead of reporting success.

## Deliberately out of scope

**Per-contract storage for futures and MCX commodities.** `NIFTY_FUT_1/2` and the
MCX metals each hold one contract's life under a rolling name. The correct model is
one symbol per contract with the continuous series derived at read time. It is real
work, it is not a prerequisite for anything above, and an attempt to shortcut it on
2026-08-08 destroyed 471 bars. Do it deliberately or not at all.

**Postgres vs SQLite.** `data_agent/README.md` says FII/DII is SQLite-only with no
Postgres dependency, but `/api/sync-all-data` writes macro factors "to PostgreSQL".
One of those is out of date. Worth resolving before phase 5 moves those steps.

## The test for any future change

Before adding a writer, three questions:

1. Does a symbol it writes already have an owner? (`sync_coverage.py`)
2. Does it go through the store layer, or raw SQL?
3. Does the audit pass after it runs? (`daily_bar_audit.py`)

Every defect from 2026-08-08 would have been caught by one of those three.
