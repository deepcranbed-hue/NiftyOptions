from datetime import datetime, timezone
from collections import defaultdict

# Directional priors for US Macro Factors mapping to NIFTY
# Format: factor: (India impact sign, default assumption logic for sign match)
US_MACRO_PRIORS = {
    "inflation": -1.0,  # hot inflation is negative for India (rates up, FII out)
    "fed": -1.0,        # hawkish fed is negative
    "jobs": -0.5,       # strong jobs is mildly negative (supports hawkish fed)
    "oil": -1.0,        # rising oil is negative (falling oil is positive). Wait, let's just assume the sign applies to "hot/hawkish/rising". If "falling", we invert it!
    "us_tech": 1.0,     # strong tech is positive for NIFTY IT, weak tech is negative
    "vix": -1.0         # rising VIX (risk-off) is negative, falling VIX is positive
}

def invert_if_falling(surprise: str, base_sign: float) -> float:
    if not surprise:
        return base_sign
    
    s = surprise.lower()
    # If the surprise implies softening/falling/weakness, invert the natural sign of the factor.
    # For example, "falling" oil -> base is -1.0 -> inverted to +1.0 (India positive)
    # "dovish" fed -> base is -1.0 -> inverted to +1.0 (India positive)
    # "weak" jobs -> base is -0.5 -> inverted to +0.5 (India positive)
    invert_words = ["falling", "soft", "weak", "dovish", "cooling", "miss", "lower", "cut", "easing", "risk-on"]
    if any(w in s for w in invert_words):
        return -base_sign
    
    return base_sign

def synthesize_macro(articles: list[dict]) -> dict:
    """
    Reads tagged articles, extracts 'us_factor' and 'surprise_direction',
    nets them into a cross-current tilt, and separates the forces explicitly.
    """
    now = datetime.now(timezone.utc)
    
    # Aggregate factors
    factor_states = {}
    
    for a in articles:
        raw_factor = a.get("us_factor")
        factors = raw_factor if isinstance(raw_factor, list) else [raw_factor]
        
        for factor in factors:
            if factor and isinstance(factor, str) and factor in US_MACRO_PRIORS:
                # Simple aggregation: take the latest or highest confidence one
                # We'll just collect them and pick the most recent valid one per factor
                published_at = a.get("published_at", "")
                if isinstance(published_at, str):
                    try:
                        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    except:
                        dt = now
                else:
                    dt = published_at if published_at else now
                    
                raw_surprise = a.get("surprise_direction", "unknown")
                surprise = raw_surprise[0] if isinstance(raw_surprise, list) and raw_surprise else (raw_surprise if isinstance(raw_surprise, str) else "unknown")
                
                if factor not in factor_states or factor_states[factor]["dt"] < dt:
                    factor_states[factor] = {
                        "surprise": surprise,
                        "title": a.get("title", ""),
                        "dt": dt,
                        "source": a.get("source", "News"),
                        "confidence": a.get("confidence", 0.5)
                    }

    net_tilt = 0.0
    positive_forces = []
    negative_forces = []
    sector_notes = []
    
    for factor, state in factor_states.items():
        base_sign = US_MACRO_PRIORS[factor]
        actual_sign = invert_if_falling(state["surprise"], base_sign)
        
        # Add to net tilt
        tilt_contribution = actual_sign * state["confidence"]
        net_tilt += tilt_contribution
        
        desc = f"{factor.upper()} {state['surprise']} ({state['title']} - via {state['source']})"
        
        if actual_sign > 0.2:
            positive_forces.append(desc)
        elif actual_sign < -0.2:
            negative_forces.append(desc)
            
        if factor == "us_tech":
            impact = "positive" if actual_sign > 0 else "drag"
            sector_notes.append(f"US Tech is {state['surprise']} -> {impact} on NIFTY IT.")
            
    # Clip net tilt
    net_tilt = max(-1.0, min(1.0, net_tilt))
    
    return {
        "net_tilt": net_tilt,
        "positive_forces": positive_forces,
        "negative_forces": negative_forces,
        "sector_notes": sector_notes,
        "as_of": now.isoformat(),
        "has_data": len(factor_states) > 0
    }
