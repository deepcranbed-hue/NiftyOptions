"""
strategy_framework/run_condor_study.py
======================================
IRON CONDOR expiry-week study: enter a 4-leg condor N days before expiry, mark it
capture-by-capture (minute cadence) to expiry, and compare MANAGEMENT rules.

Structure: short call Kc + long call Kc+W, short put Kp + long put Kp−W (net credit).
Short strikes either at the OI WALLS (max call-OI above / max put-OI below — the same
walls breadth_oi reads) or at fixed offsets. Marks are off chain LTPs (no bid/ask in
the capture), charged with the shared CostModel (8 legs round trip). Management:

    hold      carry to expiry close
    pt50      close when P&L ≥ 50% of max (theoretical) credit kept
    stop      close when P&L ≤ −stop_mult × credit
    pt+stop   both exits armed

Honest scope: ONE expiry is ONE sample — this shows the mechanics and the levers
(entry day, strike distance, management), not a validated edge. Slippage is whatever
the CostModel carries (default 0 pts): real condor fills cross 4 spreads, so live
results are worse than these marks.

    python -m strategy_framework.run_condor_study --db option_chains_full.db \
        --expiry 2026-07-28 --entry-date 2026-07-21
"""
from __future__ import annotations
import argparse
import sqlite3

from exchange_config import NIFTY_LOT_SIZE
from strategy_framework.config.settings import CostModel

_COSTS = CostModel()
_LOT = NIFTY_LOT_SIZE


def _chain_at(con, exp, ts_min):
    cap = con.execute(
        "SELECT c.capture_id, c.captured_at, c.spot FROM captures c WHERE c.captured_at>=? "
        "AND c.capture_id IN (SELECT capture_id FROM chain_rows WHERE expiry=?) "
        "ORDER BY c.captured_at LIMIT 1", (ts_min, exp)).fetchone()
    if not cap:
        return None
    rows = con.execute("SELECT strike, call_ltp, put_ltp, call_oi, put_oi FROM chain_rows "
                       "WHERE capture_id=? AND expiry=? ORDER BY strike", (cap[0], exp)).fetchall()
    return {"id": cap[0], "ts": cap[1], "spot": cap[2], "rows": rows}


def _pick_strikes(chain, mode, offset):
    spot = chain["spot"]
    if mode == "walls":
        below = [(k, poi) for k, cl, pl, coi, poi in chain["rows"] if k < spot]
        above = [(k, coi) for k, cl, pl, coi, poi in chain["rows"] if k > spot]
        kp = max(below, key=lambda x: x[1])[0]
        kc = max(above, key=lambda x: x[1])[0]
    else:
        ks = [r[0] for r in chain["rows"]]
        kp = min(ks, key=lambda k: abs(k - (spot - offset)))
        kc = min(ks, key=lambda k: abs(k - (spot + offset)))
    return kp, kc


def _leg_series(con, exp, strikes, ts_from, ts_to):
    """{ts: {(strike,'C'|'P'): ltp}, ...} for the needed strikes over the window."""
    ph = ",".join("?" * len(strikes))
    q = (f"SELECT c.captured_at, r.strike, r.call_ltp, r.put_ltp, c.spot "
         f"FROM captures c JOIN chain_rows r ON r.capture_id=c.capture_id AND r.expiry=? "
         f"WHERE r.strike IN ({ph}) AND c.captured_at>=? AND c.captured_at<=? "
         f"ORDER BY c.captured_at")
    out = {}
    spots = {}
    for ts, k, cl, pl, spot in con.execute(q, (exp, *strikes, ts_from, ts_to)):
        out.setdefault(ts, {})[(k, "C")] = cl
        out[ts][(k, "P")] = pl
        spots[ts] = spot
    return out, spots


