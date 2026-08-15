#!/usr/bin/env python3
"""
narratives/earnings.py — the Earnings narrative.

Bank results are not a new ARCHITECTURE, they are a new NARRATIVE. This plugin covers
earnings for every sector; the per-industry differences live in EXTRACTORS, because a
bank's KPIs (NIM, CASA, GNPA, credit cost) share nothing with IT's (deal TCV, attrition,
margin) or Auto's (volumes, realisation, mix).

FIVE LAYERS, STORED SEPARATELY
------------------------------
  1 metrics     reported numbers        NIM 3.48 vs 3.55 -> down
  2 guidance    what management SAID     loan growth "lower"
  3 brokerage   external opinion         Anand Rathi BUY, target 963 (was 945)
  4 management  commentary               "deposit franchise improving"
  5 peer        relative standing        ICICI loan growth 18% vs HDFC 15%

They are kept apart on purpose. Merging a reported NIM with a broker's target price and
a CEO quote produces one number that cannot be audited or contradicted — you can no
longer ask "was the FUNDAMENTAL bad but the VALUATION attractive?", which is usually the
actual question. Each layer emits its own NarrativeSignal with its own dimension.

WHAT THIS PLUGIN DOES NOT DO
----------------------------
It never computes an overall bank/sector score. Banks are also being driven today by
Oil, Treasury and RBI; if this plugin returned `overall = -0.22` and Treasury returned
`-0.30`, the shared rate reasoning would be counted twice. Aggregation belongs in
sector_analyzer.py, which sees every narrative at once.
"""

from __future__ import annotations

import re

from narratives.base import Narrative, Activation, NarrativeSignal, register, _text

# ── per-industry KPI extractors ──────────────────────────────────────────────
# (metric, regex capturing the value, higher_is_better)
_BANK_METRICS = [
    ("NIM",          r"\bnim\b[^\d%]{0,20}([\d.]+)\s*%", True),
    ("Loan Growth",  r"(?:loan|advances|credit)\s+growth[^\d%]{0,20}([\d.]+)\s*%", True),
    ("Deposit Growth", r"deposit\s+growth[^\d%]{0,20}([\d.]+)\s*%", True),
    ("CASA",         r"\bcasa\b[^\d%]{0,20}([\d.]+)\s*%", True),
    ("GNPA",         r"\b(?:gnpa|gross npa)\b[^\d%]{0,20}([\d.]+)\s*%", False),
    ("NNPA",         r"\b(?:nnpa|net npa)\b[^\d%]{0,20}([\d.]+)\s*%", False),
    ("PCR",          r"\bpcr\b[^\d%]{0,20}([\d.]+)\s*%", True),
    ("Credit Cost",  r"credit\s+cost[^\d%]{0,20}([\d.]+)\s*%", False),
    ("ROA",          r"\broa\b[^\d%]{0,20}([\d.]+)\s*%", True),
]
_IT_METRICS = [
    ("Deal TCV",     r"\btcv\b[^\d$]{0,20}\$?\s*([\d.]+)\s*(?:bn|billion)", True),
    ("Attrition",    r"attrition[^\d%]{0,20}([\d.]+)\s*%", False),
    ("EBIT Margin",  r"(?:ebit|operating)\s+margin[^\d%]{0,20}([\d.]+)\s*%", True),
]
_AUTO_METRICS = [
    ("Volumes",      r"(?:volume|dispatch|sales)\s+(?:grew|rose|up)[^\d%]{0,15}([\d.]+)\s*%", True),
    ("EBITDA Margin", r"ebitda\s+margin[^\d%]{0,20}([\d.]+)\s*%", True),
]
_EXTRACTORS = {"Banks": _BANK_METRICS, "IT Services": _IT_METRICS, "Auto": _AUTO_METRICS}

# which sector a headline belongs to (name -> sector)
_SECTOR_OF = {
    "hdfc bank": "Banks", "icici": "Banks", "axis bank": "Banks", "kotak": "Banks",
    "state bank": "Banks", "sbi": "Banks", "bajaj finance": "Banks", "indusind": "Banks",
    "tcs": "IT Services", "infosys": "IT Services", "wipro": "IT Services",
    "hcl": "IT Services", "tech mahindra": "IT Services",
    "maruti": "Auto", "mahindra": "Auto", "tata motors": "Auto", "bajaj auto": "Auto",
    "hero motocorp": "Auto", "eicher": "Auto",
}

_BEAT = ("beat", "beats", "tops estimate", "above estimate", "ahead of estimate")
_MISS = ("miss", "misses", "below estimate", "shortfall", "disappoint")
_GUIDE_DOWN = ("guidance cut", "lowers guidance", "trims guidance", "sees lower",
               "cautious outlook", "moderate", "slower")
_GUIDE_UP = ("raises guidance", "guidance raised", "upbeat outlook", "sees higher",
             "confident", "accelerate")
_RATING = re.compile(r"\b(buy|sell|hold|accumulate|reduce|outperform|underperform|"
                     r"overweight|underweight)\b", re.I)
_TARGET = re.compile(r"target(?:\s+price)?[^\d]{0,15}(?:rs\.?|₹)?\s*([\d,]+)", re.I)


