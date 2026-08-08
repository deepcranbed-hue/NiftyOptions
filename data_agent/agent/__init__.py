"""
data_agent.agent — the local-LLM brain.

  local_llm.py  — parse_intent(): turns a natural-language data-ops command into a
                  structured action using Qwen 2.5 7B via Ollama (local model),
                  with a deterministic keyword fallback so it works with no model.
  (planned) control.py  — maps parsed intents to fetching/quality actions.
  (planned) alerts.py   — morning/evening health run -> sidebar alert payload.
"""
from .local_llm import parse_intent  # noqa: F401
