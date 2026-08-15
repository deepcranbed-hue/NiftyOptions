"""
impact_scoring.py — turn a raw extracted number into a standardized IMPACT score.

A raw value ("NIM 3.6%", "deposits +15%") is not impact. Impact = how far the number is
from what was expected, whether it crosses a materiality band, and how heavy the entity is.

Four inputs, combined:
  1. SURPRISE       actual - consensus, if a consensus/estimate is supplied (dominant factor).
  2. DEVIATION      z = (value - baseline) / std, when no consensus (how unusual is it).
  3. MATERIALITY    some levels/events are inherently material (USFDA warning letter, GNPA
                    stress band, big NIM delta) — a band lookup, independent of surprise.
  4. ENTITY WEIGHT  index impact scales with the company's Nifty weight — the same beat at a
                    heavyweight moves the index; at a micro-cap it doesn't.

Output per metric: impact_score (0-1), a Low/Moderate/High/Very-High label, the signed
index_impact (impact × quality direction × weight), and a one-line rationale. Baselines are
PRIOR (sector-typical priors); replace with calibrated values from history when available.
"""
from __future__ import annotations

# metric -> baseline prior. {baseline, std, material_delta, higher_is_better, unit}
# 'higher_is_better' is the QUALITY direction for the stock (GNPA lower is better).
BASELINES = {
    "NIM":                       {"baseline": 3.3,  "std": 0.4,  "higher_is_better": True,  "unit": "%"},
    "CASA":                      {"baseline": 42.0, "std": 5.0,  "higher_is_better": True,  "unit": "%"},
    "Deposit growth":            {"baseline": 11.0, "std": 3.0,  "higher_is_better": True,  "unit": "%"},
    "Credit / advances growth":  {"baseline": 14.0, "std": 3.0,  "higher_is_better": True,  "unit": "%"},
    "GNPA":                      {"baseline": 3.0,  "std": 1.0,  "higher_is_better": False, "unit": "%"},
    "PAT growth":                {"baseline": 12.0, "std": 8.0,  "higher_is_better": True,  "unit": "%"},
    "Revenue growth":            {"baseline": 10.0, "std": 5.0,  "higher_is_better": True,  "unit": "%"},
}

_LABELS = [(2.5, "Very High"), (1.5, "High"), (0.7, "Moderate"), (0.0, "Low")]


def _label(z_abs: float) -> str:
    for thr, name in _LABELS:
        if z_abs >= thr:
            return name
    return "Low"


def _usfda_impact(value: float, text: str) -> dict:
    """USFDA severity is about the KIND of action, not just observation count."""
    t = (text or "").lower()
    if "warning letter" in t or "import alert" in t or "opai" in t:
        z, band = 3.0, "Very High"
    elif value >= 8:
        z, band = 2.6, "Very High"
    elif value >= 5:
        z, band = 1.8, "High"
    elif value >= 1:
        z, band = 1.0, "Moderate"
    else:
        z, band = 0.2, "Low (clean / zero-483)"
    return {"z": z, "label": band, "quality_dir": -1,
            "rationale": f"{int(value)} observations" + (" + warning letter/import alert" if band == 'Very High' and 'warning' in t else "")}


def _capex_impact(value: float, consensus: float | None) -> dict:
    """Hyperscaler capex: impact from the % change vs prior guidance (if known), else level."""
    if consensus:
        chg = (value - consensus) / consensus * 100
        z = min(3.0, abs(chg) / 8.0)          # ~8% guidance change = ~1σ
        return {"z": z, "label": _label(z), "quality_dir": 1 if chg > 0 else -1,
                "rationale": f"capex guidance {chg:+.0f}% vs prior ${consensus:g}bn"}
    # no prior: size bands ($bn absolute)
    z = min(3.0, value / 30.0)
    return {"z": z, "label": _label(z), "quality_dir": 1,
            "rationale": f"${value:g}bn capex (no prior guidance to compare)"}


def score_metric(metric: dict, nifty_weight: float | None = None,
                 consensus: float | None = None) -> dict:
    """Return an impact assessment for one extracted metric dict (from extract.py)."""
    name = metric.get("metric")
    val = metric.get("value")
    text = metric.get("context", "") + " " + metric.get("source", "")

    # --- special cases ---
    if name == "USFDA observations":
        core = _usfda_impact(val, text)
    elif name == "Hyperscaler capex":
        core = _capex_impact(val, consensus)
    elif name in BASELINES and val is not None:
        b = BASELINES[name]
        if consensus is not None and b["std"]:
            z = (val - consensus) / b["std"]          # surprise vs consensus (preferred)
            basis = f"vs consensus {consensus:g}{b['unit']}"
        else:
            z = (val - b["baseline"]) / b["std"]        # deviation from baseline
            basis = f"vs baseline {b['baseline']:g}{b['unit']} (±{b['std']:g})"
        good = (z > 0) == b["higher_is_better"]
        core = {"z": abs(z), "label": _label(abs(z)),
                "quality_dir": 1 if good else -1,
                "rationale": f"{val:g}{b['unit']} {basis} → {z:+.1f}σ"}
    else:
        # entity-relative currency metrics (provisions, slippages, TCV) — direction only,
        # magnitude needs the company's base (book size). Honest low-confidence read.
        qd = metric.get("quality_sign", 0)
        d = metric.get("direction")
        core = {"z": 0.4, "label": "Unscaled (needs company base)",
                "quality_dir": (1 if d == "up" else -1 if d == "down" else 0) * (1 if qd >= 0 else -1),
                "rationale": "absolute-currency metric — impact scales with the company's book size"}

    impact_score = round(min(1.0, core["z"] / 3.0), 3)
    weight = nifty_weight if nifty_weight is not None else None
    # index impact = surprise magnitude × direction × entity weight (as a fraction)
    w_frac = (weight / 100.0) if (weight and weight > 1) else (weight or 0.0)
    index_impact = round(impact_score * core["quality_dir"] * (w_frac if w_frac else 0.15), 4)

    return {
        "metric": name, "value": val,
        "impact_score": impact_score,
        "impact_label": core["label"],
        "surprise_sigma": round(core["z"], 2),
        "quality_direction": core["quality_dir"],       # +1 good for stock, -1 bad
        "nifty_weight": weight,
        "index_impact": index_impact,                    # signed, weight-scaled
        "rationale": core["rationale"],
        "tag": "PRIOR",
    }


def rank(metrics_with_scores: list[dict]) -> list[dict]:
    """Sort metrics by absolute index impact (highest first)."""
    return sorted(metrics_with_scores, key=lambda m: -abs(m.get("index_impact", 0)))
