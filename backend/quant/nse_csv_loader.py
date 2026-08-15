"""
nse_csv_loader.py
-----------------
Converts an NSE option-chain CSV export (the "option-chain-ED-NIFTY-*.csv"
download) into the `chain` dict the framework expects, and (optionally) builds
the RND so the output is ready to feed straight into the optimizer / pipeline.

NSE CSV layout (columns):
  CALLS: 1=OI 2=CHNG_IN_OI 3=VOLUME 4=IV 5=LTP 6=CHNG 7=BIDQTY 8=BID 9=ASK 10=ASKQTY
  11 = STRIKE
  PUTS:  12=BIDQTY 13=BID 14=ASK 15=ASKQTY 16=CHNG 17=LTP 18=IV 19=VOLUME
         20=CHNG_IN_OI 21=OI
Numbers use commas ("48,070") and "-" for blanks. OI is in CONTRACTS (not lakh).

Output `chain` dict (framework contract):
  strikes, call_ltp, put_ltp, spot, call_oi, put_oi, call_oi_chg, put_oi_chg,
  call_iv, put_iv, days, lot_size   (+ atm_iv, pcr convenience fields)
"""

from __future__ import annotations

import csv
from exchange_config import NIFTY_LOT_SIZE   # single source of truth for lot size


def _num(x):
    """'48,070' -> 48070.0 ; '-' or '' -> None ; '566.00' -> 566.0"""
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_nse_csv(path: str, *, spot: float, days: float, lot_size: int = NIFTY_LOT_SIZE,
                 oi_in_lakh: bool = False) -> dict:
    """
    Parse an NSE option-chain CSV into the framework `chain` dict.

    spot      : current underlying (NSE CSV doesn't always carry it cleanly — pass it)
    days      : calendar days to expiry (e.g. for 30-Jun expiry from 28-Jun -> 2;
                if today IS expiry, use a small fraction like 0.3 for intraday)
    lot_size  : NIFTY lot size (current NSE value; 65 as of 2026)
    oi_in_lakh: set True only if you want OI scaled to lakh; default keeps raw
                contracts (relative OI is all the wall/liquidity logic needs).
    """
    with open(path, newline="") as f:
        rows = list(csv.reader(f))

    # find the data rows: the header row is the one containing 'STRIKE'
    hdr_i = next(i for i, r in enumerate(rows)
                 if any("STRIKE" in (c or "").upper() for c in r))
    data = rows[hdr_i + 1:]

    strikes, c_ltp, p_ltp = [], [], []
    c_oi, p_oi, c_oichg, p_oichg, c_iv, p_iv = [], [], [], [], [], []

    for r in data:
        if len(r) < 22:
            continue
        strike = _num(r[11])
        if strike is None:
            continue
        # calls (left)
        co, coc, civ, clt = _num(r[1]), _num(r[2]), _num(r[4]), _num(r[5])
        # puts (right)
        plt, piv, poc, po = _num(r[17]), _num(r[18]), _num(r[20]), _num(r[21])

        # skip rows with no usable LTP on either side
        if clt is None and plt is None:
            continue

        scale = 1e-5 if oi_in_lakh else 1.0
        strikes.append(strike)
        c_ltp.append(clt if clt is not None else 0.0)
        p_ltp.append(plt if plt is not None else 0.0)
        c_oi.append((co or 0.0) * scale)
        p_oi.append((po or 0.0) * scale)
        c_oichg.append((coc or 0.0) * scale)        # NSE gives change in CONTRACTS; for %-change
        p_oichg.append((poc or 0.0) * scale)        #   you'd need prev OI — see note below.
        c_iv.append(civ)                  # IV in percent (e.g. 25.13) or None
        p_iv.append(piv)

    # sort by strike (NSE export is usually already sorted, but be safe)
    order = sorted(range(len(strikes)), key=lambda i: strikes[i])
    pick = lambda L: [L[i] for i in order]
    strikes = pick(strikes)
    chain = {
        "strikes": strikes,
        "call_ltp": pick(c_ltp), "put_ltp": pick(p_ltp),
        "call_oi": pick(c_oi), "put_oi": pick(p_oi),
        "call_oi_chg": pick(c_oichg), "put_oi_chg": pick(p_oichg),
        "call_iv": pick(c_iv), "put_iv": pick(p_iv),
        "spot": spot, "days": days, "lot_size": lot_size,
    }

    # convenience: ATM IV (nearest strike) and PCR
    ai = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    atm_civ = chain["call_iv"][ai]; atm_piv = chain["put_iv"][ai]
    ivs = [v for v in (atm_civ, atm_piv) if v]
    chain["atm_iv"] = round(sum(ivs) / len(ivs) / 100, 4) if ivs else None  # -> decimal
    tot_p = sum(chain["put_oi"]); tot_c = sum(chain["call_oi"]) or 1
    chain["pcr"] = round(tot_p / tot_c, 2)

    return chain


