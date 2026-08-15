"""
chain_store.py
--------------
Persist option-chain snapshots to SQLite LOSSLESSLY — every column the NSE CSV
has (LTP, OI, OI-change, IV, VOLUME, BID, ASK, BID-QTY, ASK-QTY for both call and
put sides), every strike, keyed by (captured_at, expiry, strike).

Principle: STORAGE IS LOSSLESS. Store the complete sheet; never drop columns or
low-OI strikes at save time — you can't recover data you didn't keep. Windowing
/ low-OI filtering happens at ANALYSIS time (the RND path), not here.

Enables: intraday & day-over-day comparison (same expiry, different timestamps),
CALENDAR strategies (same timestamp, different expiries), bid-ask spread &
order-book-depth studies (columns most extractors drop), and backtesting (replay
past chains through the RND/optimizer).
"""
from __future__ import annotations
import sqlite3, csv, sys
from datetime import datetime, timezone
from contextlib import contextmanager
from exchange_config import NIFTY_LOT_SIZE   # single source of truth for lot size

from db_config import DB_PATH, connect as _db_connect   # single source for the DB path (D-SC-06)

# every NSE per-side column we keep (call_ prefix and put_ prefix)
_SIDE_COLS = ["ltp", "oi", "oi_chg", "volume", "iv", "bid", "ask", "bid_qty", "ask_qty"]


@contextmanager
def _conn(db=DB_PATH):
    # via db_config so the Drive-safe busy timeout applies (D-SC-06). Python's
    # sqlite3 default is only 5s; a Drive sync can hold the file longer than that.
    c = _db_connect(db)
    try:
        yield c; c.commit()
    finally:
        c.close()


