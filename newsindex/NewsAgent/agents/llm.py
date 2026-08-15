"""
llm.py — model-agnostic LLM client for the News Intelligence Agent runtime.

One interface, four backends selected by llm_config.json:
    deterministic  — no LLM; the caller runs tools directly (offline, zero cost)
    ollama         — local Ollama (/api/chat), tool-calling
    anthropic      — Claude Messages API, tool-calling
    openai         — OpenAI or any OpenAI-compatible endpoint (base_url), tool-calling

The client exposes a single method:

    client.chat(system, messages, tools) -> Turn

where `tools` is a list of {name, description, parameters(JSONSchema)} and a `Turn`
is either:
    Turn(kind="tool_calls", tool_calls=[{id,name,arguments}])   # model wants tools run
    Turn(kind="final",       text="...")                          # model's final answer

The agent loop (agent.py) executes tool calls and feeds results back until `final`.

Providers that need a network endpoint are implemented to their documented API shapes.
Only `deterministic` is exercised by the offline test; the others activate when configured.
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
@dataclass
class Turn:
    kind: str                       # "tool_calls" | "final"
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class LLMConfig:
    provider: str = "deterministic"
    model: str = "llama3.2:3b"
    base_url: str | None = None
    api_key_env: str = "LLM_API_KEY"
    temperature: float = 0.2
    max_tokens: int = 1200
    max_tool_iters: int = 4
    request_timeout_s: int = 60

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LLMConfig":
        """Load llm_config.json (falls back to llm_config.example.json, then defaults)."""
        here = Path(__file__).resolve().parent
        candidates = [path] if path else [here / "llm_config.json",
                                          here / "llm_config.example.json"]
        for c in candidates:
            if c and Path(c).exists():
                raw = json.loads(Path(c).read_text())
                fields = {k: raw[k] for k in raw
                          if k in cls.__dataclass_fields__}
                return cls(**fields)
        return cls()


class LLMError(RuntimeError):
    """A clear, human-readable LLM transport error (carries HTTP status when known)."""
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def _http_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # server replied with an error status — surface code + body so the cause is obvious
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        hint = {401: "auth — check api_key_env / key",
                403: "forbidden — key lacks access to this model",
                404: "not found — check base_url path and model name",
                400: "bad request — model name or payload not accepted",
                500: "server error at the LLM endpoint",
                503: "endpoint unavailable / overloaded"}.get(e.code, "")
        raise LLMError(f"HTTP {e.code} at {url}"
                       + (f" ({hint})" if hint else "")
                       + (f" :: {body}" if body else ""), code=e.code) from None
    except urllib.error.URLError as e:
        # could not even reach the endpoint (e.g. Ollama not running, wrong host/port)
        raise LLMError(f"cannot reach {url} — {e.reason} "
                       "(is the LLM server running at this base_url?)") from None


# ---------------------------------------------------------------------------
class LLMClient:
    def __init__(self, config: LLMConfig | None = None):
        self.cfg = config or LLMConfig.load()

    @property
    def provider(self) -> str:
        return self.cfg.provider

    def is_deterministic(self) -> bool:
        return self.cfg.provider == "deterministic"

    # -- unified entrypoint --------------------------------------------------
    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        p = self.cfg.provider
        if p == "deterministic":
            # Should not be called in deterministic mode; the agent loop shortcuts it.
            return Turn(kind="final", text="{}")
        if p == "openai":
            import time
            time.sleep(7.5)  # Increased throttling delay to respect Nvidia NIM's strict RPM limits
        if p == "ollama":
            return self._ollama(system, messages, tools)
        if p == "anthropic":
            return self._anthropic(system, messages, tools)
        if p == "openai":
            return self._openai(system, messages, tools)
        raise ValueError(f"unknown provider: {p}")

    # -- Ollama --------------------------------------------------------------
    def _ollama(self, system, messages, tools) -> Turn:
        base = self.cfg.base_url or "http://localhost:11434"
        payload = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "options": {"temperature": self.cfg.temperature},
        }
        if tools:
            payload["tools"] = [{"type": "function",
                                 "function": {"name": t["name"],
                                              "description": t["description"],
                                              "parameters": t["parameters"]}}
                                for t in tools]
        out = _http_json(f"{base}/api/chat", payload, {"Content-Type": "application/json"},
                         self.cfg.request_timeout_s)
        msg = out.get("message", {})
        tcs = msg.get("tool_calls") or []
        if tcs:
            calls = [{"id": str(i), "name": tc["function"]["name"],
                      "arguments": tc["function"].get("arguments", {})}
                     for i, tc in enumerate(tcs)]
            return Turn(kind="tool_calls", tool_calls=calls)
        return Turn(kind="final", text=msg.get("content", ""))

    # -- Anthropic -----------------------------------------------------------
    def _anthropic(self, system, messages, tools) -> Turn:
        base = self.cfg.base_url or "https://api.anthropic.com"
        key = os.environ.get(self.cfg.api_key_env, "")
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "temperature": self.cfg.temperature,
            "system": system,
            "messages": messages,
        }
        if tools:
            payload["tools"] = [{"name": t["name"], "description": t["description"],
                                 "input_schema": t["parameters"]} for t in tools]
        headers = {"Content-Type": "application/json", "x-api-key": key,
                   "anthropic-version": "2023-06-01"}
        out = _http_json(f"{base}/v1/messages", payload, headers, self.cfg.request_timeout_s)
        calls, text = [], ""
        for block in out.get("content", []):
            if block.get("type") == "tool_use":
                calls.append({"id": block["id"], "name": block["name"],
                              "arguments": block.get("input", {})})
            elif block.get("type") == "text":
                text += block.get("text", "")
        if calls:
            return Turn(kind="tool_calls", tool_calls=calls)
        return Turn(kind="final", text=text)

    # -- OpenAI / compatible -------------------------------------------------
    def _openai(self, system, messages, tools) -> Turn:
        base = self.cfg.base_url or "https://api.openai.com/v1"
        key = os.environ.get(self.cfg.api_key_env, "")
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "messages": [{"role": "system", "content": system}] + messages,
        }
        if tools:
            payload["tools"] = [{"type": "function",
                                 "function": {"name": t["name"],
                                              "description": t["description"],
                                              "parameters": t["parameters"]}}
                                for t in tools]
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {key}"}
        out = _http_json(f"{base}/chat/completions", payload, headers,
                         self.cfg.request_timeout_s)
        choice = out["choices"][0]["message"]
        tcs = choice.get("tool_calls") or []
        if tcs:
            calls = []
            for tc in tcs:
                args = tc["function"].get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                calls.append({"id": tc.get("id", ""), "name": tc["function"]["name"],
                              "arguments": args})
            return Turn(kind="tool_calls", tool_calls=calls)
        return Turn(kind="final", text=choice.get("content", "") or "")
