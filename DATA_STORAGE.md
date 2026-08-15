# Data Storage Architecture - NiftyOptions Quant Engine

All analytical, historical price, and option chain datasets in this workspace are stored losslessly inside a single unified SQLite database file: **`option_chains.db`**.

---

## 1. Database Schema Reference

### Table 1: `price_bars`
Stores the raw historical price coordinates (1-minute and daily candles) for stocks and index tickers.

* **Schema**:
  ```sql
  CREATE TABLE price_bars (
      symbol    TEXT NOT NULL,      -- e.g. 'NIFTY', 'RELIAN'
      timeframe TEXT NOT NULL,      -- '1m' (1-minute) or '1d' (daily)
      ts        TEXT NOT NULL,      -- ISO8601 string timestamp (IST, e.g. '2026-07-03T09:15:00+05:30')
      open      REAL,
      high      REAL,
      low       REAL,
      close     REAL,
      volume    REAL,
      PRIMARY KEY (symbol, timeframe, ts)
  );
  CREATE INDEX ix_bars ON price_bars(symbol, timeframe, ts);
  ```

* **Storage Rules**:
  * **Ground Truth Only**: Only raw `1m` and `1d` intervals are stored.
  * **Idempotency**: All downloads use `INSERT OR REPLACE` to safely overwrite existing overlapping ranges without duplicates or constraint violations.
  * **Timestamp Standardization**: Daily (`1d`) index bars are normalized to `09:15:00` open time to guarantee single-row keys per trading day.

---

### Table 2: `captures`
Acts as a metadata registry log for every downloaded option chain snapshot.

* **Schema**:
  ```sql
  CREATE TABLE captures (
      capture_id  INTEGER PRIMARY KEY AUTOINCREMENT,
      captured_at TEXT NOT NULL,      -- ISO8601 snapshot capture timestamp
      spot        REAL,               -- Index spot price at capture moment
      vix         REAL,               -- VIX volatility index value
      source      TEXT,               -- 'NSE' or 'Breeze'
      note        TEXT                -- e.g. 'auto_sync_5m'
  );
  CREATE INDEX ix_cap_time ON captures(captured_at);
  ```

---

### Table 3: `chain_rows`
Stores the complete, lossless option chain records for all strikes linked to a parent `capture_id`.

* **Schema**:
  ```sql
  CREATE TABLE chain_rows (
      capture_id INTEGER NOT NULL REFERENCES captures(capture_id),
      expiry     TEXT NOT NULL,       -- Expiry date string (YYYY-MM-DD)
      strike     REAL NOT NULL,       -- Strike price coordinate
      
      -- Call side columns
      call_ltp     REAL, call_oi      REAL, call_oi_chg  REAL, call_volume REAL, call_iv     REAL, 
      call_bid     REAL, call_ask     REAL, call_bid_qty REAL, call_ask_qty REAL,
      
      -- Put side columns
      put_ltp      REAL, put_oi       REAL, put_oi_chg   REAL, put_volume  REAL, put_iv      REAL, 
      put_bid      REAL, put_ask      REAL, put_qty      REAL, put_ask_qty REAL,
      
      PRIMARY KEY (capture_id, expiry, strike)
  );
  CREATE INDEX ix_rows_expiry ON chain_rows(expiry);
  CREATE INDEX ix_rows_expiry_strike ON chain_rows(expiry, strike);
  ```

* **Storage Rules**:
  * **Lossless Storage**: The complete option chain sheet is preserved; no columns or low-OI strikes are dropped at save-time. 
  * **Windowing/Filtering**: Filtering of out-of-the-money (OTM) strikes and calculation of Greeks happens during **query-time** (for optimizer metrics) to prevent data loss.

---

## 2. Dynamic Resampling & Realized Volatility

### Timeframe Resampling
Instead of saving redundant intervals that could drift apart, all chart timeframes above 1-minute (like `5m`, `15m`, and `60m`) are dynamically aggregated on-the-fly when requested from the database:
1. The backend queries raw `1m` bars for the selected date range.
2. It groups candles into chunks (e.g. 5-minute segments).
3. The new bar is constructed using:
   * **Open**: Open price of the first 1m bar in the interval.
   * **High**: Maximum high price across all 1m bars in the interval.
   * **Low**: Minimum low price across all 1m bars in the interval.
   * **Close**: Close price of the last 1m bar in the interval.
   * **Volume**: Sum of volume across all 1m bars in the interval.

### Realized Volatility
Daily index bars (`1d`) are utilized to calculate the **Annualized Realized Close-to-Close Volatility** (`realized_vol()`), which quantifies actual market movement over a rolling period (default: 20 days) for option pricing comparison.
