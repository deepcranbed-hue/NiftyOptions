"""
run_eval.py — grade a NewsAgent report's REASONING QUALITY. Read-only; touches nothing else.

Usage:
    # grade a saved MIO (from `agents/run.py --out mio.json`), optionally with the rendered report
    python run_eval.py --mio ../../mio.json --report ../reports/news_agent_XXXX.md
    python run_eval.py --mio mio.json --no-llm            # force deterministic-only
    python run_eval.py --mio mio.json --config my_llm.json --out scorecard.json

Outputs the markdown grade sheet to stdout and (optionally) saves the scorecard JSON. When an LLM
provider is configured in agents/llm_config.json, the Chief Economist / Trading Desk / Portfolio
Manager persona reviews activate; otherwise the deterministic scorecard renders on its own.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                       # evals/ modules
sys.path.insert(0, str(_HERE.parent / "agents"))     # read-only: reuse the model-agnostic LLM client

import evaluator            # noqa: E402
import render               # noqa: E402


def _load_client(no_llm: bool, config: str | None):
    if no_llm:
        return None
    try:
        from llm import LLMClient, LLMConfig
        client = LLMClient(LLMConfig.load(config))
        return None if client.is_deterministic() else client
    except Exception as e:
        print(f"(LLM client unavailable: {e}; running deterministic-only)", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="NewsAgent reasoning-quality evaluator")
    ap.add_argument("--mio", required=True, help="path to a saved MIO JSON")
    ap.add_argument("--report", help="path to the rendered markdown report (improves language/explainability evals)")
    ap.add_argument("--config", help="path to llm_config.json (for persona evals)")
    ap.add_argument("--no-llm", action="store_true", help="deterministic evaluators only")
    ap.add_argument("--out", help="save the scorecard JSON to this path")
    ap.add_argument("--json", action="store_true", help="print scorecard JSON instead of the markdown sheet")
    args = ap.parse_args()

    mio = json.loads(Path(args.mio).read_text(encoding="utf-8"))
    report_md = Path(args.report).read_text(encoding="utf-8") if args.report and Path(args.report).exists() else ""

    client = _load_client(args.no_llm, args.config)
    sc = evaluator.evaluate(mio, report_md, client=client)

    if args.json:
        sys.stdout.write(json.dumps(sc, ensure_ascii=False, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(render.render(sc) + "\n")

    if args.out:
        Path(args.out).write_text(json.dumps(sc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nsaved scorecard -> {args.out}", file=sys.stderr)

    oq = sc.get("overall_quality", {})
    print(f"\noverall: {oq.get('score')}/10 — {oq.get('grade')} "
          f"({oq.get('n_levels_scored')} levels scored)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