def init_db(db=DB_PATH):
    call_cols = ",\n".join(f"call_{k} REAL" for k in _SIDE_COLS)
    put_cols = ",\n".join(f"put_{k} REAL" for k in _SIDE_COLS)
    with _conn(db) as c:
        c.executescript(f"""
        CREATE TABLE IF NOT EXISTS captures (
            capture_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            spot        REAL,
            vix         REAL,
            source      TEXT,
            note        TEXT,
            exchange_code  TEXT NOT NULL DEFAULT 'NFO',
            underlying     TEXT NOT NULL DEFAULT 'NIFTY',
            snapshot_minute TEXT,
            status         TEXT NOT NULL DEFAULT 'complete',
            trigger        TEXT NOT NULL DEFAULT 'manual'
        );
        CREATE TABLE IF NOT EXISTS chain_rows (
            capture_id INTEGER NOT NULL REFERENCES captures(capture_id),
            expiry TEXT NOT NULL,
            strike REAL NOT NULL,
            {call_cols},
            {put_cols},
            PRIMARY KEY (capture_id, expiry, strike)
        );
        
        CREATE TABLE IF NOT EXISTS live_captures (
            capture_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            spot        REAL,
            vix         REAL,
            source      TEXT,
            note        TEXT,
            exchange_code  TEXT NOT NULL DEFAULT 'NFO',
            underlying     TEXT NOT NULL DEFAULT 'NIFTY',
            snapshot_minute TEXT,
            status         TEXT NOT NULL DEFAULT 'complete',
            trigger        TEXT NOT NULL DEFAULT 'manual'
        );
        CREATE TABLE IF NOT EXISTS live_chain_rows (
            capture_id INTEGER NOT NULL REFERENCES live_captures(capture_id),
            expiry TEXT NOT NULL,
            strike REAL NOT NULL,
            {call_cols},
            {put_cols},
            PRIMARY KEY (capture_id, expiry, strike)
        );

        CREATE TABLE IF NOT EXISTS instruments (
            exchange_code TEXT NOT NULL,
            underlying    TEXT NOT NULL,
            lot_size      INTEGER,
            strike_step   REAL,
            tick_size     REAL,
            session_open  TEXT,
            session_close TEXT,
            holiday_calendar TEXT,
            PRIMARY KEY (exchange_code, underlying)
        );
        INSERT OR IGNORE INTO instruments (exchange_code, underlying, lot_size, strike_step, tick_size, session_open, session_close, holiday_calendar)
        VALUES ('NFO', 'NIFTY', 75, 50, 0.05, '09:15', '15:30', 'NSE_2026');
        
        CREATE INDEX IF NOT EXISTS ix_rows_expiry_strike ON chain_rows(expiry, strike);
        CREATE INDEX IF NOT EXISTS ix_cap_time ON captures(captured_at);
        CREATE INDEX IF NOT EXISTS ix_rows_expiry ON chain_rows(expiry);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_captures_snapshot ON captures(exchange_code, underlying, snapshot_minute);
        
        CREATE INDEX IF NOT EXISTS ix_live_rows_expiry_strike ON live_chain_rows(expiry, strike);
        CREATE INDEX IF NOT EXISTS ix_live_cap_time ON live_captures(captured_at);
        CREATE INDEX IF NOT EXISTS ix_live_rows_expiry ON live_chain_rows(expiry);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_live_captures_snapshot ON live_captures(exchange_code, underlying, snapshot_minute);

        CREATE TABLE IF NOT EXISTS nifty_daily_prices (
            date TEXT PRIMARY KEY,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER
        );
        
        CREATE TABLE IF NOT EXISTS global_cues (
            key TEXT PRIMARY KEY,
            as_of TEXT NOT NULL,
            session_state TEXT NOT NULL,
            pct_change REAL,
            bp_change REAL,
            ref_window TEXT,
            trailing_vol_20d REAL,
            z REAL,
            strength REAL,
            provenance TEXT
        );

        CREATE TABLE IF NOT EXISTS cue_betas (
            target TEXT NOT NULL,
            cue_key TEXT NOT NULL,
            beta REAL NOT NULL,
            fit_date TEXT NOT NULL,
            r2 REAL NOT NULL,
            PRIMARY KEY (target, cue_key)
        );

        CREATE TABLE IF NOT EXISTS minute_bars (
            symbol TEXT NOT NULL,
            ts TEXT NOT NULL,
            o REAL,
            h REAL,
            l REAL,
            c REAL,
            v REAL,
            quality_flags TEXT,
            PRIMARY KEY (symbol, ts)
        );

        CREATE TABLE IF NOT EXISTS realized_metrics (
            ts TEXT NOT NULL,
            window INTEGER NOT NULL,
            rv_index REAL,
            rv_constituent_weighted REAL,
            corr_avg REAL,
            dispersion REAL,
            rupee_volume REAL,
            flags TEXT,
            PRIMARY KEY (ts, window)
        );

        DROP VIEW IF EXISTS chain_snapshots;
        CREATE VIEW chain_snapshots AS
        -- Capture-layer contract (D-CAP-02 §1a / Immutable Rule #5). Every row carries:
        --   * quote_state — the 5-state ladder describing the raw quote:
        --       TWO_SIDED      bid>0 AND ask>bid                (mid + spread, full use)
        --       ONE_SIDED_ASK  bid absent/<=0, ask>0           (IV upper bound only)
        --       ONE_SIDED_BID  ask absent/<=0, bid>0           (IV lower bound only)
        --       CROSSED_LOCKED bid>0 AND ask>0 AND bid>=ask    (bad tick, rejected)
        --       NO_QUOTE       both absent/<=0                 (excluded)
        --   * mid — DERIVED-ONLY: (bid+ask)/2 when TWO_SIDED, else NULL. NEVER LTP, never 0.
        --   * price / price_source — the best-available consumed value and its tag in the
        --       hierarchy MID_2S -> LTP_RECENT -> EXCLUDED. LTP is permitted but never
        --       anonymous. When real bid/ask arrive a row flips to MID_2S with no code change.
        --   NOTE: the LTP recency gate is NOT applied (schema has no per-option last-trade
        --   time), so LTP_RECENT is currently recency-UNGATED — a known limitation, flagged
        --   not papered over. NULL bid/ask (post-§1.4) fall through to the LTP/EXCLUDED arms.
        SELECT
            c.captured_at AS ts, c.spot, c.vix, r.expiry, r.strike,
            'call' AS cp,
            r.call_bid AS bid, r.call_ask AS ask, r.call_ltp AS ltp,
            CASE WHEN r.call_bid > 0 AND r.call_ask > r.call_bid
                 THEN (r.call_bid + r.call_ask) / 2.0 ELSE NULL END AS mid,
            CASE WHEN r.call_bid > 0 AND r.call_ask > r.call_bid THEN (r.call_bid + r.call_ask) / 2.0
                 WHEN r.call_ltp > 0 THEN r.call_ltp ELSE NULL END AS price,
            CASE WHEN r.call_bid > 0 AND r.call_ask > r.call_bid THEN 'MID_2S'
                 WHEN r.call_ltp > 0 THEN 'LTP_RECENT' ELSE 'EXCLUDED' END AS price_source,
            CASE WHEN r.call_bid > 0 AND r.call_ask > r.call_bid THEN 'TWO_SIDED'
                 WHEN r.call_bid > 0 AND r.call_ask > 0 AND r.call_bid >= r.call_ask THEN 'CROSSED_LOCKED'
                 WHEN (r.call_bid IS NULL OR r.call_bid <= 0) AND r.call_ask > 0 THEN 'ONE_SIDED_ASK'
                 WHEN (r.call_ask IS NULL OR r.call_ask <= 0) AND r.call_bid > 0 THEN 'ONE_SIDED_BID'
                 ELSE 'NO_QUOTE' END AS quote_state,
            r.call_oi AS oi, r.call_volume AS volume
        FROM captures c
        JOIN chain_rows r ON c.capture_id = r.capture_id
        UNION ALL
        SELECT
            c.captured_at AS ts, c.spot, c.vix, r.expiry, r.strike,
            'put' AS cp,
            r.put_bid AS bid, r.put_ask AS ask, r.put_ltp AS ltp,
            CASE WHEN r.put_bid > 0 AND r.put_ask > r.put_bid
                 THEN (r.put_bid + r.put_ask) / 2.0 ELSE NULL END AS mid,
            CASE WHEN r.put_bid > 0 AND r.put_ask > r.put_bid THEN (r.put_bid + r.put_ask) / 2.0
                 WHEN r.put_ltp > 0 THEN r.put_ltp ELSE NULL END AS price,
            CASE WHEN r.put_bid > 0 AND r.put_ask > r.put_bid THEN 'MID_2S'
                 WHEN r.put_ltp > 0 THEN 'LTP_RECENT' ELSE 'EXCLUDED' END AS price_source,
            CASE WHEN r.put_bid > 0 AND r.put_ask > r.put_bid THEN 'TWO_SIDED'
                 WHEN r.put_bid > 0 AND r.put_ask > 0 AND r.put_bid >= r.put_ask THEN 'CROSSED_LOCKED'
                 WHEN (r.put_bid IS NULL OR r.put_bid <= 0) AND r.put_ask > 0 THEN 'ONE_SIDED_ASK'
                 WHEN (r.put_ask IS NULL OR r.put_ask <= 0) AND r.put_bid > 0 THEN 'ONE_SIDED_BID'
                 ELSE 'NO_QUOTE' END AS quote_state,
            r.put_oi AS oi, r.put_volume AS volume
        FROM captures c
        JOIN chain_rows r ON c.capture_id = r.capture_id;
        """)

