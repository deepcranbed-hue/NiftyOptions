"""
_sentiment_audit_demo.py
------------------------
Runs REAL headlines (fetched 2026-07-09/10 from ET / Business Standard /
Moneycontrol-class aggregators) through the ACTUAL sector_tagging pipeline
to expose concrete failure modes in sector sentiment.

The 'sentiment'/'sectors_affected' fields stand in for the Gemini step
(no API key in sandbox). Entity->constituent resolution uses the real
sector_tree.company_path + WEIGHTS, so the DIRECT path is fully faithful.
"""
from __future__ import annotations
import copy, json, statistics
from datetime import datetime, timezone

from backend.quant.sector_tree import company_path, WEIGHTS
from backend.quant.sector_map import sector_weights
from backend.quant.sector_tagging import sector_sentiment_from_gemini

NOW = datetime(2026, 7, 10, 5, 0, tzinfo=timezone.utc)

def C(*names):
    """Resolve real constituents exactly like the entity matcher would."""
    out = []
    for n in names:
        p = company_path(n)
        if p:
            sec, ind, canon = p
            out.append({"symbol": canon, "sector": sec, "industry": ind,
                        "weight": WEIGHTS.get(canon, 0.0)})
    return out

# Real headlines, 2026-07-09/10. sentiment/sectors_affected = stand-in Gemini tags.
ARTICLES = [
    {"title": "Sensex rebounds 500 points; ICICI Bank, HDFC Bank support the recovery",
     "published_at": "2026-07-09T04:00:00+00:00", "sentiment": 0.55,
     "sectors_affected": ["Financials"], "constituents": C("ICICI Bank", "HDFC Bank")},

    {"title": "Infosys advances 3.94%, TCS up 2.57% ahead of Q1 results",
     "published_at": "2026-07-09T05:00:00+00:00", "sentiment": 0.60,
     "sectors_affected": ["Information Technology"], "constituents": C("Infosys", "TCS")},

    {"title": "TCS shares decline as company prepares to announce weak Q1 revenue",
     "published_at": "2026-07-09T09:30:00+00:00", "sentiment": -0.35,
     "sectors_affected": ["Information Technology"], "constituents": C("TCS")},

    {"title": "Adani Enterprises falls 1.47% to Rs 3,159",
     "published_at": "2026-07-09T06:00:00+00:00", "sentiment": -0.40,
     "sectors_affected": [], "constituents": C("Adani Enterprises")},

    {"title": "Sun Pharma, Bharti Airtel, Bajaj Finserv lead Nifty 50 gainers",
     "published_at": "2026-07-09T10:30:00+00:00", "sentiment": 0.30,
     "sectors_affected": [], "constituents": C("Sun Pharma", "Bharti Airtel", "Bajaj Finserv")},

    {"title": "Dr Reddy's, Maruti Suzuki, ONGC top the Nifty 50 losers",
     "published_at": "2026-07-09T10:30:00+00:00", "sentiment": -0.30,
     "sectors_affected": [], "constituents": C("Dr Reddys", "Maruti Suzuki", "ONGC")},

    {"title": "Nifty Metal index down about 1% as Hindalco, Tata Steel, JSW Steel trade lower",
     "published_at": "2026-07-09T08:00:00+00:00", "sentiment": -0.45,
     "sectors_affected": ["Metals & Mining"], "constituents": C("Hindalco", "Tata Steel", "JSW Steel")},

    {"title": "Tata Steel India crude steel production rose 11.3% YoY in June quarter",
     "published_at": "2026-07-09T03:00:00+00:00", "sentiment": 0.50,
     "sectors_affected": ["Metals & Mining"], "constituents": C("Tata Steel")},

    {"title": "Mahindra & Mahindra raises vehicle prices by average 2.7% effective July 10",
     "published_at": "2026-07-09T07:00:00+00:00", "sentiment": 0.10,
     "sectors_affected": ["Automobile"], "constituents": C("Mahindra & Mahindra")},

    {"title": "Maruti Suzuki commissions 1 MWh battery storage system at Kharkhoda plant",
     "published_at": "2026-07-09T02:00:00+00:00", "sentiment": 0.05,
     "sectors_affected": ["Automobile"], "constituents": C("Maruti Suzuki")},

    # The circular price-driven live-blog you flagged: sentiment is really a
    # function of *today's price action*, but it is ingested as fundamental news.
    {"title": "Bajaj Finance share price live: stock touches day high Rs 1,025, low Rs 993",
     "published_at": "2026-07-10T04:30:00+00:00", "sentiment": 0.20,
     "sectors_affected": ["Financials"], "constituents": C("Bajaj Finance")},
]

