"""
money_sentiment.py
==================
Market-Intelligence "Money vs Sentiment" view.

Separates the scary headline ("₹X lakh crore wiped out") into:
  * EVAPORATED (notional)  = |day move %| × total market cap   -> mostly repricing/sentiment
  * WITHDRAWN  (real)      = -(net FII + net DII cash)          -> money that actually left (+)
  * SENTIMENT share        = 1 - max(0, withdrawn)/evaporated   -> how much was just fear
and classifies the day into one of four regimes by combining the index move,
net institutional flow, and NIFTY-50 delivery % (conviction) vs its baseline.

Delivery % comes from .state/delivery_cache.json (written by
scratch_scripts/download_nse_delivery.py). FII/DII from .state/flows_cash_cache.json
(written by flows_fetcher). Everything degrades gracefully to None when a source
is missing — the view flags it rather than faking. Decision-support / PRIOR, not
calibrated edge.
"""
from __future__ import annotations
import csv
import io
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime
from typing import Optional

# framework NIFTY-50 weights (for the index-weighted delivery number)
_FW = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "strategy_framework")
if _FW not in sys.path:
    sys.path.insert(0, _FW)
try:
    from config import constituents as _K
    _NIFTY_W = {s: _K.weight_of(s) for s in _K.symbols()}
except Exception:
    _NIFTY_W = {}

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STATE = os.environ.get("NIFTY_STATE_DIR", os.path.join(_REPO, ".state"))
_DELIV_CACHE = os.path.join(_STATE, "delivery_cache.json")
_FLOWS_CACHE = os.path.join(_STATE, "flows_cash_cache.json")

DEFAULT_MARKET_CAP_CR = 47_000_000.0   # ~₹470 lakh crore (refresh as needed)


# ── data readers (all None-safe) ──────────────────────────────────────────────
def read_delivery(target_date: Optional[str] = None, baseline_n: int = 20) -> dict:
    """NIFTY-50 delivery % for `target_date` (or the latest cached day if None), plus a
    baseline = mean of the days BEFORE it. If target_date is given but absent, returns
    delivery None (flagged) rather than silently falling back to another day."""
    try:
        with open(_DELIV_CACHE) as f:
            cache = json.load(f)
    except Exception:
        return {"date": target_date, "delivery_pct": None, "baseline_pct": None, "n_baseline": 0}
    dates = sorted(cache)
    if not dates:
        return {"date": target_date, "delivery_pct": None, "baseline_pct": None, "n_baseline": 0}

    def _pct(d):
        v = cache[d]
        return v.get("nifty50_index_weighted_pct") or v.get("nifty50_traded_weighted_pct")

    day = target_date or dates[-1]
    prior = [p for d in dates if d < day and (p := _pct(d)) is not None][-baseline_n:]
    baseline = sum(prior) / len(prior) if prior else None
    delivery_pct = _pct(day) if day in cache else None
    return {"date": day, "delivery_pct": delivery_pct, "baseline_pct": baseline,
            "n_baseline": len(prior)}


def read_flows(target_date: Optional[str] = None) -> dict:
    """Net FII/DII cash (₹ cr) for `target_date` (or latest if None) from the flows cache."""
    try:
        with open(_FLOWS_CACHE) as f:
            days = json.load(f)
        if target_date:
            match = [d for d in days if d.get("date") == target_date]
            if not match:
                return {"date": target_date, "fii_cr": None, "dii_cr": None}
            row = match[-1]
        else:
            row = days[-1]
        return {"date": row.get("date"), "fii_cr": row.get("fii_cash"),
                "dii_cr": row.get("dii_cash")}
    except Exception:
        return {"date": target_date, "fii_cr": None, "dii_cr": None}


def read_nifty_move(db_path: Optional[str], target_date: Optional[str] = None) -> Optional[float]:
    """Daily NIFTY % move from price_bars (1d): the close on `target_date` (or latest)
    vs the prior trading day's close."""
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        con = sqlite3.connect(db_path)
        if target_date:
            rows = con.execute(
                "SELECT close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
                "AND substr(ts,1,10) <= ? ORDER BY ts DESC LIMIT 2", (target_date,)).fetchall()
        else:
            rows = con.execute(
                "SELECT close FROM price_bars WHERE symbol='NIFTY' AND timeframe='1d' "
                "ORDER BY ts DESC LIMIT 2").fetchall()
        con.close()
        if len(rows) == 2 and rows[1][0]:
            return (rows[0][0] / rows[1][0] - 1.0) * 100.0
    except Exception:
        return None
    return None