@register
class EarningsNarrative(Narrative):
    name = "Earnings"
    priority = 2
    saturation = 4.0
    horizon = "1-2 quarters"
    triggers = ["results", "earnings", "q1", "q2", "q3", "q4", "profit", "pat",
                "revenue", "margin", "guidance", "beat", "miss", "quarterly"]

    # ---- Layer 1-5 extraction, kept separate -----------------------------
    def extract(self, news, snap=None) -> dict:
        """Return the five layers, unmerged. No scoring here."""
        out = {"metrics": [], "guidance": [], "brokerage": [],
               "management": [], "peer": []}
        for n in news or []:
            t = _text(n)
            # GATE ON THE COMPANY, NOT ON "results".
            # A results-keyword gate here silently dropped two of the five layers:
            # "HDFC Bank sees slower loan growth ahead" (guidance) and "Anand Rathi
            # maintains BUY, target 963" (brokerage) contain no results word at all,
            # yet they are exactly the forward-looking layers this plugin exists to
            # separate. Each layer now decides for itself whether it applies.
            company = next((c for c in _SECTOR_OF if c in t), "")
            if not company:
                continue
            is_result = any(k in t for k in
                            ("result", "earnings", "profit", "pat", "quarter", "revenue"))
            sector = _SECTOR_OF[company]
            disp = company.title()
            title = (n.get("title") or "")[:140]

            # 1 metrics — reported numbers only (needs an actual results item)
            for metric, pat, higher_better in (_EXTRACTORS.get(sector, []) if is_result else []):
                m = re.search(pat, t, re.I)
                if not m:
                    continue
                try:
                    val = float(m.group(1).replace(",", ""))
                except ValueError:
                    continue
                out["metrics"].append({"company": disp, "sector": sector,
                                       "metric": metric, "value": val,
                                       "higher_is_better": higher_better,
                                       "evidence": title})

            # 1b beat / miss — the headline verdict, distinct from any single metric
            if is_result and any(k in t for k in _BEAT):
                out["metrics"].append({"company": disp, "sector": sector, "metric": "Result vs est.",
                                       "value": None, "higher_is_better": True,
                                       "verdict": "beat", "evidence": title})
            elif any(k in t for k in _MISS):
                out["metrics"].append({"company": disp, "sector": sector, "metric": "Result vs est.",
                                       "value": None, "higher_is_better": True,
                                       "verdict": "miss", "evidence": title})

            # 2 guidance — what management SAID about the future
            if any(k in t for k in _GUIDE_DOWN):
                out["guidance"].append({"company": disp, "sector": sector,
                                        "direction": "Negative", "evidence": title})
            elif any(k in t for k in _GUIDE_UP):
                out["guidance"].append({"company": disp, "sector": sector,
                                        "direction": "Positive", "evidence": title})

            # 3 brokerage — external opinion, never merged with fundamentals
            r = _RATING.search(t)
            if r:
                tgt = _TARGET.search(t)
                out["brokerage"].append({
                    "company": disp, "sector": sector, "rating": r.group(1).upper(),
                    "target": float(tgt.group(1).replace(",", "")) if tgt else None,
                    "evidence": title})
        return out

    # ---- emit SIGNALS, never a score -------------------------------------
    def signals(self, news, snap=None) -> list[NarrativeSignal]:
        layers = self.extract(news, snap)
        sig: list[NarrativeSignal] = []

        for m in layers["metrics"]:
            if m.get("verdict"):
                d = "Positive" if m["verdict"] == "beat" else "Negative"
                strength, conf = 0.65, 0.8
            else:
                # A single print has no direction without a prior period to compare to.
                # Emitted as Neutral rather than guessed — the honest state is
                # "observed, not yet interpretable".
                d, strength, conf = "Neutral", 0.0, 0.5
            sig.append(NarrativeSignal(
                narrative=self.name, dimension="Fundamentals", direction=d,
                sector=m["sector"], company=m["company"], metric=m["metric"],
                strength=strength, confidence=conf, horizon="1-2 quarters",
                evidence=[m["evidence"]],
                detail={k: m[k] for k in ("value", "higher_is_better") if k in m}))

        for g in layers["guidance"]:
            sig.append(NarrativeSignal(
                narrative=self.name, dimension="Guidance", direction=g["direction"],
                sector=g["sector"], company=g["company"], metric="Outlook",
                strength=0.7, confidence=0.75, horizon="1-2 quarters",
                evidence=[g["evidence"]]))

        for b in layers["brokerage"]:
            pos = b["rating"] in ("BUY", "ACCUMULATE", "OUTPERFORM", "OVERWEIGHT")
            neg = b["rating"] in ("SELL", "REDUCE", "UNDERPERFORM", "UNDERWEIGHT")
            sig.append(NarrativeSignal(
                narrative=self.name, dimension="Valuation", metric="Broker rating",
                direction="Positive" if pos else "Negative" if neg else "Neutral",
                sector=b["sector"], company=b["company"],
                # deliberately weaker than reported fundamentals: an opinion is not a fact
                strength=0.4, confidence=0.55, horizon="quarter",
                evidence=[b["evidence"]], detail={"rating": b["rating"], "target": b["target"]}))
        return sig

    def analyse(self, snap=None) -> dict:
        return {"question": "Did the quarter change the EARNINGS PATH, or just the print?",
                "why_it_matters": "a beat on a one-off is not a re-rating; guidance and "
                                  "credit cost move the path"}

    def decision_tree(self) -> list[str]:
        return ["Earnings event",
                "  ├─ metrics (reported)   → Fundamentals signal",
                "  ├─ guidance (said)      → Guidance signal (forward-looking)",
                "  ├─ brokerage (opinion)  → Valuation signal (weakest weight)",
                "  ├─ management (quotes)  → Management signal",
                "  └─ peer comparison      → Peer signal (relative, not absolute)"]
