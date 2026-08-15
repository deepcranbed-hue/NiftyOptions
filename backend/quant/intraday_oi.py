"""
intraday_oi.py
==============
Intraday futures price × open-interest read — "who drove the move": real conviction
(fresh shorts/longs) vs intraday/leveraged churn (unwinding/covering).

For a session it splits the day into legs (morning / midday / afternoon) and, per leg,
combines Δprice with ΔOI on NIFTY_FUT_1:

    price ↓ + OI ↑  = SHORT BUILDUP     (fresh aggressive shorts — conviction, real)
    price ↓ + OI ↓  = LONG UNWINDING    (longs bailing — intraday/leveraged, bounce-prone)
    price ↑ + OI ↑  = LONG BUILDUP      (fresh longs — bullish)
    price ↑ + OI ↓  = SHORT COVERING    (bounce that can fade)
    move on ~flat OI = CHURN            (intraday, no fresh positioning)

Timestamps are UTC in the DB; IST = UTC+5:30. Session ~09:15–15:30 IST = 03:45–10:00Z.
Legs: morning = open→10:00 IST (04:30Z); afternoon = 13:30 IST (08:00Z)→close.
NO OI in the row → returns available=False with a clear note (nothing faked).
"""
from __future__ import annotations
import os
import sqlite3
from typing import Optional

# leg boundaries in UTC time-of-day (IST-5:30)
_MORNING_END = "04:30:00"   # 10:00 IST
_AFTERNOON_START = "08:00:00"  # 13:30 IST


def _has_oi(conn) -> bool:
    try:
        return "open_interest" in [r[1] for r in conn.execute("PRAGMA table_info(price_bars)")]
    except Exception:
        return False


def _label(dp_pct: float, doi_pct: Optional[float]) -> tuple:
    if doi_pct is None:
        return "oi_unavailable", "OI not captured for this leg"
    price_flat = abs(dp_pct) < 0.05
    oi_flat = abs(doi_pct) < 0.5
    # Flat price is NOT one thing. Flat price + flat OI = genuine day-trader noise
    # (churn). Flat price + HEAVY OI build = two-sided positioning stacking up with no
    # net direction yet — a COILED / pressure state, the opposite of noise. The old
    # rule collapsed both into 'churn', hiding exactly the days (e.g. 29-Jun: +40% OI,
    # flat price) where big money is being committed but hasn't resolved. We split them
    # so the OI axis stops mislabeling coiled days as noise. (Direction — long vs short
    # buildup — still needs a price sign, so coiled carries no directional lean.)
    if price_flat:
        if oi_flat:
            return "churn", "flat price & ~flat OI — day-trader churn (noise)"
        return "coiled", "flat price but OI building heavily — two-sided positioning, unresolved"
    if oi_flat:
        return "churn", "move on ~flat OI — intraday churn, not fresh positioning"
    down, oi_up = dp_pct < 0, doi_pct > 0
    if down and oi_up:
        return "short_buildup", "price ↓ + OI ↑ — fresh SHORTS (conviction, real selling)"
    if down and not oi_up:
        return "long_unwinding", "price ↓ + OI ↓ — LONGS unwinding (intraday/leveraged, bounce-prone)"
    if not down and oi_up:
        return "long_buildup", "price ↑ + OI ↑ — fresh LONGS (bullish conviction)"
    return "short_covering", "price ↑ + OI ↓ — SHORT COVERING (bounce that can fade)"


def _leg(rows: list, i0: int, i1: int) -> dict:
    a, b = rows[i0], rows[i1]
    dp = b["close"] - a["close"]
    dp_pct = (dp / a["close"] * 100.0) if a["close"] else 0.0
    # OI can be null/0 on the first bars of the day (capture starts after open); anchor
    # on the nearest bars WITHIN the leg that actually carry OI so a missing open bar
    # doesn't blank the whole leg.
    oi_a = next((rows[k]["oi"] for k in range(i0, i1 + 1) if rows[k]["oi"]), None)
    oi_b = next((rows[k]["oi"] for k in range(i1, i0 - 1, -1) if rows[k]["oi"]), None)
    doi = doi_pct = None
    if oi_a and oi_b:
        doi = oi_b - oi_a
        doi_pct = doi / oi_a * 100.0
    kind, read = _label(dp_pct, doi_pct)
    return {"from": a["ts"], "to": b["ts"],
            "d_price_pts": round(dp, 1), "d_price_pct": round(dp_pct, 2),
            "d_oi": int(doi) if doi is not None else None,
            "d_oi_pct": round(doi_pct, 2) if doi_pct is not None else None,
            "kind": kind, "read": read}


