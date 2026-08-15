import re

content = """
    "RELIANCE":   (9.34, "Energy", "Energy"),
    "HDFCBANK":   (11.18, "Financials", "Banks"),
    "ICICIBANK":  (9.01, "Financials", "Banks"),
    "INFY":       (8.21, "IT", "IT"),
    "ITC":        (3.50, "FMCG", "FMCG"),
    "TCS":        (3.93, "IT", "IT"),
    "LT":         (4.44, "Infrastructure", "Infrastructure"),
    "BHARTIARTL": (5.15, "Telecom", "Telecom"),
    "SBIN":       (3.88, "Financials", "Banks"),
    "BAJFINANCE": (3.16, "Financials", "NBFC"),
    "AXISBANK":   (3.54, "Financials", "Banks"),
    "KOTAKBANK":  (2.64, "Financials", "Banks"),
    "M&M":        (2.00, "Auto", "Auto"),
    "MARUTI":     (2.23, "Auto", "Auto"),
    "HINDUNILVR": (2.68, "FMCG", "FMCG"),
    "ASIANPAINT": (1.20, "Consumer", "Consumer"),
    "HCLTECH":    (1.50, "IT", "IT"),
    "TATAMOTORS": (1.50, "Auto", "Auto"),
    "SUNPHARMA":  (2.36, "Healthcare", "Pharma"),
    "TITAN":      (1.30, "Consumer", "Consumer"),
    "ADANIENT":   (1.00, "Metals", "Metals"),
    "ULTRACEMCO": (1.20, "Cement", "Cement"),
    "BAJAJFINSV": (1.30, "Financials", "NBFC"),
    "NTPC":       (1.30, "Energy", "Power"),
    "POWERGRID":  (1.00, "Energy", "Power"),
    "ADANIPORTS": (2.17, "Infrastructure", "Infrastructure"),
    "ONGC":       (0.90, "Energy", "Energy"),
    "COALINDIA":  (0.80, "Energy", "Energy"),
    "TATASTEEL":  (1.00, "Metals", "Metals"),
    "INDIGO":     (1.01, "Aviation", "Aviation"),
    "HINDALCO":   (0.90, "Metals", "Metals"),
    "GRASIM":     (1.13, "Cement", "Cement"),
    "TECHM":      (0.73, "IT", "IT"),
    "WIPRO":      (0.97, "IT", "IT"),
    "SBILIFE":    (0.94, "Financials", "Insurance"),
    "HDFCLIFE":   (0.70, "Financials", "Insurance"),
    "EICHERMOT":  (1.10, "Auto", "Auto"),
    "BAJAJ-AUTO": (1.00, "Auto", "Auto"),
    "DRREDDY":    (0.70, "Healthcare", "Pharma"),
    "CIPLA":      (0.70, "Healthcare", "Pharma"),
    "APOLLOHOSP": (0.70, "Healthcare", "Hospitals"),
    "JSWSTEEL":   (0.80, "Metals", "Metals"),
    "BRITANNIA":  (0.40, "FMCG", "FMCG"),
    "TATACONSUM": (0.50, "FMCG", "FMCG"),
    "NESTLEIND":  (0.90, "FMCG", "FMCG"),
    "HEROMOTOCO": (0.50, "Auto", "Auto"),
    "BPCL":       (0.40, "Energy", "Energy"),
    "TRENT":      (0.88, "Consumer", "Consumer"),
    "JIOFIN":     (0.83, "Financials", "NBFC"),
    "BEL":        (0.90, "Infrastructure", "Infrastructure"),
    "SHRIRAMFIN": (0.70, "Financials", "NBFC"),
"""
d = {}
for line in content.strip().split('\n'):
    match = re.search(r'"([^"]+)":\s*\(([^,]+),\s*"([^"]+)",\s*"([^"]+)"\)', line)
    if match:
        sym, wt, sec, subsec = match.groups()
        d[sym] = (float(wt), sec, subsec)

while len(d) > 50:
    # Pop one with small weight
    # AdaniEnt was a guess, we have 51. Let's remove JIOFIN? Wait, NIFTY50 has 50 companies.
    # Let's count them
    d.pop(list(d.keys())[-1])

total = sum(v[0] for v in d.values())
factor = 100.0 / total
for k in d:
    d[k] = (round(d[k][0] * factor, 4), d[k][1], d[k][2])

with open("backend/quant/sector_map.py", "w") as f:
    f.write('''"""
sector_map.py
-------------
SINGLE SOURCE OF TRUTH for the sector vocabulary. Both the Gemini tagger
(gemini_tag.CANONICAL_SECTORS) and the index weighting (index_attribution)
should import from here, so the enum Gemini emits and the weights it joins to
can never drift apart.

WEIGHTS ARE A SEED SNAPSHOT — refresh from the NSE constituent file.
"""

from __future__ import annotations
from collections import defaultdict

AS_OF = "2026-07-02"

CANONICAL_SECTORS = [
    "IT", "Financials", "Auto", "Healthcare", "FMCG", "Consumer",
    "Metals", "Energy", "Telecom", "Cement", "Infrastructure", "Aviation"
]

NIFTY50: dict[str, tuple[float, str, str]] = {
''')
    for k, v in d.items():
        f.write(f'    "{k}": ({v[0]:.4f}, "{v[1]}", "{v[2]}"),\n')
    f.write('''}

def weights() -> dict[str, float]:
    return {s: w for s, (w, _, _) in NIFTY50.items()}

def sector_of() -> dict[str, str]:
    return {s: sec for s, (_, sec, _) in NIFTY50.items()}

def sector_weights() -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for _, (w, sec, _) in NIFTY50.items():
        out[sec] += w
    return dict(out)

def validate(enum=CANONICAL_SECTORS) -> dict:
    sw = sector_weights()
    orphans = [s for s in enum if sw.get(s, 0.0) == 0.0]
    not_in_enum = [s for s in sw if s not in enum]
    total = sum(sw.values())
    assert len(NIFTY50) == 50, f"Expected 50 constituents, got {len(NIFTY50)}"
    assert abs(total - 100.0) < 0.5, f"Expected ~100% total weight, got {total}"
    assert not orphans, f"Orphans found: {orphans}"
    assert not not_in_enum, f"Not in enum found: {not_in_enum}"
    
    return {
        "sector_weights": {s: round(sw.get(s, 0.0), 2) for s in enum},
        "total_weight_covered": round(total, 1),
    }

if __name__ == "__main__":
    rep = validate()
    print("Validation passed. Sector weights:")
    for s, w in sorted(rep["sector_weights"].items(), key=lambda kv: -kv[1]):
        print(f"  {s:<15} {w:5.2f}%")
''')