# ── pure classifier ───────────────────────────────────────────────────────────
def classify(move_pct: Optional[float], net_fii_cr: Optional[float],
             net_dii_cr: Optional[float], delivery_pct: Optional[float],
             delivery_baseline: Optional[float],
             market_cap_cr: float = DEFAULT_MARKET_CAP_CR,
             basis_discount: Optional[bool] = None) -> dict:
    """Decomposition + 4-quadrant regime. All inputs optional; missing ones are flagged."""
    move = move_pct or 0.0
    net_inst = (net_fii_cr or 0.0) + (net_dii_cr or 0.0)
    withdrawn = -net_inst                                  # + = money left the market
    evaporated = abs(move) / 100.0 * market_cap_cr         # ₹ cr notional
    real_out = max(0.0, withdrawn)
    sentiment_share = (1.0 - real_out / evaporated) * 100.0 if evaporated > 0 else None
    if sentiment_share is not None:
        sentiment_share = max(0.0, min(100.0, sentiment_share))

    # delivery vs baseline (need a small margin to call it "high")
    delivery_high = None
    if delivery_pct is not None:
        base = delivery_baseline if delivery_baseline else 48.0   # fallback prior
        delivery_high = delivery_pct >= base

    direction = "flat" if abs(move) < 0.15 else ("down" if move < 0 else "up")
    flow_in = net_inst > 0

    # regime
    if direction == "down":
        if delivery_high and flow_in:
            regime, posture = "BUYABLE DIP / ACCUMULATION", "Fade the panic — lean bullish / mean-reversion; real money bought."
        elif delivery_high and not flow_in:
            regime, posture = "REAL DISTRIBUTION", "Respect it — defensive / bearish; genuine selling on the way out."
        elif delivery_high is False:
            regime, posture = "FROTH / CHURN", "Noise — stay small / neutral; move likely to mean-revert."
        else:
            regime, posture = "SENTIMENT DIP (delivery n/a)", "Likely repricing; confirm with delivery + flows."
    elif direction == "up":
        if delivery_high and flow_in:
            regime, posture = "CONVICTION RALLY", "Trend-follow up — real accumulation, not just short-covering."
        elif delivery_high is False:
            regime, posture = "LOW-CONVICTION BOUNCE", "Cautious — thin participation; can fade."
        else:
            regime, posture = "RALLY (delivery n/a)", "Confirm with delivery + flows before chasing."
    else:
        regime, posture = "FLAT / RANGE", "No directional edge from flow/sentiment today."

    if basis_discount:
        posture += " Futures in DISCOUNT → forced deleveraging; size down."

    return {
        "move_pct": round(move, 2),
        "market_cap_cr": market_cap_cr,
        "evaporated_cr": round(evaporated, 0),
        "net_fii_cr": net_fii_cr, "net_dii_cr": net_dii_cr,
        "net_institutional_cr": round(net_inst, 0),
        "withdrawn_cr": round(withdrawn, 0),
        "money_came_in": withdrawn < 0,
        "sentiment_share_pct": round(sentiment_share, 1) if sentiment_share is not None else None,
        "delivery_pct": delivery_pct, "delivery_baseline_pct": round(delivery_baseline, 1) if delivery_baseline else None,
        "delivery_high": delivery_high,
        "regime": regime, "posture": posture,
        "basis_discount": basis_discount,
    }


# ── delivery fetch + store (server-side, triggered by the UI button) ──────────
_BHAV_URL = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"


