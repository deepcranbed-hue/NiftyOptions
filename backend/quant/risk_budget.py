"""
risk_budget.py
--------------
The capital-protection gate. Sits AFTER market_view produces a suggestion and
BEFORE anything is sized. It answers the only question that keeps you solvent:
given the whole book, can this trade be taken, and how big?

It can VETO, not just size. Order of checks (most fatal first):
  1. Drawdown breaker   — if cumulative DD breached, block ALL new risk.
  2. Max-loss sizing     — lots from a fixed % of capital at risk per trade.
  3. Complacency filter  — premium-selling into a complacent tape is downsized/blocked.
  4. Portfolio heat      — total max-loss across open positions capped.
  5. Net Greek caps      — book net delta / net vega within bands.
Final lots = the MINIMUM the constraints allow; 0 => veto with the binding reason.

ALL NUMBERS BELOW ARE ASSUMED DEFAULTS — replace via RiskConfig when you provide
real capital / limits / lot size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ── config (ASSUMED — override with your real numbers) ───────────────────────
@dataclass
class RiskConfig:
    capital: float = 1_000_000.0        # ₹10 lakh (ASSUMED)
    risk_per_trade_pct: float = 0.015   # 1.5% of capital max loss per trade
    max_portfolio_heat_pct: float = 0.06  # 6% total max-loss across open book
    max_net_delta_units: float = 150.0  # |net delta| cap (≈2 NIFTY-fut lots @75)
    max_net_vega_rupees: float = 50_000.0  # |net vega| cap (₹ per 1 vol pt)
    max_drawdown_pct: float = 0.10      # halt new risk if DD ≥ 10%
    lot_size: int = 65                  # NIFTY lot size — SET to current NSE value (it changes)
    complacency_block: float = 70.0     # premium-sell blocked above this gauge score
    complacency_halve: float = 55.0     # premium-sell size halved above this


# ── data ──────────────────────────────────────────────────────────────────────
@dataclass
class Position:
    name: str
    lots: int
    max_loss_pts: float      # defined max loss in index POINTS per lot
    delta_per_lot: float     # signed delta units per lot
    vega_per_lot: float      # ₹ per 1 vol pt per lot (short vol => negative)


@dataclass
class Trade:
    structure: str           # e.g. "Bull put spread (sell premium below)"
    max_loss_pts: float      # per lot, defined-risk
    delta_per_lot: float
    vega_per_lot: float
    is_premium_sell: bool | None = None   # auto-inferred if None


_SELL_KW = ("sell premium", "condor", "short strangle", "short straddle",
            "bear call spread", "bull put spread", "credit")


def _infer_sell(structure: str) -> bool:
    s = structure.lower()
    return any(k in s for k in _SELL_KW)


# ── portfolio state ───────────────────────────────────────────────────────────
def portfolio_state(book: list[Position], cfg: RiskConfig) -> dict:
    heat_pts = sum(p.lots * p.max_loss_pts for p in book)
    heat_rs = heat_pts * cfg.lot_size
    net_delta = sum(p.lots * p.delta_per_lot for p in book)
    net_vega = sum(p.lots * p.vega_per_lot for p in book)
    return {
        "heat_rupees": heat_rs,
        "heat_pct": heat_rs / cfg.capital,
        "net_delta": net_delta,
        "net_vega": net_vega,
    }


# ── the gate ──────────────────────────────────────────────────────────────────
def size_trade(trade: Trade, book: list[Position], cfg: RiskConfig,
               complacency_score: float | None = None,
               current_drawdown_pct: float = 0.0) -> dict:
    warn: list[str] = []
    sell = trade.is_premium_sell if trade.is_premium_sell is not None \
        else _infer_sell(trade.structure)
    state = portfolio_state(book, cfg)

    # 1) drawdown breaker — hard stop
    if current_drawdown_pct >= cfg.max_drawdown_pct:
        return _veto(f"drawdown breaker: {current_drawdown_pct:.0%} ≥ "
                     f"{cfg.max_drawdown_pct:.0%} — new risk halted", state, cfg, structure=trade.structure)

    max_loss_rs = trade.max_loss_pts * cfg.lot_size
    if max_loss_rs <= 0:
        return _veto("trade has no defined max loss (undefined-risk) — blocked", state, cfg, structure=trade.structure)

    # 2) base max-loss sizing
    risk_budget_rs = cfg.capital * cfg.risk_per_trade_pct
    lots_risk = math.floor(risk_budget_rs / max_loss_rs)

    # 3) complacency filter (premium-selling only)
    comp_cap = math.inf
    if sell and complacency_score is not None:
        if complacency_score >= cfg.complacency_block:
            return _veto(f"premium-sell blocked: complacency {complacency_score:.0f} ≥ "
                         f"{cfg.complacency_block:.0f} (thin premium, shock-prone)",
                         state, cfg, structure=trade.structure)
        if complacency_score >= cfg.complacency_halve:
            comp_cap = max(1, lots_risk // 2)
            warn.append(f"complacency {complacency_score:.0f} — premium-sell size halved")

    # 4) portfolio heat headroom
    heat_room_rs = cfg.capital * cfg.max_portfolio_heat_pct - state["heat_rupees"]
    lots_heat = math.floor(heat_room_rs / max_loss_rs) if heat_room_rs > 0 else 0

    # 5) net Greek caps (how many lots before a band is breached)
    def cap_for(per_lot, current, limit):
        if per_lot == 0:
            return math.inf
        room = limit - abs(current) if (current + per_lot) * current >= 0 else limit + abs(current)
        room = limit - abs(current + 0)  # simple: remaining room toward the cap
        room = max(0.0, limit - abs(current))
        return math.floor(room / abs(per_lot)) if room > 0 else 0
    lots_delta = cap_for(trade.delta_per_lot, state["net_delta"], cfg.max_net_delta_units)
    lots_vega = cap_for(trade.vega_per_lot, state["net_vega"], cfg.max_net_vega_rupees)

    # final = min of all constraints
    caps = {"risk_per_trade": lots_risk, "complacency": comp_cap,
            "portfolio_heat": lots_heat, "net_delta": lots_delta, "net_vega": lots_vega}
    lots = int(min(caps.values()))
    binding = min(caps, key=caps.get)

    if lots <= 0:
        if binding == "risk_per_trade":
            msg = f"Trade Rejected: Max Loss per lot (₹{max_loss_rs:,.0f}) exceeds your {(cfg.risk_per_trade_pct*100):.1f}% Risk Budget (₹{risk_budget_rs:,.0f})"
        elif binding == "portfolio_heat":
            msg = f"Trade Rejected: Max Loss per lot (₹{max_loss_rs:,.0f}) exceeds remaining Portfolio Heat limit (₹{heat_room_rs:,.0f})"
        elif binding == "net_delta":
            msg = f"Trade Rejected: Trade Delta ({trade.delta_per_lot}) would breach the Net Delta limit ({cfg.max_net_delta_units})"
        elif binding == "net_vega":
            msg = f"Trade Rejected: Trade Vega (₹{trade.vega_per_lot}) would breach the Net Vega limit (₹{cfg.max_net_vega_rupees})"
        else:
            msg = f"sized to 0 — binding constraint: {binding}"
        return _veto(msg, state, cfg, caps=caps, structure=trade.structure)

    # projected post-trade state
    proj = {
        "heat_pct": (state["heat_rupees"] + lots * max_loss_rs) / cfg.capital,
        "net_delta": state["net_delta"] + lots * trade.delta_per_lot,
        "net_vega": state["net_vega"] + lots * trade.vega_per_lot,
    }
    return {
        "approved": True,
        "lots": lots,
        "structure": trade.structure,
        "binding_constraint": binding,
        "max_loss_per_lot_rs": round(max_loss_rs),
        "trade_max_loss_rs": round(lots * max_loss_rs),
        "trade_risk_pct": round(lots * max_loss_rs / cfg.capital, 4),
        "lot_caps": {k: (None if v == math.inf else int(v)) for k, v in caps.items()},
        "projected": {"heat_pct": round(proj["heat_pct"], 4),
                      "net_delta": round(proj["net_delta"], 1),
                      "net_vega": round(proj["net_vega"])},
        "warnings": warn,
        "is_premium_sell": sell,
    }


def _veto(reason, state, cfg, caps=None, structure=None):
    return {"approved": False, "lots": 0, "reason": reason,
            "structure": structure,
            "current_heat_pct": round(state["heat_pct"], 4),
            "current_net_delta": round(state["net_delta"], 1),
            "current_net_vega": round(state["net_vega"]),
            "lot_caps": ({k: (None if v == math.inf else int(v)) for k, v in caps.items()}
                         if caps else None)}


if __name__ == "__main__":
    import json
    cfg = RiskConfig()                                   # all assumed defaults
    book = [
        Position("existing bear call spread", lots=2, max_loss_pts=60,
                 delta_per_lot=-20, vega_per_lot=-1500),
    ]
    print("portfolio:", portfolio_state(book, cfg), "\n")

    # a) normal: bull put spread, neutral complacency
    bps = Trade("Bull put spread (sell premium below)", max_loss_pts=56,
                delta_per_lot=18, vega_per_lot=-1800)
    print("a) bull put spread, complacency 50:")
    print(json.dumps(size_trade(bps, book, cfg, complacency_score=50), indent=2), "\n")

    # b) same trade into a COMPLACENT tape -> blocked
    print("b) same, complacency 72 (complacent):")
    print(json.dumps(size_trade(bps, book, cfg, complacency_score=72), indent=2), "\n")

    # c) drawdown breached -> all new risk halted
    print("c) drawdown 11%:")
    print(json.dumps(size_trade(bps, book, cfg, complacency_score=50,
                                current_drawdown_pct=0.11), indent=2))
