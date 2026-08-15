"""
extra_validations.py — validate NEW driver→proxy relationships against the tape.

The sector factor library (§5) SCORES these relationships ("what should the sector do given
the drivers"). This module VALIDATES them (§7): did the specific proxy stocks actually move as
the relationship predicts today? It mirrors the engine's build_cause_effect_scorecard for a
set of relationships the engine doesn't carry, resolving proxies from the gazetteer.

Adds the review's "biggest missing" links:
  * India rate stress  → banks / NBFC / realty (rate-sensitives)
  * AI infrastructure (SOX) → EMS / power / telecom
  * Weak rupee         → metals & import-reliant names
  * AI substitution    → IT services (regime-conditioned)
"""
from __future__ import annotations

import math

import common


def _num(x):
    """Real finite number or None — filters None, bools and NaN/inf (yfinance returns NaN)."""
    try:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        return x if math.isfinite(x) else None
    except Exception:
        return None

# (name, driver_key, expected_sign_when_driver_UP, gazetteer sector hints, activation threshold, level)
# NOTE: AI-infra is SPLIT into EMS / Power / Telecom — each has a DIFFERENT transmission channel
#       (chip-cycle vs utility rate-base vs tariff/spectrum), so they must be validated separately.
#       Weak-rupee→metals was REMOVED: the rupee helps exporters and hurts importers, and Indian
#       steel is driven by both — the sign isn't clean enough to validate as a single relationship.
NEW_RELATIONSHIPS = [
    ("India rate stress → rate-sensitives (banks/NBFC/realty)", "rate_stress", -1,
     ["bank", "financ", "nbfc", "gold financ", "realty"], 0.5, "Sector"),
    ("AI infrastructure (SOX) → EMS / electronics", "sox_pct", +1,
     ["ems", "electronic", "semiconductor"], 1.0, "Sector"),
    # Power & Telecom are MULTI-YEAR structural AI-infra themes, NOT daily trading relationships —
    # Power Grid/NTPC/Bharti don't move because SOX fell today. Validate as structural context.
    ("AI infrastructure → Power (structural)", "sox_pct", +1,
     ["power", "electrical", "cable"], 1.0, "Structural"),
    ("AI infrastructure → Telecom (structural)", "sox_pct", +1,
     ["telecom"], 1.0, "Structural"),
    ("AI substitution → IT services (regime-gated)", "ai_substitution", -1,
     ["it"], 0.5, "Sector"),
]


def _driver_value(key, drivers, raw, ai_regime):
    if key == "rate_stress":
        cpi = 1.0 if drivers.get("india_cpi_hot") else 0.0
        y = drivers.get("us10y_pct") or 0.0
        return round(0.6 * cpi + 0.4 * max(0.0, y), 3)          # positive = tighter
    if key == "usdinr":
        return raw.get("usdinr")
    if key == "ai_substitution":
        return 1.0 if ai_regime == "Substitution" else (-1.0 if ai_regime == "Complement" else 0.0)
    return drivers.get(key)


def build(core, snap, drivers, raw, ai_regime) -> list[dict]:
    # symbol/name → today's %
    pct = {}
    for k in ("quotes_idx", "quotes_stk", "it_quotes", "sector_quotes", "theme_quotes", "univ_quotes"):
        for q in snap.get(k, []) or []:
            mv = _num(q.get("pct_change"))
            if mv is not None:
                if q.get("symbol"):
                    pct[q["symbol"]] = mv
                pct[(q.get("name") or "").lower()] = mv
    gaz = getattr(core.ms, "COMPANY_GAZETTEER", [])

    out = []
    for name, key, exp_up, hints, thr, level in NEW_RELATIONSHIPS:
        v = _driver_value(key, drivers, raw, ai_regime)
        if v is None:
            continue
        sgn = 1 if v > 0 else -1 if v < 0 else 0
        active = abs(v) >= thr
        expected = exp_up * sgn                                   # expected proxy direction today
        held, broke, seen = [], [], set()
        for kw, disp, sym, sec in gaz:
            if not any(h in sec.lower() for h in hints) or disp in seen:
                continue
            mv = pct.get(sym, pct.get(disp.lower()))
            if mv is None:
                continue
            seen.add(disp)
            ok = (mv == 0) or ((mv > 0) == (expected > 0))
            (held if ok else broke).append({"name": disp, "pct": mv})
            if len(held) + len(broke) >= 6:
                break
        if not held and not broke:
            continue
        status = "CONFIRMED" if (held and not broke) else "OVERRIDDEN" if (broke and not held) else "WEAKENED"
        out.append({
            "edge": f"Economic relationship — {name}",
            "level": level,
            "expected_sign": 1 if expected > 0 else -1,
            "observed_sign": 1 if len(held) >= len(broke) else -1,
            "status": status if active else "WEAKENED",
            "held": held, "broke": broke,
            "driver_active": active, "driver_value": v,
            "source": "overlay/extra_validations (new relationship, PRIOR)",
        })
    return out


