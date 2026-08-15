"""
VIX source + as-of contract (D-SC-05).

Two things are locked down here:

1. SOURCE — VIX comes from the INDIAVIX series in `price_bars`, never from
   `captures.vix`. That column is a constant 12.0 across all 13,126 captures: a
   placeholder, not a measurement. `backend/quant/data_quality_agent.py:52` has been
   flagging it COLUMN_DEAD all along.

2. NO LOOKAHEAD, per timeframe — the two series need different rules:
     * 1m bars carry a real intraday timestamp ('2026-08-14T10:00:00Z'), so
       `ts <= now` is a true backward as-of join.
     * 1d bars are stamped at MIDNIGHT of their trading date ('2026-08-14T00:00:00')
       but carry that date's CLOSE. `ts <= now` would hand a 09:30 IST decision that
       same evening's closing VIX. Daily bars are visible only from a strictly earlier
       trading date, unless `now` is at/after the 15:30 IST close.

Run:  PYTHONPATH=./ pytest strategy_framework/tests/test_vix_source.py
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from strategy_framework.signals.data_access import DataAccess

PLACEHOLDER = 12.0          # what captures.vix always is
EXPIRY = "2026-07-14T06:00:00.000Z"


def _db(tmp_path, with_1m=True, with_1d=True):
    """Synthetic store: captures carry the 12.0 placeholder, price_bars carry truth."""
    path = str(tmp_path / "vix.db")
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
    c.execute("INSERT INTO captures (capture_id,captured_at,spot,vix,underlying) "
              "VALUES (?,?,?,?,?)", (1, "2026-07-08T06:00:00Z", 24200.0, PLACEHOLDER, "NIFTY"))
    for k in (24100.0, 24200.0, 24300.0):
        c.execute("INSERT INTO chain_rows (capture_id,expiry,strike,call_ltp,call_oi,"
                  "call_oi_chg,put_ltp,put_oi,put_oi_chg) VALUES (?,?,?,?,?,?,?,?,?)",
                  (1, EXPIRY, k, 80.0, 100000, 500, 80.0, 120000, 400))
    if with_1d:
        # midnight-stamped, but each value IS that date's CLOSE
        for d, close in [("2026-07-06", 18.5), ("2026-07-07", 19.5), ("2026-07-08", 25.0)]:
            c.execute("INSERT INTO price_bars VALUES ('NSE','INDIAVIX','1d',?,?,?,?,?,0)",
                      (f"{d}T00:00:00", close, close, close, close))
    if with_1m:
        for hh, close in [("04:00", 21.0), ("05:00", 21.5), ("06:00", 22.0)]:
            c.execute("INSERT INTO price_bars VALUES ('NSE','INDIAVIX','1m',?,?,?,?,?,0)",
                      (f"2026-07-08T{hh}:00Z", close, close, close, close))
    c.commit(); c.close()
    return path


# --------------------------------------------------------------------------------
# source
# --------------------------------------------------------------------------------
def test_vix_comes_from_price_bars_not_captures(tmp_path):
    """captures.vix is 12.0 here. If 12.0 comes back, the placeholder is still wired in."""
    da = DataAccess(_db(tmp_path))
    v, src = da.vix_as_of("2026-07-08T06:00:00Z")
    assert v is not None
    assert v != PLACEHOLDER, "still reading captures.vix"
    assert v == pytest.approx(22.0)
    assert src == "1m"


def test_chain_snapshot_vix_is_not_the_placeholder(tmp_path):
    da = DataAccess(_db(tmp_path))
    chain = da.chain_as_of("2026-07-08T06:00:00Z", EXPIRY)
    assert chain is not None
    assert chain.vix != PLACEHOLDER
    assert chain.vix == pytest.approx(22.0)


def test_latest_vix_delegates_to_the_same_source(tmp_path):
    da = DataAccess(_db(tmp_path))
    assert da.latest_vix("2026-07-08T06:00:00Z") == pytest.approx(22.0)


# --------------------------------------------------------------------------------
# no lookahead — the whole point
# --------------------------------------------------------------------------------
def test_1m_series_is_a_true_backward_as_of_join(tmp_path):
    da = DataAccess(_db(tmp_path))
    assert da.vix_as_of("2026-07-08T04:30:00Z")[0] == pytest.approx(21.0)   # not 21.5/22.0
    assert da.vix_as_of("2026-07-08T05:30:00Z")[0] == pytest.approx(21.5)
    assert da.vix_as_of("2026-07-08T06:00:00Z")[0] == pytest.approx(22.0)


def test_daily_bar_does_not_leak_the_same_day_close(tmp_path):
    """THE regression guard. 2026-07-08 closes at 25.0. A decision at 09:30 IST on
    2026-07-08 must see 2026-07-07's 19.5 — the 25.0 has not happened yet."""
    da = DataAccess(_db(tmp_path, with_1m=False))
    v, src = da.vix_as_of("2026-07-08T04:00:00Z")        # 09:30 IST
    assert src == "1d"
    assert v == pytest.approx(19.5), "same-day close leaked into an intraday decision"
    assert v != pytest.approx(25.0)


