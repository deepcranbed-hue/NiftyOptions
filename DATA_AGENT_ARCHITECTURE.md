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
| `fetching/sync_commodities.py` | yes — `write_daily` (1d) + `save_bars` (1m), 2026-08-08 |
| `macro/download_india_indices.py` | yes — thin `daily_bars` caller, 2026-08-08 |
| `backend/main.py` | n/a — now a thin caller of `sync_all.py`, 2026-08-08 |

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

**3. Route the remaining writers through the store (sync_commodities done
2026-08-08).** It was the last holder of raw `INSERT INTO price_bars`. Converting it
removed three defects rather than three copies of code: a daily DELETE from
2025-07-30 forward that threw away history whenever an MCX contract rolled and the
new key returned only a few weeks (this is the GOLD 249 -> 12 bars incident); two
DELETEs with no `exchange` predicate, which reach across venues and full-scan a
300MB table because the index is keyed leftmost on exchange; and a DELETE comparing
against a `'Z'` timestamp while inserting the unsuffixed form, so the boundary
session was never actually cleared.

`write_daily` is now an UPSERT, not `INSERT OR REPLACE`. REPLACE is a DELETE plus an
INSERT, so any column the statement does not name comes back NULL — a 6-tuple equity
write over a row holding `open_interest` blanked it. `ON CONFLICT DO UPDATE` touches
only the listed columns. A row of 7 carries OI; a row of 6 leaves it alone.

`download_nifty_futures` and `sync_nifty50_to_now` still write 1m via `save_bars`,
which is correct — that is the store for intraday.

**4. Symbol registry.** One JSON or module listing every symbol with its source,
venue, currency and owning job. The seven hardcoded lists become views over it, and
`sync_coverage` stops guessing.

**5. Single entry point (done 2026-08-08).** `data_agent/sync_all.py` is now the
only sync. It holds every step in one ordered table, and each step declares the
credential it needs rather than the run validating all credentials up front.
`/api/sync-all-data` is a thin subprocess caller (229 lines deleted);
`sync_all_auxiliary.py` is a shim that forwards `--only <daily steps>`.

Two things this fixed beyond tidiness. The Breeze token no longer gates the Yahoo
daily bars — no-credential steps run first and a missing token skips only its own
steps. And the Kite validation, which could 400 the entire sync, turned out to set
a `skip_commodities` flag that nothing ever read; commodities go through Upstox.

**6. Verification in the pipeline (done 2026-08-08).** `daily_bar_audit` and
`sync_coverage` are the final phase of `sync_all.py`, and the run exits non-zero
when the audit reports more findings than `AUDIT_BASELINE` (currently 2: GOLD and
COPPER stale, because MCX contracts stop printing when they roll — set it to 0
once that is expressed as an exemption in the audit itself).

A verify step that CRASHES is distinguished from one that reports findings. The
first version of this conflated them and printed "verification passed" over a
`ModuleNotFoundError` — the same shape as the try/except that the old inline audit
used to swallow its own failure with.

## Deliberately out of scope

**Per-contract storage for futures and MCX commodities.** `NIFTY_FUT_1/2` and the
MCX metals each hold one contract's life under a rolling name. The correct model is
one symbol per contract with the continuous series derived at read time. It is real
work, it is not a prerequisite for anything above, and an attempt to shortcut it on
2026-08-08 destroyed 471 bars. Do it deliberately or not at all.

**Postgres vs SQLite.** `data_agent/README.md` says FII/DII is SQLite-only with no
Postgres dependency, but `/api/sync-all-data` writes macro factors "to PostgreSQL".
One of those is out of date. Worth resolving before phase 5 moves those steps.

## Expiry rules have one owner: `fetching/universe.py`

Added 2026-08-08, after nearly shipping a third copy of them.

| question | owner |
|---|---|
| WHICH expiries should we be pulling? | `fetching/universe.py` — pure date logic, offline-testable |
| WHERE does the listed-expiry set come from? | `expiries.py` — asks Breeze, returns the list verbatim |