def run_condor(db, exp_full, entry_ts, mode="walls", offset=200, wing=100,
               profit_take=None, stop_mult=None, lots=1, slippage_pts=1.0):
    """`slippage_pts`: assumed cost of crossing the bid-ask PER LEG per side, in index
    points (LTP marks carry no spread, so without this the trade is fictionally free to
    execute). 1.0pt/leg is a fair prior for liquid NIFTY weeklies; wings can be worse."""
    costs = CostModel(slippage_pts=slippage_pts)
    con = sqlite3.connect(db)
    ch = _chain_at(con, exp_full, entry_ts)
    if not ch:
        con.close()
        return {"error": f"no chain at/after {entry_ts}"}
    kp, kc = _pick_strikes(ch, mode, offset)
    legs = {"sc": (kc, "C"), "lc": (kc + wing, "C"), "sp": (kp, "P"), "lp": (kp - wing, "P")}
    strikes = sorted({k for k, _ in legs.values()})
    px = {r[0]: {"C": r[1], "P": r[2]} for r in ch["rows"]}
    try:
        entry = {n: px[k][cp] for n, (k, cp) in legs.items()}
    except KeyError:
        con.close()
        return {"error": f"wing strike missing from chain (need {strikes})"}
    credit = entry["sc"] + entry["sp"] - entry["lc"] - entry["lp"]
    max_loss = wing - credit
    open_cost = costs.legs_cost_inr(4 * lots, _LOT)
    close_cost = costs.legs_cost_inr(4 * lots, _LOT)

    exp_day = exp_full[:10]
    series, spots = _leg_series(con, exp_full, strikes, ch["ts"], exp_day + "T23:59:59Z")
    con.close()

    def _leg_px(m, k, cp, s):
        """LTP when captured; else INTRINSIC approximation (capture window follows spot,
        so far-OTM/ITM strikes drop out — approximate rather than truncate the path).
        Slightly flatters: missing legs carry no time value."""
        v = m.get((k, cp))
        if v is not None and v > 0:
            return v, False
        if s is None:
            return None, False
        return max(0.0, (s - k) if cp == "C" else (k - s)), True

    path, spot_lo, spot_hi, n_approx = [], ch["spot"], ch["spot"], 0
    for ts in sorted(series):
        m = series[ts]
        s = spots.get(ts)
        vals = {}
        bad = False
        approx = False
        for n, (k, cp) in legs.items():
            v, ap = _leg_px(m, k, cp, s)
            if v is None:
                bad = True
                break
            vals[n] = v
            approx = approx or ap
        if bad:
            continue
        n_approx += 1 if approx else 0
        debit_now = vals["sc"] + vals["sp"] - vals["lc"] - vals["lp"]
        pnl = (credit - debit_now) * _LOT * lots - open_cost - close_cost
        if s:
            spot_lo, spot_hi = min(spot_lo, s), max(spot_hi, s)
        path.append((ts, pnl, s))

    # SETTLEMENT: if the path reaches expiry day, restate the final mark from pure
    # intrinsics at the last spot (weekly options cash-settle to the closing level).
    reached_expiry = bool(path) and path[-1][0][:10] == exp_day
    if reached_expiry and path[-1][2]:
        s_end = path[-1][2]
        intr = {n: max(0.0, (s_end - k) if cp == "C" else (k - s_end))
                for n, (k, cp) in legs.items()}
        debit_end = intr["sc"] + intr["sp"] - intr["lc"] - intr["lp"]
        pnl_end = (credit - debit_end) * _LOT * lots - open_cost - close_cost
        path[-1] = (path[-1][0], pnl_end, s_end)

    def _managed(pt, sm):
        for ts, pnl, s in path:
            if sm is not None and pnl <= -sm * credit * _LOT * lots:
                return pnl, ts, "stop"
            if pt is not None and pnl >= pt * credit * _LOT * lots:
                return pnl, ts, "profit_take"
        t, p, _ = path[-1]
        return p, t, ("expiry" if reached_expiry else "DATA_END")

    variants = {"hold": _managed(None, None),
                "pt50": _managed(0.50, None),
                "stop1.5x": _managed(None, 1.5),
                "pt50+stop": _managed(0.50, 1.5)}
    pnls = [p for _, p, _ in path]
    daily = {}
    daily_min = {}
    daily_open = {}
    for ts, pnl, _ in path:
        d = ts[:10]
        if d not in daily_open:
            daily_open[d] = pnl                                # first mark of each day
        daily[d] = pnl                                         # last mark of each day
        daily_min[d] = min(daily_min.get(d, 1e18), pnl)
    # OVERNIGHT vs INTRADAY decomposition: gap P&L = each day's open minus the prior
    # day's close; intraday P&L = each day's close minus its own open.
    days_sorted = sorted(daily)
    overnight = sum(daily_open[days_sorted[i]] - daily[days_sorted[i - 1]]
                    for i in range(1, len(days_sorted)))
    intraday = sum(daily[d] - daily_open[d] for d in days_sorted)
    return {"entry_ts": ch["ts"], "spot": round(ch["spot"], 1),
            "short_put": kp, "short_call": kc, "wing": wing,
            "legs_entry": {k: round(v, 2) for k, v in entry.items()},
            "credit_pts": round(credit, 1), "max_loss_pts": round(max_loss, 1),
            "credit_inr": round(credit * _LOT * lots, 0),
            "costs_inr": round(open_cost + close_cost, 0),
            "spot_range": (round(spot_lo, 0), round(spot_hi, 0)),
            "n_marks": len(path), "n_approx": n_approx,
            "daily_open_pnl": {d: round(v, 0) for d, v in daily_open.items()},
            "overnight_pnl": round(overnight, 0), "intraday_pnl": round(intraday, 0),
            "worst": round(min(pnls), 0), "best": round(max(pnls), 0),
            "daily_close": {d: round(v, 0) for d, v in daily.items()},
            "daily_min": {d: round(v, 0) for d, v in daily_min.items()},
            "variants": {k: (round(p, 0), ts[:16], why) for k, (p, ts, why) in variants.items()}}