def test_daily_bar_becomes_visible_after_the_1530_ist_close(tmp_path):
    """At/after 15:30 IST (10:00 UTC) that day's own close IS knowable."""
    da = DataAccess(_db(tmp_path, with_1m=False))
    assert da.vix_as_of("2026-07-08T10:00:00Z")[0] == pytest.approx(25.0)   # exactly 15:30 IST
    assert da.vix_as_of("2026-07-08T12:00:00Z")[0] == pytest.approx(25.0)   # 17:30 IST


def test_daily_boundary_is_exactly_1530_ist(tmp_path):
    """One minute before the close, the day's close must still be invisible."""
    da = DataAccess(_db(tmp_path, with_1m=False))
    assert da.vix_as_of("2026-07-08T09:59:00Z")[0] == pytest.approx(19.5)   # 15:29 IST
    assert da.vix_as_of("2026-07-08T10:00:00Z")[0] == pytest.approx(25.0)   # 15:30 IST


def test_1m_is_preferred_over_1d_when_it_covers_the_moment(tmp_path):
    """Both series present: the honestly-timestamped one wins."""
    da = DataAccess(_db(tmp_path))
    v, src = da.vix_as_of("2026-07-08T06:00:00Z")
    assert src == "1m" and v == pytest.approx(22.0)


def test_falls_back_to_daily_before_the_1m_series_starts(tmp_path):
    """The 1m series only goes back ~6 weeks; the daily series reaches 2018."""
    da = DataAccess(_db(tmp_path))
    v, src = da.vix_as_of("2026-07-07T06:00:00Z")    # before any 1m bar
    assert src == "1d"
    assert v == pytest.approx(18.5)                  # 07-06's close, not 07-07's


# --------------------------------------------------------------------------------
# absence is reported, never fabricated
# --------------------------------------------------------------------------------
def test_returns_none_when_no_vix_is_knowable(tmp_path):
    """checked-and-absent != silently zero, and != a placeholder."""
    da = DataAccess(_db(tmp_path))
    v, src = da.vix_as_of("2026-07-05T06:00:00Z")    # before every bar
    assert v is None and src is None


def test_no_vix_series_at_all_returns_none_not_the_capture_value(tmp_path):
    da = DataAccess(_db(tmp_path, with_1m=False, with_1d=False))
    assert da.vix_as_of("2026-07-08T06:00:00Z")[0] is None
    assert da.latest_vix("2026-07-08T06:00:00Z") is None
    chain = da.chain_as_of("2026-07-08T06:00:00Z", EXPIRY)
    assert chain.vix is None, "fell back to the captures.vix placeholder"


def test_source_is_reported_for_provenance(tmp_path):
    da = DataAccess(_db(tmp_path))
    assert da.vix_as_of("2026-07-08T06:00:00Z")[1] == "1m"
    assert da.vix_as_of("2026-07-07T06:00:00Z")[1] == "1d"
    assert da.vix_as_of("2026-07-05T06:00:00Z")[1] is None
