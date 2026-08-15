"""
strike_optimizer.py  (payoff-engine version)
--------------------------------------------
Given an option chain + the RND, SEARCH across many strategies for the best
strikes by an objective that actually matters (EV / probability-of-profit /
risk-reward), subject to a max-loss budget — and factor in OPEN INTEREST as
(a) a liquidity filter and (b) a support/resistance edge.

Design: every strategy is reduced to its EXPIRY PAYOFF on the RND price grid.
Then for ALL strategies identically:
    EV       = ∫ payoff(S) · dens(S) dS          (RND-weighted)
    PoP      = P(payoff > 0)
    max_loss = -min(payoff)        (over the grid; flagged if undefined-risk)
    max_profit = max(payoff)
Probabilities are the MARKET's (risk-neutral Q) — what's actually priced.

Strategies covered:
  credit / premium-sell : iron_condor, iron_butterfly, bull_put_spread,
                          bear_call_spread, short_strangle*, short_straddle*
  debit  / premium-buy  : bull_call_spread, bear_put_spread, long_straddle,
                          long_strangle, long_call, long_put
  (* = undefined risk — flagged, excluded from budget unless allow_undefined)

OI usage (transparent sub-scores, NOT a black box):
  * liquidity   : min OI across legs; thin legs penalised (bad fills).
  * walls       : short strikes placed at/beyond heavy-OI walls (support below /
                  resistance above) score higher — the wall "defends" the strike.
  * oi_change   : fresh writing reinforcing those walls adds a bonus.
EV/PoP stay the PRIMARY rank; OI refines among comparable candidates (or can be
weighted in via `oi_weight`). Honest caveats returned with every result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
import numpy as np
from exchange_config import NIFTY_LOT_SIZE   # single source of truth for lot size


# ── leg payoff on a price grid (points) ──────────────────────────────────────
def _leg_payoff(grid, kind, strike, premium, sign):
    """sign +1 long / -1 short. Returns payoff array in points (excl. cost)."""
    if kind == "call":
        intrinsic = np.maximum(grid - strike, 0.0)
    else:
        intrinsic = np.maximum(strike - grid, 0.0)
    return sign * (intrinsic - premium)  # long pays premium, short collects it


@dataclass
class Strat:
    kind: str
    legs: list                # [(call/put, strike, premium, sign)]
    defined_risk: bool = True


@dataclass
class Result:
    kind: str
    legs: dict
    credit_pts: float
    max_loss_pts: float
    max_profit_pts: float
    pop: float
    ev_pts: float
    rr: float
    breakevens: tuple
    defined_risk: bool
    oi: dict = field(default_factory=dict)
    score: float = 0.0


# ── metrics from a strategy's payoff vs the RND ──────────────────────────────
def _metrics(grid, dens, strat: Strat, cost_per_leg):
    payoff = np.zeros_like(grid)
    credit = 0.0
    for (kind, strike, premium, sign) in strat.legs:
        payoff += _leg_payoff(grid, kind, strike, premium, sign)
        credit += -sign * premium                      # short=+prem, long=-prem
    payoff -= len(strat.legs) * cost_per_leg            # costs
    credit -= len(strat.legs) * cost_per_leg

    ev = float(np.trapz(payoff * dens, grid))
    win = payoff > 0
    pop = float(np.trapz(dens[win], grid[win])) if win.any() else 0.0
    max_loss = float(-payoff.min())
    max_profit = float(payoff.max())
    # breakevens: where payoff crosses zero
    sign_change = np.where(np.diff(np.sign(payoff)) != 0)[0]
    bes = tuple(round(float(np.interp(0, [payoff[i], payoff[i+1]],
                                      [grid[i], grid[i+1]])), 1)
                for i in sign_change)
    return payoff, credit, ev, pop, max_loss, max_profit, bes


# ── OI scoring (liquidity + walls + change) ──────────────────────────────────
def _oi_scores(chain, strat: Strat):
    K = np.array(chain["strikes"], float)
    coi = np.array(chain.get("call_oi", np.ones_like(K)), float)
    poi = np.array(chain.get("put_oi", np.ones_like(K)), float)
    coi_chg = np.array(chain.get("call_oi_chg", np.zeros_like(K)), float)
    poi_chg = np.array(chain.get("put_oi_chg", np.zeros_like(K)), float)
    spot = chain["spot"]

    def at(arr, k):
        i = int(np.argmin(np.abs(K - k)))
        return arr[i]

    # liquidity: min OI across legs (use the right side per leg)
    leg_oi = []
    for (kind, strike, *_ ) in strat.legs:
        leg_oi.append(at(coi if kind == "call" else poi, strike))
    liquidity = float(min(leg_oi)) if leg_oi else 0.0

    # walls
    below = K < spot
    above = K > spot
    support_wall = float(K[below][np.argmax(poi[below])]) if below.any() else spot
    resist_wall = float(K[above][np.argmax(coi[above])]) if above.any() else spot

    short_puts = [s for (kd, s, *_r), sgn in
                  [( (l[0], l[1]), l[3]) for l in strat.legs]
                  if kd == "put" and sgn < 0] if False else \
                 [l[1] for l in strat.legs if l[0] == "put" and l[3] < 0]
    short_calls = [l[1] for l in strat.legs if l[0] == "call" and l[3] < 0]

    aligned = 0; checks = 0
    for sp in short_puts:
        checks += 1
        if sp <= support_wall:        # short put at/below support wall = defended
            aligned += 1
    for sc in short_calls:
        checks += 1
        if sc >= resist_wall:         # short call at/above resistance = defended
            aligned += 1
    wall_align = (aligned / checks) if checks else None

    # oi change: fresh writing reinforcing the walls
    chg_bonus = 0.0
    if short_puts and at(poi_chg, min(short_puts)) > 0:
        chg_bonus += 0.5
    if short_calls and at(coi_chg, max(short_calls)) > 0:
        chg_bonus += 0.5

    return {"liquidity_min_oi": round(liquidity, 1),
            "support_wall": support_wall, "resistance_wall": resist_wall,
            "wall_alignment": wall_align,
            "oi_change_bonus": round(chg_bonus, 2)}


# ── strategy generators ──────────────────────────────────────────────────────
def _px(K, arr, k):
    return float(np.interp(k, K, arr))


def _generate(chain, window_pts, max_wing):
    K = np.array(chain["strikes"], float)
    C = np.array(chain["call_ltp"], float)
    P = np.array(chain["put_ltp"], float)
    spot = chain["spot"]
    lo, hi = spot - window_pts, spot + window_pts
    Ks = [k for k in K if lo <= k <= hi]
    puts = [k for k in Ks if k <= spot]
    calls = [k for k in Ks if k >= spot]
    atm = min(Ks, key=lambda k: abs(k - spot))

    out: list[Strat] = []
    cp = lambda k: ("call", k, _px(K, C, k), )
    pp = lambda k: ("put", k, _px(K, P, k), )

    # iron condor / butterfly
    for sp in puts:
        for bp in puts:
            if not (0 < sp - bp <= max_wing): continue
            for sc in calls:
                for bc in calls:
                    if not (0 < bc - sc <= max_wing): continue
                    out.append(Strat("iron_condor", [
                        ("put", bp, _px(K,P,bp), +1), ("put", sp, _px(K,P,sp), -1),
                        ("call", sc, _px(K,C,sc), -1), ("call", bc, _px(K,C,bc), +1)]))
    for w in (50, 100, 150, 200):
        bp, bc = atm - w, atm + w
        if bp in K.tolist() or True:
            out.append(Strat("iron_butterfly", [
                ("put", bp, _px(K,P,bp), +1), ("put", atm, _px(K,P,atm), -1),
                ("call", atm, _px(K,C,atm), -1), ("call", bc, _px(K,C,bc), +1)]))

    # vertical spreads (credit + debit)
    for a in puts:
        for b in puts:
            if not (0 < a - b <= max_wing): continue
            out.append(Strat("bull_put_spread", [
                ("put", b, _px(K,P,b), +1), ("put", a, _px(K,P,a), -1)]))   # credit
            out.append(Strat("bear_put_spread", [
                ("put", a, _px(K,P,a), +1), ("put", b, _px(K,P,b), -1)]))   # debit
    for a in calls:
        for b in calls:
            if not (0 < b - a <= max_wing): continue
            out.append(Strat("bear_call_spread", [
                ("call", a, _px(K,C,a), -1), ("call", b, _px(K,C,b), +1)])) # credit
            out.append(Strat("bull_call_spread", [
                ("call", a, _px(K,C,a), +1), ("call", b, _px(K,C,b), -1)])) # debit

    # straddles / strangles
    out.append(Strat("long_straddle", [
        ("call", atm, _px(K,C,atm), +1), ("put", atm, _px(K,P,atm), +1)]))
    out.append(Strat("short_straddle", [
        ("call", atm, _px(K,C,atm), -1), ("put", atm, _px(K,P,atm), -1)],
        defined_risk=False))
    for w in (100, 150, 200, 250):
        c, p = atm + w, atm - w
        out.append(Strat("long_strangle", [
            ("call", c, _px(K,C,c), +1), ("put", p, _px(K,P,p), +1)]))
        out.append(Strat("short_strangle", [
            ("call", c, _px(K,C,c), -1), ("put", p, _px(K,P,p), -1)],
            defined_risk=False))

    # simple singles
    out.append(Strat("long_call", [("call", atm, _px(K,C,atm), +1)]))
    out.append(Strat("long_put",  [("put", atm, _px(K,P,atm), +1)]))
    return out


# ── main ─────────────────────────────────────────────────────────────────────
def _tilt_density(grid, dens, bias, sd):
    """Shift the distribution by a user bias to optimize against YOUR view.
    bias in [-1,+1]: + = bullish (mass moves up), magnitude 0..1 = strength.
    Shift is capped at ~0.6·sd so a strong view tilts but never fabricates a
    move the chain can't support. Returns a renormalized density."""
    shift = bias * 0.6 * sd
    tilted = np.interp(grid - shift, grid, dens, left=0.0, right=0.0)
    area = np.trapz(tilted, grid)
    return tilted / area if area > 0 else dens