def _full_chain_series(con, exp, ts_from, ts_to):
    """{ts: {'spot': s, 'rows': {strike: (call_ltp, put_ltp, call_oi, put_oi)}}}"""
    out = {}
    q = ("SELECT c.captured_at, c.spot, r.strike, r.call_ltp, r.put_ltp, r.call_oi, r.put_oi "
         "FROM captures c JOIN chain_rows r ON r.capture_id=c.capture_id AND r.expiry=? "
         "WHERE c.captured_at>=? AND c.captured_at<=? ORDER BY c.captured_at")
    for ts, spot, k, cl, pl, coi, poi in con.execute(q, (exp, ts_from, ts_to)):
        d = out.setdefault(ts, {"spot": spot, "rows": {}})
        d["rows"][k] = (cl, pl, coi, poi)
    return out


def _atm_straddle_at(d):
    """ATM straddle from one snapshot dict {'spot','rows'} (None if legs missing)."""
    s, rows = d["spot"], d["rows"]
    if not rows:
        return None
    k = min(rows, key=lambda x: abs(x - s))
    cl, pl = rows[k][0], rows[k][1]
    return (cl + pl) if (cl and pl) else None


def _trend_expansion(series, tss, i, er_window=30, er_thr=0.55, strad_thr=0.03):
    """The 'do-not-defend-the-old-range' detector at mark i: is the tape in TREND
    EXPANSION? True when the last `er_window` minutes travelled efficiently in one
    direction (Kaufman ER — the shared primitive) OR the ATM straddle is EXPANDING
    (the market repricing a bigger move). This is the fast, in-simulator version of the
    straddle_flow + tape-regime beliefs — computed from the same definitions."""
    from strategy_framework.strategy.tape_regime import efficiency_ratio
    j0 = max(0, i - er_window)
    spots = [series[t]["spot"] for t in tss[j0:i + 1] if series[t]["spot"]]
    er = efficiency_ratio(spots) if len(spots) >= 3 else None
    s_now = _atm_straddle_at(series[tss[i]])
    s_prev = _atm_straddle_at(series[tss[j0]])
    strad_chg = ((s_now - s_prev) / s_prev) if (s_now and s_prev and s_prev > 0) else None
    trending = bool((er is not None and er >= er_thr) or
                    (strad_chg is not None and strad_chg >= strad_thr))
    return trending, er, strad_chg


