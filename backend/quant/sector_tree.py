"""
sector_tree.py
--------------
3-level NIFTY 50 hierarchy: Sector -> Industry Group -> Company.
VERIFIED constituents (post Sept-2025 rebalance; March-2026 review made no
changes): IndiGo & Max Healthcare IN; Hero MotoCorp & IndusInd Bank OUT.

Design rules:
  * The TREE defines MEMBERSHIP only (static shape). WEIGHTS live in a separate
    dict with an as_of date — they change with every price move/rebalance and
    must be refreshable without touching the hierarchy.
  * Aggregation is bottom-up weight-based at every level:
      company = weight * sentiment; industry = sum(companies);
      sector = sum(industries); index = sum(sectors).
    Same math at every level -> drill-down numbers reconcile exactly.
  * Only actual constituents may appear; validate() enforces 50 companies,
    no duplicates, weights covering all leaves and summing ~100.
"""
from __future__ import annotations

SECTOR_TREE = {
  "Financials": {
    "Private Banks": ["HDFC Bank", "ICICI Bank", "Axis Bank", "Kotak Mahindra Bank"],
    "Public Banks": ["State Bank of India"],
    "NBFC": ["Bajaj Finance", "Bajaj Finserv", "Shriram Finance", "Jio Financial Services"],
    "Insurance": ["SBI Life Insurance", "HDFC Life Insurance"],
  },
  "Information Technology": {
    "IT Services": ["TCS", "Infosys", "HCLTech", "Wipro", "Tech Mahindra"],
  },
  "Energy": {
    "Oil & Gas": ["Reliance Industries", "ONGC"],
    "Power Utilities": ["NTPC", "Power Grid"],
    "Coal & Mining Fuel": ["Coal India"],
  },
  "Automobile": {
    "Passenger & Commercial Vehicles": ["Maruti Suzuki", "Mahindra & Mahindra", "Tata Motors"],
    "Two Wheelers": ["Bajaj Auto", "Eicher Motors"],
  },
  "FMCG & Consumer": {
    "FMCG": ["Hindustan Unilever", "ITC", "Nestle India", "Tata Consumer Products"],
    "Retail & Discretionary": ["Titan", "Trent", "Eternal", "Asian Paints"],
  },
  "Healthcare": {
    "Pharmaceuticals": ["Sun Pharma", "Cipla", "Dr Reddys"],
    "Hospitals": ["Apollo Hospitals", "Max Healthcare"],
  },
  "Metals & Mining": {
    "Steel": ["Tata Steel", "JSW Steel"],
    "Non-ferrous": ["Hindalco"],
  },
  "Infrastructure & Capital Goods": {
    "Engineering & Construction": ["Larsen & Toubro"],
    "Defence Electronics": ["Bharat Electronics"],
    "Ports & Logistics": ["Adani Ports"],
    "Conglomerate": ["Adani Enterprises"],
  },
  "Telecom": {
    "Telecom Services": ["Bharti Airtel"],
  },
  "Cement & Building Materials": {
    "Cement": ["UltraTech Cement", "Grasim"],
  },
  "Aviation": {
    "Airlines": ["InterGlobe Aviation"],
  },
}

# WEIGHTS: separate from the tree — dynamic, refresh from the NSE factsheet.
# Anchored to published Jun-30-2026 values where available; remainder are
# indicative and MUST be refreshed by the weekly weight update.
WEIGHTS_AS_OF = "2026-06-30"
WEIGHTS = {
  "Reliance Industries": 9.24, "HDFC Bank": 6.49, "Bharti Airtel": 5.96,
  "ICICI Bank": 5.21, "State Bank of India": 5.00, "TCS": 3.88,
  "Bajaj Finance": 3.30, "Larsen & Toubro": 3.01, "Hindustan Unilever": 2.63,
  "Sun Pharma": 2.36, "Maruti Suzuki": 2.34, "Axis Bank": 2.21, "Adani Ports": 2.20,
  "Infosys": 4.10, "ITC": 2.60, "Kotak Mahindra Bank": 2.45, "Mahindra & Mahindra": 2.30,
  "NTPC": 1.55, "HCLTech": 1.45, "Titan": 1.40, "UltraTech Cement": 1.35,
  "Tata Motors": 1.30, "Power Grid": 1.25, "Bajaj Finserv": 1.20, "Tata Steel": 1.15,
  "Trent": 1.10, "Asian Paints": 1.05, "Coal India": 1.00, "ONGC": 1.00,
  "Bajaj Auto": 0.95, "Grasim": 0.95, "Eternal": 0.95, "Nestle India": 0.90,
  "Tech Mahindra": 0.85, "JSW Steel": 0.85, "Hindalco": 0.85, "Cipla": 0.80,
  "Wipro": 0.80, "Eicher Motors": 0.75, "Dr Reddys": 0.70, "Apollo Hospitals": 0.70,
  "Adani Enterprises": 0.70, "Shriram Finance": 0.65, "SBI Life Insurance": 0.65,
  "HDFC Life Insurance": 0.65, "Jio Financial Services": 0.60, "Bharat Electronics": 0.60,
  "Max Healthcare": 0.55, "InterGlobe Aviation": 0.55, "Tata Consumer Products": 0.56,
}

