# Daily bars pipeline — what changed and why

*2026-08-08. Covers the rebuild of the daily (1d) equity bar pipeline.*

## The short version

Daily bars arrived by two routes that disagreed about how to name a session.
`price_bars`' primary key is `(exchange, symbol, timeframe, ts)`, so two spellings
of the same trading day are **two different rows**, and the feeds never overwrote
each other — they accumulated. Everything below follows from that one fact.

```
Yahoo   ts '2018-01-01T00:00:00'    split+dividend adjusted   full history
Breeze  ts '2025-08-06T00:00:00Z'   raw traded prices         ~1 YEAR ONLY
```

There is now **one writer** for daily equity bars, one timestamp convention, and a
check that runs every sync.

## The defects, in the order they were found

**1. Two symbols stuck at 246 bars.** TATAMOTORS and ZOMATO held one year of
history while the other 48 held eight. Not the sync watermark — a different vendor.
Breeze caps daily history at ~1 year.

**2. Why they fell to Breeze at all.** `sync_nifty50_bars_yf.py` had an inverted
ticker map: `{'ETERNAL': 'ZOMATO', 'TMPV': 'TATAMOTORS'}`. `ZOMATO.NS` and
`TATAMOTORS.NS` are the **retired** tickers and return zero bars; the live ones are
`ETERNAL.NS` and `TMPV.NS`. Those two names never received Yahoo data, so the
Breeze path was the only thing filling them. This was the root cause.

**3. They were written under names nothing reads.** The same script stored them as
DB symbols `ETERNAL` / `TMPV`, while the constituents CSV, the Nifty 50 view,
`nifty50_drivers.json` and `earnings_reactions.json` all key on `ZOMATO` /
`TATAMOTORS`. Two companies, four series. The orphans were purged.

**4. `save_bars` corrupts daily timestamps.** It routes `ts` through
`backend/timeutil.to_db_ts`, which treats a naive timestamp as IST and converts to
UTC. Correct for a minute bar. For a daily bar — whose identity *is* its trading
date — `2018-01-01 00:00 IST` becomes `2017-12-31T18:30:00Z`: the session moves to
the previous calendar day and every Monday lands on a Sunday. 427 of TATAMOTORS'
2,126 bars ended up weekend-dated. **Daily bars must not go through `save_bars`.**

**5. yfinance returns a tz-aware index for `.NS`.** Taking the date off a
UTC-expressed timestamp shifts it back a day. Convert to `Asia/Kolkata` *first*.
The old script used `tz_localize(None)`, which happens to work only while yfinance
returns IST.

**6. NIFTY held 22 duplicated sessions** — the same day stored once by Yahoo at
`T00:00:00Z` and once by Breeze at `T03:45:00Z` (09:15 IST). NIFTY is the benchmark
in `earnings_reaction_backfill.py`, so every relative return double-counted them.

**7. Yahoo mis-dates the Trent bonus.** Trent's 1:2 bonus had record/ex date
**2026-06-04**. Yahoo applies the adjustment from **2026-01-01**, leaving every
earlier bar at raw scale — a 33% cliff on a day with no corporate action, and no
discontinuity on the day there was one. This is the vendor's data, not a stitching
artifact: a full `--replace` re-download reproduced it byte for byte. It made
Trent's 1Y return read **−42.1%** instead of **−13.1%**.

**8. The daily job rebuilt everything, every day.** `sync_nifty50_bars_yf.py` ran
`DELETE` then re-downloaded from 2018 for all 50 on every run. Slow, and it
silently reverted any correction held in the database — a repaired series lasted
exactly one night.

**9. The live sync still wrote Breeze daily bars.** `backend/main.py` runs
`data_agent/fetching/sync_nifty50_to_now.py`, which still contained the Breeze
daily block. The next UI sync would have re-created the duplicate series.

## Where it ended up

| | before | after |
|---|---|---|
| Audit findings | 47 | **2** |
| Timestamp conventions | 3 (`T00:00:00`, `...Z`, intraday) | **1**, across all 96 symbols |
| Duplicate sessions | 13 symbols, up to 2,117 each | **0** |
| Symbols forked across exchanges | CRUDEOIL, USDINR | **0** |
| Sector index history | 251 bars (1 year) | **~2,110 (2018→)** |
| Sector-relative event coverage | 323/1,633 (20%) | **1,323/1,633 (81%)** |

The two remaining findings are GOLD and COPPER staleness — see *Still open*.

## What the pipeline is now

| File | Role |
|---|---|
| `data_agent/fetching/daily_bars.py` | Single owner of fetch / write / verify for 1d bars. Ticker map, listing dates, vendor fixes, known real gaps. |
| `data_agent/fetching/sync_nifty50_bars_yf.py` | **The one daily writer.** Incremental, Yahoo, 50 constituents + NIFTY. Run by `sync_all_auxiliary.py`. |
| `data_agent/fetching/sync_nifty50_to_now.py` | Breeze — **1-minute bars and futures daily only.** No equity daily. |
| `data_agent/fetching/backfill_daily_bars.py` | Deep backfill, verification, dedupe, orphan purge. Not part of the daily run. |
| `data_agent/fetching/sync_{sectors,bank_bars,it_bars,finnifty_bars,crudeoil}_yf.py` | Thin callers of `sync_symbols()`. ~42 lines each; no fetch logic of their own. |
| `data_agent/quality/daily_bar_audit.py` | Six integrity checks. Run it after anything touches `price_bars`. |
| `data_agent/quality/split_mixed_symbols.py` | Repairs: `--fold-ts`, `--fold-exchange`, `--drop-intraday`, and the CRUDEOIL currency split. |

