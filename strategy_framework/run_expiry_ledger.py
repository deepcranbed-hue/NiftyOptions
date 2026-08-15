"""
strategy_framework/run_expiry_ledger.py
=======================================
THE EXPIRY LEDGER — one expiry, every session, all three checkpoints, with the
state, the decision/action, and the book's P&L at each. The complete picture:

  10:00  ENTER <structure> / STAND ASIDE      (walk-forward hierarchy: gates →
                                               regime → familiarity → ranking,
                                               analogues STRICTLY earlier days)
  13:00  HOLD / DEFEND(sided) / EXIT           (kill-list vs the ACTUAL book)
  15:20  HOLD NIGHT / NIGHT-FLAG advisory /    (overnight ruling; SETTLE on
         SETTLE ₹X                              expiry day at intrinsic)

Positions carry real legs priced from the 10:00 chain; marks use LTP with
intrinsic fallback; P&L is net of ₹85/leg both ways. Output:
knowledge/ledger_<expiry>.csv.

    python -m strategy_framework.run_expiry_ledger --expiry 2026-07-28
"""
from __future__ import annotations
import argparse
import csv
import os
import sqlite3

import numpy as np

from strategy_framework.signals.data_access import DataAccess
from strategy_framework.signals import option_oi
from strategy_framework.strategy.tape_regime import efficiency_ratio
from strategy_framework.run_analogue_days import _FEATS, _f

_KNOW = os.path.join(os.path.dirname(__file__), "knowledge")
_LOT = 65
_STRUCTS = ["condor", "fly", "strangle", "straddle"]
_CAPPED = {"condor", "fly"}
_ALLOWED = {"expansion": [], "compressed-gamma": [], "pin": ["fly", "straddle"],
            "compression": _STRUCTS, "mixed": _STRUCTS}
_CKPTS = [("10:00", "04:30"), ("13:00", "07:30"), ("15:20", "09:50")]


def _chain_state(da, d, exp, hm):
    ch = da.chain_as_of(f"{d}T{hm}:00Z", exp)
    if not ch or ch.ts[:10] != d:
        return None
    S, K = option_oi.atm_straddle(ch)
    below = [(k, ch.put_oi.get(k, 0) or 0) for k in ch.strikes if k < ch.spot]
    above = [(k, ch.call_oi.get(k, 0) or 0) for k in ch.strikes if k > ch.spot]
    if not below or not above or not S:
        return None
    pw = max(below, key=lambda x: x[1])[0]
    cw = max(above, key=lambda x: x[1])[0]
    return {"ch": ch, "spot": ch.spot, "S": S, "K": K, "pw": pw, "cw": cw,
            "cov": min(ch.spot - pw, cw - ch.spot) / (0.8 * S)}