def save_daily_prices(records: list, db=DB_PATH):
    """Upsert daily prices into nifty_daily_prices table. 
       records = [{"date": "YYYY-MM-DD", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}, ...]"""
    init_db(db)
    with _conn(db) as c:
        batch = [
            (r["date"], r.get("open", 0.0), r.get("high", 0.0), r.get("low", 0.0), r.get("close", 0.0), int(r.get("volume", 0)))
            for r in records
        ]
        c.executemany(
            "INSERT OR REPLACE INTO nifty_daily_prices(date, open, high, low, close, volume) VALUES(?,?,?,?,?,?)",
            batch
        )

def get_daily_prices(limit: int = 365, db=DB_PATH) -> list:
    """Get the most recent daily prices, sorted by date ascending."""
    init_db(db)
    with _conn(db) as c:
        rows = c.execute("SELECT * FROM nifty_daily_prices ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
        # return ascending for charts
        return [dict(r) for r in reversed(rows)]


def _num(x):
    s = str(x).replace(",", "").strip()
    return None if s in ("", "-", "—") else float(s)


def save_from_nse_csv(path: str, *, expiry: str, spot: float,
                      vix: float | None = None,
                      captured_at: str | None = None, note: str = "",
                      exchange_code: str = "NFO", underlying: str = "NIFTY",
                      status: str = "complete", trigger: str = "manual",
                      db=DB_PATH) -> int | None:
    """Save a raw NSE CSV LOSSLESSLY — all columns, all strikes. This is the
    preferred path: it reads the ORIGINAL sheet, so nothing is dropped.
    NSE layout: calls 1-10, strike 11, puts 12-21.

    expiry : 'YYYY-MM-DD' (DATE, not days — days is derived at analysis time).
    spot   : NIFTY spot at capture time (REQUIRED — the analysis anchor).
    vix    : India VIX at capture time (OPTIONAL — store if available)."""
    from backend.timeutil import to_db_ts, to_db_minute
    captured_at = to_db_ts(captured_at or datetime.now(timezone.utc))
    snapshot_minute = to_db_minute(captured_at)
    init_db(db)
    rows_out = []
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    hdr = next(i for i, r in enumerate(rows) if any("STRIKE" in (c or "").upper() for c in r))
    for r in rows[hdr + 1:]:
        if len(r) < 22:
            continue
        strike = _num(r[11])
        if strike is None:
            continue
        # calls: 1=OI 2=CHNG_OI 3=VOL 4=IV 5=LTP 8=BID 9=ASK 7=BIDQTY 10=ASKQTY
        cvals = {"ltp": _num(r[5]), "oi": _num(r[1]), "oi_chg": _num(r[2]),
                 "volume": _num(r[3]), "iv": _num(r[4]), "bid": _num(r[8]),
                 "ask": _num(r[9]), "bid_qty": _num(r[7]), "ask_qty": _num(r[10])}
        # puts: 21=OI 20=CHNG_OI 19=VOL 18=IV 17=LTP 13=BID 14=ASK 12=BIDQTY 15=ASKQTY
        pvals = {"ltp": _num(r[17]), "oi": _num(r[21]), "oi_chg": _num(r[20]),
                 "volume": _num(r[19]), "iv": _num(r[18]), "bid": _num(r[13]),
                 "ask": _num(r[14]), "bid_qty": _num(r[12]), "ask_qty": _num(r[15])}
        rows_out.append((strike, cvals, pvals))

    try:
        with _conn(db) as c:
            cid = c.execute(
                "INSERT INTO captures(captured_at,spot,vix,source,note,exchange_code,underlying,snapshot_minute,status,trigger) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (captured_at, spot, vix, "nse_csv", note, exchange_code, underlying, snapshot_minute, status, trigger)
            ).lastrowid
            placeholders = ",".join(["?"] * (3 + 2 * len(_SIDE_COLS)))
            cols = ("capture_id,expiry,strike," +
                    ",".join(f"call_{k}" for k in _SIDE_COLS) + "," +
                    ",".join(f"put_{k}" for k in _SIDE_COLS))
            batch = []
            for strike, cv, pv in rows_out:
                row = [cid, expiry, strike] + [cv[k] for k in _SIDE_COLS] + [pv[k] for k in _SIDE_COLS]
                batch.append(row)
            c.executemany(f"INSERT OR REPLACE INTO chain_rows({cols}) VALUES({placeholders})", batch)
        return cid
    except sqlite3.IntegrityError as e:
        sys.stderr.write(f"Skipping duplicate manual/EOD capture for {exchange_code} {underlying} at {snapshot_minute}: {e}\n")
        return None

def save_from_json_rows(rows: list, *, expiry: str, spot: float,
                        vix: float | None = None,
                        captured_at: str | None = None, note: str = "",
                        exchange_code: str = "NFO", underlying: str = "NIFTY",
                        status: str = "complete", trigger: str = "manual",
                        db=DB_PATH) -> int | None:
    """Save raw JSON OptionRows (like from Breeze API) LOSSLESSLY."""
    from backend.timeutil import to_db_ts, to_db_minute
    captured_at = to_db_ts(captured_at or datetime.now(timezone.utc))
    snapshot_minute = to_db_minute(captured_at)
    init_db(db)
    
    try:
        with _conn(db) as c:
            # Check if capture already exists for this snapshot_minute
            existing = c.execute(
                "SELECT capture_id FROM captures WHERE exchange_code=? AND underlying=? AND snapshot_minute=?",
                (exchange_code, underlying, snapshot_minute)
            ).fetchone()
            if existing:
                cid = existing["capture_id"] if isinstance(existing, sqlite3.Row) else existing[0]
            else:
                cid = c.execute(
                    "INSERT INTO captures(captured_at,spot,vix,source,note,exchange_code,underlying,snapshot_minute,status,trigger) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (captured_at, spot, vix, "api_json", note, exchange_code, underlying, snapshot_minute, status, trigger)
                ).lastrowid
            placeholders = ",".join(["?"] * (3 + 2 * len(_SIDE_COLS)))
            cols = ("capture_id,expiry,strike," +
                    ",".join(f"call_{k}" for k in _SIDE_COLS) + "," +
                    ",".join(f"put_{k}" for k in _SIDE_COLS))
            batch = []
            for r in rows:
                strike = r.get("strike")
                if not strike: continue
                
                # extract call/put matching _SIDE_COLS mapping. Absent fields stay None →
                # persist as NULL, NEVER coerced to 0.0 (D-CAP-02 §1.4: a missing field
                # writes NULL, and 0.0-as-quote is the exact defect being fixed).
                cv = {k: r.get(f"call_{k}") for k in _SIDE_COLS}
                pv = {k: r.get(f"put_{k}") for k in _SIDE_COLS}
                # Special case for oi_chg since JSON uses call_oichg
                if "call_oichg" in r: cv["oi_chg"] = r.get("call_oichg")
                if "put_oichg" in r: pv["oi_chg"] = r.get("put_oichg")

                row = [cid, expiry, strike] + [cv[k] for k in _SIDE_COLS] + [pv[k] for k in _SIDE_COLS]
                batch.append(row)
            c.executemany(f"INSERT OR REPLACE INTO chain_rows({cols}) VALUES({placeholders})", batch)
            return cid
    except sqlite3.IntegrityError as e:
        sys.stderr.write(f"Skipping duplicate capture for {exchange_code} {underlying} at {snapshot_minute}: {e}\n")
        return None

def resolve_capture_vix(store_vix, client_vix):
    """Resolve the India VIX to persist on a capture (D-CAP-02 §3 / capture brief §3).

    Prefer the real captured store value; the client-supplied value is a fallback only;
    if neither exists, return None → persist NULL. A constant/placeholder is NEVER
    fabricated here (the "VIX prints 12.0 all session" bug). The DataQualityAgent
    (D-CAP-03) is the cross-session safety net that flags a still-constant stream as dead.

    Returns (vix_value_or_None, source) where source ∈ {STORE, CLIENT, ABSENT}.
    """
    if store_vix is not None:
        return float(store_vix), "STORE"
    if client_vix is not None:
        return float(client_vix), "CLIENT"
    return None, "ABSENT"


def load_capture(capture_id: int, expiry: str = None, db=DB_PATH) -> dict:
    """Full lossless reconstruction of a stored capture (all columns), scoped to a SINGLE
    expiry. Never aggregates OI/quotes across expiries (D-CAP-02 §3: an unfiltered query
    summing OI across expiries is the spot/OI artifact this fix removes)."""
    expiry_auto_selected = False
    expiry_options = []
    with _conn(db) as c:
        cap = c.execute("SELECT * FROM captures WHERE capture_id=? AND status='complete'", (capture_id,)).fetchone()
        expiry_options = [r[0] for r in c.execute(
            "SELECT DISTINCT expiry FROM chain_rows WHERE capture_id=? ORDER BY expiry",
            (capture_id,)).fetchall()]
        if expiry:
            expiry_date_only = expiry.split('T')[0]
            rows = c.execute(
                "SELECT * FROM chain_rows WHERE capture_id=? AND (expiry=? OR expiry LIKE ?) ORDER BY strike",
                (capture_id, expiry, f"{expiry_date_only}%")
            ).fetchall()
        else:
            # No expiry given → default to the nearest (earliest) expiry, one expiry only.
            # This replaces the old "grab all rows" behaviour that duplicated strikes and
            # inflated OI walls by mixing expiries.
            if not expiry_options:
                rows = []
            else:
                chosen = expiry_options[0]
                expiry_auto_selected = len(expiry_options) > 1
                rows = c.execute(
                    "SELECT * FROM chain_rows WHERE capture_id=? AND expiry=? ORDER BY strike",
                    (capture_id, chosen)).fetchall()
    if not cap or not rows:
        return {}
    col = lambda k: [r[k] for r in rows]
    out = {"captured_at": cap["captured_at"], "spot": cap["spot"],
           "expiry": rows[0]["expiry"], "strikes": col("strike"),
           # provenance: which expiry these OI/quotes belong to, and whether it was
           # auto-picked because the caller passed none while multiple existed.
           "expiry_auto_selected": expiry_auto_selected,
           "expiry_options": expiry_options}
    for k in _SIDE_COLS:
        out[f"call_{k}"] = col(f"call_{k}")
        out[f"put_{k}"] = col(f"put_{k}")
    # convenience aliases matching the analysis pipeline's expected keys
    out["call_ltp"] = out["call_ltp"]; out["put_ltp"] = out["put_ltp"]
    out["call_oi"] = out["call_oi"]; out["put_oi"] = out["put_oi"]
    out["lot_size"] = NIFTY_LOT_SIZE
    return out


def delete_capture(capture_id: int, db=DB_PATH) -> bool:
    """Deletes a capture and its associated chain rows."""
    with _conn(db) as c:
        # First check if it exists
        cap = c.execute("SELECT 1 FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
        if not cap:
            return False
        
        # Delete rows then capture
        c.execute("DELETE FROM chain_rows WHERE capture_id=?", (capture_id,))
        c.execute("DELETE FROM captures WHERE capture_id=?", (capture_id,))
        return True


def compare_strike(strike, expiry, db=DB_PATH):
    """Time-series of one strike across captures — incl. bid/ask/volume now."""
    expiry_date_only = expiry.split('T')[0]
    with _conn(db) as c:
        q = """SELECT cap.captured_at, cap.spot, r.call_ltp, r.put_ltp, r.call_oi,
                      r.put_oi, r.call_iv, r.put_iv, r.call_volume, r.put_volume,
                      r.call_bid, r.call_ask, r.put_bid, r.put_ask
               FROM chain_rows r JOIN captures cap ON cap.capture_id=r.capture_id
               WHERE (r.expiry=? OR r.expiry LIKE ?) AND r.strike=? ORDER BY cap.captured_at"""
        return [dict(r) for r in c.execute(q, (expiry, f"{expiry_date_only}%", strike)).fetchall()]


def calendar_view(capture_id, strike, db=DB_PATH):
    """Same capture + strike across expiries — calendar-spread input."""
    with _conn(db) as c:
        q = """SELECT expiry, call_ltp, put_ltp, call_iv, put_iv, call_oi, put_oi
               FROM chain_rows WHERE capture_id=? AND strike=? ORDER BY expiry"""
        return [dict(r) for r in c.execute(q, (capture_id, strike)).fetchall()]


def list_captures(expiry=None, limit=50, db=DB_PATH):
    with _conn(db) as c:
        if expiry:
            expiry_date_only = expiry.split('T')[0]
            q = """SELECT DISTINCT cap.* FROM captures cap
                   JOIN chain_rows r ON r.capture_id=cap.capture_id
                   WHERE r.expiry=? OR r.expiry LIKE ? ORDER BY cap.captured_at DESC LIMIT ?"""
            return [dict(r) for r in c.execute(q, (expiry, f"{expiry_date_only}%", limit)).fetchall()]
        return [dict(r) for r in c.execute(
            "SELECT * FROM captures ORDER BY captured_at DESC LIMIT ?", (limit,)).fetchall()]


if __name__ == "__main__":
    import os
    if os.path.exists("demo.db"): os.remove("demo.db")
    cid = save_from_nse_csv("/mnt/user-data/uploads/option-chain-ED-NIFTY-30-Jun-2026.csv",
                            expiry="2026-06-30", spot=24050,
                            captured_at="2026-06-28T14:30:00+00:00", db="demo.db")
    cap = load_capture(cid, db="demo.db")
    print(f"Saved LOSSLESSLY: {len(cap['strikes'])} strikes, all columns.")
    # prove volume + bid/ask are there now
    ai = min(range(len(cap['strikes'])), key=lambda i: abs(cap['strikes'][i]-24050))
    print(f"At 24050 strike: call_vol={cap['call_volume'][ai]} call_bid={cap['call_bid'][ai]} "
          f"call_ask={cap['call_ask'][ai]} put_vol={cap['put_volume'][ai]}")
    print(f"Columns stored per side: {_SIDE_COLS}")
    os.remove("demo.db")


# ── expiry date <-> days-to-expiry helpers ──────────────────────────────────
def days_to_expiry(expiry_date: str, as_of: str | None = None,
                   expiry_time: str = "15:30", tz_offset_hours: float = 5.5) -> float:
    """Compute days-to-expiry from the stored EXPIRY DATE and the capture time.
    This is why we store the DATE not the days: days is derived, date is permanent.

    expiry_date : 'YYYY-MM-DD' (the stored, permanent value)
    as_of       : capture timestamp (ISO). Defaults to now.
    expiry_time : NSE options expire at 15:30 IST — used so expiry-DAY gives a
                  small positive fraction (intraday hours left), never 0.
    Returns days as a float (e.g. 2.0, or 0.28 on expiry-day morning).
    """
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=tz_offset_hours))
    # Clean expiry_date to only extract YYYY-MM-DD if a full timestamp is passed
    clean_date = expiry_date[:10]
    exp = datetime.fromisoformat(f"{clean_date}T{expiry_time}:00").replace(tzinfo=ist)
    if as_of:
        now = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    secs = (exp - now).total_seconds()
    days = secs / 86400.0
    # guard: on/after expiry, clamp to a tiny positive so T never hits 0 (breaks RND)
    return max(days, 0.02)   # ~0.5h floor


