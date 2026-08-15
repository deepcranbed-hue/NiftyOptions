"""
strategy_framework/signals/registry.py
======================================
THE single source of truth for the signal roster.

Every signal in the framework is declared exactly ONCE here, as a `SignalSpec`
carrying its name, its `compute` function, its FAMILY (characteristic grouping),
its default blend weight, and flags for how it participates. Nothing else in the
codebase may hardcode a signal list — the bundle (what to compute), the regime
blender (which signals vote, which get the time-of-day boost), the config weights,
the analytics roster, and the signal-study tool ALL derive from this file.

Add a new signal by (1) writing its module in this folder with a `compute(da, now,
ctx)` function, and (2) adding ONE `SignalSpec` row below. It then flows everywhere
automatically. See CLAUDE.md (DRY rule) and SKILL.md HARD RULE 13.

Fields
------
  kind           : "directional" (a market-direction vote) | "gate" (veto/modulator)
                   | "overlay" (risk hedge, not a directional vote)
  family         : characteristic grouping (leadership / trend / internals / …) —
                   correlated signals should share a family so they share a budget.
  default_weight : default blend weight (directional signals only; 0.0 = candidate,
                   evaluated + studied but not yet voting).
  blended        : True if it enters the net-score blend (the weighted core).
  momentum_boost : True if its confidence is amplified in the opening/power-hour.
  label          : human display name — the UI reads THIS, it holds no label map.
  method         : one-paragraph description of the computation, shown in the desk
                   panel's signal breakdown.
  detail_keys    : preferred ORDER for the signal's detail fields in the UI. Empty
                   tuple = render whatever the signal emits, in its own order (so a
                   new detail field appears with no frontend change).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

from . import (heavyweight_leadership, heavyweight_leadership_persistent,
               technical_momentum, global_momentum,
               breadth_oi, skew_rnd, vrp, vwap, vol_index, rel_volume,
               crude_energy, usdinr, global_gap, futures_basis, futures_calendar,
               futures_flow, futures_oi_regime, strike_role,
               pin_pressure, oi_migration, straddle_flow, oi_dispersion, oi_entropy,
               adx, choppiness, breadth_quality, dealer_center,
               time_of_day, earnings_events, derisk_preopen, derisk_liquidity)

# ── family taxonomy (characteristic grouping) ─────────────────────────────────
LEADERSHIP = "leadership"; TREND = "trend"; MACRO = "macro"; INTERNALS = "internals"
DERIVATIVES = "derivatives"; VOLATILITY = "volatility"; PARTICIPATION = "participation"
GAMMA = "gamma"; GATE = "gate"; OVERLAY = "overlay"


@dataclass(frozen=True)
class SignalSpec:
    name: str
    compute: Callable
    family: str
    default_weight: float = 0.0
    kind: str = "directional"        # directional | gate | overlay (blend MECHANICS)
    signal_class: str = "position"   # ROLE taxonomy (orthogonal to kind):
                                     #   "regime"       → what KIND of market is this? (pin,
                                     #                    expansion, trend) — modulates trust,
                                     #                    NEVER votes direction.
                                     #   "position"     → where are participants positioned?
                                     #                    (ΔOI, migration, futures OI) — the vote.
                                     #   "confirmation" → is the market ACCEPTING it? (spot,
                                     #                    VWAP, breadth, volume, futures basis).
    blended: bool = False            # participates in the net-score blend?
    momentum_boost: bool = False     # confidence amplified in opening/power-hour?
    data_ready: bool = True          # False = data missing/untrusted → PINNED at weight 0;
                                     #   the Calibration Agent may not propose weight for it.
                                     #   Flip to True (deliberately) once the feed is trusted.
    horizon: str = "intraday"        # "intraday" (edge at ~15–60m — belongs in the intraday
                                     #   net blend) | "slow" (edge at ≥2h / daily — a structural/
                                     #   macro read for the daily layer, NOT intraday entry timing).
                                     #   Set from the signal-construction audit (run_signal_audit):
                                     #   a signal whose IC/expectancy peaks at the 120m ceiling is
                                     #   slow and must not gate a 15-minute option entry.
    label: str = ""                  # display name; falls back to a title-cased name
    method: str = ""                 # how it is computed (shown in the UI breakdown)
    detail_keys: tuple = ()          # preferred detail-field order; () = show all, as emitted

    @property
    def display(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()


# ── THE ROSTER — the one and only place signals are declared ──────────────────
REGISTRY: list[SignalSpec] = [
    # ── weighted directional core (blended) ───────────────────────────────────
    # REPRESENTATIVE of the intraday momentum/flow cluster (audit: technical_momentum,
    # rel_volume, futures_flow, vol_index all ρ>0.8 with this — one bet in five hats).
    # It carries the cluster's whole blend weight so momentum is counted ONCE, not 5×.
    SignalSpec(
        "heavyweight_leadership", heavyweight_leadership.compute, LEADERSHIP, 0.56,
        blended=True, momentum_boost=True,
        label="Heavyweight leadership",
        method="Weighted tape: Σ wᵢ·rᵢ across the 50 constituents (free-float weight × return). "
               "Score = tanh(weighted_ret% / 0.6). Confidence rises with weight coverage, "
               "heavyweight volume surge, and breadth agreement.",
        detail_keys=("weighted_ret_pct", "concentration", "breadth", "coverage_weight_pct",
                     "hv_vol_surge", "n_constituents")),
    SignalSpec(
        "heavyweight_leadership_persistent", heavyweight_leadership_persistent.compute, LEADERSHIP, 0.0,
        label="Heavyweight leadership (persistent)",
        method="The signal-to-noise version of heavyweight leadership. Scores the t-statistic "
               "z = mean/(vol/√n) of the free-float-weighted per-bar constituent leadership over "
               "the window — sustained, low-noise leadership reads decisive; choppy leadership that "
               "averages ~0 reads NEUTRAL, so it stops flipping on minute noise. Same normalisation "
               "technical_momentum uses; the raw heavyweight_leadership lacks it. Confidence folds "
               "in consistency (fraction of bars agreeing with the mean) and cap-weighted breadth "
               "(how many heavyweights lead the same way). Candidate at weight 0 — validate "
               "side-by-side with the raw signal.",
        detail_keys=("z_tstat", "mean_ret_pct", "vol_ret_pct", "consistency",
                     "breadth_weighted", "n_bars", "n_constituents")),
    # DE-DUPLICATED: ρ=0.94 with heavyweight_leadership (audit) — same intraday momentum
    # bet. Dropped from the blend (blended=False, weight 0) so it isn't double-counted;
    # still evaluated & studied as a directional candidate. Re-enable only if it earns
    # DISTINCT (orthogonal) edge over the representative.
    SignalSpec(
        "technical_momentum", technical_momentum.compute, TREND, 0.0,
        blended=False, momentum_boost=True, signal_class="confirmation",
        label="Technical momentum",
        method="NIFTY 1m tape. trend_z = (EMA9−EMA21)/ATR; thrust_z = windowed log-return / its "
               "vol; vol_ratio = recent vs prior volume. Score = 0.6·tanh(trend)+0.4·tanh(thrust), "
               "scaled by participation.",
        detail_keys=("trend_z", "thrust_z", "vol_ratio", "ema_fast", "ema_slow", "atr_1m", "n_bars")),
    SignalSpec(
        "global_momentum", global_momentum.compute, MACRO, 0.0,
        blended=True, momentum_boost=True, data_ready=False,
        label="Global momentum / forex",
        method="Cross-asset tilt: metals barometer (copper − gold), USDINR inverse (rupee weak → "
               "bearish), and overnight/session index drift. Prefers the live global-cues cache "
               "when fresh, else 1m bars.",
        detail_keys=("risk_appetite", "broad_fii", "metals_score", "copper_pct", "gold_pct",
                     "usdinr_pct", "nifty_drift_pct", "n_streams", "source")),
    # The one genuinely-independent INTRADAY bet left in the blend (low audit
    # redundancy, intraday horizon). Picks up the weight freed by segregating the slow
    # structural signals so the intraday net = momentum-rep + breadth.
    SignalSpec(
        "breadth_oi", breadth_oi.compute, INTERNALS, 0.44, blended=True,
        label="Breadth & OI positioning",
        method="Constituent advance/decline breadth + option OI walls: support = max put-OI strike "
               "below spot, resistance = max call-OI above. Lean from spot position in the band, "
               "wall reinforcement, and put/call OI ratio.",
        detail_keys=("breadth", "oi")),
    # HORIZON-SEGREGATED: edge peaks at the 120m ceiling (audit) — a slow, structural
    # read, not a 15-minute entry signal. Removed from the INTRADAY net blend (kept as a
    # studied directional candidate) and tagged horizon="slow" for the daily layer.
    SignalSpec(
        "skew_rnd", skew_rnd.compute, DERIVATIVES, 0.0, blended=False, horizon="slow",
        label="Skew / RND",
        method="Risk-neutral drift: RND mean vs spot (Breeden-Litzenberger via backend/quant/rnd.py "
               "when scipy present) + 25Δ risk-reversal. Falls back to a premium-based OTM "
               "put-vs-call proxy when IV is missing.",
        detail_keys=("engine", "rnd", "skew_proxy")),
    # HORIZON-SEGREGATED: slowest of all (peaks at 120m, lowest audit redundancy) — a
    # volatility-structure read for the daily layer, not intraday direction. Out of the
    # intraday net blend; tagged horizon="slow".
    SignalSpec(
        "vrp", vrp.compute, VOLATILITY, 0.0, blended=False, horizon="slow",
        label="Variance risk premium",
        method="VRP ratio = implied vol / realized vol. RV = annualised 1m close-to-close; implied "
               "from ATM IV or India VIX. Ratio ≥1.15 RICH (sell premium), ≤0.95 CHEAP (buy). "
               "Mostly a structure modulator.",
        detail_keys=("rv_ann_pct", "implied_pct", "vrp_ratio", "regime", "implied_source")),

    # ── directional CANDIDATES — evaluated + studied, weight 0 until they prove edge ──
    SignalSpec(
        "vwap", vwap.compute, TREND, 0.0, signal_class="confirmation",
        label="VWAP position",
        method="Session VWAP — the volume-weighted mean price since the open, the reference "
               "institutions trade around. Reads where spot sits relative to it. Index volume is "
               "reconstructed as Σ index_weightᵢ × volumeᵢ (signals/index_volume.py)."),
    SignalSpec(
        "vol_index", vol_index.compute, LEADERSHIP, 0.0, signal_class="confirmation",
        label="Volume-weighted momentum",
        method="Constituent volume × index-weighted momentum — 'where the heavyweight money moves'. "
               "NIFTY being free-float cap weighted, a move in a 10%-weight name swings the index "
               "far more than the same move in a 0.4% name. Weights per-stock RETURNS (not volume)."),
    SignalSpec(
        "rel_volume", rel_volume.compute, PARTICIPATION, 0.0, signal_class="confirmation",
        label="Relative volume",
        method="Score = clamp( tanh(r_NIFTY/0.12) × clamp(0.4 + 0.6·RV, 0.3, 1.6) ), where r_NIFTY "
               "is the 15-bar NIFTY index return in PERCENT and RV is the INDEX-WEIGHTED relative "
               "volume RV = Σᵢwᵢ·mean(volᵢ, last 15) / Σᵢwᵢ·mean(volᵢ, prior 15), reconstructed from "
               "the constituents because the index carries no volume of its own (shared "
               "index_volume.per_bar_index_volume). A heavyweight's volume surge therefore counts "
               "more than a small-weight name's. Direction comes ENTIRELY from the index price; the "
               "volume term is a positive multiplier, neutral (1.0) at RV = 1.0, that can never flip "
               "the sign. The unweighted ratio is reported in detail for divergence but does not "
               "feed the score.",
        detail_keys=("recent_ret_pct", "rel_volume_weighted", "rel_volume_unweighted",
                     "participation_boost", "vol_source")),

    # ── macro feeds not yet trusted → PINNED at weight 0 until the data is in place ──
    SignalSpec(
        "crude_energy", crude_energy.compute, MACRO, 0.0, data_ready=False, horizon="slow",
        label="Crude / energy",
        method="Crude terms-of-trade tilt. India imports the bulk of its crude, so a crude spike is "
               "an inflation and current-account shock: rising crude → bearish NIFTY, falling crude "
               "→ bullish."),
    SignalSpec(
        "usdinr", usdinr.compute, MACRO, 0.0, horizon="slow",
        label="USDINR / rupee",
        method="Rupee as the fast proxy for foreign-flow direction and risk appetite: rupee weakness "
               "(USDINR up) → risk-off / FII outflow pressure → bearish NIFTY; rupee strength → "
               "inflow-supportive → bullish."),
    SignalSpec(
        "global_gap", global_gap.compute, MACRO, 0.0, data_ready=False, horizon="slow",
        label="Overnight gap / risk-off",
        method="The only signal that reads ACROSS sessions. Every other signal reads the intraday "
               "tape and is therefore blind to a move that happens overnight; this one reads the "
               "global risk-off state that produces the cash-index gap at the open."),
    SignalSpec(
        "futures_basis", futures_basis.compute, PARTICIPATION, 0.0, horizon="slow",
        label="Futures basis",
        method="NIFTY futures basis (future − spot) — the positioning / leverage read the cash-tape "
               "signals cannot see. A widening premium signals long build-up; a discount signals "
               "short build-up or hedging pressure."),
    SignalSpec(
        "futures_calendar", futures_calendar.compute, PARTICIPATION, 0.0, horizon="slow",
        label="Futures term structure",
        method="Term structure / roll pressure across near and next expiry — deliberately kept "
               "separate from futures_basis so the horizon map and attribution can decide which "
               "(if either) earns a non-zero weight."),
    # ACTIVATED: the NIFTY_FUT_1 open-interest feed (ICICI Breeze / NSE) is now captured,
    # so data_ready=True (un-pinned). Kept at weight 0 / non-blended DELIBERATELY: its own
    # DIRECTIONAL edge is weak-to-negative (audit: hollow/15m IC ≈ −0.25) — it is a
    # CONDITIONING variable (the OI-regime axis) and a reliability overlay, NOT a forecaster.
    # Its value is telling the blend WHEN to trust a directional read (conviction) vs fade
    # it (hollow/coiled/churn), which is studied via regime_by='oi', not by its own vote.
    SignalSpec(
        "futures_oi_regime", futures_oi_regime.compute, DERIVATIVES, 0.0,
        horizon="intraday",
        label="Futures OI regime",
        method="Positioning read from futures price × open interest (same engine as the "
               "Macro Shock view, backend.quant.intraday_oi). LONG BUILDUP (price↑ OI↑) and "
               "SHORT BUILDUP (price↓ OI↑) = conviction; SHORT COVERING / LONG UNWINDING = "
               "hollow; heavy OI on flat price = COILED; flat = churn. Feed now active "
               "(NIFTY_FUT_1 1m OHLCV+OI). Used as the OI-regime conditioner / reliability "
               "overlay, not a directional vote — hence weight 0.",
        detail_keys=("regime", "lean", "conviction", "read", "d_price_pct", "d_oi_pct")),
    SignalSpec(
        "futures_flow", futures_flow.compute, PARTICIPATION, 0.0,
        label="Future Flow Score",
        method="Score = PRICE RETURN × RELATIVE FUTURES VOLUME, specifically "
               "clamp( tanh(ret_pct/0.12) × clamp(0.4 + 0.6·rel_vol, 0.3, 1.6) ), where ret_pct is "
               "the 15-bar NIFTY_FUT_1 return and rel_vol = mean(volume, last 15) / mean(volume, "
               "prior 15). Direction comes ENTIRELY from price — the volume term is a positive "
               "multiplier that scales magnitude and can never flip the sign; it is neutral (1.0) "
               "at rel_vol = 1.0. Not a duplicate of technical_momentum / rel_volume because the "
               "NIFTY index carries no volume, so those must ESTIMATE participation whereas the "
               "future's volume is directly observed. NOTE: this carries NO open interest — true "
               "long-build-up / short-covering flow needs OI, which is not yet in the feed "
               "(fo_price_bars.open_interest exists in the schema but is unpopulated).",
        detail_keys=("fut_recent_ret_pct", "fut_rel_volume", "participation_boost",
                     "vol_source", "thin_volume_move")),

    # STRIKE-ROLE-CHANGE: OI-wall EVOLUTION (resistance→support flips) — the temporal
    # read breadth_oi's static snapshot misses. Studied candidate at weight 0; shares
    # the INTERNALS family with breadth_oi (both read the OI walls, likely correlated).
    SignalSpec(
        "strike_role_change", strike_role.compute, INTERNALS, 0.0,
        label="Strike role change",
        method="Tracks the OI walls' EVOLUTION, not just their level. Resistance = biggest "
               "call-OI strike above spot, support = biggest put-OI below. Using ΔOI "
               "reconstructed from levels across captures (the oi_chg columns are empty), "
               "it flags role flips: resistance call OI unwinding + support put OI building "
               "→ level turning from resistance into support (bullish); the mirror is "
               "bearish. Growth rates are relative to each wall's own OI so big and small "
               "walls read alike.",
        detail_keys=("resistance_strike", "support_strike", "resist_call_growth_pct",
                     "support_put_growth_pct", "put_build_at_resistance_pct", "read", "window")),

    # ── REGIME signals (kind=gate, signal_class=regime): pin/vol state, never a vote ──
    SignalSpec(
        "pin_pressure", pin_pressure.compute, GAMMA, kind="gate", signal_class="regime",
        label="Pin pressure (gamma)",
        method="Pin Pressure Index = (CallOI+PutOI at ATM)/ATM straddle → pin STRENGTH in [0,1] "
               "(blended with OI concentration at the pin strike, so it's scale-free). Answers "
               "'how hard is it to escape this strike', NOT 'which way' — a regime, not a vote. "
               "The controller uses the strength to damp directional trust (strong pin → expect "
               "range/reversion). Direction comes from the Position/Confirmation signals.",
        detail_keys=("ppi", "pin_strike", "dist_to_pin", "pin_share", "atm_straddle",
                     "pin_strength", "regime")),
    SignalSpec(
        "oi_dispersion", oi_dispersion.compute, GAMMA, kind="gate", signal_class="regime",
        label="OI dispersion",
        method="OI-weighted standard deviation of strikes (√dispersion, in points). Small = OI "
               "crowded into a strike (pin); large = smeared across the chain (loose). Emits a "
               "non-directional TIGHTNESS score 0..1. Complements the center of gravity: COG says "
               "WHERE the mass is, dispersion says how TIGHT.",
        detail_keys=("oi_std_pts", "cog", "tightness", "regime")),
    SignalSpec(
        "oi_entropy", oi_entropy.compute, GAMMA, kind="gate", signal_class="regime",
        label="OI entropy",
        method="Shannon entropy of the OI distribution / log(N), in [0,1]. Low = everyone crowded "
               "into one strike (pin); high = inventory distributed. Emits CROWDING = 1 − entropy. "
               "Cousin of dispersion (both measure concentration) — the audit decides if both earn "
               "a place. Your 'pin strength = PPI + entropy + dispersion' idea, learned not hardcoded.",
        detail_keys=("entropy_norm", "crowding", "n_strikes", "regime")),
    # ── expiry-day option-chain POSITION candidates (weight 0, studied) ───────
    SignalSpec(
        "oi_migration", oi_migration.compute, INTERNALS, 0.0,
        label="OI center-of-gravity migration",
        method="OI-weighted mean strike (center of gravity) for calls and puts, and its "
               "movement vs the prior snapshot. Both centers drifting up = support/resistance "
               "rising = bullish; both down = bearish. Stronger than any single strike's OI. "
               "Confidence rises when the two sides agree.",
        detail_keys=("cog_call", "cog_put", "d_cog_call", "d_cog_put", "migration_pts",
                     "sides_agree", "read")),
    SignalSpec(
        "straddle_flow", straddle_flow.compute, VOLATILITY, kind="gate", signal_class="regime",
        label="Straddle compression / expansion",
        method="ATM straddle S=C+P and its change: compression (S↓) = premium selling / "
               "pinning / range; expansion (S↑) = a move brewing. A GATE, not a direction — "
               "a straddle is symmetric and carries no bull/bear sign. Meant to modulate "
               "downstream trust (Layer-0 regime), and to become a regime_by='straddle' axis "
               "so its weight is earned from the conditional study, not assumed.",
        detail_keys=("atm_straddle", "prev_straddle", "change_pct", "regime", "note")),

    SignalSpec(
        "dealer_center", dealer_center.compute, INTERNALS, 0.0,
        label="Dealer center (ΔOI centroid)",
        method="ΔOI-weighted strike centroid — where NEW risk is being added, vs "
               "oi_migration's standing-OI mass. center = Σ strike·(ΔOI_c⁺+ΔOI_p⁺)/ΣΔOI⁺. "
               "Score: centroid above spot = higher prices being accepted; plus put-vs-call "
               "writing aggression (fresh put writing at/below spot = bullish underwriting). "
               "The dynamic support/resistance read — the centroid migrates WITH repricing "
               "while standing OI still points at the old range.",
        detail_keys=("dealer_center", "spot", "offset_pts", "put_add_share",
                     "fresh_oi_added", "window", "read")),

    # ── trend-behaviour candidates (fill the non-trend-following gaps) ────────
    SignalSpec(
        "adx", adx.compute, TREND, 0.0, signal_class="confirmation",
        label="ADX / DMI trend strength",
        method="Wilder's DMI on NIFTY 1m. +DI vs −DI gives direction, ADX gives trend "
               "STRENGTH. Score = (+DI−−DI)/(+DI+−DI); confidence scales with ADX so a "
               "directional read is trusted only when a real trend exists (ADX>25). New maths "
               "(directional movement + ATR), distinct from EMA momentum and Kaufman ER.",
        detail_keys=("plus_di", "minus_di", "adx", "read")),
    SignalSpec(
        "choppiness", choppiness.compute, VOLATILITY, kind="gate", signal_class="regime",
        label="Choppiness index",
        method="CI = 100·log10(ΣTR / (maxHigh−minLow)) / log10(n) on NIFTY 1m. High (≥61.8) = "
               "choppy/range, low (≤38.2) = trending. A REGIME read (trend↔chop), non-directional "
               "— a range-based second opinion to Kaufman ER for the regime engine.",
        detail_keys=("choppiness_index", "chop", "regime")),
    SignalSpec(
        "breadth_quality", breadth_quality.compute, INTERNALS, 0.0,
        label="Breadth quality (% above trend)",
        method="% of the 50 constituents trading above their own EMA20, index-weighted — how "
               "BROAD the move is, not just up/down. Broad participation above trend = durable; "
               "narrow/heavyweight-led = fragile. New information from the constituents, distinct "
               "from advance/decline breadth. Score = 2·(pct_above − 0.5).",
        detail_keys=("pct_above_ema_weighted", "pct_above_ema_equal", "n_constituents",
                     "narrow_vs_broad", "read")),

    # ── NON-directional: gates (veto/modulate) and risk overlays (not votes) ──
    SignalSpec(
        "time_of_day", time_of_day.compute, GATE, kind="gate",
        label="Session phase",
        method="Intraday session-phase modulator (IST). The tape is not stationary across the day: "
               "the 09:15-09:45 opening drive carries the largest directional bursts, midday "
               "chops, power hour trends again. Amplifies or damps momentum confidence."),
    SignalSpec(
        "earnings_events", earnings_events.compute, GATE, kind="gate",
        label="Event / earnings gate",
        method="A veto and a structure hint, not a direction. Binary events (CPI, RBI, Fed, "
               "large-cap earnings) inflate premium and inject gap risk that intraday momentum "
               "cannot forecast."),
    SignalSpec(
        "derisk_preopen", derisk_preopen.compute, OVERLAY, kind="overlay",
        label="Pre-open de-risk",
        method="LEADING liquidity-derisk warning read before the Indian open, so it can arm BEFORE "
               "the drawdown — the companion derisk_liquidity is coincident and arms only once the "
               "session is already falling."),
    SignalSpec(
        "derisk_liquidity", derisk_liquidity.compute, OVERLAY, kind="overlay",
        label="Liquidity de-risk",
        method="Coincident liquidity-driven de-risk detector (max-drawdown insurance trigger). A "
               "RISK OVERLAY, not a directional vote: it estimates the probability that the tape is "
               "in a broad, liquidity-driven decline."),
]

BY_NAME: dict[str, SignalSpec] = {s.name: s for s in REGISTRY}


# ── accessors (import these instead of hardcoding lists) ──────────────────────
def directional() -> list[SignalSpec]:
    return [s for s in REGISTRY if s.kind == "directional"]


def directional_names() -> list[str]:
    """All directional signals (weighted core + weight-0 candidates) — the analytics
    / study roster."""
    return [s.name for s in directional()]


def blended_names() -> list[str]:
    """Signals that enter the net-score blend (the weighted core)."""
    return [s.name for s in REGISTRY if s.blended]


def by_class(signal_class: str) -> list[str]:
    """Signals in a ROLE class: 'regime' (modulate trust, never vote), 'position'
    (the directional votes), 'confirmation' (is positioning being accepted). This is
    the three-class split the market-state engine keys off — regime signals set the
    weights the position signals vote with, and confirmation signals validate them."""
    return [s.name for s in REGISTRY if s.signal_class == signal_class]


def slow_names() -> list[str]:
    """Directional signals whose edge lives at ≥2h / daily (audit-tagged horizon='slow')
    — structural/macro reads for the DAILY layer, deliberately kept OUT of the intraday
    net so they never gate a 15-minute entry. The daily blend can pick these up."""
    return [s.name for s in directional() if s.horizon == "slow"]


def momentum_names() -> list[str]:
    """Signals whose confidence the opening/power-hour phase amplifies."""
    return [s.name for s in REGISTRY if s.momentum_boost]


def pinned_zero_names() -> list[str]:
    """Directional signals PINNED at weight 0 because their data isn't ready/trusted.
    The Calibration Agent must not propose weight for these; flip `data_ready=True`
    on the SignalSpec when the feed is in place."""
    return [s.name for s in directional() if not s.data_ready]


def default_weights() -> dict[str, float]:
    """{directional signal: default blend weight}. The canonical weight roster —
    SignalWeights derives from this."""
    return {s.name: s.default_weight for s in directional()}


def families() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in directional():
        out.setdefault(s.family, []).append(s.name)
    return out


def roster() -> list[dict]:
    """JSON-safe view of the whole roster — what `/api/strategy/config` serves and
    the UI renders. The frontend holds NO signal list, label map or method text of
    its own; add a SignalSpec row above and it appears in every view automatically.
    `compute` is deliberately omitted (not serialisable, not the UI's business)."""
    return [{"name": s.name, "label": s.display, "family": s.family,
             "kind": s.kind, "weight": s.default_weight, "blended": s.blended,
             "momentum_boost": s.momentum_boost, "data_ready": s.data_ready,
             "horizon": s.horizon, "signal_class": s.signal_class,
             "method": s.method, "detail_keys": list(s.detail_keys),
             "feature_key": f"sig_{s.name}_score"}
            for s in REGISTRY]


def validate() -> dict:
    """Sanity: unique names, blended ⊆ directional, weights sum to ~1 over blended."""
    names = [s.name for s in REGISTRY]
    dups = {n for n in names if names.count(n) > 1}
    blended_ok = all(s.kind == "directional" for s in REGISTRY if s.blended)
    wsum = round(sum(s.default_weight for s in REGISTRY if s.blended), 4)
    assert not dups, f"duplicate signal names: {dups}"
    assert blended_ok, "a blended signal is not kind=directional"
    return {"n": len(REGISTRY), "directional": len(directional()),
            "blended": len(blended_names()), "blended_weight_sum": wsum,
            "families": {f: len(v) for f, v in families().items()}}


if __name__ == "__main__":
    import json
    print(json.dumps(validate(), indent=2))