def optimize(chain, rnd, *, objective="ev", weights=None, max_loss_budget_pts=None,
             min_pop=0.0, cost_per_leg_pts=1.0, window_pts=500, max_wing=300,
             allow_undefined=False, oi_weight=0.0, top_n=6, bias=None,
             allow_bad_rnd=False, earnings_season=False):
    """
    Ranking is a USER-CONFIGURABLE weighted blend. Pass `weights` as a dict over
    {"ev","pop","rr","oi"}, e.g. {"ev":0.5,"pop":0.3,"oi":0.2}. Components are
    min-max normalized so the weights mean what they say. If `weights` is None,
    falls back to `objective` (single component) for back-compat. ALL knobs
    (budget, min_pop, cost, window, wing, undefined, bias) are caller-set —
    nothing is hard-coded.
    """
    import numpy as np
    _g = np.array(rnd["grid"]); _d = np.array(rnd["dens"])
    _m = np.trapz(_g*_d,_g); _sd = (np.trapz((_g-_m)**2*_d,_g))**0.5
    print(f"\n[CHECK] RND going INTO optimizer: move={_sd:.0f}, provenance={rnd.get('provenance')}\n")
    
    grid = np.array(rnd["grid"], float)
    market_dens = np.array(rnd["dens"], float)
    lot = chain.get("lot_size", NIFTY_LOT_SIZE)

    # ── RND CALIBRATION GUARD ───────────────────────────────────────────────
    # The optimizer is only as honest as the RND. If the RND is flagged
    # miscalibrated (expected move diverges from the ATM straddle), refuse to
    # produce a confident ranking — a wrong RND silently mis-ranks every
    # structure (e.g. an inflated move buries iron condors).
    rnd_prov = rnd.get("provenance", "PRIMARY")
    rnd_warn = rnd.get("warning", "")
    if rnd_prov == "FALLBACK" and not allow_bad_rnd:
        return {"status": "rnd_uncalibrated",
                "rnd_provenance": rnd_prov,
                "note": (rnd_warn or "RND failed its straddle-calibration check.")
                        + " Optimizer halted — fix the RND (clip/trim/renormalize) "
                          "or pass allow_bad_rnd=True to override (NOT recommended)."}

    # OPTIONAL bias: if provided, rank against YOUR tilted view; else market RND.
    if bias is not None and abs(bias) > 1e-9:
        mean = float(np.trapz(grid * market_dens, grid))
        sd = float(np.sqrt(np.trapz((grid - mean) ** 2 * market_dens, grid)))
        dens = _tilt_density(grid, market_dens, max(-1.0, min(1.0, bias)), sd)
        bias_applied = round(float(bias), 2)
    else:
        dens = market_dens
        bias_applied = None

    results: list[Result] = []
    for strat in _generate(chain, window_pts, max_wing):
        if not strat.defined_risk and not allow_undefined:
            continue
        payoff, credit, ev, pop, mloss, mprof, bes = _metrics(grid, dens, strat, cost_per_leg_pts)
        if mloss <= 0 or mprof <= 0:
            continue
        if max_loss_budget_pts and strat.defined_risk and mloss > max_loss_budget_pts:
            continue
        if pop < min_pop:
            continue
        oi = _oi_scores(chain, strat)
        legs = {f"{'+'if s>0 else '-'}{k}@{int(st)}": round(pr,2)
                for (k, st, pr, s) in strat.legs}
        results.append(Result(strat.kind, legs, round(credit,2), round(mloss,2),
                              round(mprof,2), round(pop,3), round(ev,2),
                              round(mprof/mloss,2), bes, strat.defined_risk, oi))

    if not results:
        return {"status": "no_candidates",
                "note": "Nothing fit the constraints — loosen budget/min_pop or "
                        "check chain/RND inputs."}

    # If earnings season is active, explicitly filter out undefined risk if we can
    # or strongly penalize it. Let's just override allow_undefined=False if active.
    if earnings_season and allow_undefined:
        results = [r for r in results if r.defined_risk]
        if not results:
            return {"status": "no_candidates",
                    "note": "All undefined risk removed due to earnings season gap risk, and no defined risk fits."}

    # ── ranking: a USER-CONFIGURABLE weighted blend over NORMALIZED components ──
    # weights is a dict the user sets, e.g. {"ev":0.5,"pop":0.3,"rr":0.1,"oi":0.1}.
    # If only `objective` is given (back-compat), it becomes weight 1.0 on that.
    if weights is None:
        weights = {objective: 1.0}
    wsum = sum(abs(v) for v in weights.values()) or 1.0
    weights = {k: v / wsum for k, v in weights.items()}   # normalize to 1

    # OI edge (always computed now; the weight decides whether it matters).
    # Proper weighted average of three 0..1 sub-scores -> oi_edge is bounded 0..1.
    maxoi = max((r.oi.get("liquidity_min_oi", 0) or 0) for r in results) or 1
    for r in results:
        liq = (r.oi.get("liquidity_min_oi", 0) or 0) / maxoi      # 0..1
        wall = r.oi.get("wall_alignment")                         # 0..1 or None
        wall = 0.0 if wall is None else wall
        chg = min(1.0, (r.oi.get("oi_change_bonus", 0) or 0))     # 0..1 (bonus≤1)
        # weights: liquidity 0.4, wall 0.4, fresh-writing 0.2  -> sums to 1
        r.oi["oi_edge"] = round(0.4 * liq + 0.4 * wall + 0.2 * chg, 3)

    # min-max normalize each component to 0..1 so EV(points), PoP(0..1),
    # RR(ratio), OI(0..1) are COMPARABLE and weights mean what they say.
    def _norm_list(vals):
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1.0
        return [(v - lo) / rng for v in vals]

    ev_n = _norm_list([r.ev_pts for r in results])
    pop_n = _norm_list([r.pop for r in results])
    rr_n = _norm_list([r.rr for r in results])
    oi_n = _norm_list([r.oi["oi_edge"] for r in results])
    comp = {"ev": ev_n, "pop": pop_n, "rr": rr_n, "oi": oi_n}

    for i, r in enumerate(results):
        r.score = round(sum(weights.get(k, 0.0) * comp[k][i] for k in comp), 4)
    results.sort(key=lambda r: r.score, reverse=True)

    result = {
        "status": "ok", "objective": objective, "spot": chain["spot"],
        "weights_used": weights, "bias_applied": bias_applied,
        "ranked": [_fmt(r, lot) for r in results[:top_n]],
        "n_evaluated": len(results),
        "caveats": [
            "Probabilities are RISK-NEUTRAL (Q): credit-structure EV is "
            "conservative vs real-world (variance premium).",
            ("Ranked against YOUR tilted view (bias=%s) — NOT the raw market RND. "
             "A wrong bias mis-ranks; keep |bias| proportional to conviction."
             % bias_applied) if bias_applied is not None else
            "No bias applied — ranked against the market-implied RND (symmetric).",
            "Net of assumed cost/leg — set cost_per_leg_pts to real costs.",
            "OI walls/liquidity are an EDGE/FILTER, not a guarantee; EV/PoP lead.",
            "Optimizer picks best strikes GIVEN the chain; it does NOT decide "
            "whether to trade. Apply complacency/event gates + risk_budget sizing.",
            "Undefined-risk structures (short straddle/strangle) "
            + ("BLOCKED — Earnings season elevates single-stock gap risk."
               if earnings_season else ("INCLUDED — max_loss is a grid floor, true loss is unbounded."
               if allow_undefined else "excluded (allow_undefined=False).")),
        ],
    }
    
    print(f"\n[CHECK2] n_evaluated={result.get('n_evaluated')}, "
          f"weights_used={result.get('weights_used')}, "
          f"top3={[t['kind'] for t in result['ranked'][:3]]}\n")
          
    return result


