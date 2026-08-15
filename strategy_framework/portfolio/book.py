"""
strategy_framework/portfolio/book.py
====================================
A mixed-instrument portfolio: option strategies, futures, and stocks in one book,
with combined P&L and net delta.

Kept separate from the project's existing portfolio.py (which stores option legs
only) so the two don't collide — this book persists to its own state file and
understands linear (delta-1) instruments as well as option structures.

Position kinds
--------------
  option_strategy : {legs: [(side,strike,sign), ...], entry_prices: {..}, family}
  future          : {symbol, entry_price, qty, lot_size}   (linear, delta ≈ 1/unit)
  stock           : {symbol, entry_price, qty}             (linear, delta = 1/share)

Every position carries entry prices so P&L can be marked against any later
pricing context without re-reading the entry snapshot.
"""
from __future__ import annotations
import os, json, time, uuid
from dataclasses import dataclass, field, asdict

_STATE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                      ".state", "strategy_desk_portfolio.json"))


@dataclass
class Position:
    id: str
    kind: str                      # option_strategy | future | stock
    label: str
    payload: dict
    created_at: float = field(default_factory=time.time)
    status: str = "open"

    def as_dict(self) -> dict:
        return asdict(self)


class Book:
    def __init__(self, path: str = _STATE):
        self.path = path
        self.positions: list[Position] = []
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    raw = json.load(f)
                self.positions = [Position(**p) for p in raw.get("positions", [])]
            except Exception:
                self.positions = []

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({"positions": [p.as_dict() for p in self.positions]}, f, indent=2)

    # ---- mutations -----------------------------------------------------
    def add_option_strategy(self, family: str, legs: list, entry_prices: dict,
                            lot_size: int, label: str = None,
                            exchange: str = "NFO", expiry: str = None) -> Position:
        pos = Position(id=uuid.uuid4().hex[:8], kind="option_strategy",
                       label=label or family,
                       payload={"family": family,
                                "legs": [list(l) for l in legs],
                                "entry_prices": {f"{s}:{k}": v
                                                 for (s, k), v in entry_prices.items()},
                                "lot_size": lot_size,
                                "exchange": exchange, "expiry": expiry})
        self.positions.append(pos); self._save(); return pos

    def add_future(self, symbol: str, entry_price: float, qty: int,
                   lot_size: int, label: str = None, exchange: str = "NFO",
                   expiry: str = None) -> Position:
        pos = Position(id=uuid.uuid4().hex[:8], kind="future",
                       label=label or f"{symbol} future ({exchange})",
                       payload={"symbol": symbol, "entry_price": entry_price,
                                "qty": qty, "lot_size": lot_size,
                                "exchange": exchange, "expiry": expiry})
        self.positions.append(pos); self._save(); return pos

    def add_stock(self, symbol: str, entry_price: float, qty: int,
                  label: str = None, exchange: str = "NSE") -> Position:
        pos = Position(id=uuid.uuid4().hex[:8], kind="stock",
                       label=label or f"{symbol} ({exchange})",
                       payload={"symbol": symbol, "entry_price": entry_price,
                                "qty": qty, "exchange": exchange})
        self.positions.append(pos); self._save(); return pos

    def remove(self, pos_id: str) -> bool:
        n = len(self.positions)
        self.positions = [p for p in self.positions if p.id != pos_id]
        if len(self.positions) != n:
            self._save(); return True
        return False

    def clear(self):
        self.positions = []; self._save()

    def list(self) -> list[dict]:
        return [p.as_dict() for p in self.positions]
