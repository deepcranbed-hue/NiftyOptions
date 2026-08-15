"""
Liveness gate regression (capture_layer_fix_brief §4, acceptance row 7).

A gate that does not catch the known-dead columns on known-dead data is itself defective:
  - a session with all-zero bid/ask and a constant VIX MUST flag COLUMN_DEAD;
  - a live session with varying two-sided quotes and a moving VIX MUST pass.
"""
import os
import sqlite3
import tempfile

import pytest

import chain_store
from backend.quant.data_quality_agent import DataQualityAgent

SIDE = chain_store._SIDE_COLS


def _row(cap_id, expiry, strike, cbid, cask, coi):
    cv = {k: 0.0 for k in SIDE}; pv = {k: 0.0 for k in SIDE}
    cv["bid"], cv["ask"], cv["oi"] = cbid, cask, coi
    pv["bid"], pv["ask"], pv["oi"] = cbid, cask, coi
    return [cap_id, expiry, strike] + [cv[k] for k in SIDE] + [pv[k] for k in SIDE]


@pytest.fixture()
def db_path():
    path = tempfile.mktemp(suffix=".db")
    chain_store.init_db(db=path)
    con = sqlite3.connect(path)
    cols = (["capture_id", "expiry", "strike"]
            + [f"call_{k}" for k in SIDE] + [f"put_{k}" for k in SIDE])
    ph = ",".join(["?"] * len(cols))

    # DEAD session: bid/ask all 0.0, VIX constant 12.0, but OI varies (sibling alive)
    for i, minute in enumerate(["04:00", "04:01", "04:02"]):
        con.execute("INSERT INTO captures (capture_id, captured_at, spot, vix) VALUES (?,?,?,?)",
                    (100 + i, f"2026-07-01T{minute}:00.000Z", 24000 + i, 12.0))
        con.executemany(f"INSERT INTO chain_rows ({','.join(cols)}) VALUES ({ph})",
                        [_row(100 + i, "2026-07-31", 24000, 0.0, 0.0, 1000 + i * 50),
                         _row(100 + i, "2026-07-31", 24100, 0.0, 0.0, 2000 + i * 50)])

    # LIVE session: two-sided varying quotes, VIX moves
    for i, (minute, vix) in enumerate([("04:00", 12.1), ("04:01", 12.4), ("04:02", 12.2)]):
        con.execute("INSERT INTO captures (capture_id, captured_at, spot, vix) VALUES (?,?,?,?)",
                    (200 + i, f"2026-07-06T{minute}:00.000Z", 24400 + i, vix))
        con.executemany(f"INSERT INTO chain_rows ({','.join(cols)}) VALUES ({ph})",
                        [_row(200 + i, "2026-07-31", 24000, 100 + i, 102 + i, 1000 + i * 50),
                         _row(200 + i, "2026-07-31", 24100, 80 + i, 83 + i, 2000 + i * 50)])
    con.commit(); con.close()
    yield path
    os.remove(path)


def test_dead_session_flags_bidask_and_vix(db_path):
    agent = DataQualityAgent(db_path=db_path)
    res = agent.check_liveness("2026-07-01")
    assert res["passed"] is False
    dead = {(f["stream"], f["column"]) for f in res["flags"]}
    assert ("option_chain_quotes", "call_bid") in dead
    assert ("option_chain_quotes", "call_ask") in dead
    assert ("indiavix", "vix") in dead


def test_live_session_passes_clean(db_path):
    agent = DataQualityAgent(db_path=db_path)
    res = agent.check_liveness("2026-07-06")
    assert res["passed"] is True, res["flags"]


def test_regression_audit_covers_both_sessions(db_path):
    agent = DataQualityAgent(db_path=db_path)
    audit = agent.run_regression_audit()
    by_date = {r["date"]: r for r in audit}
    assert by_date["2026-07-01"]["passed"] is False   # known-dead caught
    assert by_date["2026-07-06"]["passed"] is True     # live clean
