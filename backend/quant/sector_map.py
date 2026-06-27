"""
sector_map.py
-------------
SINGLE SOURCE OF TRUTH for the sector vocabulary. Both the Gemini tagger
(gemini_tag.CANONICAL_SECTORS) and the index weighting (index_attribution)
should import from here, so the enum Gemini emits and the weights it joins to
can never drift apart (the "Metals tagged but zero index weight" orphan bug).

Design rules:
* CANONICAL_SECTORS are mutually exclusive and each is actually represented in
  NIFTY 50 — so every sector Gemini can emit has real index weight to attach to.
* PSU and Realty are deliberately NOT here: PSU overlaps Banks/Energy (not
  mutually exclusive) and NIFTY 50 has no pure Realty constituent. Tagging them
  would orphan. If you want them as *informational* tags, keep that separate
  from the weighted bias.

WEIGHTS ARE A SEED SNAPSHOT — refresh from the NSE constituent file. The stable,
valuable part is the symbol→sector map; the weights drift daily and reset every
Mar/Sep review.
"""

from __future__ import annotations

from collections import defaultdict

CANONICAL_SECTORS = [
    "IT", "Banks", "Financials", "Auto", "Pharma", "FMCG", "Consumer",
    "Metals", "Energy", "Power", "Telecom", "Cement", "Infrastructure", "Aviation",
]

# symbol: (free_float_weight_%, canonical_sector).  ~NIFTY50, seed ≈ Jun-2026.
# (*) = added/approximate weight; verify against NSE.
NIFTY50: dict[str, tuple[float, str]] = {
    "RELIANCE":   (9.34, "Energy"),
    "HDFCBANK":   (6.29, "Banks"),
    "BHARTIARTL": (6.11, "Telecom"),
    "ICICIBANK":  (5.06, "Banks"),
    "SBIN":       (4.98, "Banks"),
    "TCS":        (3.93, "IT"),
    "ITC":        (3.50, "FMCG"),        # *
    "BAJFINANCE": (3.16, "Financials"),
    "LT":         (3.03, "Infrastructure"),
    "HINDUNILVR": (2.68, "FMCG"),
    "SUNPHARMA":  (2.36, "Pharma"),
    "AXISBANK":   (2.23, "Banks"),
    "MARUTI":     (2.23, "Auto"),
    "INFY":       (2.20, "IT"),
    "ADANIPORTS": (2.17, "Infrastructure"),
    "KOTAKBANK":  (2.11, "Banks"),
    "M&M":        (2.00, "Auto"),        # *
    "TATAMOTORS": (1.50, "Auto"),        # *
    "BAJAJFINSV": (1.30, "Financials"),  # *
    "TITAN":      (1.30, "Consumer"),    # *
    "NTPC":       (1.30, "Power"),       # *
    "ULTRACEMCO": (1.20, "Cement"),      # *
    "GRASIM":     (1.13, "Cement"),
    "EICHERMOT":  (1.10, "Auto"),
    "INDIGO":     (1.01, "Aviation"),
    "POWERGRID":  (1.00, "Power"),       # *
    "ASIANPAINT": (1.00, "Consumer"),    # *
    "TATASTEEL":  (1.00, "Metals"),      # *
    "WIPRO":      (0.97, "IT"),
    "SBILIFE":    (0.94, "Financials"),
    "BEL":        (0.90, "Infrastructure"),  # *
    "ONGC":       (0.90, "Energy"),      # *
    "HINDALCO":   (0.90, "Metals"),      # *
    "NESTLEIND":  (0.90, "FMCG"),        # *
    "TRENT":      (0.88, "Consumer"),
    "JSWSTEEL":   (0.80, "Metals"),      # *
    "COALINDIA":  (0.80, "Energy"),      # *
    "JIOFIN":     (0.83, "Financials"),
    "TECHM":      (0.73, "IT"),
    "CIPLA":      (0.70, "Pharma"),      # *
    "DRREDDY":    (0.70, "Pharma"),      # *
    "APOLLOHOSP": (0.70, "Pharma"),      # *
    "HDFCLIFE":   (0.70, "Financials"),  # *
    "SHRIRAMFIN": (0.70, "Financials"),  # *
    "BAJAJ-AUTO": (1.00, "Auto"),        # *
    "HEROMOTOCO": (0.50, "Auto"),        # *
    "TATACONSUM": (0.50, "FMCG"),        # *
    "BRITANNIA":  (0.40, "FMCG"),        # *
    "BPCL":       (0.40, "Energy"),      # *
}


def weights() -> dict[str, float]:
    return {s: w for s, (w, _) in NIFTY50.items()}


def sector_of() -> dict[str, str]:
    return {s: sec for s, (_, sec) in NIFTY50.items()}


def sector_weights() -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for _, (w, sec) in NIFTY50.items():
        out[sec] += w
    return dict(out)


def validate(enum=CANONICAL_SECTORS) -> dict:
    """Reconcile the enum with the weights. Flags orphan sectors (in the enum
    but zero index weight) and the share of index weight covered."""
    sw = sector_weights()
    orphans = [s for s in enum if sw.get(s, 0.0) == 0.0]
    not_in_enum = [s for s in sw if s not in enum]
    total = sum(sw.values())
    return {
        "sector_weights": {s: round(sw.get(s, 0.0), 2) for s in enum},
        "orphans_in_enum": orphans,                 # enum sectors with no weight
        "sectors_not_in_enum": not_in_enum,         # weighted sectors Gemini can't emit
        "total_weight_covered": round(total, 1),
    }


if __name__ == "__main__":
    import json
    rep = validate()
    print("sector weights (canonical):")
    for s, w in sorted(rep["sector_weights"].items(), key=lambda kv: -kv[1]):
        print(f"  {s:<15} {w:5.2f}%")
    print(f"\ntotal covered weight : {rep['total_weight_covered']}%")
    print(f"orphans in enum      : {rep['orphans_in_enum'] or 'none ✓'}")
    print(f"weighted but not in enum: {rep['sectors_not_in_enum'] or 'none ✓'}")
