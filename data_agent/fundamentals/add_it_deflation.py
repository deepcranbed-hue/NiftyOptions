#!/usr/bin/env python3
"""Add the IT services AI-deflation factor to the curated library, and one
name-specific headwind line to each of the five Nifty 50 IT constituents.

Idempotent: re-running replaces the factor and does not duplicate the headwind lines.
"""
import json, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))

FACTOR = {
    "id": "it_ai_deflation",
    "name": "IT services AI deflation and pricing pressure",
    "direction": "headwind",
    # Not fragile: fragile means "one headline reverses it". This is a repricing of the
    # unit of sale, running over years, and nothing in a single quarter reverses it.
    "fragile": False,
    "status": (
        "Clients are shrinking existing contracts to fund AI work rather than adding "
        "budget. Google cut ~$50m off a ~$200m annual HCLTech application-development "
        "and engineering contract as part of a wider cost programme touching several "
        "vendors; Meta trimmed a Wipro deal by $15-20m and partially pulled Accenture, "
        "Teleperformance and Concentrix. Hi-tech clients moved first because they can "
        "see AI use-cases in their own workflows and several now run in-house models. "
        "Non-tech enterprises are following on price: 20-30% discounts are being sought "
        "on new contracts, which is producing vendor consolidation and share loss to "
        "mid-tier names (Persistent, Mphasis, Coforge, Zensar all growing double digits "
        "while tier-1 does not). Three of the top five reported a sequential operating "
        "margin decline in Q1 FY27, Infosys the exception. Nifty IT is 13.0% of the "
        "index (INFY 5.5, TCS 4.0, HCLTECH 1.6, WIPRO 1.0, TECHM 0.9)."
    ),
    "transmission": (
        "This is a REVENUE-BASE factor, not a demand-cycle one, and that is what makes it "
        "different from every other headwind in this library. A demand slowdown postpones "
        "spend; AI deflation shrinks the price of the same delivered work — HCLTech's CEO "
        "put it as 'a $100m deal is now an $80m deal'. It therefore does not mean-revert "
        "when budgets recover, and it compounds: BNP Paribas puts the drag at ~3pp of "
        "annual revenue growth for another three to five years, Ambit at 15-20% cumulative "
        "over three to four years (3-4%/yr) and says the industry is not yet halfway "
        "through the cycle. Margins take a second hit from the cost side, because wage "
        "bills are 50-60% of costs and have not shrunk while AI skilling and partnership "
        "spend add 1-2%. A third effect is on VISIBILITY rather than level: as pricing "
        "moves from headcount-based billing to business-outcome deals, revenue is harder "
        "to recognise early, which widens the distribution of quarterly prints and is a "
        "reason to expect larger results-day moves in the sector, not smaller. "
        "DISTINCT FROM ai_capex_roi: that factor is about the BUYERS of AI compute and "
        "reaches India through global beta; this one is about the SELLERS of IT services "
        "and reaches the index through 13% of its weight directly. Cheap tokens are the "
        "shared cause; the two legs transmit on different clocks."
    ),
    "facts": [
        {"date": "2026-08-14",
         "note": ("Google cut ~$50m from a ~$200m annual HCLTech contract covering part of its "
                  "application development and engineering work — a cost-efficiency programme "
                  "repurposing budget to AI, applied across several IT partners. Wipro's deal "
                  "with Meta trimmed by $15-20m; Meta also partially pulled Accenture, "
                  "Teleperformance and Concentrix."),
         "source": "Moneycontrol; corroborated by StartupTalky, CIO Tech Outlook, Digital Terminal"},
        {"date": "2026-08-14",
         "note": ("Clients seeking 20-30% discounts on new contracts across telecom, SaaS, "
                  "hi-tech and financial services (Gaurav Vasu, UnearthInsight). Intense "
                  "competition for the same accounts is driving vendor consolidation at lower "
                  "price points. UnearthInsight expects sector operating margins down 0.5-1pp "
                  "cumulatively in FY27."),
         "source": "Moneycontrol / UnearthInsight"},
        {"date": "2026-08-14",
         "note": ("Ambit (Ashwin Mehta) after Q1 FY27: deflation is running at the LOW end of "
                  "what companies guided and 'could be higher going forward'; the industry has "
                  "not passed the midway point of the cycle. BNP Paribas (Kumar Rakesh): at "
                  "least 3pp of annual revenue growth lost to AI deflation for another 3-5 years."),
         "source": "Moneycontrol; Ambit note of late June 2026 (15-20% cumulative over 3-4 years)"},
        {"date": "2026-04",
         "note": ("HCLTech CEO C Vijayakumar quantified it on the Q4 FY26 call: 'Let's say "
                  "something was a $100 million deal, now could be an $80 million deal' — "
                  "estimated impact ~2-3% a year."),
         "source": "HCLTech Q4 FY26 press briefing"},
        {"date": "2026-08-14",
         "note": ("The repo's own screen agrees on the delivery side and disagrees on the price. "
                  "Every IT constituent FAILS the delivered-growth leg on 5-year net profit CAGR "
                  "(INFY 8.8%, TCS 8.7%, HCLTECH 8.3%, WIPRO 4.1%, TECHM 1.7% against a 10% bar) "
                  "while three of five pass consistency at 88.9% — they grew almost every year, "
                  "slowly. But implied forward growth is NOT uniformly low: INFY is priced for "
                  "+3.9% and WIPRO +9.7%, whereas HCLTECH is priced for +24.6% on 3.9% delivered "
                  "over three years and TECHM for +50.1% on -0.1%. The deflation is priced into "
                  "the two cheapest names and not into the two carrying the largest expectation "
                  "gaps — and HCLTech is the name that just lost a quarter of its Google contract."),
         "source": "quality_growth.json (screen), expectation_snapshots.json 2026-08-08"},
    ],
    "watch": [
        "Deal TCV versus deal REVENUE. Renegotiations show up as flat-to-lower revenue on "
        "unchanged or rising TCV — a book-to-bill that looks fine while pricing erodes underneath.",
        "Sequential operating margin, not YoY. Three of the top five fell QoQ in Q1 FY27; "
        "the sector-wide guide is -0.5 to -1pp cumulatively across FY27.",
        "Mid-tier growth minus tier-1 growth (Persistent, Coforge, Mphasis, Zensar). A widening "
        "spread is vendor consolidation moving share DOWN-market, which is the share-loss channel "
        "rather than the demand channel.",
        "Hi-tech client concentration in the disclosures — those accounts renegotiated first and "
        "are the leading indicator for the non-tech verticals.",
        "Whether outcome-based pricing shows up as WIDER results-day moves. If revenue becomes "
        "harder to forecast, the sector's earnings-day distribution should fatten; "
        "earnings_reactions.json is where that would be visible.",
        "The expectation gap, not the news. INFY at +3.9% implied has little left to disappoint; "
        "HCLTECH at +24.6% and TECHM at +50.1% are the names where a deflation print collides "
        "with a multiple.",
    ],
    "scenarios": [
        {"trigger": "Another named tier-1 contract is publicly cut or repriced",
         "nifty_pct": [-0.5, -0.2], "horizon": "days",
         "anchor": ("Arithmetic, not an episode: IT is 13.0% of the index, so a 2-4% sector day "
                    "is 0.26-0.52% of the Nifty ≈ 65-125 points at 24,400. Nifty IT is already "
                    "~39% off its 2024 peak, which is why single headlines now move it less than "
                    "the first ones did")},
        {"trigger": "Sector-wide FY27 margin guidance cut at the Q2 FY27 round",
         "nifty_pct": [-1.0, -0.4], "horizon": "1-2 weeks",
         "anchor": ("A guidance cut repriced the sector rather than one name; the -0.5 to -1pp "
                    "FY27 margin path is the consensus already in the price, so the move comes "
                    "from exceeding it")},
        {"trigger": "Deflation proves front-loaded — pricing stabilises and AI work converts to billable revenue",
         "nifty_pct": [0.5, 1.5], "horizon": "quarters",
         "anchor": ("INFY at +3.9% and WIPRO at +9.7% implied forward growth are priced for very "
                    "little; the asymmetry in those two is upward. This is the leg the bears "
                    "under-weight and it has no historical analogue yet")},
    ],
}

