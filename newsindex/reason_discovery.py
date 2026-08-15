"""
reason_discovery.py — evidence-BACKED overrides: retrieve the reason, don't guess it.

CANONICAL SHARED HOME (newsindex/reason_discovery.py)
-----------------------------------------------------
Lifted out of NewsAgent/overlay/ so BOTH engines can use it. market_scan.py had its
own _override_analysis() doing this same job with hardcoded +/-2 heuristic scores,
no evidence and no confidence — the "why +2?" problem. This is the better
implementation; the heuristic one should be retired in its favour rather than
calibrated. NewsAgent/overlay/reason_discovery.py is now a thin shim re-exporting
this module, so existing `import reason_discovery` call sites keep working.

When a relationship breaks (proxies moved against the expected direction), we shouldn't hard-code or
let an LLM invent the "Why". Instead we RETRIEVE today's news for the specific proxy stocks, extract
the catalysts actually being reported (earnings beat, deal win, broker upgrade, loan growth, rupee…),
rank them by frequency × source-quality, and return evidence-backed override candidates with a
confidence score. The economic mechanism stays as the fallback when no news evidence is found.

Deterministic and offline-testable (works off the snapshot news the pipeline already fetched). An
optional targeted search (news_fetch.search_google_news) can widen coverage on a live run.

Confidence = evidence_quality × agreement × coverage
  quality   — mean source reputation of the supporting items
  agreement — share of catalyst hits that point to the top explanation
  coverage  — fraction of the broken proxies for which a catalyst was found
"""
from __future__ import annotations

import json
import re

import textutil

# (key, label, base_stars, direction: +1 bullish / -1 bearish / 0 neutral, keywords)
CATALYSTS = [
    # NOTE: matching here is plain substring (not word-boundary), so keywords are kept
    # as STEMS ("profit ris" catches rises/rise/rising). The list previously missed the
    # most common Indian formats entirely — "beats estimates" (plural verb), "profit
    # rises" (present tense; only "profit rose" was listed) and "PAT" (absent) — so a
    # TCS/Infosys results rally could not be discovered at all. Keep stems DIRECTIONAL:
    # a bare "net profit" would also match "net profit falls" and mislabel a miss.
    ("earnings_beat", "Earnings beat / strong results", 5, +1,
     ["beat estimate", "beats estimate", "beat street", "beats street", "above estimate",
      "better-than-expected", "better than expected", "strong results", "results beat",
      "topped estimates", "tops estimate", "earnings beat",
      "profit rose", "profit ris", "profit jump", "profit surge", "profit climb",
      "profit grew", "profit grow", "profit up", "profit soar",
      "pat ris", "pat jump", "pat grew", "pat at rs", "pat up",
      "revenue ris", "revenue grew", "revenue up", "revenue jump",
      "margin expansion", "record profit", "strong quarter", "beats forecast"]),
    ("deal_win", "Large deal / order win", 4, +1,
     ["large deal", "multi-year deal", "mega deal", "big deal", "order win", "bags order", "wins contract",
      "won a deal", "tcv", "new deal", "deal win", "landmark deal", "7-year deal", "multiyear"]),
    ("broker_upgrade", "Broker upgrade / target raise", 4, +1,
     ["upgrade", "raised target", "target price raise", "outperform", "buy rating", "reiterate buy",
      "price target hike", "brokerage bullish", "upgraded to buy"]),
    ("guidance_raise", "Guidance raised / upbeat outlook", 4, +1,
     ["raised guidance", "guidance up", "strong outlook", "upbeat guidance", "raised outlook", "upgraded guidance"]),
    ("loan_growth", "Loan / business-growth update", 4, +1,
     ["loan growth", "credit growth", "advances grew", "deposit growth", "healthy growth", "business update",
      "strong loan", "robust credit", "aum growth"]),
    ("buyback", "Buyback / dividend", 3, +1, ["buyback", "share buyback", "special dividend", "dividend hike"]),
    ("rupee", "Rupee tailwind", 2, +1, ["weak rupee", "rupee depreciat", "rupee fell", "rupee slips", "currency tailwind"]),
    ("results_miss", "Earnings miss / weak results", 5, -1,
     ["miss estimate", "misses estimate", "missed estimate", "below estimate", "results miss",
      "misses street", "weak results", "disappointing", "guidance cut", "lowered guidance",
      "warns", "warning", "profit fell", "profit fall", "profit drop", "profit declin",
      "profit slip", "profit slump", "profit plunge", "profit down", "profit tumble",
      "pat fell", "pat fall", "pat declin", "revenue fell", "revenue declin", "revenue down",
      "margin contract", "margin pressure", "weak quarter"]),
    ("downgrade", "Broker downgrade / target cut", 4, -1,
     ["downgrade", "cut target", "sell rating", "underperform", "downgraded to", "target cut"]),
    ("order_loss", "Deal loss / weak order book", 3, -1, ["lost deal", "weak order", "order miss", "deal loss", "furlough"]),
    ("global_cue", "Global / SOX / macro cue", 1, 0, ["sox", "semiconductor", "nasdaq", "us yields", "global cues", "wall street"]),
]

