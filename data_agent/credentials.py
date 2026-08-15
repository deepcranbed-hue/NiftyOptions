"""credentials.py — the one place that knows where secrets live.

Before this, the Breeze api_key and api_secret were literals in 39 files. Two of
them are live data scripts (`fetching/sync_nifty50_to_now.py`,
`fetching/download_nifty_futures.py`), one is the live option-chain capture
(`scratch_scripts/fetch_historical_option_chain.py`), and the rest are scratch. A
committed secret has to be rotated out of every one of those at once or not at all,
which in practice means not at all.

So: `.env` is the source, and this module is the only reader. `.gitignore` already
covers `.env*`, so the value stops travelling with the repo.

    from credentials import breeze_creds
    api_key, api_secret = breeze_creds()

WHY A HAND-ROLLED .env PARSER
-----------------------------
python-dotenv is installed in breeze_env but NOT in
data_agent/breeze_env, and the daily fetching scripts run under the second one.
Importing it would make this module work in half the environments that need it —
the same class of split that broke the macro steps. Ten lines of parsing has no
such problem.

DUPLICATE KEYS: first occurrence wins, and a value already in the real environment
is never overwritten. That makes `BREEZE_API_KEY=... python foo.py` a working
override for a one-off, and it means an accidental duplicate line in .env behaves
predictably rather than depending on file order.
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
ENV_PATH = os.path.join(REPO_ROOT, ".env")

_loaded = False


def load_dotenv(path: str | None = None, force: bool = False) -> list[str]:
    """Read .env into os.environ without clobbering what is already set."""
    global _loaded
    if _loaded and not force:
        return []
    _loaded = True
    keys = []
    try:
        with open(path or ENV_PATH) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
                    keys.append(k)
    except OSError:
        return []
    return keys


def breeze_creds(required: bool = True):
    """(api_key, api_secret) for Breeze, from the environment or .env.

    Raises rather than returning empty strings when `required`: a BreezeConnect
    session built from a blank key fails later, deeper, and with a worse message
    than a missing-credential error raised here.
    """
    load_dotenv()
    key = os.environ.get("BREEZE_API_KEY")
    secret = os.environ.get("BREEZE_API_SECRET")
    if required and not (key and secret):
        raise RuntimeError(
            "BREEZE_API_KEY / BREEZE_API_SECRET are not set.\n"
            f"Add them to {ENV_PATH} (it is gitignored), or export them:\n"
            "    BREEZE_API_KEY=... BREEZE_API_SECRET=... python <script>")
    return key, secret


def breeze_session_token():
    """Today's Breeze session token, if one has been cached by the UI."""
    from datetime import datetime
    load_dotenv()
    tok = os.environ.get("BREEZE_SESSION_TOKEN")
    if tok:
        return tok
    path = os.path.join(REPO_ROOT, "breezesession",
                        f"session_{datetime.now():%Y-%m-%d}.json")
    try:
        import json
        with open(path) as f:
            tok = json.load(f).get("session_token")
        return tok if tok and len(tok) > 5 and tok != "undefined" else None
    except (OSError, ValueError):
        return None
