"""
mio_builder.py — assemble a Market Intelligence Object (MIO) from the engine outputs.

Every number here is sourced from the Deterministic Core (market_scan.py, via core.py).
The builder only *re-projects* those numbers into the standardized MIO shape defined in
NewsAgent/MARKET_INTELLIGENCE_OBJECT.md and validated against
NewsAgent/schemas/mio.schema.json.

Honesty notes (kept faithful to the framework's HARD RULES):
  * The engine is a directional *intuition* read over ~1 session, not a calibrated forecast.
    So `impact.immediate` / `impact.short` carry the engine's expected move; `medium` and
    `structural` are emitted Neutral/0 with an explicit note rather than fabricated.
  * `historical_reliability` is tagged PRIOR — no >=60-session calibration is wired in this
    server, so it is presented as descriptive only (mirrors D-MA-04).
"""
from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from typing import Any

import core

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "mio.schema.json"


def _dir(x: float | None, band: float = 0.10) -> str:
    if x is None:
        return "Neutral"
    if x > band:
        return "Up"
    if x < -band:
        return "Down"
    return "Neutral"


def _sign(x: float | None) -> int:
    if x is None:
        return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _stars(conviction: str) -> int:
    return {"High": 5, "Moderate": 3, "Low": 2}.get(conviction, 3)


