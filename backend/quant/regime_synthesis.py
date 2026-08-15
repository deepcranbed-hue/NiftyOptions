"""
regime_synthesis.py
-------------------
Additive synthesis on top of EXISTING modules — does NOT touch index_attribution,
global_cues, or market_regime. Adds three interpretation pieces:

  1. classify_breadth()  — index_attribution's breadth + attribution -> ROTATION
     vs DECLINE vs narrow-rally. The index level can LIE (heavyweight-dragged).
  2. semi_transmission() — the ^SOX cue (already in global_cues) -> India IT read,
     flagged ROTATIONAL vs STRUCTURAL.
  3. detect_republished() — republish/re-date sanity check for the news layer.

Reads existing module OUTPUTS; re-implements nothing.
"""
from __future__ import annotations
import re


def classify_breadth(attr: dict, heavyweight_contrib: float | None = None) -> dict:
    """attr = index_attribution.attribute_index_move() output (needs breadth_up,
    breadth_down, index_move/index_return)."""
    up = attr.get("breadth_up", 0); down = attr.get("breadth_down", 0)
    total = (up + down) or 1
    up_share = up / total
    idx = attr.get("index_move", attr.get("index_return", 0.0))
    idx_down, idx_up = idx < -0.05, idx > 0.05
    breadth_up, breadth_down = up_share >= 0.55, up_share <= 0.45

    if idx_down and breadth_up:
        regime, read = "ROTATION_DOWN_INDEX", (
            "Index DOWN but breadth POSITIVE — a ROTATION, not a selloff. The fall "
            "is heavyweight-dragged (a few high-weight names down) while most "
            "stocks rise. Read: NEUTRAL/reallocating, NOT defensive. Index "
            "understates health.")
    elif idx_up and breadth_down:
        regime, read = "NARROW_RALLY", (
            "Index UP but breadth NEGATIVE — narrow, heavyweight-led rally; most "
            "stocks falling. Fragile/top-heavy — index OVERSTATES health.")
    elif idx_down and breadth_down:
        regime, read = "BROAD_SELLOFF", (
            "Index DOWN and breadth NEGATIVE — genuine broad decline. Risk-off; "
            "defensive posture warranted.")
    elif idx_up and breadth_up:
        regime, read = "BROAD_RALLY", "Index UP and breadth POSITIVE — healthy broad advance."
    else:
        regime, read = "MIXED", "Flat / balanced — no dominant regime; stock-specific."

    return {"regime": regime, "read": read, "breadth_up": up, "breadth_down": down,
            "up_share": round(up_share, 2), "index_move": idx,
            "heavyweight_contrib": heavyweight_contrib,
            "caveat": "Breadth is a COUNT, not weighted — pair with the cap-weighted "
                      "attribution for the full picture."}


def semi_transmission(sox_pct: float, *, persistent: bool | None = None) -> dict:
    """sox_pct = the ^SOX % move already in global_cues. persistent=True only if
    the chip move has held multiple sessions."""
    if abs(sox_pct) < 0.8:
        return {"signal": "neutral", "structural": False,
                "india_read": "Chips ~flat — no IT transmission."}
    direction = "down" if sox_pct < 0 else "up"
    structural = bool(persistent)
    sev = "STRUCTURAL" if structural else "ROTATIONAL"
    if direction == "down":
        india = (f"Chips {sox_pct:+.1f}% ({sev}). India: IT-sector pressure "
                 f"(TCS/Infy track global tech), mild risk-off. "
                 + ("Structural (multi-session AI-thesis doubt) → real IT downgrade risk."
                    if structural else
                    "One-day wobble → ROTATIONAL; AI-infra demand usually intact, chips "
                    "rebound. Don't over-read."))
    else:
        india = (f"Chips {sox_pct:+.1f}% ({sev}). India: IT-sector relief, risk-on. "
                 + ("Structural recovery supports IT." if structural else
                    "One-day bounce — confirm it holds."))
    return {"signal": f"chips_{direction}", "structural": structural, "india_read": india,
            "caveat": "^SOX is overnight (leads NIFTY open). One session is ROTATIONAL "
                      "until it persists — don't treat a wobble as a thesis break."}


def _norm(t): return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()
def _shingles(t, k=4):
    w = _norm(t).split()
    return {" ".join(w[i:i+k]) for i in range(max(0, len(w)-k+1))} or {_norm(t)}


def detect_republished(articles: list[dict], sim_threshold: float = 0.6) -> dict:
    """Flag near-duplicate 'fresh' articles (re-dated syndication). articles need
    title (+ body?). Returns clusters so the pipeline COUNTS each story once."""
    sh = [(_shingles(f"{a.get('title','')} {a.get('body','') or ''}"), i)
          for i, a in enumerate(articles)]
    clusters, used = [], set()
    for a in range(len(sh)):
        if a in used: continue
        s_a, i_a = sh[a]; group = [i_a]
        for b in range(a+1, len(sh)):
            if b in used: continue
            s_b, i_b = sh[b]
            inter = len(s_a & s_b); union = len(s_a | s_b) or 1
            if inter/union >= sim_threshold:
                group.append(i_b); used.add(b)
        if len(group) > 1:
            clusters.append(group); used.add(a)
    n_dupe = sum(len(g)-1 for g in clusters)
    return {"duplicate_clusters": clusters, "n_near_duplicate": n_dupe,
            "note": (f"{len(clusters)} clusters of near-identical articles ({n_dupe} "
                     f"redundant). COUNT each story ONCE — re-dated syndication "
                     f"shouldn't inflate corroboration." if clusters
                     else "No near-duplicate clusters."),
            "warning": ("⚠ Several 'fresh' articles near-identical — possible re-dated "
                        "syndication; verify the event is actually current."
                        if n_dupe >= 3 else "")}


if __name__ == "__main__":
    print("ROTATION (today-like: index -0.4%, 33 up / 17 down):")
    print(" ", classify_breadth({"breadth_up":33,"breadth_down":17,"index_move":-0.4})["regime"])
    print(" ", classify_breadth({"breadth_up":33,"breadth_down":17,"index_move":-0.4})["read"])
    print("\nBROAD SELLOFF (index -1.2%, 12 up / 38 down):")
    print(" ", classify_breadth({"breadth_up":12,"breadth_down":38,"index_move":-1.2})["regime"])
    print("\nSEMI (chips +3.1% today, one session):")
    print(" ", semi_transmission(3.1, persistent=False)["india_read"])
    print("\nREPUBLISH:")
    arts=[{"title":"Semiconductor stocks crash 1.3T in AI selloff"},
          {"title":"Semiconductor stocks crash 1.3 trillion in AI sell off"},
          {"title":"Nifty opens higher led by realty and banks"}]
    print(" ", detect_republished(arts)["note"])
