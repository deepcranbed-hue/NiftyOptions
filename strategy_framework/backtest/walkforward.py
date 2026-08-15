"""
strategy_framework/backtest/walkforward.py
==========================================
Event-driven walk-forward backtest with rupee-accurate costs and (optionally)
active position management.

Principles:
  * No lookahead — every decision uses only data with ts <= now (as-of reads).
  * Walk forward, fixed priors — signals/weights are not fit to the scored window.
  * Leg-level, rupee-accurate P&L — every option leg is priced from the snapshot;
    a ₹/leg transaction cost (default ₹20) is charged when a leg is opened,
    closed, or touched by an adjustment.

Exit modes:
  * "horizon" : open, mark-to-market `hold` snapshots later, close.
  * "expiry"  : open, hold to expiry, settle at intrinsic.
  * "manage"  : open, then at each later snapshot re-classify the regime and let
                the ADJUSTMENT ENGINE morph the position (roll / tilt / convert /
                recenter / close) instead of flat-closing. Runs to horizon or
                expiry; every touched leg is charged the transaction cost.

With ~5 sessions of data these are plumbing tests, not edge estimates —
metrics.summarize() says so explicitly.
"""
from __future__ import annotations
from dataclasses import dataclass

from ..signals.data_access import DataAccess, BarCache, days_to_expiry, CROSS_ASSET_SYMBOLS
from ..signals import bundle as signal_bundle
from ..strategy import suggester, regime as regime_mod, adjustment, constructor
from ..strategy import action_eval as _action_eval
from ..strategy import futures_action_eval as _fut_eval
from . import metrics


def _session(ts: str) -> str:
    return ts[:10] if ts else "?"


def _mins_between(a: str, b: str):
    """Minutes between two UTC 'Z' timestamps (None if either missing)."""
    if not a or not b:
        return None
    from datetime import datetime
    try:
        da_ = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db_ = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return abs((da_ - db_).total_seconds()) / 60.0
    except Exception:
        return None


def _price(chain, side, strike):
    if side == "future":                 # a future marks at spot (linear, no premium)
        return float(chain.spot)
    book = chain.call_ltp if side == "call" else chain.put_ltp
    return float(book.get(strike, 0.0))


def _intrinsic(side, strike, spot):
    if side == "future":
        return float(spot)
    return max(spot - strike, 0) if side == "call" else max(strike - spot, 0)


class _Position:
    """Leg-level open position. Each leg: [side, strike, sign, entry_price]."""
    def __init__(self, legs, entry_chain, family, cfg, max_loss_pts=0.0):
        self.cfg = cfg
        self.family = family
        self.legs = []
        entry_net = 0.0
        for side, k, sign in legs:
            e = _price(entry_chain, side, k)
            self.legs.append([side, k, sign, e])
            entry_net += sign * e                    # + = net debit paid, − = credit
        n = len(self.legs)
        self.realized_pts = 0.0                      # from closed legs (adjustments)
        self.cost_inr = cfg.costs.legs_cost_inr(n, cfg.lot_size)   # entry
        self.n_adjust = 0
        self.last_adjust_ts = None                   # for the cooldown gate
        self.breach_streak = 0                       # for the persistence filter
        self.adjust_log = []
        self.entry_credit_pts = max(-entry_net, 0.0)  # points collected (credit)
        self.entry_max_loss_pts = max_loss_pts
        # harvest-debt state: how far the wings have been pulled IN from entry, and
        # how many times — the path-dependent variable a one-step optimiser can't see.
        self.orig_put_short = max([k for s, k, g in legs if s == "put" and g < 0], default=None)
        self.orig_call_short = min([k for s, k, g in legs if s == "call" and g < 0], default=None)
        self.n_harvests = 0

    def harvest_debt_pts(self) -> float:
        """Total points the short wings have been rolled INWARD from their entry
        strikes (protection sold away). 0 = wings still at/beyond entry width."""
        debt = 0.0
        cur_put = max([l[1] for l in self.legs if l[0] == "put" and l[2] < 0], default=None)
        cur_call = min([l[1] for l in self.legs if l[0] == "call" and l[2] < 0], default=None)
        if self.orig_put_short and cur_put:
            debt += max(0.0, cur_put - self.orig_put_short)      # put rolled up = inward
        if self.orig_call_short and cur_call:
            debt += max(0.0, self.orig_call_short - cur_call)    # call rolled down = inward
        return debt

    def mark_net_rupees(self, chain):
        """Current mark-to-market net P&L (₹), incl. realised + open legs − costs."""
        m = self.realized_pts
        for side, k, sign, entry in self.legs:
            px = _price(chain, side, k)
            if px <= 0:
                px = _intrinsic(side, k, chain.spot)
            m += sign * (px - entry)
        return m * self.cfg.lot_size - self.cost_inr

    def profit_target_rupees(self, frac):
        """Book-profit level: `frac` of the max credit received (the condor's max
        profit). Returns a huge number for debit structures (no natural credit
        target) so take-profit simply never fires on them."""
        if self.entry_credit_pts > 0:
            return frac * self.entry_credit_pts * self.cfg.lot_size
        return 1e12

    def stop_threshold_rupees(self, mult):
        """Loss level at which to bail. Use `mult`× credit received, but never more
        than ~60% of the defined max loss (so the stop always fires before you hit
        full max loss). Falls back to whichever is known."""
        lot = self.cfg.lot_size
        cands = []
        if self.entry_credit_pts > 0:
            cands.append(mult * self.entry_credit_pts * lot)
        if self.entry_max_loss_pts > 0:
            cands.append(0.6 * self.entry_max_loss_pts * lot)
        return min(cands) if cands else 1e12          # unknown risk -> effectively no stop

    # -- apply an adjustment plan at a later snapshot -----------------------
    def apply(self, plan, chain):
        # verify every opened leg is priceable; else skip (treat as HOLD).
        for side, k, sign in plan.open_legs:
            if _price(chain, side, k) <= 0:
                return False
        # close legs -> realise P&L at current price
        for side, k, sign in plan.close_legs:
            for leg in list(self.legs):
                if leg[0] == side and leg[1] == k and leg[2] == sign:
                    px = _price(chain, side, k)
                    self.realized_pts += leg[2] * (px - leg[3])
                    self.legs.remove(leg)
                    break
        # open new legs at current price
        for side, k, sign in plan.open_legs:
            self.legs.append([side, k, sign, _price(chain, side, k)])
        touched = plan.touched
        self.cost_inr += self.cfg.costs.legs_cost_inr(touched, self.cfg.lot_size)
        self.n_adjust += 1
        if getattr(plan, "action", "") == "HARVEST_WING":
            self.n_harvests += 1
        if plan.new_family:
            self.family = plan.new_family
        # concrete orders, in trader terms: closing a long = SELL, closing a
        # short = BUY (to close); opening a +1 = BUY, a -1 = SELL.
        orders = []
        for side, k, sign in plan.close_legs:
            orders.append(f"{'Buy' if sign < 0 else 'Sell'} to close {side} {int(k)}")
        for side, k, sign in plan.open_legs:
            orders.append(f"{'Buy' if sign > 0 else 'Sell'} {side} {int(k)}")
        self.adjust_log.append({"action": plan.action, "touched": touched,
                                "rationale": plan.rationale, "orders": orders,
                                "at": chain.ts})
        return True

    # -- close everything, realise at price or expiry intrinsic -------------
    def close(self, chain, at_expiry=False, settle_spot=None):
        for leg in self.legs:
            side, k, sign, entry = leg
            px = _intrinsic(side, k, settle_spot) if at_expiry else _price(chain, side, k)
            self.realized_pts += sign * (px - entry)
        n = len(self.legs)
        self.cost_inr += self.cfg.costs.legs_cost_inr(n, self.cfg.lot_size)
        self.legs = []

    def net_rupees(self):
        return self.realized_pts * self.cfg.lot_size - self.cost_inr