def _label_vol(dp_pct: float, vol_ratio: float | None) -> tuple:
    """VOLUME-based conviction proxy for symbols WITHOUT futures OI (e.g. single
    stocks). Uses participation (leg volume vs the day's average) in place of OI.
    IMPORTANT: this is WEAKER than OI — volume shows participation but cannot tell a
    NEW position from a CLOSING one (a rally on heavy volume may be fresh buyers OR
    shorts covering). Kinds are the OI analogues so the UI reuses the same colours.

        price ↑ + heavy vol → 'long_buildup'   (accumulation — bullish participation)
        price ↓ + heavy vol → 'short_buildup'  (distribution — bearish participation)
        price ↑ + light vol → 'short_covering' (weak rally — hollow)
        price ↓ + light vol → 'long_unwinding' (weak selloff — hollow)
    """
    if vol_ratio is None:
        return "oi_unavailable", "no volume for this leg"
    avg_vol = 0.9 <= vol_ratio <= 1.1
    if abs(dp_pct) < 0.05:
        # flat price on HEAVY volume = accumulation/distribution battle (coiled proxy);
        # flat price on average volume = genuine churn. Mirrors the OI split above.
        return ("churn", "flat price on ~average volume — churn (noise)") if avg_vol \
            else ("coiled", "flat price on HEAVY volume — two-sided battle, unresolved (proxy: no OI)")
    if avg_vol:
        return "churn", "move on ~average volume — no participation edge"
    down, heavy = dp_pct < 0, vol_ratio > 1.1
    if down and heavy:
        return "short_buildup", "price ↓ on HEAVY volume — distribution (proxy: no OI)"
    if down and not heavy:
        return "long_unwinding", "price ↓ on LIGHT volume — weak selloff, bounce-prone (proxy)"
    if not down and heavy:
        return "long_buildup", "price ↑ on HEAVY volume — accumulation (proxy: no OI)"
    return "short_covering", "price ↑ on LIGHT volume — weak rally that can fade (proxy)"


def _leg_vol(rows: list, i0: int, i1: int, day_avg_vol: float) -> dict:
    a, b = rows[i0], rows[i1]
    dp = b["close"] - a["close"]
    dp_pct = (dp / a["close"] * 100.0) if a["close"] else 0.0
    vols = [rows[k].get("vol") or 0.0 for k in range(i0, i1 + 1)]
    leg_avg = (sum(vols) / len(vols)) if vols else 0.0
    vol_ratio = (leg_avg / day_avg_vol) if day_avg_vol > 0 else None
    kind, read = _label_vol(dp_pct, vol_ratio)
    return {"from": a["ts"], "to": b["ts"], "d_price_pts": round(dp, 1),
            "d_price_pct": round(dp_pct, 2),
            "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
            "d_oi": None, "d_oi_pct": None, "kind": kind, "read": read, "proxy": "volume"}