# Aliases for entity matching (LLM/company detection -> canonical leaf name)
ALIASES = {
  "hdfcbank": "HDFC Bank", "icici": "ICICI Bank",
  "sbi": "State Bank of India", "state bank": "State Bank of India",
  "airtel": "Bharti Airtel", "ril": "Reliance Industries",
  "reliance": "Reliance Industries", "tata consultancy": "TCS",
  "l&t": "Larsen & Toubro", "larsen": "Larsen & Toubro",
  "hul": "Hindustan Unilever", "m&m": "Mahindra & Mahindra",
  "mahindra": "Mahindra & Mahindra", "maruti": "Maruti Suzuki",
  "zomato": "Eternal", "indigo": "InterGlobe Aviation",
  "kotak": "Kotak Mahindra Bank", "dr reddy": "Dr Reddys",
  "ultratech": "UltraTech Cement", "bel": "Bharat Electronics",
  "sbi life": "SBI Life Insurance", "hdfc life": "HDFC Life Insurance",
  "asian paint": "Asian Paints", "apollo hospital": "Apollo Hospitals",
  "tata consumer": "Tata Consumer Products", "tech mahindra": "Tech Mahindra",
  "tata motors": "Tata Motors",
}


def all_companies():
    return [c for s in SECTOR_TREE.values() for ind in s.values() for c in ind]


def company_path(name: str):
    """Resolve a (possibly aliased) name -> (sector, industry, canonical)."""
    n = name.strip().lower()
    canon = None
    for c in all_companies():
        if c.lower() == n:
            canon = c; break
    if canon is None:
        import re
        # Sort aliases by length descending so longer ones match first
        for a in sorted(ALIASES.keys(), key=len, reverse=True):
            if re.search(rf"\b{re.escape(a)}\b", n):
                canon = ALIASES[a]; break
    if canon is None:
        for c in all_companies():          # loose contains
            if c.lower() in n or n in c.lower():
                canon = c; break
    if canon is None:
        return None
    for sec, inds in SECTOR_TREE.items():
        for ind, cs in inds.items():
            if canon in cs:
                return (sec, ind, canon)
    return None


def aggregate(company_sentiments: dict) -> dict:
    """{company: sentiment(-1..1)} -> weighted contributions at every level.
    contribution = weight/100 * sentiment; sums reconcile bottom-up exactly."""
    ind_c, sec_c, comp_c, unmatched = {}, {}, {}, []
    for name, s in company_sentiments.items():
        path = company_path(name)
        if not path:
            unmatched.append(name); continue
        sec, ind, canon = path
        w = WEIGHTS.get(canon, 0.0) / 100.0
        contrib = round(w * s, 4)
        comp_c[canon] = {"sector": sec, "industry": ind, "weight": WEIGHTS.get(canon),
                         "sentiment": s, "contribution": contrib}
        ind_c[(sec, ind)] = round(ind_c.get((sec, ind), 0) + contrib, 4)
        sec_c[sec] = round(sec_c.get(sec, 0) + contrib, 4)
    return {"index_bias": round(sum(sec_c.values()), 4),
            "sectors": sec_c,
            "industries": {f"{s} / {i}": v for (s, i), v in ind_c.items()},
            "companies": comp_c, "unmatched": unmatched,
            "weights_as_of": WEIGHTS_AS_OF,
            "caveat": "Bias covers only companies WITH news this window (not the "
                      "whole index). Weights as of the stated date — refresh weekly."}


def validate() -> dict:
    cs = all_companies()
    dups = {c for c in cs if cs.count(c) > 1}
    missing_w = [c for c in cs if c not in WEIGHTS]
    extra_w = [c for c in WEIGHTS if c not in cs]
    total = round(sum(WEIGHTS.get(c, 0) for c in cs), 1)
    ok = (len(cs) == 50 and not dups and not missing_w and not extra_w
          and 95 <= total <= 105)
    return {"n_companies": len(cs), "duplicates": sorted(dups),
            "missing_weights": missing_w, "orphan_weights": extra_w,
            "weights_sum": total, "ok": ok}


if __name__ == "__main__":
    v = validate()
    print(f"VALIDATE: {v['n_companies']} companies, dups={v['duplicates']}, "
          f"missing_w={v['missing_weights']}, orphans={v['orphan_weights']}, "
          f"weight_sum={v['weights_sum']} -> {'OK' if v['ok'] else 'FAIL'}")
    print("\nHDFC beat example:")
    out = aggregate({"HDFC Bank": +0.6, "Infosys": -0.3, "indigo": +0.4})
    print(f"  index_bias={out['index_bias']:+.4f}")
    for s, c in out["sectors"].items(): print(f"  {s}: {c:+.4f}")
    print("  drill:", out["companies"]["HDFC Bank"])