def simulate_one(cfg, expiry: str, entry_ts: str, family: str | None = None,
                 legs: list | None = None,
                 exit_mode: str = "manage", roll_directional: bool = False,
                 stop_loss: bool = False, stop_loss_rupees: float | None = None,
                 stop_loss_mult: float = 2.0, max_manage: int = 400,
                 cooldown_min: float | None = None, max_rolls: int | None = None,
                 persist_near: int | None = None,
                 harvest: bool = False, min_harvest_inr: float = 100.0,
                 take_profit: bool = False, take_profit_frac: float = 0.6,
                 proactive: bool = False, proactive_lambda: float = 0.5,
                 proactive_horizon_frac: float = 1.0,
                 proactive_min_edge: float = 5.0,
                 proactive_risk_drift: float = 1.0,
                 proactive_max_harvests: int | None = None,
                 proactive_max_harvest_debt: float | None = None,
                 proactive_min_wing_buffer: float | None = None,
                 proactive_min_width: float = 200.0,
                 harvest_gate: str = "off",
                 fut_max_lots: int = 2, fut_allow_reverse: bool = True,
                 regime_expiry: str | None = None) -> dict:
    """Open ONE structure at a chosen past `entry_ts` and simulate it forward to
    expiry: price it at that snapshot, then walk forward marking an equity curve,
    applying management (roll/defend/convert) and stop-loss if requested.

    `family` None → use the framework's own suggestion at `entry_ts`.
    Returns the entry, the per-snapshot equity series, adjustments, and the final
    trade record — the single-position analogue of the auto backtest.
    """
    da = DataAccess(cfg.db_path)
    # A FUTURE has its own expiry and no option chain — walk the NIFTY spot path
    # from entry to the chosen futures expiry, marking linearly. `expiry` here is
    # the FUTURES expiry / exit date, not an option expiry.
    if family in ("long_future", "short_future"):
        return _simulate_future(cfg, da, family, entry_ts, expiry,
                                stop_loss=stop_loss, stop_loss_rupees=stop_loss_rupees,
                                take_profit=take_profit, take_profit_frac=take_profit_frac,
                                max_manage=max_manage,
                                # the forecast optimizer (HOLD/EXIT/ADD/REDUCE/REVERSE)
                                # runs advisory-only when `proactive` is set.
                                advisory=proactive, adv_lam=proactive_lambda,
                                adv_max_lots=int(fut_max_lots), adv_allow_reverse=bool(fut_allow_reverse),
                                adv_risk_drift=proactive_risk_drift,
                                adv_horizon_frac=proactive_horizon_frac,
                                regime_expiry=regime_expiry)

    entry_chain = da.chain_as_of(entry_ts, expiry)
    if entry_chain is None:
        return {"error": f"no option-chain snapshot at/before {entry_ts} for this expiry"}
    entry_ts = entry_chain.ts                       # snap to the actual snapshot used

    used_family, note = family, ""
    if legs:                                          # explicit user-picked legs win
        used_family = family or "custom"
        st = constructor.from_legs(used_family, legs, entry_chain, cfg)
        note = "custom legs"
        if st is None:
            return {"error": "custom legs not priceable at that snapshot "
                    "(a chosen strike has no price then, or legs net to zero)"}
    else:
        if not family:
            sug = suggester.suggest(cfg, entry_ts, expiry)
            st0 = sug.structure
            if not st0:
                return {"error": "no tradeable suggestion at that time — pick a family or strikes",
                        "note": sug.note}
            used_family = st0["family"]; note = "family from framework suggestion"
        st = constructor.build(used_family, entry_chain, cfg,
                               dte_days=days_to_expiry(entry_ts, expiry))
        if st is None:
            return {"error": f"{used_family} not priceable at that snapshot (missing leg prices)"}
    pos = _Position([(s, k, g) for s, k, g in st.legs], entry_chain, used_family, cfg,
                    max_loss_pts=st.max_loss)

    caps = [c for c in da.list_captures(expiry=expiry) if c["captured_at"] >= entry_ts]
    series, decisions, exit_ts, exit_spot = [], [], entry_ts, entry_chain.spot
    _bc = _bar_cache_for(cfg, caps)             # 8× faster regime re-evaluation
    _cache: dict = {}
    def reg_at(ts):
        if ts not in _cache:
            _cache[ts] = _regime_at(cfg, ts, expiry, bar_cache=_bc)
        return _cache[ts]

    # Manage on a STRIDE that spans the whole window (not the first max_manage
    # snapshots), so a multi-day expiry is covered across every session — otherwise
    # management (and stop-loss / take-profit) would silently stop partway through
    # day 1 and the trade would ride unmanaged to settle.
    manage_stride = max(1, -(-len(caps) // max(1, max_manage)))   # ceil div
    for idx, c in enumerate(caps):
        ts = c["captured_at"]
        ch = da.chain_as_of(ts, expiry)
        if ch is None:
            continue
        mark = pos.mark_net_rupees(ch)
        if exit_mode == "manage" and ts != entry_ts and idx % manage_stride == 0:
            reg, pin, sig = reg_at(ts)
            SC, SP = _short_strikes(pos.legs)
            # A two-sided "short-strike band" only exists for a condor/strangle (a short
            # call AND a short put). Directional spreads / single-leg / long structures
            # have no band — describe them by their actual strikes instead of "n/a".
            has_band = SP is not None and SC is not None
            if has_band:
                in_range = SP < ch.spot < SC
                rng = f"[{int(SP)}, {int(SC)}]"
            else:
                in_range = None
                _ks = sorted({int(l[1]) for l in pos.legs})
                rng = "strikes " + "/".join(str(k) for k in _ks)
            pnl_word = "profit" if mark >= 0 else "loss"

            # ADVISORY: forecast-driven action evaluator runs ALONGSIDE the rules
            # (log-only — does not change execution). Lets the optimizer be compared
            # to the rule engine on real data before it's promoted to live.
            advisory = None
            if proactive:
                try:
                    advisory = _action_eval.evaluate_actions(
                        pos.family, [(l[0], l[1], l[2]) for l in pos.legs], ch, reg, cfg,
                        lam=proactive_lambda, horizon_frac=proactive_horizon_frac,
                        min_edge=proactive_min_edge, current_mark_inr=mark,
                        risk_drift_frac=proactive_risk_drift,
                        harvest_debt_pts=pos.harvest_debt_pts(), n_harvests=pos.n_harvests,
                        max_harvests=proactive_max_harvests,
                        max_harvest_debt=proactive_max_harvest_debt,
                        min_wing_buffer=proactive_min_wing_buffer,
                        min_width=proactive_min_width)
                except Exception as _e:
                    advisory = {"error": str(_e)}

            # -- take-profit: bank the gain once enough of the max credit is captured
            # (don't hold a winner all the way to expiry and risk giving it back) ----
            if take_profit:
                tgt = pos.profit_target_rupees(take_profit_frac)
                if mark >= tgt:
                    reason = (f"Mark P&L ₹{round(mark)} reached {round(take_profit_frac * 100)}% "
                              f"of the max credit (₹{round(tgt)}) → book the profit and close.")
                    pos.adjust_log.append({"action": "TAKE_PROFIT", "touched": 0,
                                           "rationale": reason, "orders": [], "at": ts, "signal": sig})
                    decisions.append({"ts": ts, "spot": round(ch.spot), "mark_pnl": round(mark),
                                      "in_range": in_range, "action": "TAKE_PROFIT",
                                      "reason": reason, "signal": sig, "advisory": advisory})
                    series.append({"ts": ts, "spot": ch.spot, "pnl_rupees": round(mark, 0)})
                    pos.close(ch); exit_ts, exit_spot = ts, ch.spot
                    break

            # -- stop-loss: cut when the running loss breaches the threshold ----
            if stop_loss:
                thr = stop_loss_rupees if stop_loss_rupees else pos.stop_threshold_rupees(stop_loss_mult)
                if mark <= -thr:
                    reason = (f"Mark P&L ₹{round(mark)} ({pnl_word}) breached the "
                              f"₹{round(thr)} stop → cut early to cap the loss.")
                    pos.adjust_log.append({"action": "STOP_LOSS", "touched": 0,
                                           "rationale": reason, "orders": [], "at": ts, "signal": sig})
                    decisions.append({"ts": ts, "spot": round(ch.spot), "mark_pnl": round(mark),
                                      "in_range": in_range, "action": "STOP_LOSS",
                                      "reason": reason, "signal": sig, "advisory": advisory})
                    series.append({"ts": ts, "spot": ch.spot, "pnl_rupees": round(mark, 0)})
                    pos.close(ch); exit_ts, exit_spot = ts, ch.spot
                    break

            plan = adjustment.evaluate(pos.family, [(l[0], l[1], l[2]) for l in pos.legs],
                                       ch, reg, cfg, pin_risk=pin, roll_directional=roll_directional,
                                       n_adjust=pos.n_adjust,
                                       mins_since_last=_mins_between(ts, pos.last_adjust_ts),
                                       breach_streak=pos.breach_streak,
                                       cooldown_min=cooldown_min, max_rolls=max_rolls,
                                       persist_near=persist_near,
                                       harvest=harvest, min_harvest_inr=min_harvest_inr,
                                       stop_active=stop_loss, credit_pts=pos.entry_credit_pts)
            pos.breach_streak = pos.breach_streak + 1 if plan.threatened else 0
            if plan.action in ("CLOSE", "EXIT"):
                reason = f"{plan.rationale}. Mark P&L ₹{round(mark)}."
                decisions.append({"ts": ts, "spot": round(ch.spot), "mark_pnl": round(mark),
                                  "in_range": in_range, "action": plan.action, "reason": reason, "signal": sig, "advisory": advisory})
                pos.adjust_log.append({"action": plan.action, "touched": 0, "rationale": reason,
                                       "orders": [], "at": ts, "signal": sig})
                series.append({"ts": ts, "spot": ch.spot, "pnl_rupees": round(mark, 0)})
                pos.close(ch); exit_ts, exit_spot = ts, ch.spot
                break
            elif plan.action != "HOLD":
                # HARVEST execution gate (strategy C/D): veto the rule's harvest when
                # the optimizer says it isn't worth it or the budget is spent.
                if plan.action == "HARVEST_WING" and harvest_gate != "off":
                    adv = advisory or _action_eval.evaluate_actions(
                        pos.family, [(l[0], l[1], l[2]) for l in pos.legs], ch, reg, cfg,
                        lam=proactive_lambda, risk_drift_frac=proactive_risk_drift,
                        harvest_debt_pts=pos.harvest_debt_pts(), n_harvests=pos.n_harvests,
                        max_harvests=proactive_max_harvests, max_harvest_debt=proactive_max_harvest_debt,
                        min_wing_buffer=proactive_min_wing_buffer, current_mark_inr=mark)
                    vetoed = False
                    if harvest_gate in ("budget", "both") and adv.get("harvest_state", {}).get("blocked"):
                        vetoed = True
                    if harvest_gate in ("optimizer", "both") and not vetoed:
                        hv = next((r for r in adv.get("table", []) if r["action"] == "HARVEST_WING"), None)
                        if hv is None or hv.get("score", -1) <= 0:
                            vetoed = True
                    if vetoed:
                        reason = (f"HARVEST vetoed by gate ({harvest_gate}) — optimizer/budget says "
                                  f"holding beats harvesting. Spot {round(ch.spot)}, mark ₹{round(mark)}.")
                        decisions.append({"ts": ts, "spot": round(ch.spot), "mark_pnl": round(mark),
                                          "in_range": in_range, "action": "HARVEST_VETO", "reason": reason,
                                          "signal": sig, "advisory": advisory})
                        series.append({"ts": ts, "spot": ch.spot, "pnl_rupees": round(mark, 0)})
                        exit_ts, exit_spot = ts, ch.spot
                        continue
                roll_cost = cfg.costs.legs_cost_inr(plan.touched, cfg.lot_size)
                orders = _order_lines(plan)
                roll = _roll_summary(plan)
                reason = (f"{plan.rationale} — spot {round(ch.spot)}, band {rng}; "
                          + (f"{roll}; " if roll else "")
                          + f"signals {sig['regime']} ({sig['net_score']:+}, conf {sig['confidence']}); "
                          f"cost ~₹{round(roll_cost)} ({plan.touched} legs × ₹{int(cfg.costs.per_leg_inr)}). "
                          f"Mark P&L ₹{round(mark)}.")
                pos.apply(plan, ch)
                pos.last_adjust_ts = ts
                pos.adjust_log[-1]["signal"] = sig
                pos.adjust_log[-1]["rationale"] = reason
                pos.adjust_log[-1]["orders"] = orders
                decisions.append({"ts": ts, "spot": round(ch.spot), "mark_pnl": round(mark),
                                  "in_range": in_range, "action": plan.action, "reason": reason,
                                  "orders": orders, "roll": roll, "signal": sig, "advisory": advisory})
            else:
                fam_txt = (pos.family or "position").replace("_", " ")
                if has_band:
                    reason = (f"Mark P&L ₹{round(mark)} ({pnl_word}). Spot {round(ch.spot)} is "
                              f"{'inside' if in_range else 'outside'} the short-strike band {rng} — "
                              + ("still in the position's profit zone at expiry, so HOLD (don't pay to adjust a winner)."
                                 if in_range else "watching; not yet threatening a short strike, so HOLD.")
                              + f" Signals {sig['regime']} ({sig['net_score']:+}).")
                else:
                    # directional spread / single-leg / long structure — no short band
                    reason = (f"Mark P&L ₹{round(mark)} ({pnl_word}). Spot {round(ch.spot)} vs the "
                              f"{fam_txt} {rng}. No adjustment rule triggered, so HOLD. "
                              f"Signals {sig['regime']} ({sig['net_score']:+}).")
                decisions.append({"ts": ts, "spot": round(ch.spot), "mark_pnl": round(mark),
                                  "in_range": in_range, "action": "HOLD", "reason": reason, "signal": sig, "advisory": advisory})
        series.append({"ts": ts, "spot": ch.spot, "pnl_rupees": round(pos.mark_net_rupees(ch), 0)})
        exit_ts, exit_spot = ts, ch.spot

    if pos.legs:                                     # no exit fired: close at last snapshot
        last_ts = caps[-1]["captured_at"]
        fin = da.chain_as_of(last_ts, expiry)
        pos.close(fin); exit_ts, exit_spot = last_ts, (fin.spot if fin else exit_spot)
        # Distinguish a real settlement from running out of data on a live expiry:
        # if the last snapshot is before the expiry date, this is NOT settled — it's
        # marked to the latest snapshot and the P&L is provisional (time value remains).
        settled = last_ts[:10] >= str(expiry)[:10]
        if settled:
            action = "SETTLE"
            reason = (f"Held to expiry close. Final P&L ₹{round(pos.net_rupees())} "
                      f"(net of ₹{round(pos.cost_inr)} costs).")
        else:
            action = "MARK"
            reason = (f"⚠ Expiry {str(expiry)[:10]} not yet reached — marked to the latest "
                      f"snapshot ({last_ts[:10]}); P&L ₹{round(pos.net_rupees())} is PROVISIONAL "
                      f"(≈{round(days_to_expiry(last_ts, expiry))}d and time value still remain), "
                      f"not a settled result.")
        series.append({"ts": exit_ts, "spot": exit_spot, "pnl_rupees": round(pos.net_rupees(), 0)})
        decisions.append({"ts": exit_ts, "spot": round(exit_spot), "mark_pnl": round(pos.net_rupees()),
                          "in_range": None, "action": action, "reason": reason, "signal": None})

    # rule-vs-optimizer agreement + a first VALIDATION of the disagreements:
    # when the optimizer said CLOSE but the rules HELD, would closing then (locking
    # the mark) have beaten the actually-realised final P&L? (The counterfactual is
    # exact for CLOSE — it's just mark-at-t vs the final result.)
    advisory_agreement = None
    if proactive:
        adv = [d for d in decisions if d.get("advisory") and not d["advisory"].get("error")]
        if adv:
            match = sum(1 for d in adv
                        if (d["advisory"]["best"] != "HOLD") == (d["action"] != "HOLD"))
            final_pnl = round(pos.net_rupees(), 0)
            close_dis = [d for d in adv
                         if d["advisory"]["best"] == "CLOSE" and d["action"] == "HOLD"
                         and d.get("mark_pnl") is not None]
            close_val = None
            if close_dis:
                # closing at t locks mark_pnl; holding produced final_pnl. "better" = mark > final.
                wins = sum(1 for d in close_dis if d["mark_pnl"] > final_pnl)
                avg_gain = round(sum(d["mark_pnl"] - final_pnl for d in close_dis) / len(close_dis), 0)
                close_val = {"n": len(close_dis), "close_better": wins,
                             "avg_close_minus_hold_inr": avg_gain, "final_pnl_inr": final_pnl}
            advisory_agreement = {"matches": match, "total": len(adv),
                                  "pct": round(match / len(adv) * 100),
                                  "close_vs_hold": close_val}

    # ---- equity curve / drawdown / summary stats (tabular analysis) ----------
    # Capital base = margin at risk (the condor's max loss); returns/drawdown are a
    # % of that. Peak/drawdown are computed over the mark-P&L path decision-by-decision.
    lot = cfg.lot_size
    capital = (st.max_loss * lot) if (st and st.max_loss) else 0.0
    final_pnl = round(pos.net_rupees(), 0)
    peak, max_dd = None, 0.0
    for d in decisions:
        mp = d.get("mark_pnl")
        if mp is None:
            continue
        peak = mp if peak is None else max(peak, mp)
        dd = peak - mp
        d["peak_inr"] = round(peak, 0)
        d["drawdown_inr"] = round(dd, 0)
        d["drawdown_pct"] = round(dd / capital * 100, 2) if capital else None
        if capital:
            d["return_pct"] = round(mp / capital * 100, 2)
        max_dd = max(max_dd, dd)
    # ---- overnight VEGA exposure at entry ------------------------------------
    # Net vega (₹ per 1 vol point) of the structure. A short condor is short vega,
    # so an overnight IV spike (e.g. a risk-off) hits the mark even if spot barely
    # moves — the 06-30 lesson. Solved from entry LTPs via BS.
    net_vega_inr = None
    try:
        from .. import bs as _bs
        dte0 = max(days_to_expiry(entry_ts, expiry), 1e-4)
        S0 = entry_chain.spot
        nv = 0.0
        for side, k, sign in st.legs:
            entry_px = float(st.premiums.get((side, k), 0.0))
            if entry_px <= 0:
                continue
            iv = _bs.implied_vol(entry_px, S0, k, dte0 / 365.0, call=(side == "call"))
            if iv and iv > 0:
                nv += sign * _bs.bs_vega_per_volpt(S0, k, dte0 / 365.0, iv)
        net_vega_inr = round(nv * lot, 0)      # ₹ per +1 vol point
    except Exception:
        net_vega_inr = None

    marks = [d["mark_pnl"] for d in decisions if d.get("mark_pnl") is not None]
    stats = {
        "capital_base_inr": round(capital, 0),
        "total_pnl_inr": final_pnl,
        "total_return_pct": round(final_pnl / capital * 100, 2) if capital else None,
        "peak_pnl_inr": round(max(marks), 0) if marks else 0,
        "trough_pnl_inr": round(min(marks), 0) if marks else 0,
        "max_drawdown_inr": round(max_dd, 0),
        "max_drawdown_pct": round(max_dd / capital * 100, 2) if capital else None,
        "n_decisions": len(decisions),
        "n_adjustments": pos.n_adjust,
        "n_harvests": pos.n_harvests,
        "n_vetoes": sum(1 for d in decisions if d.get("action") == "HARVEST_VETO"),
        "cost_inr": round(pos.cost_inr, 0),
        "won": final_pnl > 0,
        "net_vega_inr_per_volpt": net_vega_inr,   # ₹ per +1 vol point (short condor = negative)
        "vega_3pt_inr": round(net_vega_inr * 3, 0) if net_vega_inr is not None else None,
        "vega_5pt_inr": round(net_vega_inr * 5, 0) if net_vega_inr is not None else None,
    }

    return {
        "expiry": expiry, "entry_ts": entry_ts, "exit_ts": exit_ts, "note": note,
        "advisory_agreement": advisory_agreement, "stats": stats,
        "entry_family": used_family, "final_family": pos.family,
        "entry_legs": [f"{'Buy' if s > 0 else 'Sell'} {sd} {int(k)}" for sd, k, s in st.legs],
        "entry_spot": entry_chain.spot, "exit_spot": exit_spot,
        "cost_inr": round(pos.cost_inr, 0), "n_adjustments": pos.n_adjust,
        "adjustments": pos.adjust_log, "pnl_rupees": final_pnl,
        "series": series, "decisions": decisions,
    }


def _order_lines(plan):
    """Human-readable order tickets for an adjustment: what was closed, what was
    opened (e.g. 'Buy to close call 24350 · Sell to open call 24300')."""
    out = []
    for side, k, sign in getattr(plan, "close_legs", []) or []:
        out.append(f"{'Buy' if sign < 0 else 'Sell'} to close {side} {int(k)}")
    for side, k, sign in getattr(plan, "open_legs", []) or []:
        out.append(f"{'Sell' if sign < 0 else 'Buy'} to open {side} {int(k)}")
    return out


def _roll_summary(plan):
    """One-line 'rolled short {side} {old} → {new}' when a short is closed and a new
    short of the same side is opened (the common roll/harvest/defend case)."""
    closes = [(s, int(k)) for s, k, g in (getattr(plan, "close_legs", []) or []) if g < 0]
    opens = [(s, int(k)) for s, k, g in (getattr(plan, "open_legs", []) or []) if g < 0]
    parts = []
    for side in ("put", "call"):
        oc = [k for s, k in closes if s == side]
        oo = [k for s, k in opens if s == side]
        if oc and oo:
            parts.append(f"rolled short {side} {oc[0]} → {oo[0]}")
        elif oo:
            parts.append(f"sold new short {side} {oo[0]}")
    return "; ".join(parts)


def _simulate_future(cfg, da, family, entry_ts, fut_expiry, stop_loss=False,
                     stop_loss_rupees=None, take_profit=False, take_profit_frac=0.6,
                     max_manage=400, advisory=False, adv_lam=0.5, adv_max_lots=2,
                     adv_allow_reverse=True, adv_risk_drift=1.0, adv_horizon_frac=1.0,
                     regime_expiry=None) -> dict:
    """Backtest a long/short NIFTY future (a linear NIFTY walk). With `advisory`,
    the forecast optimizer scores HOLD/EXIT/ADD/REDUCE/REVERSE at each bar."""
    sign = 1 if family == "long_future" else -1
    return _simulate_linear(cfg, da, "NIFTY", sign, cfg.lot_size, entry_ts, fut_expiry,
                            label=f"{'long' if sign > 0 else 'short'} NIFTY future",
                            kind="future", stop_loss=stop_loss, stop_loss_rupees=stop_loss_rupees,
                            take_profit=take_profit, take_profit_frac=take_profit_frac,
                            max_manage=max_manage, advisory=advisory, adv_lam=adv_lam,
                            adv_max_lots=adv_max_lots, adv_allow_reverse=adv_allow_reverse,
                            adv_risk_drift=adv_risk_drift, adv_horizon_frac=adv_horizon_frac,
                            regime_expiry=regime_expiry)


def _simulate_linear(cfg, da, symbol, sign, unit_size, entry_ts, end_expiry,
                     label="linear", kind="future", margin_frac=0.12,
                     stop_loss=False, stop_loss_rupees=None, take_profit=False,
                     take_profit_frac=0.6, max_manage=400,
                     advisory=False, adv_lam=0.5, adv_max_lots=2,
                     adv_allow_reverse=True, adv_risk_drift=1.0,
                     adv_horizon_frac=1.0, regime_expiry=None) -> dict:
    """Backtest ANY linear (delta-1) instrument — a NIFTY future OR an individual
    stock — by walking its 1-minute price path from entry to `end_expiry`, marking
    P&L = sign·(price−entry)·unit_size. `unit_size` is the lot (future) or share
    quantity (stock). Returns the same shape as the option simulate so the UI works.

    If `advisory` is set, the FORECAST-DRIVEN futures optimizer is run at every
    managed bar: it scores HOLD/EXIT/ADD/REDUCE/REVERSE and logs what it WOULD do
    (plus a shadow 'would-be' equity if its calls were followed). The recorded P&L
    still walks the plain 1-lot position — the overlay is there to be VALIDATED
    before it's ever allowed to trade ("evaluate before you trust")."""
    lot = unit_size
    end_ts = f"{str(end_expiry)[:10]}T23:59:59Z" if end_expiry else None
    bars = [b for b in da.bars(symbol, "1m", end=end_ts, start=entry_ts, limit=200000) if b.get("close")]
    if len(bars) < 2:
        return {"error": f"no {symbol} 1m bars from {entry_ts} to {end_expiry} — pick a later date, or {symbol} isn't in the DB"}
    entry_spot = float(bars[0]["close"])
    entry_ts = bars[0]["ts"]
    stride = max(1, len(bars) // max(1, max_manage or 200))
    decisions, series = [], []
    peak, max_dd = None, 0.0
    exit_ts, exit_spot, final = bars[-1]["ts"], float(bars[-1]["close"]), None

    # --- advisory (would-be) shadow state -------------------------------------
    adv_cache = None
    shadow_pos = int(sign)                       # start in the entered direction, 1 lot
    shadow_cash = -sign * entry_spot * lot       # established at entry → equity 0 at entry
    shadow_cost = 0.0
    adv_actions = 0
    if advisory:
        try:
            adv_cache = BarCache(cfg.db_path, list(dict.fromkeys(
                da.available_symbols("1m") + list(CROSS_ASSET_SYMBOLS))),
                start=entry_ts, end=end_ts or bars[-1]["ts"])
        except Exception:
            adv_cache = None

    for i in range(0, len(bars), stride):
        b = bars[i]; sp = float(b["close"])
        mark = sign * (sp - entry_spot) * lot
        peak = mark if peak is None else max(peak, mark)
        dd = peak - mark; max_dd = max(max_dd, dd)
        action = "HOLD"
        reason = (f"Mark P&L ₹{round(mark)}. {symbol} {round(sp)} vs {label} "
                  f"entry {round(entry_spot)} ({sp - entry_spot:+.0f} pts).")
        if take_profit and take_profit_frac and stop_loss_rupees and mark >= abs(stop_loss_rupees) * take_profit_frac:
            action = "TAKE_PROFIT"; reason = f"Mark ₹{round(mark)} hit take-profit → close."
        elif stop_loss and stop_loss_rupees and mark <= -abs(stop_loss_rupees):
            action = "STOP_LOSS"; reason = f"Mark ₹{round(mark)} breached ₹{round(abs(stop_loss_rupees))} stop → exit."
        dec = {"ts": b["ts"], "spot": round(sp), "mark_pnl": round(mark),
               "in_range": None, "action": action, "reason": reason, "signal": None,
               "advisory": None, "peak_inr": round(peak, 0), "drawdown_inr": round(dd, 0)}

        # --- forecast-driven advisory overlay (does NOT change recorded P&L) ---
        # runs for NIFTY and its futures series (regime = NIFTY direction; the
        # instrument's own price is used as the level).
        if advisory and "NIFTY" in symbol.upper():
            try:
                reg, _pin, sig = _regime_at(cfg, b["ts"], regime_expiry or end_expiry, adv_cache)
                cur_mark = shadow_cash + shadow_pos * sp * lot - shadow_cost
                adv = _fut_eval.evaluate_futures_actions(
                    sp, shadow_pos, reg, cfg, lam=adv_lam, horizon_frac=adv_horizon_frac,
                    max_lots=adv_max_lots, allow_reverse=adv_allow_reverse,
                    risk_drift_frac=adv_risk_drift, current_mark_inr=cur_mark)
                chosen = next((r for r in adv["table"] if r["action"] == adv["best"]), None)
                if chosen is not None:
                    delta = int(chosen["target_lots"]) - shadow_pos
                    if delta != 0:
                        shadow_cash -= delta * sp * lot
                        shadow_cost += cfg.costs.legs_cost_inr(abs(delta), lot)
                        shadow_pos = int(chosen["target_lots"])
                        adv_actions += 1
                would_be = shadow_cash + shadow_pos * sp * lot - shadow_cost
                dec["signal"] = sig
                dec["advisory"] = {"best": adv["best"], "forecast": adv["forecast"],
                                   "table": adv["table"], "shadow_lots": shadow_pos,
                                   "would_be_pnl": round(would_be, 0)}
            except Exception:
                pass

        decisions.append(dec)
        series.append({"ts": b["ts"], "spot": sp, "pnl_rupees": round(mark, 0)})
        if action in ("STOP_LOSS", "TAKE_PROFIT"):
            final, exit_ts, exit_spot = mark, b["ts"], sp
            break
    if final is None:
        final = sign * (exit_spot - entry_spot) * lot
    would_be_final = (shadow_cash + shadow_pos * exit_spot * lot - shadow_cost) if advisory else None
    marks = [d["mark_pnl"] for d in decisions]
    # capital base: futures ≈ SPAN margin (margin_frac × notional); stock = full value.
    cap = entry_spot * lot * (margin_frac if kind == "future" else 1.0)
    stats = {"capital_base_inr": round(cap, 0),
             "total_pnl_inr": round(final, 0),
             "total_return_pct": round(final / cap * 100, 2) if cap else None,
             "peak_pnl_inr": round(max(marks), 0), "trough_pnl_inr": round(min(marks), 0),
             "max_drawdown_inr": round(max_dd, 0),
             "max_drawdown_pct": round(max_dd / cap * 100, 2) if cap else None,
             "n_decisions": len(decisions), "n_adjustments": 0, "n_harvests": 0, "n_vetoes": 0,
             "cost_inr": round(cfg.costs.per_leg_inr * 2, 0), "won": final > 0,
             "net_vega_inr_per_volpt": 0, "vega_3pt_inr": 0, "vega_5pt_inr": 0}
    if advisory:
        # the would-be optimizer path (what the plain 1-lot HOLD would become if the
        # forecast optimizer's calls were followed) — for validation, not execution.
        stats["advisory"] = {
            "would_be_pnl_inr": round(would_be_final, 0) if would_be_final is not None else None,
            "plain_pnl_inr": round(final, 0),
            "edge_inr": round((would_be_final - final), 0) if would_be_final is not None else None,
            "n_advisory_actions": adv_actions, "max_lots": adv_max_lots,
            "note": "optimizer ran advisory-only; recorded P&L is the plain 1-lot path."}
    return {
        "expiry": end_expiry, "entry_ts": entry_ts, "exit_ts": exit_ts,
        "note": f"{label} — {symbol} (linear)",
        "advisory_agreement": (stats.get("advisory") if advisory else None), "stats": stats,
        "entry_family": label, "final_family": label,
        "entry_legs": [f"{'Buy' if sign > 0 else 'Sell'} {int(lot)} {symbol} @ {round(entry_spot)}"],
        "entry_spot": entry_spot, "exit_spot": exit_spot,
        "cost_inr": stats["cost_inr"], "n_adjustments": 0, "adjustments": [],
        "pnl_rupees": round(final, 0), "series": series, "decisions": decisions,
    }


def _short_strikes(legs):
    """(short_call_strike, short_put_strike) from leg list [side,strike,sign,entry]."""
    sc = [l[1] for l in legs if l[0] == "call" and l[2] < 0]
    sp = [l[1] for l in legs if l[0] == "put" and l[2] < 0]
    return (min(sc) if sc else None), (max(sp) if sp else None)


@dataclass
class BacktestResult:
    trades: list
    metrics: dict
    decisions: list

    def as_dict(self):
        return {"metrics": self.metrics, "n_decisions": len(self.decisions),
                "trades": self.trades}


# blended-core roster from the single source (signals/registry.py) — never hardcode.
from ..signals.registry import blended_names as _blended_names
_DIR_SIGNALS = _blended_names()


def _bar_cache_for(cfg, caps, pad_days: int = 2):
    """Scoped in-memory BarCache over the caps' span so the per-snapshot regime
    re-evaluation does in-memory slices, not ~8 SQLite queries each (≈8× faster)."""
    if not caps:
        return None
    try:
        from datetime import datetime, timedelta
        from ..signals.data_access import CROSS_ASSET_SYMBOLS
        start = (datetime.fromisoformat(caps[0]["captured_at"].replace("Z", "+00:00"))
                 - timedelta(days=pad_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        da = DataAccess(cfg.db_path)
        syms = list(dict.fromkeys(da.available_symbols("1m") + CROSS_ASSET_SYMBOLS))
        return BarCache(cfg.db_path, syms, start=start, end=caps[-1]["captured_at"])
    except Exception:
        return None


def _regime_at(cfg, now, expiry, bar_cache=None):
    b = signal_bundle.evaluate(cfg.db_path, now, expiry,
                               veto_days=cfg.gates.event_veto_days, bar_cache=bar_cache)
    reg = regime_mod.classify(b, cfg.weights, cfg.gates)
    pin = b.get("time_of_day").detail.get("pin_risk", False)
    sig = {"regime": reg.label, "net_score": round(float(reg.net_score), 3),
           "direction": int(reg.direction), "confidence": round(float(reg.net_confidence), 3),
           "signals": {n: round(float(b.get(n).score), 2) for n in _DIR_SIGNALS
                       if b.get(n).status == "OK"}}
    return reg, pin, sig


def _cadence_min(caps) -> float:
    """Median spacing between captures, in minutes (defaults to 1.0)."""
    from datetime import datetime
    ts = []
    for c in caps[:200]:
        try:
            ts.append(datetime.fromisoformat(c["captured_at"].replace("Z", "+00:00")))
        except Exception:
            pass
    if len(ts) < 2:
        return 1.0
    diffs = sorted((ts[i + 1] - ts[i]).total_seconds() / 60.0 for i in range(len(ts) - 1))
    diffs = [d for d in diffs if d > 0]
    return diffs[len(diffs) // 2] if diffs else 1.0


def run(cfg, expiry: str, exit_mode: str = "horizon", hold: int = 2,
        max_manage: int = 60, start: str | None = None, end: str | None = None,
        log_stand_asides: bool = True, max_entries: int = 40,
        freq_minutes: float | None = None, hard_cap: int = 220,
        roll_directional: bool = False,
        window_days: float | None = None,
        stop_loss: bool = False, stop_loss_mult: float = 2.0,
        stop_loss_rupees: float | None = None,
        cooldown_min: float | None = None, max_rolls: int | None = None,
        persist_near: int | None = None,
        harvest: bool = False, min_harvest_inr: float = 100.0,
        take_profit: bool = False, take_profit_frac: float = 0.6,
        mps_benchmark: str = "off",
        harvest_gate: str = "off", harvest_lambda: float = 0.5,
        harvest_risk_drift: float = 0.0, harvest_max: int | None = None,
        harvest_max_debt: float | None = None,
        harvest_min_buffer: float | None = None,
        _shared_caches: dict | None = None) -> BacktestResult:
    import time as _time
    _t0 = _time.time()
    da = DataAccess(cfg.db_path)
    # Backtest window = the last N TRADING SESSIONS (session days) up to and
    # including expiry. We take the distinct session dates that actually have data
    # (holidays/weekends simply have none), keep the most recent N, and start from
    # the first capture of the earliest kept session. E.g. sessions
    # …Jul 1,2,3,6,7 with expiry Jul 7: N=4 → {Jul 2,3,6,7} → start Jul 2.
    if window_days and not start:
        _all = da.list_captures(expiry=expiry)
        _dates = sorted({c["captured_at"][:10] for c in _all})
        if _dates:
            _keep = _dates[-int(window_days):]
            start = min(c["captured_at"] for c in _all
                        if c["captured_at"][:10] == _keep[0])
    caps = da.list_captures(expiry=expiry, start=start, end=end)
    if len(caps) < 2:
        note = (f"only {len(caps)} snapshot(s) in this window"
                + (f" ({window_days}d before expiry)" if window_days else "")
                + " — widen the window or pick an expiry with more data")
        return BacktestResult([], {"n_trades": 0, "captures_total": len(caps),
                                   "window_days": window_days, "note": note}, [])

    # Entry cadence. If the user asked for a frequency (e.g. every 15 min), derive
    # the stride from the actual capture spacing; otherwise bound by max_entries.
    # Either way a hard runtime cap keeps a fine frequency on a huge DB from
    # blowing up the eval count.
    cadence = _cadence_min(caps)
    # manage mode re-evaluates ~max_manage times per entry, so cap entries tighter.
    eff_cap = min(hard_cap, 60) if exit_mode == "manage" else hard_cap
    if freq_minutes:
        stride = max(1, round(freq_minutes / cadence))
    else:
        stride = max(1, len(caps) // max(1, max_entries))
    if len(caps) // stride > eff_cap:               # runtime guard
        stride = -(-len(caps) // eff_cap)           # ceil div

    trades, decisions = [], []

    # Memoise regime evaluation per timestamp: managed positions overlap heavily,
    # so the same snapshot's regime would otherwise be recomputed many times.
    # `_shared_caches` (passed by the A/B/C/D harness) lets the four runs REUSE the
    # identical prediction timeline — regimes, bar cache and entry suggestions are
    # the same across strategies, so only the first run pays for them (≈4× → ≈1×).
    _sc = _shared_caches if _shared_caches is not None else {}
    _regime_cache: dict = _sc.setdefault("regime", {})
    _suggest_cache: dict = _sc.setdefault("suggest", {})
    if "bar" not in _sc:
        _sc["bar"] = _bar_cache_for(cfg, caps)
    _bc = _sc["bar"]
    def regime_at_cached(ts):
        if ts not in _regime_cache:
            _regime_cache[ts] = _regime_at(cfg, ts, expiry, bar_cache=_bc)
        return _regime_cache[ts]

    for i, cap in enumerate(caps):
        if i % stride != 0:                 # only enter on strided snapshots
            continue
        now = cap["captured_at"]
        if days_to_expiry(now, expiry) <= 0.01:
            continue

        sug = _suggest_cache.get(now)
        if sug is None:
            sug = suggester.suggest(cfg, now, expiry)
            _suggest_cache[now] = sug
        decisions.append({"now": now, "action": sug.decision.get("action"),
                          "regime": sug.decision.get("regime"),
                          "family": sug.decision.get("family"),
                          "net_score": sug.decision.get("net_score"),
                          "edge_ratio": sug.decision.get("edge_ratio"),
                          "cost_gated": sug.decision.get("cost_gated"),
                          "tradeable": sug.tradeable})
        if not sug.tradeable or sug.structure is None:
            continue

        entry_chain = da.chain_as_of(now, expiry)
        pos = _Position([(l["side"], l["strike"], l["sign"])
                         for l in sug.structure["legs"]],
                        entry_chain, sug.structure["family"], cfg,
                        max_loss_pts=sug.structure.get("max_loss_pts", 0.0))

        # ---- forward walk / management -----------------------------------
        # Scale the holding window by `stride` so trades span meaningful time on
        # 1-minute data (a "hold" of 2 means 2 stride-steps, not 2 minutes), and
        # so management checks happen at spaced intervals rather than every minute.
        exit_ts = now
        exit_spot = entry_chain.spot
        exit_reason = "MARK"          # tagged per exit path below; feeds metrics attribution

        if exit_mode == "manage":
            checks = 0
            j = i + stride
            while j < len(caps) and checks < max_manage:
                jn = caps[j]["captured_at"]
                later_chain = da.chain_as_of(jn, expiry)
                if later_chain is None:
                    j += stride; checks += 1; continue
                reg, pin, sig = regime_at_cached(jn)
                # take-profit: bank the gain once enough of the max credit is captured.
                if take_profit:
                    net = pos.mark_net_rupees(later_chain)
                    tgt = pos.profit_target_rupees(take_profit_frac)
                    if net >= tgt:
                        pos.adjust_log.append({"action": "TAKE_PROFIT", "touched": 0,
                                               "rationale": f"booked ₹{round(net)} "
                                               f"(≥{round(take_profit_frac*100)}% of max credit)",
                                               "orders": [], "at": jn, "signal": sig})
                        pos.close(later_chain)
                        exit_ts, exit_spot = jn, later_chain.spot
                        exit_reason = "TAKE_PROFIT"
                        break
                # stop-loss: bail early if the loss has run past the threshold,
                # instead of holding/rolling to expiry ("fear of larger loss").
                if stop_loss:
                    net = pos.mark_net_rupees(later_chain)
                    thresh = (stop_loss_rupees if stop_loss_rupees
                              else pos.stop_threshold_rupees(stop_loss_mult))
                    if net <= -thresh:
                        _why = (f"loss ₹{round(-net)} past ₹{round(thresh)} stop"
                                + (" (user-set)" if stop_loss_rupees else " (auto)")
                                + " — cut early")
                        pos.adjust_log.append({"action": "STOP_LOSS", "touched": 0,
                                               "rationale": _why, "orders": [], "at": jn,
                                               "signal": sig})
                        pos.close(later_chain)
                        exit_ts, exit_spot = jn, later_chain.spot
                        exit_reason = "STOP_LOSS"
                        break
                plan = adjustment.evaluate(pos.family, [(l[0], l[1], l[2]) for l in pos.legs],
                                           later_chain, reg, cfg, pin_risk=pin,
                                           roll_directional=roll_directional,
                                           n_adjust=pos.n_adjust,
                                           mins_since_last=_mins_between(jn, pos.last_adjust_ts),
                                           breach_streak=pos.breach_streak,
                                           cooldown_min=cooldown_min, max_rolls=max_rolls,
                                           persist_near=persist_near,
                                           harvest=harvest, min_harvest_inr=min_harvest_inr,
                                           stop_active=stop_loss, credit_pts=pos.entry_credit_pts)
                pos.breach_streak = pos.breach_streak + 1 if plan.threatened else 0
                if plan.action in ("CLOSE", "EXIT"):
                    pos.adjust_log.append({"action": plan.action, "touched": 0,
                                           "rationale": plan.rationale, "orders": [],
                                           "at": jn, "signal": sig})
                    pos.close(later_chain)
                    exit_ts, exit_spot = jn, later_chain.spot
                    exit_reason = plan.action        # "CLOSE" or "EXIT"
                    break
                if plan.action != "HOLD":
                    # HARVEST execution gate (Strategy C/D): veto the rule's harvest
                    # when the optimizer says it isn't worth it, or the budget is spent.
                    vetoed = False
                    if plan.action == "HARVEST_WING" and harvest_gate != "off":
                        adv = _action_eval.evaluate_actions(
                            pos.family, [(l[0], l[1], l[2]) for l in pos.legs], later_chain, reg, cfg,
                            lam=harvest_lambda, risk_drift_frac=harvest_risk_drift,
                            harvest_debt_pts=pos.harvest_debt_pts(), n_harvests=pos.n_harvests,
                            max_harvests=harvest_max, max_harvest_debt=harvest_max_debt,
                            min_wing_buffer=harvest_min_buffer,
                            current_mark_inr=pos.mark_net_rupees(later_chain))
                        if harvest_gate in ("budget", "both") and adv.get("harvest_state", {}).get("blocked"):
                            vetoed = True
                        if harvest_gate in ("optimizer", "both") and not vetoed:
                            hv = next((r for r in adv.get("table", []) if r["action"] == "HARVEST_WING"), None)
                            if hv is None or hv.get("score", -1) <= 0:   # HOLD ≥ harvesting
                                vetoed = True
                    if not vetoed:
                        pos.apply(plan, later_chain)
                        pos.last_adjust_ts = jn
                        pos.adjust_log[-1]["signal"] = sig      # what the signals said
                    else:
                        pos.adjust_log.append({"action": "HARVEST_VETO", "touched": 0,
                                               "rationale": "harvest vetoed (optimizer/budget)",
                                               "orders": [], "at": jn, "signal": sig})
                exit_ts, exit_spot = jn, later_chain.spot
                j += stride; checks += 1
            if pos.legs:                             # no CLOSE fired -> settle at EXPIRY close
                exit_reason = "EXPIRY_MARK"          # ran to expiry without a bracket/adjust close
                fin_ts = caps[-1]["captured_at"]
                fin_chain = da.chain_as_of(fin_ts, expiry)
                if fin_chain is not None:
                    pos.close(fin_chain)
                    exit_ts, exit_spot = fin_ts, fin_chain.spot
                else:
                    pos.close(da.chain_as_of(exit_ts, expiry))
        elif exit_mode == "expiry":
            settle_spot = caps[-1]["spot"]
            pos.close(entry_chain, at_expiry=True, settle_spot=settle_spot)
            exit_ts, exit_spot = caps[-1]["captured_at"], settle_spot
            exit_reason = "EXPIRY_SETTLE"
        else:  # horizon
            j = min(i + hold * stride, len(caps) - 1)
            later_chain = da.chain_as_of(caps[j]["captured_at"], expiry)
            if later_chain is None:
                continue
            pos.close(later_chain)
            exit_ts, exit_spot = caps[j]["captured_at"], later_chain.spot
            exit_reason = "HORIZON"

        net_rupees = pos.net_rupees()

        # ---- tail-hedge / drawdown insurance overlay --------------------------
        # If the liquidity-derisk overlay fired at entry, the desk holds a long OTM
        # put alongside the primary structure. Model its P&L: (exit value − premium)
        # × lot × lots, less round-trip cost. Uses the exit-chain put LTP as the mark,
        # falling back to intrinsic. This is what caps drawdown on a de-risk day.
        hedge_rupees = 0.0
        hedge_rec = sug.hedge if (sug.hedge and sug.hedge.get("fired")) else None
        if hedge_rec:
            lp = hedge_rec["long_put"]; k = lp["strike"]
            prem = lp["premium_pts"]; lots = hedge_rec.get("lots", 1)
            exit_chain = da.chain_as_of(exit_ts, expiry)
            val = (exit_chain.put_ltp.get(k) if exit_chain else None)
            if not val:
                val = max(k - exit_spot, 0.0)               # intrinsic fallback
            hedge_rupees = ((val - prem) * cfg.lot_size * lots
                            - cfg.costs.legs_cost_inr(2 * lots, cfg.lot_size))
            net_rupees += hedge_rupees

        trades.append({
            "session": _session(now), "entry_ts": now, "exit_ts": exit_ts,
            "direction": sug.decision["direction"],
            "hedge_fired": bool(hedge_rec),
            "hedge_intensity": round(sug.hedge["intensity"], 3) if sug.hedge else None,
            "hedge_pnl_rupees": round(hedge_rupees, 0) if hedge_rec else None,
            "regime": sug.decision.get("regime"),
            "exit_reason": exit_reason,
            "entry_family": sug.structure["family"], "final_family": pos.family,
            "entry_legs": [f"{'Buy' if l['sign'] > 0 else 'Sell'} {l['side']} {int(l['strike'])}"
                           for l in sug.structure["legs"]],
            "entry_spot": entry_chain.spot, "exit_spot": exit_spot,
            "net_score": sug.decision["net_score"],
            "gross_pnl_pts": round(pos.realized_pts, 3),
            "cost_inr": round(pos.cost_inr, 0),
            "n_adjustments": pos.n_adjust, "adjustments": pos.adjust_log,
            "pnl_rupees": round(net_rupees, 0),
            "pnl_pts": round(net_rupees / cfg.lot_size, 3),   # net of costs, for metrics
            "won": bool(net_rupees > 0),
        })

    # MPS0 perfect-hindsight benchmark (optional). The price path is the NIFTY spot
    # series over the window; flip cost depends on the dropdown basis:
    #   off   -> no benchmark
    #   gross -> zero-cost ceiling (Pardo potential profit / total opportunity)
    #   net   -> charge the desk's actual avg round-trip cost per reversal
    if mps_benchmark and mps_benchmark != "off":
        # Measure the ceiling at the strategy's ENTRY CADENCE (every `stride`
        # snapshot), NOT every minute. A tick/minute-resolution MPS0 captures
        # micro-reversals no options structure could trade, which inflates the
        # ceiling ~10-100× and makes capture% meaninglessly tiny. Sampling at the
        # decision cadence keeps capture% comparable to how the desk actually trades.
        price_path = [caps[i]["spot"] for i in range(0, len(caps), stride)
                      if caps[i].get("spot")]
        if mps_benchmark == "gross":
            flip_cost = 0.0
        else:  # "net"
            avg_cost = (sum(t.get("cost_inr", 0) for t in trades) / len(trades)) if trades else 0.0
            flip_cost = avg_cost or (cfg.costs.legs_cost_inr(2, cfg.lot_size) * 2.0)
        m = metrics.summarize(trades, price_path=price_path,
                              mps_flip_cost_inr=flip_cost, lot_size=cfg.lot_size,
                              mps_label=mps_benchmark)
    else:
        m = metrics.summarize(trades)
    _hedged = [t for t in trades if t.get("hedge_fired")]
    m["hedge_trades"] = len(_hedged)
    m["hedge_pnl_rupees"] = round(sum(t.get("hedge_pnl_rupees") or 0 for t in _hedged), 0)
    m["n_harvests"] = sum(1 for t in trades for a in t.get("adjustments", []) if a.get("action") == "HARVEST_WING")
    m["n_harvest_vetoes"] = sum(1 for t in trades for a in t.get("adjustments", []) if a.get("action") == "HARVEST_VETO")
    m["harvest_gate"] = harvest_gate

    # ---- cost-edge gate activity (so the 1.5× setting is VISIBLE) -------------
    _mult = float(getattr(cfg.gates, "min_edge_cost_mult", 0.0) or 0.0)
    m["cost_edge_mult"] = _mult
    if _mult > 0:
        acted = [d for d in decisions if d.get("action") == "ACT" or d.get("tradeable")]
        gated = [d for d in decisions if d.get("cost_gated")]
        ratios = [d["edge_ratio"] for d in decisions if d.get("edge_ratio") is not None]
        avg_ratio = round(sum(ratios) / len(ratios), 2) if ratios else None
        m["cost_gated_count"] = len(gated)
        m["cost_edge_avg_ratio"] = avg_ratio
        m["cost_edge_note"] = (
            f"Cost-edge gate ON at {_mult:g}×: a would-be trade is stood aside unless the "
            f"expected 1σ move (₹/lot) is ≥ {_mult:g}× its round-trip cost. "
            f"It gated {len(gated)} would-be entr{'y' if len(gated)==1 else 'ies'}"
            + (f"; average 1σ/cost ratio was {avg_ratio}× (≥{_mult:g}× = trade, below = skip)."
               if avg_ratio is not None else "."))
    else:
        m["cost_edge_note"] = "Cost-edge gate OFF (0×) — trades taken on signal regardless of edge/cost."
    if mps_benchmark and mps_benchmark != "off":
        m["mps_note_plain"] = (
            f"MPS0 ({mps_benchmark}) is a perfect-hindsight ceiling: the best P&L a flawless "
            f"trader could have extracted from this exact price path ({'zero-cost' if mps_benchmark=='gross' else 'charging your avg round-trip cost'}). "
            f"Capture % = your realised net ÷ that ceiling — a skill score vs the opportunity, NOT a target you should hit.")
    m["elapsed_sec"] = round(_time.time() - _t0, 1)
    m["captures_total"] = len(caps)
    m["entry_stride"] = stride
    m["cadence_min"] = round(cadence, 1)
    m["freq_minutes_effective"] = round(stride * cadence, 1)
    m["window_sessions"] = window_days
    m["sessions_in_window"] = len({c["captured_at"][:10] for c in caps})
    m["session_dates"] = sorted({c["captured_at"][:10] for c in caps})
    m["window_start"] = caps[0]["captured_at"] if caps else None
    m["window_end"] = caps[-1]["captured_at"] if caps else None
    return BacktestResult(trades=trades, metrics=m,
                          decisions=decisions if log_stand_asides else [])
