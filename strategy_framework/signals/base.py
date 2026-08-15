"""
strategy_framework/signals/base.py
==================================
Common vocabulary for every signal module.

A Signal is the atomic unit the combiner consumes. The contract is deliberately
narrow so signals stay comparable and auditable:

    score       float in [-1, +1]   ( + bullish NIFTY, - bearish, 0 neutral )
    confidence  float in [ 0,  1]   ( how much to trust `score` right now )
    tag         "PRIOR" | "FITTED"  ( calibration status, D-MA-04 )
    detail      dict                ( raw numbers behind the score, for tracing )

Signals never decide to trade and never size. They only describe a directional
lean and how confident they are. The combiner blends them; the gates decide.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def squash(z: float, scale: float = 2.0) -> float:
    """Map an unbounded z-score to (-1, 1) via tanh — the same continuous
    strength transform Global Cues v2 uses (D-GC-01)."""
    return math.tanh(z / scale)


@dataclass
class Signal:
    name: str
    score: float = 0.0
    confidence: float = 0.0
    tag: str = "PRIOR"
    detail: dict = field(default_factory=dict)
    status: str = "OK"          # OK | INSUFFICIENT_HISTORY | NO_DATA | STALE

    def __post_init__(self):
        self.score = clamp(self.score)
        self.confidence = clamp(self.confidence, 0.0, 1.0)

    @classmethod
    def no_data(cls, name: str, why: str = "no data") -> "Signal":
        return cls(name=name, score=0.0, confidence=0.0, tag="PRIOR",
                   status="NO_DATA", detail={"note": why})

    def as_dict(self) -> dict:
        return {"name": self.name, "score": round(self.score, 4),
                "confidence": round(self.confidence, 4), "tag": self.tag,
                "status": self.status, "detail": self.detail}


@dataclass
class SignalBundle:
    """All signals evaluated as-of a single decision timestamp."""
    now: str
    spot: float
    signals: dict = field(default_factory=dict)   # name -> Signal
    context: dict = field(default_factory=dict)    # spot, vix, expiry, dte, ...

    def add(self, sig: Signal):
        self.signals[sig.name] = sig

    def get(self, name: str) -> Signal:
        return self.signals.get(name, Signal.no_data(name))

    def as_dict(self) -> dict:
        return {"now": self.now, "spot": self.spot, "context": self.context,
                "signals": {k: v.as_dict() for k, v in self.signals.items()}}
