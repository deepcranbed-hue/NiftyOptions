"""
relationship_tiers.py — the three-tier relationship hierarchy (Barra-style decomposition).

A stock's move = MARKET (systematic) + SECTOR + IDIOSYNCRATIC (specific). This module makes
that hierarchy explicit and, crucially, detects DECOUPLING — when a name's own catalyst
overrides the market so it "rises while the whole market falls" (or vice versa).

    ① PRIMARY  (Systematic / market-wide)  → direct influence on EVERYTHING (beta).
                 FII flows, US rates/Fed, VIX/risk, USD, global equities, geopolitics/oil shock.
                 "If this moves, the whole index moves."
    ② SECONDARY (Sector / thematic)         → moves a GROUP.
                 oil→energy, rates→banks/realty, USDINR→IT/pharma, China→metals, AI→power/tech.
    ③ TERTIARY  (Idiosyncratic / specific)  → moves ONE name; can DECOUPLE it from the market.
                 earnings surprise, USFDA, scheme beneficiary, order win, M&A, broker upgrade.

Reads the MIO fields the overlay already computed (engine drivers, sector library, company
intelligence, policy catalysts, extracted fundamentals). No engine edits.
"""
from __future__ import annotations

import common


# ---- PRIMARY: systematic drivers that move the whole market (with sign rule) ----
# (driver_key, name, +1 sign means "positive value = market UP", mechanism)
PRIMARY = [
    ("fii_kcr",          "FII flows",            +1, "foreign flows set the marginal bid/offer for the whole index"),
    ("us10y_pct",        "US rates / Fed path",  -1, "higher US yields → stronger USD → EM/FII outflow → market down"),
    ("dxy_pct",          "US Dollar (DXY)",      -1, "a strong dollar pulls capital out of EM equity"),
    ("vix_pct",          "Volatility / risk",    -1, "rising VIX = risk-off = broad de-risking"),
    ("sox_pct",          "Global equities (SOX)",+1, "global tech/risk sentiment sets the overnight lead"),
    ("kospi_pct",        "Asia risk (Kospi)",    +1, "Asia session risk appetite spills into India"),
    ("geopolitics_hits", "Geopolitics / oil shock", -1, "supply-risk premium is a systematic risk-off shock"),
    ("india_cpi_hot",    "India CPI / RBI",      -1, "hot CPI → hawkish RBI → tighter conditions for the whole market"),
]

# idiosyncratic catalyst kinds (tertiary) and whether they can decouple a stock
IDIO_CATALYSTS = ["earnings", "result", "guidance", "usfda", "order win", "order book", "tcv",
                  "m&a", "acquisition", "merger", "buyback", "management", "ceo", "fundraise",
                  "scheme", "pli", "beneficiary", "upgrade", "downgrade"]


def _dir_word(x, band=0.05):
    return "Up" if x > band else "Down" if x < -band else "Flat"


