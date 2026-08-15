"""expiries.py — ASK THE BROKER what is listed. The rules live in universe.py.

This module does exactly one thing: read the expiry list out of Breeze. Which of
those expiries we should be pulling right now is a different question, and
`data_agent/fetching/universe.py` already answers it:

    ROLL_AHEAD_DAYS = 2
    is_expired(expiry, today)
    active_future_expiries(expiries, today, n=2)   # near + next, always two
    active_option_expiries(expiries, today)        # current, + next inside 2 days

The first version of this file reimplemented three of those — an `>= today` filter,
`exps[0], exps[1]` for the futures pair, and `0 <= delta <= 2` for the option
rollover. Three copies of a rule is three places to disagree, and the copies were
already subtly weaker: universe's rollover triggers when the CURRENT expiry is
within two days, mine triggered on a signed delta that would have silently stopped
firing the moment an expiry date slipped past today.

So the split is:

    expiries.py    WHERE the list comes from   (Breeze, a subprocess, a session)
    universe.py    WHICH ones we want          (pure date logic, offline-testable)

Add a rule to universe.py. Add a source here. Never the other way round.

WHY A SUBPROCESS
----------------
`breeze_connect` lives in the venvs, not in whatever interpreter imported this — the
FastAPI process and the sync CLI both run outside them. The Breeze call is shelled
out to the venv python and the result comes back as one JSON line.

NO HARDCODED FALLBACK LIST. The endpoint this replaces returned a baked-in list of
July 2026 dates when the lookup failed, which in August is not a fallback but a
wrong answer that looks like a right one. A failure returns empty and says why.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

for _p in (_HERE, os.path.join(_HERE, "fetching")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from universe import (                                            # noqa: E402
    ROLL_AHEAD_DAYS, _as_date, active_future_expiries, active_option_expiries,
    is_expired,
)

VENV_CANDIDATES = [
    "data_agent/breeze_env/bin/python",
    "breeze_env/bin/python",
]

_CACHE: dict[tuple, list[str]] = {}

_SNIPPET = """
import json
from breeze_connect import BreezeConnect
b = BreezeConnect(api_key=%r)
b.generate_session(api_secret=%r, session_token=%r)
sc = %r
pt = %r
kwargs = {'stock_code': sc, 'exchange_code': 'NFO', 'product_type': pt}
if pt == 'options':
    # Breeze REQUIRES expiry_date or strike_price for an options chain and returns
    # HTTP 500 "Either Expiry-Date or Strike-Price cannot be empty" without one.
    # strike_price='0' matches nothing in particular, which is what we want: the
    # response still enumerates every listed contract, so the distinct expiry_date
    # values are the expiry list.
    kwargs['right'] = 'Call'
    kwargs['strike_price'] = '0'
# For FUTURES, `right` is omitted entirely. Futures have no call/put, and passing
# one does not error — Breeze matches nothing and returns an empty Success list,
# which reads as "this product has no expiries". That is what produced
#     Could not find 2 active NIFTY futures expiries. Found: []
r = b.get_option_chain_quotes(**kwargs)
rows = (r or {}).get('Success') or []
exps = sorted({x.get('expiry_date') for x in rows if x.get('expiry_date')})
if not exps:
    # Never fail silently again: show what Breeze actually said.
    print('__RAW__' + json.dumps(r)[:500])
