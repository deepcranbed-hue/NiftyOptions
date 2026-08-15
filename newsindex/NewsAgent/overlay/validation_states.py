"""
validation_states.py — richer relationship-validation states, LEVELS + economic overrides.

Replaces the binary "Supported / Dominated by stronger drivers" with a 5-state taxonomy and,
crucially, WHY a relationship failed and WHO overrode it — the dominant override, not just "broke".

It also organises relationships the way investment decisions are actually made:

    macro drives sectors → sectors drive companies

so every relationship carries a LEVEL (Index / Macro / Sector / Company). The report groups by
level, which tells the reader immediately *which level of the market* each relationship lives at.

States:
    ✅ Confirmed            — price action matched (every proxy agreed)
    ⚠️ Partially Confirmed  — mixed: some proxies agreed, others didn't
    🔄 Overridden           — a stronger driver (earnings/policy/liquidity) dominated
    ⏸️ Inactive             — the trigger was absent/too weak to expect the relationship
    ❓ Inconclusive          — evidence insufficient / no proxies with data

Content fixes in this version (per desk review):
  * Copper → base-metals is RELABELLED "Global industrial metals (China-demand proxy) → Indian
    metals": Indian steel tracks steel prices / iron ore / coking coal / China property far more
    than copper — copper is only a broad global-activity proxy.
  * SOX → IT "Why" no longer carries the regime's directional call ("Bearish near-term"); that
    belongs in the AI-regime read. Validation only explains why the RELATIONSHIP behaved as it did.
  * Oil → OMC "Why" names GRM (gross refining margin), the term energy analysts actually use.
  * Every Overridden / Partial row now carries a PRIMARY OVERRIDE label (and, for the oil links,
    a ranked list of candidate explanations).
"""
from __future__ import annotations


def state_of(held, broke, driver_active=True):
    """Per-proxy state from held/broke."""
    h, b = len(held), len(broke)
    if not driver_active:
        return ("⏸️", "Inactive")
    if not h and not b:
        return ("❓", "Inconclusive")
    if h and not b:
        return ("✅", "Confirmed")
    if b and not h:
        return ("🔄", "Overridden")
    return ("⚠️", "Partially Confirmed")


# --- LEVEL taxonomy (macro drives sectors drive companies) -----------------
_LEVEL_KW = [
    ("Index",   ["heavyweight"]),
    ("Macro",   ["us yield", "us10y", "yields → bank", "rising us yields", "fii flow", "fii →",
                 "capital flow", "it exporter", "rupee → it", "weak rupee → it", "usdinr", "dxy"]),
    ("Sector",  ["sox", "semis", "ai substitution", "ai infrastructure", "china", "industrial metals",
                 "copper", "rate stress", "rate-sensitive", "banks/nbfc", "→ ems", "→ power",
                 "→ telecom", "metals"]),
    ("Company", ["upstream", "downstream", "omc", "fuel consumer", "ice autos", "→ autos",
                 "defence", "producers vs users"]),
]


def _level_of(edge, given=None):
    if given:
        return given
    low = (edge or "").lower()
    for lvl, kws in _LEVEL_KW:
        if any(k in low for k in kws):
            return lvl
    return "Company"


# --- PRIMARY OVERRIDE (EVIDENCE-GATED) --------------------------------------
# There is ALWAYS a primary driver, and it IS being overridden — but on a single day's tape we
# usually cannot PROVE which driver did the overriding. So an override label is either:
#   • EVIDENCED  — we hold data that supports it (risk-on tape, semis cause read, DII sign, …), or
#   • CANDIDATE  — a structural mechanism we can NAME but not confirm today (e.g. GRM, govt policy).
# We NEVER assert per-stock flow attribution (e.g. "DII bought ICICI") — we don't have that data.

