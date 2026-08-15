import math
from typing import List, Dict, Any, Tuple
from datetime import datetime

# ---- single source for Black-Scholes IV inversion ---------------------------------
# The inverter lives in strategy_framework/bs.py. This module is an ADAPTER over that
# one implementation — NOT a second implementation.
import os as _os, sys as _sys
_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
from strategy_framework.bs import implied_vol as _bs_implied_vol

# NOTE: local bs_price / bs_vega were removed with the Newton solver they served.
# All BS math now comes from strategy_framework/bs.py (imported above).
def implied_volatility(cp_flag, target_value, S, K, T, r, max_iter=100, precision=1.0e-5):
    """IV from a market price. Returns None when it cannot be solved.

    Delegates to strategy_framework.bs.implied_vol (Newton with a bisection fallback
    and a convergence check). The previous local Newton had two failure modes that
    both produced numbers indistinguishable from real readings, and this function
    WRITES call_iv / put_iv into the store:

      * `return 0.0` on target<=0 or sub-intrinsic. Deep-ITM strikes routinely print
        an LTP below intrinsic (stale last trade on an untraded strike) — measured at
        8 of 21 puts on a real near-expiry chain. Each was stored as IV 0.00.
      * `return v` after max_iter with NO convergence check. Newton with no bracket
        diverges on low-vega strikes, hits the max(0.0001, v) floor and returns it:
        K=26000, px=0.05, T=0.5d gave 0.000100 where bs.py and rnd.py both give 0.4549.

    None is the honest answer and the callers already expect it — strike_map seeds
    call_iv/put_iv to None and the row builder branches on `is not None`.
    `max_iter` / `precision` are retained for signature compatibility.
    """
    if target_value is None or target_value <= 0:
        return None
    sigma = _bs_implied_vol(target_value, S, K, T, r, cp_flag == 'c')
    return None if sigma is None else float(sigma)