print('__EXPIRIES__' + json.dumps(exps))
"""


# Breeze returns expiries as '25-Aug-2026'. Everything downstream — the chain
# capture script, the futures writer, the frontend's stored value — uses
# '2026-08-25T06:00:00.000Z'. Converting HERE, at the one place that talks to
# Breeze, keeps the vendor's format from leaking into universe.py (which is meant to
# be pure date logic) or into callers that would each need their own parser.
#
# The docstring above this module used to claim Breeze already returns the ISO form.
# It does not, and that claim is why the format mismatch was not caught in review.
_CANON = "%Y-%m-%dT06:00:00.000Z"


def _canonical(raw):
    """Any Breeze expiry spelling -> '2026-08-25T06:00:00.000Z'. None if unparseable."""
    s = str(raw).strip()
    for parse in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
                  lambda v: datetime.strptime(v[:10], "%Y-%m-%d"),
                  lambda v: datetime.strptime(v, "%d-%b-%Y"),
                  lambda v: datetime.strptime(v, "%d-%B-%Y")):
        try:
            return parse(s).strftime(_CANON)
        except (ValueError, TypeError):
            continue
    print(f"    (unparseable expiry from Breeze: {raw!r})")
    return None


def _venv_python():
    for rel in VENV_CANDIDATES:
        p = os.path.join(REPO_ROOT, rel)
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return sys.executable


def listed(symbol="NIFTY", product="options", session_token=None, python=None,
           api_key=None, api_secret=None, use_cache=True):
    """Every expiry Breeze lists for `symbol`, unfiltered. -> (expiries, error).

    Returned verbatim in Breeze's own form — 2026-08-27T06:00:00.000Z — which is
    what the rest of the repo passes around. Nothing is reformatted and nothing is
    dropped here; filtering is universe.py's job.
    """
    if product not in ("options", "futures"):
        return [], f"unknown product {product!r}"

    if not (api_key and api_secret):
        from credentials import breeze_creds
        try:
            api_key, api_secret = breeze_creds()
        except RuntimeError as e:
            return [], str(e)

    if not session_token:
        from credentials import breeze_session_token
        session_token = breeze_session_token()
    if not session_token:
        return [], "no Breeze session token"

    ck = (symbol.upper(), product, session_token[-6:])
    if use_cache and ck in _CACHE:
        return _CACHE[ck], None

    cmd = [python or _venv_python(), "-c",
           _SNIPPET % (api_key, api_secret, session_token, symbol.upper(),
                       product)]
    try:
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                           timeout=90)
    except subprocess.TimeoutExpired:
        return [], "Breeze expiry lookup timed out"
    line = next((l for l in r.stdout.splitlines()
                 if l.startswith("__EXPIRIES__")), None)
    if not line:
        return [], (r.stderr or r.stdout or "no output").strip()[:300]

    out = [_canonical(e) for e in json.loads(line[len("__EXPIRIES__"):]) if e]
    out = sorted(e for e in out if e)
    if not out:
        # Say WHY it was empty. A silent empty list here cost a pipeline run.
        raw = next((l for l in r.stdout.splitlines()
                    if l.startswith("__RAW__")), "")
        return [], (f"Breeze returned no {product} expiries for {symbol}. "
                    f"Raw: {raw[len('__RAW__'):][:300] or '(no payload)'}")
    if use_cache:
        _CACHE[ck] = out
    return out, None


def _pick(all_expiries, wanted_dates):
    """Map universe's chosen dates back to the broker's original strings."""
    by_date = {}
    for e in all_expiries:
        by_date.setdefault(_as_date(e), e)
    return [by_date[d] for d in wanted_dates if d in by_date]


def unexpired(symbol="NIFTY", product="options", **kw):
    """Everything still tradeable — what an expiry dropdown should offer."""
    all_e, err = listed(symbol, product, **kw)
    if err:
        return [], err
    return [e for e in sorted(all_e) if not is_expired(e)], None


def active(symbol="NIFTY", product="options", today=None, **kw):
    """The expiries we should be PULLING right now, per universe.py's rules.

    futures -> near + next (always two)
    options -> current, plus the next one once we are inside ROLL_AHEAD_DAYS
    """
    all_e, err = listed(symbol, product, **kw)
    if err:
        return [], err
    chooser = (active_future_expiries if product == "futures"
               else active_option_expiries)
    return _pick(all_e, chooser(all_e, today)), None


def main():
    """Print what Breeze returns for both product types. Reads only, writes nothing.

    The equivalent of sync_commodities' --resolve-only, for expiries. Its absence is
    why a bad futures lookup was first seen as a failed pipeline run rather than in
    two seconds at the point of change.
    """
    import argparse
    ap = argparse.ArgumentParser(description="Check Breeze expiry resolution.")
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--token", default=None, help="Breeze session token")
    args = ap.parse_args()
    for product in ("futures", "options"):
        raw, err = listed(args.symbol, product, session_token=args.token)
        if err:
            print(f"{product:<9} ERROR  {err}")
            continue
        act, _ = active(args.symbol, product, session_token=args.token)
        print(f"{product:<9} {len(raw)} listed: "
              f"{', '.join(e[:10] for e in raw[:6])}")
        print(f"{'':9} -> would use: {', '.join(e[:10] for e in act) or 'none'}")
    print("\nNothing written.")


if __name__ == "__main__":
    main()