def build_mio() -> dict:
    """Assemble a session-level MIO from the current snapshot's engine outputs."""
    s = core._ensure()
    eng = core.run_engine()
    reg = core.detect_regime()
    dom = core.driver_dominance("Nifty 50")
    sectors = core.sector_intelligence()
    companies = core.company_intelligence()
    themes = core.market_themes()
    validations = core.validate_relationships()

    nifty = eng["indices"].get("Nifty 50", {})
    nifty_total = nifty.get("total", 0.0)
    contrib = nifty.get("contrib", {})

    # --- event: derived from the dominant driver ---------------------------
    dom_key = dom.get("dominant_driver_key")
    ev_class, ev_label = core.DRIVER_EVENT.get(dom_key, ("Market", "Session Driver Move"))
    top_theme = themes[0]["name"] if themes else core.DRIVER_LABELS.get(dom_key, "Market")

    # --- oil level context: a +2% move at $100 bites harder than at $70 -----
    brent = eng.get("brent_price")
    oil_lvl = core.oil_level(brent)               # {price, multiplier, band}
    oil_move = eng.get("drivers", {}).get("oil_pct", 0.0)

    # --- transmission: top drivers on Nifty as scored single-hop chains ----
    absmap = {k: abs(v) for k, v in contrib.items() if v}
    tot = sum(absmap.values()) or 1.0
    transmission = []
    for k, v in sorted(contrib.items(), key=lambda kv: -abs(kv[1])):
        if not v:
            continue
        label = core.DRIVER_LABELS.get(k, k)
        if k == "oil_pct" and brent is not None:
            mech = (f"Oil {oil_move:+.1f}% at Brent ${brent:.0f} "
                    f"({oil_lvl['band']}, x{oil_lvl['multiplier']:.1f} level amplifier) "
                    f"contributes {v:+.3f} to the Nifty expected move — the SAME % move bites "
                    f"~{oil_lvl['multiplier']:.1f}x vs the $75-85 normal band, because impact "
                    f"scales with the absolute price level, not just the % change.")
            entry = {"chain": [f"Oil (${brent:.0f})", "Nifty 50"], "score": round(abs(v)/tot, 3),
                     "sign": _sign(v), "mechanism": mech,
                     "driver_price": brent, "level_multiplier": oil_lvl["multiplier"],
                     "level_band": oil_lvl["band"]}
        else:
            entry = {"chain": [label, "Nifty 50"], "score": round(abs(v)/tot, 3),
                     "sign": _sign(v),
                     "mechanism": f"{label} contributes {v:+.3f} to the Nifty expected move "
                                  f"(coefficient x driver, capped & regime-aware)."}
        transmission.append(entry)
    if not transmission:  # schema requires >=1
        transmission.append({"chain": ["Market", "Nifty 50"], "score": 0.0, "sign": 0,
                             "mechanism": "No active driver above threshold this session."})

    # --- affected sectors --------------------------------------------------
    aff_sectors = []
    for row in sectors:
        net = row["net"]
        if abs(net) < 0.05:
            continue
        aff_sectors.append({
            "sector": row["sector"],
            "direction": _dir(net),
            "score": round(min(abs(net), 1.0), 3),
            "mechanism": (row["rows"][0][0] + " dominant") if row["rows"] else "net driver score",
        })

    # --- affected companies ------------------------------------------------
    aff_companies = []
    for c in companies[:12]:
        d = {"pos": "Up", "neg": "Down", "neutral": "Neutral"}.get(c.get("sentiment"), "Neutral")
        aff_companies.append({
            "company": c["company"],
            "direction": d,
            "score": round(min((c.get("nifty_wt") or 0.3) + 0.3, 1.0), 3),
            "nifty_weight": round((c.get("nifty_wt") or 0.0) / 100.0, 5)
                            if (c.get("nifty_wt") or 0) > 1 else (c.get("nifty_wt") or 0.0),
            "exposure_vector": {"sector": c.get("sector"), "catalyst_kind": c.get("kind")},
        })

    # --- expected direction map -------------------------------------------
    d = eng["drivers"]
    expected_direction = {
        "Nifty 50": _dir(nifty_total),
        "Bank Nifty": _dir(eng["indices"].get("Bank Nifty", {}).get("total")),
        "Bond Yield": _dir(d.get("us10y_pct")),
        "USD": _dir(d.get("dxy_pct")),
    }

    # --- impact by horizon (honest: engine is a ~1-session directional read)
    mag = round(abs(nifty_total), 3)
    dir_short = _dir(nifty_total)
    impact = {
        "immediate": {"direction": dir_short, "magnitude": mag, "unit": "pct_nifty",
                      "note": "engine expected move (intraday reaction)"},
        "short": {"direction": dir_short, "magnitude": mag, "unit": "pct_nifty",
                  "note": "1-5 day directional lean from the causal engine"},
        "medium": {"direction": "Neutral", "magnitude": 0.0, "unit": "pct_nifty",
                   "note": "not modelled — engine is a session-horizon read"},
        "structural": {"direction": "Neutral", "magnitude": 0.0, "unit": "pct_nifty",
                       "note": "not modelled — requires fundamental/regime overlay"},
    }

    # --- validation (expected vs observed) --------------------------------
    val_out = []
    for v in validations[:10]:
        exp_sign = 1 if v["expected"] == "↑" else (-1 if v["expected"] == "↓" else 0)
        obs_sign = exp_sign if v["status"] == "CONFIRMED" else -exp_sign if v["status"] == "OVERRIDDEN" else 0
        # per-proxy detail: which specific stocks held the relationship vs broke it
        checks = v.get("checks", [])
        held = [{"name": c["name"], "pct": c["observed_pct"]} for c in checks if c.get("ok")]
        broke = [{"name": c["name"], "pct": c["observed_pct"]} for c in checks if not c.get("ok")]
        entry = {"edge": v["name"], "expected_sign": exp_sign, "observed_sign": obs_sign,
                 "status": v["status"], "held": held, "broke": broke,
                 "confirmed": v.get("confirmed"), "disagreed": v.get("disagreed")}
        if v["status"] == "OVERRIDDEN":
            names = ", ".join(f"{c['name']} {c['pct']:+.1f}%" for c in broke[:4]) or "the proxies"
            entry["reason"] = (f"broke on {names} (weighted agreement {v['weighted_agreement_pct']}%) "
                               f"— a stronger concurrent driver, positioning, or policy override.")
        val_out.append(entry)

    # --- confidence triple -------------------------------------------------
    agreement = eng.get("agreement", 0.0)
    confidence = {
        "econ_rationale_stars": _stars(eng.get("conviction", "Moderate")),
        "historical_reliability": round(agreement, 2),   # PRIOR — descriptive only
        "today_confidence": round(agreement, 2),
        "reliability_tag": "PRIOR",
    }

    # --- driver dominance --------------------------------------------------
    driver_dominance = {
        "vector": dom["vector"],
        "dominant_driver": dom["dominant_driver"] or "None",
        "dominant_driver_score": dom["dominant_driver_score"],
    }

    as_of = s["as_of"]
    stamp = as_of.replace(":", "").replace("-", "")[:15]
    mio = {
        "mio_id": f"mio_{stamp}_{(dom_key or 'na')}",
        "as_of": as_of,
        # PROVENANCE: True = live fetch, False = injected replay/mock fixture.
        # The snapshot always carried this; the MIO dropped it, so a --mock report was
        # indistinguishable from a live one all the way to the reader. Propagate it so
        # the reporter can banner it.
        "live": s.get("live"),
        "graph_version": "engine_market_scan",
        "regime_version": f"{reg['ai_regime']}|{reg['observed_tone']}",
        "degraded": False,
        "event": {
            "event_id": f"evt_{(dom_key or 'session')}",
            "canonical_label": ev_label,
            "class": ev_class,
            "novelty": None,
            "member_items": [],
        },
        "theme": top_theme,
        "market_context": {
            "brent_price": brent,
            "oil_level_multiplier": oil_lvl["multiplier"],
            "oil_level_band": oil_lvl["band"],
            "oil_pct_move": oil_move,
            "note": ("Oil impact is level-scaled: the same % move has a larger effect at a "
                     "higher absolute Brent price (e.g. +2% at $100 > +2% at $70)."),
        },
        "regime": {
            "active": [reg["ai_regime"], reg["observed_tone"]],
            "primary": reg["observed_tone"],
            "confidence": round(agreement, 2),
        },
        "shock_type": core.shock_type(),
        "transmission": transmission,
        "affected_sectors": aff_sectors,
        "affected_companies": aff_companies,
        "expected_direction": expected_direction,
        "impact": impact,
        "validation": val_out,
        "confidence": confidence,
        "driver_dominance": driver_dominance,
        "provenance": {
            "pipeline_run": f"mcp_{stamp}",
            "core_version": "market_scan.py (NiftyOptions news engine)",
        },
    }
    # drop the None novelty so it doesn't trip the number-typed schema field
    if mio["event"]["novelty"] is None:
        mio["event"].pop("novelty")
    return mio


# ---------------------------------------------------------------------------
def validate_mio(mio: dict) -> dict:
    """Validate an MIO against schemas/mio.schema.json. Returns {valid, errors}."""
    try:
        import jsonschema  # optional dependency
    except Exception:
        return {"valid": None, "errors": ["jsonschema not installed — validation skipped"]}
    schema = json.loads(_SCHEMA_PATH.read_text())
    validator = jsonschema.Draft7Validator(schema)
    errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(mio)]
    return {"valid": not errors, "errors": errors}
