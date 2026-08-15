"""
strategy_framework/portfolio/valuation.py
=========================================
Mark a mixed book (option strategies + futures + stocks) to a pricing context and
report per-position and combined P&L in rupees, plus a net delta.

Pricing context
---------------
  chain         : {call_ltp: {strike:ltp}, put_ltp: {..}, spot: float}  (or None)
  symbol_prices : {SYMBOL: last_price}   for futures / stocks / spot
  spot          : current NIFTY spot

Net delta convention: rupees of P&L per +1 NIFTY index point.
  option : expiry delta of each leg (call +1 if ITM else 0; put -1 if ITM else 0),
           × sign × lot_size.  (A simple, robust slope; refine with greeks later.)
  future : qty × lot_size            (tracks the index 1:1)
  stock  : qty × price / spot        (beta ≈ 1 approximation — flagged in output)
"""
from __future__ import annotations

from ..config.settings import LOT_SIZE   # single source of truth (exchange_config)


def _opt_price(chain, side, strike):
    if not chain:
        return None
    book = chain.get("call_ltp" if side == "call" else "put_ltp", {})
    # keys may be float or str depending on JSON round-trips
    return book.get(strike, book.get(str(strike), book.get(float(strike), None)))


def _intrinsic(side, strike, spot):
    return max(spot - strike, 0) if side == "call" else max(strike - spot, 0)


def value_option_strategy(payload, chain, spot):
    legs = payload["legs"]
    entry = payload["entry_prices"]
    lot = payload.get("lot_size", LOT_SIZE)
    pnl_pts = 0.0
    delta_pts = 0.0
    marked = True
    for side, strike, sign in legs:
        e = entry.get(f"{side}:{strike}", entry.get(f"{side}:{float(strike)}"))
        cur = _opt_price(chain, side, strike)
        if cur is None or cur <= 0:
            cur = _intrinsic(side, strike, spot); marked = False
        if e is None:
            e = cur
        pnl_pts += sign * (cur - e)
        # expiry delta of the leg
        if side == "call":
            d = 1.0 if spot > strike else 0.0
        else:
            d = -1.0 if spot < strike else 0.0
        delta_pts += sign * d
    entry_net = sum(sign * (entry.get(f"{s}:{k}", entry.get(f"{s}:{float(k)}")) or 0.0)
                    for s, k, sign in legs)
    cur_net = entry_net + pnl_pts
    return {"pnl_rupees": pnl_pts * lot, "delta_index_rupees": delta_pts * lot,
            "marked": marked, "qty": f"{len(legs)} legs × {lot}",
            "entry": round(entry_net, 2), "current": round(cur_net, 2)}


def value_future(payload, symbol_prices, spot):
    sym = payload["symbol"]
    cur = symbol_prices.get(sym, spot)        # NIFTY future ~ spot if not supplied
    e = payload["entry_price"]; qty = payload["qty"]; lot = payload.get("lot_size", LOT_SIZE)
    return {"pnl_rupees": (cur - e) * qty * lot,
            "delta_index_rupees": qty * lot, "marked": sym in symbol_prices,
            "qty": f"{qty} × {lot}", "entry": round(e, 2), "current": round(cur, 2)}


def value_stock(payload, symbol_prices, spot):
    sym = payload["symbol"]
    cur = symbol_prices.get(sym, payload["entry_price"])
    e = payload["entry_price"]; qty = payload["qty"]
    beta_approx = (cur / spot) if spot else 0.0
    return {"pnl_rupees": (cur - e) * qty,
            "delta_index_rupees": qty * beta_approx,   # beta≈1 assumption
            "marked": sym in symbol_prices, "beta_note": "beta≈1",
            "qty": str(qty), "entry": round(e, 2), "current": round(cur, 2)}


def value_book(positions: list, chain: dict | None, symbol_prices: dict,
               spot: float) -> dict:
    lines = []
    total_pnl = 0.0
    net_delta = 0.0
    any_unmarked = False
    for p in positions:
        kind, payload = p["kind"], p["payload"]
        if kind == "option_strategy":
            v = value_option_strategy(payload, chain, spot)
        elif kind == "future":
            v = value_future(payload, symbol_prices, spot)
        elif kind == "stock":
            v = value_stock(payload, symbol_prices, spot)
        else:
            continue
        total_pnl += v["pnl_rupees"]
        net_delta += v["delta_index_rupees"]
        any_unmarked = any_unmarked or (not v.get("marked", True))
        lines.append({"id": p["id"], "label": p["label"], "kind": kind,
                      "qty": v.get("qty"), "entry": v.get("entry"), "current": v.get("current"),
                      "pnl_rupees": round(v["pnl_rupees"], 0),
                      "delta_index_rupees": round(v["delta_index_rupees"], 1),
                      "marked_live": v.get("marked", True)})
    return {"lines": lines, "total_pnl_rupees": round(total_pnl, 0),
            "net_delta_rupees_per_point": round(net_delta, 1),
            "spot": spot, "any_unmarked": any_unmarked,
            "note": ("some legs marked to intrinsic (no live/last price)"
                     if any_unmarked else "all positions marked to price")}