def window_chain(chain: dict, band_pts: float = 1200,
                 min_price: float = 2.0) -> dict:
    """Restrict a (possibly huge) NSE chain to a sensible band around spot, and
    drop strikes where BOTH legs are near-zero quoted noise. Wide NSE exports
    span e.g. 12000-34500 - 95% of that is deep-OTM noise that contaminates the
    Breeden-Litzenberger RND (variance blows up). Window BEFORE building the RND.

    Returns a NEW chain dict with the same keys, filtered. Use this before
    extract_rnd / optimize on full NSE CSV chains."""
    spot = chain["spot"]
    K = chain["strikes"]
    keep = []
    for i, k in enumerate(K):
        if abs(k - spot) > band_pts:
            continue
        clt = chain["call_ltp"][i]; plt = chain["put_ltp"][i]
        if (clt or 0) < min_price and (plt or 0) < min_price:
            continue            # both legs near-zero = noise
        keep.append(i)

    out = dict(chain)
    for key in ("strikes", "call_ltp", "put_ltp", "call_oi", "put_oi",
                "call_oi_chg", "put_oi_chg", "call_iv", "put_iv",
                "call_oi_chg_pct", "put_oi_chg_pct"):
        if key in chain and chain[key] is not None:
            out[key] = [chain[key][i] for i in keep]
    return out


# ── NOTE on OI-change % ──────────────────────────────────────────────────────
# The NSE CSV's "CHNG IN OI" is an ABSOLUTE change (contracts), not a %. The
# complacency gauge / OI-wall logic want PERCENT change (e.g. 150 for +150%).
# To get %, you need the PREVIOUS OI: pct = chng / (oi - chng) * 100.
def add_oi_change_pct(chain: dict) -> dict:
    """Derive OI-change % from absolute change + current OI (prev = oi - chng)."""
    def pct(oi, chg):
        prev = (oi - chg)
        return round(chg / prev * 100, 1) if prev and prev > 0 else 0.0
    chain["call_oi_chg_pct"] = [pct(o, c) for o, c in
                                zip(chain["call_oi"], chain["call_oi_chg"])]
    chain["put_oi_chg_pct"] = [pct(o, c) for o, c in
                               zip(chain["put_oi"], chain["put_oi_chg"])]
    return chain


if __name__ == "__main__":
    import sys, json
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/mnt/user-data/uploads/option-chain-ED-NIFTY-30-Jun-2026.csv"
    chain = load_nse_csv(path, spot=24050, days=2, lot_size=NIFTY_LOT_SIZE)
    chain = add_oi_change_pct(chain)
    print(f"Parsed {len(chain['strikes'])} strikes, "
          f"range {chain['strikes'][0]:.0f}–{chain['strikes'][-1]:.0f}")
    print(f"spot={chain['spot']} atm_iv={chain['atm_iv']} pcr={chain['pcr']}")
    # show the rows around spot
    ai = min(range(len(chain['strikes'])), key=lambda i: abs(chain['strikes'][i]-24050))
    print("\nstrike  call_ltp  put_ltp  call_oi  put_oi  put_oichg%")
    for i in range(max(0, ai-4), min(len(chain['strikes']), ai+5)):
        print(f"  {chain['strikes'][i]:.0f}  {chain['call_ltp'][i]:>8}  "
              f"{chain['put_ltp'][i]:>7}  {chain['call_oi'][i]:>8.0f}  "
              f"{chain['put_oi'][i]:>8.0f}  {chain['put_oi_chg_pct'][i]:>7}")
