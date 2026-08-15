"""
policy_catalysts.py — generalized policy→sector detection + broker views.

NO hardcoded beneficiary stocks. Two maintainable keyword layers only:
  1. POLICY_THEMES  — policy/scheme keywords → the SECTOR(S) they affect (+ tailwind/headwind).
  2. broker cues    — generic analyst-view language.

The affected COMPANIES are then resolved dynamically from the engine's existing company
universe (market_scan.COMPANY_GAZETTEER: 97 names, each tagged with a sector). So "Union
Cabinet approves semiconductor/mobile manufacturing" resolves to whatever the gazetteer tags
as EMS/Electronics or Semiconductor (Dixon, etc.) — add a stock to the gazetteer and it's
picked up automatically; nothing here changes.

Likewise a broker view ("<any broker> says <any stock> is a beneficiary") resolves the stock
from the gazetteer, not a fixed list.
"""
from __future__ import annotations

import re

# ---- LAYER 1: policy keyword -> affected sector hints (+ theme label) ------
# sector hints are matched (substring, case-insensitive) against gazetteer sector tags,
# so one hint like "electronic" covers "EMS/Electronics"; "auto" covers Auto/Auto-EV/etc.
POLICY_THEMES = [
    (["semiconductor", "chip manufactur", " fab ", "osat", "wafer", "compound semiconductor", "isms"],
     "Semiconductor manufacturing", ["semiconductor", "electronic"]),
    (["mobile phone", "smartphone", "mobile manufactur", "electronics manufactur", "mobile 2.0",
      "ecms", "components scheme", "pli electronics"],
     "Electronics / mobile manufacturing", ["electronic", "semiconductor"]),
    (["solar", "renewable", "solar module", "rooftop", "almm", "pm surya", "wind energy", "green hydrogen"],
     "Renewables / solar", ["power", "electrical", "cable"]),
    (["defence", "indigenis", "make in india defence", "defence procurement", "artillery", "missile", "warship"],
     "Defence", ["defence"]),
    (["electric vehicle", " ev ", "ev policy", "battery", "fame", "acc pli", "charging infra", "e-mobility"],
     "EV / battery", ["ev", "auto"]),
    (["pharma", " api", "bulk drug", "key starting material", "drug pricing", "nppa"],
     "Pharma / API", ["pharma"]),
    (["railway", "vande bharat", "locomotive", "metro rail", "rail coach"],
     "Railways", ["capital goods"]),
    (["highway", "road project", "nhai", "expressway", "bharatmala", "infrastructure push"],
     "Roads / infrastructure", ["cement", "capital goods"]),
    (["cement"], "Cement", ["cement"]),
    (["steel", "metal", "mining", "iron ore", "coal", "critical mineral"],
     "Metals / mining", ["metals"]),
    (["telecom", "spectrum", "5g", "bharatnet", "trai"],
     "Telecom", ["telecom"]),
    (["power sector", "electricity", "power grid", "transmission", "discom", "smart meter"],
     "Power", ["power", "electrical", "cable"]),
    (["textile", "pm mitra", "apparel"], "Textiles", []),                 # no gazetteer sector yet
    (["ethanol", "sugar", "fertiliser", "fertilizer", "agri", "kisan", "msp", "food processing"],
     "Agri / sugar / fertiliser", ["fmcg"]),
    (["housing", "pmay", "affordable housing", "real estate", "redevelopment"],
     "Housing / realty", ["realty", "cement"]),
    (["bank recap", "psu bank", "financial inclusion", "banking reform"],
     "Banking / financials", ["bank", "financ"]),
    (["insurance", "irdai", "fdi insurance"], "Insurance", ["insurance"]),
]

# generic policy signal (is this a policy/regulatory item at all?)
POLICY_CUES = ["cabinet", "union cabinet", "ccea", "pli", "production linked", "scheme",
               "incentive", "subsidy", "outlay", "policy", "ministry", "govt", "government",
               "budget", "notified", "guidelines", "gazette", "reform", "sops", "approved",
               "approves", "clears", "nod", "sanctioned", "gst council", "duty", "tariff", "ban"]

APPROVAL_CUES = ["cabinet approv", "union cabinet", "cabinet clear", "cabinet nod", "approved",
                 "approves", "clears", "notified", "gets nod", "sanctioned", "greenlit"]

# negative / headwind policy cues (tax, duty hike, ban, curbs)
NEGATIVE_CUES = ["ban ", "banned", "duty hike", "higher duty", "import duty", "export ban",
                 "tax hike", "levy", "curb", "restriction", "penalty", "phase out", "withdraw",
                 "crackdown", "cap on", "price cap", "tightening norms"]

# ---- LAYER 2: broker/analyst view cues (generic) --------------------------
_BROKER_REF = ["j.p. morgan", "jp morgan", "jpmorgan", "morgan stanley", "goldman", "clsa",
               "jefferies", "nomura", "citi", "ubs", "bofa", "bank of america", "macquarie",
               "nuvama", "motilal", "kotak institutional", "hsbc", "bernstein", "investec",
               "emkay", "icici securities", "axis capital", "antique", "elara", "systematix"]
_BROKER_GENERIC = ["brokerage", "analysts at", "research note", "initiated coverage",
                   "initiates coverage", "maintains", "reiterates", "rated", "coverage"]
_VIEW_CUES = ["beneficiary", "key beneficiary", "earnings upgrade", "upgrade", "downgrade",
              "overweight", "underweight", "outperform", "top pick", "target price",
              "raise target", "cut target", "buy rating", "sell rating", "initiate"]


import common
_text = common.news_text            # single source of truth (overlay/common.py)
_sentences = common.sentences
_dedupe = common.dedupe


