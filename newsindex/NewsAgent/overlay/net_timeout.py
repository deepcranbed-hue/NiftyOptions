"""
net_timeout.py — hard timeouts so a hung download can never stall the whole run.

The engine sets socket.setdefaulttimeout() per symbol, but a pooled HTTP connection that stalls
mid-read (Yahoo throttling / dropped packets) can hang PAST that — the socket default doesn't
reliably apply to an already-established pooled connection, and newer yfinance uses curl_cffi, not
requests. So we add two layers, both additive (no engine edit):

  1) install_default_timeouts() — monkeypatch a default (connect, read) timeout into BOTH
     `requests` and `curl_cffi` sessions, so no underlying HTTP call can block forever.
  2) run_with_timeout()        — a WALL-CLOCK cap: run the fetch on a daemon thread and join for
     `budget` seconds. If it hasn't returned, we abandon it (the daemon thread dies with the
     process) and carry on with a safe default. This works no matter which HTTP library hangs.

Together they bound every fetch: worst case is `budget` seconds per group, not an open-ended hang.
"""
from __future__ import annotations

import threading


def install_default_timeouts(connect: float = 4.0, read: float = 8.0) -> None:
    """Inject a default timeout into requests AND curl_cffi so no HTTP call hangs forever."""
    try:
        import requests
        if not getattr(requests.Session, "_nt_patched", False):
            _orig = requests.Session.request

            def _req(self, method, url, **kw):
                kw.setdefault("timeout", (connect, read))
                return _orig(self, method, url, **kw)

            requests.Session.request = _req
            requests.Session._nt_patched = True
    except Exception:
        pass
    try:
        from curl_cffi import requests as _creq       # newer yfinance backend
        if not getattr(_creq.Session, "_nt_patched", False):
            _corig = _creq.Session.request

            def _creqf(self, method, url, **kw):
                kw.setdefault("timeout", read)
                return _corig(self, method, url, **kw)

            _creq.Session.request = _creqf
            _creq.Session._nt_patched = True
    except Exception:
        pass


def run_with_timeout(fn, budget: float, default=None):
    """Run fn() on a daemon thread; return its result, or `default` if it exceeds `budget` sec.

    A timed-out thread is ABANDONED (daemon → dies with the process), so a stuck socket read can
    never block the pipeline. Returns (value, timed_out_bool).
    """
    box = {"v": default, "done": False}

    def _worker():
        try:
            box["v"] = fn()
        except Exception:
            box["v"] = default
        finally:
            box["done"] = True

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(budget)
    return (box["v"], not box["done"])