_HIGH_SRC = ["reuters", "economic times", "et markets", "moneycontrol", "bloomberg", "business standard",
             "livemint", "mint", "cnbc", "businessline", "financial express", "the hindu"]


def _age_hours(item: dict):
    """Article age in hours, or None if we can't tell. Handles RSS (RFC-822) and ISO."""
    raw = (item.get("published") or item.get("published_at")
           or item.get("date") or "").strip()
    if not raw:
        return None
    import datetime as _dt
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(raw)
    except Exception:
        try:
            d = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - d).total_seconds() / 3600.0


# A break is a TODAY event, so its explanation must be a TODAY article. Google News
# search happily returns a well-matching older story — e.g. a June "Accenture guidance
# cut sparks 6% IT selloff" piece surfacing for a query about today's -0.6% drift. It
# matches every keyword, so it was ranked as ★★★★★ evidence for an unrelated session.
# Nothing in discover() looked at the date. Anything older than this is now dropped.
MAX_EVIDENCE_AGE_H = 48.0


def _source_quality(n: dict) -> float:
    s = (str(n.get("source", "")) + " " + str(n.get("link", ""))).lower()
    return 0.9 if any(h in s for h in _HIGH_SRC) else 0.5


def _snippet(text: str, kws) -> str | None:
    for sent in textutil.sentences(text):
        low = sent.lower()
        if any(k in low for k in kws):
            s = sent.strip()
            return (s[:160] + "…") if len(s) > 160 else s
    return None


def discover(names: list[str], news: list[dict], observed_dir: int = 1) -> dict | None:
    """Find + rank the catalysts behind a broken relationship's proxy moves.

    names        — the proxy stocks that moved against expectation (need explaining)
    observed_dir — +1 if they moved UP, -1 if DOWN (only catalysts consistent with the move count)
    Returns {primary_override, confidence, candidates:[...], coverage} or None if no evidence.
    """
    names_l = [n.lower() for n in names if n]
    if not names_l or not news:
        return None
    disp = {nm.lower(): nm for nm in names if nm}      # lower -> original casing
    agg: dict[str, dict] = {}
    total_hits, covered = 0, set()
    stale_dropped = []
    for n in news:
        # AGE GATE — a stale article cannot explain today's move, however well it matches.
        age = _age_hours(n)
        if age is not None and age > MAX_EVIDENCE_AGE_H:
            stale_dropped.append(((n.get("title") or "")[:70], round(age / 24.0, 1)))
            continue
        t = textutil.news_text(n)
        matched = [nm for nm in names_l if nm in t]
        if not matched:
            continue
        q = _source_quality(n)
        for key, label, stars, direction, kws in CATALYSTS:
            if not any(k in t for k in kws):
                continue
            if observed_dir and direction and direction != observed_dir:
                continue                       # ignore catalysts that don't match the actual move
            a = agg.setdefault(key, {"label": label, "stars": stars, "count": 0,
                                     "quality": 0.0, "evidence": [], "sources": set(),
                                     "names": set()})
            a["count"] += 1
            a["quality"] += q
            # WHICH names this specific catalyst explains — previously we only kept a
            # global `covered` set, so a catalyst touching 1 of 3 broken names inherited
            # the coverage earned by OTHER catalysts and looked far better supported
            # than it was (e.g. ICICI's earnings "explaining" a DLF realty move).
            a["names"].update(matched)
            snip = _snippet(t, kws)
            if snip:
                a["evidence"].append(snip)
            src = str(n.get("source", "")).strip()
            if src:
                a["sources"].add(src)
            total_hits += 1
            covered.update(matched)
    if not agg:
        # Distinguish "nothing matched" from "everything that matched was stale" — the
        # second means a plausible-looking explanation was deliberately rejected, which
        # the report should be able to say rather than reporting a bare blank.
        if stale_dropped:
            return {"primary_override": None, "confidence": 0.0, "coverage": 0.0,
                    "candidates": [], "partial": False, "explains": [],
                    "unexplained": [disp.get(n, n) for n in names_l],
                    "stale_only": True, "stale_dropped": stale_dropped[:4],
                    "source": "reason_discovery — matches found but ALL older than "
                              f"{MAX_EVIDENCE_AGE_H:.0f}h; not used as evidence"}
        return None

    ranked = sorted(agg.items(),
                    key=lambda kv: -(kv[1]["stars"] * kv[1]["count"] * (kv[1]["quality"] / max(1, kv[1]["count"]))))
    top_key, top = ranked[0]
    quality = min(1.0, top["quality"] / max(1, top["count"]))
    agreement = top["count"] / max(1, total_hits)
    # Coverage is now the PRIMARY catalyst's own reach, not the union across all
    # catalysts. This is strictly stricter and lowers confidence on partial reasons.
    top_coverage = len(top["names"]) / max(1, len(names_l))
    any_coverage = len(covered) / max(1, len(names_l))
    confidence = round(quality * agreement * top_coverage, 2)

    explained = sorted(disp.get(n, n) for n in top["names"])
    unexplained = sorted(disp.get(n, n) for n in names_l if n not in top["names"])

    # per-name attribution: which catalyst (if any) accounts for each broken name
    attribution = {}
    for nm in names_l:
        hits = [a["label"] for _k, a in ranked if nm in a["names"]]
        attribution[disp.get(nm, nm)] = hits or None

    candidates = []
    for key, a in ranked[:5]:
        candidates.append({"catalyst": a["label"], "stars": "★" * a["stars"], "mentions": a["count"],
                           "evidence": textutil.dedupe(a["evidence"])[:2],
                           "sources": sorted(a["sources"])[:3],
                           "explains": sorted(disp.get(n, n) for n in a["names"])})
    return {"primary_override": top["label"], "confidence": confidence,
            "coverage": round(top_coverage, 2), "any_coverage": round(any_coverage, 2),
            "explains": explained, "unexplained": unexplained,
            "partial": bool(unexplained), "attribution": attribution,
            "candidates": candidates,
            "stale_dropped": stale_dropped[:4],
            "source": f"reason_discovery (articles < {MAX_EVIDENCE_AGE_H:.0f}h old)"}


