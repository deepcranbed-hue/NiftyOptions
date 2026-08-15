"""
strategy_suggester.py
---------------------
BEFORE the optimizer searches strikes, this picks the strategy FAMILY that fits
the regime — "sell iron condor" vs "buy strangle" vs "debit spread with the
view" — and explains why. The optimizer then finds the best strikes WITHIN the
recommended family.

It reasons over the signals you already compute:
  * complacency (0–100)  -> vol environment (sell vs buy premium)
  * bias (-1..+1, optional, user) -> directional tilt
  * expected move vs straddle -> is vol cheap/rich
  * event proximity -> expansion risk ahead (favor long vol / avoid selling)

Output: a RANKED list of archetypes with action (SELL/BUY), structure, and the
rationale — plus explicit "two-sided" notes when opposing ideas both have merit
(e.g. complacent now but event ahead = condor collects theta but buy wings for
the gap). It RECOMMENDS families; it never decides to trade (gates do that).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Suggestion:
    rank: int
    action: str          # SELL | BUY | NEUTRAL
    family: str          # iron_condor | strangle | straddle | bull_put_spread ...
    rationale: str
    fits: list           # which conditions favoured it
    caution: str = ""


def suggest(complacency: float, *, bias: float | None = None,
            expected_move_pts: float | None = None,
            straddle_pts: float | None = None,
            event_near_days: int | None = None,
            iv_percentile: float | None = None,
            earnings_season: bool = False) -> dict:
    """Return ranked archetype suggestions for the current regime."""
    sugg: list[Suggestion] = []
    notes: list[str] = []

    # --- vol environment from complacency ---
    if complacency >= 70:
        vol_env = "complacent"          # premium thin, shock-prone
    elif complacency < 45:
        vol_env = "fearful"             # premium rich, expansion likely
    else:
        vol_env = "neutral"

    # --- is vol cheap or rich? (straddle vs expected move, or IV pct) ---
    vol_cheap = None
    if iv_percentile is not None:
        vol_cheap = iv_percentile < 0.3
    elif expected_move_pts and straddle_pts:
        # if straddle (what you pay) is small vs the move the RND expects, vol is cheap
        vol_cheap = straddle_pts < 0.9 * expected_move_pts

    event_soon = event_near_days is not None and event_near_days <= 3
    b = bias or 0.0
    directional = abs(b) >= 0.4
    
    if earnings_season:
        notes.append("Earnings season: Favour defined-risk and smaller index-level bets due to single-stock gap risk.")

    # ── decision logic (ranked) ─────────────────────────────────────────────
    # 1. Event ahead or fearful/expansion + cheap vol -> BUY vol
    if event_soon or (vol_env == "fearful") or (vol_cheap is True):
        why = []
        if event_soon: why.append(f"high-impact event in {event_near_days}d (gap risk)")
        if vol_env == "fearful": why.append("fearful regime — expansion likely")
        if vol_cheap: why.append("vol looks cheap (straddle < expected move)")
        if directional:
            sugg.append(Suggestion(0, "BUY",
                "bull_call_spread" if b > 0 else "bear_put_spread",
                f"Directional view ({'bullish' if b>0 else 'bearish'}) with "
                f"expansion ahead — express it with a DEBIT spread (defined cost, "
                f"profits on the move).", why))
        sugg.append(Suggestion(0, "BUY", "long_strangle",
            "Buy a strangle to be long volatility into the expansion — profits on "
            "a sharp move either way; max loss = premium.", why,
            caution="Theta bleeds if the move doesn't come — size small, time it to the event."))

    # 2. Complacent -> do NOT sell premium; stand aside or buy cheap protection
    if vol_env == "complacent":
        sugg.append(Suggestion(0, "NEUTRAL", "stand_aside_or_long_vol",
            "Complacency high — premium is thin and the tape is short-vol. Poor "
            "pay for selling; favour standing aside or buying cheap optionality "
            "for the eventual snap.", ["complacency ≥ 70"],
            caution="Your risk gate will BLOCK premium-selling here anyway."))
        if not event_soon and not earnings_season:
            notes.append("Complacent but quiet: if you must be in, a tight DEFINED-"
                         "RISK condor only, small size — but the gate may halve/block it.")

    # 3. Neutral, range-ish, no event -> SELL condor (collect theta)
    if vol_env == "neutral" and not event_soon and (vol_cheap is not True):
        if directional:
            sugg.append(Suggestion(0, "SELL",
                "bull_put_spread" if b > 0 else "bear_call_spread",
                f"Range-ish with a {'bullish' if b>0 else 'bearish'} lean — sell a "
                f"credit spread on the side you expect to hold (collect theta, "
                f"defined risk, leaning with your view).",
                ["neutral vol", f"bias {b:+.2f}"]))
        sugg.append(Suggestion(0, "SELL", "iron_condor",
            "Neutral, range-bound, vol not cheap — sell a defined-risk iron condor "
            "to collect theta while price stays in the band.",
            ["neutral vol", "no imminent event"],
            caution="Check complacency/event gates before sizing."))

    # 4. Rich vol + neutral -> selling better compensated
    if vol_cheap is False and vol_env != "complacent":
        sugg.append(Suggestion(0, "SELL", "iron_condor",
            "Vol looks rich (straddle ≥ expected move) — premium-selling is better "
            "compensated; condor or credit spread to harvest it.",
            ["vol rich"], caution="Rich vol often means larger realised moves — defined risk only."))

    # two-sided note: complacent now but event ahead
    if vol_env == "complacent" and event_soon:
        notes.append("TWO-SIDED: complacent tape (theta tempting) BUT event in "
                     f"{event_near_days}d (gap risk). The honest combo is small/none "
                     "premium-sell + BUY cheap wings or a strangle for the event break.")

    if not sugg:
        sugg.append(Suggestion(0, "NEUTRAL", "stand_aside",
            "No clean edge from the current regime signals — stand aside.", []))

    # dedupe by (action,family), keep first; re-rank
    seen = set(); ranked = []
    for s in sugg:
        key = (s.action, s.family)
        if key in seen: continue
        seen.add(key); s.rank = len(ranked) + 1; ranked.append(s)

    return {
        "regime": {"vol_env": vol_env, "vol_cheap": vol_cheap,
                   "event_near_days": event_near_days, "bias": bias},
        "suggestions": [{"rank": s.rank, "action": s.action, "family": s.family,
                         "rationale": s.rationale, "fits": s.fits,
                         "caution": s.caution} for s in ranked],
        "notes": notes,
        "disclaimer": "Recommends a strategy FAMILY for the regime; the optimizer "
                      "then finds strikes, and the gates + risk_budget decide "
                      "whether and how big. Not a trade recommendation.",
    }


if __name__ == "__main__":
    import json
    cases = {
        "complacent + event in 2d (your current-ish setup)":
            dict(complacency=72, bias=0.1, event_near_days=2, expected_move_pts=290, straddle_pts=210),
        "neutral, range, slight bullish, no event":
            dict(complacency=58, bias=0.3, event_near_days=None, expected_move_pts=290, straddle_pts=300),
        "fearful, vol cheap, bearish view":
            dict(complacency=35, bias=-0.5, iv_percentile=0.2),
    }
    for tag, kw in cases.items():
        print(f"\n=== {tag} ===")
        r = suggest(**kw)
        for s in r["suggestions"]:
            print(f"  {s['rank']}. {s['action']} {s['family']} — {s['rationale']}")
        for n in r["notes"]:
            print(f"  • {n}")
