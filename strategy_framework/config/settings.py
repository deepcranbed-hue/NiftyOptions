"""
strategy_framework/config/settings.py
=====================================
Central configuration for the directional-momentum strategy framework.

Everything tunable lives here so signal weights, gates, and DB location are in
one auditable place. Per DECISIONS.md D-MA-04, every threshold that has NOT been
calibrated on >= 60 sessions of history ships tagged PRIOR. This module carries
that tag alongside each number so the UI / logs can surface it honestly.
"""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# DB path resolution — DELEGATED (D-SC-06)
# --------------------------------------------------------------------------
# This used to carry its own copy of the Drive literal plus its own priority
# ladder — a sixth independent definition of one decision. The single source is
# db_config.py at the repo root: $NIFTY_DB / $OPTION_CHAINS_DB -> Google Drive
# (the primary data engine) -> repo-local copy.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from db_config import resolve_db_path   # noqa: E402  (re-exported for callers)


# --------------------------------------------------------------------------
# Trading grid / instrument constants  (D-MA-01)
# --------------------------------------------------------------------------
IST_OFFSET_MIN = 330            # UTC+5:30
SESSION_OPEN = "09:15"
SESSION_CLOSE = "15:30"
# Contract params come from the ONE canonical source (repo-root exchange_config.py)
# — never hardcode a lot size (CLAUDE.md DRY rule / SKILL.md HARD RULE 12).
from exchange_config import NIFTY_LOT_SIZE, STRIKE_STEP    # noqa: E402
LOT_SIZE = NIFTY_LOT_SIZE       # NFO NIFTY lot size (65 as of 1-Jan-2026)
RISK_FREE = 0.0655

# --------------------------------------------------------------------------
# Signal weights for the directional combiner.
# Positive score = bullish NIFTY, negative = bearish. Weights sum to 1.0.
# These are PRIOR (judgement-based) until walk-forward calibration exists.
# --------------------------------------------------------------------------
@dataclass
class SignalWeights:
    """The directional-combiner weights. The ROSTER and the DEFAULT weights come
    from `signals/registry.py` (the single source of truth — one SignalSpec per
    signal, carrying its family + default weight). This class only holds optional
    per-signal `overrides` on top of those defaults, so adding/removing a signal or
    changing a default weight is done in the registry and flows here automatically.
    All PRIOR until walk-forward calibration exists."""
    overrides: dict = field(default_factory=dict)
    tag: str = "PRIOR"

    def as_dict(self) -> dict:
        from ..signals.registry import default_weights
        w = default_weights()
        w.update(self.overrides or {})
        return w


# --------------------------------------------------------------------------
# Conviction gates: how strong the blended signal must be to act.
# --------------------------------------------------------------------------
@dataclass
class Gates:
    # |net_score| below this -> stand aside (no directional edge).
    min_abs_score: float = 0.15
    # blended confidence below this -> stand aside (signals disagree / stale).
    min_confidence: float = 0.35
    # if an event is within this many days -> veto new directional entries.
    event_veto_days: float = 1.0
    # VRP ratio (IV/RV) above this => premium is rich => prefer defined-risk
    # spreads over naked long options even when directional.
    vrp_rich_ratio: float = 1.15
    # VRP ratio below this => premium cheap => long options acceptable.
    vrp_cheap_ratio: float = 0.95
    # Cost-edge gate ("do-nothing threshold", after Salov's Maximum Profit
    # Strategy): require the expected 1σ move (in ₹/lot) to clear this multiple of
    # the structure's round-trip transaction cost, else STAND_ASIDE. 0.0 = OFF.
    # The paper's do-nothing threshold corresponds to ≈2×cost. PRIOR until
    # calibrated (D-MA-04).
    min_edge_cost_mult: float = 0.0
    tag: str = "PRIOR"


# --------------------------------------------------------------------------
# Strike-selection preferences for directional structures.
# --------------------------------------------------------------------------
@dataclass
class StrikeConfig:
    # width of a vertical spread, in strikes (x STRIKE_STEP points).
    spread_width_strikes: int = 2
    # how far OTM to place a long directional option, in strikes.
    long_otm_strikes: int = 1
    # minimum OI for a strike to be considered liquid enough to trade.
    min_oi: float = 50_000.0
    # Iron-condor short placement: target distance of each short from spot as a
    # multiple of the expected move to expiry (≈1σ). Keeps shorts sensibly OTM for
    # the DTE/vol instead of near-ATM. An OI wall is used only if it falls within
    # [1±tol]×target. PRIOR until calibrated (D-MA-04).
    condor_short_em_mult: float = 1.0
    condor_wall_tol: float = 0.4


@dataclass
class HedgeConfig:
    """Tail-hedge / max-drawdown insurance, driven by the derisk_liquidity overlay.

    When the liquidity-derisk intensity clears `trigger`, the desk buys a long OTM
    put (uncapped downside protection). The strike is placed between `sigma_hi`
    (further OTM, cheaper — low intensity) and `sigma_lo` (closer, costlier — high
    intensity) multiples of the expected move (≈1σ). Size scales from 1 lot at the
    trigger up to `max_lots` at intensity 1.0. `spread_sigma` (if set) also prices
    a cost-reduced put SPREAD variant (short a further put) for reference."""
    trigger: float = 0.45
    sigma_lo: float = 1.0        # strike distance at intensity 1.0 (closer/costlier)
    sigma_hi: float = 1.5        # strike distance at the trigger  (further/cheaper)
    max_lots: int = 3
    spread_sigma: float = 2.5    # short leg of the reference put-spread, in σ
    enabled: bool = True


