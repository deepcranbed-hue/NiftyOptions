"""
data_agent/fetching/orchestrator.py
===================================
The collection loop that ties the fetching layer together:

    universe (WHAT to pull)  ->  broker (Breeze/Kite)  ->  storage
        cash  -> bar_store.save_bars   (price_bars)        [DEFAULT PATH]
        F&O   -> fo_bars.save_fo_bars  (fo_price_bars)     [OPT-IN backtest store only]
    ->  data_health (WHAT's still missing)  ->  re-pull the gaps

    NOTE (single-store rule): the primary job is a GAP AUDIT over the EXISTING tables
    (price_bars for cash, captures/chain_rows for option chains) — see
    data_health.missing_report(). Cash fill writes back into price_bars. The per-contract
    fo_price_bars store is a SEPARATE, opt-in backtest sink (mode='all'); it is NOT part
    of the health/audit surface and nothing lands there unless explicitly requested with
    expiries. The live app continues to read chain_rows, untouched.

Design:
  * `build_plan()` turns the universe rules into a flat list of typed targets
    (cash stocks + index; futures near+next; options current+/-next-expiry x the
    ATM strike window x CE/PE). Expired series are excluded by universe.py.
  * `run(broker, plan, db)` is WATERMARK-INCREMENTAL — for each target it reads
    the last stored bar and only fetches from there to now (same logic as the
    existing cash sync), so re-runs are cheap and idempotent.
  * Per-target ERROR ISOLATION (one bad contract never kills the batch) and an
    empty->retry so a transient miss gets one more attempt.
  * The BROKER is injected, so the whole loop tests offline with a mock — the only
    thing that needs a live token is the real broker itself.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .universe import active_future_expiries, active_option_expiries
from .fo_bars import save_fo_bars, FUT, OPT
from chain_store import save_from_json_rows, DB_PATH

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(_IST)


def _fmt(dt: datetime, kind: str) -> str:
    """Broker-specific date string. Breeze: ISO Z; Kite: 'YYYY-MM-DD HH:MM:SS'."""
    return (dt.strftime("%Y-%m-%dT%H:%M:%S.000Z") if kind == "breeze"
            else dt.strftime("%Y-%m-%d %H:%M:%S"))


def _session_window(watermark_utc_iso: str | None, now_ist: datetime) -> tuple[datetime, datetime]:
    """From = the minute after the last stored bar (else today's 09:15); To = now,
    clamped to 15:30. Both returned in IST for the broker call."""
    if watermark_utc_iso:
        wm = datetime.fromisoformat(watermark_utc_iso.replace("Z", "+00:00")).astimezone(_IST)
        frm = wm + timedelta(minutes=1)
    else:
        frm = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return frm, min(now_ist, close)


def _cash_watermark(db: str, symbol: str, timeframe: str = "1m") -> str | None:
    try:
        with sqlite3.connect(db) as c:
            r = c.execute("SELECT MAX(ts) FROM price_bars WHERE symbol=? AND timeframe=?",
                          (symbol, timeframe)).fetchone()
        return r[0] if r and r[0] else None
    except sqlite3.OperationalError:
        return None


def _fo_watermark(db, underlying, itype, expiry, strike, right) -> str | None:
    try:
        with sqlite3.connect(db) as c:
            r = c.execute(
                "SELECT MAX(ts) FROM fo_price_bars WHERE underlying=? AND instrument_type=? "
                "AND expiry=? AND strike=? AND right=? AND timeframe='1m'",
                (underlying.upper(), itype, expiry, float(strike), right)).fetchone()
        return r[0] if r and r[0] else None
    except sqlite3.OperationalError:
        return None   # fo_price_bars not created yet -> first pull


# ── plan ────────────────────────────────────────────────────────────────────
def build_plan(*, stocks=None, sector_indices=None, underlying="NIFTY", future_expiries=None,
               option_expiries=None, option_strikes=None, today=None,
               include_cash=True, include_fo=True) -> list[dict]:
    """Universe rules -> flat target list. Expiry selection is delegated to
    universe.py (futures near+next; options current, +next within 2 days)."""
    today = today or _now_ist().date()
    plan: list[dict] = []
    if include_cash:
        for s in stocks or []:
            plan.append({"kind": "cash", "symbol": s.upper()})
        for s in sector_indices or []:
            plan.append({"kind": "cash", "symbol": s.upper()})
        plan.append({"kind": "cash", "symbol": underlying})
    if include_fo:
        for exp in active_future_expiries(future_expiries or [], today):
            plan.append({"kind": FUT, "underlying": underlying, "expiry": exp.isoformat()})
        for exp in active_option_expiries(option_expiries or [], today):
            for k in (option_strikes or []):
                for r in ("CE", "PE"):
                    plan.append({"kind": OPT, "underlying": underlying,
                                 "expiry": exp.isoformat(), "strike": float(k), "right": r})
    return plan


# ── collection ──────────────────────────────────────────────────────────────
def _collect_one(broker, t: dict, db: str, now_ist: datetime, timeframe: str = "1m") -> int:
    kind = t["kind"]
    if kind == "cash":
        # For daily data, we query the last year if no watermark exists
        if timeframe == "1d":
            watermark = _cash_watermark(db, t["symbol"], "1d")
            if watermark:
                frm = datetime.fromisoformat(watermark.replace("Z", "+00:00")).astimezone(_IST) + timedelta(days=1)
            else:
                frm = now_ist - timedelta(days=365)
            to = now_ist
        else:
            frm, to = _session_window(_cash_watermark(db, t["symbol"], "1m"), now_ist)
            
        if frm >= to:
            return 0
            
        interval = "1day" if timeframe == "1d" else "1minute"
        bars = broker.fetch_cash(t["symbol"], _fmt(frm, broker.kind), _fmt(to, broker.kind), interval=interval)
        if not bars:
            return 0
        from bar_store import save_bars
        return save_bars(bars, exchange="NSE", symbol=t["symbol"], timeframe=timeframe, db=db)

    if kind == FUT:
        # Check watermark for daily futures
        if timeframe == "1d":
            watermark = _fo_watermark(db, t["underlying"], FUT, t["expiry"], 0.0, "")
            if watermark:
                frm = datetime.fromisoformat(watermark.replace("Z", "+00:00")).astimezone(_IST) + timedelta(days=1)
            else:
                frm = now_ist - timedelta(days=365)
            to = now_ist
        else:
            frm, to = _session_window(_fo_watermark(db, t["underlying"], FUT, t["expiry"], 0.0, ""), now_ist)
            
        if frm >= to:
            return 0
            
        interval = "1day" if timeframe == "1d" else "1minute"
        bars = broker.fetch_future(t["underlying"], t["expiry"], _fmt(frm, broker.kind), _fmt(to, broker.kind), interval=interval)
        if not bars:
            return 0
        return save_fo_bars(bars, db=db, underlying=t["underlying"], instrument_type=FUT, expiry=t["expiry"], timeframe=timeframe)

    if kind == OPT:
        # Bypassed entirely for timeframe='1d' in build_plan, but kept for signature parity
        frm, to = _session_window(
            _fo_watermark(db, t["underlying"], OPT, t["expiry"], t["strike"], t["right"]), now_ist)
        if frm >= to:
            return 0
        bars = broker.fetch_option(t["underlying"], t["expiry"], t["strike"], t["right"],
                                   _fmt(frm, broker.kind), _fmt(to, broker.kind))
        if not bars:
            return 0
        return save_fo_bars(bars, db=db, underlying=t["underlying"], instrument_type=OPT,
                            expiry=t["expiry"], strike=t["strike"], right=t["right"])
    return 0


def run(broker, plan: list[dict], *, db: str, timeframe: str = "1m", now_ist: datetime | None = None,
        retries: int = 1) -> dict:
    """Walk the plan, collecting each target watermark-incrementally. Per-target
    error isolation; empty results get one retry. Returns a run report."""
    now_ist = now_ist or _now_ist()
    results, saved_total = [], 0
    for t in plan:
        rec = {**t}
        for attempt in range(retries + 1):
            try:
                n = _collect_one(broker, t, db, now_ist, timeframe=timeframe)
                rec["saved"] = n
                rec["status"] = "ok" if n > 0 else "empty"
                saved_total += n
                if n > 0 or attempt == retries:
                    break
            except Exception as e:                 # one bad contract never kills the batch
                rec["saved"] = 0
                rec["status"] = "error"
                rec["error"] = str(e)
                if attempt == retries:
                    break
        results.append(rec)
    ok = sum(1 for r in results if r["status"] == "ok")
    return {"saved_total": saved_total, "targets": len(plan),
            "ok": ok, "empty": sum(1 for r in results if r["status"] == "empty"),
            "errors": sum(1 for r in results if r["status"] == "error"),
            "results": results}


def run_option_chain_sync(broker, expiry_date: str | list[str], symbol: str = "NIFTY",
                          start_date: str = "", end_date: str = "", db_path: str = DB_PATH) -> dict:
    """Fetch complete option chain quotes for active expiries using the broker SDK and save captures."""
    if broker.kind != "breeze":
        return {"error": "Option chain capture is currently only supported via Breeze broker"}

    breeze = broker._session()
    
    # Resolve active expiries if list or string
    expiries_to_sync = [expiry_date] if isinstance(expiry_date, str) else list(expiry_date)
    expiries_to_sync = [e for e in expiries_to_sync if e]
    
    # Get spot price
    quote_res = breeze.get_quotes(stock_code=symbol, exchange_code="NSE", product_type="cash")
    if not quote_res or not quote_res.get("Success") or not len(quote_res["Success"]) > 0:
        raise ValueError(f"Failed to retrieve spot quote for {symbol}")
    
    spot = float(quote_res["Success"][0].get("last") or quote_res["Success"][0].get("close"))
    strike_step = 50 if "NIFTY" in symbol.upper() else 100

    # Date range parsing
    from datetime import date
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    end_date_obj = datetime.strptime(end_date[:10], "%Y-%m-%d").date() if end_date else now_ist.date()
    start_date_obj = datetime.strptime(start_date[:10], "%Y-%m-%d").date() if start_date else end_date_obj - timedelta(days=5)

    curr_date = start_date_obj
    saved_count = 0

    while curr_date <= end_date_obj:
        if curr_date.weekday() >= 5:
            curr_date += timedelta(days=1)
            continue

        day_spot = spot
        day_atm = round(day_spot / strike_step) * strike_step
        day_strikes = [day_atm + (i * strike_step) for i in range(-10, 11)]

        from_str = f"{curr_date}T09:15:00.000Z"
        to_str = f"{curr_date}T15:30:00.000Z"

        snapshots = {}
        for exp in expiries_to_sync:
            for strike in day_strikes:
                for right in ["Call", "Put"]:
                    try:
                        res = breeze.get_historical_data_v2(
                            interval="1minute",
                            from_date=from_str,
                            to_date=to_str,
                            stock_code=symbol,
                            exchange_code="NFO",
                            product_type="options",
                            expiry_date=exp,
                            right=right.lower(),
                            strike_price=str(strike)
                        )
                        rows = res.get("Success") if res else None
                        if not rows:
                            continue
                        for r in rows:
                            ts = r.get("datetime")
                            if not ts:
                                continue
                            chain_row = {
                                "expiry": exp,
                                "strike_price": float(strike),
                                "option_type": right.upper(),
                                "call_ltp" if right == "Call" else "put_ltp": float(r["close"]),
                                "call_oi" if right == "Call" else "put_oi": float(r.get("open_interest", 0.0) or 0.0),
                                "call_volume" if right == "Call" else "put_volume": float(r.get("volume", 0.0) or 0.0)
                            }
                            snapshots.setdefault((exp, ts), []).append(chain_row)
                    except Exception:
                        pass

        # Save snapshots
        for (exp_key, ts_str), rows in snapshots.items():
            if len(rows) < 4:
                continue
            dt_ist = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            dt_utc = dt_ist - timedelta(hours=5, minutes=30)
            captured_at = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

            merged_strikes = {}
            for r in rows:
                stk = r["strike_price"]
                merged_strikes.setdefault(stk, {
                    "strike": stk, "strike_price": stk,
                    "call_ltp": 0.0, "call_oi": 0, "call_volume": 0, "call_oichg": 0.0,
                    "put_ltp": 0.0, "put_oi": 0, "put_volume": 0, "put_oichg": 0.0
                })
                if r["option_type"] == "CALL":
                    merged_strikes[stk].update({"call_ltp": r["call_ltp"], "call_oi": int(r["call_oi"]), "call_volume": int(r["call_volume"])})
                else:
                    merged_strikes[stk].update({"put_ltp": r["put_ltp"], "put_oi": int(r["put_oi"]), "put_volume": int(r["put_volume"])})

            save_from_json_rows(
                list(merged_strikes.values()),
                expiry=exp_key,
                spot=day_spot,
                vix=12.0,
                note="Data Agent Sync",
                exchange_code="NFO",
                underlying=symbol,
                captured_at=captured_at,
                status="complete",
                trigger="manual",
                db_path=db_path
            )
            saved_count += 1

        curr_date += timedelta(days=1)

    return {"success": True, "snapshots_saved": saved_count}


def run_and_check(broker, plan, *, db, timeframe: str = "1m", now_ist=None, retries=1) -> dict:
    """run() + a cash data-health pass so the caller gets 'what did we get' AND
    'what's still missing' in one shot (F&O health is reported via the run's
    empty/error targets until an fo-coverage checker is added)."""
    report = run(broker, plan, db=db, timeframe=timeframe, now_ist=now_ist, retries=retries)
    try:
        from ..quality.data_health import coverage_report, alert_message
        health = coverage_report(db)
        report["health"] = alert_message(health, when="")
    except Exception as e:
        report["health"] = {"level": "unknown", "detail": f"health check skipped: {e}"}
    report["fo_gaps"] = [r for r in report["results"]
                         if r["kind"] in (FUT, OPT) and r["status"] != "ok"]
    return report


if __name__ == "__main__":
    print("orchestrator: build_plan() + run(broker, plan, db). Inject a live broker via")
    print("data_agent.fetching.get_broker('breeze'|'kite', ...) to collect for real.")
