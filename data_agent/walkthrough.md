# Walkthrough: Unified Data Agent & Option Chains Sync

We have completed the integration of the **Data Agent** to automatically handle data auditing and unified data downloading across **Cash (Equities/Indices)**, **Futures**, and **Option Chains**.

---

## 📊 Database Architecture & Table Targets

All historical market data is saved into the single source-of-truth SQLite database located at:  
`/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db`

| Asset Type | Supported Resolutions | Table Name | Storage Schema & Key Columns |
| :--- | :--- | :--- | :--- |
| **Cash (Equities & Indices)** | **1-minute** AND **1-day (`1d`)** | `price_bars` | `exchange, symbol, timeframe, ts, open, high, low, close, volume` |
| **Futures (F&O)** | **1-minute** AND **1-day (`1d`)** | `fo_price_bars` | `exchange, underlying, instrument_type='FUT', expiry, strike=0.0, right='', timeframe, ts, open, high, low, close, volume, open_interest` |
| **Option Contract Bars** | **1-minute** Only | `fo_price_bars` | `exchange, underlying, instrument_type='OPT', expiry, strike, right, timeframe='1m', ts, open, high, low, close, volume, open_interest` |
| **Option Chain Captures** | **1-minute Snapshots** Only | `captures` & `chain_rows` | **`captures`**: `capture_id, captured_at, spot, vix, status`<br>**`chain_rows`**: `capture_id, expiry, strike_price, call_ltp, put_ltp, call_oi, put_oi, call_volume, put_volume` |

> [!NOTE]
> **Resolution Policy**:
> * **Cash & Futures**: Downloaded and stored at both **1-minute (`1m`)** intraday resolution and **1-day (`1d`)** daily resolution.
> * **Option Chains & Option Contracts**: Downloaded and stored **EXCLUSIVELY at 1-minute (`1m`) resolution** (option chains are intraday snapshots and do not have `1d` daily chain snapshots).

---

## 🛠 Key Changes Made

### 1. **Option Chain Sync Engine & Rollover Handling**
*   **File**: [orchestrator.py](file:///Users/deepak/antigravity/NiftyOptions/data_agent/fetching/orchestrator.py#L202-L310)
*   Updated `run_option_chain_sync()` to accept single or multiple active expiries.
*   **Rollover Policy**: Integrated with [universe.py](file:///Users/deepak/antigravity/NiftyOptions/data_agent/fetching/universe.py) (`ROLL_AHEAD_DAYS = 2`). When within 2 days of current expiry, the Data Agent automatically fetches option chains for **both current AND next expiries** simultaneously so next-series data builds before the current series rolls off.

### 2. **Unified Data Agent API Endpoints**
*   **File**: [data_agent_routes.py](file:///Users/deepak/antigravity/NiftyOptions/backend/routes/data_agent_routes.py#L1-L50) & [data_agent_routes.py](file:///Users/deepak/antigravity/NiftyOptions/backend/data_agent_routes.py)
*   **Active Expiry Resolution Fix**: Corrected F&O parsing from the scrip master. It now filters out expired expiries (like `2026-07-28`) *before* selecting the next active contract, ensuring the plan always targets valid unexpired expiries (e.g. `2026-08-04` for options and `2026-08-25`/`2026-09-29` for futures).
*   Exposed two unified routes:
    *   `GET /api/data-agent/health`: Audits the freshness and sample sizes across stocks, futures, and option chain captures using `missing_report()`.
    *   `POST /api/data-agent/sync`: Triggers a unified sync for cash, futures, commodities, and option chain captures in a single request. Supports both `1m` and `1d` timeframe resolutions for Cash & Futures.

### 3. **Unified Sync Pipeline Integration**
*   **File**: [main.py](file:///Users/deepak/antigravity/NiftyOptions/backend/main.py#L436-L450)
*   Integrated the Data Agent F&O target sync inline inside `api_sync_all_data`. Executing a sync from the frontend now simultaneously handles Equities, Commodities, GIFT Nifty, Index Futures, Option Contract Bars, and Option Chain Captures.
*   **Daily (1d) Upstox Sync**: Upgraded [sync_commodities.py](file:///Users/deepak/antigravity/NiftyOptions/scratch_scripts/sync_commodities.py) to download both `1m` and `1d` resolutions for indices and commodities using the corrected Upstox `day` interval parameter.

---

## ✅ Verification Results

1. **Option Chain Backfill**:
   * Executed `fetch_historical_option_chain.py` for July 23rd & July 24th.
   * Successfully imported **`750`** complete 1-minute option chain snapshots into `captures` and `chain_rows`.

2. **Data Agent F&O Sync**:
   * Successfully executed direct API run resulting in **10,201 price bars** parsed and saved across **44 active targets** (NIFTY Futures and Options).

3. **1-Day (`1d`) & 1-Minute (`1m`) Resolution Audit**:
   * Verified `curl -s http://127.0.0.1:8000/api/data-agent/health`.
   * Confirmed Cash, Futures, Indices, and Commodities support both 1-minute and 1-day daily bars in `price_bars` and `fo_price_bars`, while Option Chains are kept strictly at 1-minute resolution. All data is verified clean with **zero duplicates**.