# STEEL AND BASE METALS ARE SIBLINGS, NOT PARENT AND CHILD.
# ---------------------------------------------------------------------------
# This function used to drive EVERY Indian metals name off ONE blended composite
# (base_metals 0.4 + steel_complex 0.6). So a copper/aluminium/zinc rally set the
# expected direction for Tata Steel — and when Tata Steel didn't follow, the engine
# reported a confident "Overridden". That is a FALSE OVERRIDE: nothing in a copper
# move implies anything about steel, so there was no expectation to break.
#
# Steel is driven by iron ore, coking coal, HRC/China rebar prices and Chinese
# construction. Copper/aluminium/zinc/nickel/lead are a different cycle with different
# beneficiaries. metals_sentiment already separates them into `bucket_avgs`
# (base_metals vs steel_complex) — that split just wasn't used.
#
# Each stock is now judged against ITS OWN bucket, and when the relevant bucket has no
# signal the relationship is marked INACTIVE rather than validated. No expectation, no
# override.
# Metals family now DERIVES from the canonical taxonomy — the same steel/base_metals
# split that caused the false-override bug is defined ONCE in newsindex/taxonomy.py, so
# this consumer can never disagree with §8 or the sector model about which bucket a name
# is in. Keyword fallback retained only for names the taxonomy hasn't classified.
_STEEL_FALLBACK = ("tata steel", "jsw", "sail", "jindal", "steel")
_BASE_FALLBACK = ("hindalco", "nalco", "national aluminium", "hindustan copper",
                  "hindustan zinc", "vedanta", "alumin", "copper", "zinc")

try:
    import sys as _sys
    from pathlib import Path as _Path
    _shared = _Path(__file__).resolve().parents[2]
    if str(_shared) not in _sys.path:
        _sys.path.insert(0, str(_shared))
    import taxonomy as _TAX
except Exception:
    _TAX = None


def _metal_family(disp: str, sector: str) -> str | None:
    """Which metals cycle drives this name? steel_complex | base_metals | None.
    Canonical taxonomy first; keyword fallback for anything it hasn't classified."""
    if _TAX is not None:
        sym = _TAX.resolve(disp)
        if sym:
            b = _TAX.bucket_of(sym)
            if b == "steel":
                return "steel_complex"
            if b == "base_metals":
                return "base_metals"
    blob = f"{disp} {sector}".lower()
    if any(w in blob for w in _STEEL_FALLBACK):
        return "steel_complex"
    if any(w in blob for w in _BASE_FALLBACK):
        return "base_metals"
    return None