def analyze_volume(db_path: str, date: str, symbol: str = "NIFTY") -> dict:
    """Volume-proxy positioning for a CASH symbol (no futures OI needed). Same legs
    as analyze(), classified by price × participation. Weaker than OI — flagged
    proxy='volume' throughout. Returns available=False for a symbol with no volume
    (e.g. the NIFTY index carries none)."""
    if not db_path or not os.path.exists(db_path):
        return {"available": False, "note": "db not found"}
    try:
        con = sqlite3.connect(db_path)
        rows = [{"ts": r[0], "close": r[1], "vol": r[2] or 0.0} for r in con.execute(
            "SELECT ts, close, volume FROM price_bars WHERE symbol=? AND timeframe='1m' "
            "AND substr(ts,1,10)=? ORDER BY ts", (symbol, date)).fetchall()]
        con.close()
    except Exception as e:
        return {"available": False, "note": f"{type(e).__name__}: {e}"}
    if len(rows) < 3:
        return {"available": False, "note": f"no/too few {symbol} 1m bars for {date}"}
    day_avg = sum(r["vol"] for r in rows) / len(rows)
    if day_avg <= 0:
        return {"available": False, "note": f"{symbol} carries no volume (e.g. an index) — no volume proxy"}

    def _time(r):
        return r["ts"][11:19]
    i_open, i_close = 0, len(rows) - 1
    i_morn = max((i for i, r in enumerate(rows) if _time(r) <= _MORNING_END), default=i_open)
    i_noon = next((i for i, r in enumerate(rows) if _time(r) >= _AFTERNOON_START), i_close)
    legs = {"full_day": _leg_vol(rows, i_open, i_close, day_avg),
            "morning": _leg_vol(rows, i_open, i_morn, day_avg) if i_morn > i_open else None,
            "midday": _leg_vol(rows, i_morn, i_noon, day_avg) if i_noon > i_morn else None,
            "afternoon": _leg_vol(rows, i_noon, i_close, day_avg) if i_close > i_noon else None}
    return {"available": True, "symbol": symbol, "date": date, "legs": legs,
            "proxy": "volume",
            "note": "VOLUME proxy (no futures OI): participation, not positioning — cannot "
                    "distinguish new positions from closing ones. Weaker than OI."}


def analyze(db_path: str, date: str, symbol: str = "NIFTY_FUT_1") -> dict:
    """Split `date`'s session into legs and label each by price × ΔOI."""
    if not db_path or not os.path.exists(db_path):
        return {"available": False, "note": "db not found"}
    try:
        con = sqlite3.connect(db_path)
        if not _has_oi(con):
            con.close()
            return {"available": False, "note": "price_bars has no open_interest column — capture OI to enable"}
        rows = [{"ts": r[0], "close": r[1], "oi": r[2]} for r in con.execute(
            "SELECT ts, close, open_interest FROM price_bars WHERE symbol=? AND timeframe='1m' "
            "AND substr(ts,1,10)=? ORDER BY ts", (symbol, date)).fetchall()]
        con.close()
    except Exception as e:
        return {"available": False, "note": f"{type(e).__name__}: {e}"}

    if len(rows) < 3:
        return {"available": False, "note": f"no/too few {symbol} 1m bars for {date}"}
    if all(r["oi"] is None for r in rows):
        return {"available": False, "note": f"{symbol} bars have empty OI for {date}"}

    def _time(r):
        return r["ts"][11:19]
    # boundary indices
    i_open, i_close = 0, len(rows) - 1
    i_morn = max((i for i, r in enumerate(rows) if _time(r) <= _MORNING_END), default=i_open)
    i_noon = next((i for i, r in enumerate(rows) if _time(r) >= _AFTERNOON_START), i_close)

    legs = {"full_day": _leg(rows, i_open, i_close),
            "morning": _leg(rows, i_open, i_morn) if i_morn > i_open else None,
            "midday": _leg(rows, i_morn, i_noon) if i_noon > i_morn else None,
            "afternoon": _leg(rows, i_noon, i_close) if i_close > i_noon else None}

    # verdict: is the day's move driven by conviction or intraday/leverage?
    fd = legs["full_day"]
    culprit = ("Conviction — fresh futures positioning" if fd["kind"] in ("short_buildup", "long_buildup")
               else "Coiled — heavy two-sided positioning, unresolved" if fd["kind"] == "coiled"
               else "Intraday / leveraged — unwinding or churn" if fd["kind"] in ("long_unwinding", "short_covering", "churn")
               else "Unknown (no OI)")

    # optional: intraday-churn share from delivery cache (100 - NIFTY-50 delivery %)
    churn_share = None
    try:
        from backend.quant.money_sentiment import read_delivery
        d = read_delivery(target_date=date)
        if d.get("delivery_pct") is not None:
            churn_share = round(100.0 - d["delivery_pct"], 1)
    except Exception:
        pass

    return {"available": True, "symbol": symbol, "date": date, "legs": legs,
            "verdict": culprit,
            "intraday_churn_share_pct": churn_share,
            "note": "price×ΔOI on the near future. Delivery-based churn share (if present) = "
                    "100 − NIFTY-50 delivery %."}
