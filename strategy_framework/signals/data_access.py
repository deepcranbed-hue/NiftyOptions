"""
strategy_framework/signals/data_access.py
==========================================
Thin, self-contained read layer over option_chains.db.

Why not just import bar_store / chain_store everywhere? Two reasons:
  1. Those modules hard-code the live Google-Drive DB path; this layer takes an
     explicit path so the framework runs against a test DB or a sandbox copy.
  2. The framework must guarantee *no lookahead* (D-MA-01): every read is a
     backward as-of join keyed on a decision timestamp `now`. Keeping the SQL
     in one place makes that property auditable.

All timestamps stored in the DB are UTC with a trailing 'Z'. Callers pass and
receive UTC ISO strings; IST conversion is a display concern handled elsewhere.
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

# Connections go through db_config so the Drive-safe busy timeout applies (D-SC-06).
import os as _os, sys as _sys
_RT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
_RT in _sys.path or _sys.path.insert(0, _RT)
from db_config import connect as _db_connect
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

# Constituent / cross-asset symbols the momentum + forex signals want.
CONSTITUENT_SYMBOLS = ["RELIANCE", "HDFCBANK", "TCS"]          # extend as bars land
# Every cross-asset / macro symbol the signals may query. Bulk-loading (and
# negative-caching absent ones) here keeps the BarCache from per-snapshot DB hits.
CROSS_ASSET_SYMBOLS = ["GOLD", "SILVER", "COPPER", "USDINR", "CRUDEOIL", "GIFTNIFTY",
                       "NIFTY_FUT_1", "NIFTY_FUT_2"]

# Some DB copies predate the futures `open_interest` column. Detect it once per DB so
# the bar readers can carry OI when present and degrade cleanly (oi=None) when absent —
# never crash a plain OHLCV read with "no such column".
_HAS_OI: dict = {}


def _price_bars_has_oi(db_path: str) -> bool:
    if db_path not in _HAS_OI:
        try:
            c = _db_connect(db_path)
            cols = [r[1] for r in c.execute("PRAGMA table_info(price_bars)")]
            c.close()
            _HAS_OI[db_path] = "open_interest" in cols
        except Exception:
            _HAS_OI[db_path] = False
    return _HAS_OI[db_path]


# --------------------------------------------------------------------------
def _norm_ts(ts: str) -> str:
    """Normalise any ISO-ish string to the DB's '...Z' UTC form for comparison."""
    if ts is None:
        return ts
    ts = ts.strip()
    if ts.endswith("Z"):
        # Some rows carry millis ('...:00.000Z'); string comparison still works
        # because we always compare same-precision — but normalise to be safe.
        return ts
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ts


@dataclass
class ChainSnapshot:
    """One option-chain snapshot, as-of a decision time."""
    capture_id: int
    ts: str                 # UTC 'Z'
    expiry: str
    spot: float
    vix: Optional[float]
    strikes: list           # sorted ascending
    call_ltp: dict          # strike -> ltp
    put_ltp: dict
    call_oi: dict
    put_oi: dict
    call_oi_chg: dict
    put_oi_chg: dict
    call_iv: dict
    put_iv: dict
    call_volume: dict = None
    put_volume: dict = None

    def atm_strike(self) -> float:
        return min(self.strikes, key=lambda k: abs(k - self.spot))

    def as_pipeline_chain(self, days_to_expiry: float) -> dict:
        """Shape expected by backend/quant/pipeline.run_pipeline & rnd."""
        ks = self.strikes
        return {
            "strikes": ks,
            "call_ltp": [self.call_ltp.get(k, 0.0) for k in ks],
            "put_ltp": [self.put_ltp.get(k, 0.0) for k in ks],
            "call_oi": [self.call_oi.get(k, 0.0) for k in ks],
            "put_oi": [self.put_oi.get(k, 0.0) for k in ks],
            "call_oi_chg": [self.call_oi_chg.get(k, 0.0) for k in ks],
            "put_oi_chg": [self.put_oi_chg.get(k, 0.0) for k in ks],
            "spot": self.spot, "days": days_to_expiry, "r": 0.0655,
            "vix": self.vix, "captured_at": self.ts,
        }


