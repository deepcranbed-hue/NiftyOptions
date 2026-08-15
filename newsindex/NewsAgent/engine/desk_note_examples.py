"""
desk_note_examples.py
---------------------
"Prompt distillation" assets — a TEACHER model (Claude) wrote these gold-standard
Indian-markets desk notes and the scoring rubric. market_scan.py injects a couple
of these as FEW-SHOT examples so the small local model imitates the structure and
rigour without any fine-tuning. eval_notes.py uses the RUBRIC to score models.

Edit / add examples to steer the house style. Keep them tight (4-5 sentences),
India-first, with a named mover + its index weight, real driver numbers, and NO
unverified RBI figures.
"""

from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# FEW-SHOT EXEMPLARS  (input context  ->  gold desk note)
# ---------------------------------------------------------------------------
FEWSHOT_EXAMPLES = [
    {
        "input": (
            "VERDICT: Risk-off. Nifty -0.60%, banks -1.19%, VIX +3.26%, oil +3.22%, "
            "FII -3,062cr, DII +2,172cr; 3 geopolitics, SOX -4.8%.\n"
            "MOVERS: Tech Mahindra +2.7% (~1.0% wt); Kalyan Jewellers +3.6% (small wt); "
            "HCL Tech -4.1% (~1.6% wt); HDFC Bank -0.9% (~13% wt).\n"
            "INDIA HEADLINES: HCL Tech Rs 3,500cr AI data-centre foray; India June "
            "inflation 4.38% beats forecast; Kalyan Jewellers +50% in 5 days."
        ),
        "gold": (
            "Indian markets are risk-off: Nifty -0.60% and Bank Nifty -1.19% lead the fall, "
            "with India VIX up 3.3% and Brent jumping 3.2% to a level that keeps inflation "
            "(June CPI 4.38%, above forecast) and the RBI in focus — a headwind for "
            "rate-sensitive banks. FII sold Rs 3,062cr in cash, only partly cushioned by DII "
            "buying of Rs 2,172cr. At the stock level HCL Tech fell ~4% on questions over its "
            "Rs 3,500cr AI data-centre spend, but its ~1.6% Nifty weight caps the index drag, "
            "while Tech Mahindra (+2.7%) and Kalyan Jewellers (+3.6%, not in Nifty) outperformed "
            "without moving the index. A weaker rupee is a mild offset for IT exporters; if the "
            "weakness persists watch for possible RBI smoothing, though today's data doesn't "
            "confirm any intervention."
        ),
    },
    {
        "input": (
            "VERDICT: Mildly bullish. Nifty +0.45%, banks +0.8%, VIX -6.0%, oil -1.5%, "
            "FII +1,850cr, DII +900cr; Kospi +1.2%.\n"
            "MOVERS: ICICI Bank +2.1% (~8.5% wt); Reliance +1.3% (~8.5% wt); "
            "Ola Electric +5% (small wt).\n"
            "INDIA HEADLINES: ICICI Bank Q1 profit beats; cooling oil eases inflation worry; "
            "FIIs turn net buyers."
        ),
        "gold": (
            "Indian markets are firmer, with Nifty +0.45% led by heavyweight financials — ICICI "
            "Bank (+2.1%, ~8.5% weight) did real index work after a Q1 profit beat, and Reliance "
            "(+1.3%) added to the lift. A 6% drop in India VIX and Brent easing 1.5% cool the "
            "inflation worry, supporting rate-sensitives, while FIIs turning net buyers (+Rs "
            "1,850cr) alongside DII inflows removed the recent flow overhang. Ola Electric's +5% "
            "grabbed headlines but its tiny index weight means it's a stock-story, not a Nifty "
            "driver. Firm global risk appetite (Kospi +1.2%) is a mild tailwind for IT sentiment."
        ),
    },
    {
        "input": (
            "VERDICT: Mixed. Nifty -0.05%, banks +0.2%, VIX +0.5%, oil +0.3%, "
            "FII -400cr, DII +600cr.\n"
            "MOVERS: Titan +2.4% (~1.3% wt); Infosys -1.1% (~5% wt); Tata Steel +1.8% (~1.2% wt).\n"
            "INDIA HEADLINES: Infosys slips ahead of results; gold at record lifts jewellers; "
            "steel firms on China stimulus hopes."
        ),
        "gold": (
            "Indian markets are flat-to-mixed, with Nifty little changed as gains in metals and "
            "jewellery offset weakness in IT. Infosys (-1.1%, ~5% weight) was the main index "
            "drag, drifting ahead of its results, while Tata Steel (+1.8%) firmed on China "
            "stimulus hopes and Titan (+2.4%) rode record gold prices. Flows were balanced — a "
            "small FII sale met by DII buying — and with oil and VIX barely moved, there was no "
            "strong macro steer either way. It's a stock-specific, results-driven tape rather "
            "than a directional one."
        ),
    },
]


