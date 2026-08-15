"""
subsectors.py — deep sub-sector factor models (Phase 2).

The review: "Auto → Oil" is too broad; PV / CV / 2W / EV / components react differently.
Banking is driven by deposit/credit/CASA/NIM/provisions, not just macro. Pharma needs
USFDA / API / USD / export. FMCG needs food inflation / monsoon / palm / rural.

This encodes each parent sector's sub-sectors as a factor model. Two factor kinds:
  * MACRO factors — computable from today's tape (oil, US10Y, USDINR, DXY) via the Core.
  * STRUCTURAL/CATALYST factors — either detected from news (USFDA, monsoon, palm, EV theme)
    or flagged as "needs data" (deposit growth, CASA, NIM) with their exposure SIGN so the
    desk knows the driver and its direction even when the live value isn't in the feed.

No engine edits: computes from Core signals + news; honestly separates computed vs needs-data.
"""
from __future__ import annotations

# factor exposure sign/weight per sub-sector.
# kind: "macro" (computed from tape) | "catalyst" (from news) | "fundamental" (needs data)
SUBSECTOR_FACTORS: dict[str, dict[str, list[tuple]]] = {
    # (factor_label, weight, kind, keywords-for-catalyst)
    "Auto": {
        "Passenger Vehicles (PV)": [
            ("Oil / fuel cost", -0.30, "macro", "oil_pct"),
            ("Interest rates (financing)", -0.40, "macro", "us10y_pct"),
            ("Festival / urban demand", +0.25, "catalyst", ["festival", "discount", "launch"]),
            ("Rural sentiment", +0.15, "catalyst", ["rural", "monsoon"]),
        ],
        "Commercial Vehicles (CV)": [
            ("Diesel / freight cost", -0.50, "macro", "oil_pct"),
            ("Rates (heavy financing)", -0.50, "macro", "us10y_pct"),
            ("Infra / industrial activity", +0.40, "catalyst", ["infra", "capex", "e-way", "freight"]),
        ],
        "Two-Wheelers (2W)": [
            ("Petrol price", -0.40, "macro", "oil_pct"),
            ("Rural demand", +0.50, "catalyst", ["rural", "monsoon", "kharif", "msp"]),
            ("Rates (financing)", -0.20, "macro", "us10y_pct"),
        ],
        "Electric Vehicles (EV)": [
            ("High oil → EV attractive", +0.30, "macro", "oil_pct"),
            ("EV policy / theme", +0.55, "catalyst", ["ev ", "electric vehicle", "e-scooter", "ather", "ola electric", "pli", "subsidy"]),
            ("Battery / metal input cost", -0.20, "macro", "copper_pct"),
        ],
        "Auto Components": [
            ("Rupee (exporters)", +0.35, "macro", "usdinr_level_sign"),
            ("Input cost (oil/metals)", -0.30, "macro", "oil_pct"),
            ("Global/US auto demand", +0.30, "catalyst", ["us auto", "export order", "global auto"]),
        ],
    },
    "Banks/Financials": {
        "Deposit franchise": [
            ("Deposit growth", +0.40, "fundamental", "deposit growth / mobilization"),
            ("CASA ratio", +0.35, "fundamental", "low-cost CASA mix"),
        ],
        "Credit / growth": [
            ("Credit growth", +0.45, "fundamental", "loan book / advances growth"),
            ("Rates (NIM)", +0.20, "macro", "us10y_pct"),
        ],
        "Margins / quality": [
            ("Net Interest Margin (NIM)", +0.35, "fundamental", "spread / cost of funds"),
            ("Provisions / asset quality", -0.40, "fundamental", "GNPA / slippage / credit cost"),
            ("Treasury MTM (yields up = hit)", -0.30, "macro", "us10y_pct"),
        ],
        "Catalysts": [
            ("Quarterly results", +0.0, "catalyst", ["result", "q1", "q2", "q3", "q4", "profit", "nim", "gnpa", "slippage"]),
        ],
    },
    "Pharma": {
        "US generics / exports": [
            ("USD / rupee (export)", +0.35, "macro", "usdinr_level_sign"),
            ("US export demand", +0.30, "catalyst", ["us fda", "usfda", "anda", "us market", "export"]),
            ("Generic price erosion", -0.35, "catalyst", ["price erosion", "generic pricing", "pricing pressure"]),
        ],
        "Regulatory": [
            ("USFDA action (483 / warning / clearance)", -0.45, "catalyst", ["usfda", "us fda", "483", "warning letter", "import alert", "inspection"]),
            ("Patent cliff / launch", +0.30, "catalyst", ["patent", "launch", "approval", "exclusivity"]),
        ],
        "Inputs": [
            ("China API cost", -0.30, "catalyst", ["china api", "api cost", "active pharma", "key starting material"]),
        ],
    },
    "FMCG": {
        "Rural demand": [
            ("Monsoon / rural wage", +0.45, "catalyst", ["monsoon", "rural", "kharif", "msp", "rural wage"]),
            ("Festival demand", +0.25, "catalyst", ["festival", "diwali", "wedding"]),
        ],
        "Input cost / margin": [
            ("Palm oil / edible oil", -0.40, "catalyst", ["palm oil", "edible oil", "vegetable oil"]),
            ("Crude derivatives (packaging)", -0.25, "macro", "oil_pct"),
            ("Food inflation (pricing power vs volume)", -0.20, "catalyst", ["food inflation", "cpi food", "wheat", "sugar"]),
        ],
    },
}


