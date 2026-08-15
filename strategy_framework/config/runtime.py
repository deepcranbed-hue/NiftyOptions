"""
strategy_framework/config/runtime.py
====================================
Persisted RUNTIME settings for the strategy framework — the settings a user can
change from the UI and expect to stick, everywhere, until they change them again.

Mirrors the repo-root `agent_settings.py` pattern: a small JSON blackboard under
`.state/`, atomic writes (tmp + os.replace) under a lock, defensive reads (missing
or corrupt file -> defaults).

Currently ONE setting:

  * lookback_min — THE return window (in 1-minute bars) shared by every
    price-return signal. Set it once and rel_volume, futures_flow, vol_index and
    heavyweight_leadership all follow, live and after a restart.

Why this file exists rather than a field on FrameworkConfig
-----------------------------------------------------------
`FrameworkConfig()` is instantiated at MODULE level in several places
(`settings.DEFAULT`, `api._CFG`). If the window were captured at construction
time, changing it would not reach those long-lived objects until a process
restart — the classic "I changed the setting and nothing happened" bug. So
`MomentumWindow.bars()` reads THIS store at CALL time, which makes a change take
effect immediately in every consumer, including ones constructed long before.

Reads are cheap (a small JSON file, hit once per signal evaluation) and cached for
a moment to avoid hammering the filesystem inside tight backfill loops.
"""
from __future__ import annotations
import json
import os
import threading
import time

_LOCK = threading.Lock()
_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".state", "strategy_runtime.json")

# The shipped default window. Changing this literal changes the out-of-the-box
# behaviour; changing the SETTING (via set_lookback_min) changes the live desk.
DEFAULT_LOOKBACK_MIN = 15
ALLOWED_LOOKBACKS = (5, 15, 30, 60)

_DEFAULTS = {"lookback_min": DEFAULT_LOOKBACK_MIN}

_cache: dict | None = None
_cache_at: float = 0.0
_CACHE_TTL = 1.0          # seconds — long enough for a backfill loop, short enough to feel live


def _read(force: bool = False) -> dict:
    global _cache, _cache_at
    if not force and _cache is not None and (time.time() - _cache_at) < _CACHE_TTL:
        return _cache
    try:
        with open(_PATH) as f:
            data = json.load(f)
        out = dict(_DEFAULTS)
        n = data.get("lookback_min")
        if isinstance(n, (int, float)) and int(n) > 0:
            out["lookback_min"] = int(n)
    except Exception:
        out = dict(_DEFAULTS)
    _cache, _cache_at = out, time.time()
    return out


def get_settings() -> dict:
    return dict(_read())


def get_lookback_min() -> int:
    """THE shared return window, in minutes. Read at call time — see module docstring."""
    return int(_read()["lookback_min"])


def set_lookback_min(minutes: int) -> dict:
    """Persist the shared return window. Takes effect immediately in every consumer.

    NOTE: signal scores are a FUNCTION of this window, so any cached feature-store
    rows computed at the previous window are now stale — rebuild them
    (`features backfill --force`) before trusting IC / correlation output.
    `features.store.window_audit()` reports the mismatch."""
    n = int(minutes)
    if n <= 0:
        raise ValueError(f"lookback_min must be positive, got {minutes}")
    global _cache, _cache_at
    with _LOCK:
        cur = _read(force=True)
        cur["lookback_min"] = n
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cur, f)
        os.replace(tmp, _PATH)
        _cache, _cache_at = dict(cur), time.time()
        return dict(cur)


if __name__ == "__main__":
    print("strategy runtime settings:", get_settings(), "@", _PATH)
