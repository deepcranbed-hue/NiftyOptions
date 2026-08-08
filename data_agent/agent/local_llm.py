"""
data_agent.agent.local_llm
==========================
The local-LLM brain for the data agent. Turns a natural-language command into a
structured action using Qwen 2.5 7B via Ollama (the same local model the rest of
the project uses), with a deterministic keyword fallback so it works with no model.

    parse_intent("start downloading with my breeze token abc123")
      -> {"action": "start", "broker": "breeze", "token": "abc123",
          "symbols": [], "days": None, "include_options": False}

Actions: start | stop | sync | health | backfill | unknown
Local by design — no cloud call; if Ollama isn't running it degrades to keywords.
"""
from __future__ import annotations
import json
import re
import urllib.request

OLLAMA = "http://localhost:11434"
QWEN_MODEL = "llama3.2:3b"

SYSTEM = (
    "You convert a market-data operations command into JSON. Valid actions: "
    "start (begin auto-downloading), stop, sync (fetch specific symbols now), "
    "health (report data completeness / is data up to the mark), backfill (fill "
    "past days), unknown. Return ONLY a JSON object with keys: "
    '{"action": <one of the above>, "broker": "breeze"|"kite"|null, '
    '"token": string|null, "symbols": [uppercase tickers], "days": int|null, '
    '"include_options": boolean}. No prose, no markdown, no code fences."'
)


def _ollama_up() -> bool:
    try:
        urllib.request.urlopen(OLLAMA + "/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _qwen(text: str) -> dict | None:
    body = json.dumps({
        "model": QWEN_MODEL, "temperature": 0.0, "stream": False,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": text}],
    }).encode()
    req = urllib.request.Request(OLLAMA + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    t = r["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", re.sub(r"```json|```", "", t), re.DOTALL)
    return json.loads(m.group(0)) if m else None


_TICKER = re.compile(r"\b([A-Z][A-Z&\-]{2,})\b")
_NOISE = {"TOKEN", "BREEZE", "KITE", "ZERODHA", "OK", "PDF"}


def _fallback(text: str) -> dict:
    """Deterministic keyword parse — the always-available path."""
    t = text.lower()
    if "stop" in t:
        action = "stop"
    elif any(w in t for w in ("up to", "mark", "health", "quality", "complete", "coverage")):
        action = "health"
    elif "backfill" in t:
        action = "backfill"
    elif "start" in t or ("download" in t and "sync" not in t):
        action = "start"
    elif "sync" in t or "fetch" in t or "download" in t:
        action = "sync"
    else:
        action = "unknown"

    broker = "breeze" if "breeze" in t else "kite" if ("kite" in t or "zerodha" in t) else None
    mtok = re.search(r"token\s+([A-Za-z0-9_\-.]+)", text)
    md = re.search(r"(\d+)\s*day", t)
    syms = [s for s in _TICKER.findall(text) if s not in _NOISE]
    return {"action": action, "broker": broker,
            "token": mtok.group(1) if mtok else None,
            "symbols": syms, "days": int(md.group(1)) if md else None,
            "include_options": "option" in t, "_provider": "keyword_fallback"}


def parse_intent(text: str, prefer_llm: bool | None = None) -> dict:
    # None -> consult the global toggle (agent_settings.local_llm_enabled). An explicit
    # True/False still wins, so callers/tests can force a path.
    if prefer_llm is None:
        try:
            import agent_settings
            prefer_llm = agent_settings.local_llm_enabled()
        except Exception:
            prefer_llm = True
    if prefer_llm and _ollama_up():
        try:
            j = _qwen(text)
            if isinstance(j, dict) and j.get("action"):
                j.setdefault("_provider", QWEN_MODEL)
                j.setdefault("symbols", [])
                return j
        except Exception:
            pass
    return _fallback(text)


if __name__ == "__main__":
    for cmd in [
        "start downloading with my breeze token abc123XYZ",
        "sync TCS and its options",
        "is the data up to the mark?",
        "backfill last 5 days for NIFTY",
        "stop the data agent",
    ]:
        print(f"{cmd!r:52} -> {json.dumps(parse_intent(cmd, prefer_llm=False))}")