# ---------------------------------------------------------------------------
# targeted search — actively FETCH "why did X move today" articles for the proxies
# ---------------------------------------------------------------------------
def build_queries(names: list[str], observed_dir: int, max_names: int = 3) -> list[str]:
    word = "rally surge gains why today" if observed_dir >= 0 else "fall drop slump why today"
    return [f'{n} share price {word}' for n in names[:max_names] if n]


def _key(n: dict) -> str:
    return (str(n.get("title", "")) or str(n.get("link", ""))).strip().lower()[:140]


def gather_news(names, observed_dir, search_fn, base_news=None, per_query: int = 6) -> list[dict]:
    """Merge the snapshot news with fresh targeted-search results for the proxy stocks."""
    pool = list(base_news or [])
    seen = {_key(n) for n in pool}
    for q in build_queries(names, observed_dir):
        try:
            for item in (search_fn(q, per_query) or []):
                k = _key(item)
                if k and k not in seen:
                    seen.add(k)
                    pool.append(item)
        except Exception:
            continue
    return pool


# ---------------------------------------------------------------------------
# LLM re-rank — retrieve deterministically, then let a model synthesise/re-order
# (deterministic order is a heuristic; the LLM adds judgement but may ONLY use the
#  supplied candidates + evidence — it can't invent a new reason)
# ---------------------------------------------------------------------------
def llm_rerank(client, edge: str, discovered: dict) -> dict | None:
    if client is None or getattr(client, "is_deterministic", lambda: True)():
        return None
    cands = discovered.get("candidates") or []
    if not cands:
        return None
    sysmsg = ("You rank the likely REASONS a market relationship broke today. Use ONLY the provided "
              "candidate catalysts and their evidence — do NOT invent new reasons. Pick the single most "
              "likely primary override and re-order the rest. Reply with STRICT JSON only.")
    user = ('JSON schema: {"primary":"<one candidate label>","ranked":['
            '{"catalyst":"...","stars":1-5,"why":"..."}],"rationale":"..."}\n'
            f"Relationship that broke: {edge}\n"
            f"Candidates (deterministic order + evidence):\n{json.dumps(cands, ensure_ascii=False)[:3500]}")
    try:
        turn = client.chat(sysmsg, [{"role": "user", "content": user}], [])
        txt = (getattr(turn, "text", "") or "").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def llm_rerank_all(client, mio: dict) -> int:
    """Re-rank every discovered override with the LLM. Returns how many rows were re-ranked."""
    if client is None or getattr(client, "is_deterministic", lambda: True)():
        return 0
    n = 0
    for v in mio.get("validation", []) or []:
        d = v.get("override_discovered")
        if not d:
            continue
        r = llm_rerank(client, v.get("edge", ""), d)
        if r and r.get("primary"):
            d["llm"] = r
            v["override"] = r["primary"]                 # LLM's synthesised primary
            note = (v.get("override_note") or "")
            v["override_note"] = (note + " · LLM-reranked").strip(" ·")
            n += 1
    return n
