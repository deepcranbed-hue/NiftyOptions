"""
agent.py — the Agent base and its tool-calling loop.

An Agent is: a name, a role, a system prompt (the blueprint's prompt contract +
guardrails), a bounded set of Core tools, and a deterministic `reduce` function.

Two execution modes, chosen by the LLM config:
  * deterministic  — run the agent's declared tools and hand their outputs to `reduce`.
  * llm            — a bounded tool-calling loop: the model may call the agent's tools;
                     tool RESULTS (the numbers) come from the Core; the model returns a
                     final JSON payload. On any failure the agent falls back to `reduce`
                     (the "degrade to the deterministic core" invariant).

Either way, numbers originate in the Core; the LLM only decides which tools to call and
writes the narrative/structure. `reduce` also guarantees the agent always returns a
well-formed output even with no LLM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Any

import tools as toolreg
from llm import LLMClient, Turn


@dataclass
class Agent:
    name: str
    role: str
    prompt_contract: str
    guardrails: str
    tool_names: list[str]
    reduce: Callable[[dict, dict], dict]         # (tool_outputs, context) -> output
    output_hint: str = ""                         # what JSON the LLM should return

    # -- system prompt shown to the LLM -------------------------------------
    def system_prompt(self) -> str:
        return (
            f"You are the {self.name} in an institutional market-intelligence engine.\n"
            f"MISSION: {self.role}\n\n"
            f"PROMPT CONTRACT:\n{self.prompt_contract}\n\n"
            f"GUARDRAILS:\n{self.guardrails}\n\n"
            "HARD RULE: every NUMBER must come from a tool result (the Core). Never invent "
            "a figure. You decide which tools to call and write the reasoning.\n"
            f"When done, reply with ONLY a JSON object: {self.output_hint}"
        )

    # -- run ----------------------------------------------------------------
    def run(self, client: LLMClient, context: dict) -> dict:
        # gather this agent's tool outputs once (used by reduce, and as LLM context)
        tool_outputs = {n: toolreg.call(n) for n in self.tool_names}

        if client.is_deterministic():
            out = self.reduce(tool_outputs, context)
            return self._wrap(out, mode="deterministic", trace=list(tool_outputs))

        # --- LLM reasoning (single-turn, model-agnostic) ---
        # The agent's Core tools are prefetched above (numbers from the Core). We hand
        # those results to the LLM and ask for the structured output. This works with ANY
        # model — including ones without tool-calling — and keeps the hard rule intact
        # (the LLM reasons/narrates; every number is a Core result). Optional tool specs
        # are still passed so tool-calling models MAY request more; a returned tool-call
        # turn is honoured once, then we ask again for the final JSON.
        try:
            specs = toolreg.spec(self.tool_names)
            user = {"role": "user", "content": json.dumps({
                "task": self.role,
                "upstream_context": {k: context[k] for k in context},
                "core_tool_results": tool_outputs,
                "respond_with": self.output_hint,
            }, default=str)[:12000]}
            messages = [user]
            called = list(tool_outputs)
            turn: Turn = client.chat(self.system_prompt(), messages, specs)
            if turn.kind == "tool_calls":                       # optional extra tools
                for tc in turn.tool_calls[:4]:
                    res = toolreg.call(tc["name"], tc.get("arguments"))
                    called.append(tc["name"])
                    messages.append({"role": "user",
                                     "content": f"tool {tc['name']} -> "
                                                + json.dumps(res, default=str)[:6000]})
                messages.append({"role": "user",
                                 "content": f"Now respond with ONLY the JSON: {self.output_hint}"})
                turn = client.chat(self.system_prompt(), messages, [])
            parsed = _parse_json(turn.text)
            if parsed is not None:
                return self._wrap(parsed, mode=f"llm:{client.provider}", trace=called)
        except Exception as e:
            return self._wrap(self.reduce(tool_outputs, context),
                              mode=f"fallback({type(e).__name__})",
                              trace=list(tool_outputs), error=str(e))
        # unparseable/empty -> degrade to the deterministic core
        return self._wrap(self.reduce(tool_outputs, context),
                          mode="fallback(no-json)", trace=list(tool_outputs),
                          error="LLM returned no parseable JSON")

    def _wrap(self, output: dict, mode: str, trace: list[str], error: str = "") -> dict:
        w = {"agent": self.name, "mode": mode, "tools_called": trace, "output": output}
        if error:
            w["error"] = error
        return w


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    s = text.strip()
    # strip code fences if present
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s.strip("`")
        s = s[4:] if s.lower().startswith("json") else s
    try:
        return json.loads(s)
    except Exception:
        # last resort: grab the outermost {...}
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                return None
    return None