# structural mechanisms we can name but not prove on a single day (label, is_honest_structural)
#   'honest structural' = the label itself only claims idiosyncrasy/indirectness (stock-specific,
#   indirect, order-timing), which is always a fair statement, so it isn't flagged as unconfirmed.
_STRUCTURAL = [
    ("upstream", "Government policy / windfall tax", False),
    ("omc", "GRM / marketing margins", False),
    ("downstream", "GRM / marketing margins", False),
    ("industrial metals", "China demand / steel prices", False),
    ("china", "China demand / steel prices", False),
    ("copper", "China demand / steel prices", False),
    ("ai substitution", "Earnings optimism", False),
    ("it exporter", "AI-services pricing / global tech", False),
    ("rupee → it", "AI-services pricing / global tech", False),
    ("→ ems", "Order book / valuation", False),
    ("→ power", "Utility rate base (rate-driven)", False),
    ("→ telecom", "Tariff / spectrum", False),
    ("yield", "Indirect / mixed transmission", True),
    ("defence", "Order-timing / stock-specific", True),
    ("rate stress", "Stock-specific", True),
    ("rate-sensitive", "Stock-specific", True),
]


def _override_gated(low, context, semis):
    """Return (label, evidenced_bool, note_or_None) for an Overridden/Partial row."""
    c = context or {}

    # SOX → IT: the semis cause classifier is our evidence source
    if "indian it" in low or ("sox" in low and " it" in low) or ("semis" in low and " it" in low):
        cause = (semis or {}).get("primary_cause", "") or ""
        if "budget_rotation" in cause:
            conf = (semis or {}).get("confidence")
            note = f"semis cause read{f', conf {conf:.2f}' if isinstance(conf, (int, float)) else ''}"
            return ("Enterprise budget rotation", True, note)
        return ("Enterprise budget rotation", False, "candidate — semis cause not budget-rotation")

    # Oil → fuel consumers / ICE autos: a risk-on tape is directly observable
    if "fuel consumer" in low or "ice autos" in low or "→ autos" in low:
        if c.get("risk_on"):
            return ("Risk-on market", True, "tape is risk-on")
        return ("Company-specific demand", False, "candidate — tape not clearly risk-on")

    # FII: we hold AGGREGATE DII sign only — NEVER per-stock. Do not claim "DII bought X".
    if "fii" in low:
        dii = c.get("dii_net")
        if isinstance(dii, (int, float)) and dii > 0:
            return ("Broad-ownership spread / domestic bid", False,
                    f"DII net +₹{dii:,.0f}cr aggregate — NOT attributable per-stock")
        return ("Broad-ownership spread", False, "no per-stock flow data")

    for k, lbl, honest in _STRUCTURAL:
        if k in low:
            return (lbl, honest, None if honest else "structural — not confirmed by today's tape")
    return ("Stronger concurrent driver", False, "unspecified")


# ranked candidate explanations (institutional habit: rank the causes, don't just list them)
_OVERRIDE_RANK = {
    "upstream": [["Government policy / windfall tax", "★★★★★"], ["Profit-taking", "★★★"], ["Oil move", "★★"]],
    "omc": [["GRM (gross refining margin)", "★★★★★"], ["Marketing margins", "★★★"],
            ["Retail fuel-price policy", "★★★"], ["Crude input", "★★"]],
    "downstream": [["GRM (gross refining margin)", "★★★★★"], ["Marketing margins", "★★★"],
                   ["Retail fuel-price policy", "★★★"], ["Crude input", "★★"]],
}


def _override_ranking(low):
    for k, rank in _OVERRIDE_RANK.items():
        if k in low:
            return rank
    return None


# oil proxy → sub-relationship (each has its OWN mechanism, so they must not be grouped)
_OIL_SUB = [
    (["ongc", "oil india", "oil & natural", "natural gas"], "Oil → Upstream producers"),
    (["bpcl", "ioc", "hpcl", "indian oil", "hindustan petroleum", "bharat petroleum", "petronet", "gail", "igl", "mgl"],
     "Oil → Downstream OMCs"),
    (["indigo", "interglobe", "spicejet", "asian paint", "berger", "paint", "apollo tyre", "mrf",
      "ceat", "balkrishna", "tyre", "chemical", "srf", "pidilite", "upl", "aarti"], "Oil → Fuel consumers"),
]


def _oil_subgroup(name: str) -> str:
    n = (name or "").lower()
    for kws, label in _OIL_SUB:
        if any(k in n for k in kws):
            return label
    return "Oil → other users"


