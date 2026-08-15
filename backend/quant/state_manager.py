import json
import os
import tempfile
from datetime import datetime, timezone

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".state")

def _get_path(name: str) -> str:
    if not os.path.exists(STATE_DIR):
        os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, f"{name}.json")

def write_state(name: str, data: dict):
    """Atomically writes JSON state to disk."""
    data["as_of"] = datetime.now(timezone.utc).isoformat()
    if "stale" not in data:
        data["stale"] = False
        
    path = _get_path(name)
    fd, temp_path = tempfile.mkstemp(dir=STATE_DIR, prefix=f"{name}_", suffix=".tmp")
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
    
    # Atomic rename (POSIX guarantees this is atomic; Windows mostly does too in newer versions)
    os.replace(temp_path, path)

def read_state(name: str, fallback: dict = None) -> dict:
    """Reads JSON state. Returns fallback (with stale=True) if missing."""
    if fallback is None:
        fallback = {"as_of": None, "stale": True}
    else:
        fallback = fallback.copy()
        fallback["stale"] = True
        
    path = _get_path(name)
    if not os.path.exists(path):
        return fallback
        
    try:
        with open(path, 'r') as f:
            data = json.load(f)
            return data
    except (json.JSONDecodeError, OSError):
        return fallback
