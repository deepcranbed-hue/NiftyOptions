"""
Price-source contract for the chain_snapshots view (D-CAP-02 / Immutable Rule #5).

Exercises the REAL view DDL (via chain_store.init_db) against a temp DB:
  - mid is DERIVED-ONLY: bid/ask mid when TWO_SIDED, else NULL (never LTP).
  - price falls back to LTP when not two-sided; NULL when neither exists (excluded).
  - price_source tags every row TWO_SIDED / LTP / NONE — no anonymous price.
  - Flipping bid/ask on (real quotes arriving) switches a row to TWO_SIDED with no
    code change — the behaviour the user asked for.
"""
import sqlite3
import tempfile
import os

import pytest

import chain_store

SIDE = chain_store._SIDE_COLS


def _mkrow(cap_id, expiry, strike, c, p):
    """c/p are (bid, ask, ltp) tuples; other side cols default 0.0."""
    cv = {k: 0.0 for k in SIDE}; pv = {k: 0.0 for k in SIDE}
    cv["bid"], cv["ask"], cv["ltp"] = c
    pv["bid"], pv["ask"], pv["ltp"] = p
    return [cap_id, expiry, strike] + [cv[k] for k in SIDE] + [pv[k] for k in SIDE]


@pytest.fixture()
def db_path():
    path = tempfile.mktemp(suffix=".db")
    chain_store.init_db(db=path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO captures (capture_id, captured_at, spot, vix) "
                "VALUES (1, '2026-07-06T04:00:00.000Z', 24000, 12.0)")
    cols = (["capture_id", "expiry", "strike"]
            + [f"call_{k}" for k in SIDE] + [f"put_{k}" for k in SIDE])
    rows = [
        _mkrow(1, "2026-07-31", 24000, (10.0, 12.0, 11.0), (9.0, 11.0, 10.0)),   # two-sided
        _mkrow(1, "2026-07-31", 24100, (0.0, 0.0, 8.0),   (0.0, 0.0, 7.0)),      # LTP only
        _mkrow(1, "2026-07-31", 24200, (0.0, 0.0, 0.0),   (0.0, 0.0, 0.0)),      # nothing
        _mkrow(1, "2026-07-31", 24300, (12.0, 10.0, 9.0), (0.0, 0.0, 0.0)),      # crossed quote (ask<bid)
    ]
    ph = ",".join(["?"] * len(cols))
    con.executemany(f"INSERT INTO chain_rows ({','.join(cols)}) VALUES ({ph})", rows)
    con.commit(); con.close()
    yield path
    os.remove(path)


def _snap(db):
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    out = {(r["strike"], r["cp"]): r for r in con.execute(
        "SELECT strike, cp, bid, ask, ltp, mid, price, price_source, quote_state FROM chain_snapshots")}
    con.close()
    return out


def test_two_sided_row_uses_bidask_mid(db_path):
    r = _snap(db_path)[(24000.0, "call")]
    assert r["quote_state"] == "TWO_SIDED"
    assert r["price_source"] == "MID_2S"
    assert r["mid"] == pytest.approx(11.0)     # (10+12)/2
    assert r["price"] == pytest.approx(11.0)


def test_ltp_only_row_mid_is_null_price_is_ltp(db_path):
    r = _snap(db_path)[(24100.0, "call")]
    assert r["quote_state"] == "NO_QUOTE"      # bid/ask both 0
    assert r["price_source"] == "LTP_RECENT"
    assert r["mid"] is None                    # mid is derived-only, NEVER ltp
    assert r["price"] == pytest.approx(8.0)     # price falls back to ltp


def test_nothing_row_excluded(db_path):
    r = _snap(db_path)[(24200.0, "put")]
    assert r["quote_state"] == "NO_QUOTE"
    assert r["price_source"] == "EXCLUDED"
    assert r["mid"] is None
    assert r["price"] is None                  # excluded — not 0.0


def test_crossed_quote_rejected_and_falls_back_to_ltp(db_path):
    # bid >= ask is a bad tick → CROSSED_LOCKED, not two-sided; price uses ltp
    r = _snap(db_path)[(24300.0, "call")]
    assert r["quote_state"] == "CROSSED_LOCKED"
    assert r["price_source"] == "LTP_RECENT"
    assert r["mid"] is None
    assert r["price"] == pytest.approx(9.0)


def test_one_sided_ask_state(db_path):
    # add a strike with only an ask quote
    con = sqlite3.connect(db_path)
    cols = (["capture_id", "expiry", "strike"]
            + [f"call_{k}" for k in SIDE] + [f"put_{k}" for k in SIDE])
    ph = ",".join(["?"] * len(cols))
    con.execute(f"INSERT INTO chain_rows ({','.join(cols)}) VALUES ({ph})",
                _mkrow(1, "2026-07-31", 24500, (0.0, 6.0, 0.0), (0.0, 0.0, 0.0)))
    con.commit(); con.close()
    r = _snap(db_path)[(24500.0, "call")]
    assert r["quote_state"] == "ONE_SIDED_ASK"


def test_flipping_bidask_on_switches_to_two_sided(db_path):
    # Simulate real quotes arriving on the previously NO_QUOTE strike.
    con = sqlite3.connect(db_path)
    con.execute("UPDATE chain_rows SET call_bid = 7.8, call_ask = 8.2 "
                "WHERE strike = 24100 AND expiry = '2026-07-31'")
    con.commit(); con.close()
    r = _snap(db_path)[(24100.0, "call")]
    assert r["quote_state"] == "TWO_SIDED"      # no code change — view reflects it
    assert r["price_source"] == "MID_2S"
    assert r["mid"] == pytest.approx(8.0)
    assert r["price"] == pytest.approx(8.0)


def test_migration_view_has_all_capture_columns(db_path):
    # Mirrors /api/admin/reinit-capture-view verification: the recreated view must expose
    # every D-CAP-02 column so downstream consumers and the endpoint report succeed.
    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(chain_snapshots)")]
    con.close()
    for required in ["ts", "spot", "strike", "cp", "bid", "ask", "ltp",
                     "mid", "price", "price_source", "quote_state", "oi", "volume"]:
        assert required in cols, f"view missing column {required}"


def test_null_bidask_after_silent_default_fix_is_no_quote(db_path):
    # §1.4: absent bid/ask must persist as NULL (not 0.0). A NULL row is NO_QUOTE/EXCLUDED,
    # and — critically — is NOT mistaken for a real 0-priced two-sided quote.
    con = sqlite3.connect(db_path)
    con.execute("UPDATE chain_rows SET call_bid = NULL, call_ask = NULL, call_ltp = NULL "
                "WHERE strike = 24000 AND expiry = '2026-07-31'")
    con.commit(); con.close()
    r = _snap(db_path)[(24000.0, "call")]
    assert r["quote_state"] == "NO_QUOTE"
    assert r["price_source"] == "EXCLUDED"
    assert r["mid"] is None and r["price"] is None