def build(core, mio: dict) -> dict:
    eng = core.run_engine()
    drivers = eng.get("drivers", {})
    contrib = eng.get("indices", {}).get("Nifty 50", {}).get("contrib", {})
    market_total = eng.get("indices", {}).get("Nifty 50", {}).get("total", 0.0)
    market_dir = _dir_word(market_total)

    # ---- ① PRIMARY (systematic) -----------------------------------------
    primary = []
    for key, name, sign, mech in PRIMARY:
        val = drivers.get(key)
        c = contrib.get(key, 0.0)
        if val is None or abs(val) < 1e-9:
            continue
        # market effect direction: sign of the driver's contribution to the index
        eff = _dir_word(c) if abs(c) >= 0.005 else _dir_word(sign * common.norm(val, common.cap_for(key)))
        primary.append({
            "relationship": name, "driver": key, "value": round(val, 2),
            "market_effect": eff, "contribution": round(c, 3), "mechanism": mech,
            "active": abs(c) >= 0.02,
        })
    primary.sort(key=lambda r: -abs(r["contribution"]))
    market_tilt = {
        "expected_move_pct": round(market_total, 2),
        "direction": market_dir,
        "note": "systematic (beta) tilt — the whole-market lean from primary drivers",
    }

    # ---- ② SECONDARY (sector) -------------------------------------------
    lib = mio.get("sector_factor_library", [])
    secondary = [{
        "sector": s["sector"], "direction": s["verdict"], "score": s["score"],
        "top_driver": (s["active_factors"][0]["factor"].split("(")[0].strip()
                       if s.get("active_factors") else "—"),
    } for s in lib[:6]] + [{
        "sector": s["sector"], "direction": s["verdict"], "score": s["score"],
        "top_driver": (s["active_factors"][0]["factor"].split("(")[0].strip()
                       if s.get("active_factors") else "—"),
    } for s in lib[-3:]] if lib else []

    # ---- ③ TERTIARY (idiosyncratic) + DECOUPLING ------------------------
    tertiary, decoupling = [], []

    # gather company-level catalysts from company intelligence + policy + extracted
    companies = mio.get("affected_companies", [])
    scheme_named = []
    for s in (mio.get("policy_catalysts", {}) or {}).get("scheme_catalysts", []):
        for nm in s.get("beneficiaries_named", []):
            scheme_named.append((nm, "scheme beneficiary", "Up" if s.get("direction") != "headwind" else "Down"))
    extracted = mio.get("extracted_fundamentals", [])

    seen = set()
    def _add(company, catalyst, idio_dir, strength):
        if not company or company in seen:
            return
        seen.add(company)
        row = {"company": company, "catalyst": catalyst, "idiosyncratic_direction": idio_dir,
               "strength": strength}
        tertiary.append(row)
        # DECOUPLING: idiosyncratic move opposes the market tilt and is strong
        if market_dir in ("Up", "Down") and idio_dir in ("Up", "Down") and idio_dir != market_dir \
                and strength in ("High", "Very High", "Catalyst"):
            decoupling.append({
                "company": company, "catalyst": catalyst,
                "market_says": market_dir, "stock_expected": idio_dir,
                "verdict": (f"DECOUPLED — expected to {idio_dir.lower()} despite the market leaning "
                            f"{market_dir.lower()}; its idiosyncratic catalyst ({catalyst}) dominates."),
            })

    # company intelligence catalysts
    for c in companies:
        kind = (c.get("exposure_vector", {}) or {}).get("catalyst_kind") or ""
        idio_dir = c.get("direction", "Flat")
        is_cat = kind == "Catalyst"
        strength = "Catalyst" if is_cat else "News"
        label = "results/earnings catalyst" if is_cat else "company news"
        _add(c.get("company"), label, idio_dir, strength)
    # scheme beneficiaries (named in a headline)
    for nm, cat, d in scheme_named:
        _add(nm, cat, d, "Catalyst")
    # high-impact parsed fundamentals (company resolved via source weight)
    for m in extracted:
        if m.get("impact_label") in ("High", "Very High") and m.get("nifty_weight"):
            # infer the company from the source headline via the gazetteer
            src = (m.get("source") or "").lower()
            comp = None
            for kw, disp, sym, sec in getattr(core.ms, "COMPANY_GAZETTEER", []):
                if kw.lower() in src:
                    comp = disp
                    break
            idio_dir = "Up" if m.get("quality_direction", 0) > 0 else "Down"
            _add(comp, f"{m['metric']} {m['value']}{m.get('unit','')}", idio_dir, m["impact_label"])

    return {
        "framework": "return = market (systematic) + sector + idiosyncratic (specific)",
        "market_tilt": market_tilt,
        "primary": primary,          # systematic — moves everything
        "secondary": secondary,      # sector — moves a group
        "tertiary": tertiary,        # idiosyncratic — moves one name
        "decoupling": decoupling,    # names expected to move AGAINST the market
    }
