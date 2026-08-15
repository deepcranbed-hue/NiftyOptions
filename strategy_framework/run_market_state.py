"""
strategy_framework/run_market_state.py
======================================
PRIORITY 0: the MARKET-STATE DATASET — one row per session's 10:00 IST entry.

Every row = the state KNOWABLE at 10:00 (features) + what happened AFTER (labels).
This is the container the selection layer learns from — features never get
redesigned per-study again; new feeds just add columns.

FEATURE columns (entry-time only, no lookahead):
  context   prev_day_ret_pct, overnight_gap_pct   (from NIFTY 1m BARS — the
            captures.spot column carries stale end-of-day values, a known
            capture bug, so closes are derived from bars)
  tape      ret_open_to_1000, er30, chop_index
  chain     atm_straddle_pts, straddle_chg_30m_pct, pcr, put_wall_dist_pts,
            call_wall_dist_pts, pin_share, oi_std_pts
  sector    <sector>_lead_pct = index-weighted sector return (open→10:00)
            MINUS the NIFTY return — who is dragging/driving this morning
  heavy     top-5 heavyweight morning returns
  global    (columns reserved, empty until the feeds exist — Phase 3)

LABEL columns (forward — allowed to look ahead, they're what we predict):
  day_ret_1000_close_pct   spot move 10:00 → close (same day)
  day_range_pts            high−low after 10:00
  next_gap_pct             next session open vs today close
  next_gap_big             |next_gap| >= 0.8%  (the overnight-shock label)
  straddle_exp_close_pct   ATM straddle 10:00 → 15:20 change (vol-expansion label)

Output: strategy_framework/knowledge/market_state.csv (+ a descriptive summary).
With ~25 sessions everything here is DESCRIPTIVE — the dataset earns inference
power one week at a time.

    python -m strategy_framework.run_market_state --db option_chains_full.db
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
from strategy_framework.config import constituents as K

_ENTRY = "04:30"          # 10:00 IST in UTC
_CLOSEQ = "09:50"         # 15:20 IST


def _bars_windows(con, syms):
    """{sym: {day: {'open': first close ≤04:31, 'at10': close @≤04:30,
                    'close': last close of day, 'hi'/'lo' after 10:00}}}.
    NIFTY loads the FULL day (labels need close/hi/lo); constituents load the
    MORNING window only (sector leadership needs open→10:00) — ~75% fewer rows,
    which is what keeps the whole build inside a shell timeout."""
    def _iter():
        q_n = ("SELECT symbol, ts, close, high, low FROM price_bars "
               "WHERE timeframe='1m' AND symbol='NIFTY' ORDER BY ts")
        yield from con.execute(q_n)
        others = [s for s in syms if s != "NIFTY"]
        ph = ",".join("?" * len(others))
        q_c = (f"SELECT symbol, ts, close, high, low FROM price_bars "
               f"WHERE timeframe='1m' AND symbol IN ({ph}) "
               f"AND substr(ts,12,5) <= '04:31' ORDER BY ts")
        yield from con.execute(q_c, others)
    out: dict = {}
    for sym, ts, c, h, l in _iter():
        d, hm = ts[:10], ts[11:16]
        rec = out.setdefault(sym, {}).setdefault(d, {})
        if "open" not in rec:
            rec["open"] = c
        if hm <= _ENTRY:
            rec["at10"] = c
        else:
            rec["hi"] = max(rec.get("hi", h), h)
            rec["lo"] = min(rec.get("lo", l), l)
        rec["close"] = c
    return out


def _crude_regime(con):
    """CRUDE (WTI) REGIME per session — the macro state that governs GAP risk.

    MCX crude quotes ₹/bbl; WTI $/bbl = ₹price / USDINR. Measured behaviour
    (28 sessions, frozen thresholds = domain priors, not fitted):
      below_80        crude is a LIVE intraday variable (corr −0.44..−0.48 with the
                      next session); gaps are small and mostly benign.
      crossing_up     first session ≥$80 after being below — the repricing event.
      sustained_high  ≥$80 for 3+ sessions: daily correlation collapses (−0.17) —
                      the level is priced in — BUT gap risk stays elevated
                      (gap↔intraday continuation +0.53 vs +0.15 below $80), i.e.
                      moves now arrive overnight and CONTINUE into the session.
      falling_back    first session back under $80.
    Policy use: this is a GAP-RISK / structure gate, never a direction call.
    """
    def _daily(sym):
        out = {}
        for d, c in con.execute("SELECT substr(ts,1,10), close FROM price_bars "
                                "WHERE symbol=? AND timeframe='1m' ORDER BY ts", (sym,)):
            r = out.setdefault(d, {"open": c, "close": c})
            r["close"] = c
        return out
    C, U = _daily("CRUDEOIL"), _daily("USDINR")
    days = sorted(set(C) & set(U))
    out, streak, prev_hi = {}, 0, False
    for i, d in enumerate(days):
        wti = C[d]["close"] / U[d]["close"] if U[d]["close"] else None
        if wti is None:
            continue
        hi = wti >= 80.0
        streak = streak + 1 if hi else 0
        reg = ("crossing_up" if hi and not prev_hi else
               "sustained_high" if hi and streak >= 3 else
               "elevated" if hi else
               "falling_back" if prev_hi else "below_80")
        w3 = [C[x]["close"] / U[x]["close"] for x in days[max(0, i - 2):i + 1] if U[x]["close"]]
        out[d] = {"wti_usd": round(wti, 1), "crude_regime": reg,
                  "crude_days_above_80": streak,
                  "crude_move_pct": round((C[d]["close"] / C[d]["open"] - 1) * 100, 2),
                  "crude_trend_3d_pct": round((w3[-1] / w3[0] - 1) * 100, 2) if len(w3) > 2 else None}
        prev_hi = hi
    return out


def _repricing_state(row, prev_rows):
    """REPRICING STATE — narrative-independent: is the market still charging a
    changing premium for uncertainty? Deliberately says nothing about crude, RBI,
    war or earnings; any narrative that trips a trigger flows through the same
    machine, and the STATE is read from the option market's own pricing.

        HIGH      straddle expanding ≥3% (or crush ≥+15%) — uncertainty being ADDED
        FALLING   straddle collapsing ≤−3% (or crush ≤−8%) — uncertainty ABSORBED
        NORMAL    neither — stable pricing
        REACTIVATED  HIGH again within 5 sessions of a prior HIGH that had settled:
                  an OLD narrative reopening (Hormuz-headline case), which behaves
                  differently from a genuinely new shock.

    VALIDATED (29 sessions, descriptive): premium-selling P&L by state —
      HIGH ₹1,676 (50% win) · NORMAL ₹7,313 (83%) · FALLING ₹8,170 (86%);
      day range 201 / 140 / 145 pts. So HIGH marks the state where short-vol
      assumptions break down — the decision-relevant finding, not a direction call.

    CAVEAT (kept explicit): straddle expansion is ONE OBSERVABLE PROXY for
    repricing, not ground truth — IV also moves on liquidity, dealer positioning,
    gamma imbalance and scheduled events. Treat as evidence, never as certainty.
    """
    exp = row.get("straddle_chg_30m_pct")
    crush = row.get("crush_excess_pct")
    if exp is None:
        return None
    high = exp >= 3.0 or (crush is not None and crush >= 15.0)
    falling = exp <= -3.0 or (crush is not None and crush <= -8.0)
    st = "HIGH" if high else ("FALLING" if falling else "NORMAL")
    if st == "HIGH":
        recent = [p.get("repricing_state") for p in prev_rows[-5:]]
        if "HIGH" in recent and recent[-1] in ("NORMAL", "FALLING"):
            st = "REACTIVATED"      # old narrative reopening, not a fresh shock
    return st


def _narrative_state(row, prev_rows):
    """LEGACY narrative lifecycle (kept for continuity; the decision layer uses
    `repricing_state`, which is narrative-independent and validated above).

    Key design choice: the stage is read from the OPTION MARKET'S OWN REPRICING,
    not from elapsed days and not from a hand-weighted ShockScore. Rationale —
    (a) a weighted composite of jump/news/IV/decay is the Dealer-Comfort-v1 trap
    (five plausible components, asserted weights, no validation); (b) the July
    test found NO time-based decay signature (|day move| by shock age: 0.27 /
    0.25 / 0.29 / 0.46 — flat), so 'days since event' is not yet a usable clock;
    (c) straddle/IV expansion IS the market publishing whether it is still
    repricing, which is exactly the quantity the lifecycle needs.

    Event-agnostic by construction: any trigger (crude cross, gap, RBI/Fed, a
    tariff headline) sets `trigger`; the stage then depends on measured repricing.
    """
    trig = row.get("crude_regime") in ("crossing_up",) or \
        (row.get("overnight_gap_pct") is not None and abs(row["overnight_gap_pct"]) >= 0.6)
    exp = row.get("straddle_chg_30m_pct")           # vol still expanding?
    crush = row.get("crush_excess_pct")             # premium richer than theta implies?
    repricing = (exp is not None and exp >= 3.0) or (crush is not None and crush >= 15.0)
    recent_trig = any(p.get("_trigger") for p in prev_rows[-3:])
    if trig and repricing:
        st = "NEW_SHOCK"          # event + market still marking up risk
    elif trig or (recent_trig and repricing):
        st = "ASSIMILATING"       # event landed, or repricing continues after one
    elif recent_trig:
        st = "PRICED"             # event happened, repricing has stopped
    elif row.get("crude_regime") == "falling_back":
        st = "RESOLVING"          # the driver itself is receding
    else:
        st = "NORMAL"
    row["_trigger"] = bool(trig)
    return st


def build(db: str) -> list[dict]:
    con = sqlite3.connect(db)
    da = DataAccess(db)
    crude = _crude_regime(con)
    sectors = sorted(set(K.SECTOR_OF.values()))
    heavies = K.HEAVYWEIGHTS[:5]
    syms = ["NIFTY"] + sorted(set(K.symbols()) - {"NIFTY"})
    B = _bars_windows(con, syms)
    nif = B.get("NIFTY", {})
    days = sorted(d for d, r in nif.items() if "at10" in r and "close" in r)
    expiries = sorted({r[0][:10] for r in con.execute("SELECT DISTINCT expiry FROM chain_rows")})
    exp_full = {r[0][:10]: r[0] for r in con.execute("SELECT DISTINCT expiry FROM chain_rows")}

    rows = []
    for i, d in enumerate(days):
        r = nif[d]
        prev = nif.get(days[i - 1]) if i > 0 else None
        prev2 = nif.get(days[i - 2]) if i > 1 else None
        nifty_morning = (r["at10"] / r["open"] - 1) * 100 if r.get("open") else None
        row = {"date": d, "spot_1000": r["at10"],
               "prev_day_ret_pct": (round((prev["close"] / prev2["close"] - 1) * 100, 3)
                                    if prev and prev2 else None),
               "overnight_gap_pct": (round((r["open"] / prev["close"] - 1) * 100, 3)
                                     if prev else None),
               "ret_open_to_1000_pct": round(nifty_morning, 3) if nifty_morning is not None else None}

        # tape: ER + choppiness over the first ~30 minutes (bars 04:00→04:30)
        mb = da.bars("NIFTY", "1m", end=f"{d}T{_ENTRY}:00Z", limit=31)
        closes = [b["close"] for b in mb]
        row["er30"] = round(efficiency_ratio(closes), 3) if len(closes) >= 3 else None
        if len(mb) >= 15:
            h = np.array([b["high"] for b in mb]); l = np.array([b["low"] for b in mb])
            c = np.array([b["close"] for b in mb])
            tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
            rng = h[1:].max() - l[1:].min()
            row["chop_index"] = round(float(100 * np.log10(tr.sum() / rng) / np.log10(len(tr))), 1) \
                if rng > 0 and tr.sum() > 0 else None
        else:
            row["chop_index"] = None

        # chain features at 10:00 (front expiry ≥ d)
        fexp = next((exp_full[e] for e in expiries if e >= d), None)
        ch = da.chain_as_of(f"{d}T{_ENTRY}:00Z", fexp) if fexp else None
        if ch:
            S, katm = option_oi.atm_straddle(ch)
            prior = option_oi.prior_chain(da, ch, f"{d}T{_ENTRY}:00Z", lookback_min=30)
            S0 = option_oi.atm_straddle(prior)[0] if prior else None
            tot_p = sum(v or 0 for v in ch.put_oi.values()); tot_c = sum(v or 0 for v in ch.call_oi.values())
            below = [(k, ch.put_oi.get(k, 0) or 0) for k in ch.strikes if k < ch.spot]
            above = [(k, ch.call_oi.get(k, 0) or 0) for k in ch.strikes if k > ch.spot]
            conc = option_oi.oi_concentration(ch)
            _, _, pin_share = option_oi.pin_strike(ch)
            row.update({"atm_straddle_pts": round(S, 1),
                        "straddle_chg_30m_pct": (round((S / S0 - 1) * 100, 2) if S0 else None),
                        "pcr": round(tot_p / tot_c, 3) if tot_c else None,
                        "put_wall_dist_pts": (round(ch.spot - max(below, key=lambda x: x[1])[0], 0)
                                              if below else None),
                        "call_wall_dist_pts": (round(max(above, key=lambda x: x[1])[0] - ch.spot, 0)
                                               if above else None),
                        "pin_share": round(pin_share, 3),
                        "oi_std_pts": round(conc["std"], 0) if conc else None})
            chc = da.chain_as_of(f"{d}T{_CLOSEQ}:00Z", fexp)
            Sc = option_oi.atm_straddle(chc)[0] if chc and chc.ts[:10] == d else None
            row["straddle_exp_close_pct"] = round((Sc / S - 1) * 100, 2) if (Sc and S) else None
            # CRUSH EXCESS: observed straddle decay MINUS the √T theta expectation.
            # An ATM straddle ~ σ√T, so with no vol change S_close/S_1000 ≈ √(T_c/T_1).
            # crush_excess < 0 → vol collapsed FASTER than time alone justifies (the
            # premium-seller's day); > 0 → options stayed bid despite the clock —
            # someone is paying up for protection. (Calendar-minute T; approximation.)
            if Sc and S:
                from datetime import date as _date
                ed = _date.fromisoformat(fexp[:10]).toordinal()
                dd = _date.fromisoformat(d).toordinal()
                t1 = (ed - dd) * 1440 + (595 - 270)      # 10:00 → expiry ~15:25 IST
                tc = (ed - dd) * 1440 + (595 - 590)      # 15:20 → expiry close
                exp_ratio = (tc / t1) ** 0.5 if t1 > 0 else None
                row["crush_excess_pct"] = (round((Sc / S - exp_ratio) * 100, 2)
                                           if exp_ratio is not None else None)
            else:
                row["crush_excess_pct"] = None
            # INTERACTION features (the relational edges): coverage ratio (escape
            # capacity), premium-per-unit-coverage (₹350 of straddle at coverage 0.9 is
            # a different animal from ₹350 at 0.05 — the 29-Jul detector), and
            # crush×chop (vol dying in a choppy tape = the premium-seller's day).
            wd = [row.get("put_wall_dist_pts"), row.get("call_wall_dist_pts")]
            if all(v is not None for v in wd) and S:
                cov = min(wd) / (0.8 * S)
                row["coverage_ratio"] = round(cov, 2)
                row["cov_adj_premium"] = round(S / max(cov, 0.05), 0)
            else:
                row["coverage_ratio"] = row["cov_adj_premium"] = None
        else:
            row.update({k: None for k in ("atm_straddle_pts", "straddle_chg_30m_pct", "pcr",
                                          "put_wall_dist_pts", "call_wall_dist_pts",
                                          "pin_share", "oi_std_pts", "straddle_exp_close_pct",
                                          "crush_excess_pct", "coverage_ratio",
                                          "cov_adj_premium")})

        # SECTOR LEADERSHIP (Phase 2 — from data we already own): index-weighted
        # morning return per sector MINUS NIFTY's morning return.
        for sec in sectors:
            num = den = 0.0
            for sym in K.symbols():
                if K.SECTOR_OF.get(sym) != sec:
                    continue
                sb = B.get(sym, {}).get(d)
                if not sb or "at10" not in sb or "open" not in sb or not sb["open"]:
                    continue
                w = K.weight_of(sym)
                num += w * (sb["at10"] / sb["open"] - 1) * 100
                den += w
            lead = (num / den - (nifty_morning or 0.0)) if den > 0 else None
            row[f"{sec.lower().replace(' ', '_').replace('&', 'and')}_lead_pct"] = \
                round(lead, 3) if lead is not None else None
        for sym in heavies:
            sb = B.get(sym, {}).get(d)
            row[f"{sym.lower()}_morning_pct"] = (round((sb["at10"] / sb["open"] - 1) * 100, 3)
                                                 if sb and sb.get("open") and "at10" in sb else None)

        # CRUDE macro regime (gap-risk state, not a direction call)
        row.update(crude.get(d, {"wti_usd": None, "crude_regime": None,
                                 "crude_days_above_80": None, "crude_move_pct": None,
                                 "crude_trend_3d_pct": None}))

        row["repricing_state"] = _repricing_state(row, rows)
        row["narrative_state"] = _narrative_state(row, rows)

        # LABELS (forward)
        row["day_ret_1000_close_pct"] = round((r["close"] / r["at10"] - 1) * 100, 3)
        row["day_range_pts"] = (round(r["hi"] - r["lo"], 0)
                                if "hi" in r and "lo" in r else None)
        nxt = nif.get(days[i + 1]) if i + 1 < len(days) else None
        gap = ((nxt["open"] / r["close"] - 1) * 100) if nxt and nxt.get("open") else None
        row["next_gap_pct"] = round(gap, 3) if gap is not None else None
        row["next_gap_big"] = (int(abs(gap) >= 0.8) if gap is not None else None)
        rows.append(row)
    con.close()
    return rows


def main():
    ap = argparse.ArgumentParser(description="Build the 10:00-IST market-state dataset.")
    ap.add_argument("--db", default="option_chains_full.db")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                                                  "knowledge", "market_state.csv"))
    args = ap.parse_args()
    rows = build(args.db)
    if not rows:
        print("no sessions found")
        return
    cols = list(rows[0].keys())
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows × {len(cols)} cols → {args.out}")

    # descriptive scan: |corr| of each feature vs each label (n is tiny — NO inference)
    labels = ["day_ret_1000_close_pct", "day_range_pts", "next_gap_pct", "straddle_exp_close_pct"]
    feats = [c for c in cols if c not in labels + ["date", "next_gap_big", "crude_regime", "narrative_state", "repricing_state", "_trigger"]]
    print("\ntop feature↔label |corr| (DESCRIPTIVE — ~%d rows, not evidence):" % len(rows))
    out = []
    for lab in labels:
        y = np.array([r.get(lab) if r.get(lab) is not None else np.nan for r in rows], float)
        for ft in feats:
            x = np.array([r.get(ft) if r.get(ft) is not None else np.nan for r in rows], float)
            m = ~np.isnan(x) & ~np.isnan(y)
            if m.sum() >= 10 and x[m].std() > 0 and y[m].std() > 0:
                out.append((abs(float(np.corrcoef(x[m], y[m])[0, 1])), ft, lab, int(m.sum())))
    out.sort(reverse=True)
    for a, ft, lab, n in out[:12]:
        print(f"  {ft:<28} ↔ {lab:<24} |r|={a:.2f}  (n={n})")


if __name__ == "__main__":
    main()