def days_from_capture(capture_id: int, db=DB_PATH) -> float:
    """Days-to-expiry for a stored capture, using its captured_at + expiry date."""
    with _conn(db) as c:
        cap = c.execute("SELECT captured_at FROM captures WHERE capture_id=? AND status='complete'",
                        (capture_id,)).fetchone()
        row = c.execute("SELECT expiry FROM chain_rows WHERE capture_id=? LIMIT 1",
                        (capture_id,)).fetchone()
    if not cap or not row:
        return None
    return round(days_to_expiry(row["expiry"], as_of=cap["captured_at"]), 3)


# ── dropdown support: list captures for selection (latest first) ─────────────
def capture_options(db=DB_PATH, limit=5000, exchange_code="NFO", underlying="NIFTY"):
    """For the UI dropdown: recent captures with a human label. Latest first
    (the default selection). Each item: capture_id, label, captured_at, expiry,
    spot, vix."""
    with _conn(db) as c:
        q = """SELECT cap.capture_id, cap.captured_at, cap.spot, cap.vix,
                      MIN(r.expiry) AS expiry, COUNT(DISTINCT r.expiry) AS n_expiries
               FROM captures cap JOIN chain_rows r ON r.capture_id=cap.capture_id
               WHERE cap.exchange_code=? AND cap.underlying=? AND cap.status='complete'
               GROUP BY cap.capture_id ORDER BY cap.captured_at DESC LIMIT ?"""
        rows = c.execute(q, (exchange_code, underlying, limit)).fetchall()
    opts = []
    for r in rows:
        exp = r["expiry"] + (f" +{r['n_expiries']-1}" if r["n_expiries"] > 1 else "")
        from backend.timeutil import to_display_ist
        ist_time = to_display_ist(r["captured_at"])
        label = (f"{ist_time[:16]}  |  spot {r['spot']:.0f}"
                 f"  |  exp {exp}" + (f"  |  VIX {r['vix']:.1f}" if r["vix"] else ""))
        opts.append({"capture_id": r["capture_id"], "label": label,
                     "captured_at": r["captured_at"], "expiry": r["expiry"],
                     "spot": r["spot"], "vix": r["vix"]})
    return opts   # opts[0] is the latest (default selection)


