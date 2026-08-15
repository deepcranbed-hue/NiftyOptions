# NiftyOptions Data Source Architecture

**CRITICAL INSTRUCTION FOR ALL AGENTS:** 
Read this file before making assumptions about where data comes from. The system relies on multiple distinct brokers/APIs for different asset classes. Do not assume all data comes from ICICI Breeze.

## 1. Core Equities & Derivatives (ICICI Breeze)
**Data Source:** ICICI Breeze API
**Assets Covered:** 
- Nifty 50 Constituent Stocks (Cash)
- Nifty 50 Futures (`NIFTY_FUT_1`, `NIFTY_FUT_2`)
- Nifty 50 Option Chains (1-minute snapshots & fo_price_bars)
**How it's Synced:** 
- Live Option Chain: Background loop (`option_chain_sync_loop`) in `backend/main.py`, started via the BreezeSyncPanel in the UI.
- Backtesting Data / End-of-Day: The Unified Data Agent (`POST /api/data-agent/run`) using the `breeze_session_token`.

## 2. Commodities, Currencies & Global Cues (Upstox)
**Data Source:** Upstox API
**Assets Covered:**
- Commodities: `CRUDEOIL`, `GOLD`, `SILVER`, `COPPER`
- Currencies: `USDINR`
- Global/Proxy: `GIFTNIFTY`
**How it's Synced:**
- Handled via the standalone script `data_agent/fetching/sync_commodities.py`.
- **Automated Sync:** This script is automatically spawned in the background by the Data Agent's `_do_run` endpoint (via `sync_all_auxiliary.py`) whenever a normal Breeze sync is started from the UI.
- **Important Note:** We **stopped using Kite (Zerodha)** for these assets. Do not use Kite scripts for these symbols. All commodities and currencies are now fetched via Upstox.

## 3. Sector Indices & Sector Constituents (Yahoo Finance)
**Data Source:** Yahoo Finance (`yf`)
**Assets Covered:**
- **Sector Indices:** `CNXIT`, `NIFTYAUTO`, `NIFTYCONSUM`, `NIFTYENERGY`, `NIFTYFIN`, `NIFTYFMCG`, `NIFTYINFRA`, `NIFTYIT`, `NIFTYMEDIA`, `NIFTYMETAL`, `NIFTYPHARMA`, `NIFTYPSU`, `NIFTYREALTY`, `NSEBANK`.
- **Bank Nifty Constituents:** `HDFCBANK`, `ICICIBANK`, `SBIN`, `KOTAKBANK`, `AXISBANK`, `INDUSINDBK`, `BANKBARODA`, `PNB`, `AUBANK`, `IDFCFIRSTB`, `FEDERALBNK`, `BANDHANBNK`.
- **Nifty IT Constituents:** `LTIM`, `PERSISTENT`, `COFORGE`, `MPHASIS`, `LTTS`.
- **FinNifty Constituents:** `ICICIGI`, `ICICIPRULI`, `LICHSGFIN`, `HDFCAMC`.
**How it's Synced:**
- Handled via specific `yf` scripts in the `data_agent/fetching/` directory:
  - `sync_sectors_yf.py` (All 14 Sector Indices)
  - `sync_bank_bars_yf.py` (BankNifty Constituents)
  - `sync_it_bars_yf.py` (Nifty IT Constituents)
  - `sync_nifty50_bars_yf.py` (Nifty 50 Daily Bars)
  - `sync_finnifty_bars_yf.py` (FinNifty Constituents)
- **Automated Sync:** These scripts are all automatically orchestrated and run sequentially by `sync_all_auxiliary.py` in the background immediately after the main Data Agent Breeze sync completes. They are completely decoupled from the Breeze and Upstox API quotas.

---

## 4. Master Orchestrator (`backend/data_agent_routes.py`)
When you click **Start** in the UI, it calls `_do_run()` in the backend. This acts as the master orchestrator:
1. It synchronously fetches the Nifty 50 Cash & Options data using the provided Breeze session token.
2. Once Breeze completes, it spawns `data_agent/fetching/sync_all_auxiliary.py` in the background.
3. `sync_all_auxiliary.py` acts as a sequential wrapper to safely run Upstox (`sync_commodities.py`) and all 5 Yahoo Finance scripts (`sync_sectors_yf.py`, `sync_bank_bars_yf.py`, etc.) one after the other. (Running them sequentially prevents SQLite database lock crashes).

This means a single click in the UI completely synchronizes all 3 data sources across all assets.

### Summary Checklist for Troubleshooting Stale Data
*   **Stale Option Chain / Nifty 50:** Check Breeze Session Token.
*   **Stale CRUDEOIL / GOLD / SILVER:** Check Upstox `.env` API token. You can manually run `python data_agent/fetching/sync_commodities.py`.
*   **Stale NIFTYAUTO / NIFTYIT or Sector Constituents:** Check Yahoo Finance scripts (`yf`). You can manually run `python data_agent/fetching/sync_sectors_yf.py`.
*   **False Weekend DEGRADED Alerts:** We explicitly filter out commodities (like USDINR/CRUDEOIL) from the strict coverage audit in `data_health.py` to avoid false alerts caused by irregular weekend ticks.