def run_rolling_condor(db, exp_full, entry_ts, mode="walls", offset=200, wing=100,
                       lots=1, slippage_pts=1.0, buffer_pts=0.0,
                       cooldown_min=30, max_rolls=8, gate: str = "always"):
    """ADJUSTING condor: when spot touches a short strike (± buffer), CLOSE the whole
    structure at marks and RE-CENTER a fresh condor around the current market (same
    mode/wing, same expiry) — 'move the range along with the market'. Every roll
    realizes the P&L of the old legs and pays 8 legs of costs, so this tests whether
    following the market saves more than the rolling bleeds. Cooldown + max_rolls stop
    pathological churn. Settlement at expiry = intrinsic at the last spot."""
    costs = CostModel(slippage_pts=slippage_pts)
    leg_cost4 = costs.legs_cost_inr(4 * lots, _LOT)
    con = sqlite3.connect(db)
    exp_day = exp_full[:10]
    series = _full_chain_series(con, exp_full, entry_ts, exp_day + "T23:59:59Z")
    con.close()
    if not series:
        return {"error": f"no chain data from {entry_ts}"}
    tss = sorted(series)

    def _px(rows, k, cp, s):
        r = rows.get(k)
        v = (r[0] if cp == "C" else r[1]) if r else None
        if v is not None and v > 0:
            return v
        return max(0.0, (s - k) if cp == "C" else (k - s))    # intrinsic fallback

    def _open(ts):
        d = series[ts]
        s, rows = d["spot"], d["rows"]
        if mode == "walls":
            below = [(k, v[3]) for k, v in rows.items() if k < s]
            above = [(k, v[2]) for k, v in rows.items() if k > s]
            if not below or not above:
                return None
            kp = max(below, key=lambda x: x[1])[0]
            kc = max(above, key=lambda x: x[1])[0]
        else:
            ks = sorted(rows)
            kp = min(ks, key=lambda k: abs(k - (s - offset)))
            kc = min(ks, key=lambda k: abs(k - (s + offset)))
        legs = {"sc": (kc, "C"), "lc": (kc + wing, "C"), "sp": (kp, "P"), "lp": (kp - wing, "P")}
        entry = {n: _px(rows, k, cp, s) for n, (k, cp) in legs.items()}
        credit = entry["sc"] + entry["sp"] - entry["lc"] - entry["lp"]
        if credit <= 0:
            return None
        return {"legs": legs, "entry": entry, "credit": credit, "sp": kp, "sc": kc, "ts": ts}

    i0 = 0
    pos = _open(tss[i0])
    if pos is None:
        return {"error": "could not open condor at entry"}
    realized = -leg_cost4                      # opening costs
    rolls, worst, last_roll_i = [], 0.0, i0
    final_ts = tss[-1]
    for i in range(i0 + 1, len(tss)):
        ts = tss[i]
        d = series[ts]
        s, rows = d["spot"], d["rows"]
        debit = sum(_px(rows, *pos["legs"][n], s) * (1 if n in ("sc", "sp") else -1)
                    for n in ("sc", "sp", "lc", "lp"))
        # NB: debit = sc+sp-lc-lp cost to close
        unreal = (pos["credit"] - debit) * _LOT * lots
        worst = min(worst, realized + unreal - leg_cost4)
        touched = (s <= pos["sp"] + buffer_pts) or (s >= pos["sc"] - buffer_pts)
        is_expiry_close = (ts == final_ts)
        if is_expiry_close:
            if ts[:10] == exp_day:            # settle at intrinsic
                intr = {n: max(0.0, (s - k) if cp == "C" else (k - s))
                        for n, (k, cp) in pos["legs"].items()}
                debit = intr["sc"] + intr["sp"] - intr["lc"] - intr["lp"]
            realized += (pos["credit"] - debit) * _LOT * lots - leg_cost4
            pos = None
            break
        if gate == "daily":
            # DAILY RE-CENTER: roll every morning at/after 10:00 IST regardless of
            # touches — "adjust the condor each day based on the opening/morning".
            touched = (ts[:10] > pos["ts"][:10]) and (ts[11:19] >= "04:30:00")
        if touched and gate == "regime":
            # BELIEF-GATED roll: only defend by re-centering when the tape says TREND
            # EXPANSION (efficient directional travel or straddle repricing). A touch
            # during pin/chop is left alone — rolling there is the whipsaw bleed.
            trending, _er, _sc = _trend_expansion(series, tss, i)
            touched = touched and trending
        if touched and len(rolls) < max_rolls and (i - last_roll_i) >= cooldown_min:
            realized += (pos["credit"] - debit) * _LOT * lots - leg_cost4   # close old
            new = _open(ts)
            if new is None:
                pos = None
                break
            realized -= leg_cost4                                           # open new
            rolls.append({"ts": ts[:16], "spot": round(s, 0),
                          "from": (pos["sp"], pos["sc"]), "to": (new["sp"], new["sc"])})
            pos, last_roll_i = new, i
    return {"entry_ts": tss[i0], "final_pnl": round(realized, 0), "n_rolls": len(rolls),
            "rolls": rolls, "worst_mark": round(worst, 0),
            "roll_cost_inr": round(2 * leg_cost4, 0)}


