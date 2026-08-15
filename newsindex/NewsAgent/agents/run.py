"""
run.py — CLI entrypoint for the News Intelligence Agent runtime.

Runs the full multi-agent pipeline and prints (and optionally saves) the standardized
Market Intelligence Object plus the agent trace.

Usage:
    python run.py                       # live snapshot, provider from llm_config.json
    python run.py --config my.json      # explicit LLM config
    python run.py --out mio.json        # also save the MIO
    python run.py --trace               # print the per-agent execution trace
    python run.py --bundle              # print the full intelligence bundle

Set NEWSINDEX_HOME to the newsindex project root if market_scan.py isn't auto-found.
"""
from __future__ import annotations

import argparse
import json
import sys

from llm import LLMClient, LLMConfig
from orchestrator import Orchestrator


def main() -> int:
    ap = argparse.ArgumentParser(description="News Intelligence Agent — multi-agent runtime")
    ap.add_argument("--config", help="path to llm_config.json")
    ap.add_argument("--out", help="save the MIO JSON to this path")
    ap.add_argument("--from-mio", dest="from_mio",
                    help="skip the pipeline; render the report from an already-saved MIO JSON")
    ap.add_argument("--report", action="store_true",
                    help="also render + save a markdown desk report (market_scan style + agent layer)")
    ap.add_argument("--report-dir", help="directory for the report (default: project reports/)")
    ap.add_argument("--trace", action="store_true", help="print per-agent execution trace")
    ap.add_argument("--bundle", action="store_true", help="print the full intelligence bundle")
    ap.add_argument("--mock", action="store_true",
                    help="offline sanity check: run on the built-in mock snapshot (no network/LLM data)")
    ap.add_argument("--no-llm", action="store_true",
                    help="force the deterministic Core (no LLM), overriding llm_config.json provider")
    args = ap.parse_args()

    # ---- render-only mode: build the report straight from a saved MIO JSON ----
    if args.from_mio:
        import reporter
        with open(args.from_mio, encoding="utf-8") as f:
            saved = json.load(f)
        if not saved.get("engine_stats"):
            print("⚠️  this MIO predates engine_stats — exec-summary conviction may read 0; "
                  "re-run the pipeline once to refresh.", file=sys.stderr)
        result = {"mio": saved, "validation": {"valid": None},
                  "provider": "saved-json", "agent_trace": [],
                  "intelligence_bundle": {}}
        md = reporter.render_report(result)
        if args.report:
            path = reporter.save_report(result, args.report_dir)
            print(f"saved report -> {path}", file=sys.stderr)
        sys.stdout.write(md + "\n")
        return 0

    if args.no_llm:
        client = LLMClient(LLMConfig(provider="deterministic"))
    else:
        client = LLMClient(LLMConfig.load(args.config))
    print(f"provider: {client.provider}  model: {client.cfg.model}", file=sys.stderr)

    orch = Orchestrator(client)
    if args.mock:
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
        from test_offline import MOCK
        result = orch.run(snapshot=dict(MOCK))   # offline replay
    else:
        result = orch.run()                       # live snapshot via the Collector agent

    mio = result["mio"]
    val = result["validation"]

    if args.report:
        # print the FORMATTED markdown report to stdout (what most users want to see)
        import reporter
        sys.stdout.write(reporter.render_report(result))
        sys.stdout.write("\n")
    else:
        # no --report: print the MIO JSON
        print(json.dumps(mio, ensure_ascii=False, indent=2, default=str))

    print(f"\nschema valid: {val.get('valid')}", file=sys.stderr)
    for e in val.get("errors", []):
        print("  - " + e, file=sys.stderr)

    if mio.get("overlay_error"):
        print(f"\n⚠️  overlay failed entirely: {mio['overlay_error']}", file=sys.stderr)
    if mio.get("overlay_warnings"):
        print("\n⚠️  overlay partial — some sections skipped:", file=sys.stderr)
        for w in mio["overlay_warnings"]:
            print("     - " + w, file=sys.stderr)

    if result.get("llm_note"):
        print(f"\n⚠️  {result['llm_note']}", file=sys.stderr)

    if args.trace:
        print("\n--- agent trace ---", file=sys.stderr)
        for t in result["agent_trace"]:
            line = f"  {t['agent']:32s} [{t['mode']}]  tools={t['tools_called']}"
            if t.get("error"):
                line += f"\n      ↳ {t['error']}"
            print(line, file=sys.stderr)

    if args.bundle:
        print("\n--- intelligence bundle ---", file=sys.stderr)
        print(json.dumps(result["intelligence_bundle"], ensure_ascii=False,
                         indent=2, default=str), file=sys.stderr)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(mio, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nsaved MIO -> {args.out}", file=sys.stderr)

    if args.report:
        import reporter
        path = reporter.save_report(result, args.report_dir)
        print(f"saved report -> {path}", file=sys.stderr)

    return 0 if val.get("valid") in (True, None) else 1


if __name__ == "__main__":
    sys.exit(main())