def global_metals_cycle(core, snap, metals) -> dict | None:
    """Industrial-metals cycle → Indian metals producers, SPLIT BY FAMILY (PRIOR).

    Metals analysts don't ask "what did copper do" — they ask whether the global
    industrial cycle is strengthening. But that cycle reaches STEEL and BASE METALS
    through different inputs, so the two are validated against different buckets:

        steel_complex (iron ore, coking coal, HRC, China rebar) → Tata Steel, JSW, SAIL
        base_metals   (copper, aluminium, zinc, nickel, lead)   → Hindalco, NALCO, HZL

    Tagged PRIOR: a REDEFINED hypothesis, so the old Copper→Tata Steel calibration
    (~59%) does not transfer.
    """
    if not metals:
        return None
    news = metals.get("news") or {}
    news_score = news.get("score", 0) or 0
    buckets = metals.get("bucket_avgs") or {}
    comp = metals.get("composite")
    if comp is None and not news and not buckets:
        return None

    # per-family cycle signal: that family's own tape + the (shared) news read
    # A family is validated ONLY if it has a real directional reading of its own.
    # Two traps this closes:
    #   * an empty-but-truthy news dict made `not news` False, so a family with NO tape
    #     got cycle 0.0 — and sign 0 passes every check, marking steel CONFIRMED on no
    #     data at all. Confirmed-by-default is as wrong as the false override.
    #   * a genuinely flat cycle (sign 0) has no direction to confirm or break either.
    fam_cycle, fam_sgn = {}, {}
    _news_usable = bool(news) and news.get("n_items", 0) >= 2 and abs(float(news_score)) > 0.05
    for fam in ("steel_complex", "base_metals"):
        tape = buckets.get(fam)
        if tape is None and not _news_usable:
            continue                                  # no reading for this family
        c = (tape if tape is not None else 0.0) + float(news_score)
        s = 1 if c > 0.05 else -1 if c < -0.05 else 0
        if s == 0:
            continue                                  # flat ⇒ no expectation to test
        fam_cycle[fam] = round(c, 3)
        fam_sgn[fam] = s

    if not fam_cycle:
        return None
    cycle = (comp if comp is not None else 0.0) + float(news_score)
    active = any(abs(v) >= 0.2 for v in fam_cycle.values()) or \
        (news and news.get("n_items", 0) >= 2)

    # resolve the metals SECTOR from the gazetteer
    pct = {}
    for k in ("quotes_stk", "sector_quotes", "theme_quotes", "univ_quotes"):
        for q in snap.get(k, []) or []:
            mv = _num(q.get("pct_change"))
            if mv is not None:
                if q.get("symbol"):
                    pct[q["symbol"]] = mv
                pct[(q.get("name") or "").lower()] = mv
    gaz = getattr(core.ms, "COMPANY_GAZETTEER", [])
    held, broke, seen, unmapped = [], [], set(), []
    for kw, disp, sym, sec in gaz:
        if "metal" not in sec.lower() and not any(w in (disp + " " + sec).lower()
                                                  for w in ("steel", "alumin", "zinc", "hindalco", "vedanta")):
            continue
        if disp in seen:
            continue
        mv = pct.get(sym, pct.get(disp.lower()))
        if mv is None:
            continue
        fam = _metal_family(disp, sec)
        # No family, or that family's cycle has no reading today ⇒ NO EXPECTATION.
        # Previously such a name was still scored against the blended composite and
        # could be filed as "broke", manufacturing an override out of nothing.
        if fam is None or fam not in fam_sgn:
            unmapped.append({"name": disp, "pct": mv,
                             "why": "no family cycle signal — not validated"})
            seen.add(disp)
            continue
        seen.add(disp)
        s = fam_sgn[fam]
        ok = (mv == 0) or (s == 0) or ((mv > 0) == (s > 0))
        (held if ok else broke).append({"name": disp, "pct": mv, "family": fam,
                                        "family_cycle": fam_cycle[fam]})
        if len(held) + len(broke) >= 8:
            break
    if not held and not broke:
        # Nothing had a valid expectation — report that, don't invent an override.
        # expected_sign 0 is the schema's own encoding of "no direction expected" —
        # exactly the state here. driver_active=False makes validation_states render
        # it as ⏸️ Inactive rather than a confirmed/broken verdict. Status must be one
        # of the schema's three, so WEAKENED + inactive is the honest pairing:
        # nothing was validated, and nothing is claimed.
        return {"edge": "Industrial metals cycle → Indian metals producers (by family)",
                "level": "Sector", "status": "WEAKENED", "driver_active": False,
                "expected_sign": 0, "observed_sign": 0,
                "held": [], "broke": [], "unmapped": unmapped,
                "reason": "no family-level cycle signal today — expectation undefined, "
                          "so the relationship was not validated (an undefined "
                          "expectation cannot be 'overridden')",
                "source": "overlay/extra_validations metals-by-family (PRIOR)"}
    status = "CONFIRMED" if (held and not broke) else "OVERRIDDEN" if (broke and not held) else "WEAKENED"

    override_ranking = [
        ["China property expectations", "★★★★★"],
        ["Domestic steel pricing", "★★★★★"],
        ["China PMI / industrial activity", "★★★★"],
        ["Iron ore", "★★★"],
        ["Coking coal", "★★★"],
        ["Company-specific positioning / earnings", "★★"],
    ]
    return {
        # Renamed: the old label asserted a copper→steel link that does not exist.
        "edge": "Industrial metals cycle → Indian metals producers (by family)",
        "level": "Sector",
        "families": {f: {"cycle": fam_cycle[f], "sign": fam_sgn[f]} for f in fam_cycle},
        "unmapped": unmapped,
        "family_note": ("steel_complex (iron ore · coking coal · HRC · China rebar) drives "
                        "Tata Steel/JSW/SAIL; base_metals (copper · aluminium · zinc · nickel · "
                        "lead) drives Hindalco/NALCO/HZL. They are SIBLING cycles — a copper "
                        "rally implies nothing for steel, so each is validated separately."),
        "expected_sign": 1 if (fam_sgn.get("steel_complex", fam_sgn.get("base_metals", 0)) >= 0) else -1,
        "observed_sign": 1 if len(held) >= len(broke) else -1,
        "status": status if active else "WEAKENED",
        "held": held, "broke": broke,
        "driver_active": active,
        "override_ranking": override_ranking,
        "reason": ("Indian steel producers are driven primarily by domestic steel prices, China "
                   "property/infrastructure activity, iron ore and coking coal. Copper/aluminium are "
                   "useful GLOBAL manufacturing-cycle proxies but only indirect signals for Indian steel."),
        "metals_basket": metals,
        "source": "overlay/extra_validations global-metals-cycle (PRIOR — hypothesis redefined, recalibration pending)",
    }


