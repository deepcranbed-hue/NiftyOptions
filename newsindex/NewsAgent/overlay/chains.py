"""
chains.py — canonical multi-hop transmission chain library (economic priors).

The critique: a driver must never connect DIRECTLY to an outcome. "Oil → Banks" is wrong;
the real path is Oil → Inflation → RBI → Yield Curve → Financial Conditions → Banks. This
module encodes those full economic pathways as *priors* (textbook macro transmission), not
fitted coefficients — so no engine number is invented here. The Core supplies the driver's
magnitude/sign; this library supplies the PATH the effect travels.

Each chain is a list of hops; each hop names an economic node and the mechanism into it.
A driver can fan out into several parallel branches (inflation, fiscal, currency, freight…).
"""
from __future__ import annotations

# A hop = (node_label, mechanism_into_this_node)
# A branch = {"branch": name, "hops": [hop, ...], "sub_sectors": [...]}
# CHAIN_LIBRARY[driver_key][direction] = [branch, ...]

CHAIN_LIBRARY: dict[str, dict[str, list[dict]]] = {
    # ---------------------------------------------------------------- OIL ↑
    "oil_pct": {
        "up": [
            {"branch": "inflation→rates", "sub_sectors": ["Banks/Financials", "NBFC", "Real Estate"],
             "hops": [
                 ("Oil", "crude rises"),
                 ("Import Bill / Inflation", "India imports ~85% of crude → CPI & WPI up"),
                 ("RBI", "inflation keeps policy higher-for-longer / hawkish"),
                 ("Yield Curve", "repo & G-sec yields rise"),
                 ("Financial Conditions", "cost of capital tightens"),
                 ("Banks", "treasury MTM hit + funding cost up")]},
            {"branch": "fiscal→borrowing", "sub_sectors": ["Bond proxies", "Banks/Financials", "Capital Goods"],
             "hops": [
                 ("Oil", "crude rises"),
                 ("Import Bill", "oil import outgo rises"),
                 ("Current Account Deficit", "trade balance worsens"),
                 ("Fiscal Deficit", "fuel subsidy / lower excise room pressures the budget"),
                 ("Govt Borrowing", "higher gross borrowing to fund the gap"),
                 ("Bond Yield", "supply pushes the 10Y higher")]},
            {"branch": "current-account (CAD→INR→flows)",
             "sub_sectors": ["IT Services", "Pharma", "Importers", "Banks/Financials"],
             "hops": [
                 ("Oil", "crude rises — India imports ~85% of its crude"),
                 ("Current Account Deficit", "oil import bill widens the CAD"),
                 ("USDINR", "wider CAD pressures the rupee weaker"),
                 ("IT / Pharma exporters", "weaker rupee is a partial export TAILWIND (offset)"),
                 ("Import-reliant sectors", "weaker rupee raises landed input cost — HEADWIND"),
                 ("Imported Inflation", "weaker rupee re-imports inflation → bond yields up"),
                 ("FII Flows", "CAD + currency risk deters foreign inflows → Nifty")]},
            {"branch": "freight/input-cost", "sub_sectors": ["Chemicals", "Auto Components", "EMS", "Pharma API"],
             "hops": [
                 ("Oil", "crude rises"),
                 ("Freight / Tanker Rates", "energy + risk premium lifts shipping"),
                 ("Input Cost", "landed cost of imported inputs rises"),
                 ("Margin Squeeze", "import-reliant manufacturers compressed")]},
            {"branch": "OMC-margin", "sub_sectors": ["Upstream", "OMC"],
             "hops": [
                 ("Oil", "crude rises"),
                 ("Upstream Realisation", "ONGC/OIL realise more (less windfall tax)"),
                 ("OMC Marketing Margin", "BPCL/IOC/HPCL squeezed if retail prices capped")]},
            {"branch": "consumer/margin", "sub_sectors": ["Airlines", "Paints", "Tyres", "Logistics", "Cement", "FMCG"],
             "hops": [
                 ("Oil", "crude rises"),
                 ("Fuel & Derivative Cost", "ATF, crude derivatives, freight up"),
                 ("Corporate Margin", "fuel-intensive users compressed"),
                 ("Consumption", "higher pump prices trim discretionary demand")]},
        ],
        "down": [
            {"branch": "disinflation→easing", "sub_sectors": ["Banks/Financials", "NBFC", "Auto", "Real Estate"],
             "hops": [
                 ("Oil", "crude falls"),
                 ("Inflation", "import bill & CPI ease"),
                 ("RBI", "room to cut / turn dovish"),
                 ("Yield Curve", "yields drift lower"),
                 ("Financial Conditions", "cost of capital eases"),
                 ("Rate-sensitives", "banks, NBFCs, autos, realty supported")]},
            {"branch": "fiscal-relief", "sub_sectors": ["Capital Goods", "Infra"],
             "hops": [
                 ("Oil", "crude falls"),
                 ("Current Account", "CAD narrows"),
                 ("Fiscal Space", "subsidy relief / excise room improves"),
                 ("Bond Yield", "lower borrowing pressure eases yields")]},
        ],
    },
    # ---------------------------------------------------------- AI / SEMIS ↑
    "sox_pct": {
        "up": [
            {"branch": "AI-infrastructure", "sub_sectors": ["Semiconductors", "Power", "Data Centres", "Telecom", "EMS"],
             "hops": [
                 ("SOX / Semis", "global chip cycle up"),
                 ("AI Infrastructure Capex", "hyperscaler + enterprise build-out"),
                 ("Compute / Power / Data-centre Demand", "infra pull-through")]},
            {"branch": "AI-financing (banks finance AI)", "sub_sectors": ["Banks/Financials", "Capital Goods"],
             "hops": [
                 ("SOX / Semis", "AI capex cycle up"),
                 ("Corporate AI Investment", "firms fund AI build-outs"),
                 ("Software / Cloud / Power", "capex flows to the stack"),
                 ("Bank Corporate Lending", "banks FINANCE the AI infra build, not just benefit from productivity"),
                 ("Capital Goods", "electrical & data-centre equipment orders")]},
            {"branch": "AI-substitution (regime-gated)", "sub_sectors": ["IT Services"],
             "hops": [
                 ("SOX / Semis", "AI infra capex up"),
                 ("Enterprise Budget Reallocation", "spend shifts from consulting to AI infra"),
                 ("IT Services", "Indian services PRESSURED under an active Substitution regime")]},
            {"branch": "AI-complement (regime-gated)", "sub_sectors": ["IT Services", "EMS"],
             "hops": [
                 ("SOX / Semis", "AI adoption up"),
                 ("AI Deal Wins / Transformation", "cloud & GenAI deal pipeline"),
                 ("IT Services", "Indian services TAILWIND under an active Complement regime")]},
        ],
    },
    # ------------------------------------------------------------- US 10Y ↑
    "us10y_pct": {
        "up": [
            {"branch": "yields→dollar→flows", "sub_sectors": ["Banks/Financials", "IT Services"],
             "hops": [
                 ("US 10Y Yield", "US yields rise"),
                 ("US Dollar", "higher yields lift the dollar"),
                 ("USDINR", "rupee weakens"),
                 ("FII Flows", "EM equity relatively less attractive → outflow"),
                 ("Large-caps (banks, IT, Reliance)", "high-foreign-ownership names sold first")]},
        ],
    },
    # ------------------------------------------------------------- DOLLAR ↑
    "dxy_pct": {
        "up": [
            {"branch": "dollar→EM-flows", "sub_sectors": ["Banks/Financials"],
             "hops": [
                 ("Dollar Index", "USD strengthens"),
                 ("USDINR", "rupee under pressure"),
                 ("FII Flows", "dollar strength pulls capital from EM"),
                 ("Nifty", "foreign selling weighs on the index")]},
        ],
    },
    # ---------------------------------------------------------------- FII
    "fii_kcr": {
        "down": [
            {"branch": "foreign-selling", "sub_sectors": ["Banks/Financials", "IT Services"],
             "hops": [
                 ("FII Net Sell", "foreign investors reduce India"),
                 ("High-foreign-ownership Large-caps", "banks, IT, Reliance sold first"),
                 ("Nifty / Bank Nifty", "index pressured (DII flows cushion)")]},
        ],
        "up": [
            {"branch": "foreign-buying", "sub_sectors": ["Banks/Financials", "IT Services"],
             "hops": [
                 ("FII Net Buy", "foreign inflows into India"),
                 ("High-foreign-ownership Large-caps", "banks, IT, Reliance bought"),
                 ("Nifty / Bank Nifty", "index supported")]},
        ],
    },
    # ---------------------------------------------------------------- VIX ↑
    "vix_pct": {
        "up": [
            {"branch": "risk-off", "sub_sectors": ["High-beta", "NBFC", "Small/Mid caps"],
             "hops": [
                 ("India VIX", "implied vol rises"),
                 ("Risk-Off", "positioning de-risks"),
                 ("High-beta / Leveraged Names", "sold as risk appetite falls")]},
        ],
    },
    # ------------------------------------------------------------ INDIA CPI
    "india_cpi_hot": {
        "up": [
            {"branch": "cpi→rates", "sub_sectors": ["Banks/Financials", "NBFC", "Auto", "Real Estate"],
             "hops": [
                 ("India CPI", "inflation prints above forecast"),
                 ("RBI", "stays hawkish"),
                 ("Yield Curve", "rates higher-for-longer"),
                 ("Rate-sensitives", "financials, NBFCs, autos, realty pressured")]},
        ],
    },
    # --------------------------------------------------------------- US CPI
    "us_cpi_cool": {
        "up": [
            {"branch": "us-disinflation→risk-on", "sub_sectors": ["Semiconductors", "IT Services", "Banks/Financials"],
             "hops": [
                 ("US CPI", "US inflation cools"),
                 ("Fed Easing Hopes", "rate-cut expectations build"),
                 ("Global Risk-On", "EM equities & FII flows supported"),
                 ("Nifty", "global tailwind partly offsets India headwinds")]},
        ],
    },
    # ---------------------------------------------------------- GEOPOLITICS
    "geopolitics_hits": {
        "up": [
            {"branch": "geopolitics→oil→inflation", "sub_sectors": ["Oil & Gas", "Defence", "Banks/Financials"],
             "hops": [
                 ("Geopolitical Risk", "Middle-East / Hormuz tension"),
                 ("Oil", "supply-risk premium"),
                 ("Inflation", "energy-led price pressure"),
                 ("RBI / Rates", "policy stays tight"),
                 ("Banks & Consumer", "rate + demand channel")]},
            {"branch": "geopolitics→freight", "sub_sectors": ["Chemicals", "Auto Components", "EMS", "Pharma API"],
             "hops": [
                 ("Geopolitical Risk", "shipping-lane disruption"),
                 ("Freight / Tanker Insurance", "container & tanker rates up"),
                 ("Import Landed Cost", "input-cost squeeze"),
                 ("Import-reliant Manufacturers", "chemicals, auto-components, EMS, pharma API")]},
        ],
    },
}


