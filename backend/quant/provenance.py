"""
provenance.py
-------------
Transparency layer. In a money context, every number must declare WHERE it came
from: real signal, or a fallback — and if a fallback, WHICH one and WHY. Nothing
degrades silently.

Usage: components emit Provenance records as they compute. The pipeline collects
them and `summarize()` produces a trust banner for the panel:

    "DATA QUALITY: DEGRADED — 2 of 5 components on fallback"
      • sentiment: KEYWORD_FALLBACK (Gemini call failed) — 8/20 articles
      • coverage : LOW_COVERAGE (31% < 35%) — heavyweights uncovered

Each Provenance carries a SOURCE quality so the UI can colour it:
  PRIMARY    — computed from real, fresh signal as designed.
  PARTIAL    — real signal but degraded (thin coverage, some articles fell back).
  FALLBACK   — produced by a backup heuristic, NOT the primary method.
  STALE      — reused persisted state older than its freshness budget.
  UNAVAILABLE— could not compute; no value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Quality(str, Enum):
    PRIMARY = "PRIMARY"
    PARTIAL = "PARTIAL"
    FALLBACK = "FALLBACK"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


# rank for picking the worst (drives the overall banner)
_RANK = {Quality.PRIMARY: 0, Quality.PARTIAL: 1, Quality.STALE: 2,
         Quality.FALLBACK: 3, Quality.UNAVAILABLE: 4}


@dataclass
class Provenance:
    component: str            # "sentiment", "coverage", "rnd", "complacency", ...
    quality: Quality
    method: str              # what actually produced it, e.g. "gemini" / "keyword_fallback"
    reason: str = ""         # WHY it degraded (the user-facing explanation)
    detail: dict = field(default_factory=dict)  # numbers: counts, ratios, as_of

    def to_dict(self):
        return {"component": self.component, "quality": self.quality.value,
                "method": self.method, "reason": self.reason, "detail": self.detail}


# ── helpers components call (so the language stays consistent) ────────────────
def primary(component, method, **detail):
    return Provenance(component, Quality.PRIMARY, method, "", detail)

def partial(component, method, reason, **detail):
    return Provenance(component, Quality.PARTIAL, method, reason, detail)

def fallback(component, method, reason, **detail):
    return Provenance(component, Quality.FALLBACK, method, reason, detail)

def stale(component, method, reason, **detail):
    return Provenance(component, Quality.STALE, method, reason, detail)

def unavailable(component, reason, **detail):
    return Provenance(component, Quality.UNAVAILABLE, "none", reason, detail)


# ── ready-made records for the KNOWN fallback points in the pipeline ─────────
def sentiment_provenance(articles: list[dict], llm_down: bool | None = None,
                         sector_weight=None) -> Provenance:
    """Inspect tagged articles: how many used the keyword fallback (provider=keyword_fallback)?

    `llm_down`: pass True if the LLM client itself failed/errored (total
    outage) so we flag RED regardless of counts. If None, inferred (all fell back).

    `sector_weight`: optional {sector: index_weight%} (sector_map.sector_weights()).
    If given, fallback severity is weighted by the INDEX WEIGHT the failed
    articles touch — a fallback on Banks/IT (heavyweights) is far more serious
    than the same COUNT on light sectors. Articles aren't equal; index weight is.
    """
    n = len(articles) or 1
    # Check if keyword fallback was used based on _provider
    fb_articles = [a for a in articles if a.get("_provider") == "keyword_fallback" or a.get("confidence", 1.0) <= 0.31]
    fb = len(fb_articles)
    frac_count = fb / n
    down = llm_down if llm_down is not None else (fb == n)

    if down:
        return fallback("sentiment", "keyword_fallback",
                        "⛔ LLM DOWN — ALL sentiment is keyword-heuristic, not "
                        "model-analysed. Treat sentiment/bias as UNRELIABLE until "
                        "the LLM is restored.",
                        articles=n, fallback_articles=fb, fallback_frac=1.0,
                        llm_down=True)
    if fb == 0:
        # Check active provider name for the primary reporting
        prim_prov = articles[0].get("_provider", "llm") if articles else "llm"
        return primary("sentiment", prim_prov, articles=n, fallback_articles=0,
                       llm_down=False)

    # ── weight severity by the index weight the FAILED articles touch ──
    weighted_note = ""
    fb_weight = None
    if sector_weight is not None:
        touched = set()
        for a in fb_articles:
            for s in (a.get("sectors_affected") or []):
                touched.add(s)
        fb_weight = round(sum(sector_weight.get(s, 0.0) for s in touched), 1)
        heavy = sorted(((sector_weight.get(s, 0.0), s) for s in touched),
                       reverse=True)[:3]
        heavy_str = ", ".join(f"{s} {w:.1f}%" for w, s in heavy if w > 0)
        weighted_note = (f" Failed articles touch ~{fb_weight}% of index weight"
                         + (f" (incl. {heavy_str})" if heavy_str else "") + ".")

    # escalate to FALLBACK if the failed articles hit heavyweights (>=15% weight),
    # even on a minority count; light-sector misses stay PARTIAL.
    heavy_hit = (fb_weight is not None and fb_weight >= 15.0)

    if frac_count >= 0.5 or heavy_hit:
        sev = "HEAVYWEIGHT sectors" if heavy_hit else "majority of articles"
        return fallback("sentiment", "keyword_fallback",
                        f"LLM unavailable for {fb}/{n} articles — fallback hit "
                        f"{sev}; sentiment there is keyword-heuristic, not "
                        f"model-analysed.{weighted_note}",
                        articles=n, fallback_articles=fb,
                        fallback_frac=round(frac_count, 2), fallback_weight=fb_weight,
                        llm_down=False)
    return partial("sentiment", "llm+keyword",
                   f"{fb}/{n} articles fell back to keyword heuristic; rest are "
                   f"LLM.{weighted_note} Failed articles are light-weight "
                   f"sectors — limited impact on the index read.",
                   articles=n, fallback_articles=fb, fallback_frac=round(frac_count, 2),
                   fallback_weight=fb_weight, llm_down=False)


def coverage_provenance(coverage: float, min_cov: float = 0.35) -> Provenance:
    if coverage >= min_cov:
        return primary("coverage", "weighted", coverage=round(coverage, 2))
    return partial("coverage", "weighted",
                   f"Only {coverage:.0%} of index weight carries sentiment "
                   f"(< {min_cov:.0%}) — heavyweight sectors likely uncovered; "
                   f"weighted lean is unreliable.",
                   coverage=round(coverage, 2), threshold=min_cov)


def rnd_provenance(has_put_leg: bool, straddle_ok: bool) -> Provenance:
    if has_put_leg and straddle_ok:
        return primary("rnd", "breeden_litzenberger_otm")
    if not has_put_leg:
        return fallback("rnd", "call_only",
                        "put_ltp missing — RND built from calls only; skew sign "
                        "unreliable. Pass put_ltp to fix.")
    return partial("rnd", "breeden_litzenberger_otm",
                   "RND move diverges from the ATM straddle yardstick — inputs "
                   "(days/strikes) may be off; treat density with caution.")


def complacency_provenance(warnings: list[str]) -> Provenance:
    if not warnings:
        return primary("complacency", "chain_components")
    return partial("complacency", "chain_components",
                   "; ".join(warnings), warnings=warnings)


def state_provenance(component: str, age_seconds: float, budget_seconds: float,
                     as_of: str) -> Provenance:
    """For the decoupled JSON-state path: is the reused slow-layer state fresh?"""
    if age_seconds <= budget_seconds:
        return primary(component, "persisted_state", as_of=as_of,
                       age_s=int(age_seconds))
    return stale(component, "persisted_state",
                 f"Reusing {component} state from {as_of} — older than its "
                 f"{int(budget_seconds)}s freshness budget; refresh to update.",
                 as_of=as_of, age_s=int(age_seconds))


# ── aggregate into one trust banner ──────────────────────────────────────────
def summarize(records: list[Provenance]) -> dict:
    if not records:
        return {"overall": Quality.UNAVAILABLE.value, "headline": "no provenance",
                "records": []}
    worst = max(records, key=lambda r: _RANK[r.quality]).quality
    degraded = [r for r in records if r.quality != Quality.PRIMARY]
    if worst == Quality.PRIMARY:
        headline = "ALL PRIMARY — every output computed from real, fresh signal."
    else:
        n_fb = sum(1 for r in records if r.quality == Quality.FALLBACK)
        n_deg = len(degraded)
        label = {Quality.PARTIAL: "DEGRADED", Quality.STALE: "PARTLY STALE",
                 Quality.FALLBACK: "FALLBACK ACTIVE",
                 Quality.UNAVAILABLE: "INCOMPLETE"}[worst]
        headline = (f"DATA QUALITY: {label} — {n_deg} of {len(records)} components "
                    f"degraded" + (f", {n_fb} on FALLBACK" if n_fb else "") + ".")
    return {
        "overall": worst.value,
        "headline": headline,
        "degraded": [r.to_dict() for r in degraded],   # what to show prominently
        "records": [r.to_dict() for r in records],      # full list for the audit view
    }


if __name__ == "__main__":
    import json
    # a realistic degraded cycle: sentiment partly fell back, coverage thin,
    # RND fine, complacency fine, flows state stale.
    arts = [{"confidence": 0.3}] * 8 + [{"confidence": 0.8}] * 12
    recs = [
        sentiment_provenance(arts),
        coverage_provenance(0.31),
        rnd_provenance(has_put_leg=True, straddle_ok=True),
        complacency_provenance([]),
        state_provenance("flows", age_seconds=90000, budget_seconds=86400,
                         as_of="2026-06-26T18:00:00+05:30"),
    ]
    print(json.dumps(summarize(recs), indent=2))