@dataclass
class CostModel:
    """Transaction costs in RUPEES, charged per option leg per transaction.

    A leg is charged once when opened, once when closed, and once each time an
    adjustment touches it. ₹20/leg matches a typical Indian discount-broker flat
    fee per order. `slippage_pts` optionally adds half-tick slippage per leg
    (in points, converted to rupees via lot size)."""
    per_leg_inr: float = 20.0
    # DEFAULT 1.0pt/leg: crossing the bid-ask is a REAL cost every fill pays. The old
    # default of 0 made backtests fictionally frictionless — the July-2026 condor
    # studies showed 1pt/leg (₹85/leg all-in at lot 65) flips the verdict on every
    # high-frequency adjustment scheme. Set 0.0 explicitly only to study the
    # frictionless ceiling, never to report results.
    slippage_pts: float = 1.0

    def legs_cost_inr(self, n_legs: int, lot_size: int) -> float:
        return n_legs * (self.per_leg_inr + self.slippage_pts * lot_size)


@dataclass
class MomentumWindow:
    """THE single source of truth for the price-return lookback used by every
    momentum-style signal (rel_volume, futures_flow, vol_index,
    heavyweight_leadership). One knob, so all of them measure the SAME window and
    their correlations / ICs stay apples-to-apples (CLAUDE.md DRY rule).

    Why the scale must move with the window
    ---------------------------------------
    Returns grow roughly as √t, so a longer window produces bigger numbers. Measured
    on NIFTY 1m bars: median |return| = 0.053% at 15 bars, 0.074% at 30, 0.094% at
    60 — close to the √t prediction (√2 = 1.41 vs observed 1.40, √4 = 2.0 vs 1.78).

    If `scale` stayed fixed while the window grew, the signal would simply read
    hotter and saturate more often — conflating "longer window" with "stronger
    signal". Scaling the tanh denominator by √(n/REF) holds the SCORE DISTRIBUTION
    roughly constant, so changing the window changes the signal-to-noise ratio (the
    point of the exercise) without changing how hot the signal reads.

        scale(n) = base_scale × √(n / 15)

    `base_scale` is the calibrated value at the 15-bar reference; the per-signal
    factors preserve each signal's existing relative sensitivity, so switching the
    global window does not silently re-tune one signal against another.

    Where the value lives
    ---------------------
    `lookback_min=None` (the default) means "follow the persisted runtime setting"
    in config/runtime.py, read at CALL time. That is what makes this setting global:
    `settings.DEFAULT` and `api._CFG` are constructed once at import, so a window
    captured at construction would never reach them — every read goes through
    `bars()` instead. Pass an explicit `lookback_min` only to PIN a window for a
    test or a one-off sweep (the feature backfill does this).
    """
    lookback_min: int | None = None      # None = follow the persisted global setting
    ref_bars: int = 15                   # reference window the base scales are calibrated at
    options: tuple = (5, 15, 30, 60)     # selectable in the UI (minutes)

    # base tanh scale per signal AT `ref_bars` — these reproduce today's behaviour
    # exactly when lookback_min == 15 for the 15-bar signals.
    base_scale: dict = field(default_factory=lambda: {
        "rel_volume": 0.12,
        "futures_flow": 0.12,
        "vol_index": 0.15 / (30 / 15) ** 0.5,             # was 0.15 at 30 bars
        "heavyweight_leadership": 0.60 / (60 / 15) ** 0.5,  # was 0.60 at 60 bars
    })

    def minutes(self) -> int:
        """The active window in minutes — the pinned value, else the global setting."""
        if self.lookback_min is not None:
            return int(self.lookback_min)
        from . import runtime
        return runtime.get_lookback_min()

    def bars(self) -> int:
        return max(2, self.minutes())

    @property
    def pinned(self) -> bool:
        return self.lookback_min is not None

    def scale_for(self, signal: str, bars: int | None = None) -> float:
        """tanh scale for `signal` at the active (or given) window."""
        n = bars if bars is not None else self.bars()
        base = self.base_scale.get(signal, 0.12)
        return float(base * (n / float(self.ref_bars)) ** 0.5)

    def as_dict(self) -> dict:
        n = self.bars()
        return {"lookback_min": self.minutes(), "bars": n, "pinned": self.pinned,
                "ref_bars": self.ref_bars, "options": list(self.options),
                "scales": {s: round(self.scale_for(s, n), 4) for s in self.base_scale},
                "rule": "scale(n) = base × √(n/ref) — holds the score distribution "
                        "stable as the window changes"}


@dataclass
class FrameworkConfig:
    db_path: str = field(default_factory=resolve_db_path)
    weights: SignalWeights = field(default_factory=SignalWeights)
    gates: Gates = field(default_factory=Gates)
    strikes: StrikeConfig = field(default_factory=StrikeConfig)
    costs: CostModel = field(default_factory=CostModel)
    hedge: HedgeConfig = field(default_factory=HedgeConfig)
    momentum: MomentumWindow = field(default_factory=MomentumWindow)
    lot_size: int = LOT_SIZE

    def summary(self) -> dict:
        return {"db_path": self.db_path, "lot_size": self.lot_size,
                "weights": self.weights.as_dict(),
                "gates": vars(self.gates), "strikes": vars(self.strikes),
                "costs": vars(self.costs), "hedge": vars(self.hedge),
                "momentum_window": self.momentum.as_dict()}


DEFAULT = FrameworkConfig()
