"""
Capture brief §3: OI must be expiry-scoped (never summed across expiries) and VIX must be
real-or-NULL (never a fabricated constant).
"""
import os
import sqlite3
import tempfile

import pytest

import chain_store

SIDE = chain_store._SIDE_COLS


def _row(cap_id, expiry, strike, coi, poi):
    cv = {k: 0.0 for k in SIDE}; pv = {k: 0.0 for k in SIDE}
    cv["oi"], pv["oi"] = coi, poi
    cv["ltp"], pv["ltp"] = 10.0, 9.0
    return [cap_id, expiry, strike] + [cv[k] for k in SIDE] + [pv[k] for k in SIDE]


@pytest.fixture()
def db_two_expiries():
    path = tempfile.mktemp(suffix=".db")
    chain_store.init_db(db=path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO captures (capture_id, captured_at, spot, vix) "
                "VALUES (1, '2026-07-06T04:00:00.000Z', 24000, 12.3)")
    cols = (["capture_id", "expiry", "strike"]
            + [f"call_{k}" for k in SIDE] + [f"put_{k}" for k in SIDE])
    ph = ",".join(["?"] * len(cols))
    rows = [
        # same strike 24000 exists in TWO expiries with different OI
        _row(1, "2026-07-09", 24000, 100, 500),
        _row(1, "2026-07-09", 24100, 200, 600),
        _row(1, "2026-07-31", 24000, 9999, 8888),   # far expiry, big OI
        _row(1, "2026-07-31", 24100, 7777, 6666),
    ]
    con.executemany(f"INSERT INTO chain_rows ({','.join(cols)}) VALUES ({ph})", rows)
    con.commit(); con.close()
    yield path
    os.remove(path)


def test_load_capture_no_expiry_uses_nearest_only_not_aggregate(db_two_expiries):
    cap = chain_store.load_capture(1, db=db_two_expiries)  # no expiry given
    # nearest expiry is 2026-07-09; strikes must NOT be duplicated across expiries
    assert cap["expiry"] == "2026-07-09"
    assert cap["strikes"] == [24000.0, 24100.0]          # 2 strikes, not 4
    assert cap["call_oi"] == [100, 200]                   # nearest-expiry OI, not the 9999 far one
    assert cap["expiry_auto_selected"] is True
    assert cap["expiry_options"] == ["2026-07-09", "2026-07-31"]


def test_load_capture_explicit_expiry_scopes_correctly(db_two_expiries):
    cap = chain_store.load_capture(1, expiry="2026-07-31", db=db_two_expiries)
    assert cap["expiry"] == "2026-07-31"
    assert cap["call_oi"] == [9999, 7777]
    assert cap["strikes"] == [24000.0, 24100.0]           # still not mixed with the other expiry


# ---------- VIX resolution (never a fabricated constant) ----------

def test_vix_prefers_real_store_value():
    val, src = chain_store.resolve_capture_vix(13.7, 12.0)
    assert (val, src) == (13.7, "STORE")


def test_vix_falls_back_to_client_when_store_absent():
    val, src = chain_store.resolve_capture_vix(None, 12.0)
    assert (val, src) == (12.0, "CLIENT")


def test_vix_absent_persists_null_never_constant():
    val, src = chain_store.resolve_capture_vix(None, None)
    assert val is None and src == "ABSENT"    # NULL, not a placeholder like 12.0