# ── Live-Specific Database Wrapper Functions ──────────────────────────────────
def save_live_from_json_rows(rows: list, *, expiry: str, spot: float,
                             vix: float | None = None,
                             captured_at: str | None = None, note: str = "",
                             exchange_code: str = "NFO", underlying: str = "NIFTY",
                             status: str = "complete", trigger: str = "manual",
                             db=DB_PATH) -> int | None:
    """Save live JSON OptionRows (like from Breeze API) to live_captures and live_chain_rows tables."""
    from backend.timeutil import to_db_ts, to_db_minute
    captured_at = to_db_ts(captured_at or datetime.now(timezone.utc))
    snapshot_minute = to_db_minute(captured_at)
    init_db(db)
    
    try:
        with _conn(db) as c:
            cid = c.execute(
                "INSERT INTO live_captures(captured_at,spot,vix,source,note,exchange_code,underlying,snapshot_minute,status,trigger) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (captured_at, spot, vix, "api_json_live", note, exchange_code, underlying, snapshot_minute, status, trigger)
            ).lastrowid
            placeholders = ",".join(["?"] * (3 + 2 * len(_SIDE_COLS)))
            cols = ("capture_id,expiry,strike," +
                    ",".join(f"call_{k}" for k in _SIDE_COLS) + "," +
                    ",".join(f"put_{k}" for k in _SIDE_COLS))
            batch = []
            for r in rows:
                strike = r.get("strike")
                if not strike: continue
                
                # Absent fields stay None → NULL, never 0.0 (D-CAP-02 §1.4).
                cv = {k: r.get(f"call_{k}") for k in _SIDE_COLS}
                pv = {k: r.get(f"put_{k}") for k in _SIDE_COLS}
                if "call_oichg" in r: cv["oi_chg"] = r.get("call_oichg")
                if "put_oichg" in r: pv["oi_chg"] = r.get("put_oichg")

                row = [cid, expiry, strike] + [cv[k] for k in _SIDE_COLS] + [pv[k] for k in _SIDE_COLS]
                batch.append(row)
            c.executemany(f"INSERT OR REPLACE INTO live_chain_rows({cols}) VALUES({placeholders})", batch)
            return cid
    except sqlite3.IntegrityError as e:
        sys.stderr.write(f"Skipping duplicate live capture for {exchange_code} {underlying} at {snapshot_minute}: {e}\n")
        return None

