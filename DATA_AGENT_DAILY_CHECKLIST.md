# Data Agent — daily runbook

*Rewritten 2026-08-09. The previous version passed a `kite_access_token`, asked you to
look up the expiry by hand, and drove `/api/data-agent/sync`. None of that applies:
Kite is gone from the repo, expiries resolve themselves, and there is one command.*

## The command

```bash
cd /Users/deepak/antigravity/NiftyOptions
python data_agent/sync_all.py --breeze-token <SESSION_TOKEN>
```

That is the whole sync — and since 2026-08-17 it genuinely is. The two follow-up steps
that used to be manual are now part of the plan, and they run in OPPOSITE directions
because the two stores do:

  `[mirror]`     Drive -> local. Refreshes the repo-local SQLite mirror that
                 `backend/quant/*` and other readers use. Refuses if the mirror is
                 NEWER than Drive (someone wrote to the read-only copy — C37) or if a
                 hot journal means the source is mid-transaction.
  `[pg-backup]`  local -> Drive. Dumps macro + fundamentals, which live on localhost
                 and which git cannot hold.

Both sit after every write and before the audits. You should no longer need to run
`cp` or `pg_backup.sh` by hand; `python3 data_agent/quality/backup_audit.py` says so
independently.

> **Run it in the MORNING.** Breeze publishes the `1day` bar for a session in an
> overnight batch around 23:30-00:00 IST — verified 2026-08-17 by querying the
> endpoint directly and getting bars terminating at 08-14 while 1m bars for 08-17
> were already stored. The `stock-futures` step therefore collects the PREVIOUS
> session. An afternoon run fetches nothing new and reports success. A missed day is
> **unrecoverable** — Breeze serves no history for settled contracts — so
> `data_agent/freshness.py` treats the futures table as blocking and allows exactly
> one session of lag before calling it overdue.
 No expiry argument, no second broker token, no endpoint.
Without a Breeze token it still runs everything that does not need one.

Useful variants:

```bash
python data_agent/sync_all.py --dry-run          # plan only, nothing fetched
python data_agent/sync_all.py --only sectors,nifty50
python data_agent/sync_all.py --skip chains
python data_agent/sync_all.py --no-verify        # skip the audit (not recommended)
```

## Pre-flight, when something upstream has changed

Both are read-only and take seconds. Run them after any change to an expiry source
or an instrument mapping — that is the class of change that has broken this pipeline
most often, and both failures were invisible until a full run failed.

```bash
# Which NIFTY expiries does Breeze return, and which would we use?
./data_agent/breeze_env/bin/python data_agent/expiries.py --token <TOKEN>

# Which MCX contract does each commodity resolve to?
./data_agent/breeze_env/bin/python data_agent/fetching/sync_commodities.py --resolve-only
```

## What runs, in order

Steps declaring no credential run FIRST, so an expired Breeze token cannot block the
Yahoo daily bars. A missing credential skips its own steps; it never aborts the run.

| # | step | needs | notes |
|---|---|---|---|
| 1 | `sectors` | – | NIFTY* sector indices + BANKNIFTY |
| 2 | `nifty50` | – | 50 constituents + NIFTY |
| 3 | `banks` | – | |
| 4 | `it` | – | |
| 5 | `finnifty` | – | |
| 5a | `ai-infra` | – | 38 AI-infra theme names; list read from `ai_infra_theme.json` |
| 6 | `crude` | – | CRUDEOIL, WTI in USD (CL=F) |
| 7 | `metals-usd` | – | GOLD_USD / SILVER_USD / COPPER_USD (COMEX, 2018→) |
| 8 | `commodities` | Upstox | MCX per contract + rolling series, USDINR, GIFTNIFTY |
| – | `india-indices` | – | **not run** — all 5 symbols owned by steps 1/2/8 |
| 9 | `breeze-1m` | Breeze | Nifty 50 + indices, 1m |
| 10 | `futures` | Breeze | NIFTY_FUT_1 / NIFTY_FUT_2, 1m |
| 11 | `fo` | Breeze + keys | futures and option contract bars |
| 12 | `chains` | Breeze | option chain captures |
| 13 | `macro` | – | US10Y / NASDAQ / CRUDE from FRED → Postgres |
| 14 | `india-rates` | – | IN10Y_INDEX → Postgres |
| 15 | `us-stocks` | – | ACN / CTSH / CRM / INFY_ADR → Postgres |
| 16 | `flows` | – | FII / DII → Postgres |
| 17 | `audit` | – | integrity gate, **baseline 0** |
| 18 | `coverage` | – | ownership and orphan check |

