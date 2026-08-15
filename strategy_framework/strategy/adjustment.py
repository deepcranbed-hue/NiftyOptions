"""
strategy_framework/strategy/adjustment.py
=========================================
Position management: what to do with an OPEN structure when the market moves,
instead of always flat-closing it.

The user's workflow, encoded as rules: once a range structure (iron condor /
butterfly / short strangle) is on and the market develops a direction, don't just
close — *morph* the position to tilt P&L with the move:

  ROLL_UNTESTED_TOWARD : roll the untested credit spread toward spot in the trend
                         direction — collects fresh premium and adds directional
                         delta ("reduce the wing span on the winning side").
  DEFEND_TESTED        : roll the tested spread further out / wider to buy room
                         ("spread the wing span on the threatened side").
  CONVERT_TO_VERTICAL  : drop the tested spread entirely; the retained credit
                         spread is now a directional structure riding the trend.
  ADD_LONG_DIRECTIONAL : add a long ATM option in the trend direction to tilt
                         delta hard (condor → directional hybrid).
  RECENTER             : range persists but spot drifted — re-establish a fresh
                         condor/butterfly centred on the new spot.
  CONVERT_STRADDLE/STRANGLE/BUTTERFLY : re-express the view as another structure.
  CLOSE                : last resort (expiry-close pin, or thesis broken).
  HOLD                 : thesis intact, leave it alone.

Output is expressed as leg deltas — `close_legs` (buy/sell to close) and
`open_legs` (new legs) — so the backtest can realise P&L leg-by-leg and charge
the ₹/leg transaction cost on exactly the legs touched.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

_RANGE_FAMILIES = {"iron_condor", "iron_butterfly", "short_strangle", "short_straddle"}

# ---- position-management discipline (plan §4/§5): don't chase a trend --------
# All PRIOR (judgement) until calibrated on history (D-MA-04).
COOLDOWN_MIN = 15.0        # min minutes between adjustments (unless truly breached)
MAX_ROLLS = 2             # after this many adjustments, exit instead of chasing
PERSIST_NEAR = 1          # a mere "near" needs 1 prior threatened snapshot to act
                          # (a real breach acts immediately)


@dataclass
class AdjustmentPlan:
    action: str
    rationale: str
    close_legs: list = field(default_factory=list)   # [(side,strike,sign), ...]
    open_legs: list = field(default_factory=list)
    new_family: str = None
    threatened: bool = False   # was a short strike near/breached at this snapshot?

    @property
    def touched(self) -> int:
        return len(self.close_legs) + len(self.open_legs)

    def as_dict(self) -> dict:
        return {"action": self.action, "rationale": self.rationale,
                "new_family": self.new_family, "touched_legs": self.touched,
                "threatened": self.threatened,
                "close_legs": [list(l) for l in self.close_legs],
                "open_legs": [list(l) for l in self.open_legs]}


def _degenerate(result_legs) -> bool:
    """True if a roll collapsed a spread — same side holding a short AND a long at
    the identical strike (zero width), which is a no-op that still costs ₹/leg. This
    is the band-edge bug in the reported trace (`Sell call 24450 / Buy call 24450`)."""
    from collections import defaultdict
    by_side = defaultdict(lambda: {"s": set(), "l": set()})
    for s, k, g in result_legs:
        by_side[s]["s" if g < 0 else "l"].add(k)
    return any(v["s"] & v["l"] for v in by_side.values())


def _step(chain) -> float:
    ks = sorted(set(chain.strikes))
    return float(np.median(np.diff(ks))) if len(ks) > 1 else 50.0


def _nearest(chain, target):
    return min(chain.strikes, key=lambda k: abs(k - target))


def _shorts(legs):
    sc = [k for s, k, g in legs if s == "call" and g < 0]
    sp = [k for s, k, g in legs if s == "put" and g < 0]
    return (min(sc) if sc else None), (max(sp) if sp else None)


def _longs(legs):
    lc = [k for s, k, g in legs if s == "call" and g > 0]
    lp = [k for s, k, g in legs if s == "put" and g > 0]
    return (max(lc) if lc else None), (min(lp) if lp else None)


def evaluate(family: str, legs: list, chain, regime, cfg,
             pin_risk: bool = False, roll_directional: bool = False,
             n_adjust: int = 0, mins_since_last: float | None = None,
             breach_streak: int = 0,
             cooldown_min: float | None = None, max_rolls: int | None = None,
             persist_near: int | None = None,
             harvest: bool = False, min_harvest_inr: float = 100.0,
             stop_active: bool = False, credit_pts: float = 0.0) -> AdjustmentPlan:
    """Decide how to manage `legs` (current position) given the fresh `regime`.

    `roll_directional`: when True, directional structures (verticals / long
    options) are also rolled in the trend direction (take profit + re-arm on a
    continuation, defend on a mild pullback) instead of only holding or closing.

    Position-management discipline (so the engine trades like a desk, not a strike
    follower):
      `n_adjust`        — adjustments already made to this position; past MAX_ROLLS
                          we EXIT rather than keep chasing.
      `mins_since_last` — minutes since the last adjustment; inside COOLDOWN_MIN we
                          hold (unless the strike is actually breached).
      `breach_streak`   — consecutive prior snapshots the tested short was
                          threatened; a lone "near" waits for confirmation.
    A strong, CONFIRMED trend with the move means the range thesis is dead: we
    CONVERT to a directional spread on the winning wing (or exit), instead of
    rolling the whole condor into the move (which chases the trend and re-sells the
    losing side each time — the pathology in the reported trace).
    """
    # A future is a linear directional position with no wings to roll/harvest —
    # there's nothing for the strike-management engine to do. Hold; the trade is
    # managed by stop-loss / take-profit (which run in the backtest loop).
    if family in ("long_future", "short_future") or any(l[0] == "future" for l in legs):
        return AdjustmentPlan("HOLD", "future — linear position, no wings to manage")

    # resolve tunables (UI can override; else the PRIOR defaults above)
    cooldown_min = COOLDOWN_MIN if cooldown_min is None else cooldown_min
    max_rolls = MAX_ROLLS if max_rolls is None else max_rolls
    persist_near = PERSIST_NEAR if persist_near is None else persist_near

    spot = chain.spot
    step = _step(chain)
    em = max(regime.expected_move_pts, step)
    SC, SP = _shorts(legs)
    LC, LP = _longs(legs)
    direction = regime.direction
    strong = abs(regime.net_score) >= 0.30 and regime.net_confidence >= 0.45

    # ---- expiry-close pin: never hold short gamma into the print ----------
    if pin_risk:
        return AdjustmentPlan("CLOSE", "expiry-close pin risk — flatten short gamma",
                              close_legs=list(legs))

    # ---- already-directional positions -----------------------------------
    if family not in _RANGE_FAMILIES:
        pos_dir = _family_direction(family, legs)
        # a strong trend now opposing the position -> cut it (both modes).
        if strong and direction != 0 and direction != pos_dir:
            return AdjustmentPlan("CLOSE",
                                  "trend reversed against directional position",
                                  close_legs=list(legs))
        # opt-in: roll a winning directional trade in the trend direction to lock
        # gains and re-arm higher/lower (same ₹20/leg accounting).
        if roll_directional and strong and direction != 0 and direction == pos_dir:
            return _roll_structure(legs, chain, cfg, direction, roll=1, step=step)
        return AdjustmentPlan("HOLD", "directional thesis intact")

    # ---- range position, no new direction: manage the range ---------------
    if direction == 0 or not strong:
        # range persists — but if spot drifted well past the body, re-centre.
        center = _center(SC, SP, spot)
        if abs(spot - center) > 1.0 * em and regime.label == "RANGE":
            return _recenter(family, legs, chain, cfg, step, em=regime.expected_move_pts)
        return AdjustmentPlan("HOLD", "range intact, position centred")

    # ---- range position + a NEW direction emerged: tilt/convert -----------
    if direction > 0:            # bullish breakout: call side is tested
        tested_short, untested_short = SC, SP
        tested_side, untested_side = "call", "put"
    else:                        # bearish breakout: put side is tested
        tested_short, untested_short = SP, SC
        tested_side, untested_side = "put", "call"

    if tested_short is None:     # e.g. short straddle has no distinct wings
        return _convert_directional_from_straddle(legs, chain, cfg, direction, step)

    # How close is spot to the tested wing's BREAKEVEN (in expected-move units)?
    # The breakeven sits `credit_pts` beyond the short strike (the collected credit
    # is a cushion), so we react to actually-losing territory, not the raw short.
    dist = (tested_short - spot) if direction > 0 else (spot - tested_short)
    be_dist = dist + max(credit_pts, 0.0)          # distance to breakeven
    breached = be_dist <= 0                          # past breakeven — actually losing
    near = be_dist <= 0.5 * em
    threatened = near or breached

    very_strong = abs(regime.net_score) >= 0.45 and regime.net_confidence >= 0.55

    if not threatened:
        # Opportunistic premium harvest (plan §6): we're in a TREND (this branch),
        # the tested short is still safe, and the UNTESTED wing has gone over-safe.
        # If rolling it toward spot collects more fresh premium than it costs, take
        # it — but only when enabled, outside the cooldown, and worth ≥ min_harvest.
        # (Deliberately trend-only: in a quiet range premiums barely move, so this
        # would just churn fees.)
        if harvest and (mins_since_last is None or mins_since_last >= cooldown_min):
            hp = _harvest_wing(legs, chain, cfg, untested_side, min_harvest_inr, step)
            if hp is not None:
                return hp
        # Otherwise: don't chase minor spot drift — the directional view matters more
        # than intraday wiggles, and rolling every snapshot just bleeds ₹/leg.
        return AdjustmentPlan("HOLD", "directional lean but short strikes still safe — hold")

    # -- discipline gates (plan §5): don't gamma-chase --------------------------
    # Cooldown: give the last adjustment room to work; a real breach overrides it.
    if mins_since_last is not None and mins_since_last < cooldown_min and not breached:
        return AdjustmentPlan("HOLD", threatened=True, rationale=(
            f"near a short strike but only {mins_since_last:.0f}m since the last "
            f"adjustment (< {cooldown_min:.0f}m cooldown) — let it develop, don't chase"))
    # Persistence: a single "near" snapshot isn't enough — wait for confirmation.
    if near and not breached and breach_streak < persist_near:
        return AdjustmentPlan("HOLD", threatened=True, rationale=(
            "spot is near a short strike but the breach isn't confirmed yet "
            "(persistence filter) — hold one more snapshot before acting"))
    # Roll budget spent: stop ADJUSTING (no more ₹/leg on rolls) — but the budget is
    # a fee/anti-chase limit, NOT a risk limit. If a stop-loss is active, let IT own
    # the exit (there's room until the stop), so just hold; only force a flat when
    # there's no stop-loss backstop at all.
    if n_adjust >= max_rolls:
        if stop_active:
            return AdjustmentPlan("HOLD", threatened=True, rationale=(
                f"used the {max_rolls}-adjustment budget — stop adjusting and let the "
                f"stop-loss govern the exit (still within risk limit; no more ₹/leg on rolls)"))
        return AdjustmentPlan("EXIT", threatened=True, close_legs=list(legs), rationale=(
            f"used the {max_rolls}-adjustment budget and no stop-loss is set — exit "
            f"rather than ride an unadjusted position with no risk backstop"))

    # -- strong, CONFIRMED trend with the move: the range thesis is dead --------
    # Shed the tested (losing) wing, KEEP the untested (winning) wing as a
    # directional credit spread riding the trend. This is the key fix: convert once
    # instead of rolling the whole condor into the trend over and over.
    if very_strong:
        plan = _convert_to_vertical(legs, chain, cfg, direction, tested_side,
                                    add_long=False, step=step)
        plan.threatened = True
        return plan

    # -- moderate lean: wing-level tilt (plan §1/§2/§7) -------------------------
    # Roll only the untested spread toward spot (collect premium, add delta, keep
    # the winner); defend the tested wing outward only if it's actually breached.
    plan = _tilt(legs, chain, cfg, direction, roll=1, defend=breached, step=step)
    plan.threatened = True
    return plan


# --------------------------------------------------------------------------
# Transformations (each returns an AdjustmentPlan with close/open leg deltas)
# --------------------------------------------------------------------------
def _center(SC, SP, spot):
    if SC and SP:
        return (SC + SP) / 2.0
    return spot


def _family_direction(family, legs):
    """Directional bias of a structure. Family name is authoritative (a vertical's
    net option-delta cancels, so leg signs alone are ambiguous)."""
    if family:
        if family.startswith("bull") or family == "long_call":
            return 1
        if family.startswith("bear") or family == "long_put":
            return -1
    # fallback: long strike vs short strike on the traded side
    calls = [(k, s) for side, k, s in legs if side == "call"]
    if calls:
        longs = [k for k, s in calls if s > 0]; shorts = [k for k, s in calls if s < 0]
        if longs and shorts:
            return 1 if min(longs) < min(shorts) else -1
    puts = [(k, s) for side, k, s in legs if side == "put"]
    if puts:
        longs = [k for k, s in puts if s > 0]; shorts = [k for k, s in puts if s < 0]
        if longs and shorts:
            return 1 if max(shorts) > max(longs) else -1
    return 0


def _prem(chain, side, k):
    book = chain.call_ltp if side == "call" else chain.put_ltp
    return float(book.get(k, 0.0))


def _harvest_wing(legs, chain, cfg, untested_side, min_inr, step):
    """Opportunistic premium harvest on the SAFE (untested) wing during a trend.

    The trend has pushed spot away from this wing, so its short is now deep-OTM and
    cheap. Roll the whole 2-leg spread one strike toward spot: buy back the cheap
    short, sell a richer one closer in, roll the long with it (defined risk kept).
    Only returns a plan if the FRESH net credit collected exceeds the ₹/leg cost by
    at least `min_inr` — otherwise there's nothing worth harvesting (as in a quiet
    range, where premiums barely move). Returns None if not worthwhile.

    This is income, not guaranteed profit: the credit is fully kept only if the wing
    stays OTM — which is why it's gated on the trend leaning AWAY from this wing.
    """
    shorts = [k for s, k, g in legs if s == untested_side and g < 0]
    longs = [k for s, k, g in legs if s == untested_side and g > 0]
    if not shorts or not longs:
        return None
    if untested_side == "call":                       # safe call wing above spot → roll down
        os_, ol_ = min(shorts), max(longs)
        ns_, nl_ = _nearest(chain, os_ - step), _nearest(chain, ol_ - step)
    else:                                             # safe put wing below spot → roll up
        os_, ol_ = max(shorts), min(longs)
        ns_, nl_ = _nearest(chain, os_ + step), _nearest(chain, ol_ + step)
    if ns_ == os_ or nl_ == ol_:
        return None                                    # at the band edge, no room
    fresh_pts = (_prem(chain, untested_side, ns_) - _prem(chain, untested_side, os_)) \
        - (_prem(chain, untested_side, nl_) - _prem(chain, untested_side, ol_))
    cost_inr = cfg.costs.legs_cost_inr(4, cfg.lot_size)     # close 2 + open 2
    net_inr = fresh_pts * cfg.lot_size - cost_inr
    close = [(untested_side, os_, -1), (untested_side, ol_, +1)]
    open_ = [(untested_side, ns_, -1), (untested_side, nl_, +1)]
    result = [l for l in legs if l not in close] + open_
    if _degenerate(result) or net_inr < min_inr:
        return None
    plan = AdjustmentPlan(
        "HARVEST_WING",
        (f"trend leaves the {untested_side} wing over-safe — roll it toward spot to "
         f"collect ~₹{round(net_inr)}/lot fresh premium after ₹{round(cost_inr)} cost "
         f"(income if it stays OTM; leans with the trend)"),
        close_legs=close, open_legs=open_)
    plan.threatened = False
    return plan


def _roll_structure(legs, chain, cfg, direction, roll, step):
    """Roll the ENTIRE structure `roll` strikes in the trend direction, keeping
    every leg (so an iron condor stays an iron condor, just shifted up/down).

    For a bullish move this rolls the call spread to higher strikes (buy/sell the
    calls up) and the put spread up too (sell/buy the puts up) — the position the
    user described: never flat-close the whole thing, just chase the move with all
    four legs so the tested short stays ahead of spot and the untested side keeps
    collecting premium.
    """
    roll_pts = roll * step * direction
    close, open_ = [], []
    for side, k, sign in legs:
        newk = _nearest(chain, k + roll_pts)
        if newk != k:
            close.append((side, k, sign)); open_.append((side, newk, sign))
    if not close:
        return AdjustmentPlan("HOLD", "no room to roll — at the strike-band edge")
    result = [l for l in legs if l not in close] + open_
    if _degenerate(result):
        return AdjustmentPlan("HOLD", "roll would collapse a spread at the band edge — skip")
    action = "ROLL_UP" if direction > 0 else "ROLL_DOWN"
    why = ("roll the whole structure %s %d strike(s) to follow the move — all legs "
           "kept, short strikes stay ahead of spot"
           % ("up" if direction > 0 else "down", roll))
    return AdjustmentPlan(action, why, close_legs=close, open_legs=open_)


def _tilt(legs, chain, cfg, direction, roll, defend, step):
    """Roll the untested credit spread toward spot (tilt), and optionally roll the
    tested spread outward to defend. Expressed as close/open leg deltas."""
    roll_pts = roll * step
    close, open_ = [], []
    for side, k, sign in legs:
        is_call = side == "call"
        tested = (is_call and direction > 0) or ((not is_call) and direction < 0)
        if tested and defend:
            newk = _nearest(chain, k + roll_pts * direction)   # roll out with move
            if newk != k:
                close.append((side, k, sign)); open_.append((side, newk, sign))
        elif not tested:
            newk = _nearest(chain, k + roll_pts * direction)   # roll toward spot
            if newk != k:
                close.append((side, k, sign)); open_.append((side, newk, sign))
    action = "DEFEND_TESTED" if defend else "ROLL_UNTESTED_TOWARD"
    why = ("roll untested spread toward spot to add %s delta and collect premium"
           % ("bullish" if direction > 0 else "bearish"))
    if defend:
        why = "defend tested wing (roll out/wider) + " + why
    if not close:
        return AdjustmentPlan("HOLD", "no beneficial roll available")
    result = [l for l in legs if l not in close] + open_
    if _degenerate(result):
        return AdjustmentPlan("HOLD", "wing roll would collapse a spread at the band edge — skip")
    return AdjustmentPlan(action, why, close_legs=close, open_legs=open_)


def _convert_to_vertical(legs, chain, cfg, direction, tested_side, add_long, step):
    """Drop the tested spread; keep the untested credit spread as a directional
    structure that rides the trend. Optionally add a long ATM option to tilt hard."""
    close = [(s, k, g) for s, k, g in legs if s == tested_side]
    remaining = [(s, k, g) for s, k, g in legs if s != tested_side]
    open_ = []
    new_family = ("bull_put_spread" if direction > 0 else "bear_call_spread")
    action = "CONVERT_TO_VERTICAL"
    why = ("drop tested %s spread; ride trend on retained %s credit spread"
           % (tested_side, "put" if direction > 0 else "call"))
    if add_long:
        atm = _nearest(chain, chain.spot)
        long_side = "call" if direction > 0 else "put"
        open_.append((long_side, atm, +1))
        action = "CONVERT_TO_VERTICAL+LONG"
        new_family = "bull_put_spread+long_call" if direction > 0 else "bear_call_spread+long_put"
        why += "; add long ATM %s to lean into the move" % long_side
    return AdjustmentPlan(action, why, close_legs=close, open_legs=open_,
                          new_family=new_family)


def _convert_directional_from_straddle(legs, chain, cfg, direction, step):
    """A short straddle with an emerging trend: buy back the losing side, keep the
    winning short, and lean directional — effectively a covered directional short."""
    atm = _nearest(chain, chain.spot)
    losing_side = "call" if direction > 0 else "put"   # short call loses in an up-move
    close = [(s, k, g) for s, k, g in legs if s == losing_side]
    long_side = "call" if direction > 0 else "put"
    open_ = [(long_side, atm, +1)]
    return AdjustmentPlan("CONVERT_STRADDLE_DIRECTIONAL",
                          "buy back losing short leg of straddle; add long %s to ride trend"
                          % long_side, close_legs=close, open_legs=open_,
                          new_family="directional_from_straddle")


def _recenter(family, legs, chain, cfg, step, em=None):
    """Range persists but spot drifted: close and re-open the range structure
    centred on the new spot."""
    from . import constructor
    st = constructor.build(family, chain, cfg, expected_move_pts=em)
    if st is None:
        return AdjustmentPlan("HOLD", "range drifted but no clean re-centre available")
    return AdjustmentPlan("RECENTER",
                          "range intact but spot drifted — re-centre %s on new spot" % family,
                          close_legs=list(legs),
                          open_legs=[(s, k, g) for s, k, g in st.legs],
                          new_family=family)
