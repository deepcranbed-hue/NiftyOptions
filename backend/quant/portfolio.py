"""
portfolio.py
------------
Manages the tracked portfolio of trades. Allows saving recommended or optimized
structures, and valuing them against any snapshot (capture) to generate P&L
and attribution.
"""

from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from backend.quant.state_manager import STATE_DIR
from exchange_config import NIFTY_LOT_SIZE   # single source of truth for lot size

PORTFOLIO_FILE = os.path.join(STATE_DIR, "portfolio.json")

def _load_portfolio() -> list[dict]:
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def _save_portfolio(data: list[dict]):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2)

def add_position(legs: list[tuple[str, float, int]], expiry: str, entry_capture_id: int, 
                 source: str, lots: int = 1, lot_size: int = NIFTY_LOT_SIZE, lineage: dict = None) -> str:
    """Adds a new position to the portfolio. Returns the position ID."""
    portfolio = _load_portfolio()
    
    pos_id = str(uuid.uuid4())
    pos = {
        "id": pos_id,
        "status": "open",
        "legs": legs, # [(side, strike, sign)]
        "expiry": expiry,
        "entry_capture_id": entry_capture_id,
        "source": source,
        "lots": lots,
        "lot_size": lot_size,
        "lineage": lineage or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None
    }
    
    portfolio.append(pos)
    _save_portfolio(portfolio)
    return pos_id

def get_open_positions() -> list[dict]:
    return [p for p in _load_portfolio() if p["status"] == "open"]

def close_position(pos_id: str):
    """Marks a position as closed."""
    portfolio = _load_portfolio()
    for p in portfolio:
        if p["id"] == pos_id:
            p["status"] = "closed"
            p["closed_at"] = datetime.now(timezone.utc).isoformat()
            break
    _save_portfolio(portfolio)
    return True