def heavyweight_nifty(core, snap) -> dict | None:
    """Index-level relationship: did the top-weight names DRIVE today's Nifty move?

    For a Nifty trader this is often more actionable than half the macro relationships. We weight
    each tracked constituent's move by its Nifty weight, rank by |contribution|, and check whether
    the top handful account for the majority of the weighted move AND point the same way as the
    index. Contribution is over TRACKED constituents (not all 50), and labelled as such.
    """
    W = getattr(core.ms, "NIFTY50_WEIGHTS", {}) or {}
    nifty = None
    for q in snap.get("quotes_idx", []) or []:
        if "nifty 50" in (q.get("name", "") or "").lower():
            nifty = _num(q.get("pct_change"))
            break
    if nifty is None or not W:
        return None
    contribs, seen = [], set()
    for k in ("quotes_stk", "it_quotes", "sector_quotes", "theme_quotes", "univ_quotes"):
        for q in snap.get(k, []) or []:
            sym = (q.get("symbol") or "").replace(".NS", "").strip()
            pct = _num(q.get("pct_change"))
            w = W.get(sym)
            if not sym or pct is None or not w or sym in seen:
                continue
            seen.add(sym)
            contribs.append((q.get("name") or sym, w, pct, w * pct))
    if not contribs:
        return None
    total = sum(c[3] for c in contribs)
    if abs(total) < 1e-9:
        return None
    contribs.sort(key=lambda c: -abs(c[3]))
    top = contribs[:3]
    top_sum = sum(c[3] for c in top)
    share = round(100 * abs(top_sum) / abs(total))
    aligned = ((top_sum > 0) == (nifty > 0)) if nifty != 0 else True
    leaders = [{"name": c[0], "pct": c[2]} for c in top]
    status = "CONFIRMED" if (aligned and share >= 50) else "WEAKENED"
    return {
        "edge": "Heavyweight leadership → Nifty direction",
        "level": "Index",
        "expected_sign": 1 if nifty >= 0 else -1,
        "observed_sign": 1 if top_sum >= 0 else -1,
        "status": status,
        "held": leaders if aligned else [],
        "broke": [] if aligned else leaders,
        "contribution_pct": share,
        # Export the RAW DECOMPOSITION, not just the ratio. "127% of the net weighted
        # move" forces the reader to reverse-engineer what happened; the two numbers
        # that produce it say it directly: leaders pulled one way, everyone else pushed
        # back, and the net is what survived.
        "top_contribution": round(top_sum, 3),
        "rest_contribution": round(total - top_sum, 3),
        "net_contribution": round(total, 3),
        "nifty_pct": nifty,
        "n_tracked": len(contribs),
        "source": "overlay/extra_validations heavyweight (PRIOR)",
    }
