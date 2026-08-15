"""
agent_settings.py  (repo-root, single source of truth for runtime toggles)
==========================================================================
Small persisted switchboard both the backend and the data-agent read. Two switches:

  * local_llm_enabled  — run the LOCAL Qwen (Ollama) on this Mac. Turn OFF to stop
                         local inference heating the processor. When off: the data-agent
                         command box falls back to keyword parsing, and the news/earnings
                         tagger skips the on-device model (uses a cloud provider if a key
                         is set, else its keyword heuristic). No Ollama call is made.
  * agent_enabled      — the data agent itself (the 5 PM EOD audit loop + collection).
                         Turn OFF to fully idle it.

Persisted to .state/agent_settings.json so a restart keeps your choice. Reads are
cheap and defensive (missing/broken file -> defaults, both ON).
"""
from __future__ import annotations
import json
import os
import threading

_LOCK = threading.Lock()
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".state", "agent_settings.json")
_DEFAULTS = {"local_llm_enabled": False, "agent_enabled": False}   # OFF by default — opt in explicitly


def _read() -> dict:
    try:
        with open(_PATH) as f:
            data = json.load(f)
        return {**_DEFAULTS, **{k: bool(data[k]) for k in _DEFAULTS if k in data}}
    except Exception:
        return dict(_DEFAULTS)


def get_settings() -> dict:
    return _read()


def set_settings(**kw) -> dict:
    """Update one or both switches; unknown keys ignored. Returns the new settings."""
    with _LOCK:
        cur = _read()
        for k in _DEFAULTS:
            if k in kw and kw[k] is not None:
                cur[k] = bool(kw[k])
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cur, f)
        os.replace(tmp, _PATH)
        return cur


def local_llm_enabled() -> bool:
    return _read()["local_llm_enabled"]


def agent_enabled() -> bool:
    return _read()["agent_enabled"]


if __name__ == "__main__":
    print("settings:", get_settings(), "@", _PATH)