def _sector_index(gazetteer):
    """sector tag -> [(display, symbol, keyword)] from the engine's company universe."""
    idx = {}
    for kw, disp, sym, sec in gazetteer:
        idx.setdefault(sec, []).append((disp, sym, kw))
    return idx


def _sector_companies(sector_index, hints):
    """Distinct display names whose sector matches any hint (deduped by company name)."""
    names = []
    for sec, comps in sector_index.items():
        if any(h in sec.lower() for h in hints):
            names += [disp for disp, sym, kw in comps]
    return _dedupe(names)


def _named_in(sector_index, hints, sentence):
    """Companies of the target sector whose keyword appears in THIS sentence."""
    out = []
    for sec, comps in sector_index.items():
        if not any(h in sec.lower() for h in hints):
            continue
        for disp, sym, kw in comps:
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", sentence):
                out.append(disp)
    return _dedupe(out)


def detect_scheme_catalysts(news, sector_index) -> list[dict]:
    """Sentence-scoped: a scheme needs its policy cue AND a domain keyword in the SAME
    sentence. Aggregated to ONE entry per theme (tailwind/headwind reconciled to 'mixed')."""
    acc = {}   # theme -> aggregate
    for n in news or []:
        title = n.get("title", "")
        for sent in _sentences(_text(n)):
            if not any(c in sent for c in POLICY_CUES):
                continue
            for kws, label, hints in POLICY_THEMES:
                if not any(k in sent for k in kws):
                    continue
                a = acc.setdefault(label, {"hints": hints, "approval": False,
                                           "tw": False, "hw": False,
                                           "named": [], "headlines": []})
                a["approval"] = a["approval"] or any(c in sent for c in APPROVAL_CUES)
                if any(c in sent for c in NEGATIVE_CUES):
                    a["hw"] = True
                else:
                    a["tw"] = True
                if hints:
                    a["named"] += _named_in(sector_index, hints, sent)
                a["headlines"].append(title)
                break                                   # one theme per sentence
    out = []
    for label, a in acc.items():
        direction = "mixed" if a["tw"] and a["hw"] else "headwind" if a["hw"] else "tailwind"
        named = _dedupe(a["named"])
        watch = [c for c in _sector_companies(sector_index, a["hints"]) if c not in named][:6] \
            if a["hints"] else []
        sig = ("POLICY MIXED — conflicting signals" if direction == "mixed"
               else ("POLICY HEADWIND" if direction == "headwind" else "POLICY TAILWIND")
               + (" — approved" if a["approval"] else " — in the news"))
        out.append({
            "theme": label, "affected_sectors": a["hints"] or [label],
            "direction": direction, "is_approval": a["approval"],
            "beneficiaries_named": named, "beneficiaries_watch": watch,
            "resolved_from": "COMPANY_GAZETTEER (dynamic, not hardcoded)",
            "signal": sig, "headlines": _dedupe(a["headlines"])[:3],
        })
    return out


_MARKET_WORDS = ["indian equities", "indian equity", "indian market", "the market", "nifty",
                 "sensex", "index", "equities", "market to"]


def detect_broker_views(news, gazetteer) -> list[dict]:
    """Sentence-scoped: broker + view verb + subject must co-occur in ONE sentence, so a
    broker/stock elsewhere in the body isn't misattributed. Subject resolves to a specific
    gazetteer stock or, failing that, a market-level call — else the sentence is skipped."""
    kw_to_company = {kw.lower(): (disp, sym, sec) for kw, disp, sym, sec in gazetteer}
    out, seen = [], set()
    for n in news or []:
        title = n.get("title", "")
        for sent in _sentences(_text(n)):
            broker = next((b for b in _BROKER_REF if b in sent), None)
            generic = any(c in sent for c in _BROKER_GENERIC)
            view = [c for c in _VIEW_CUES if c in sent]
            if not (broker or generic) or not view:
                continue
            # resolve the subject from THIS sentence only
            stock = None
            for kw, (disp, sym, sec) in kw_to_company.items():
                if re.search(r"\b" + re.escape(kw) + r"\b", sent):
                    stock = {"company": disp, "symbol": sym, "sector": sec}
                    break
            subject = (stock["company"] if stock
                       else "Indian equities / market" if any(w in sent for w in _MARKET_WORDS)
                       else None)
            if subject is None:
                continue                                # no resolvable subject → skip (no guessing)
            broker_name = (broker.title().replace("Jp", "J.P.").replace("Hsbc", "HSBC").replace("Ubs", "UBS").replace("Bofa", "BofA")
                           if broker else "Brokerage (unnamed)")
            key = (broker_name, subject)
            if key in seen:
                continue
            seen.add(key)
            m = re.search(r"target(?:\s*price)?\s*(?:of\s*)?(?:rs\.?|₹)\s*([\d,]+)", sent)
            stance = ("upgrade" if "upgrade" in sent or "raise target" in sent else
                      "downgrade" if "downgrade" in sent or "cut target" in sent else
                      "positive" if any(w in sent for w in ["beneficiary", "overweight", "outperform", "buy", "top pick"]) else
                      "negative" if any(w in sent for w in ["underweight", "sell rating"]) else "view")
            out.append({
                "broker": broker_name, "stock": stock, "subject": subject,
                "stance": stance, "cues": view[:4],
                "target_price": (m.group(1).replace(",", "") if m else None),
                "headline": title, "link": n.get("link", ""),
            })
    return out


def build(news: list[dict], gazetteer) -> dict:
    sector_index = _sector_index(gazetteer)
    schemes = detect_scheme_catalysts(news, sector_index)
    brokers = detect_broker_views(news, gazetteer)
    if not schemes and not brokers:
        return {}
    return {"scheme_catalysts": schemes, "broker_views": brokers}