Conventions, all enforced in one place:

- **Timestamp**: `%Y-%m-%dT00:00:00`, IST trading date, no `Z`, no conversion.
- **Price basis**: `auto_adjust=True` — split *and* dividend adjusted.
- **Writes**: `INSERT OR REPLACE`, never `DELETE`-then-rebuild.
- **Breeze**: 1-minute, options chains, futures daily. Never equity daily.
- **Upstox**: untouched.

## Running it

```bash
# daily — automatic, via sync_all_auxiliary.py after the Breeze sync
python data_agent/fetching/sync_nifty50_bars_yf.py

# after a split/bonus, when the continuity check names a symbol
python data_agent/fetching/sync_nifty50_bars_yf.py --full TRENT

# coverage / ts format / duplicate dates, fetches nothing
python data_agent/fetching/backfill_daily_bars.py --verify-only --symbols ALL
```

The database lives on Drive; `<repo>/option_chains.db` is a **manual mirror** so
agents without Drive access can read it. It is a copy, not a link — after any write,
re-copy it or analysis reads stale bars. Every tool prints the exact `cp`.

## The invariant that matters

Incremental syncing has one failure mode, and it is the shape of defect #7: a split
makes Yahoo re-adjust the *entire* history at once, while an incremental run only
rewrites the tail — leaving a cliff at the join.

So every sync ends with a **continuity check**: any unexplained single-day gap >15%
is reported as `NEEDS FULL REFRESH` with the exact command. Two suppression lists
keep it honest:

- `VENDOR_ADJUSTMENTS` — the vendor got an action *wrong*; scale the bars (Trent).
- `KNOWN_REAL_GAPS` — an action the vendor correctly does *not* adjust; leave the
  gap, stop flagging it (the Tata Motors demerger). A backstop that fires every day
  is one nobody reads.

## Three renames in eight months

`ZOMATO → ETERNAL`, `TATAMOTORS → TMPV`, `LTIM → LTM` (2026-02-27, LTIMindtree became
LTM Limited). Each one silently hollowed out a series, because **a renamed ticker does
not raise — it returns an empty frame.** The sync writes 19,143 bars for nine names,
zero for the tenth, and reports success.

This is why `TICKER_ALTS` holds candidate *lists* and `fetch_best()` prints which one
won. The loop that caught the third instance in fifteen minutes: the audit flagged LTIM
as stale, the sync named the symbol, a direct probe confirmed all candidates 404'd, and
a search found the rename. Expect a fourth; the mechanism is in place for it.

## Lessons worth keeping

**Verify what landed, not what you sent.** The timezone bug passed its checks
because they ran on the in-memory rows *before* the write, and the write was what
corrupted them. `verify_daily()` now re-reads from the database.

**Equal size does not mean equal file.** The two `option_chains.db` copies are both
exactly 329,240,576 bytes with different inodes. The original guard compared sizes
and stayed silent on precisely the case it existed to catch. Compare
`(st_dev, st_ino)`.

**A clean ratio is not proof of a real action.** The gap detector labelled the
phantom Trent cliff a "1:2 bonus" because the ratio was exactly 0.6667. It was an
artifact. The check that settled it was comparing a stored bar against an
independently reported traded price — external to the pipeline, so it cannot
confirm the pipeline's own error.

**A correct bar count proves nothing.** The shifted TATAMOTORS series had exactly
2,126 bars, matching its peers. Only the calendar-overlap test failed.

## Still open

**MCX series are not continuous contracts.** GOLD, SILVER, COPPER and CRUDEOIL_MCX each
hold one contract's life: they start when it lists, end when it expires, and carry
zero-volume prints with `open == close` in between. Two symptoms follow from the one
cause. The 2026-02-02 "gaps" in gold and silver are rolls, not moves — the preceding
bars are far-month prints at volume 0-2 and the gap day is the next contract's first bar
at volume 83 and 8. And GOLD/COPPER go stale because `sync_commodities.py` holds
hardcoded Upstox instrument keys (`MCX_FO|466583`) pointing at a *specific* contract;
when it expires the key simply stops returning data.

The fix is a dynamic active-contract lookup against the Upstox instrument master plus
roll adjustment. Worth doing with the API in front of you, not by patching keys. CRUDEOIL
already shows the alternative shape: a clean USD NYMEX series for analysis
(`CRUDEOIL`, continuous, from `CL=F`) alongside the tradeable INR contract
(`CRUDEOIL_MCX`).

**`to_db_ts` still shifts daily timestamps** for any other caller of `save_bars`. The
futures path is one. Separate symbols, so no collision today.

**Futures storage is undecided** — `NIFTY_FUT_1/2` hold one contract each rather than a
stitched series. The expiry *selection* is correct (nearest and next-after from Kite's
instrument feed); only the storage question is open: per-contract symbols with a rolling
view derived at read time, versus stitching on roll.
