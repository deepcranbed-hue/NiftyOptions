def trace_bias(sector_scores: dict, sector_weights: dict, covered_weight: float, bias: float) -> dict:
    components = []
    for sec, score in sector_scores.items():
        w = sector_weights.get(sec, 0.0)
        if w > 0:
            components.append(f"{sec}({score:.2f} × {w:.1f}%)")
    
    comp_str = " + ".join(components)
    return {
        "formula": "bias = Σ(sectorᵢ_score × sectorᵢ_weight) / Σ(covered weight)",
        "subbed": f"[{comp_str}] / {covered_weight:.1f}% = {bias:.2f}",
        "meaning": "Index bias is the weighted sum of sentiment across active sectors."
    }

def trace_complacency(components: dict, weights: dict, score: float) -> dict:
    parts = []
    for k, v in components.items():
        w = weights.get(k, 0.0)
        parts.append(f"{k}({v:.2f} × {w:.2f})")
    
    comp_str = " + ".join(parts)
    return {
        "formula": "score = Σ(componentᵢ × weightᵢ) × 100",
        "subbed": f"[{comp_str}] × 100 = {score:.1f}",
        "meaning": "Complacency scores how calm or underpriced risk is in the options chain (100 = perfectly calm)."
    }

def trace_rnd(sd: float, p_below: float, p_above: float, skew: float) -> dict:
    return {
        "formula": "Breeden–Litzenberger density (2nd derivative of call prices w.r.t strike)",
        "subbed": f"Expected Move(1σ) = ±{sd:.1f} pts, P(below_spot) = {p_below*100:.1f}%, P(above_spot) = {p_above*100:.1f}%, Skew = {skew:.3f}",
        "meaning": "Risk-Neutral Distribution extracts the market's true probability density from option prices."
    }

def trace_sizing(caps: dict, lots: int, max_loss_pts: float, lot_size: int) -> dict:
    caps_str = ", ".join(f"{k}={v}" for k, v in caps.items())
    return {
        "formula": "lots = min(risk_per_trade, complacency_cap, heat_cap, delta_cap, vega_cap)",
        "subbed": f"min({caps_str}) = {lots} lots. Max Loss Rs = {max_loss_pts:.1f} × {lot_size} × {lots} = Rs {(max_loss_pts * lot_size * lots):.0f}",
        "meaning": "Trade sizing is constrained restrictively by the lowest computed risk cap."
    }

def trace_metals(cu: float, au: float, ag: float, growth: float, fear: float) -> dict:
    return {
        "formula": "growth = clip(cu×0.7 + ag×0.3), fear = clip(au)",
        "subbed": f"growth = clip({cu:.2f}×0.7 + {ag:.2f}×0.3) = {growth:.2f}, fear = clip({au:.2f}) = {fear:.2f}",
        "meaning": "Copper/Silver track global growth optimism; Gold tracks fear/real-rates."
    }

def trace_flow(fii: float, dii: float, tilt: float) -> dict:
    return {
        "formula": "tilt = clip((fii_cum + 0.5×dii_cum) / 20000)",
        "subbed": f"clip(({fii:.1f} + 0.5 × {dii:.1f}) / 20000) = {tilt:.2f}",
        "meaning": "Institutional flow tilt tracks cumulative FII cash (and half-weighted DII) over a short window, scaled to ±1."
    }