def load_live_capture(capture_id: int, expiry: str = None, db=DB_PATH) -> dict:
    """Load an option chain capture from the live tables."""
    with _conn(db) as c:
        cap = c.execute("SELECT * FROM live_captures WHERE capture_id=? AND status='complete'", (capture_id,)).fetchone()
        if expiry:
            expiry_date_only = expiry.split('T')[0]
            rows = c.execute(
                "SELECT * FROM live_chain_rows WHERE capture_id=? AND (expiry=? OR expiry LIKE ?) ORDER BY strike",
                (capture_id, expiry, f"{expiry_date_only}%")
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM live_chain_rows WHERE capture_id=? ORDER BY expiry, strike",
                (capture_id,)
            ).fetchall()
            
    if not cap:
        return {}
    res = {k: cap[k] for k in ["capture_id", "captured_at", "spot", "vix"]}
    res["strikes"] = [r["strike"] for r in rows]
    for k in _SIDE_COLS:
        res[f"call_{k}"] = [r[f"call_{k}"] for r in rows]
        res[f"put_{k}"] = [r[f"put_{k}"] for r in rows]
    return res

def capture_live_options(db=DB_PATH, limit=200, exchange_code="NFO", underlying="NIFTY"):
    """Fetch live option chain capture dropdown items."""
    with _conn(db) as c:
        q = """SELECT cap.capture_id, cap.captured_at, cap.spot, cap.vix,
                      MIN(r.expiry) AS expiry, COUNT(DISTINCT r.expiry) AS n_expiries
               FROM live_captures cap JOIN live_chain_rows r ON r.capture_id=cap.capture_id
               WHERE cap.exchange_code=? AND cap.underlying=? AND cap.status='complete'
               GROUP BY cap.capture_id ORDER BY cap.captured_at DESC LIMIT ?"""
        rows = c.execute(q, (exchange_code, underlying, limit)).fetchall()
    opts = []
    for r in rows:
        exp = r["expiry"] + (f" +{r['n_expiries']-1}" if r["n_expiries"] > 1 else "")
        from backend.timeutil import to_display_ist
        ist_time = to_display_ist(r["captured_at"])
        label = (f"{ist_time[:16]}  |  spot {r['spot']:.0f}"
                 f"  |  exp {exp}" + (f"  |  VIX {r['vix']:.1f}" if r["vix"] else ""))
        opts.append({"capture_id": r["capture_id"], "label": label,
                     "captured_at": r["captured_at"], "expiry": r["expiry"],
                     "spot": r["spot"], "vix": r["vix"]})
    return opts

def delete_live_capture(capture_id: int, db=DB_PATH) -> bool:
    with _conn(db) as c:
        cap = c.execute("SELECT 1 FROM live_captures WHERE capture_id=?", (capture_id,)).fetchone()
        if not cap:
            return False
        c.execute("DELETE FROM live_chain_rows WHERE capture_id=?", (capture_id,))
        c.execute("DELETE FROM live_captures WHERE capture_id=?", (capture_id,))
        return True