def process_breeze_chain(breeze_data: List[Dict[str, Any]], days_to_expiry: float) -> Tuple[List[Dict[str, Any]], float]:
    """
    Transforms raw breeze get_option_chain_quotes output into an array of OptionRow dicts.
    Returns: (list_of_option_rows, spot_price)
    """
    strike_map = {}
    spot_price = 0.0
    r = 0.0655 # Risk-free rate used in the system
    T = max(days_to_expiry / 365.0, 0.001)

    for item in breeze_data:
        strike_val = item.get("strike_price")
        if strike_val is None:
            continue
        strike = float(strike_val)
            
        if spot_price == 0.0 and item.get("spot_price"):
            spot_price = float(item.get("spot_price"))
            
        if strike not in strike_map:
            strike_map[strike] = {
                "strike": strike,
                "call_ltp": None, "call_oi": None, "call_oichg": None, "call_iv": None,
                "call_volume": None, "call_bid": None, "call_bid_qty": None, "call_ask": None, "call_ask_qty": None,
                "put_ltp": None, "put_oi": None, "put_oichg": None, "put_iv": None,
                "put_volume": None, "put_bid": None, "put_bid_qty": None, "put_ask": None, "put_ask_qty": None
            }
            
        right = item.get("right", "").lower()
        ltp = float(item.get("ltp")) if item.get("ltp") is not None else None
        oi = int(item.get("open_interest")) if item.get("open_interest") is not None else None
        oi_chg = int(item.get("chnge_oi")) if item.get("chnge_oi") is not None else None
        
        # Calculate percentage change for oi_chg
        oi_chg_pct = None
        if oi is not None and oi_chg is not None:
            prev_oi = oi - oi_chg
            oi_chg_pct = round((oi_chg / prev_oi) * 100, 1) if prev_oi > 0 else 0.0
        
        if right == "call":
            strike_map[strike]["call_ltp"] = ltp
            strike_map[strike]["call_oi"] = oi
            strike_map[strike]["call_oichg"] = oi_chg_pct
            strike_map[strike]["call_volume"] = int(item.get("total_quantity_traded")) if item.get("total_quantity_traded") is not None else None
            
            bid = float(item.get("best_bid_price")) if item.get("best_bid_price") is not None else None
            ask = float(item.get("best_offer_price")) if item.get("best_offer_price") is not None else None
            
            strike_map[strike]["call_bid"] = bid
            strike_map[strike]["call_bid_qty"] = int(item.get("best_bid_quantity")) if item.get("best_bid_quantity") is not None else None
            strike_map[strike]["call_ask"] = ask
            strike_map[strike]["call_ask_qty"] = int(item.get("best_offer_quantity")) if item.get("best_offer_quantity") is not None else None
            
            if ltp is not None and ltp > 0 and spot_price > 0:
                iv = implied_volatility('c', ltp, spot_price, strike, T, r)
                strike_map[strike]["call_iv"] = round(iv * 100, 2) if iv is not None else None
        elif right == "put":
            strike_map[strike]["put_ltp"] = ltp
            strike_map[strike]["put_oi"] = oi
            strike_map[strike]["put_oichg"] = oi_chg_pct
            strike_map[strike]["put_volume"] = int(item.get("total_quantity_traded")) if item.get("total_quantity_traded") is not None else None
            
            bid = float(item.get("best_bid_price")) if item.get("best_bid_price") is not None else None
            ask = float(item.get("best_offer_price")) if item.get("best_offer_price") is not None else None
            
            strike_map[strike]["put_bid"] = bid
            strike_map[strike]["put_bid_qty"] = int(item.get("best_bid_quantity")) if item.get("best_bid_quantity") is not None else None
            strike_map[strike]["put_ask"] = ask
            strike_map[strike]["put_ask_qty"] = int(item.get("best_offer_quantity")) if item.get("best_offer_quantity") is not None else None
            
            if ltp is not None and ltp > 0 and spot_price > 0:
                iv = implied_volatility('p', ltp, spot_price, strike, T, r)
                strike_map[strike]["put_iv"] = round(iv * 100, 2) if iv is not None else None
                
    # Format into OptionRow list expected by frontend
    rows = []
    for strike in sorted(strike_map.keys()):
        data = strike_map[strike]
        
        # Calculate derived mid for Call and Put
        call_bid = data["call_bid"]
        call_ask = data["call_ask"]
        call_mid = None
        call_quote_state = "NO_QUOTE"
        
        if call_bid is not None and call_ask is not None:
            if call_bid > 0 and call_ask > call_bid:
                call_mid = (call_bid + call_ask) / 2.0
                call_quote_state = "TWO_SIDED"
            elif call_bid <= 0 and call_ask > 0:
                call_quote_state = "ONE_SIDED_ASK"
            elif call_ask <= 0 and call_bid > 0:
                call_quote_state = "ONE_SIDED_BID"
            elif call_bid >= call_ask and call_bid > 0:
                call_quote_state = "CROSSED_LOCKED"
                
        put_bid = data["put_bid"]
        put_ask = data["put_ask"]
        put_mid = None
        put_quote_state = "NO_QUOTE"
        
        if put_bid is not None and put_ask is not None:
            if put_bid > 0 and put_ask > put_bid:
                put_mid = (put_bid + put_ask) / 2.0
                put_quote_state = "TWO_SIDED"
            elif put_bid <= 0 and put_ask > 0:
                put_quote_state = "ONE_SIDED_ASK"
            elif put_ask <= 0 and put_bid > 0:
                put_quote_state = "ONE_SIDED_BID"
            elif put_bid >= put_ask and put_bid > 0:
                put_quote_state = "CROSSED_LOCKED"
        
        # Blended IV calculation
        c_iv = data["call_iv"] if data["call_iv"] is not None else 0.0
        p_iv = data["put_iv"] if data["put_iv"] is not None else 0.0
        row_iv = 0.0
        if c_iv > 0 and p_iv > 0:
            row_iv = (c_iv + p_iv) / 2.0
        else:
            row_iv = c_iv if c_iv > 0 else p_iv
            
        rows.append({
            "strike": strike,
            "call_ltp": data["call_ltp"],
            "call_oi": data["call_oi"],
            "call_oichg": data["call_oichg"],
            "call_volume": data["call_volume"],
            "call_bid": call_bid,
            "call_bid_qty": data["call_bid_qty"],
            "call_ask": call_ask,
            "call_ask_qty": data["call_ask_qty"],
            "call_mid": call_mid,
            "call_quote_state": call_quote_state,
            "put_ltp": data["put_ltp"],
            "put_oi": data["put_oi"],
            "put_oichg": data["put_oichg"],
            "put_volume": data["put_volume"],
            "put_bid": put_bid,
            "put_bid_qty": data["put_bid_qty"],
            "put_ask": put_ask,
            "put_ask_qty": data["put_ask_qty"],
            "put_mid": put_mid,
            "put_quote_state": put_quote_state,
            "iv": row_iv
        })
        
    return rows, spot_price
