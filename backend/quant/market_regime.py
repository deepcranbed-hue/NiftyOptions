"""
market_regime.py
----------------
Regime-tagging, recency-decay weighting, and cross-asset corroboration for a
news-driven sector-sentiment classifier (NewsAPI -> Gemini -> this module).

Design principles
-----------------
* Separation of concerns: the LLM (Gemini) does per-article NLU only -- it
  returns a sentiment score in [-1, +1] (and optionally a driver hint). Every
  aggregation, weighting and regime decision is deterministic and lives here,
  so it is cheap, testable, reproducible, and free of hallucinated numbers.
* Continuous functions throughout (exponential decay, soft saturating
  corroboration) rather than hard cutoffs.
* Config-driven: drivers, keywords, market surfaces and sector transmission
  are *data*, not branching logic -- tune them without touching the engine.

The three pieces that fix the "global cues should have led but didn't" bug:
    1. Driver tagging      -> what macro factor is the tape trading right now
    2. Recency decay        -> stale Iran/crude headlines stop dominating
    3. Cross-asset corrob.   -> KOSPI + SOX + Nasdaq + Nifty IT agreeing is a
                                high-conviction signal vs one isolated headline
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence


# --------------------------------------------------------------------------- #
# 1. Taxonomy (config)
# --------------------------------------------------------------------------- #
class Driver(str, Enum):
    AI_SEMI = "ai_semiconductor"
    RATES_FED = "rates_fed"
    GEOPOLITICS_OIL = "geopolitics_oil"
    DOMESTIC_FLOWS = "domestic_flows"
    EARNINGS = "earnings"
    FX_RUPEE = "fx_rupee"
    OTHER = "other"


# A "surface" is an asset/geography lens. Corroboration counts how many DISTINCT
# surfaces echo the same driver in-window -- that distinguishes a global,
# structural move from a single local headline.
class Surface(str, Enum):
    KR_EQUITY = "korea_equity"
    JP_EQUITY = "japan_equity"
    US_TECH = "us_tech"
    US_RATES = "us_rates"
    IN_IT = "india_it"
    IN_BROAD = "india_broad"
    COMMODITY_OIL = "commodity_oil"
    FX = "fx"
    OTHER = "other"


# keyword (lower-case substring) -> (driver, surface). One article can hit many.
# NOTE: keep keys specific ("nifty it", not "it") to avoid spurious substring
# matches.
KEYWORD_MAP: dict[str, tuple[Driver, Surface]] = {
    # --- AI / semiconductor complex: the surface that just led NIFTY down ---
    "kospi": (Driver.AI_SEMI, Surface.KR_EQUITY),
    "samsung": (Driver.AI_SEMI, Surface.KR_EQUITY),
    "sk hynix": (Driver.AI_SEMI, Surface.KR_EQUITY),
    "hbm": (Driver.AI_SEMI, Surface.KR_EQUITY),
    "nikkei": (Driver.AI_SEMI, Surface.JP_EQUITY),
    "softbank": (Driver.AI_SEMI, Surface.JP_EQUITY),
    "nasdaq": (Driver.AI_SEMI, Surface.US_TECH),
    "philadelphia semiconductor": (Driver.AI_SEMI, Surface.US_TECH),
    "sox index": (Driver.AI_SEMI, Surface.US_TECH),
    "nvidia": (Driver.AI_SEMI, Surface.US_TECH),
    "micron": (Driver.AI_SEMI, Surface.US_TECH),
    "semiconductor": (Driver.AI_SEMI, Surface.US_TECH),
    "ai rally": (Driver.AI_SEMI, Surface.US_TECH),
    "ai selloff": (Driver.AI_SEMI, Surface.US_TECH),
    "infosys": (Driver.AI_SEMI, Surface.IN_IT),
    "tcs": (Driver.AI_SEMI, Surface.IN_IT),
    "accenture": (Driver.AI_SEMI, Surface.IN_IT),
    "nifty it": (Driver.AI_SEMI, Surface.IN_IT),
    # --- Rates / Fed ---
    "federal reserve": (Driver.RATES_FED, Surface.US_RATES),
    "warsh": (Driver.RATES_FED, Surface.US_RATES),
    "fomc": (Driver.RATES_FED, Surface.US_RATES),
    "rate hike": (Driver.RATES_FED, Surface.US_RATES),
    "treasury yield": (Driver.RATES_FED, Surface.US_RATES),
    # --- Geopolitics / oil ---
    "brent": (Driver.GEOPOLITICS_OIL, Surface.COMMODITY_OIL),
    "crude": (Driver.GEOPOLITICS_OIL, Surface.COMMODITY_OIL),
    "hormuz": (Driver.GEOPOLITICS_OIL, Surface.COMMODITY_OIL),
    "iran": (Driver.GEOPOLITICS_OIL, Surface.COMMODITY_OIL),
    "opec": (Driver.GEOPOLITICS_OIL, Surface.COMMODITY_OIL),
    # --- Domestic flows ---
    "fii": (Driver.DOMESTIC_FLOWS, Surface.IN_BROAD),
    "fpi": (Driver.DOMESTIC_FLOWS, Surface.IN_BROAD),
    "dii": (Driver.DOMESTIC_FLOWS, Surface.IN_BROAD),
    "sip inflow": (Driver.DOMESTIC_FLOWS, Surface.IN_BROAD),
    # --- FX ---
    "rupee": (Driver.FX_RUPEE, Surface.FX),
    "dollar index": (Driver.FX_RUPEE, Surface.FX),
    "dxy": (Driver.FX_RUPEE, Surface.FX),
    # --- Earnings ---
    "agm": (Driver.EARNINGS, Surface.IN_BROAD),
    "guidance": (Driver.EARNINGS, Surface.IN_BROAD),
    "earnings": (Driver.EARNINGS, Surface.IN_BROAD),
}


# driver -> {sector: transmission weight}. Weight magnitude = how hard the
# driver hits that Indian sector; sign = direction of the sector move relative
# to the article sentiment (so a +0.7 with a bearish article => bearish sector,
# a -0.4 flips it, e.g. weak INR is a *tailwind* for IT/Pharma exporters).
SECTOR_TRANSMISSION: dict[Driver, dict[str, float]] = {
    Driver.AI_SEMI: {"IT": 1.0, "Telecom": 0.2},
    Driver.RATES_FED: {"IT": 0.5, "Banks": 0.4, "Realty": 0.6, "Auto": 0.3},
    Driver.GEOPOLITICS_OIL: {"OMC": 0.7, "Aviation": 0.8, "Paints": 0.5,
                             "Auto": 0.4, "Energy": -0.6},
    Driver.DOMESTIC_FLOWS: {"Banks": 0.6, "Financials": 0.7},
    Driver.EARNINGS: {"IT": 0.5},
    Driver.FX_RUPEE: {"IT": -0.4, "Pharma": -0.3, "OMC": 0.5},
}


# --------------------------------------------------------------------------- #
# 2. Data model
# --------------------------------------------------------------------------- #
@dataclass
class Article:
    title: str
    published_at: datetime          # MUST be tz-aware UTC
    sentiment: float = 0.0          # [-1, +1] from Gemini; -1 = very bearish
    body: str = ""                  # optional; improves keyword recall

    @property
    def text(self) -> str:
        return f"{self.title} {self.body}".lower()


@dataclass
class RegimeState:
    driver_scores: dict[Driver, float]          # corroboration-weighted evidence
    surfaces_by_driver: dict[Driver, set[Surface]]
    dominant: Driver
    conviction: float                           # dominant share of evidence [0,1]
    flipped_from: Driver | None = None          # set if regime changed


# --------------------------------------------------------------------------- #
# 3. Engine (deterministic, continuous)
# --------------------------------------------------------------------------- #
def decay_weight(age_hours: float, half_life_hours: float = 12.0) -> float:
    """An article loses half its weight every `half_life_hours`. Short
    half-life => fast tape forgets stale drivers quickly."""
    return math.exp(-math.log(2) * max(age_hours, 0.0) / half_life_hours)


def corroboration_multiplier(n_surfaces: int, k: float = 0.55) -> float:
    """Soft, saturating boost for cross-asset confirmation. 1 surface -> 1.0;
    grows and saturates as independent surfaces agree. Continuous, no cutoff.
        1 surface  -> 1.00
        2 surfaces -> 1.38
        3 surfaces -> 1.60
        5 surfaces -> 1.88
    """
    return 1.0 + k * math.log1p(max(n_surfaces - 1, 0))


def tag(article: Article) -> set[tuple[Driver, Surface]]:
    text = article.text
    hits = {ds for kw, ds in KEYWORD_MAP.items() if kw in text}
    return hits or {(Driver.OTHER, Surface.OTHER)}


def assess_regime(
    articles: Sequence[Article],
    now: datetime | None = None,
    half_life_hours: float = 12.0,
    prev_regime: Driver | None = None,
) -> RegimeState:
    """Which macro driver is the tape trading, and how convinced are we."""
    now = now or datetime.now(timezone.utc)
    weighted: dict[Driver, float] = defaultdict(float)
    surfaces: dict[Driver, set[Surface]] = defaultdict(set)

    for art in articles:
        age_h = (now - art.published_at).total_seconds() / 3600.0
        w = decay_weight(age_h, half_life_hours)
        for driver, surface in tag(art):
            weighted[driver] += w
            surfaces[driver].add(surface)

    # cross-asset corroboration applied per driver
    final = {d: raw * corroboration_multiplier(len(surfaces[d]))
             for d, raw in weighted.items()}
    final.pop(Driver.OTHER, None)  # OTHER never wins the dominance race

    if not final:
        dominant, conviction = Driver.OTHER, 0.0
    else:
        total = sum(final.values()) or 1.0
        dominant = max(final, key=final.get)
        conviction = final[dominant] / total

    flipped = prev_regime if (prev_regime and prev_regime != dominant) else None
    return RegimeState(final, dict(surfaces), dominant, conviction, flipped)


def sector_sentiment(
    articles: Sequence[Article],
    now: datetime | None = None,
    half_life_hours: float = 12.0,
) -> dict[str, float]:
    """Decay- and corroboration-weighted signed sentiment per Indian sector,
    normalised to roughly [-1, +1]. Negative => bearish."""
    now = now or datetime.now(timezone.utc)

    surfaces: dict[Driver, set[Surface]] = defaultdict(set)
    for art in articles:
        for d, s in tag(art):
            surfaces[d].add(s)

    scores: dict[str, float] = defaultdict(float)
    norm: dict[str, float] = defaultdict(float)
    for art in articles:
        age_h = (now - art.published_at).total_seconds() / 3600.0
        w = decay_weight(age_h, half_life_hours)
        for d, _surface in tag(art):
            mult = corroboration_multiplier(len(surfaces[d]))
            for sector, transmission in SECTOR_TRANSMISSION.get(d, {}).items():
                scores[sector] += w * mult * transmission * art.sentiment
                norm[sector] += w * mult * abs(transmission)

    return {s: scores[s] / norm[s] for s in scores if norm[s] > 0}


# --------------------------------------------------------------------------- #
# 4. Demo against the actual June-2026 episode
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from datetime import timedelta

    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)

    def hrs_ago(h):  # helper
        return now - timedelta(hours=h)

    headlines = [
        Article("KOSPI meltdown drags Indian indices into red; Nifty falls 279 points",
                hrs_ago(40), sentiment=-0.8),
        Article("Samsung and SK Hynix sink as AI rally faces reality check",
                hrs_ago(44), sentiment=-0.85),
        Article("AI stock selloff hits Nasdaq; Philadelphia Semiconductor index -8%",
                hrs_ago(42), sentiment=-0.7),
        Article("Nifty 50 falls 0.64% as IT stocks slide on Accenture's weak guidance",
                hrs_ago(36), sentiment=-0.6),
        Article("Micron earnings tonight test whether HBM demand justifies AI rally",
                hrs_ago(20), sentiment=-0.2),
        Article("Global economy fragile despite US-Iran truce, but India has buffers: RBI",
                hrs_ago(30), sentiment=0.1),
        Article("FIIs offload record amount in first half of June on elevated crude",
                hrs_ago(72), sentiment=-0.5),  # stale -> heavily decayed
    ]

    state = assess_regime(headlines, now=now, prev_regime=Driver.GEOPOLITICS_OIL)
    print("DOMINANT DRIVER :", state.dominant.value)
    print("CONVICTION      : {:.0%}".format(state.conviction))
    print("FLIPPED FROM    :", state.flipped_from.value if state.flipped_from else None)
    print("AI_SEMI surfaces:", sorted(s.value for s in state.surfaces_by_driver[Driver.AI_SEMI]))
    print("\nSector sentiment (negative = bearish):")
    for sec, val in sorted(sector_sentiment(headlines, now=now).items(),
                           key=lambda kv: kv[1]):
        print(f"  {sec:<12} {val:+.2f}")