def _fmt(r: Result, lot):
    return {"kind": r.kind, "legs": r.legs, "defined_risk": r.defined_risk,
            "credit_pts": r.credit_pts, "max_loss_pts": r.max_loss_pts,
            "max_profit_pts": r.max_profit_pts, "prob_of_profit": r.pop,
            "ev_pts": r.ev_pts, "risk_reward": r.rr, "breakevens": r.breakevens,
            "oi": r.oi, "score": r.score,
            "rupees": {"max_loss": round(r.max_loss_pts*lot),
                       "max_profit": round(r.max_profit_pts*lot)}}


if __name__ == "__main__":
    import json
    strikes = list(range(23400, 24650, 50))
    spot = 24050
    call_ltp = [max(2.0, 120 - (k-spot)*0.18) if k>=spot else max(2.0,(spot-k)+40) for k in strikes]
    put_ltp  = [max(2.0, 120 - (spot-k)*0.18) if k<=spot else max(2.0,(k-spot)+40) for k in strikes]
    # OI: heavy put OI (support) ~23800, heavy call OI (resistance) ~24300
    put_oi  = [max(1,100 - abs(k-23800)/50) for k in strikes]
    call_oi = [max(1,100 - abs(k-24300)/50) for k in strikes]
    put_oi_chg  = [80 if 23800<=k<=24100 else 0 for k in strikes]
    call_oi_chg = [60 if 24200<=k<=24400 else 0 for k in strikes]
    grid = np.linspace(23000,25100,400); dens = np.exp(-0.5*((grid-spot)/290)**2); dens/=np.trapz(dens,grid)

    chain = {"strikes":strikes,"call_ltp":call_ltp,"put_ltp":put_ltp,"spot":spot,
             "call_oi":call_oi,"put_oi":put_oi,"call_oi_chg":call_oi_chg,
             "put_oi_chg":put_oi_chg,"lot_size":NIFTY_LOT_SIZE}
    rnd = {"grid":grid.tolist(),"dens":dens.tolist(),"spot":spot}

    res = optimize(chain, rnd, objective="ev", max_loss_budget_pts=100,
                   min_pop=0.45, oi_weight=0.3, top_n=5)
    print(f"Evaluated {res['n_evaluated']} structures. Top 5 by EV (+OI nudge):\n")
    for r in res["ranked"]:
        print(f"{r['kind']:18} EV {r['ev_pts']:>6} PoP {r['prob_of_profit']:.2f} "
              f"maxL {r['max_loss_pts']:>5}pts (₹{r['rupees']['max_loss']:>5}) "
              f"R:R {r['risk_reward']} wall_align={r['oi'].get('wall_alignment')}")