# economic override / partial reasons, keyed by relationship (mechanism only, no regime direction)
_REASON = {
    "Oil → Upstream producers": "govt pricing / windfall tax / profit-taking can outweigh the crude move — upstream is NOT a pure oil play.",
    "Oil → Downstream OMCs": "GRM, marketing margins and retail fuel-pricing policy dominated today's crude move — the crude-input link is the weak one.",
    "Oil → Fuel consumers": "broad risk-on / company-specific demand outweighed the fuel-cost headwind today.",
    "Oil → other users": "company-specific / demand factors outweighed the fuel-cost link.",
    "yields": "US10Y hits Indian banks INDIRECTLY (US yields → global capital flows → INR → India bond yield → banks), so pass-through is mixed, not 1:1.",
    "fii": "response spread across ALL high-foreign-ownership names (banks, IT, Reliance), not banks alone.",
    "autos": "strong domestic demand / earnings / EV mix outweighed the fuel-cost concern; PV vs 2W vs CV differ.",
    "defence": "company-specific (order timing, results) — border-security (BEL) and platforms (HAL) are different businesses.",
    "rupee_it": "IT export tailwind can be swamped by AI-services pricing pressure or global tech sentiment.",
    "rupee_pharma": "for pharma exporters the rupee tailwind is often outweighed by US generic price erosion, USFDA action (warning letters / import alerts) or China API cost — sector-specific factors, not the currency.",
    "china_metals": "Indian steel producers are driven primarily by domestic steel prices, China property/infrastructure activity, iron ore and coking coal — copper/aluminium are useful GLOBAL manufacturing-cycle proxies but only indirect signals for Indian steel.",
    "sox_it": "enterprise AI budget rotation supported IT services despite chip weakness — the SOX→IT link is cause-dependent, not automatic.",
    "ai_power": "power names are utility / rate-base driven; today's move is a rates story, not an AI-infrastructure signal.",
    "ai_telecom": "telecom tracks tariffs, spectrum and subscriber trends more than the AI-capex cycle.",
    "ai_ems": "EMS names moved on order-book / valuation, not the day's chip tape.",
    "rate_sensitive": "rate-sensitive response is stock-specific today — not every bank / NBFC / realty name moved with the rate signal.",
}


def _exp_arrow(v, held, broke):
    """Expected direction (↑/↓) for the RHS of the relationship.

    PREFER the declared economic sign. The fallbacks below infer 'expected' from what
    was OBSERVED, which is circular — it derives the economics from the pass/fail result
    instead of the other way round. That is only tolerable as a last resort, and rows
    that rely on it are tagged `expected_inferred` so a reader knows the arrow was not
    economically derived.
    """
    es = v.get("expected_sign")
    if es in (1, -1):
        return "↑" if es > 0 else "↓"
    # --- last-resort back-inference (flagged) ---------------------------------
    v["expected_inferred"] = True
    if held:
        p = held[0].get("pct", 0)
        return "↑" if p > 0 else "↓" if p < 0 else "→"
    if broke:                     # broke ⇒ observed is the OPPOSITE of expected
        p = broke[0].get("pct", 0)
        return "↓" if p > 0 else "↑" if p < 0 else "→"
    return "→"


def _is_sox_it(low: str) -> bool:
    return ("indian it" in low) or ("sox" in low and " it" in low) or ("semis" in low and " it" in low)


