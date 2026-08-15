"""contract_registry.py — remember every futures contract we have ever seen.

WHY THIS IS THE IMPORTANT FILE
------------------------------
Upstox drops a contract from the instrument master the moment it expires, and the
Expired Instruments API cannot list MCX expiries because commodities have no
permanent underlying key — `MCX_FO|GOLD` returns 400. Expired candles ARE reachable,
but only if you can construct

    MCX_FO|{exchange_token}|{DD-MM-YYYY}

which means only if you already know the numeric token. Nothing recovers it later.

So the token is the perishable part, and this file is where it stops perishing. A
contract seen once is recorded forever, and its history stays fetchable for as long
as Upstox serves expired candles.

The cost of not having had this: contracts that expired before the 2026-07-28 scrip
master snapshot are unrecoverable. Their tokens were never written down, so roughly
a year of per-contract MCX history cannot be rebuilt at any price. Two contracts
survive — GOLD 466583 and COPPER 562048 — purely because that snapshot happened to
be on disk.

APPEND-ONLY
-----------
Entries are never removed and tokens are never rewritten. A contract that has
expired is exactly the entry that matters; pruning them would recreate the problem
this exists to solve. `first_seen` records when we learned of it, so a future
question about when coverage begins has an answer.

SOURCES
-------
  MCXScripMaster.txt   Breeze's snapshot. Its Token column IS the Upstox numeric
                       suffix — token 562057 is GOLDTEN 31-Aug-2026 in this file and
                       MCX_FO|562057 at Upstox. Verified, not assumed.
  Upstox complete.csv  the live master, on every sync.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(_HERE, "contracts.json")
SCRIP_MASTER = os.path.join(_HERE, "MCXScripMaster.txt")

# Breeze CompanyName -> our db product symbol
PRODUCT_MAP = {"GOLD": "GOLD", "SILVER": "SILVER", "COPPER": "COPPER",
               "CRUDEOIL": "CRUDEOIL_MCX"}


def load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"version": 1, "contracts": {}}


def save(doc):
    with open(PATH, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)


def key(product, expiry):
    return f"{product}_{expiry}"


def record(doc, product, expiry, token, exchange="MCX", extra=None, today=None):
    """Add a contract if unseen. Never overwrites a token. -> True if new."""
    k = key(product, expiry)
    if k in doc["contracts"]:
        return False
    doc["contracts"][k] = {
        "product": product, "expiry": expiry, "token": str(token),
        "exchange": exchange,
        "instrument_key": f"MCX_FO|{token}",
        # The form the Expired Instruments API needs once this contract dies. Built
        # now, while the pieces are known, rather than reconstructed later from a
        # date format nobody remembers.
        "expired_instrument_key":
            f"MCX_FO|{token}|{datetime.strptime(expiry, '%Y-%m-%d'):%d-%m-%Y}",
        "first_seen": str(today or date.today()),
        **(extra or {}),
    }
    return True


def seed_from_scrip_master(doc, path=None, today=None):
    """Harvest tokens from the Breeze MCX snapshot on disk."""
    path = path or SCRIP_MASTER
    added = 0
    try:
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("Series", "").strip().upper() != "FUTURE":
                    continue
                prod = PRODUCT_MAP.get(r.get("CompanyName", "").strip().upper())
                if not prod:
                    continue
                try:
                    exp = datetime.strptime(r["ExpiryDate"], "%d-%b-%Y").date()
                except (KeyError, ValueError):
                    continue
                if record(doc, prod, exp.isoformat(), r["Token"].strip(),
                          extra={"lot_size": r.get("LotSize"),
                                 "price_unit": r.get("PriceUnit"),
                                 "source": "MCXScripMaster.txt"}, today=today):
                    added += 1
    except OSError as e:
        return 0, f"{path}: {e}"
    return added, None


def seed_from_upstox(doc, today=None):
    """Harvest tokens from the live Upstox instrument master."""
    import re
    import pandas as pd
    added = 0
    try:
        df = pd.read_csv(
            "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz",
            low_memory=False)
    except Exception as e:                                   # noqa: BLE001
        return 0, str(e)[:200]
    df = df[df["instrument_key"].astype(str).str.startswith("MCX_FO|", na=False)].copy()
    it = df["instrument_type"].astype(str).str.upper().str.strip()
    df = df[it.str.startswith("FUT")]
    if "strike_price" in df.columns:
        df = df[pd.to_numeric(df["strike_price"], errors="coerce").fillna(0) == 0]
    tsym = df["tradingsymbol"].astype(str).str.upper().str.strip()
    for prod, code in (("GOLD", "GOLD"), ("SILVER", "SILVER"), ("COPPER", "COPPER"),
                       ("CRUDEOIL_MCX", "CRUDEOIL")):
        m = df[tsym.str.match(rf"^{code}[0-9]{{2}}[A-Z]{{3}}FUT$", na=False)]
        for _, r in m.iterrows():
            exp = str(pd.to_datetime(r["expiry"], errors="coerce"))[:10]
            if exp == "NaT" or not exp:
                continue
            token = str(r["instrument_key"]).split("|")[-1]
            if record(doc, prod, exp, token,
                      extra={"tradingsymbol": r.get("tradingsymbol"),
                             "source": "upstox"}, today=today):
                added += 1
    return added, None


def expired(doc, today=None):
    """Contracts that have expired — the ones needing the expired-candle endpoint."""
    t = today or date.today()
    return [c for c in doc["contracts"].values()
            if date.fromisoformat(c["expiry"]) < t]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Record MCX contract tokens, forever.")
    ap.add_argument("--no-upstox", action="store_true",
                    help="seed only from the local scrip master (no network)")
    args = ap.parse_args()

    doc = load()
    before = len(doc["contracts"])
    n1, err1 = seed_from_scrip_master(doc)
    print(f"MCXScripMaster.txt: +{n1}" + (f"  ({err1})" if err1 else ""))
    if not args.no_upstox:
        n2, err2 = seed_from_upstox(doc)
        print(f"Upstox live master : +{n2}" + (f"  ({err2})" if err2 else ""))
    save(doc)
    print(f"\n{PATH}: {before} -> {len(doc['contracts'])} contracts")
    exp = expired(doc)
    print(f"expired and now only reachable via the recorded token: {len(exp)}")
    for c in sorted(exp, key=lambda x: x["expiry"]):
        print(f"   {c['product']:<14} {c['expiry']}  {c['expired_instrument_key']}")


if __name__ == "__main__":
    main()