Two interpreters, deliberately. Steps 1–12 use `data_agent/breeze_env` (yfinance,
breeze_connect); 13–16 use `breeze_env` (psycopg, dotenv). They are
not interchangeable — flattening them silently broke every Postgres step once.

Credentials come from `.env` (gitignored): `BREEZE_API_KEY`, `BREEZE_API_SECRET`,
`UPSTOX_ACCESS_TOKEN`, `DATABASE_URL`. The session token is the only thing passed on
the command line, because it changes daily.

## How expiries are chosen

Every rule lives in `data_agent/fetching/universe.py`. Nothing else decides these.

**NIFTY futures — near + next, always two.** `active_future_expiries(n=2)`. The first
two unexpired contracts become NIFTY_FUT_1 and NIFTY_FUT_2.

**NIFTY options — current, plus the next once inside `ROLL_AHEAD_DAYS` (2).** So three
days before expiry you capture one series; two days before, you capture two, and the
next series is already building when the current one rolls off.

**Expired series are never requested.** The exchange has no data for them, so the
quality checker must not flag their absence either.

Expiries come from Breeze via `data_agent/expiries.py`, which converts Breeze's
`25-Aug-2026` into the canonical `2026-08-25T06:00:00.000Z` at the vendor boundary.
`universe.py` stays pure date logic and never learns a vendor's format.

> **Known limitation.** Breeze's option-chain endpoint enumerates only the *current*
> expiry when you do not name one, so the options list arrives with a single entry and
> the 2-day rollover cannot fire. `FONSEScripMaster.txt` holds 7. Chains can be
> captured retrospectively, so this costs the pre-roll head start, not the data.

## How MCX commodity contracts roll

Different mechanism, because these are stored per contract.

- **One symbol per contract.** `GOLD_2026-10-05` means one instrument forever. The
  bare name `GOLD` is DERIVED and never written to directly.
- **Front month** starts the day after the previous contract expires — a lookup in
  `contract_registry.py`, not a volume guess. MCX gold is bi-monthly, so the front
  month sits up to two months from expiry and the second month still trades heavily;
  volume cannot tell them apart.
- **Leaves each contract `FUT_ROLL_AHEAD_DAYS` (3) before its expiry.** The last
  sessions before an MCX expiry are thin.
- **Ratio back-adjustment** at each roll, measured on the last date both contracts
  traded, so returns are continuous. The newest segment is never scaled — the front of
  the series is always real traded prices.
- **Zero-volume bars are excluded.** MCX carries the previous price forward on
  untraded days; a return across two marks is noise.

Rebuild the derived series at any time:

```bash
./data_agent/breeze_env/bin/python data_agent/fetching/continuous.py --apply
./data_agent/breeze_env/bin/python data_agent/fetching/continuous.py --timeframe 1m --apply
```

## The AI-infrastructure theme

Three moving parts, and they must stay in step.

**`ai_infra_theme.json`** is the curated dataset: 38 companies, 11 segments, five
hypotheses, dated evidence, a 3-month lean and a 12-month buy/hold/sell grade per name.
Edit it by hand. Everything else reads from it.

**Bars.** The `ai-infra` sync step reads its symbol list *from that JSON*, so adding a
company to the theme is enough to start collecting its price history — there is no
second list to update. After a split or bonus, re-run that name with `--full`, because
an incremental run only rewrites the tail and leaves a scale break at the join:

```bash
./data_agent/breeze_env/bin/python data_agent/fetching/sync_ai_infra_bars_yf.py --full --only E2E
```

Two names need this right now: **E2E** (1:10 split, ex-date 2026-06-05) and
**TDPOWERSYS** (subdivision approved 2026-08-12, not yet effective).

**Calls.** `ai_infra_theme.json` holds only the CURRENT call; revising a stance
overwrites the previous one. `ai_infra_call_log.py` is the append-only record that makes
the view falsifiable — run it *after every edit to the theme file*, or the call you just
replaced is gone for good:

```bash
python ai_infra_call_log.py                    # record today's calls
python ai_infra_call_log.py --list TDPOWERSYS  # what did we say, and when
```

Idempotent — re-running adds nothing. The realized return is deliberately NOT stored; it
is computed at read time from `price_bars`, because a stored return is wrong the next day
and nobody notices.

**The company page** at `/intel/ai-infra/<SYMBOL>` (same pattern as
`/intel/nifty50/<SYMBOL>`, opened from the symbol in the theme table) joins the three:
evidence aged against the framework's decay table, the hypotheses each name is
load-bearing for, and every past call scored over the window it was actually live.
Served by `/api/ai-infra-company/{symbol}` — read-only over the JSON files and
`price_bars`.

