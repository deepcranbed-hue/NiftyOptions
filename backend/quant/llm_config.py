"""
llm_config.py
-------------
Single source of truth for WHICH LLM the system uses. Change the model here,
nothing else. Supports Gemini, Qwen (Alibaba DashScope / OpenRouter / self-hosted),
or any OpenAI-compatible endpoint. Provider is config, not code.

The tagging task (news sentiment + sector + beat/miss) is easy enough that any
modern model handles it — so this is about COST and CONTROL, not quality. Keep a
fallback chain so an outage never blocks the pipeline (provenance records which
provider actually ran).
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field


@dataclass
class LLMProvider:
    name: str                       # "gemini" | "qwen" | "openai_compatible" | "local"
    model: str                      # model id string
    endpoint: str                   # full URL of the completion endpoint
    api_key_env: str                # env var holding the key ("" for keyless/local)
    auth_style: str = "bearer"      # "bearer" (OpenAI/Qwen) | "query_key" (Gemini)
    request_style: str = "openai"   # "openai" (messages[]) | "gemini" (contents[])
    extra_headers: dict = field(default_factory=dict)


# ── PROVIDER REGISTRY — add/edit providers here ─────────────────────────────
PROVIDERS = {
    "gemini": LLMProvider(
        name="gemini",
        model="gemini-3.1-flash-lite",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/"
                 "gemini-3.1-flash-lite:generateContent",
        api_key_env="GEMINI_API_KEY",
        auth_style="query_key", request_style="gemini"),

    # Qwen via Alibaba DashScope (OpenAI-compatible mode)
    "qwen": LLMProvider(
        name="qwen",
        model="qwen-plus",          # or qwen-turbo (cheaper), qwen-max (stronger)
        endpoint="https://dashscope-intl.aliyuncs.com/compatible-mode/v1/"
                 "chat/completions",
        api_key_env="DASHSCOPE_API_KEY",
        auth_style="bearer", request_style="openai"),

    # Qwen via OpenRouter (alternative, also OpenAI-compatible)
    "qwen_openrouter": LLMProvider(
        name="qwen_openrouter",
        model="qwen/qwen-2.5-72b-instruct",
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        api_key_env="OPENROUTER_API_KEY",
        auth_style="bearer", request_style="openai"),

    # Self-hosted model / any local OpenAI-compatible server (vLLM, Ollama, LM Studio)
    "local": LLMProvider(
        name="local",
        model="qwen2.5:7b",
        endpoint="http://localhost:11434/v1/chat/completions",  # Ollama default
        api_key_env="",             # keyless
        auth_style="bearer", request_style="openai"),
}

# ── ACTIVE CONFIG — change these two lines to switch the whole system ────────
ACTIVE_PROVIDER = "gemini"
FALLBACK_CHAIN = ["gemini"]                    # tried in order on failure
# (keyword heuristic is the final fallback inside the tagger — never an LLM)


def active() -> LLMProvider:
    return PROVIDERS[ACTIVE_PROVIDER]


def _local_llm_on() -> bool:
    """Respect the global on-device toggle. When OFF, the 'local' (Ollama on this Mac)
    provider is dropped from the chain so no local inference runs — the tagger uses a
    cloud provider if a key is set, else its keyword heuristic. Never heats the Mac."""
    return False


def fallback_providers() -> list[LLMProvider]:
    """Active first, then the rest of the chain (deduped, only those with keys set
    or keyless local). The keyless 'local' provider is skipped when the on-device LLM
    toggle is OFF."""
    local_on = _local_llm_on()
    order = [ACTIVE_PROVIDER] + [p for p in FALLBACK_CHAIN if p != ACTIVE_PROVIDER]
    out = []
    for name in order:
        p = PROVIDERS.get(name)
        if not p:
            continue
        if p.name == "local" and not local_on:                # on-device disabled
            continue
        if p.api_key_env == "" or os.getenv(p.api_key_env):   # key present or keyless
            out.append(p)
    return out


if __name__ == "__main__":
    print(f"ACTIVE: {ACTIVE_PROVIDER} -> {active().model} @ {active().endpoint}")
    print("Fallback chain (with keys available):",
          [p.name for p in fallback_providers()] or "(none — set an API key)")