class BarCache:
    """Bulk-loads all 1m bars for a symbol set once, into memory, so per-snapshot
    bar reads become in-memory slices (bisect) instead of one SQLite query each.
    Turns the backfill's ~131k constituent queries into a single bulk load."""
    def __init__(self, db_path: str, symbols, timeframe: str = "1m",
                 start: str = None, end: str = None):
        """`start`/`end` scope the bulk load to a time window (e.g. one expiry's
        span + a lookback buffer) so RAM stays bounded regardless of total
        history — never loads the whole price_bars table."""
        import bisect
        from collections import defaultdict
        self._bisect = bisect
        self.timeframe = timeframe
        self.data: dict = {}                 # symbol -> (ts_list, rows_list)
        self.n_bars = 0
        syms = [s for s in dict.fromkeys(symbols)]     # unique, keep order
        # Negative-cache every requested symbol: one that has NO bars in the window
        # (e.g. GOLD/COPPER in a DB that doesn't carry them) should return [] from
        # memory, not re-hit SQLite on every signal call.
        self._requested = set(syms)
        if not syms:
            return
        c = _db_connect(db_path)
        ph = ",".join("?" * len(syms))
        _oi = _price_bars_has_oi(db_path)
        _oicol = ", open_interest" if _oi else ""
        q = (f"SELECT symbol, ts, open, high, low, close, volume{_oicol} FROM price_bars "
             f"WHERE timeframe = ? AND symbol IN ({ph})")
        args = [timeframe, *syms]
        if start:
            q += " AND ts >= ?"; args.append(_norm_ts(start))
        if end:
            q += " AND ts <= ?"; args.append(_norm_ts(end))
        q += " ORDER BY symbol, ts"
        tmp = defaultdict(list)
        for r in c.execute(q, args):
            tmp[r["symbol"]].append({"ts": r["ts"], "open": r["open"], "high": r["high"],
                                     "low": r["low"], "close": r["close"], "volume": r["volume"],
                                     "open_interest": (r["open_interest"] if _oi else None)})
        c.close()
        for sym, rows in tmp.items():
            self.data[sym] = ([b["ts"] for b in rows], rows)
            self.n_bars += len(rows)

    def has(self, sym: str, timeframe: str) -> bool:
        # True for any REQUESTED symbol (even one with no bars → bars() returns []),
        # so a missing symbol is served from memory instead of a per-call DB query.
        return timeframe == self.timeframe and (sym in self.data or sym in self._requested)

    def symbols(self) -> list:
        return list(self.data.keys())

    def bars(self, sym: str, end=None, limit=400, start=None) -> list:
        d = self.data.get(sym)
        if not d:
            return []
        ts_list, rows = d
        hi = self._bisect.bisect_right(ts_list, end) if end else len(ts_list)
        lo = self._bisect.bisect_left(ts_list, start) if start else 0
        sl = rows[lo:hi]
        return sl[-limit:] if limit else sl