def run_static_structure(db, exp_full, entry_ts, structure="condor", wing=100,
                         lots=1, slippage_pts=1.0, hedge_band=50, hedge_hyst=30,
                         hedge_times=None):
    """Generic premium-selling STRUCTURE, held to expiry (no adjustments):

        condor     short put/call at the OI walls + wings          (bounded)
        strangle   short put/call at the walls, NO wings           (unbounded)
        straddle   short ATM call + put                            (unbounded, max credit)
        fly        short ATM straddle + wings ±wing                (bounded)
        straddle_h short ATM straddle + FUTURES delta hedge:       (unbounded, hedged)
                   long 1 fut when spot > K+band, short 1 fut when spot < K−band,
                   flat inside K±hyst (spot used as the futures proxy — basis ignored).

    `hedge_times`: None = re-check the hedge EVERY minute (continuous — pays whipsaw at
    the band on every oscillation); or a list of UTC times, e.g. ["04:30","07:30","09:50"]
    (10:00 / 13:00 / 15:20 IST) = SCHEDULED hedging — at most that many hedge decisions
    per day, and the last checkpoint sets the overnight-gap hedge.

    Same marks (LTP + intrinsic fallback), same cost model (every leg and every hedge
    trade pays brokerage + slippage), intrinsic settlement at expiry."""
    costs = CostModel(slippage_pts=slippage_pts)
    con = sqlite3.connect(db)
    exp_day = exp_full[:10]
    series = _full_chain_series(con, exp_full, entry_ts, exp_day + "T23:59:59Z")
    con.close()
    if not series:
        return {"error": f"no chain data from {entry_ts}"}
    tss = sorted(series)
    d0 = series[tss[0]]
    s0, rows0 = d0["spot"], d0["rows"]

    def _px(rows, k, cp, s):
        r = rows.get(k)
        v = (r[0] if cp == "C" else r[1]) if r else None
        if v is not None and v > 0:
            return v
        return max(0.0, (s - k) if cp == "C" else (k - s))

    K = min(rows0, key=lambda k: abs(k - s0))
    below = [(k, v[3]) for k, v in rows0.items() if k < s0]
    above = [(k, v[2]) for k, v in rows0.items() if k > s0]
    if not below or not above:
        return {"error": "spot outside strike ladder"}
    kp = max(below, key=lambda x: x[1])[0]
    kc = max(above, key=lambda x: x[1])[0]

    hedged = structure == "straddle_h"
    legs = {"condor": [(kc, "C", 1), (kc + wing, "C", -1), (kp, "P", 1), (kp - wing, "P", -1)],
            "strangle": [(kc, "C", 1), (kp, "P", 1)],
            "straddle": [(K, "C", 1), (K, "P", 1)],
            "straddle_h": [(K, "C", 1), (K, "P", 1)],
            "fly": [(K, "C", 1), (K, "P", 1), (K + wing, "C", -1), (K - wing, "P", -1)],
            }[structure]
    credit = sum(sd * _px(rows0, k, cp, s0) for k, cp, sd in legs)
    if credit <= 0:
        return {"error": "no net credit at entry"}
    n_legs = len(legs)
    opt_cost = 2 * costs.legs_cost_inr(n_legs * lots, _LOT)      # open + close
    fut_leg = costs.legs_cost_inr(lots, _LOT)

    pos = 0
    hpnl = hcost = 0.0
    n_hedges = 0
    prev_s = s0
    worst = 0.0
    final = None
    for i, ts in enumerate(tss):
        d = series[ts]
        s, rows = d["spot"], d["rows"]
        if s is None:
            continue
        if hedged:
            hpnl += pos * (s - prev_s) * _LOT * lots            # accrue on the old position
            allowed = True
            if hedge_times is not None:                          # scheduled checkpoints only
                day, hhmm = ts[:10], ts[11:16]
                fired = getattr(run_static_structure, "_fired", None)
                if fired is None or fired.get("run") != id(series):
                    fired = {"run": id(series), "done": set()}
                    run_static_structure._fired = fired
                allowed = False
                for c in hedge_times:
                    if hhmm >= c and (day, c) not in fired["done"]:
                        fired["done"].add((day, c))
                        allowed = True
            if allowed:
                target = pos
                if s > K + hedge_band:
                    target = 1
                elif s < K - hedge_band:
                    target = -1
                elif abs(s - K) < hedge_hyst:
                    target = 0
                if target != pos:
                    hcost += fut_leg * abs(target - pos)
                    n_hedges += 1
                    pos = target
        prev_s = s
        if ts == tss[-1] and ts[:10] == exp_day:
            debit = sum(sd * max(0.0, (s - k) if cp == "C" else (k - s)) for k, cp, sd in legs)
        else:
            debit = sum(sd * _px(rows, k, cp, s) for k, cp, sd in legs)
        pnl = (credit - debit) * _LOT * lots - opt_cost + hpnl - hcost
        worst = min(worst, pnl)
        final = pnl
    return {"structure": structure, "K": K, "kp": kp, "kc": kc,
            "credit_pts": round(credit, 1), "final_pnl": round(final, 0),
            "worst_mark": round(worst, 0), "n_hedges": n_hedges}