def _sox_it_read(semis, held, broke):
    """Direction-aware SOX→IT override. Returns (label, evidenced, note, reason).

    Key rule (per desk review): a fall in SOX has 5+ causes with DIFFERENT implications for Indian
    IT. Most are NOT 'AI demand weak'. Crucially, a cause that is BEARISH for Indian IT (budget
    rotation, productivity deflation, macro derating) CANNOT explain Indian IT going UP — so when IT
    rallies against the SOX signal, the override is idiosyncratic (results/rupee), not the chip cause.
    Only a demand-neutral cause (valuation reset / profit-taking) explains an IT decoupling to the
    upside. 'Evidenced' also requires real confidence, not a 0.15 read.
    """
    s = semis or {}
    cause = s.get("primary_cause", "") or ""
    label = s.get("cause_label") or (cause.replace("_", " ").strip().capitalize()) or "Cause-dependent"
    conf = s.get("confidence")
    it_exp = (s.get("indian_it_expected") or "").lower()
    it_supportive = ("bull" in it_exp) or it_exp.startswith("neutral")   # valuation-reset ⇒ IT can rise
    it_up = len(broke) > len(held)                                       # IT moved opposite to expected-down
    confident = isinstance(conf, (int, float)) and conf >= 0.40
    cstr = f", conf {conf:.2f}" if isinstance(conf, (int, float)) else ""

    if it_up and not it_supportive:
        # bearish/neutral-for-IT chip cause cannot explain an IT RALLY → it's idiosyncratic
        return ("Company results / rupee (idiosyncratic)", False, "not the chip cause",
                f"{label} is a near-term HEADWIND for Indian IT, so it does NOT explain today's IT "
                f"rally — IT rose on its own catalyst (results / rupee tailwind). SOX weakness and IT "
                f"strength are separate stories today.")
    if it_up and it_supportive:
        return (f"{label} — no demand change", confident, f"positioning/valuation{cstr}",
                f"SOX fell on {label.lower()} (positioning, not demand), so Indian IT decoupled to the "
                f"upside — the SOX→IT link is cause-dependent, not automatic.")
    # IT fell too → the link effectively held via a bearish chip cause
    return (label, confident, cstr.strip(", ") or None,
            f"chip weakness reads as {label.lower()} — consistent with Indian IT softness; note this "
            f"is a spending/allocation or valuation story, NOT 'AI demand weakening'.")


def _reason_for(low):
    """Economic 'Why' for an Overridden/Partial row (mechanism only, no regime direction)."""
    if "copper" in low or "industrial metals" in low or ("china" in low and "metal" in low):
        return _REASON["china_metals"]
    if "indian it" in low or ("sox" in low and " it" in low) or ("semis" in low and "it" in low):
        return _REASON["sox_it"]
    if "→ power" in low:
        return _REASON["ai_power"]
    if "→ telecom" in low:
        return _REASON["ai_telecom"]
    if "→ ems" in low:
        return _REASON["ai_ems"]
    if "rate-sensitive" in low or "rate stress" in low:
        return _REASON["rate_sensitive"]
    # pharma BEFORE the generic exporter branch — "pharma exporters" also contains
    # "exporter", so without this it fell through to the IT reason.
    if "rupee" in low and "pharma" in low:
        return _REASON["rupee_pharma"]
    if "rupee" in low and ("it exporter" in low or "indian it" in low):
        return _REASON["rupee_it"]
    if "yield" in low:
        return _REASON["yields"]
    if "fii" in low:
        return _REASON["fii"]
    if "auto" in low:
        return _REASON["autos"]
    if "defence" in low:
        return _REASON["defence"]
    return None


def _finish(v, held, broke, semis=None, context=None):
    """Attach state, level, override (evidence-gated), ranking, expected-arrow and reason."""
    edge = v.get("edge", "")
    low = edge.lower()
    emoji, st = state_of(held, broke, v.get("driver_active", True))
    row = {**v, "held": held, "broke": broke,
           "state": emoji, "state_label": st,
           "level": _level_of(edge, v.get("level")),
           "exp_dir": _exp_arrow(v, held, broke)}
    if st in ("Overridden", "Partially Confirmed"):
        if _is_sox_it(low):
            # SOX→IT needs the direction-aware, confidence-gated read (not a blanket 'budget rotation')
            lbl, evidenced, note, reason = _sox_it_read(semis, held, broke)
            row["override"] = lbl
            row["override_evidenced"] = evidenced
            if note:
                row["override_note"] = note
            row["reason_econ"] = reason
        else:
            lbl, evidenced, note = _override_gated(low, context, semis)
            row["override"] = lbl
            row["override_evidenced"] = evidenced
            if note:
                row["override_note"] = note
            rank = _override_ranking(low)
            if rank:
                row["override_ranking"] = rank
            row["reason_econ"] = _reason_for(low)
    return row


