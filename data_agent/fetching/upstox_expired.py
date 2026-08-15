"""upstox_expired.py — reach expired MCX contracts through Upstox's Expired
Instruments API (requires Upstox Plus).

WHY THIS EXISTS
---------------
The live instrument master drops a contract the moment it expires — a
`--list-contracts` run returned 0 expired contracts for all four products. That made
per-contract history look uncapturable in arrears: whatever we had not fetched while
a contract was alive appeared to be gone.

It is not gone. The Expired Instruments API serves both the contract keys and their
candles after expiry, which means the whole per-contract history can be rebuilt
rather than accumulated slowly from today.

    /v2/expired-instruments/expiry            -> past expiry dates for an underlying
    /v2/expired-instruments/future/contract   -> the expired_instrument_key for one
    /v2/expired-instruments/historical-candle/{key}/{interval}/{to}/{from}

THE UNDERLYING KEY IS THE UNCERTAIN PART
----------------------------------------
The docs give `NSE_INDEX|Nifty 50` as the example. MCX has no index in that sense,
so the underlying for a commodity is a guess until proven: `MCX_INDEX|GOLD`,
`MCX_FO|GOLD` and a bare `GOLD` are all plausible. Rather than pick one and write a
backfill on top of it, `probe()` tries the candidates and prints exactly what comes
back.

Nothing here writes to the database. This module only reads Upstox.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(os.path.dirname(_HERE)),
           os.path.join(os.path.dirname(os.path.dirname(_HERE)), "scratch_scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BASE = "https://api.upstox.com/v2/expired-instruments"

# Candidate shapes for a commodity underlying, in the order worth trying.
UNDERLYING_CANDIDATES = ["MCX_INDEX|{p}", "MCX_FO|{p}", "{p}", "MCX|{p}"]

PRODUCTS = ["GOLD", "SILVER", "COPPER", "CRUDEOIL"]


def _token():
    from upstox_auth import get_upstox_token
    return get_upstox_token()


def _get(path, params, token, timeout=30):
    """-> (status, parsed_json_or_text). Never raises on an HTTP error status."""
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body
    except Exception as e:                                   # noqa: BLE001
        return -1, str(e)


def expiries(underlying, token=None):
    """Past expiry dates for an underlying. -> (list, error)."""
    st, body = _get("expiries", {"instrument_key": underlying}, token or _token())
    if st != 200:
        return [], f"HTTP {st}: {str(body)[:200]}"
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        data = data.get("expiries") or data.get("expiry") or []
    return (data or []), None


def future_contract(underlying, expiry_date, token=None):
    """The expired_instrument_key for one expired futures contract. -> (key, error)."""
    st, body = _get("future/contract",
                    {"instrument_key": underlying, "expiry_date": expiry_date},
                    token or _token())
    if st != 200:
        return None, f"HTTP {st}: {str(body)[:200]}"
    data = (body or {}).get("data")
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return None, f"unexpected payload: {str(body)[:200]}"
    key = (data.get("expired_instrument_key") or data.get("instrument_key")
           or data.get("trading_symbol"))
    return key, None if key else f"no key in payload: {str(data)[:200]}"


def candles(expired_key, interval, frm, to, token=None):
    """Historical candles for an expired contract. -> (rows, error).

    Note the path order: {to_date}/{from_date}, matching the live historical-candle
    endpoint. Getting it backwards returns an empty list rather than an error, which
    is the kind of silence that reads as 'no data' — so it is stated here once.
    """
    url = (f"{BASE}/historical-candle/{urllib.parse.quote(expired_key, safe='')}"
           f"/{interval}/{to}/{frm}")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token or _token()}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
        return (body.get("data", {}) or {}).get("candles", []) or [], None
    except urllib.error.HTTPError as e:
        return [], f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:                                   # noqa: BLE001
        return [], str(e)


def probe():
    """Find which underlying key shape MCX accepts, and prove candles come back."""
    token = _token()
    print(f"token: {'present' if token else 'MISSING'}\n")
    working = {}
    for product in PRODUCTS:
        print(f"=== {product} ===")
        for shape in UNDERLYING_CANDIDATES:
            u = shape.format(p=product)
            exps, err = expiries(u, token)
            if err:
                print(f"   {u:<22} {err}")
                continue
            print(f"   {u:<22} OK — {len(exps)} past expiries"
                  f"{': ' + ', '.join(str(e)[:10] for e in exps[:6]) if exps else ''}")
            if exps:
                working[product] = (u, exps)
                break
        print()

    print("=== resolving one expired contract per product, and fetching a day ===")
    for product, (u, exps) in working.items():
        latest = sorted(str(e)[:10] for e in exps)[-1]
        key, err = future_contract(u, latest, token)
        if err:
            print(f"   {product:<10} {latest}  contract lookup failed: {err}")
            continue
        print(f"   {product:<10} {latest}  key={key}")
        rows, err = candles(key, "day", "2026-01-01", latest, token)
        if err:
            print(f"   {'':<10} candles failed: {err}")
        else:
            print(f"   {'':<10} {len(rows)} daily candles"
                  + (f", first {rows[-1][0][:10]} last {rows[0][0][:10]}" if rows else ""))
    print("\nNothing was written. If the keys resolve and candles come back, the full")
    print("per-contract history can be rebuilt and a wipe costs nothing.")


if __name__ == "__main__":
    probe()
