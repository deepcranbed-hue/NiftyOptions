"""
upstox_auth.py - single source for the Upstox access token.

The token value lives ONLY in the repo-root .env (git-ignored) - never hardcoded.
Reads UPSTOX_ACCESS_TOKEN from the environment first; if unset, parses .env
directly so standalone scripts work without python-dotenv installed.
"""
import os


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_upstox_token():
    tok = os.getenv("UPSTOX_ACCESS_TOKEN")
    if tok:
        return tok.strip()
    try:
        with open(os.path.join(_repo_root(), ".env")) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.split("=", 1)[0].strip() == "UPSTOX_ACCESS_TOKEN":
                    return line.split("=", 1)[1].strip().strip("\"").strip("'")
    except FileNotFoundError:
        pass
    return None
