"""
interactions.py — explicit cross-driver interaction terms.

The review's biggest quantitative point: drivers interact. Oil AND a strong dollar together
produce a larger FII effect than either alone; the engine's single 'interaction' number is
too generic. Here we compute NAMED interaction terms from the Core's own driver values, so
the second-order effects are explicit and inspectable rather than buried.

Each term is a product of the two drivers' normalized magnitudes × a PRIOR weight, with the
sign set by economic logic. Tagged PRIOR — these are judgement priors until calibrated, and
they annotate (not overwrite) the Core's numbers.
"""
from __future__ import annotations

# (name, driver_a, driver_b, prior_weight, sign_rule, mechanism)
# sign_rule: function(a, b) -> +1 / -1  (economic direction of the JOINT effect on Nifty)
_TERMS = [
    ("Oil × USD",
     "oil_pct", "dxy_pct", 0.9,
     "both-adverse",
     "Rising oil AND a strong dollar compound the import-bill + outflow hit — a weaker rupee "
     "imports inflation while foreign capital leaves; jointly more bearish than either alone."),
    ("Oil × India-CPI",
     "oil_pct", "india_cpi_hot", 1.0,
     "both-adverse",
     "High oil into an already-hot CPI compounds the inflation signal → RBI stays tighter "
     "for longer → rate-sensitives hit harder."),
    ("US10Y × Dollar",
     "us10y_pct", "dxy_pct", 0.9,
     "both-adverse",
     "Rising US yields with a strengthening dollar is the classic FII-outflow accelerant for "
     "EM equity — the combined pull exceeds the sum of parts."),
    ("FII × VIX",
     "fii_kcr", "vix_pct", 1.0,
     "sell-into-fear",
     "FII selling INTO rising volatility is self-reinforcing — forced de-risking amplifies the "
     "drawdown beyond the flow number itself."),
    ("AI × SOX",
     "sox_pct", "us_cpi_cool", 0.6,
     "risk-on",
     "A semis/AI rally alongside cooling US inflation is a coherent global risk-on impulse for "
     "Indian tech & EM flows — mutually reinforcing."),
    ("Oil × Geopolitics",
     "oil_pct", "geopolitics_hits", 1.1,
     "both-adverse",
     "A geopolitical flare-up ON TOP of rising oil is a supply-shock accelerant — the risk "
     "premium compounds the price move → inflation + CAD stress beyond either alone."),
    ("USD × FII",
     "dxy_pct", "fii_kcr", 0.9,
     "sell-into-strength",
     "A strengthening dollar alongside FII selling is self-reinforcing EM outflow pressure — "
     "the currency move and the flow feed each other."),
    ("US-CPI × Fed-path (yields)",
     "us_cpi_cool", "us10y_pct", 0.7,
     "dovish-risk-on",
     "US CPI and the Fed path are NOT independent — cooling CPI with falling yields is a "
     "coherent dovish/risk-on signal for EM equity; the two reinforce."),
]


import common
_norm = common.norm            # single source of truth
_CAPS = common.CAPS


def compute(drivers: dict) -> list[dict]:
    """Return active interaction terms (both legs non-trivial), largest first."""
    out = []
    for name, a, b, w, rule, mech in _TERMS:
        va = drivers.get(a)
        vb = drivers.get(b)
        na = _norm(va, _CAPS.get(a, 1.0))
        nb = _norm(vb, _CAPS.get(b, 1.0))
        if abs(na) < 0.05 or abs(nb) < 0.05:      # need BOTH legs active
            continue
        magnitude = round(abs(na) * abs(nb) * w, 3)
        if magnitude < 0.01:
            continue
        # sign of the joint effect on Nifty
        if rule == "both-adverse":
            sign = -1
        elif rule == "sell-into-fear":
            sign = -1 if (va is not None and va < 0) else +1   # FII net sell → bearish
        elif rule == "sell-into-strength":
            sign = -1 if (vb is not None and vb < 0) else +1   # USD up + FII sell → bearish
        elif rule == "dovish-risk-on":
            sign = +1 if (vb is not None and vb < 0) else -1   # cooling CPI + yields down → bullish
        elif rule == "risk-on":
            sign = +1
        else:
            sign = -1
        out.append({
            "term": name, "legs": [a, b],
            "leg_values": {a: va, b: vb},
            "magnitude": magnitude, "sign": sign,
            "tag": "PRIOR",
            "mechanism": mech,
        })
    out.sort(key=lambda x: -x["magnitude"])
    return out


def dominance_boost(interactions: list[dict]) -> dict:
    """How much extra weight the interactions imply for each participating driver — the
    answer to 'oil 7% is too low': oil's effective importance rises through interaction terms,
    not through a bigger direct coefficient. Returns {driver_key: extra_share_points}."""
    boost: dict[str, float] = {}
    for it in interactions:
        for leg in it["legs"]:
            boost[leg] = round(boost.get(leg, 0.0) + it["magnitude"] / 2.0, 3)
    return boost