def run(articles, label):
    res = sector_sentiment_from_gemini(copy.deepcopy(articles), now=NOW)
    res.pop("__drilldown", None)
    audit = res.pop("__audit", {})
    print(f"\n===== {label} =====")
    print(f"{'sector':<26}{'combined':>9}{'cov':>7}{'n':>4}{'spr':>6}  low_conf  flag")
    for sec, d in sorted(res.items(), key=lambda kv: kv[1]['combined'], reverse=True):
        print(f"{sec:<26}{d['combined']:>+9.3f}{d['coverage']:>7.3f}"
              f"{d['direct_n']+d['derived_n']:>4}{d['spread']:>6.2f}  "
              f"{str(d['low_confidence']):>7}   {d['flag']}")
    if audit:
        print(f"  audit: scored={audit['n_scored']} quarantined={audit['n_quarantined']} "
              f"tiers={audit['tier_counts']}")
        for q in audit.get("quarantined", []):
            print(f"    QUARANTINED [{q['reason']}]: {q['title'][:55]}")
    return res

if __name__ == "__main__":
    sw = sector_weights()

    base = run(ARTICLES, "BASELINE (real headlines)")

    # ---- Attack 1: single poisoned/syndicated PR item into Metals ----
    poisoned = copy.deepcopy(ARTICLES)
    poisoned.append({
        "title": "Steel demand surges to record high, analysts see strong upside: PR wire",
        "published_at": "2026-07-10T04:45:00+00:00", "sentiment": 1.0,
        "sectors_affected": ["Metals & Mining"], "constituents": C("Tata Steel")})
    atk = run(poisoned, "ATTACK 1: +1 syndicated PR item, Metals & Mining")
    print(f"\n  Metals combined: {base['Metals & Mining']['combined']:+.3f} "
          f"-> {atk['Metals & Mining']['combined']:+.3f}  "
          f"(shift {atk['Metals & Mining']['combined']-base['Metals & Mining']['combined']:+.3f})")

    # ---- Attack 2: direct instruction-injection style score on a low-n sector
    # (Automobile: 2 near-zero real items). One +1.0 flips the sign & magnitude.
    inj = copy.deepcopy(ARTICLES)
    inj.append({
        "title": "Auto sector: Editor's note classifies outlook as strongly positive",
        "published_at": "2026-07-10T04:50:00+00:00", "sentiment": 1.0,
        "sectors_affected": ["Automobile"], "constituents": C("Maruti Suzuki")})
    a2 = run(inj, "ATTACK 2: +1 injected item, Automobile")
    print(f"\n  Auto combined: {base['Automobile']['combined']:+.3f} "
          f"-> {a2['Automobile']['combined']:+.3f}")

    # ---- Robust-aggregation counterfactual: median vs mean on Metals raw scores
    metals_raw = [-0.45, 0.50, 1.0]  # two real + one poison
    print("\n===== ROBUST-AGG COUNTERFACTUAL (Metals raw scores w/ poison) =====")
    print(f"  scores={metals_raw}")
    print(f"  mean   = {statistics.mean(metals_raw):+.3f}   <- current pipeline")
    print(f"  median = {statistics.median(metals_raw):+.3f}   <- injection largely inert")

    # ---- Coverage distortion: combined score printed flat despite thin coverage
    print("\n===== COVERAGE vs CONFIDENCE =====")
    for sec in ["Financials", "Metals & Mining", "Automobile"]:
        d = base[sec]
        print(f"  {sec:<20} combined {d['combined']:+.3f} on coverage "
              f"{d['coverage']*100:4.1f}% of the {sw.get(sec,0):.1f}% sector weight "
              f"(n_dir={d['direct_n']})")

    # ---- Dedup probe: two syndications of the same story, different tails ----
    from backend.quant.sector_tagging import sector_sentiment_from_gemini as _f
    import hashlib, re
    def cid(t):
        norm = re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()
        return hashlib.sha256(norm.encode()).hexdigest()[:16]
    t1 = "Tata Steel India crude steel production rose 11.3% YoY in June quarter"
    t2 = "Tata Steel India crude steel production rose 11.3% YoY in June quarter, delivery up 8.8%"
    print("\n===== DEDUP PROBE (title-key vs cluster_id) =====")
    print(f"  60-char key match: {t1.lower()[:60] == t2.lower()[:60]}")
    print(f"  cluster_id match : {cid(t1) == cid(t2)}   (different -> counted TWICE)")
