"""
proc_fetch.py — run the yfinance quote fetch in a KILLABLE subprocess.

Why a process and not a thread: newer yfinance uses curl_cffi, whose synchronous perform() can
HOLD the GIL during a stalled network call. When that happens no other Python thread can run, so
thread-based timeouts (call_with_timeout, the wall-clock cap) can't fire and the whole process
freezes on a rate-limited/hung download. A thread can't be force-killed; a PROCESS can.

So we fetch every quote group in a child `python -c` process and read its JSON from stdout with a
hard `subprocess` timeout. If it overruns, subprocess kills the child (SIGKILL) and we return a
timeout marker — the caller then falls back to placeholder rows + the NSE/Stooq backfill. This is
GIL-independent: a stuck curl call dies with the child instead of hanging the run.

Disable with NEWSAGENT_PROC_ISOLATE=0 (falls back to the in-process threaded fetch).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

# child program: import the (vendored) engine, fetch each group, print JSON. Just before the
# parent's kill deadline it dumps ALL thread stacks to stderr (faulthandler) so a stall is
# diagnosable — you can SEE whether threads are in socket.recv (network), lock.acquire
# (deadlock), or Python bytecode (real GIL/CPU), instead of guessing.
_RUNNER = r"""
import sys, json, faulthandler
engine_dir, budget = sys.argv[1], float(sys.argv[2])
faulthandler.enable()
faulthandler.dump_traceback_later(max(1.0, budget * 0.8), repeat=False)   # ~before parent kills
if engine_dir not in sys.path:
    sys.path.insert(0, engine_dir)
try:
    import net_timeout as _nt          # inject requests/curl_cffi read timeouts in the child too
    _nt.install_default_timeouts()
except Exception:
    pass
import market_engine as ms
symmaps = json.loads(sys.stdin.read())
out = {}
for group, sm in symmaps.items():
    try:
        out[group] = ms.fetch_quotes(sm)
    except Exception as e:
        out[group] = {"__group_error__": str(e)[:120]}
faulthandler.cancel_dump_traceback_later()
sys.stdout.write(json.dumps(out))
"""


def fetch_quotes_isolated(engine_dir: str, symmaps: dict[str, dict], overlay_dir: str | None = None,
                          budget: float = 60.0) -> dict | None:
    """Fetch all quote groups in a killable child. Returns {group: rows}, or a marker dict:
       {"__timeout__": True} if the child was killed, {"__error__": ...} on failure, or
       None if isolation is disabled (caller should fetch in-process)."""
    if os.environ.get("NEWSAGENT_PROC_ISOLATE", "1") == "0":
        return None
    env = dict(os.environ)
    # let the child import net_timeout (in overlay) alongside the engine
    extra = os.pathsep.join(p for p in (overlay_dir, engine_dir) if p)
    env["PYTHONPATH"] = extra + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        p = subprocess.run([sys.executable, "-c", _RUNNER, engine_dir, str(budget)],
                           input=json.dumps(symmaps), capture_output=True, text=True,
                           timeout=budget, env=env)
        if p.stdout.strip():
            return json.loads(p.stdout)
        return {"__error__": (p.stderr or "empty output")[:200]}
    except subprocess.TimeoutExpired as e:
        # child was SIGKILLed; its faulthandler dump (thread stacks) is in the captured stderr
        trace = ""
        try:
            trace = (e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or ""))
        except Exception:
            pass
        return {"__timeout__": True, "stall_trace": trace[-2500:]}
    except Exception as e:
        return {"__error__": str(e)[:200]}
