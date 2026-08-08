# Archived Upstox equity syncs

**These are retired. Do not run them as-is.** Kept for reference because the Upstox
plumbing in them is sound and may be worth reusing.

Archived 2026-08-08, during the daily-bars consolidation.

## What they are

| File | Wrote |
|---|---|
| `sync_bank_bars.py` | 12 BANKNIFTY constituents, daily, from Upstox `NSE_EQ` |
| `sync_it_bars_upstox.py` | `LTIM` only, daily, from Upstox `NSE_EQ` |
| `sync_finnifty_bars.py` | 10 FINNIFTY constituents, daily, from Upstox `NSE_EQ` |

## Why they were retired

**They are duplicate writers.** `sync_bank_bars.py` targets the *identical* 12
symbols as `sync_bank_bars_yf.py`. Same for FINNIFTY. `price_bars` has one row per
`(exchange, symbol, timeframe, ts)`, so two vendors writing one symbol do not
produce a better series — they produce a series whose provenance depends on which
job ran last.

**The price basis differs.** Upstox serves raw traded prices. The `_yf` scripts use
`auto_adjust=True`, so their bars are split- and dividend-adjusted — which is why
RELIANCE opens 2018 at ~405 rather than its ~918 traded price. Running these would
put 12 bank names on a different basis from the other 84 symbols in the table. That
is the same defect that made CRUDEOIL jump 84x in a day, at twelve times the scale.

**`sync_it_bars_upstox.py` is doubly unsafe.** It `DELETE`s a symbol's full history
before writing, and it only ever handled `LTIM` — it was a hand-patch for a gap
whose real cause was found later: NSE renamed the symbol `LTIM` -> `LTM` on
2026-02-27, so `LTIM.NS` 404s. That is fixed properly in
`daily_bars.TICKER_ALTS`, which now tries `LTM.NS` first. Running this script today
would delete 2,135 correctly adjusted bars and, because it looks `LTIM` up in
Postgres, probably fail to find a key anyway.

## What was salvaged

`sync_finnifty_bars.py` resolved Upstox instrument keys **dynamically** from the
instrument master (`assets.upstox.com/.../complete.csv.gz`, filter `NSE_EQ|`, match
on `tradingsymbol`) rather than hardcoding them. That pattern is now in
`sync_commodities.resolve_mcx_keys()`, where it fixes the real bug it was always the
answer to: hardcoded `MCX_FO|` keys point at ONE contract and go silent when it
expires, which is why GOLD stalled at 2026-08-04 and COPPER at 2026-07-30.

## If you ever revive one

1. **Fix the paths.** They compute `REPO_ROOT` as two levels up from the file. They
   are now three levels deep, so that resolves wrong.
2. **Decide the basis first.** Either adjust Upstox prices for corporate actions, or
   store them under a distinct symbol (the `CRUDEOIL` / `CRUDEOIL_MCX` pattern:
   one symbol = one instrument, one venue, one currency, one basis).
3. **Route the write through `daily_bars.write_daily()`**, which purges foreign
   timestamp formats and resolves the exchange from what is already stored. Writing
   raw SQL is how the duplicates got created in the first place.
4. **Run `data_agent/quality/daily_bar_audit.py` afterwards.** It catches all of the
   above.

## Live Upstox paths (NOT archived)

- `sync_commodities.py` — GOLD, SILVER, COPPER, CRUDEOIL_MCX, USDINR, GIFTNIFTY
- `data_agent/fundamentals/download_fundamentals.py`
- `data_agent/macro/download_fii_dii.py`, `ingest_india_rates.py`

Known loose end: they all import `upstox_auth` from `scratch_scripts/` via a
`sys.path` append. A live daily job depending on a scratch directory is fragile —
worth relocating, but it touches four `data_agent` modules and nine scratch scripts,
so it was left alone rather than done carelessly.
