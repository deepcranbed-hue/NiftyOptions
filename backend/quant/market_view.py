"""
market_view.py
--------------
Combine TWO views that are both available with ZERO historical data:

  1. NEWS view (subjective, uncalibrated): direction + momentum, from sentiment.
  2. MARKET view (objective, no history): the risk-neutral distribution from the
     live chain -- what the market is ALREADY pricing.

The decision is RELATIVE, not absolute: "is the move my news read implies already
priced by the market?" The RND answers that today, so the system is useful before
any calibration exists. Calibration (later) only sharpens HOW MUCH to trust the
news leg -- it is not required to start.

This module also LOGS every run. Realised NIFTY outcomes are free going forward;
join them to the log nightly and you grow your own calibration set. The harness
is built now; it just starts producing calibrated numbers after N sessions.

HONESTY: the RND leg is quantitative and anchored. The news leg is a
direction+intensity overlay whose SCALE is uncalibrated -> use it to pick
structure and skew, not to compute a hard EV. Size small until the log calibrates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
@dataclass
class NewsView:
    index_bias: float        # [-1,+1] weighted-sentiment directional lean
    momentum: float          # [0..~1] force of the news: magnitude x freshness x corroboration
    coverage: float          # [0..1] fraction of index weight carrying sentiment

@dataclass
class MarketView:
    spot: float
    p_below_spot: float      # RND prob of finishing below spot
    expected_move: float     # RND sd in points (~ATM straddle)
    skew: float              # RND skew (negative = downside-fat, the usual NIFTY case)


# --------------------------------------------------------------------------- #
# Comparison logic (no history needed)
# --------------------------------------------------------------------------- #
NEUTRAL_BAND = 0.15
COVERAGE_MIN = 0.35
MOMENTUM_HI = 0.45           # above this, news implies a move -> expect vol expansion


def _dir(x, band):
    return "bearish" if x < -band else "bullish" if x > band else "neutral"


def compare(news: NewsView, mkt: MarketView) -> dict:
    # --- direction each view implies ---
    news_dir = _dir(news.index_bias, NEUTRAL_BAND)
    # market's priced lean: how far P(below) sits from 50/50
    market_tilt = mkt.p_below_spot - 0.5          # +ve = market prices downside
    market_dir = _dir(-market_tilt, 0.04)          # sign flip: more downside = bearish

    # --- is the news direction already priced? ---
    # priced_strength: how much the RND already leans the news way (0..1-ish)
    if news_dir == "bearish":
        priced_strength = max(0.0, market_tilt) / 0.5
    elif news_dir == "bullish":
        priced_strength = max(0.0, -market_tilt) / 0.5
    else:
        priced_strength = 0.0

    agree = (news_dir == market_dir) and news_dir != "neutral"
    diverge = (news_dir != market_dir) and "neutral" not in (news_dir, market_dir)

    if news.coverage < COVERAGE_MIN:
        relation = "UNRELIABLE"          # heavyweights silent
    elif agree and priced_strength < 0.35:
        relation = "CONFIRMED_UNDERPRICED"   # best case: news agrees, market hasn't moved
    elif agree:
        relation = "CONFIRMED_PRICED_IN"     # news right but already in the price -> low edge
    elif diverge:
        relation = "DIVERGENT"               # news vs market disagree -> caution / contrarian
    else:
        relation = "NEUTRAL"

    # --- vol axis: event-driven, not valuation-driven (valuation needs IV history) ---
    # high fresh momentum => expect realised move to EXCEED what's priced => long vol
    vol_state = "expansion" if news.momentum >= MOMENTUM_HI else "range"

    return {
        "news_dir": news_dir, "market_dir": market_dir,
        "market_prices_downside": round(mkt.p_below_spot, 3),
        "priced_strength": round(priced_strength, 2),
        "relation": relation, "vol_state": vol_state,
        "expected_move_pts": round(mkt.expected_move, 0),
        "skew": round(mkt.skew, 2),
    }


# --------------------------------------------------------------------------- #
# Strategy suggestion from the comparison
# --------------------------------------------------------------------------- #
def suggest(cmp: dict, news: NewsView) -> dict:
    rel, vol, d = cmp["relation"], cmp["vol_state"], cmp["news_dir"]

    if rel == "UNRELIABLE":
        return {"action": "STAND ASIDE",
                "why": "news covers the loud mover, not the heavyweights; weighted lean unreliable."}
    if rel == "DIVERGENT":
        return {"action": "STAND ASIDE / SMALL",
                "why": "your news view contradicts what the market prices; defer to the RND unless "
                       "momentum is very high and fresh. No clear edge."}
    if rel == "NEUTRAL":
        return {"action": "RANGE / THETA" if vol == "range" else "LONG VOL (non-directional)",
                "structure": "Iron condor (defined risk)" if vol == "range"
                             else "Long straddle / strangle",
                "why": "no directional lean; trade the vol axis only."}

    # CONFIRMED cases -> directional, modulated by whether it's priced and vol state
    long_vol = vol == "expansion"
    if d == "bearish":
        structure = ("Bear put spread / long puts" if long_vol
                     else "Bear call spread (sell premium above)")
    else:  # bullish
        structure = ("Bull call spread / long calls" if long_vol
                     else "Bull put spread (sell premium below)")

    edge = "HIGHER edge: market hasn't priced it yet" if rel == "CONFIRMED_UNDERPRICED" \
           else "LOWER edge: largely priced in -> only play fresh momentum, size down"
    # size from momentum x coverage, shrunk hard if priced-in
    size = round(news.momentum * news.coverage * (1.0 if rel == "CONFIRMED_UNDERPRICED" else 0.4), 2)

    return {"action": "TRADE", "direction": d, "vol_state": vol,
            "structure": structure, "edge_note": edge, "size_mult": size,
            "skew_tip": ("steep put skew: long puts are dear, prefer put SPREADS / risk-reversals"
                         if cmp["skew"] < -0.3 else "skew mild: outright legs ok")}


# --------------------------------------------------------------------------- #
# Logging -> builds the calibration set going forward (the "harness without data")
# --------------------------------------------------------------------------- #
def log_run(news: NewsView, mkt: MarketView, cmp: dict, rec: dict,
            path="signal_log.jsonl"):
    row = {"ts": datetime.now(timezone.utc).isoformat(),
           "spot": mkt.spot,
           **{f"news_{k}": v for k, v in asdict(news).items()},
           **{f"cmp_{k}": v for k, v in cmp.items()},
           "action": rec.get("action"), "structure": rec.get("structure"),
           "realized_next_close": None,   # <-- fill nightly from price data
           "realized_move_pts": None}
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


# --------------------------------------------------------------------------- #
# Demo: the 23-Jun episode, with the RND read from rnd.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from rnd import extract_rnd, rnd_stats

    strikes = list(range(23750, 24850, 50))
    call_ltp = [478.60, 430.00, 383.85, 340.05, 295.50, 252.25, 212.40, 175.20,
                141.65, 112.70, 86.90, 65.35, 48.40, 35.70, 26.30, 19.35, 14.00,
                10.75, 8.30, 6.30, 5.20, 3.95]
    grid, dens = extract_rnd(strikes, call_ltp, 24_200.0, 7 / 365, 0.0655)
    st = rnd_stats(grid, dens, 24_200.0)

    mkt = MarketView(spot=24_200.0, p_below_spot=st["p_below_spot"],
                     expected_move=st["sd"], skew=st["skew"])

    # news leg from the decision_engine demo: mildly bearish index bias, but the
    # NEWS FORCE is high (fresh AI selloff, corroborated KOSPI/SOX/IT).
    news = NewsView(index_bias=-0.215, momentum=0.62, coverage=0.67)

    cmp = compare(news, mkt)
    rec = suggest(cmp, news)

    print("MARKET (RND) prices:")
    print(f"  P(below spot) {mkt.p_below_spot:.0%} | expected move +/-{mkt.expected_move:.0f} "
          f"| skew {mkt.skew:+.2f}\n")
    print("NEWS view:")
    print(f"  bias {news.index_bias:+.2f} ({cmp['news_dir']}) | momentum {news.momentum:.2f} "
          f"| coverage {news.coverage:.0%}\n")
    print("COMPARISON:")
    for k, v in cmp.items():
        print(f"  {k:<24}: {v}")
    print("\nSUGGESTION:")
    for k, v in rec.items():
        print(f"  {k:<14}: {v}")

    log_run(news, mkt, cmp, rec)
    print("\n[logged to signal_log.jsonl -- join realised closes nightly to calibrate]")
