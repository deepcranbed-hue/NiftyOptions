"""
macro_expectations.py — the forward-looking macro block, ALWAYS included, split by GEOGRAPHY.

Markets trade expectations, not just prints. This module always surfaces, with a clear
US vs INDIA distinction:

    UNITED STATES
      • Labor / jobs         (nonfarm payrolls, jobless claims, unemployment)
      • Inflation            (US CPI + PPI / wholesale, core PCE)
      • Rate expectation     (Fed / FOMC: hike bias / on hold / cut bias)

    INDIA
      • Inflation            (India CPI / WPI, food, oil pass-through)
      • Rate expectation     (RBI / MPC: hawkish / on hold / room to cut)

Each read is tagged with its country and evidence so the desk never confuses a US labor
print with an India rate signal. Derived from news cues (geo-classified) + the tape
(US 10Y, dollar, oil). Deterministic; an LLM only narrates.
"""
from __future__ import annotations

# ---- geography markers ----------------------------------------------------
_US_MARK = ["us ", "u.s", "united states", "fed", "fomc", "powell", "nonfarm", "payroll",
            "jobless", "treasury", "nasdaq", "s&p", "wall street", "dxy", "dollar index",
            "pce", "us cpi", "us inflation", "us ppi"]
_IN_MARK = ["india", "indian", "rbi", "mpc", "repo", "wpi", "sensex", "nifty", "domestic",
            "monsoon", "india cpi", "india inflation"]
_EU_MARK = ["ecb", "european central bank", "lagarde", "eurozone", "euro area",
            "euro-area", "frankfurt", "bundesbank", "europe inflation"]


def _geo(text: str) -> str:
    us = any(m in text for m in _US_MARK)
    ind = any(m in text for m in _IN_MARK)
    eu = any(m in text for m in _EU_MARK)
    if eu and not ind:
        return "EU"          # ECB is distinct — its own monetary block
    if ind and not us:
        return "IN"
    if us and not ind:
        return "US"
    if us and ind:
        return "US"          # e.g. "US data lifts India" — the DATA is US
    return "US"              # unlabelled global macro defaults to US (Fed complex)


# ---- keyword banks (direction) --------------------------------------------
_INFL_COOL = ["softer inflation", "cooling inflation", "disinflation", "inflation eased",
              "cpi cooled", "cpi cools", "softer wholesale", "wholesale inflation eased",
              "ppi eased", "softer ppi", "price pressure eased", "cooler than expected",
              "eased concerns over further policy tightening", "soft cpi", "below expectations"]
_INFL_HOT = ["hot cpi", "sticky inflation", "inflation rose", "inflation jumped", "price pressure",
             "hotter than expected", "upside inflation", "cpi accelerat", "wholesale inflation rose",
             "above expectations"]

_JOBS_STRONG = ["strong jobs", "payrolls beat", "robust hiring", "jobs surged", "low unemployment",
                "hiring accelerat", "job growth", "nonfarm beat", "labour market strong", "labor market strong"]
_JOBS_WEAK = ["job cuts", "layoffs", "jobless claims rose", "weak jobs", "payrolls miss",
              "unemployment rose", "hiring slow", "labour market cool", "labor market cool", "jobs miss"]

_HAWKISH = ["rate hike", "hike rates", "higher for longer", "sticky inflation", "hot inflation",
            "hawkish", "more tightening", "no rate cut", "delay cut", "rules out cut",
            "hot cpi", "upside surprise", "rate increase", "restrictive stance"]
_DOVISH = ["rate cut", "cut rates", "rate cuts", "dovish", "easing", "pivot", "pause",
           "softer inflation", "cooling inflation", "disinflation", "eased concerns",
           "eased concerns over further policy tightening", "softer wholesale",
           "wholesale inflation eased", "softer ppi", "ppi eased", "soft cpi",
           "rate cut hopes", "fed easing", "cut bias", "no further tightening", "room to cut"]


import common

def _snippet(text, kws):
    """Return the SENTENCE that actually contains a matching keyword (relevant evidence),
    not the whole headline — so a broad article's title isn't shown for every category."""
    for sent in common.sentences(text):
        low = sent.lower()
        if any(k in low for k in kws):
            s = sent.strip()
            return (s[:150] + "…") if len(s) > 150 else s
    return None


