"""Shock-Recovery (/api/shock-recovery) — the VIX-filtered dip-buy flagger.

Serves the validated historical over-bouncer stats (shock_recovery_v2.json) PLUS today's live
state from price_bars: is today a macro shock (NIFTY < thresh), is VIX elevated, and is each name
above its own 200-day MA. The setup is live only when shock + elevated VIX coincide.

HONEST LABEL (per SECTOR_INTELLIGENCE_FRAMEWORK.md): this is a TECHNICAL mean-reversion edge, not
a quality signal — ROE has ~0 correlation with the bounce (roe_corr in the payload). The VIX
filter is the edge; the over-bouncers are high-beta cyclicals; size for the tail (worst column).
Live state cached 10 min so it never hammers the DB; nothing computes on app start.
"""
from __future__ import annotations  # backend runs py3.9

import json
import os
import sqlite3
import time

from fastapi import APIRouter, HTTPException

router = APIRouter()

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JSON = os.path.join(_REPO, "data_agent", "fundamentals", "shock_recovery_v2.json")
_sys_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _sys_root not in __import__("sys").path:
    __import__("sys").path.insert(0, _sys_root)
from db_config import DB_PATH as _DB   # honours $OPTION_CHAINS_DB itself (D-SC-06)
_STATE = os.path.join(_REPO, ".state")
_CACHE = os.path.join(_STATE, "shock_recovery_live.json")
_TTL = 10 * 60


def _live(symbols: list) -> dict:
    """Today's state: NIFTY last-day return, VIX level, and each name's 200DMA position."""
    con = sqlite3.connect(_DB)
    cur = con.cursor()

    def closes(sym, n):
        cur.execute("SELECT close FROM price_bars WHERE symbol=? AND timeframe='1d' "
                    "AND close IS NOT NULL ORDER BY ts DESC LIMIT ?", (sym, n))
        return [float(r[0]) for r in cur.fetchall()][::-1]

    nf = closes("NIFTY", 2)
    vx = closes("INDIAVIX", 1)
    nifty_ret = round((nf[-1] / nf[-2] - 1) * 100, 2) if len(nf) == 2 else None
    vix = round(vx[-1], 1) if vx else None
    above = {}
    for s in symbols:
        cl = closes(s, 200)
        if len(cl) >= 100:
            above[s] = bool(cl[-1] > (sum(cl) / len(cl)))
    con.close()
    return {"fetched_at": time.time(), "nifty_ret": nifty_ret, "vix": vix, "above": above}


@router.get("/api/shock-recovery")
def shock_recovery(force: bool = False):
    if not os.path.exists(_JSON):
        raise HTTPException(status_code=500, detail="shock_recovery_v2.json not found — run shock_recovery_v2.py")
    with open(_JSON) as f:
        data = json.load(f)
    thresh = data.get("thresh", -1.5)
    hivix = data.get("hivix", 20)
    symbols = [s["sym"] for s in data.get("stocks", [])]

    live = None
    if not force:
        try:
            with open(_CACHE) as f:
                c = json.load(f)
            if time.time() - c.get("fetched_at", 0) <= _TTL:
                live = c
        except Exception:
            pass
    if live is None:
        try:
            live = _live(symbols)
            os.makedirs(_STATE, exist_ok=True)
            with open(_CACHE, "w") as f:
                json.dump(live, f)
        except Exception as e:
            live = {"nifty_ret": None, "vix": None, "above": {}, "live_error": str(e)}

    nifty_ret, vix, above = live.get("nifty_ret"), live.get("vix"), live.get("above", {})
    shock_today = nifty_ret is not None and nifty_ret < thresh
    vix_elevated = vix is not None and vix >= hivix
    for s in data["stocks"]:
        s["above_200dma"] = above.get(s["sym"])

    return {"success": True, "as_of": data.get("as_of"), "thresh": thresh, "hivix": hivix,
            "roe_corr": data.get("roe_corr"), "pooled": data.get("pooled"),
            "nifty_ret": nifty_ret, "vix": vix, "shock_today": shock_today,
            "vix_elevated": vix_elevated, "setup_active": bool(shock_today and vix_elevated),
            "live_error": live.get("live_error"), "stocks": data["stocks"]}