def _news_hit(news, keywords) -> bool:
    for n in news or []:
        t = _text(n)
        if any(k in t for k in keywords):
            return True
    return False


import common

def _text(n: dict) -> str:
    return common.news_text(n)


# fundamental factors the crawler CAN surface from earnings/results coverage.
# {factor_label: (keywords, up_cues, down_cues)}  — direction inferred from the text.
_FUNDAMENTAL_CUES = {
    "deposit growth / mobilization": (["deposit"],
        ["deposit growth", "deposits rose", "deposits up", "strong deposit", "deposit accretion"],
        ["deposit growth slow", "deposits fell", "weak deposit", "deposit crunch"]),
    "low-cost CASA mix": (["casa"],
        ["casa rose", "casa improved", "casa expansion", "higher casa"],
        ["casa fell", "casa declined", "casa pressure", "lower casa"]),
    "spread / cost of funds": (["nim", "net interest margin", "margin"],
        ["nim expand", "margin expand", "nim rose", "margin improved", "nim up"],
        ["nim compress", "margin compress", "nim fell", "margin pressure", "nim contract"]),
    "GNPA / slippage / credit cost": (["gnpa", "npa", "slippage", "asset quality", "provision", "credit cost"],
        ["asset quality improved", "lower slippage", "gnpa fell", "provisions eased", "npa declined"],
        ["slippage rose", "gnpa rose", "higher provision", "asset quality deteriorat", "npa rose", "credit cost up"]),
    "loan book / advances growth": (["credit growth", "advances", "loan growth", "loan book"],
        ["credit growth", "advances grew", "loan growth strong", "robust credit"],
        ["credit growth slow", "advances fell", "weak loan", "loan growth moderat"]),
}


def _detect_fundamental(news, factor_label: str, base_sign: str):
    """Try to surface a fundamental factor from crawled news with a direction.
    Returns (contrib, evidence) or (None, None) if not mentioned today."""
    cue = _FUNDAMENTAL_CUES.get(factor_label)
    if not cue:
        return None, None
    keys, up_cues, down_cues = cue
    for n in news or []:
        t = _text(n)
        if not any(k in t for k in keys):
            continue
        up = any(c in t for c in up_cues)
        down = any(c in t for c in down_cues)
        if not (up or down):
            continue
        # base_sign is the factor's exposure sign; direction from the text sets the outcome
        exposure = 1 if base_sign == "＋" else -1
        move = 1 if up and not down else -1 if down and not up else 0
        if move == 0:
            continue
        contrib = round(0.10 * exposure * move, 3)
        return contrib, n.get("title", "")
    return None, None


