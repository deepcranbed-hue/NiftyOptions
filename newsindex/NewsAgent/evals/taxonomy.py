"""
taxonomy.py — constants the evaluators grade against (no external deps, no engine imports).

Everything here is REFERENCE data for the evaluation layer. The evals never touch the engine or
overlay; they read a saved MIO + the rendered report and grade them against these tables.
"""
from __future__ import annotations

# ---- NIFTY sector universe (canonical name -> match aliases) --------------
# 'core' sectors are the ones an institutional NIFTY note is expected to cover.
NIFTY_SECTORS = {
    "Banks / Financials": (["bank", "financ", "nbfc"], True),
    "IT Services":        (["it ", "it services", "software", "tech"], True),
    "Auto":               (["auto", "vehicle"], True),
    "Energy / Oil & Gas": (["energy", "oil", "gas", "opec", "omc", "upstream", "refin"], True),
    "Metals":             (["metal", "steel", "alumin", "zinc"], True),
    "Pharma / Health":    (["pharma", "health", "hospital"], True),
    "FMCG":               (["fmcg", "consumer stap", "staples"], True),
    "Power / Utilities":  (["power", "utilit", "grid"], True),
    "Capital Goods":      (["capital goods", "capex", "industrial", "engineering"], True),
    "Telecom":            (["telecom", "spectrum", "airtel"], True),
    "Realty":             (["realty", "real estate", "property", "housing"], False),
    "Cement":             (["cement"], False),
    "Infra":              (["infra", "construction", "roads"], False),
    "Insurance":          (["insurance", "life co", "gic"], False),
    "Consumer Durables":  (["durable", "appliance", "electronics"], False),
    "Chemicals":          (["chemical", "specialty chem"], False),
}

# ---- canonical economic sign rules (for contradiction / inconsistency check) ----
# Each: (antecedent tokens, consequent tokens, expected co-movement sign, plain-English rule).
# sign +1 => antecedent-up implies consequent-up ; -1 => antecedent-up implies consequent-down.
CANONICAL_RULES = [
    (["fed cut", "rate cut", "cut rates", "dovish"], ["usd", "dollar", "dxy"], -1,
     "A Fed/rate CUT is USD-negative (lower yields → weaker dollar), not USD-positive."),
    (["rate hike", "hike rates", "hawkish", "higher for longer"], ["usd", "dollar", "dxy"], +1,
     "A rate HIKE is USD-positive (higher yields attract flows)."),
    (["oil up", "crude up", "brent up", "oil rises", "oil surge"], ["inflation", "cpi", "wpi"], +1,
     "Higher oil pushes inflation UP (import bill / fuel pass-through)."),
    (["us yields up", "us10y up", "yields rise", "higher yields"], ["fii", "foreign flows", "em flows"], -1,
     "Rising US yields pull FII flows OUT of EM equity (relative return)."),
    (["weak rupee", "rupee depreciat", "usdinr up"], ["it exporter", "it services", "exporter"], +1,
     "A weaker rupee is a tailwind for exporters (IT/pharma) — revenue in USD."),
    (["risk-off", "risk off"], ["high beta", "leveraged", "smallcap", "midcap"], -1,
     "Risk-off hurts high-beta / leveraged names the most."),
    (["gold up", "gold rises"], ["gold financier", "muthoot", "manappuram"], +1,
     "Higher gold lifts gold-financiers' collateral value."),
]

# ---- news that is BACKGROUND, not market-moving (should not influence the read) ----
BACKGROUND_NEWS = [
    "groww", "zerodha", "upstox", "angel one", "demat", "brokerage", "broker ",
    "trading app", "trading platform", "open account", "how to trade", "sip ",
    "mutual fund", "ipo allotment", "trading account", "referral", "cashback",
    "webinar", "learn trading", "app launch", "launches trading",
]

# ---- over-certain / deterministic language a probabilistic note should avoid ----
OVERCERTAIN_TERMS = [
    "will definitely", "guaranteed", "certainly", "no doubt", "for sure", "must rise",
    "must fall", "is going to", "always", "never fails", "100%",
]
# a bare directional call with no hedge word nearby reads as over-deterministic
HEDGE_WORDS = ["bias", "lean", "tilt", "likely", "probab", "skew", "expected", "risk of",
               "odds", "%", "tends to", "on balance"]
