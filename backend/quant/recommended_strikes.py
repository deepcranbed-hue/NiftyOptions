"""
recommended_strikes.py
----------------------
Takes a high-level strategy "family" (from the macro suggester) and translates it
into a concrete set of default legs (strikes & sides). It anchors short legs
at/behind major OI walls (support/resistance) or uses the Expected Move (RND).
"""

from __future__ import annotations
import math

def recommend_strikes(family: str, spot: float, expected_move: float,
                      support_wall: float, resistance_wall: float,
                      strikes: list[float]) -> list[tuple[str, float, int]]:
    """
    Returns [(side, strike, sign)] for the given family.
    Uses OI walls for credit trades; Expected Move for debit trades.
    """
    atm = min(strikes, key=lambda k: abs(k - spot)) if strikes else round(spot / 50) * 50
    
    # Fallbacks if walls are missing or nonsensical
    if not support_wall or support_wall >= spot:
        support_wall = atm - expected_move
    if not resistance_wall or resistance_wall <= spot:
        resistance_wall = atm + expected_move
        
    def snap(target: float) -> float:
        """Snap a target float to the nearest valid strike."""
        return min(strikes, key=lambda k: abs(k - target)) if strikes else round(target / 50) * 50

    legs = []
    
    if family == "iron_condor":
        # Short strangles at the walls, buy wings 100pts further out
        sp = snap(support_wall)
        sc = snap(resistance_wall)
        bp = snap(sp - 100)
        bc = snap(sc + 100)
        legs = [("put", bp, +1), ("put", sp, -1), ("call", sc, -1), ("call", bc, +1)]
        
    elif family == "iron_butterfly":
        # Pin short straddle at ATM, buy wings at Expected Move
        bp = snap(atm - expected_move)
        bc = snap(atm + expected_move)
        legs = [("put", bp, +1), ("put", atm, -1), ("call", atm, -1), ("call", bc, +1)]
        
    elif family == "bull_put_spread":
        # Short put at support wall, long put 100pts below
        sp = snap(support_wall)
        bp = snap(sp - 100)
        legs = [("put", bp, +1), ("put", sp, -1)]
        
    elif family == "bear_call_spread":
        # Short call at resistance wall, long call 100pts above
        sc = snap(resistance_wall)
        bc = snap(sc + 100)
        legs = [("call", sc, -1), ("call", bc, +1)]
        
    elif family == "bull_call_spread":
        # Buy ATM call, sell expected move
        bc = atm
        sc = snap(atm + expected_move)
        legs = [("call", bc, +1), ("call", sc, -1)]
        
    elif family == "bear_put_spread":
        # Buy ATM put, sell expected move
        bp = atm
        sp = snap(atm - expected_move)
        legs = [("put", bp, +1), ("put", sp, -1)]
        
    elif family == "long_straddle":
        legs = [("call", atm, +1), ("put", atm, +1)]
        
    elif family == "short_straddle":
        legs = [("call", atm, -1), ("put", atm, -1)]
        
    elif family == "long_strangle":
        # Buy expected move
        c = snap(atm + expected_move)
        p = snap(atm - expected_move)
        legs = [("call", c, +1), ("put", p, +1)]
        
    elif family == "short_strangle":
        # Sell at walls
        c = snap(resistance_wall)
        p = snap(support_wall)
        legs = [("call", c, -1), ("put", p, -1)]
        
    elif family == "long_call":
        legs = [("call", atm, +1)]
        
    elif family == "long_put":
        legs = [("put", atm, +1)]

    return legs