class DataAccess:
    def __init__(self, db_path: str, bar_cache: "BarCache" = None):
        self.db_path = db_path
        self._bar_cache = bar_cache

    def _conn(self) -> sqlite3.Connection:
        c = _db_connect(self.db_path)
        return c

    # ---- captures ------------------------------------------------------
    def list_captures(self, expiry: Optional[str] = None,
                      start: Optional[str] = None,
                      end: Optional[str] = None) -> list[dict]:
        """Captures ordered by time. Bounds are inclusive UTC 'Z' strings."""
        q = "SELECT capture_id, captured_at, spot, vix, underlying, note FROM captures WHERE 1=1"
        args: list = []
        if expiry:
            q += (" AND capture_id IN (SELECT DISTINCT capture_id FROM chain_rows "
                  "WHERE expiry = ?)")
            args.append(expiry)
        if start:
            q += " AND captured_at >= ?"; args.append(_norm_ts(start))
        if end:
            q += " AND captured_at <= ?"; args.append(_norm_ts(end))
        q += " ORDER BY captured_at ASC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def expiries(self) -> list[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute(
                "SELECT DISTINCT expiry FROM chain_rows ORDER BY expiry")]

    # ---- chain as-of ---------------------------------------------------
    def chain_as_of(self, now: str, expiry: str) -> Optional[ChainSnapshot]:
        """Latest chain snapshot for `expiry` with captured_at <= now.

        Backward as-of join (D-MA-01): a snapshot stamped T is visible only to
        decisions at or after T. Returns None if nothing precedes `now`.
        """
        now = _norm_ts(now)
        with self._conn() as c:
            cap = c.execute(
                "SELECT capture_id, captured_at, spot, vix FROM captures "
                "WHERE captured_at <= ? AND capture_id IN "
                "  (SELECT DISTINCT capture_id FROM chain_rows WHERE expiry = ?) "
                "ORDER BY captured_at DESC LIMIT 1", (now, expiry)).fetchone()
            if cap is None:
                return None
            rows = c.execute(
                "SELECT strike, call_ltp, put_ltp, call_oi, put_oi, "
                "call_oi_chg, put_oi_chg, call_iv, put_iv, call_volume, put_volume "
                "FROM chain_rows WHERE capture_id = ? AND expiry = ? ORDER BY strike",
                (cap["capture_id"], expiry)).fetchall()
        if not rows:
            return None
        d = lambda col: {r["strike"]: (r[col] or 0.0) for r in rows}
        return ChainSnapshot(
            capture_id=cap["capture_id"], ts=cap["captured_at"], expiry=expiry,
            # captures.vix is a constant 12.0 placeholder (D-SC-05) — the snapshot's
            # vix comes from the INDIAVIX price_bars series, as-of the capture time.
            spot=cap["spot"], vix=self.latest_vix(cap["captured_at"]),
            strikes=[r["strike"] for r in rows],
            call_ltp=d("call_ltp"), put_ltp=d("put_ltp"),
            call_oi=d("call_oi"), put_oi=d("put_oi"),
            call_oi_chg=d("call_oi_chg"), put_oi_chg=d("put_oi_chg"),
            call_iv=d("call_iv"), put_iv=d("put_iv"),
            call_volume=d("call_volume"), put_volume=d("put_volume"))

    # ---- price bars ----------------------------------------------------
    def bars(self, symbol: str, timeframe: str = "1m",
             end: Optional[str] = None, limit: int = 400,
             start: Optional[str] = None) -> list[dict]:
        """Return up to `limit` bars ending at/<= `end` (backward as-of).

        Ordered ascending (oldest first). Only base timeframes stored are '1m'
        and '1d'; resampling to 5m/15m is done by resample() below.
        """
        # in-memory cache fast path (backfill) — bisect slice, no DB round-trip
        if self._bar_cache is not None and self._bar_cache.has(symbol, timeframe):
            return self._bar_cache.bars(symbol, end=_norm_ts(end) if end else None,
                                        limit=limit, start=_norm_ts(start) if start else None)
        _oicol = ", open_interest" if _price_bars_has_oi(self.db_path) else ""
        q = (f"SELECT ts, open, high, low, close, volume{_oicol} FROM price_bars "
             "WHERE symbol = ? AND timeframe = ?")
        args: list = [symbol, timeframe]
        if end:
            q += " AND ts <= ?"; args.append(_norm_ts(end))
        if start:
            q += " AND ts >= ?"; args.append(_norm_ts(start))
        q += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q, args)]
        return list(reversed(rows))          # ascending

    def available_symbols(self, timeframe: str = "1m") -> list[str]:
        """Distinct symbols that actually have bars stored (for the given tf)."""
        if self._bar_cache is not None and timeframe == self._bar_cache.timeframe:
            return self._bar_cache.symbols()
        with self._conn() as c:
            return [r[0] for r in c.execute(
                "SELECT DISTINCT symbol FROM price_bars WHERE timeframe = ?",
                (timeframe,))]

    def latest_vix(self, now: Optional[str] = None) -> Optional[float]:
        """Last INDIAVIX value knowable at `now`, from the price_bars time series.

        NOT from `captures.vix` (D-SC-05): that column is a constant 12.0 across all
        13,126 captures — a placeholder, not a measurement. This used to read it, so
        every VIX-derived number in the framework was a fabricated constant.

        Delegates to bar_store.get_latest_vix — ONE implementation of the as-of rule,
        which differs per timeframe (1m bars are honestly timestamped; 1d bars are
        stamped at midnight but carry that day's close, so they must not be visible to
        an intraday decision on the same date). Passes this instance's db_path
        explicitly: bar_store's own DB_PATH default points at a Google Drive copy.

        Returns None when no VIX is knowable — never a fabricated fallback.
        """
        return self.vix_as_of(now)[0]

    def vix_as_of(self, now: Optional[str] = None):
        """(value, source) where source is '1m' | '1d' | None. Same rules as above;
        use this when the caller needs to record WHICH series answered."""
        try:
            import os as _os, sys as _sys
            _root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from bar_store import get_latest_vix
        except Exception:
            return (None, None)
        try:
            return get_latest_vix(before_ts=_norm_ts(now) if now else None,
                                  db=self.db_path, with_source=True)
        except Exception:
            return (None, None)


# --------------------------------------------------------------------------
def days_to_expiry(now: str, expiry: str) -> float:
    """Calendar days from decision time to expiry (never below a small floor so
    time-value math stays finite intraday on expiry day)."""
    n = datetime.fromisoformat(_norm_ts(now).replace("Z", "+00:00"))
    e = datetime.fromisoformat(_norm_ts(expiry).replace("Z", "+00:00"))
    return max((e - n).total_seconds() / 86400.0, 1e-4)