# One line each. Name-specific — a generic line repeated five times is not information.
HEADWINDS = {
    "HCLTECH": ("Google cut ~$50m off a ~$200m annual contract (Aug 2026) as part of an "
                "AI-repurposing programme — and the stock is priced for ~+25% earnings growth "
                "against 3.9% delivered over three years, the widest gap in the sector after TECHM"),
    "WIPRO":   ("Meta trimmed a deal by $15-20m amid hi-tech client repricing; weakest 5-year "
                "profit CAGR of the tier-1 group at 4.1%, so AI deflation lands on the thinnest "
                "delivery record"),
    "INFY":    ("Clients seeking 20-30% discounts on new contracts and repurposing existing "
                "budgets to AI; the offset is that at ~+4% implied forward growth the market is "
                "already paying for almost none of the growth it once assumed"),
    "TCS":     ("AI deflation reprices the unit of sale rather than delaying it — 'a $100m deal "
                "is now an $80m deal', ~2-3%/yr — against a 5-year profit CAGR of 8.7% that has "
                "been slowing (5.3% over three years)"),
    "TECHM":   ("Priced for ~+50% earnings growth on a 3-year profit CAGR of -0.1%, the largest "
                "expectation gap in the index's IT block, while the sector's pricing base deflates"),
}

SHARED = ("Outcome-based deals replacing headcount billing make revenue harder to recognise "
          "early — a visibility problem on top of a pricing one, which widens the range of "
          "quarterly outcomes rather than narrowing it")


def patch_factors(path):
    with open(path) as f:
        doc = json.load(f)
    doc["factors"] = [x for x in doc["factors"] if x["id"] != FACTOR["id"]]
    # Insert next to ai_capex_roi so the two related legs read together.
    ids = [x["id"] for x in doc["factors"]]
    at = ids.index("ai_capex_roi") + 1 if "ai_capex_roi" in ids else len(doc["factors"])
    doc["factors"].insert(at, FACTOR)
    doc["as_of"] = "2026-08-14"
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    return len(doc["factors"])


def patch_drivers(path):
    with open(path) as f:
        doc = json.load(f)
    n = 0
    for sym, line in HEADWINDS.items():
        c = doc["companies"].get(sym)
        if not c:
            print(f"  !! {sym} not in nifty50_drivers.json")
            continue
        hw = c.setdefault("headwinds", [])
        for text in (line, SHARED):
            key = text[:40]
            if not any(key in h for h in hw):
                hw.append(text)
                n += 1
    doc["as_of"] = "2026-08-14"
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    return n


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    nf = patch_factors(os.path.join(root, "nifty_factors.json"))
    nd = patch_drivers(os.path.join(root, "nifty50_drivers.json"))
    print(f"nifty_factors.json: {nf} factors (it_ai_deflation inserted after ai_capex_roi)")
    print(f"nifty50_drivers.json: {nd} headwind lines added across 5 IT names")