def transform(validation, semis=None, drivers=None, context=None):
    """Return a richer validation list: levels, states, splits, overrides, economic reasons."""
    drivers = drivers or {}
    out = []
    for v in validation or []:
        edge = v.get("edge", "")
        low = edge.lower()

        # (a) DROP Kospi → Indian IT — Kospi is a broad index (Samsung, Hyundai, banks…), not an
        #     AI proxy. Its AI signal belongs in a Global-AI-Infrastructure read, not here.
        if "kospi" in low:
            continue

        held, broke = v.get("held", []), v.get("broke", [])

        # (b) DROP the engine's single-proxy "Copper → base-metals (Tata Steel)" row: it is
        #     SUPERSEDED by the overlay's "Global Industrial Metals Cycle → Indian Steel Producers"
        #     (sector-wide, cycle-driven, China-centric override). Its ~59% calibration belonged to
        #     the OLD copper→metals hypothesis and does not transfer to the redefined relationship.
        if "copper" in low or ("base-metal" in low and "cycle" not in low):
            continue

        # (b2) FII hits ALL high-foreign-ownership names (banks, IT, Reliance), not "financials"
        if "fii" in low and ("financ" in low or "bank" in low):
            v = {**v, "edge": "FII flow → high-foreign-ownership stocks", "level": "Macro"}
            low = v["edge"].lower()

        # (c) SPLIT the grouped "Oil → producers vs users" into its 3 distinct mechanisms
        if "producers vs users" in low:
            groups: dict[str, dict] = {}
            for c in held:
                groups.setdefault(_oil_subgroup(c["name"]), {"held": [], "broke": []})["held"].append(c)
            for c in broke:
                groups.setdefault(_oil_subgroup(c["name"]), {"held": [], "broke": []})["broke"].append(c)
            # Each sub-relationship has its OWN economic sign, and they are OPPOSITE:
            # oil↑ lifts upstream realisations but squeezes OMC input cost and fuel
            # users. The parent groups all four proxies, so its expected_sign is
            # "mixed" → 0, and inheriting that zero made _exp_arrow() fall through to
            # back-inference ("it broke while rising, so expected must be ↓") — which
            # derives the ECONOMICS from the PASS/FAIL rather than the other way round.
            # Circular, and it only looked right by coincidence. Set the real sign here.
            _oil_move = (context or {}).get("oil_pct")
            _base = {"Oil → Upstream producers": +1,     # sells crude → higher realisation
                     "Oil → Downstream OMCs": -1,        # buys crude → input cost
                     "Oil → Fuel consumers": -1,         # burns fuel → cost push
                     "Oil → other users": -1}
            for label, hb in groups.items():
                sub = {**v, "edge": f"Economic relationship — {label}", "level": "Company"}
                b = _base.get(label)
                _held, _broke = hb["held"], hb["broke"]
                if b is not None and _oil_move not in (None, 0):
                    es = b * (1 if _oil_move > 0 else -1)
                    sub["expected_sign"] = es
                    sub["expected_basis"] = (
                        f"{'oil ↑' if _oil_move > 0 else 'oil ↓'} {_oil_move:+.2f}% × "
                        f"{'producer (+)' if b > 0 else 'consumer (−)'} exposure")
                    # RE-CLASSIFY against the sub-relationship's OWN sign.
                    # held/broke arrived from the PARENT, whose proxies have mixed signs,
                    # so a producer could be filed as "broke" using a consumer's expected
                    # direction. Correcting only the displayed arrow left the row
                    # self-contradictory — "Expected ↑ · ONGC +0.8% ✗" — which is exactly
                    # the nonsense a reader would (rightly) not believe.
                    _re_h, _re_b = [], []
                    for c in (_held + _broke):
                        p = c.get("pct")
                        if p is None or p == 0:
                            _re_h.append(c)
                            continue
                        (_re_h if (p > 0) == (es > 0) else _re_b).append(c)
                    _held, _broke = _re_h, _re_b
                row = _finish(sub, _held, _broke, semis, context)
                if row.get("state_label") in ("Overridden", "Partially Confirmed"):
                    row["reason_econ"] = _REASON.get(label, row.get("reason_econ"))
                out.append(row)
            continue

        out.append(_finish(v, held, broke, semis, context))

    # schema guard: any OVERRIDDEN row must carry `reason` (MIO schema requirement)
    for r in out:
        if r.get("status") == "OVERRIDDEN" and not r.get("reason"):
            r["reason"] = r.get("reason_econ") or "dominated by a stronger concurrent driver today."

    # order within the whole list: Overridden/Partial first (need attention), then Confirmed
    order = {"🔄": 0, "⚠️": 1, "❓": 2, "⏸️": 3, "✅": 4}
    out.sort(key=lambda r: order.get(r.get("state"), 5))
    return out
