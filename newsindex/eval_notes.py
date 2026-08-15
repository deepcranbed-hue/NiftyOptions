#!/usr/bin/env python3
"""
eval_notes.py
-------------
Objective scoreboard for the desk-note generator. Runs a set of fixed report
inputs through 2-3 LOCAL Ollama models (with the same few-shot prompt used in
production) and scores each output against the teacher-written RUBRIC in
desk_note_examples.py. Lets you pick the best model / prompt with NUMBERS
instead of eyeballing one day's output. No training, fully local.

Usage:
    python3 eval_notes.py
    python3 eval_notes.py --models llama3.2:3b qwen2.5:7b
    python3 eval_notes.py --no-fewshot     # measure the lift few-shot gives

Requires Ollama running (ollama serve) with the models pulled.
"""

from __future__ import annotations
import argparse
import requests

from desk_note_examples import fewshot_block, score_note, RUBRIC

OLLAMA_URL = "http://localhost:11434/api/generate"

# Held-out eval fixtures (NOT the few-shot examples — avoid teaching-to-the-test).
# Each: a report context + the mover names we expect the note to mention.
FIXTURES = [
    {
        "name": "risk-off / oil spike",
        "movers": ["HCL Tech", "Tech Mahindra", "HDFC Bank", "Kalyan"],
        "context": (
            "COMPUTED READ:\nVerdict: Risk-off. Nifty -0.72%, banks -1.4%, VIX +4.1%, "
            "oil +2.8% (Brent $92), FII -4,100cr, DII +2,600cr; 4 Middle-East headlines, SOX -3%.\n\n"
            "STANDOUT MOVERS:\nHCL Tech -3.8% (~1.6% Nifty wt); Tech Mahindra +2.2% (~1.0% wt); "
            "Kalyan Jewellers +4% (small wt); HDFC Bank -1.1% (~13% wt).\n\n"
            "CROSS-ASSET -> SECTOR IMPACT:\n- Oil regime Stress ($90-100): OMCs/paints/aviation "
            "hit; ONGC up.\n- FII selling pressures financials.\n\n"
            "INDIA HEADLINES:\n- Brent tops $92 on Hormuz risk\n- Nifty IT slips on AI-capex "
            "worries\n- Kalyan Jewellers extends rally\n\nGLOBAL CUES (context only):\n- US futures soft"
        ),
    },
    {
        "name": "risk-on / earnings beat",
        "movers": ["ICICI Bank", "Reliance", "Ola Electric"],
        "context": (
            "COMPUTED READ:\nVerdict: Mildly bullish. Nifty +0.55%, banks +1.0%, VIX -5%, "
            "oil -1.2%, FII +2,200cr, DII +700cr; Kospi +1.4%.\n\n"
            "STANDOUT MOVERS:\nICICI Bank +2.4% (~8.5% wt); Reliance +1.1% (~8.5% wt); "
            "Ola Electric +6% (small wt).\n\n"
            "CROSS-ASSET -> SECTOR IMPACT:\n- Softer oil eases inflation, helps rate-sensitives.\n"
            "- FII net buyers - flow tailwind.\n\n"
            "INDIA HEADLINES:\n- ICICI Bank Q1 profit beats estimates\n- Oil eases, rupee steadies\n"
            "- FIIs turn net buyers\n\nGLOBAL CUES (context only):\n- Nasdaq firm overnight"
        ),
    },
]


def instruction() -> str:
    return (
        "You are an Indian equity markets analyst writing a desk note for a Nifty trader. "
        "In 4-5 crisp sentences, explain what is driving INDIAN markets today. Lead with "
        "India; name 1-2 standout movers and note if their Nifty weight is small; use the "
        "numbers; do NOT quote specific RBI reserve figures; no investment advice.\n\n"
    )


def build_prompt(ctx: str, use_fewshot: bool) -> str:
    p = instruction()
    if use_fewshot:
        p += ("Study these example desk notes and match their style/rigour:\n\n"
              + fewshot_block(2) + "\n\n---\n\nNow write the desk note for TODAY:\n\n")
    else:
        p += "Write the desk note for TODAY:\n\n"
    return p + ctx


def run_model(model: str, prompt: str) -> str:
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.3},
        }, timeout=120)
        if r.status_code == 200:
            return r.json().get("response", "").strip()
        return f"[http {r.status_code}]"
    except Exception as e:
        return f"[error: {str(e)[:60]}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["llama3.2:3b", "qwen2.5:7b"])
    ap.add_argument("--no-fewshot", action="store_true")
    args = ap.parse_args()
    use_fewshot = not args.no_fewshot

    print(f"Few-shot: {'ON' if use_fewshot else 'OFF'} | rubric max = {len(RUBRIC)} pts\n")
    totals = {m: 0 for m in args.models}

    for fx in FIXTURES:
        print(f"{'='*70}\nFIXTURE: {fx['name']}\n{'='*70}")
        prompt = build_prompt(fx["context"], use_fewshot)
        for model in args.models:
            note = run_model(model, prompt)
            sc = score_note(note, {"movers": fx["movers"]})
            fails = [k for k, v in sc.items() if v is False and not k.startswith("_")]
            print(f"\n[{model}]  score {sc['_total']}/{sc['_max']}"
                  + (f"   ✗ {', '.join(fails)}" if fails else "   ✓ all"))
            print("   " + note.replace("\n", " ")[:300] + ("..." if len(note) > 300 else ""))
            totals[model] += sc["_total"]

    print(f"\n{'='*70}\nTOTALS (higher = better)\n{'='*70}")
    denom = len(FIXTURES) * len(RUBRIC)
    for m in sorted(totals, key=totals.get, reverse=True):
        print(f"  {m:16} {totals[m]}/{denom}")
    print("\nTip: run with --no-fewshot to measure how much the few-shot exemplars help.")


if __name__ == "__main__":
    main()
