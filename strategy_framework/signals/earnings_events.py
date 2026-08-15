"""
strategy_framework/signals/earnings_events.py
=============================================
Event / earnings gate.

This is NOT a directional signal — it is a veto and a structure hint. Binary
events (CPI, RBI, Fed, big-cap earnings) inflate premium and inject gap risk
that intraday momentum cannot forecast. Ahead of a high-impact event within the
veto window, the framework should stand aside or, if it must act, prefer
defined-risk spreads over naked long options.

Uses backend/quant modules (event_calendar, india_macro) when importable; falls
back to a neutral "no known event" posture otherwise so the framework never
crashes on a missing optional dependency.
"""
from __future__ import annotations
from datetime import datetime, timezone
from .base import Signal


def _events_via_engine(now_dt):
    try:
        import sys, os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from event_calendar import upcoming_events, event_proximity  # noqa
    except Exception:
        return None
    try:
        evs = upcoming_events(today=now_dt.date())
        prox = event_proximity(evs, today=now_dt.date())
        return prox
    except Exception:
        return None


def _earnings_via_engine(now_dt):
    try:
        import sys, os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from backend.quant.india_macro import earnings_regime  # noqa
        return earnings_regime(now_dt.date())
    except Exception:
        return None


def compute(da, now: str, ctx: dict, veto_days: float = 1.0) -> Signal:
    now_dt = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(timezone.utc)
    detail: dict = {"veto": False, "reason": None}

    prox = _events_via_engine(now_dt)
    if prox:
        days_away = prox.get("days_away")
        detail["nearest_event"] = prox.get("nearest_high_impact") or prox.get("name")
        detail["days_away"] = days_away
        detail["action"] = prox.get("action")
        if days_away is not None and days_away <= veto_days:
            detail["veto"] = True
            detail["reason"] = f"high-impact event in {days_away}d"

    earn = _earnings_via_engine(now_dt)
    if earn and earn.get("active"):
        detail["earnings_season"] = True
        # earnings season doesn't hard-veto but flags gap risk for structure choice.
        detail.setdefault("reason", "earnings season active")

    # score/confidence carry no direction; the bundle/constructor read `veto`.
    status = "OK" if prox is not None else "NO_DATA"
    return Signal("earnings_events", 0.0, 0.0, "PRIOR", status=status, detail=detail)
