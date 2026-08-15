"""
Tests for the three expiry-day option-chain signals: pin_pressure (reversion-to-pin),
oi_migration (COG mass shift), straddle_flow (vol-regime gate), plus the shared
option_oi chain helpers. Two synthetic snapshots 30 min apart.
"""
import sqlite3
from strategy_framework.signals.data_access import DataAccess
from strategy_framework.signals import (pin_pressure, oi_migration, straddle_flow,
                                        oi_dispersion, oi_entropy, option_oi)

_EXP = "2026-01-08T06:00:00.000Z"


def _mkdb(tmp_path, rows_t0, rows_t1, spot=24000.0):
    """rows = [(strike, call_ltp, call_oi, put_ltp, put_oi), ...]."""
    db = str(tmp_path / "c.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE captures (capture_id INTEGER, captured_at TEXT, spot REAL, "
                "vix REAL, underlying TEXT, note TEXT)")
    con.execute("CREATE TABLE chain_rows (capture_id INTEGER, expiry TEXT, strike REAL, "
                "call_ltp REAL, call_oi REAL, call_oi_chg REAL, call_volume REAL, call_iv REAL, "
                "put_ltp REAL, put_oi REAL, put_oi_chg REAL, put_volume REAL, put_iv REAL)")

    def ins(cid, ts, rows):
        con.execute("INSERT INTO captures VALUES (?,?,?,?,?,?)", (cid, ts, spot, 12.0, "NIFTY", ""))
        for k, cl, coi, pl, poi in rows:
            con.execute("INSERT INTO chain_rows (capture_id,expiry,strike,call_ltp,call_oi,"
                        "call_oi_chg,put_ltp,put_oi,put_oi_chg) VALUES (?,?,?,?,?,0,?,?,0)",
                        (cid, _EXP, k, cl, coi, pl, poi))
    ins(1, "2026-01-08T05:15:00Z", rows_t0)
    ins(2, "2026-01-08T05:45:00Z", rows_t1)
    con.commit(); con.close()
    return DataAccess(db)


def test_option_oi_helpers(tmp_path):
    rows = [(23900, 40, 50, 60, 100), (24000, 74, 300, 74, 300), (24100, 60, 40, 40, 30)]
    da = _mkdb(tmp_path, rows, rows)
    ch = da.chain_as_of("2026-01-08T05:45:00Z", _EXP)
    S, k = option_oi.atm_straddle(ch)
    assert k == 24000 and abs(S - 148.0) < 1e-6            # 74 + 74
    pin_k, pin_oi, share = option_oi.pin_strike(ch)
    assert pin_k == 24000 and pin_oi == 600               # 300 + 300, the biggest wall
    assert 0 < share < 1
    assert option_oi.oi_cog(ch, "call") is not None


def test_pin_pressure_is_nondirectional_strength(tmp_path):
    # strong, concentrated pin at 24100 → HIGH strength, but NO direction (score in [0,1]).
    rows = [(23900, 40, 30, 60, 40), (24000, 74, 60, 74, 60), (24100, 60, 900, 40, 900)]
    da = _mkdb(tmp_path, rows, rows, spot=24000.0)
    ch = da.chain_as_of("2026-01-08T05:45:00Z", _EXP)
    s = pin_pressure.compute(da, "2026-01-08T05:45:00Z", {"chain": ch})
    assert s.status == "OK"
    assert s.detail["pin_strike"] == 24100
    assert 0.0 <= s.score <= 1.0 and s.score > 0.5         # strong pin, non-directional strength
    assert "regime" in s.detail                            # it's a regime read, not a vote


def test_dispersion_and_entropy_flag_concentration(tmp_path):
    # crowded: one huge strike vs the rest → high tightness AND high crowding
    crowded = [(23900, 40, 5, 60, 5), (24000, 74, 1000, 74, 1000), (24100, 60, 5, 40, 5)]
    da = _mkdb(tmp_path, crowded, crowded)
    ch = da.chain_as_of("2026-01-08T05:45:00Z", _EXP)
    d = oi_dispersion.compute(da, "2026-01-08T05:45:00Z", {"chain": ch})
    e = oi_entropy.compute(da, "2026-01-08T05:45:00Z", {"chain": ch})
    assert d.status == "OK" and e.status == "OK"
    assert d.score > 0.6 and e.score > 0.5                 # concentrated → both read 'pinned'
    # diffuse: OI spread evenly → lower tightness / crowding
    diffuse = [(23900, 40, 300, 60, 300), (24000, 74, 300, 74, 300), (24100, 60, 300, 40, 300)]
    sub = tmp_path / "b"; sub.mkdir()
    da2 = _mkdb(sub, diffuse, diffuse)
    ch2 = da2.chain_as_of("2026-01-08T05:45:00Z", _EXP)
    e2 = oi_entropy.compute(da2, "2026-01-08T05:45:00Z", {"chain": ch2})
    assert e2.score < e.score                              # more distributed → less crowded


def test_oi_migration_bullish_when_mass_rises(tmp_path):
    # both call & put OI mass shift UP from t0 to t1 → bullish migration
    t0 = [(23900, 40, 200, 60, 200), (24000, 74, 100, 74, 100), (24100, 60, 50, 40, 50)]
    t1 = [(23900, 40, 50, 60, 50), (24000, 74, 100, 74, 100), (24100, 60, 200, 40, 200)]
    da = _mkdb(tmp_path, t0, t1)
    ch = da.chain_as_of("2026-01-08T05:45:00Z", _EXP)
    s = oi_migration.compute(da, "2026-01-08T05:45:00Z", {"chain": ch})
    assert s.status == "OK"
    assert s.score > 0 and s.detail["migration_pts"] > 0
    assert s.detail["sides_agree"] is True


def test_straddle_flow_signs_compression_and_expansion(tmp_path):
    # straddle 148 → 100 = compression (score negative); no direction implied
    t0 = [(24000, 74, 300, 74, 300)]
    t1 = [(24000, 50, 300, 50, 300)]
    da = _mkdb(tmp_path, t0, t1)
    ch = da.chain_as_of("2026-01-08T05:45:00Z", _EXP)
    s = straddle_flow.compute(da, "2026-01-08T05:45:00Z", {"chain": ch})
    assert s.status == "OK"
    assert s.score < 0 and s.detail["change_pct"] < 0     # compression
    assert "compression" in s.detail["regime"]


def test_expiry_signals_insufficient_history(tmp_path):
    rows = [(24000, 74, 300, 74, 300)]
    da = _mkdb(tmp_path, rows, rows)
    ch = da.chain_as_of("2026-01-08T05:15:00Z", _EXP)     # first snapshot
    assert oi_migration.compute(da, "2026-01-08T05:15:00Z", {"chain": ch}).status == "INSUFFICIENT_HISTORY"
    assert straddle_flow.compute(da, "2026-01-08T05:15:00Z", {"chain": ch}).status == "INSUFFICIENT_HISTORY"
