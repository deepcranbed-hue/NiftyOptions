"""
sectors.py — Energy upstream/OMC split + company→sector bridge.

Two review fixes:
  6) The sector factor model returns 'Energy (upstream)' but never a separate OMC line —
     "BPCL is not ONGC". Upstream is LONG crude (higher realisations); OMCs are SHORT crude
     (input cost, capped retail margins). We add the OMC line with the opposite oil sign.
 13) Company catalysts (e.g. 'HDFC Bank Q1 beat') never fed the sector model — Company and
     Sector felt separate. We fold company sentiment into the matching sector score, so a
     bank beat lifts Banks/Financials inside the factor model.

Operates purely on the Core's outputs (sector_intelligence + company_intelligence); it adds
and nudges sector rows, tagging every addition so nothing looks like a native Core number.
"""
from __future__ import annotations

# map a company gazetteer sector -> the factor-model sector bucket
COMPANY_SECTOR_MAP = {
    "Bank": "Banks/Financials", "PSU Bank": "Banks/Financials", "Financials": "Banks/Financials",
    "Insurance": "Banks/Financials", "Gold financier": "Banks/Financials",
    "IT": "IT services",
    "Auto": "Auto", "Auto-2W": "Auto", "Auto-CV": "Auto", "Auto-PV/UV": "Auto",
    "Auto-2W/CV": "Auto", "Auto/EV": "Auto", "EV": "Auto", "Defence/Auto": "Auto",
    "Pharma": "Pharma", "Metals": "Metals",
    "Energy (upstream)": "Energy (upstream)", "Energy (OMC)": "Energy (OMC)",
    "Energy/Conglomerate": "Energy (upstream)",
    "FMCG": "FMCG", "Capital Goods": "Capital Goods", "Capital goods": "Capital Goods",
}


def split_energy(sectors: list[dict], oil_pct: float | None) -> list[dict]:
    """Ensure a distinct OMC line exists with the opposite crude sign to Upstream."""
    names = {s["sector"] for s in sectors}
    if "Energy (OMC)" in names:
        return sectors
    up = next((s for s in sectors if s["sector"] == "Energy (upstream)"), None)
    if up is None or oil_pct is None or abs(oil_pct) < 0.3:
        return sectors
    # OMC net ~ opposite of upstream on the crude leg (marketing-margin squeeze when oil up)
    omc_net = -round(0.6 * up.get("net", 0.0), 3) if up.get("net") is not None else \
        (-0.10 if oil_pct > 0 else 0.10)
    verdict = ("🟢 Bullish" if omc_net > 0.10 else "🔴 Bearish" if omc_net < -0.10 else "🟡 Neutral")
    sectors = list(sectors) + [{
        "sector": "Energy (OMC)",
        "net": omc_net, "verdict": verdict,
        "rows": [("Crude input cost (opposite of upstream)", omc_net)],
        "note": "overlay: OMCs are SHORT crude — marketing margins squeezed when oil rises "
                "and retail prices are capped (distinct from upstream realisations).",
        "overlay": True,
    }]
    return sectors


def bridge_companies(sectors: list[dict], companies: list[dict]) -> list[dict]:
    """Nudge sector scores by company catalysts (bridging Company → Sector)."""
    by_sector: dict[str, list[dict]] = {}
    for c in companies or []:
        bucket = COMPANY_SECTOR_MAP.get(c.get("sector", ""), None)
        if not bucket:
            continue
        by_sector.setdefault(bucket, []).append(c)

    out = []
    for s in sectors:
        cos = by_sector.get(s["sector"], [])
        if cos:
            nudge = 0.0
            names = []
            for c in cos:
                sgn = {"pos": 1, "neg": -1}.get(c.get("sentiment"), 0)
                w = 0.06 if c.get("kind") == "Catalyst" else 0.03    # catalysts weigh more
                nudge += sgn * w
                names.append(f"{c['company']}({'+' if sgn>0 else '-' if sgn<0 else '0'})")
            nudge = round(nudge, 3)
            if abs(nudge) >= 0.01:
                s = dict(s)
                s["net"] = round(s.get("net", 0.0) + nudge, 3)
                s["rows"] = list(s.get("rows", [])) + [(f"Company catalysts: {', '.join(names)}", nudge)]
                s["verdict"] = ("🟢 Bullish" if s["net"] > 0.10 else
                                "🔴 Bearish" if s["net"] < -0.10 else "🟡 Neutral")
                s["company_bridge"] = names
        out.append(s)
    out.sort(key=lambda s: -s.get("net", 0.0))
    return out
