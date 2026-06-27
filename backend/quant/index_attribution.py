"""
index_attribution.py
---------------------
Cap-weighted index decomposition, data-feed corroboration, and fusion with the
news-driven regime classifier (market_regime.py).

Core identity
-------------
    index_return  =  Σ_i  w_i * r_i          (free-float weight x constituent return)

The heavyweight attribution is the GROUND TRUTH of index direction -- it needs
no news. News explains *why* and *anticipates*; this confirms *how much* and
*who*. Two layers, fused:

    news  (market_regime.py)   ->  forward-looking + explanatory, can be early/noisy
    tape  (this module)        ->  realised, certain, but backward-looking

Key nuance this catches: in NIFTY the heaviest names are Reliance + the banks
(financials ~37%), NOT IT (~9%). So an AI/IT sentiment shock can be loud in the
news while the index barely moves -- unless the financial heavyweights follow.
That is exactly why NIFTY fell ~0.6% on 23-Jun-2026 while KOSPI fell ~10%.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


# --------------------------------------------------------------------------- #
# Config -- SEED ONLY. Refresh from the NSE constituent file / your data feed.
# Free-float weights drift daily with price and reset semi-annually (Mar/Sep).
# Source snapshot: NSE, 23-Jun-2026 (top constituents; extend to all 50).
# --------------------------------------------------------------------------- #
from .sector_map import weights, sector_of

NIFTY50_WEIGHTS: dict[str, float] = weights()
SECTOR_OF: dict[str, str] = sector_of()

# Representative instrument per news "surface" (see market_regime.Surface) for
# magnitude-based corroboration: a surface that actually moved hard should count
# more than one merely talked about.
SURFACE_PROXY: dict[str, str] = {
    "korea_equity": "KOSPI", "japan_equity": "NIKKEI", "us_tech": "SOX",
    "us_rates": "US10Y", "india_it": "NIFTYIT", "india_broad": "NIFTY",
    "commodity_oil": "BRENT", "fx": "USDINR",
}


@dataclass
class Quote:
    symbol: str
    pct_change: float        # today's % move from the data feed, e.g. -2.4


# --------------------------------------------------------------------------- #
# 1. Mechanical attribution: who is actually moving the index
# --------------------------------------------------------------------------- #
def attribute_index_move(
    quotes: list[Quote],
    weights: dict[str, float] | None = None,
    normalize: bool = False,
) -> dict:
    """Decompose the index move into per-stock and per-sector point
    contributions. With full constituent coverage, index_pct == the actual
    index % move. `normalize=True` rescales partial weight coverage to 100%
    (use only when you deliberately have a subset)."""
    weights = weights or NIFTY50_WEIGHTS
    denom = (sum(weights.values()) if normalize else 100.0) or 1.0

    contrib: dict[str, float] = {}
    for q in quotes:
        w = weights.get(q.symbol)
        if w is None:
            continue
        contrib[q.symbol] = (w / denom) * q.pct_change   # contribution in index %

    index_pct = sum(contrib.values())

    sector_contrib: dict[str, float] = defaultdict(float)
    for sym, c in contrib.items():
        sector_contrib[SECTOR_OF.get(sym, "Other")] += c

    ranked = sorted(contrib.items(), key=lambda kv: abs(kv[1]), reverse=True)
    total_abs = sum(abs(c) for c in contrib.values()) or 1.0
    top5_concentration = sum(abs(c) for _, c in ranked[:5]) / total_abs

    return {
        "index_pct": index_pct,
        "stock_contrib": dict(ranked),
        "sector_contrib": dict(sector_contrib),
        "top_contributors": ranked[:5],
        "top5_concentration": top5_concentration,
        "breadth_up": sum(1 for q in quotes if q.pct_change > 0),
        "breadth_down": sum(1 for q in quotes if q.pct_change < 0),
    }


# --------------------------------------------------------------------------- #
# 2. Data-feed corroboration: scale conviction by how hard surfaces moved
# --------------------------------------------------------------------------- #
def magnitude_corroboration(surface_moves: dict[str, float], cap: float = 2.0) -> float:
    """Multiplier driven by the *size* of the corroborating moves (abs %).
    Complements market_regime.corroboration_multiplier (which counts how MANY
    surfaces agree); this captures how VIOLENTLY they moved. Saturating, capped.
        avg |move| 0%  -> 1.00
        avg |move| 2%  -> 1.40
        avg |move| 5%  -> 2.00 (cap)
    """
    if not surface_moves:
        return 1.0
    avg_abs = sum(abs(v) for v in surface_moves.values()) / len(surface_moves)
    return min(1.0 + avg_abs / 5.0, cap)


# --------------------------------------------------------------------------- #
# 3. Fusion: blend anticipatory news sentiment with realised tape attribution
# --------------------------------------------------------------------------- #
def fuse_sector_view(
    news_sentiment: dict[str, float],   # from market_regime.sector_sentiment()
    index_attr: dict,                   # from attribute_index_move()
    w_news: float = 0.4,
    w_tape: float = 0.6,                # tape is ground truth -> weighted higher
) -> dict[str, float]:
    """Per-sector blended score in roughly [-1, +1] (negative = bearish).
    Tape contributions are normalised to the same scale as news before blending.
    """
    sector_tape = index_attr["sector_contrib"]
    scale = max((abs(v) for v in sector_tape.values()), default=1.0) or 1.0

    out: dict[str, float] = {}
    for s in set(news_sentiment) | set(sector_tape):
        n = news_sentiment.get(s, 0.0)
        t = sector_tape.get(s, 0.0) / scale
        out[s] = w_news * n + w_tape * t
    return out


# --------------------------------------------------------------------------- #
# 4. Demo: the 23-Jun-2026 episode -- IT leads sentiment, but the index's fate
#    rides on the financial heavyweights.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # IT sharply down (the AI/semi read-through), banks & Reliance only mildly
    # soft -> the index barely falls, despite the loud IT narrative.
    quotes = [
        Quote("INFY", -2.8), Quote("TCS", -2.5), Quote("WIPRO", -2.2),
        Quote("TECHM", -2.0),                       # IT: big % move, small weight
        Quote("HDFCBANK", -0.4), Quote("ICICIBANK", -0.3), Quote("SBIN", -0.5),
        Quote("AXISBANK", -0.2), Quote("KOTAKBANK", -0.3),  # banks: small move, huge weight
        Quote("RELIANCE", +0.2), Quote("BHARTIARTL", +0.4),
        Quote("LT", -0.1), Quote("MARUTI", +0.3), Quote("SUNPHARMA", +0.5),
    ]

    attr = attribute_index_move(quotes)
    print(f"Index move (covered names): {attr['index_pct']:+.2f}%")
    print(f"Top-5 concentration       : {attr['top5_concentration']:.0%}")
    print(f"Breadth                   : {attr['breadth_down']} down / {attr['breadth_up']} up")
    print("\nTop contributors to the move (index points):")
    for sym, c in attr["top_contributors"]:
        print(f"  {sym:<11} {c:+.3f}")
    print("\nSector contribution to index move:")
    for sec, c in sorted(attr["sector_contrib"].items(), key=lambda kv: kv[1]):
        print(f"  {sec:<14} {c:+.3f}")

    # --- counterfactual: same IT crash, but banks follow it down ---
    quotes_banks_follow = [q for q in quotes if SECTOR_OF.get(q.symbol) != "Banks"]
    quotes_banks_follow += [Quote("HDFCBANK", -1.8), Quote("ICICIBANK", -1.6),
                            Quote("SBIN", -1.7), Quote("AXISBANK", -1.5),
                            Quote("KOTAKBANK", -1.6)]
    attr2 = attribute_index_move(quotes_banks_follow)
    print(f"\n[Counterfactual] if banks follow IT down: index {attr2['index_pct']:+.2f}%")
    print("  -> same IT move; the index outcome is set by whether financials join.")

    # --- fusion with the news layer (illustrative news scores) ---
    news = {"IT": -0.6, "Banks": -0.2, "Energy": 0.0}
    fused = fuse_sector_view(news, attr)
    print("\nFused sector view (0.4 news + 0.6 tape):")
    for sec, v in sorted(fused.items(), key=lambda kv: kv[1]):
        print(f"  {sec:<14} {v:+.2f}")

    # --- magnitude corroboration from actual surface moves ---
    surface_moves = {"korea_equity": -9.99, "us_tech": -8.0, "india_it": -2.3}
    print(f"\nMagnitude corroboration (KOSPI -10%, SOX -8%, NiftyIT -2.3%): "
          f"x{magnitude_corroboration(surface_moves):.2f}")