def _classify(news, kws):
    """Return {geo: [relevant snippets]} for items matching any keyword, deduped.
    Evidence is the matching sentence, so it's on-topic for the category and differs across
    categories even when one article discusses jobs AND inflation AND the Fed."""
    out = {"US": [], "IN": [], "EU": []}     # EU added for the ECB block
    for n in news or []:
        t = common.news_text(n)
        snip = _snippet(t, kws)
        if snip:
            out[_geo(t)].append(snip)
    for g in out:
        out[g] = common.dedupe(out[g])
    return out


def _trend(pos, neg, tape=0):
    s = len(pos) - len(neg) + tape
    return s


def build(signals: dict, news: list[dict]) -> dict:
    us10y = signals.get("us10y_pct") or 0.0
    dxy = signals.get("dxy_pct") or 0.0
    oil = signals.get("oil_pct") or 0.0

    cool = _classify(news, _INFL_COOL)
    hot = _classify(news, _INFL_HOT)
    strong = _classify(news, _JOBS_STRONG)
    weak = _classify(news, _JOBS_WEAK)
    hawk = _classify(news, _HAWKISH)
    dov = _classify(news, _DOVISH)

    # ================= UNITED STATES =================
    # US inflation (CPI + PPI/wholesale)
    us_infl_s = _trend(hot["US"], cool["US"])
    us_infl_trend = "Rising" if us_infl_s > 0 else "Cooling" if us_infl_s < 0 else "Stable"
    us_infl_impl = "hawkish" if us_infl_trend == "Rising" else "dovish" if us_infl_trend == "Cooling" else "neutral"

    # US labor
    us_jobs_s = _trend(strong["US"], weak["US"])
    us_jobs_trend = "Improving" if us_jobs_s > 0 else "Weakening" if us_jobs_s < 0 else "Stable"
    us_jobs_impl = "hawkish (delays cuts)" if us_jobs_trend == "Improving" else \
                   "dovish (brings cuts forward)" if us_jobs_trend == "Weakening" else "neutral"

    # US rate expectation (Fed) — combines US news + US tape + US inflation + US labor
    tape_bias = (1 if us10y > 1.0 else 0) + (1 if dxy > 0.3 else 0) \
                - (1 if us10y < -1.0 else 0) - (1 if dxy < -0.3 else 0)
    fed_s = (len(hawk["US"]) - len(dov["US"])) + tape_bias \
        + (1 if us_infl_trend == "Rising" else -1 if us_infl_trend == "Cooling" else 0) \
        + (1 if us_jobs_trend == "Improving" else -1 if us_jobs_trend == "Weakening" else 0)
    fed_exp = ("Hike bias / higher-for-longer" if fed_s >= 2 else
               "Cut bias" if fed_s <= -2 else "On hold")
    fed_conf = round(min(0.9, 0.4 + 0.1 * (len(hawk["US"]) + len(dov["US"]) + abs(tape_bias))), 2)

    us_block = {
        "labor": {"trend": us_jobs_trend, "rate_implication": us_jobs_impl,
                  "evidence": (strong["US"][:2] + weak["US"][:2]) or ["no fresh US labor print"]},
        "inflation": {"trend": us_infl_trend, "components": "US CPI + PPI/wholesale + core PCE",
                      "rate_implication": us_infl_impl,
                      "evidence": (cool["US"][:2] + hot["US"][:2]) or ["no fresh US inflation print"]},
        "rate_expectation": {"central_bank": "Fed / FOMC", "expectation": fed_exp,
                             "confidence": fed_conf,
                             "drivers": {"news_hawkish": len(hawk["US"]), "news_dovish": len(dov["US"]),
                                         "tape_bias": tape_bias, "us10y_pct": us10y, "dxy_pct": dxy},
                             "evidence": list(dict.fromkeys(dov["US"][:2] + hawk["US"][:2]))
                                         or ["no fresh Fed data — read from the US tape"]},
    }

    # ================= EUROPE (ECB) =================
    # A HOLD IS NOT NEUTRAL — its meaning is in the guidance. A pause because inflation
    # is cooling is constructive; a pause "waiting to see whether oil reignites inflation"
    # is a RESTRICTIVE hold. And oil reaches the ECB the same way it reaches the RBI —
    # energy → headline CPI → the bank cannot promise easier policy. So oil>2 tilts the
    # ECB hawkish even with no explicit ECB hawkish headline. The ECB is an AMPLIFIER of
    # the oil shock through the global-liquidity channel, not a primary Nifty driver.
    ecb_hawk, ecb_dov = len(hawk["EU"]), len(dov["EU"])
    ecb_s = (ecb_hawk - ecb_dov) + (1 if oil > 2 else 0)
    if ecb_s >= 2:
        ecb_exp, ecb_tone = "Hawkish hold / tightening bias", "restrictive"
    elif ecb_hawk and ecb_dov == 0:
        # explicit hold + hawkish guidance = the "wait-and-see because of oil" case
        ecb_exp, ecb_tone = "Hawkish hold (data-dependent, oil-wary)", "restrictive"
    elif ecb_s <= -2:
        ecb_exp, ecb_tone = "Cut bias / dovish", "supportive"
    else:
        ecb_exp, ecb_tone = "On hold (neutral)", "neutral"
    ecb_drivers = []
    if oil > 2:
        ecb_drivers.append(f"oil {oil:+.1f}% → euro-area energy inflation risk")
    if ecb_hawk:
        ecb_drivers.append("hawkish ECB guidance")
    if ecb_dov:
        ecb_drivers.append("dovish ECB guidance")
    # transmission note: ECB restrictive → EU yields ↑ → global risk appetite ↓ → EM/Nifty
    ecb_nifty = ("indirect amplifier: hawkish ECB → EU yields ↑ → global liquidity "
                 "expectations ↓ → EM/FII flows softer → mild Nifty headwind, mostly ON TOP "
                 "of the oil shock rather than on its own") if ecb_tone == "restrictive" else (
                 "supportive: ECB easing lifts global liquidity → EM inflows" if ecb_tone == "supportive"
                 else "neutral for Nifty in isolation")
    europe_block = {
        "rate_expectation": {"central_bank": "ECB", "expectation": ecb_exp, "tone": ecb_tone,
                             "drivers": ecb_drivers or ["no fresh ECB signal"],
                             "nifty_transmission": ecb_nifty,
                             "evidence": list(dict.fromkeys(dov["EU"][:2] + hawk["EU"][:2]))
                                         or (["oil-linked hawkish tilt (no direct ECB headline)"]
                                             if oil > 2 else ["no fresh ECB data"])},
    }

    # ================= INDIA =================
    # India inflation (CPI/WPI) + oil import pass-through
    in_infl_s = _trend(hot["IN"], cool["IN"], tape=(1 if oil > 1.5 else -1 if oil < -1.5 else 0))
    in_infl_trend = "Rising" if in_infl_s > 0 else "Cooling" if in_infl_s < 0 else "Stable"

    # RBI rate expectation
    if oil > 2 or in_infl_trend == "Rising":
        rbi_exp = "Hawkish / higher-for-longer"
    elif in_infl_trend == "Cooling":
        rbi_exp = "Room to cut"
    else:
        rbi_exp = "On hold"
    rbi_drivers = []
    if oil > 2:
        rbi_drivers.append("oil/import inflation")
    if in_infl_trend != "Stable":
        rbi_drivers.append(f"India inflation {in_infl_trend.lower()}")

    india_block = {
        "labor": {"trend": "n/a", "note": "India labour prints are infrequent; not modelled daily"},
        "inflation": {"trend": in_infl_trend, "components": "India CPI/WPI + food + oil pass-through",
                      "evidence": (cool["IN"][:2] + hot["IN"][:2]) or
                                  ([f"oil {oil:+.1f}% → import-inflation channel"] if abs(oil) > 1.5
                                   else ["no fresh India inflation print"])},
        "rate_expectation": {"central_bank": "RBI / MPC", "expectation": rbi_exp,
                             "drivers": rbi_drivers or ["no fresh India rate signal"],
                             "evidence": (hawk["IN"][:2] + dov["IN"][:2]) or ["read from oil/inflation channel"]},
    }

    return {
        "always_included": True,
        "us": us_block,
        "india": india_block,
        "europe": europe_block,
        "policy_path": {
            "us": f"US inflation {us_infl_trend.lower()} + jobs {us_jobs_trend.lower()} → Fed {fed_exp.lower()}",
            "india": f"India inflation {in_infl_trend.lower()} → RBI {rbi_exp.lower()}",
            "europe": f"ECB {ecb_exp.lower()} ({ecb_tone}) — {'oil-driven' if oil > 2 else 'guidance-driven'}",
        },
        "tag": "EXPECTATION",
    }
