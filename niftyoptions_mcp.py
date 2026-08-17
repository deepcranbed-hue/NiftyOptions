#!/usr/bin/env python3
"""
niftyoptions_mcp.py — MCP server over the NiftyOptions desk.

ONE server, TWO clients: register it in Antigravity's mcp_config.json and in the
Claude desktop app. Same process, same code, no fork.

DESIGN RULES (why this file is thin)
------------------------------------
* It is an ADAPTER, not a second implementation. Route logic stays in the FastAPI
  backend and is reached over HTTP; maths stays in strategy_framework/bs.py and
  backend/quant/skew; the DB path comes from db_config.py. Same rule as
  DATA_SOURCES.md and CLAUDE.md's mandatory DB rule.
* READ-ONLY by default. /api/portfolio/*, /api/settle and /api/schedule/* are
  deliberately unreachable — an agent must not be able to close a position because
  it misread a prompt. Set NIFTY_MCP_ALLOW_WRITES=1 to override knowingly.
* Every number that could be absent says so. The digests carry provenance
  (em_source, vix_source, price_source, calibration provenance) because today's
  bugs were all "a fabricated value that looked like a measurement".

TOOL SURFACE (6 tools, not 58 — a big tool list wrecks selection and eats context)
  nifty_query   read-only data, `resource` discriminator over the GET routes
  nifty_sql     read-only SQL escape hatch (SELECT/WITH only, hard row cap)
  render        chart + digest: a picture for the human, reduced features for the model
  run_study     submit one of the 22 strategy_framework runners -> job_id
  job_status    poll
  job_result    fetch (this is what escapes the 45s tool timeout)

INSTALL
  pip install "mcp[cli]" httpx
RUN
  python niftyoptions_mcp.py          # stdio
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP          # noqa: E402
import httpx                                     # noqa: E402

from db_config import connect, describe as db_describe   # noqa: E402  single DB source (D-SC-06)

BACKEND = os.environ.get("NIFTY_BACKEND", "http://127.0.0.1:8000")
ALLOW_WRITES = os.environ.get("NIFTY_MCP_ALLOW_WRITES") == "1"
ARTIFACT_DIR = Path(os.environ.get("NIFTY_MCP_ARTIFACTS", ROOT / ".state" / "mcp_artifacts"))
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("niftyoptions")

# --------------------------------------------------------------------------------
# read-only route allowlist. Anything not here is unreachable by design.
# --------------------------------------------------------------------------------
RESOURCES: dict[str, str] = {
    "health":            "/api/health",
    "captures":          "/api/captures",
    "capture":           "/api/load-capture/{capture_id}",
    "chain":             "/api/fetch-chain",
    "bars":              "/api/bars",
    "bars_range":        "/api/bars/range",
    "bars_symbols":      "/api/bars/symbols",
    "realized_vol":      "/api/bars/realized-vol",
    "skew":              "/api/skew",
    "skew_vocabulary":   "/api/skew/vocabulary",
    "flows":             "/api/flows",
    "flows_history":     "/api/flows-history",
    "nifty_today":       "/api/nifty-today",
    "nifty_factors":     "/api/nifty-factors",
    "nifty_history":     "/api/nifty-history-db",
    "nifty50_view":      "/api/nifty50-view",
    "sector_view":       "/api/sector-view/{sector}",
    "event_calendar":    "/api/event-calendar",
    "fundamentals":      "/api/fundamentals",
    "money_sentiment":   "/api/money-sentiment",
    "participant_history": "/api/participant-history",
    "realized_metrics":  "/api/realized-metrics",
    "macro_shock":       "/api/macro-shock",
    "shock_recovery":    "/api/shock-recovery",
    "impact_monitor":    "/api/impact-monitor",
    "recommend_strikes": "/api/recommend-strikes",
    "analyze_desk":      "/api/analyze-desk",
    "replay_context":    "/api/replay-context",
    "intraday_dates":    "/api/intraday-dates",
    "exchange_expiries": "/api/exchange-expiries",
}

# never reachable, even with ALLOW_WRITES — these move money or state
FORBIDDEN = ("/api/portfolio/", "/api/settle", "/api/schedule/", "/api/admin/")


@mcp.tool()
async def nifty_query(resource: str, params: Optional[dict] = None) -> str:
    """Read one resource from the desk backend.

    `resource` is a key from the allowlist — call with resource="__list__" to see
    them all. `params` are query-string args (expiry, date, symbol, capture_id …).

    Reads the RUNNING backend over HTTP so route logic is never duplicated here.
    Returns the route's JSON verbatim; on a connection error it says the backend is
    down rather than inventing a shape.
    """
    if resource == "__list__":
        return json.dumps({"resources": sorted(RESOURCES), "backend": BACKEND}, indent=2)
    path = RESOURCES.get(resource)
    if path is None:
        return json.dumps({"error": "unknown resource", "known": sorted(RESOURCES)})
    p = dict(params or {})
    for token in ("capture_id", "sector"):
        if "{" + token + "}" in path:
            if token not in p:
                return json.dumps({"error": f"resource '{resource}' requires params.{token}"})
            path = path.replace("{" + token + "}", str(p.pop(token)))
    if any(path.startswith(f) for f in FORBIDDEN):
        return json.dumps({"error": "route is write/state-changing and not exposed"})
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(BACKEND + path, params=p)
        return r.text
    except httpx.ConnectError:
        return json.dumps({
            "error": "backend_unreachable",
            "detail": f"no server at {BACKEND} — start uvicorn, or set NIFTY_BACKEND",
        })


@mcp.tool()
def nifty_sql(sql: str, limit: int = 500) -> str:
    """Read-only SQL against the market store (SQLite, chains/bars/captures).

    The escape hatch for anything the routes don't cover — 448k chain rows have more
    in them than 30 endpoints expose. SELECT / WITH only; opened read-only through
    db_config so the Drive-safe busy timeout applies (D-SC-06).

    NOT the macro/fundamentals store — that lives in Postgres (see CLAUDE.md).
    """
    s = sql.strip().rstrip(";")
    if not s.lower().startswith(("select", "with")):
        return json.dumps({"error": "read-only: SELECT or WITH statements only"})
    if any(w in s.lower() for w in (" attach", " pragma ", "insert ", "update ", "delete ", "drop ")):
        return json.dumps({"error": "read-only: statement rejected"})
    try:
        con = connect(readonly=True)
        cur = con.execute(s)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchmany(limit)]
        truncated = len(rows) == limit and cur.fetchone() is not None
        con.close()
        return json.dumps({"columns": cols, "rows": rows, "n": len(rows),
                           "truncated": truncated}, default=str, indent=2)
    except Exception as e:
        return json.dumps({"error": type(e).__name__, "detail": str(e)})


@mcp.tool()
def render(view: str, capture_id: Optional[int] = None, expiry: Optional[str] = None,
           ts: Optional[str] = None) -> str:
    """Draw a market view and return BOTH a chart and a compact digest.

    Two channels, deliberately:
      * `chart` — an HTML/SVG file path. For the human. Shows shape: a kink in the
        skew, an OI wall, where a regime breaks.
      * `digest` — ~15 reduced features. For the model. Cheaper than raw rows AND
        more useful: max-pain, PCR and centre-of-gravity aren't in the rows at all.

    The chart is drawn by Python from the store — nothing in it is model-generated,
    so there are no invented data points.

    Every digest carries PROVENANCE, because today's bugs were all fabricated values
    posing as measurements: em_source, vix_source, price_source, calibration
    provenance. A field that could not be computed says so instead of returning 0.

    views: oi_profile | skew | vrp | term_structure
    """
    from renders import build          # local module, kept beside this file
    try:
        out = build(view, capture_id=capture_id, expiry=expiry, ts=ts,
                    outdir=ARTIFACT_DIR)
        return json.dumps(out, default=str, indent=2)
    except NotImplementedError:
        return json.dumps({"error": "unknown view",
                           "views": ["oi_profile", "skew", "vrp", "term_structure"]})
    except Exception as e:
        return json.dumps({"error": type(e).__name__, "detail": str(e)})


# --------------------------------------------------------------------------------
# async studies — the point of this is escaping the tool-call timeout
# --------------------------------------------------------------------------------
_JOBS: dict[str, dict] = {}

STUDIES = sorted(p.stem.replace("run_", "")
                 for p in (ROOT / "strategy_framework").glob("run_*.py"))


@mcp.tool()
def run_study(name: str, args: Optional[list[str]] = None) -> str:
    """Start one of the strategy_framework runners in the background; returns a job_id.

    This exists because a tool call times out long before a walk-forward backtest or
    a rotation sweep finishes. Submit here, poll job_status, collect job_result.

    Call with name="__list__" for the available studies.
    """
    if name == "__list__":
        return json.dumps({"studies": STUDIES}, indent=2)
    if name not in STUDIES:
        return json.dumps({"error": "unknown study", "studies": STUDIES})
    script = ROOT / "strategy_framework" / f"run_{name}.py"
    job_id = f"{name}-{uuid.uuid4().hex[:8]}"
    log = ARTIFACT_DIR / f"{job_id}.log"
    fh = open(log, "w")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen([sys.executable, str(script), *(args or [])],
                            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, env=env)
    _JOBS[job_id] = {"proc": proc, "log": log, "started": time.time(),
                     "name": name, "args": args or [], "fh": fh}
    return json.dumps({"job_id": job_id, "study": name, "status": "running",
                       "log": str(log)})


@mcp.tool()
def job_status(job_id: str) -> str:
    """Is it done? Returns running|ok|failed, elapsed seconds, and a log tail."""
    j = _JOBS.get(job_id)
    if not j:
        return json.dumps({"error": "unknown job_id", "known": sorted(_JOBS)})
    rc = j["proc"].poll()
    tail = ""
    try:
        tail = j["log"].read_text(errors="ignore")[-800:]
    except Exception:
        pass
    return json.dumps({
        "job_id": job_id, "study": j["name"],
        "status": "running" if rc is None else ("ok" if rc == 0 else "failed"),
        "returncode": rc, "elapsed_s": round(time.time() - j["started"], 1),
        "log_tail": tail,
    }, indent=2)


@mcp.tool()
def job_result(job_id: str, max_chars: int = 20000) -> str:
    """Full output of a finished job. Says so plainly if it is still running."""
    j = _JOBS.get(job_id)
    if not j:
        return json.dumps({"error": "unknown job_id", "known": sorted(_JOBS)})
    rc = j["proc"].poll()
    if rc is None:
        return json.dumps({"status": "running", "hint": "poll job_status",
                           "elapsed_s": round(time.time() - j["started"], 1)})
    try:
        j["fh"].close()
    except Exception:
        pass
    text = j["log"].read_text(errors="ignore")
    return json.dumps({
        "job_id": job_id, "study": j["name"],
        "status": "ok" if rc == 0 else "failed", "returncode": rc,
        "elapsed_s": round(time.time() - j["started"], 1),
        "truncated": len(text) > max_chars,
        "output": text[-max_chars:],
    }, indent=2)


@mcp.tool()
def desk_health() -> str:
    """Which stores resolved, is the backend up, what is actually usable right now.

    Deliberately explicit about absence — 'checked-and-absent != silently zero'
    applies to infrastructure too.
    """
    info: dict[str, Any] = {"db": db_describe(), "backend": BACKEND,
                            "writes_allowed": ALLOW_WRITES,
                            "artifacts": str(ARTIFACT_DIR),
                            "studies": len(STUDIES),
                            "resources": len(RESOURCES)}
    try:
        with httpx.Client(timeout=5) as c:
            info["backend_status"] = c.get(BACKEND + "/api/health").status_code
    except Exception as e:
        info["backend_status"] = f"unreachable ({type(e).__name__})"
    try:
        con = connect(readonly=True)
        info["captures"] = con.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
        info["latest_capture"] = con.execute(
            "SELECT MAX(captured_at) FROM captures").fetchone()[0]
        con.close()
    except Exception as e:
        info["store"] = f"unreadable ({type(e).__name__})"
    return json.dumps(info, default=str, indent=2)


if __name__ == "__main__":
    mcp.run()