def _decide(ms, cs, ti, dates_all, d):
    """Walk-forward 10:00 decision (same logic as the policy replay)."""
    t = ms[ti]
    cov, er = _f(t, "coverage_ratio"), _f(t, "er30")
    chop, schg = _f(t, "chop_index"), _f(t, "straddle_chg_30m_pct")
    pin = _f(t, "pin_share")
    fatal = (cov is not None and cov < 0.05) or \
            (schg is not None and schg >= 3.0) or (er is not None and er >= 0.55)
    restrict = (not fatal) and (cov is not None and cov < 0.10)
    if cov is not None and cov < 0.05:
        reg = "compressed-gamma"
    elif fatal:
        reg = "expansion"
    elif pin is not None and pin >= 0.15 and chop is not None and chop >= 50:
        reg = "pin"
    elif (chop is not None and chop >= 55) or (er is not None and er <= 0.20):
        reg = "compression"
    else:
        reg = "mixed"
    X = np.array([[(_f(r, c) if _f(r, c) is not None else np.nan) for c in _FEATS]
                  for r in ms], float)
    mu, sd = np.nanmean(X, axis=0), np.nanstd(X, axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    hist = [i for i, r in enumerate(ms) if r["date"] in cs and r["date"] < d]
    sims = []
    for j in hist:
        m = ~np.isnan(Z[ti]) & ~np.isnan(Z[j])
        if m.sum() >= 8:
            sims.append((float(np.sqrt(np.mean((Z[ti][m] - Z[j][m]) ** 2))), j))
    sims.sort()
    top = sims[:7]
    fam = max(0.0, min(100.0, (1.6 - float(np.mean([x[0] for x in top]))) / 1.6 * 100)) \
        if top else 0.0
    allowed = [] if fatal else [s for s in _ALLOWED[reg] if not (restrict and s in _CAPPED)]
    if not allowed or len(top) < 3 or fam < 40:
        why = ("L1 gate" if fatal else "L2 gate" if (restrict and not allowed)
               else f"familiarity {fam:.0f}%<40" if fam < 40 else "no history")
        return "STAND ASIDE", why, fam, reg
    w = np.array([1.0 / (x[0] + 0.25) for x in top]); w /= w.sum()
    scores = {s: float((w * np.array([_f(cs[ms[j]['date']], f"{s}_pnl") or 0.0
                                      for _, j in top])).sum()) for s in allowed}
    return max(scores, key=scores.get), f"fam {fam:.0f}% regime {reg}", fam, reg


def _open_position(st, structure, wing=100):
    ch = st["ch"]
    legs = {"condor": [("C", st["cw"], 1), ("C", st["cw"] + wing, -1),
                       ("P", st["pw"], 1), ("P", st["pw"] - wing, -1)],
            "fly": [("C", st["K"], 1), ("P", st["K"], 1),
                    ("C", st["K"] + wing, -1), ("P", st["K"] - wing, -1)],
            "strangle": [("C", st["cw"], 1), ("P", st["pw"], 1)],
            "straddle": [("C", st["K"], 1), ("P", st["K"], 1)]}[structure]
    out, credit = [], 0.0
    for cp, k, sgn in legs:
        px = (ch.call_ltp if cp == "C" else ch.put_ltp).get(k)
        if not px or px <= 0:
            return None
        out.append([cp, k, sgn, px])
        credit += sgn * px
    return {"structure": structure, "legs": out, "credit": credit}


def _mark(pos, st, settle=False):
    debit = 0.0
    breach = []
    for cp, k, sgn, epx in pos["legs"]:
        if settle:
            cur = max(0.0, (st["spot"] - k) if cp == "C" else (k - st["spot"]))
        else:
            cur = (st["ch"].call_ltp if cp == "C" else st["ch"].put_ltp).get(k)
            if not cur or cur <= 0:
                cur = max(0.0, (st["spot"] - k) if cp == "C" else (k - st["spot"]))
        debit += sgn * cur
        if sgn > 0:
            dist = (st["spot"] - k) if cp == "P" else (k - st["spot"])
            if dist <= 0:
                breach.append(f"{cp}{k:.0f}")
    costs = 2 * len(pos["legs"]) * (20.0 + 1.0 * _LOT)
    return (pos["credit"] - debit) * _LOT - costs, breach


def main():
    ap = argparse.ArgumentParser(description="Full 3-checkpoint ledger for one expiry.")
    ap.add_argument("--db", default="option_chains_full.db")
    ap.add_argument("--expiry", default="2026-07-28")
    args = ap.parse_args()

    da = DataAccess(args.db)
    con = sqlite3.connect(args.db)
    exp = con.execute("SELECT DISTINCT expiry FROM chain_rows WHERE expiry LIKE ?",
                      (args.expiry + "%",)).fetchone()[0]
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT substr(c.captured_at,1,10) FROM captures c JOIN chain_rows r "
        "ON r.capture_id=c.capture_id AND r.expiry=? ORDER BY 1", (exp,))]
    con.close()
    week = [d for d in days if d <= exp[:10]][-5:]
    ms = list(csv.DictReader(open(os.path.join(_KNOW, "market_state.csv"))))
    cs = {r["entry"]: r for r in csv.DictReader(open(os.path.join(_KNOW, "chain_structure.csv")))}
    midx = {r["date"]: i for i, r in enumerate(ms)}

    book, rows = [], []
    for d in week:
        base = _chain_state(da, d, exp, "04:30")
        is_exp = d == exp[:10]
        for ist, hm in _CKPTS:
            st = _chain_state(da, d, exp, hm)
            if st is None:
                continue
            schg = (st["S"] / base["S"] - 1) * 100 if base else 0.0
            action, detail = "", ""
            if ist == "10:00":
                if is_exp:
                    action, detail = "NO NEW ENTRY", "expiry day (pin-risk gate)"
                elif d in midx:
                    dec, why, fam, reg = _decide(ms, cs, midx[d], days, d)
                    if dec == "STAND ASIDE":
                        action, detail = "STAND ASIDE", why
                    else:
                        p = _open_position(st, dec)
                        if p:
                            book.append(p)
                            action = f"ENTER {dec}"
                            detail = (why + " | legs " +
                                      " ".join(f"{cp}{k:.0f}@{px}" for cp, k, sgn, px in p["legs"]))
                        else:
                            action, detail = "STAND ASIDE", "not priceable"
            else:
                mb = np.array([efficiency_ratio([b["close"] for b in
                                                da.bars("NIFTY", "1m", end=f"{d}T{hm}:00Z",
                                                        limit=31)] or [0, 0, 0])])
                er_now = float(mb[0]) if mb[0] is not None else None
                trend_conf = (schg >= 3.0) or (er_now is not None and er_now >= 0.55)
                if not book:
                    action = "—"
                elif schg >= 5.0:
                    action, detail = "EXIT SIGNAL", f"straddle {schg:+.1f}% ≥ +5%"
                else:
                    anybr = any(_mark(p, st)[1] for p in book)
                    if anybr and trend_conf:
                        action, detail = "DEFEND (sided)", "breach + trend-expansion confirmed"
                    elif anybr:
                        action, detail = "HOLD", "breach but no trend confirm — do not chase"
                    else:
                        action = "HOLD"
                if ist == "15:20" and book:
                    if is_exp:
                        tot = sum(_mark(p, st, settle=True)[0] for p in book)
                        action, detail = f"SETTLE ₹{tot:+.0f}", "expiry intrinsic"
                    else:
                        flag = schg >= 3.0 or st["cov"] < 0.10
                        action += " | NIGHT-FLAG" if flag else " | HOLD NIGHT"
            pnl = sum(_mark(p, st, settle=(is_exp and ist == "15:20"))[0] for p in book) \
                if book else None
            brs = ",".join(b for p in book for b in _mark(p, st)[1]) if book else ""
            rows.append({"date": d, "ckpt_ist": ist, "spot": round(st["spot"], 0),
                         "straddle": round(st["S"], 1), "coverage": round(st["cov"], 2),
                         "straddle_chg_vs_1000_pct": round(schg, 1),
                         "book": "+".join(p["structure"] for p in book) or "flat",
                         "book_pnl_inr": (round(pnl, 0) if pnl is not None else None),
                         "breached_shorts": brs, "action": action, "detail": detail})

    out = os.path.join(_KNOW, f"ledger_{exp[:10]}.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} checkpoint rows → {out}\n")
    print(f"{'date':<12}{'ckpt':<7}{'spot':>7}{'strad':>7}{'cov':>6}{'book':<19}"
          f"{'P&L':>8}  action")
    print("-" * 88)
    for r in rows:
        pnl = r["book_pnl_inr"]
        pnl_s = f"{pnl:+.0f}" if pnl is not None else "·"
        print(f"{r['date']:<12}{r['ckpt_ist']:<7}{r['spot']:>7.0f}{r['straddle']:>7.1f}"
              f"{r['coverage']:>6}{r['book']:<19}{pnl_s:>8}  {r['action']}")


if __name__ == "__main__":
    main()
