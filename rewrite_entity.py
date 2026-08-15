content = '''"""
entity_extract.py
-----------------
Sharpens sector attribution by extracting named entities (companies) from text
and mapping each to its NIFTY constituent -> sector + index weight.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

try:
    from backend.quant.sector_map import NIFTY50, sector_of, weights  # type: ignore
    _HAVE_MAP = True
except Exception:
    _HAVE_MAP = False
    NIFTY50 = {
        "HDFCBANK": (11.2, "Financials", "Banks"), "ICICIBANK": (8.5, "Financials", "Banks"),
        "RELIANCE": (9.8, "Energy", "Energy"), "INFY": (4.9, "IT", "IT"), "TCS": (3.9, "IT", "IT"),
        "MARUTI": (1.9, "Auto", "Auto"), "TATAMOTORS": (2.1, "Auto", "Auto"),
        "SUNPHARMA": (1.6, "Healthcare", "Pharma"), "ITC": (3.8, "FMCG", "FMCG"),
    }
    def sector_of(): return {s: v[1] for s, v in NIFTY50.items()}
    def weights():   return {s: v[0] for s, v in NIFTY50.items()}

# ── alias table ────────────────────────────────────────────────────────
MANUAL_ALIASES = {
    "HDFCBANK": ["hdfc bank"],
    "ICICIBANK": ["icici bank", "icici"],
    "SBIN": ["state bank", "state bank of india"],
    "AXISBANK": ["axis bank"],
    "KOTAKBANK": ["kotak", "kotak mahindra"],
    "RELIANCE": ["reliance", "reliance industries", "ril"],
    "INFY": ["infosys"],
    "TCS": ["tcs", "tata consultancy"],
    "HCLTECH": ["hcl tech", "hcl technologies"],
    "WIPRO": ["wipro"],
    "TECHM": ["tech mahindra"],
    "MARUTI": ["maruti", "maruti suzuki"],
    "TATAMOTORS": ["tata motors"],
    "M&M": ["m&m", "mahindra & mahindra"],
    "BAJAJ-AUTO": ["bajaj auto"],
    "SUNPHARMA": ["sun pharma"],
    "DRREDDY": ["dr reddy", "dr reddys"],
    "CIPLA": ["cipla"],
    "ITC": ["itc"],
    "HINDUNILVR": ["hindustan unilever", "hul"],
    "NESTLEIND": ["nestle"],
    "LT": ["larsen", "l&t", "larsen & toubro"],
    "BHARTIARTL": ["bharti airtel", "airtel"],
    "TATASTEEL": ["tata steel"],
    "JSWSTEEL": ["jsw steel"],
    "HINDALCO": ["hindalco"],
    "NTPC": ["ntpc"],
    "POWERGRID": ["power grid", "powergrid"],
    "ONGC": ["ongc"],
    "COALINDIA": ["coal india"],
    "BAJFINANCE": ["bajaj finance"],
    "BAJAJFINSV": ["bajaj finserv"],
    "ADANIENT": ["adani enterprises"],
    "ADANIPORTS": ["adani ports"],
    "SBILIFE": ["sbi life"],
    "HDFCLIFE": ["hdfc life"],
    "ASIANPAINT": ["asian paints", "asian paint"],
    "TITAN": ["titan"],
    "ULTRACEMCO": ["ultratech", "ultratech cement"],
    "GRASIM": ["grasim"],
    "INDIGO": ["indigo", "interglobe"],
    "EICHERMOT": ["eicher"],
    "APOLLOHOSP": ["apollo hospital", "apollo hospitals"],
    "BRITANNIA": ["britannia"],
    "TATACONSUM": ["tata consumer"],
    "HEROMOTOCO": ["hero moto"],
    "BPCL": ["bpcl", "bharat petroleum"],
    "TRENT": ["trent"],
    "JIOFIN": ["jio financial"],
    "BEL": ["bharat electronics"],
    "SHRIRAMFIN": ["shriram finance"]
}

ALIASES: dict[str, str] = {}
for sym, aliases in MANUAL_ALIASES.items():
    for alias in aliases:
        ALIASES[alias] = sym

# multi-word aliases first so "hdfc bank" matches before "hdfc" (if it existed)
_ALIAS_KEYS = sorted(ALIASES, key=len, reverse=True)

if _HAVE_MAP:
    for alias, sym in ALIASES.items():
        if sym not in NIFTY50:
            import warnings
            warnings.warn(f"Orphan alias: {alias!r} -> {sym!r} (symbol not in NIFTY50)")

@dataclass
class ConstituentHit:
    symbol: str
    sector: str
    weight: float
    matched_text: str

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9& ]", " ", s.lower()).strip()

def match_entities(entity_strings: list[str]) -> list[ConstituentHit]:
    sec = sector_of()
    wts = NIFTY50
    hits: dict[str, ConstituentHit] = {}
    for ent in entity_strings:
        n = _norm(ent)
        for alias in _ALIAS_KEYS:
            if re.search(rf"\\b{re.escape(alias)}\\b", n):
                sym = ALIASES[alias]
                if sym in wts and sym not in hits:
                    w, s, _ = wts[sym]
                    hits[sym] = ConstituentHit(sym, s, w, ent)
                break
    return list(hits.values())

def weighted_sector_hits(hits: list[ConstituentHit]) -> dict[str, float]:
    out: dict[str, float] = {}
    for h in hits:
        out[h.sector] = round(out.get(h.sector, 0.0) + h.weight, 4)
    return out

def extract_constituents(text: str, nlp=None) -> list[ConstituentHit]:
    if nlp is not None:
        doc = nlp(text)
        orgs = [e.text for e in doc.ents if e.label_ in ("ORG", "PERSON", "GPE")]
        return match_entities(orgs + [text])
    return match_entities([text])

if __name__ == "__main__":
    def test(text: str, expected_sym: str, unexpected_sym: str = None):
        hits = extract_constituents(text)
        syms = [h.symbol for h in hits]
        assert expected_sym in syms or expected_sym is None, f"Expected {expected_sym} in {syms} for '{text}'"
        if unexpected_sym:
            assert unexpected_sym not in syms, f"Did not expect {unexpected_sym} in {syms} for '{text}'"
        print(f"PASS: {text[:40]}... -> {syms}")

    test("SBI Life reports strong VNB growth", "SBILIFE", "SBIN")
    test("HDFC AMC AUM crosses ...", None, "HDFCBANK")
    test("Bajaj Finserv Q1 results", "BAJAJFINSV", "BAJFINANCE")
    test("Bajaj Auto sales up", "BAJAJ-AUTO", "BAJFINANCE")
    test("Tech Mahindra wins large deal", "TECHM", "M&M")
    test("Tata Steel raises prices", "TATASTEEL", "TCS")
    test("HDFC Bank Q4 profit ...", "HDFCBANK", "HDFCLIFE")
    test("SBI cuts MCLR", "SBIN", "SBILIFE")
    print("All adversarial tests passed.")
'''
with open("entity_extract.py", "w") as f:
    f.write(content)