`universe.py` already encoded every rule the desk uses: `ROLL_AHEAD_DAYS = 2`,
`is_expired()`, `active_future_expiries(n=2)` for the NIFTY_FUT_1/FUT_2 pair, and
`active_option_expiries()` for "current expiry, plus the next once we are within two
days." Three separate places had grown their own versions — `download_nifty_futures`
sorted and sliced `[0]` and `[1]`, `/api/exchange-expiries` filtered `>= today`
inline, and `sync_all` tested `0 <= delta <= 2`. All three now call `universe`.

The copies were not merely redundant, they were weaker. `sync_all`'s signed-delta
rollover would have stopped firing the moment an expiry slipped past today;
`universe`'s asks whether the CURRENT expiry is within the window, which cannot.

**Add a rule to `universe.py`. Add a source to `expiries.py`. Never the reverse.**


## Commodities: the contract is the name

*Established 2026-08-09, after `GOLD` spent five weeks holding option premium.*

**One symbol per contract. The rolling name is DERIVED and never written to directly.**

    GOLD_2026-08-05   one contract, one instrument, forever      <- fetched
    GOLD_2026-10-05   the next contract, stored alongside        <- fetched
    GOLD              ratio-adjusted rolling series              <- generated

A contract series cannot silently become something else, because the expiry is in the
name. `GOLD` can be regenerated at any time, so a bad roll is a rebuild rather than a
restore.

### The rules, and where each lives

| question | owner |
|---|---|
| which contract applies on a date | `fetching/universe.py` — `roll_schedule`, `FUT_ROLL_AHEAD_DAYS = 3` |
| when a contract became FRONT month | previous expiry + 1, from `contract_registry` |
| where the expiry list comes from | `expiries.py` (Breeze) / the Upstox instrument master |
| joining contracts | `continuous.py` — ratio back-adjustment, newest anchored at 1.0 |

Front-month detection is a registry lookup, not a volume heuristic. MCX gold is
bi-monthly, so the front month sits up to two months from expiry and the second month
still trades heavily — volume cannot tell them apart. Only the previous contract's
expiry can. The heuristic remains as a fallback when no earlier contract is known.

Zero-volume bars never enter a derived series. MCX carries the previous price forward
on untraded days; a return computed across two marks is noise. Measured, not assumed:
CRUDEOIL_MCX's correlation with WTI was +0.558 across all bars and +0.985 restricted
to bars that traded.

### The token is the perishable part

Upstox delists a contract at expiry, and the Expired Instruments API **cannot list MCX
expiries** — commodities have no permanent underlying key, so `expiries` returns 400
for any MCX token, live or dead. The only way back to an expired contract is

    MCX_FO|{token}|{DD-MM-YYYY}

which requires the token to have been recorded while the contract lived.

**A recorded token buys back the contract's ENTIRE history**, not just its tail:
`GOLD_2026-08-05` returned 235 daily bars reaching to 2025-09-08. So
`contract_registry.py` runs on every sync, append-only, and that is what makes the
model work at all.

Two operational notes on that API: the path is `/expired-instruments/expiries`
(plural), and Cloudflare rejects requests without a browser `User-Agent`.

**FLOOR: 2026-07-28.** Contracts that expired before that snapshot had their tokens
recorded nowhere and are gone permanently. History grows forward from here.

### Long history comes from elsewhere

MCX daily is months deep and is a basis series, not a backtest. The depth lives in
Yahoo's continuous contracts, which roll and back-adjust themselves:

    GOLD / SILVER / COPPER / CRUDEOIL_MCX     MCX, INR, per contract   basis, execution
    GOLD_USD / SILVER_USD / COPPER_USD        COMEX, USD, ~2,160 bars  backtest
    CRUDEOIL                                  NYMEX, USD, 2,163 bars   backtest

`CRUDEOIL` is named the other way round to the metals and predates the convention.

**Never convert an Indian series into an international one.** The basis is duty plus
GST plus a physical premium — 18.45% on silver before any premium — and it moves.
`quality/data_profile.py` correlates each pair on traded bars with a per-pair floor;
copper's is 0.82 rather than 0.90 because it references LME while ours is COMEX, and
it trades thin.


## The test for any future change

Before adding a writer, three questions:

1. Does a symbol it writes already have an owner? (`sync_coverage.py`)
2. Does it go through the store layer, or raw SQL?
3. Does the audit pass after it runs? (`daily_bar_audit.py`)

Every defect from 2026-08-08 would have been caught by one of those three.