# subsector fundamental factor -> the parsed metric that scales it
FUND_METRIC_MAP = {
    "deposit growth / mobilization": "Deposit growth",
    "low-cost CASA mix": "CASA",
    "spread / cost of funds": "NIM",
    "GNPA / slippage / credit cost": "GNPA",
    "loan book / advances growth": "Credit / advances growth",
}


def _impact_scaled(key: str, extracted: list[dict]):
    """If a parsed+scored metric matches this fundamental, use its impact as the contrib
    magnitude (impact_score × quality_direction). Returns (contrib, evidence) or (None, None)."""
    want = FUND_METRIC_MAP.get(key)
    if not want or not extracted:
        return None, None
    for m in extracted:
        if m.get("metric") == want and m.get("quality_direction"):
            # scale impact (0-1) into the factor-contribution range, preserving ordering
            contrib = round(m.get("impact_score", 0.0) * 0.5 * m["quality_direction"], 3)
            if abs(contrib) >= 0.005:
                return contrib, f"{m.get('value')}{m.get('unit','')} · {m.get('impact_label')} ({m.get('rationale','')})"
    return None, None


def build(signals: dict, news: list[dict], extracted: list[dict] | None = None) -> list[dict]:
    """signals: {oil_pct, us10y_pct, copper_pct, usdinr_sign} — Core macro moves.
    extracted: parsed+impact-scored fundamentals (from extract/impact_scoring) — when a
    fundamental factor has a matching parsed metric, its contrib is scaled by that impact.
    Returns per-parent-sector sub-sector factor breakdowns."""
    out = []
    for parent, subs in SUBSECTOR_FACTORS.items():
        sub_rows = []
        for sub, factors in subs.items():
            computed, structural, net = [], [], 0.0
            for label, w, kind, key in factors:
                if kind == "macro":
                    val = signals.get(key if isinstance(key, str) and key.endswith("_pct")
                                      else {"usdinr_level_sign": "usdinr_sign"}.get(key, key))
                    if val is None:
                        continue
                    contrib = round(w * val, 3)
                    if abs(contrib) >= 0.005:
                        computed.append({"factor": label, "contrib": contrib, "kind": "macro"})
                        net += contrib
                elif kind == "catalyst":
                    hit = _news_hit(news, key if isinstance(key, list) else [key])
                    if hit:
                        contrib = round(w if w != 0 else 0.05, 3)
                        computed.append({"factor": label, "contrib": contrib, "kind": "catalyst-hit"})
                        net += contrib
                    else:
                        structural.append({"factor": label, "sign": _sign(w), "kind": "catalyst (none today)"})
                else:  # fundamental — impact-scaled parsed number first, then news direction
                    contrib, evidence = _impact_scaled(key, extracted)
                    kind = "fundamental (parsed, impact-scaled)"
                    if contrib is None:
                        contrib, evidence = _detect_fundamental(news, key, _sign(w))
                        kind = "fundamental (from news)"
                    if contrib is not None:
                        computed.append({"factor": label, "contrib": contrib,
                                         "kind": kind, "evidence": evidence})
                        net += contrib
                    else:
                        structural.append({"factor": label, "sign": _sign(w),
                                           "kind": "fundamental (needs data)", "driver": key})
            net = round(net, 3)
            verdict = ("🟢 Bullish" if net > 0.10 else "🔴 Bearish" if net < -0.10 else "🟡 Neutral")
            sub_rows.append({
                "sub_sector": sub, "net_computed": net, "verdict": verdict,
                "computed_factors": computed, "structural_factors": structural,
            })
        sub_rows.sort(key=lambda r: -r["net_computed"])
        out.append({"parent": parent, "sub_sectors": sub_rows})
    return out


def _sign(w: float) -> str:
    return "＋" if w > 0 else "－" if w < 0 else "0"
