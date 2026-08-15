"""
No-lookahead / as-of correctness tests against a synthetic temp DB.
Run: python -m pytest strategy_framework/tests/test_nolookahead.py -q
"""
import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from strategy_framework.signals.data_access import DataAccess, days_to_expiry


def _make_db():
    path = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE captures (capture_id INTEGER PRIMARY KEY, captured_at TEXT, spot REAL,
        vix REAL, source TEXT, note TEXT, exchange_code TEXT, underlying TEXT,
        snapshot_minute TEXT, status TEXT, trigger TEXT);
    CREATE TABLE chain_rows (capture_id INTEGER, expiry TEXT, strike REAL,
        call_ltp REAL, call_oi REAL, call_oi_chg REAL, call_volume REAL, call_iv REAL,
        call_bid REAL, call_ask REAL, call_bid_qty REAL, call_ask_qty REAL,
        put_ltp REAL, put_oi REAL, put_oi_chg REAL, put_volume REAL, put_iv REAL,
        put_bid REAL, put_ask REAL, put_bid_qty REAL, put_ask_qty REAL);
    CREATE TABLE price_bars (exchange TEXT, symbol TEXT, timeframe TEXT, ts TEXT,
        open REAL, high REAL, low REAL, close REAL, volume REAL);
    """)
    exp = "2026-07-14T06:00:00.000Z"
    caps = [(1, "2026-07-08T04:00:00Z", 24200.0), (2, "2026-07-08T05:00:00Z", 24260.0),
            (3, "2026-07-08T06:00:00Z", 24230.0)]
    for cid, ts, spot in caps:
        c.execute("INSERT INTO captures (capture_id,captured_at,spot,vix,underlying) "
                  "VALUES (?,?,?,?,?)", (cid, ts, spot, 12.5, "NIFTY"))
        for k in (24100.0, 24200.0, 24300.0):
            c.execute("INSERT INTO chain_rows (capture_id,expiry,strike,call_ltp,call_oi,"
                      "call_oi_chg,put_ltp,put_oi,put_oi_chg) VALUES (?,?,?,?,?,?,?,?,?)",
                      (cid, exp, k, 80.0, 100000, 500, 80.0, 120000, 400))
    # bars straddling the decision time
    for m, close in enumerate([24180, 24190, 24200, 24230, 24260, 24280]):
        ts = f"2026-07-08T0{4 + m // 3}:{(m % 3) * 20:02d}:00Z"
        c.execute("INSERT INTO price_bars VALUES ('NSE','NIFTY','1m',?,?,?,?,?,?)",
                  (ts, close, close + 5, close - 5, close, 1000 + m * 10))
    c.commit(); c.close()
    return path, exp


def test_chain_as_of_never_sees_future():
    path, exp = _make_db()
    da = DataAccess(path)
    # decision at 05:00 must see capture 2 (05:00), never capture 3 (06:00)
    snap = da.chain_as_of("2026-07-08T05:30:00Z", exp)
    assert snap.capture_id == 2
    assert snap.spot == 24260.0
    # decision before any capture -> None
    assert da.chain_as_of("2026-07-08T03:00:00Z", exp) is None


def test_bars_backward_only():
    path, exp = _make_db()
    da = DataAccess(path)
    bars = da.bars("NIFTY", "1m", end="2026-07-08T04:40:00Z", limit=100)
    assert all(b["ts"] <= "2026-07-08T04:40:00Z" for b in bars)
    # ascending order
    assert bars == sorted(bars, key=lambda b: b["ts"])


def test_list_captures_filtered_by_expiry_and_time():
    path, exp = _make_db()
    da = DataAccess(path)
    caps = da.list_captures(expiry=exp, start="2026-07-08T04:30:00Z")
    assert [c["capture_id"] for c in caps] == [2, 3]


def test_days_to_expiry_positive_floor():
    assert days_to_expiry("2026-07-14T06:00:00.000Z", "2026-07-14T06:00:00.000Z") >= 0
    assert days_to_expiry("2026-07-08T06:00:00Z", "2026-07-14T06:00:00.000Z") > 5


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