def run_sided_condor(db, exp_full, entry_ts, mode="walls", offset=200, wing=100,
                     lots=1, slippage_pts=1.0, buffer_pts=0.0,
                     cooldown_min=30, max_rolls=8, gate: str = "always"):
    """SIDED adjustment: when spot threatens ONE short strike, roll ONLY that spread
    (2 legs) out to a wider level — the untested spread is left alone to keep earning.
    Half the friction of the 4-leg re-center (₹~680 vs ₹~1360 per adjustment) and the
    winning side's credit is never torn up. gate='regime' additionally requires the
    trend-expansion detector to confirm before defending (a touch in chop is ignored)."""
    costs = CostModel(slippage_pts=slippage_pts)
    cost2 = costs.legs_cost_inr(2 * lots, _LOT)
    con = sqlite3.connect(db)
    exp_day = exp_full[:10]
    series = _full_chain_series(con, exp_full, entry_ts, exp_day + "T23:59:59Z")
    con.close()
    if not series:
        return {"error": f"no chain data from {entry_ts}"}
    tss = sorted(series)

    def _px(rows, k, cp, s):
        r = rows.get(k)
        v = (r[0] if cp == "C" else r[1]) if r else None
        if v is not None and v > 0:
            return v
        return max(0.0, (s - k) if cp == "C" else (k - s))

    def _open_side(ts, side):
        """Open one credit spread on `side` ('P'|'C') at the current wall/offset."""
        d = series[ts]
        s, rows = d["spot"], d["rows"]
        if side == "P":
            cands = [(k, v[3]) for k, v in rows.items() if k < s]
            if mode != "walls":
                cands = [(min(rows, key=lambda k: abs(k - (s - offset))), 0)]
            if not cands:
                return None
            ks = max(cands, key=lambda x: x[1])[0] if mode == "walls" else cands[0][0]
            kl = ks - wing
            credit = _px(rows, ks, "P", s) - _px(rows, kl, "P", s)
        else:
            cands = [(k, v[2]) for k, v in rows.items() if k > s]
            if mode != "walls":
                cands = [(min(rows, key=lambda k: abs(k - (s + offset))), 0)]
            if not cands:
                return None
            ks = max(cands, key=lambda x: x[1])[0] if mode == "walls" else cands[0][0]
            kl = ks + wing
            credit = _px(rows, ks, "C", s) - _px(rows, kl, "C", s)
        if credit <= 0:
            return None
        return {"ks": ks, "kl": kl, "credit": credit}

    def _side_debit(side, pos_s, rows, s):
        cp = "P" if side == "P" else "C"
        return _px(rows, pos_s["ks"], cp, s) - _px(rows, pos_s["kl"], cp, s)

    t0 = tss[0]
    sides = {"P": _open_side(t0, "P"), "C": _open_side(t0, "C")}
    if not sides["P"] or not sides["C"]:
        return {"error": "could not open condor at entry"}
    realized = -2 * cost2                      # 4 legs opened
    rolls, worst, last_roll_i = [], 0.0, {"P": 0, "C": 0}
    for i in range(1, len(tss)):
        ts = tss[i]
        d = series[ts]
        s, rows = d["spot"], d["rows"]
        unreal = sum((sides[x]["credit"] - _side_debit(x, sides[x], rows, s)) * _LOT * lots
                     for x in ("P", "C") if sides[x])
        worst = min(worst, realized + unreal - 2 * cost2)
        if ts == tss[-1]:                       # settle (intrinsic on expiry day)
            for x in ("P", "C"):
                if not sides[x]:
                    continue
                if ts[:10] == exp_day:
                    cp = x
                    intr_s = max(0.0, (s - sides[x]["ks"]) if cp == "C" else (sides[x]["ks"] - s))
                    intr_l = max(0.0, (s - sides[x]["kl"]) if cp == "C" else (sides[x]["kl"] - s))
                    debit = intr_s - intr_l
                else:
                    debit = _side_debit(x, sides[x], rows, s)
                realized += (sides[x]["credit"] - debit) * _LOT * lots - cost2
            break
        for x in ("P", "C"):
            if not sides[x]:
                continue
            touched = (s <= sides[x]["ks"] + buffer_pts) if x == "P" else \
                      (s >= sides[x]["ks"] - buffer_pts)
            if not touched or len(rolls) >= max_rolls or (i - last_roll_i[x]) < cooldown_min:
                continue
            if gate == "regime":
                trending, _e, _sc = _trend_expansion(series, tss, i)
                if not trending:
                    continue
            debit = _side_debit(x, sides[x], rows, s)
            realized += (sides[x]["credit"] - debit) * _LOT * lots - cost2   # close old side
            new = _open_side(ts, x)
            if new is None:
                sides[x] = None
                continue
            realized -= cost2                                               # open new side
            rolls.append({"ts": ts[:16], "side": x, "spot": round(s, 0),
                          "from": sides[x]["ks"], "to": new["ks"]})
            sides[x], last_roll_i[x] = new, i
    return {"entry_ts": t0, "final_pnl": round(realized, 0), "n_rolls": len(rolls),
            "rolls": rolls, "worst_mark": round(worst, 0)}


