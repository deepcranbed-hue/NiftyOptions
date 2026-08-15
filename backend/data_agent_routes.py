"""
backend/data_agent_routes.py
============================
HTTP surface for the data agent — the thing the UI button and the natural-language
command box actually call. Mounted in main.py via `app.include_router(...)`.

Endpoints (all under /api/data-agent):
  GET  /health    -> data-health coverage/alert payload (drives the sidebar badge)
  GET  /validate  -> constituent-alignment check (registry.validate)
  POST /run       -> collect data with a chosen broker + token (the "Start" button)
  POST /command   -> natural language -> parse_intent (local Qwen) -> dispatch

Tokens are taken from the request per-call (session-only; not persisted here).
All data-agent imports are lazy so importing this module is cheap and can't break
app startup if the package is mid-build.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/data-agent", tags=["data-agent"])


def _db() -> str:
    import bar_store
    return bar_store.DB_PATH


@router.get("/health")
def data_agent_health():
    """Unified 'what's missing' over the EXISTING tables — cash (price_bars) AND
    option chain (captures/chain_rows). Shaped for the sidebar badge + panel list.

    Prefers the cached 5 PM EOD audit (so the badge reflects the last end-of-day scan);
    falls back to a live scan if no EOD run has happened yet today."""
    import json, os
    rep = None
    p = _last_audit_path()
    if os.path.exists(p):
        try:
            with open(p) as f:
                rep = json.load(f)
        except Exception:
            rep = None
    source = "eod"
    if rep is None:
        # No heavy live scan on this interactive path — the audit runs at 17:00 IST (or via
        # POST /run-audit on demand). Return a light 'not audited yet' status instead.
        return {"level": "unknown", "headline": "No EOD audit yet — runs at 17:00 IST.",
                "detail": "Click Run Audit or wait for the 5 PM scan.", "flagged": [],
                "source": "none", "audit_date": None, "ran_at": None}
    # flatten into one list the panel renders. Options & futures = last-timestamp (STALE)
    # only; stocks add a per-day THIN coverage flag.
    flagged = []
    fresh = rep.get("freshness", {})
    for c in fresh.get("stale_stocks", []):
        flagged.append({"symbol": c["symbol"], "date": c["last_day"], "status": "STALE",
                        "reason": f"{c['days_behind']}d behind (last {c['last_ts']}, {c['sample_size']} bars)"})
    for c in fresh.get("stale_futures", []):
        flagged.append({"symbol": f"FUT {c['symbol']}", "date": c["last_day"], "status": "STALE",
                        "reason": f"{c['days_behind']}d behind (last {c['last_ts']}, {c['sample_size']} bars shown)"})
    for c in fresh.get("stale_chain", []):
        flagged.append({"symbol": f"OPT {c['expiry'][:10]}", "date": c["last_day"], "status": "STALE",
                        "reason": f"{c['days_behind']}d behind (last {c['last_ts']}, {c['n_snapshots']} snaps shown)"})
    for f in rep.get("coverage", {}).get("stock_thin", []):
        flagged.append({"symbol": f["symbol"], "date": f["date"],
                        "status": f["status"], "reason": f["reason"]})
    detail = " · ".join(x for x in (rep.get("inventory", {}).get("summary"),
                                    rep.get("coverage", {}).get("summary")) if x)
    return {"level": rep["level"], "headline": rep["headline"], "detail": detail,
            "flagged": flagged, "checked_at": rep.get("checked_at"),
            "source": source, "audit_date": rep.get("audit_date"), "ran_at": rep.get("ran_at")}


@router.get("/missing")
def data_agent_missing():
    """Full structured gap report (per-symbol cash days + per-expiry option days)."""
    from data_agent.quality.data_health import missing_report
    return missing_report(_db())


def _last_audit_path() -> str:
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".state", "last_audit.json")


@router.get("/inventory")
def data_agent_inventory():
    """Per-symbol + per-expiry freshness table: first_day, last_day, last_ts, sample_size,
    n_days, days_behind, status (CURRENT | STALE | EXPIRED)."""
    from data_agent.quality.data_health import inventory_report
    return inventory_report(_db())


@router.get("/last-audit")
def data_agent_last_audit():
    """The most recent 5 PM EOD audit result (cached by the backend loop). Returns
    {available: False} until the first EOD run has happened."""
    import json, os
    p = _last_audit_path()
    if not os.path.exists(p):
        return {"available": False, "detail": "No EOD audit has run yet (fires at 17:00 IST)."}
    try:
        with open(p) as f:
            rep = json.load(f)
        rep["available"] = True
        return rep
    except Exception as e:
        return {"available": False, "detail": f"could not read cached audit: {e}"}


@router.post("/run-audit")
def data_agent_run_audit():
    """Run the EOD audit on demand (same scan the 5 PM loop performs) and cache it."""
    import json, os
    from datetime import datetime, timezone
    from data_agent.quality.data_health import missing_report
    rep = missing_report(_db())
    rep["audit_date"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    rep["ran_at"] = datetime.now(timezone.utc).isoformat()
    try:
        p = _last_audit_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(rep, f)
    except Exception:
        pass
    return rep


@router.get("/settings")
def data_agent_get_settings():
    """Current runtime toggles: {local_llm_enabled, agent_enabled}."""
    import agent_settings
    return agent_settings.get_settings()


class SettingsReq(BaseModel):
    local_llm_enabled: Optional[bool] = None
    agent_enabled: Optional[bool] = None


@router.post("/settings")
def data_agent_set_settings(req: SettingsReq):
    """Flip either switch. Turning local_llm_enabled off stops all on-device Qwen
    inference (no Mac heating); turning agent_enabled off idles the 5 PM loop + collection."""
    import agent_settings
    return agent_settings.set_settings(local_llm_enabled=req.local_llm_enabled,
                                       agent_enabled=req.agent_enabled)


@router.get("/validate")
def data_agent_validate():
    from data_agent.constituents import validate
    return validate()


@router.get("/status")
def data_agent_status():
    from data_agent.constituents import missing_files
    return {"running": False, "core_files_missing": missing_files(core_only=True), "db": _db()}


class RunReq(BaseModel):
    broker: str                       # "breeze" (Kite removed 2026-08-08)
    token: str
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    mode: str = "cash"                # cash | fo | all
    timeframe: str = "1m"             # "1m" | "1d"
    future_expiries: Optional[list[str]] = None
    option_expiries: Optional[list[str]] = None
    option_strikes: Optional[list[float]] = None


def _do_run(req: RunReq) -> dict:
    import agent_settings
    if not agent_settings.agent_enabled():
        raise HTTPException(409, "Data agent is switched off. Turn it on in the Data Agent panel to collect.")
    from data_agent.constituents import symbols, require_files
    from data_agent.fetching import get_broker, build_plan, run_and_check
    try:
        require_files()
    except Exception as e:
        raise HTTPException(400, f"config guard: {e}")

    if req.broker == "breeze":
        creds = {"session_token": req.token}
        if req.api_key:
            creds["api_key"] = req.api_key
        if req.api_secret:
            creds["api_secret"] = req.api_secret
    else:
        creds = {"access_token": req.token}
        if req.api_key:
            creds["api_key"] = req.api_key
    try:
        broker = get_broker(req.broker, **creds)
    except Exception as e:
        raise HTTPException(400, f"broker init failed: {e}")

    include_cash = req.mode in ("cash", "all")
    # For daily runs, we do NOT download option chains. Only cash and futures.
    include_fo = req.mode in ("fo", "all") and req.timeframe != "1d"

    # Auto-resolve expiries if not provided
    fut_exps = req.future_expiries
    opt_exps = req.option_expiries
    opt_strikes = req.option_strikes
    
    if include_fo:
        # Load from SecurityMaster if missing
        if not fut_exps or not opt_exps or not opt_strikes:
            try:
                import urllib.request, zipfile, io, os
                from datetime import datetime, date
                local_zip = "/Users/deepak/antigravity/NiftyOptions/SecurityMaster.zip"
                if os.path.exists(local_zip):
                    z = zipfile.ZipFile(local_zip)
                    with z.open('FONSEScripMaster.txt') as fz:
                        content = fz.read().decode('utf-8', errors='ignore')
                        
                        # Find option expiries
                        opt_dates = set()
                        for line in content.split('\n'):
                            if '"NIFTY"' in line and '"OPTIDX"' in line:
                                parts = [p.strip('"') for p in line.split(',')]
                                if len(parts) >= 5:
                                    dt = datetime.strptime(parts[4], "%d-%b-%Y")
                                    opt_dates.add(dt.strftime("%Y-%m-%d"))
                        if opt_dates:
                            today_dt = date.today()
                            unexpired_opt = [d for d in sorted(list(opt_dates)) if datetime.strptime(d, "%Y-%m-%d").date() >= today_dt]
                            if unexpired_opt and not opt_exps:
                                opt_exps = [unexpired_opt[0]]
                                
                        # Find future expiries
                        fut_dates = set()
                        for line in content.split('\n'):
                            if '"NIFTY"' in line and '"FUTIDX"' in line:
                                parts = [p.strip('"') for p in line.split(',')]
                                if len(parts) >= 5:
                                    dt = datetime.strptime(parts[4], "%d-%b-%Y")
                                    fut_dates.add(dt.strftime("%Y-%m-%d"))
                        if fut_dates:
                            today_dt = date.today()
                            unexpired_fut = [d for d in sorted(list(fut_dates)) if datetime.strptime(d, "%Y-%m-%d").date() >= today_dt]
                            if unexpired_fut and not fut_exps:
                                fut_exps = unexpired_fut[:2]
                                
                if not opt_strikes:
                    # Default ATM +/- 500 strikes
                    opt_strikes = [float(k) for k in range(23650, 24651, 50)]
            except Exception as e:
                import traceback
                traceback.print_exc()

    # Canonical DB names. NSEBANK/CNXIT are Yahoo TICKERS, not symbols — writing
    # them created 257-bar shadow copies of BANKNIFTY/NIFTYIT, which already hold
    # the full 2018+ history. download_india_indices.py maps ^NSEBANK -> BANKNIFTY
    # and ^CNXIT -> NIFTYIT; this now matches.
    sector_indices = ["BANKNIFTY", "NIFTYIT", "INDIAVIX"] if include_cash else []

    plan = build_plan(
        stocks=symbols() if include_cash else [],
        sector_indices=sector_indices,
        future_expiries=fut_exps, option_expiries=opt_exps,
        option_strikes=opt_strikes,
        include_cash=include_cash, include_fo=include_fo)

    result = run_and_check(broker, plan, db=_db(), timeframe=req.timeframe)
    
    # Run the unified pipeline sequentially in the background to prevent SQLite DB locks
    import subprocess
    import os
    import sys
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wrapper_script = os.path.join(repo_root, "data_agent/fetching/sync_all_auxiliary.py")
    if os.path.exists(wrapper_script):
        subprocess.Popen([sys.executable, wrapper_script], cwd=repo_root,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
    return result


@router.post("/run")
def data_agent_run(req: RunReq):
    return _do_run(req)


class CmdReq(BaseModel):
    text: str
    breeze_token: Optional[str] = None
    api_key: Optional[str] = None


@router.post("/command")
def data_agent_command(req: CmdReq):
    """Natural language -> structured action (local Qwen, keyword fallback) -> dispatch."""
    from data_agent.agent import parse_intent
    intent = parse_intent(req.text)
    action = intent.get("action")
    out = {"intent": intent}

    if action == "health":
        out["health"] = data_agent_health()
    elif action in ("start", "sync", "backfill"):
        broker = intent.get("broker") or ("breeze" if req.breeze_token else None)
        token = intent.get("token") or req.breeze_token
        if not broker or not token:
            out["message"] = ("Tell me which broker and provide its token — e.g. "
                              "'start downloading with my breeze token <tok>'.")
        else:
            out["run"] = _do_run(RunReq(broker=broker, token=token, api_key=req.api_key, mode="cash"))
    elif action == "stop":
        out["message"] = "Collection runs synchronously per request; nothing long-running to stop yet."
    else:
        out["message"] = f"Understood action '{action}', but no handler is wired for it yet."
    return out