## Verify

```bash
python data_agent/quality/daily_bar_audit.py     # integrity; must PASS
python data_agent/quality/sync_coverage.py       # every symbol has one owner
python data_agent/quality/data_profile.py        # what each series is FIT FOR
```

The first two ask "is this broken". The third asks "what can I trust this for", and
that is the one that catches instrument-mapping errors — its cross-venue correlations
would have caught the GOLDTEN mix-up, the option contamination and the untraded marks,
all three of which passed the integrity audit cleanly.

Do not raise `AUDIT_BASELINE` above 0 in `sync_all.py` to quiet a finding. It sat at 2
for a day and was covering for a wrong instrument key.

## Typechecking the frontend

`tsconfig.json` sets `allowJs: true` with no `include`, so `tsc -p tsconfig.json` walks
`node_modules` and dies of heap exhaustion before reporting anything. Use the scoped
config — same options, `src` only:

```bash
node --max-old-space-size=4096 ./node_modules/typescript/lib/tsc.js \\
     --noEmit -p tsconfig.typecheck.json
```

17 pre-existing errors in `PriceChartPanel.tsx` (a lightweight-charts `Time` type
mismatch) are the current baseline; anything beyond that is new.

## After the sync

Copy the database to the local mirror, which is what most tooling reads:

```bash
cp "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db" \
   /Users/deepak/antigravity/NiftyOptions/option_chains.db
```

## Fundamentals, FII, and Participant Positioning Backfills

Apart from the standard daily market data sync, there are three key fundamental and flow backfill datasets stored across SQLite and PostgreSQL:

### Databases & Schema

1. **SQLite (`option_chains.db`)**:
   * **`participant_flows`**: Stores daily trading volumes (flows) for Client, DII, FII, and Pro participants.
   * **`participant_oi`**: Stores EOD open interest (standing position book) for all participants.
   * **`india_macro_history`**: Stores historical CPI inflation rate series, available-from watermarks, and interbank rates.
2. **PostgreSQL (`niftyoptions`)**:
   * **`fundamentals.financials`**: Financial statements history (balance sheets, P&L, quarterly results).
   * **`fundamentals.shareholding`**: FII and DII shareholding patterns.
   * **`macro.fii_dii_flows`**: FII/DII institutional cash flow segments.

### Executable Scripts & Backfill Commands

If you need to sync, backfill, or refresh these datasets, use the following specific Python scripts:

> **The `SQLITE_DB_PATH=...` prefix below is no longer required** (2026-08-17). Those
> scripts defaulted to the repo-local MIRROR and depended on this export to reach
> Drive — forget it once and the write landed in a copy while the run reported
> success. They now resolve through `db_config.resolve_writable_db_path()`, which
> RAISES when Drive is unreachable instead of falling back. The variable is still
> honoured as a deliberate override, so the commands work either way.

#### 1. F&O Participant Volumes & Open Interest (Positions)
* **Daily Flow Sync (Volume)**: Run `data_agent/macro/download_nse_participants.py` (which populates the `participant_flows` table in SQLite).
* **Historical EOD Positions Backfill (OI)**: Run `data_agent/macro/backfill_nse_participants.py` (which populates `participant_oi` in SQLite).
  ```bash
  # Backfill EOD Participant Open Interest (OI) for a date range:
  SQLITE_DB_PATH="/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db" \
    ./breeze_env/bin/python -m data_agent.macro.backfill_nse_participants --from 2025-08-13 --to 2026-08-13 --series oi
  ```

#### 2. India Macro (CPI Inflation & Interest Rates)
* **Sync Script**: Run `data_agent/macro/sync_india_macro.py` (which populates `india_macro_history` in SQLite).
  ```bash
  SQLITE_DB_PATH="/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db" \
    ./breeze_env/bin/python -m data_agent.macro.sync_india_macro
  ```

#### 3. Fundamentals & PE Valuation History
* **FII Holdings Profile Backfill**: Run `data_agent/fundamentals/fii_holding_backfill.py` (which reads from Postgres and writes `fii_holdings.json`).
  ```bash
  DATABASE_URL="postgresql://localhost/niftyoptions" \
    ./breeze_env/bin/python -m data_agent.fundamentals.fii_holding_backfill
  ```
* **PE History Profile Backfill**: Run `data_agent/fundamentals/pe_history_backfill.py` (which reads from Postgres and writes `pe_history.json`).
  ```bash
  DATABASE_URL="postgresql://localhost/niftyoptions" \
    ./breeze_env/bin/python -m data_agent.fundamentals.pe_history_backfill
  ```