def main():
    ap = argparse.ArgumentParser(description="Iron-condor expiry-week study.")
    ap.add_argument("--db", default="option_chains_full.db")
    ap.add_argument("--expiry", default="2026-07-28")
    ap.add_argument("--entry-date", default="2026-07-21")
    ap.add_argument("--entry-time", default="04:30", help="UTC HH:MM (04:30 = 10:00 IST)")
    ap.add_argument("--mode", default="walls", choices=["walls", "offset"])
    ap.add_argument("--offset", type=float, default=200)
    ap.add_argument("--wing", type=float, default=100)
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--slippage-pts", type=float, default=1.0, help="bid-ask crossing cost per "
                    "leg per side, index points (0 = the old fictional frictionless fills)")
    ap.add_argument("--sweep", action="store_true", help="also sweep entry days + modes")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    exp_full = con.execute("SELECT DISTINCT expiry FROM chain_rows WHERE expiry LIKE ?",
                           (args.expiry + "%",)).fetchone()
    con.close()
    if not exp_full:
        print(f"ERROR: no expiry matching {args.expiry} in {args.db}")
        return
    exp_full = exp_full[0]

    def show(tag, r):
        if r.get("error"):
            print(f"{tag}: ERROR {r['error']}")
            return
        print(f"\n### {tag}")
        print(f"entry {r['entry_ts']}  spot {r['spot']}  |  SP {r['short_put']:.0f} / SC {r['short_call']:.0f} "
              f"wing {r['wing']:.0f}  |  credit {r['credit_pts']}pts (₹{r['credit_inr']:.0f})  "
              f"max-loss {r['max_loss_pts']}pts  costs ₹{r['costs_inr']:.0f}")
        print(f"spot range to expiry: {r['spot_range'][0]:.0f}–{r['spot_range'][1]:.0f}  "
              f"| P&L path: worst ₹{r['worst']:.0f}  best ₹{r['best']:.0f}  "
              f"({r['n_marks']} marks, {r['n_approx']} intrinsic-approx)")
        print("daily close P&L: " + "  ".join(f"{d[5:]}: ₹{v:.0f}" for d, v in r["daily_close"].items()))
        print("daily WORST P&L: " + "  ".join(f"{d[5:]}: ₹{v:.0f}" for d, v in r["daily_min"].items()))
        for k, (p, ts, why) in r["variants"].items():
            print(f"  {k:<10} → ₹{p:>7.0f}   ({why} @ {ts})")

    base = run_condor(args.db, exp_full, f"{args.entry_date}T{args.entry_time}:00Z",
                      mode=args.mode, offset=args.offset, wing=args.wing, lots=args.lots,
                      slippage_pts=args.slippage_pts)
    show(f"{args.mode} entry {args.entry_date}", base)

    if args.sweep:
        for d in ("2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27"):
            r = run_condor(args.db, exp_full, f"{d}T{args.entry_time}:00Z",
                           mode=args.mode, offset=args.offset, wing=args.wing, lots=args.lots,
                           slippage_pts=args.slippage_pts)
            show(f"{args.mode} entry {d}", r)

    print("\nHonest notes: ONE expiry = ONE sample — mechanics and levers, not a validated edge.")
    print("Marks are LTP-based; real fills cross 4 option spreads, so live is worse than shown.")


if __name__ == "__main__":
    main()