def fewshot_block(n: int = 2) -> str:
    """Render the first n exemplars as INPUT->DESK NOTE pairs (for bigger models)."""
    parts = []
    for ex in FEWSHOT_EXAMPLES[:n]:
        parts.append("INPUT:\n" + ex["input"] + "\n\nDESK NOTE:\n" + ex["gold"])
    return "\n\n---\n\n".join(parts)


def fewshot_style(n: int = 1) -> str:
    """Gold PARAGRAPHS only (no input labels) — safer for small models that would
    otherwise copy the 'INPUT:/DESK NOTE:' scaffolding into their output."""
    return "\n\n".join(ex["gold"] for ex in FEWSHOT_EXAMPLES[:n])


# ---------------------------------------------------------------------------
# RUBRIC  — programmatic checks (each worth 1 point). ctx may supply 'movers'.
# ---------------------------------------------------------------------------
def _leads_with_india(note: str, ctx: dict) -> bool:
    head = note[:220].lower()
    return any(w in head for w in ("nifty", "sensex", "indian market", "bank nifty"))


def _names_a_mover(note: str, ctx: dict) -> bool:
    movers = [m.lower() for m in ctx.get("movers", [])]
    nl = note.lower()
    return any(m in nl for m in movers) if movers else bool(
        re.search(r"[A-Z][a-z]+.*[+\-]\s?\d", note))


def _weight_context(note: str, ctx: dict) -> bool:
    nl = note.lower()
    return ("weight" in nl or "% of nifty" in nl or "index weight" in nl
            or re.search(r"~?\d+(\.\d+)?%\s*(weight|wt|of nifty)", nl) is not None
            or "not in nifty" in nl or "index driver" in nl or "stock-story" in nl
            or "stock-specific" in nl or "stock-story" in nl)


def _cites_driver_number(note: str, ctx: dict) -> bool:
    nl = note.lower()
    return bool(re.search(r"(oil|brent|vix|fii|dii|nifty|rupee|cpi|inflation)\D{0,20}[+\-]?\d",
                          nl))


def _no_unverified_rbi_number(note: str, ctx: dict) -> bool:
    nl = note.lower()
    # fail if a $/bn figure sits near RBI / forward book / reserves
    if re.search(r"(rbi|forward book|forex reserves|reserves)", nl):
        if re.search(r"\$?\s?\d{2,3}\s*(bn|billion|\$bn)", nl):
            return False
    return True


def _length_ok(note: str, ctx: dict) -> bool:
    sents = [s for s in re.split(r"[.!?]\s", note.strip()) if len(s) > 15]
    return 3 <= len(sents) <= 7


def _no_advice(note: str, ctx: dict) -> bool:
    nl = note.lower()
    bad = ["should buy", "should sell", "i recommend", "you should", "buy the dip",
           "short the", "go long", "go short", "target price"]
    return not any(b in nl for b in bad)


def _no_prompt_leakage(note: str, ctx: dict) -> bool:
    """Small models sometimes echo the input scaffolding into the output. Penalise it."""
    nl = note.lower()
    bad = ["verdict:", "standout movers:", "desk note:", "india headlines:",
           "computed read:", "global cues (context", "cross-asset ->"]
    return not any(b in nl for b in bad)


RUBRIC = [
    ("leads_with_india",       _leads_with_india),
    ("names_a_mover",          _names_a_mover),
    ("gives_weight_context",   _weight_context),
    ("cites_driver_number",    _cites_driver_number),
    ("no_unverified_rbi_num",  _no_unverified_rbi_number),
    ("length_3_7_sentences",   _length_ok),
    ("no_investment_advice",   _no_advice),
    ("no_prompt_leakage",      _no_prompt_leakage),
]


def score_note(note: str, ctx: dict | None = None) -> dict:
    ctx = ctx or {}
    results = {name: bool(fn(note, ctx)) for name, fn in RUBRIC}
    results["_total"] = sum(1 for v in results.values() if v is True)
    results["_max"] = len(RUBRIC)
    return results