def _num(x):
    s = str(x).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_and_store_delivery(date_str: str) -> dict:
    """Download NSE sec_bhavdata for a date, compute NIFTY-50 delivery %, and append
    to .state/delivery_cache.json. Runs server-side (needs NSE reachable from the host).
    Returns {ok, ...summary} or {ok:False, error}. Same cache the CLI script writes."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return {"ok": False, "error": "date must be YYYY-MM-DD"}
    url = _BHAV_URL.format(ddmmyyyy=dt.strftime("%d%m%Y"))
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
               "Referer": "https://www.nseindia.com/"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code} — NSE may not have data for {date_str} "
                "(weekend/holiday/not yet published)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # parse EQ rows
    reader = csv.reader(io.StringIO(text.strip()))
    header = [h.strip() for h in next(reader)]
    try:
        ix = {n: header.index(n) for n in ("SYMBOL", "SERIES", "TTL_TRD_QNTY", "DELIV_QTY", "DELIV_PER")}
    except ValueError as e:
        return {"ok": False, "error": f"unexpected columns: {e}"}
    rows = {}
    for r in reader:
        if len(r) <= ix["DELIV_PER"] or r[ix["SERIES"]].strip() != "EQ":
            continue
        traded = _num(r[ix["TTL_TRD_QNTY"]])
        if traded is None:
            continue
        rows[r[ix["SYMBOL"]].strip()] = {"traded": traded,
                                         "deliv": _num(r[ix["DELIV_QTY"]]) or 0.0,
                                         "deliv_pct": _num(r[ix["DELIV_PER"]])}
    if not rows:
        return {"ok": False, "error": "no EQ rows parsed"}

    present = {s: rows[s] for s in _NIFTY_W if s in rows} if _NIFTY_W else {}
    n_tr = sum(r["traded"] for r in present.values())
    n_dl = sum(r["deliv"] for r in present.values())
    traded_w = 100.0 * n_dl / n_tr if n_tr else None
    wsum = pctsum = 0.0
    for s, r in present.items():
        if r["deliv_pct"] is not None:
            wsum += _NIFTY_W[s]; pctsum += _NIFTY_W[s] * r["deliv_pct"]
    idx_w = (pctsum / wsum) if wsum else None
    tot_tr = sum(r["traded"] for r in rows.values())
    tot_dl = sum(r["deliv"] for r in rows.values())
    mkt = 100.0 * tot_dl / tot_tr if tot_tr else None

    summary = {"nifty50_index_weighted_pct": round(idx_w, 2) if idx_w else None,
               "nifty50_traded_weighted_pct": round(traded_w, 2) if traded_w else None,
               "market_eq_pct": round(mkt, 2) if mkt else None,
               "names_found": len(present)}

    # append to the SAME cache read_delivery() reads
    try:
        os.makedirs(_STATE, exist_ok=True)
        cache = {}
        if os.path.exists(_DELIV_CACHE):
            with open(_DELIV_CACHE) as f:
                cache = json.load(f)
        cache[date_str] = summary
        for k in sorted(cache)[:-120]:
            cache.pop(k, None)
        with open(_DELIV_CACHE, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except Exception as e:
        return {"ok": False, "error": f"parsed but could not persist: {e}", **summary}
    return {"ok": True, "date": date_str, **summary}


def build_view(db_path: Optional[str] = None, market_cap_cr: float = DEFAULT_MARKET_CAP_CR,
               move_pct: Optional[float] = None, basis_discount: Optional[bool] = None,
               date: Optional[str] = None) -> dict:
    """Gather inputs AS-OF `date` (or latest if None) and classify. `move_pct` overrides
    the DB-derived move. All three sources honour the same target date."""
    deliv = read_delivery(target_date=date)
    flows = read_flows(target_date=date)
    move = move_pct if move_pct is not None else read_nifty_move(db_path, target_date=date)
    view = classify(move, flows["fii_cr"], flows["dii_cr"],
                    deliv["delivery_pct"], deliv["baseline_pct"],
                    market_cap_cr=market_cap_cr, basis_discount=basis_discount)
    view["requested_date"] = date
    view["as_of"] = {"delivery_date": deliv["date"], "flows_date": flows["date"],
                     "delivery_baseline_n": deliv["n_baseline"]}
    view["sources_ok"] = {"delivery": deliv["delivery_pct"] is not None,
                          "flows": flows["fii_cr"] is not None,
                          "move": move is not None}
    return {"success": True, "view": view}
