"""
entity_extract.py
-----------------
Sharpens sector attribution by extracting named entities (companies) from text
and mapping each to its NIFTY constituent -> sector + industry + weight.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

try:
    from backend.quant.sector_tree import company_path, WEIGHTS
except Exception:
    def company_path(name): return None
    WEIGHTS = {}

@dataclass
class ConstituentHit:
    symbol: str
    sector: str
    industry: str
    weight: float
    matched_text: str

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9& ]", " ", s.lower()).strip()

def match_entities(entity_strings: list[str]) -> list[ConstituentHit]:
    hits: dict[str, ConstituentHit] = {}
    for ent in entity_strings:
        path = company_path(ent)
        if path:
            sec, ind, canon = path
            if canon not in hits:
                w = WEIGHTS.get(canon, 0.0)
                hits[canon] = ConstituentHit(canon, sec, ind, w, ent)
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
    def test(text: str, expected_canon: str, unexpected_canon: str = None):
        hits = extract_constituents(text)
        syms = [h.symbol for h in hits]
        assert expected_canon in syms or expected_canon is None, f"Expected {expected_canon} in {syms} for '{text}'"
        if unexpected_canon:
            assert unexpected_canon not in syms, f"Did not expect {unexpected_canon} in {syms} for '{text}'"
        print(f"PASS: {text[:40]}... -> {syms}")

    test("SBI Life reports strong VNB growth", "SBI Life Insurance", "State Bank of India")
    test("HDFC AMC AUM crosses ...", None, "HDFC Bank")
    test("Bajaj Finserv Q1 results", "Bajaj Finserv", "Bajaj Finance")
    test("Bajaj Auto sales up", "Bajaj Auto", "Bajaj Finance")
    test("Tech Mahindra wins large deal", "Tech Mahindra", "Mahindra & Mahindra")
    test("Tata Steel raises prices", "Tata Steel", "TCS")
    test("HDFC Bank Q4 profit ...", "HDFC Bank", "HDFC Life Insurance")
    test("SBI cuts MCLR", "State Bank of India", "SBI Life Insurance")
    print("All adversarial tests passed.")
