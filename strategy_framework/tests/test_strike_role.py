"""
Tests for option ΔOI reconstruction (option_oi), the strike-role-change signal, and
the breadth_oi wall-reinforcement fix (it must read reconstructed ΔOI, not the empty
oi_chg columns).
"""
import sqlite3
import pytest
from strategy_framework.signals.data_access import DataAccess
from strategy_framework.signals import strike_role, option_oi
from strategy_framework.signals.breadth_oi import _oi_lean

_EXP = "2026-01-08T06:00:00.000Z"


def _mkdb(tmp_path):
    """Two snapshots 30 min apart, spot 24000. Resistance 24100 call OI UNWINDS
    (100→80), support 23900 put OI BUILDS (100→130) — a textbook bullish role flip."""
    db = str(tmp_path / "c.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE captures (capture_id INTEGER, captured_at TEXT, spot REAL, "
                "vix REAL, underlying TEXT, note TEXT)")
    con.execute("CREATE TABLE chain_rows (capture_id INTEGER, expiry TEXT, strike REAL, "
                "call_ltp REAL, call_oi REAL, call_oi_chg REAL, call_volume REAL, call_iv REAL, "
                "put_ltp REAL, put_oi REAL, put_oi_chg REAL, put_volume REAL, put_iv REAL)")

    def ins(cid, ts, rows):
        con.execute("INSERT INTO captures VALUES (?,?,?,?,?,?)", (cid, ts, 24000.0, 12.0, "NIFTY", ""))
        for k, coi, poi in rows:
            con.execute("INSERT INTO chain_rows (capture_id,expiry,strike,call_ltp,call_oi,"
                        "call_oi_chg,put_ltp,put_oi,put_oi_chg) VALUES (?,?,?,?,?,0,?,?,0)",
                        (cid, _EXP, k, 50.0, coi, 50.0, poi))
    ins(1, "2026-01-08T05:15:00Z", [(23900, 20, 100), (24000, 90, 95), (24100, 100, 20)])
    ins(2, "2026-01-08T05:45:00Z", [(23900, 20, 130), (24000, 90, 95), (24100, 80, 20)])
    con.commit(); con.close()
    return db


def test_reconstruct_doi_from_levels(tmp_path):
    da = DataAccess(_mkdb(tmp_path))
    ch = da.chain_as_of("2026-01-08T05:45:00Z", _EXP)
    doi = option_oi.reconstruct_doi(da, ch, "2026-01-08T05:45:00Z")
    assert doi is not None
    assert doi["call_doi"][24100] == -20        # resistance calls unwound
    assert doi["put_doi"][23900] == 30          # support puts built


def test_reconstruct_returns_none_without_prior(tmp_path):
    da = DataAccess(_mkdb(tmp_path))
    ch = da.chain_as_of("2026-01-08T05:15:00Z", _EXP)     # first snapshot → no earlier one
    assert option_oi.reconstruct_doi(da, ch, "2026-01-08T05:15:00Z") is None


def test_strike_role_detects_bullish_flip(tmp_path):
    da = DataAccess(_mkdb(tmp_path))
    ch = da.chain_as_of("2026-01-08T05:45:00Z", _EXP)
    s = strike_role.compute(da, "2026-01-08T05:45:00Z", {"chain": ch})
    assert s.status == "OK"
    assert s.score > 0.3                        # resistance dissolving + support building = bullish
    assert "bullish" in s.detail["read"]
    assert s.detail["resistance_strike"] == 24100 and s.detail["support_strike"] == 23900


def test_strike_role_insufficient_history(tmp_path):
    da = DataAccess(_mkdb(tmp_path))
    ch = da.chain_as_of("2026-01-08T05:15:00Z", _EXP)
    s = strike_role.compute(da, "2026-01-08T05:15:00Z", {"chain": ch})
    assert s.status == "INSUFFICIENT_HISTORY"


def test_breadth_oi_lean_uses_reconstructed_doi():
    """_oi_lean's reinforcement term must move with reconstructed ΔOI (not stay 0)."""
    class Ch:
        spot = 24000.0
        strikes = [23900, 24000, 24100]
        call_oi = {23900: 20, 24000: 90, 24100: 100}
        put_oi = {23900: 130, 24000: 95, 24100: 20}
    bullish = _oi_lean(Ch(), {"call_doi": {24100: -20}, "put_doi": {23900: 30}})
    none = _oi_lean(Ch(), None)                 # no ΔOI → build term neutral
    assert bullish is not None and none is not None
    assert bullish["score"] != none["score"]    # the reinforcement term actually bites now
