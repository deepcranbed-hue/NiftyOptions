"""
exchange_config.py
------------------
SINGLE SOURCE OF TRUTH for NSE / NFO contract parameters (lot size, strike step).

Import the value from here — do NOT hardcode a lot size anywhere else in the repo
(see CLAUDE.md → DRY rule and strategy_framework/SKILL.md HARD RULE 12). NSE revises
the NIFTY F&O lot size periodically; when it changes, change it HERE and nowhere else.

Usage (works from either package, run from the repo root):
    from exchange_config import NIFTY_LOT_SIZE      # -> 65
    from exchange_config import ExchangeConfig       # dataclass, if you want strike_step too
"""
from __future__ import annotations
from dataclasses import dataclass

# NIFTY F&O lot size (units per contract).
#   75  through 2025
#   65  effective 1-Jan-2026   <-- current
NIFTY_LOT_SIZE: int = 65

# NIFTY option strike spacing (points).
STRIKE_STEP: float = 50.0


@dataclass(frozen=True)
class ExchangeConfig:
    """NSE/NFO contract parameters. `ExchangeConfig()` returns the current defaults."""
    lot_size: int = NIFTY_LOT_SIZE
    strike_step: float = STRIKE_STEP


# Convenience singleton.
NIFTY = ExchangeConfig()