def direction_of(driver_key: str, value: float | None) -> str | None:
    """Map a Core driver value to the chain direction ('up'/'down')."""
    if value is None or value == 0:
        return None
    lib = CHAIN_LIBRARY.get(driver_key, {})
    if driver_key in ("india_cpi_hot", "us_cpi_cool", "geopolitics_hits"):
        return "up" if value else None
    if value > 0:
        return "up" if "up" in lib else None
    return "down" if "down" in lib else None


def expand(driver_key: str, value: float | None,
           regime: str | None = None) -> list[dict]:
    """Return the full economic pathways for an active driver, as chains.

    regime gates the AI branches: only the branch matching the active AI regime
    (Substitution/Complement) is emitted for SOX.
    """
    direction = direction_of(driver_key, value)
    if direction is None:
        return []
    branches = CHAIN_LIBRARY.get(driver_key, {}).get(direction, [])
    out = []
    for b in branches:
        name = b["branch"]
        if driver_key == "sox_pct":
            if "substitution" in name and regime != "Substitution":
                continue
            if "complement" in name and regime != "Complement":
                continue
        out.append({
            "driver": driver_key,
            "branch": name,
            "chain": [h[0] for h in b["hops"]],
            "mechanisms": [f"{h[0]}: {h[1]}" for h in b["hops"]],
            "sub_sectors": b.get("sub_sectors", []),
        })
    return out
