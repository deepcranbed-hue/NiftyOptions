import os
import sys
import json
import urllib.request
import urllib.error

# Load .env
env_path = "/Users/deepak/antigravity/NiftyOptions/.env"
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

api_key = os.environ.get("NVIDIA_API_KEY")
url = "https://integrate.api.nvidia.com/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}

# Emulate the Event Detection Agent system prompt
system_prompt = (
    "You are the Event Detection Agent in an institutional market-intelligence engine.\n"
    "MISSION: Decide which items can change market expectations; classify them.\n\n"
    "PROMPT CONTRACT:\nFrom themes, company catalysts and the observed tape, list the candidate events "
    "that can move expectations. Classify each into Economic/Corporate/Policy/"
    "Geopolitical/Market. Tune for recall; drop pure noise.\n\n"
    "GUARDRAILS:\nJudge market-moving capability, not just presence. No magnitude estimates here.\n\n"
    "HARD RULE: every NUMBER must come from a tool result (the Core). Never invent "
    "a figure. You decide which tools to call and write the reasoning.\n"
    'When done, reply with ONLY a JSON object: {"candidates":[{"label":...,"class":...,"why":...}]}'
)

# Mock input payload context
user_payload = {
    "task": "Decide which items can change market expectations; classify them.",
    "upstream_context": {"live": False},
    "core_tool_results": {
        "market_themes": [
            {"name": "FII flow", "hits": 3, "why": "Foreign institutional selling pressure on large caps"},
            {"name": "Geopolitics", "hits": 2, "why": "Rising tensions near the Strait of Hormuz affecting oil"}
        ],
        "company_intelligence": [
            {"company": "HDFC Bank", "kind": "Catalyst", "title": "Q1 profit beats street estimates"}
        ],
        "market_verdict": {"verdict": "Risk-Off / Defensive"}
    },
    "respond_with": '{"candidates":[{"label":...,"class":...,"why":...}]}'
}

payload = {
    "model": "nvidia/llama-3.3-nemotron-super-49b-v1",
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, indent=2)}
    ],
    "temperature": 0.1,
    "max_tokens": 1200
}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers=headers, method="POST")

print(f"Sending Event Detection Agent request to {url} (waiting up to 70 seconds)...")
try:
    with urllib.request.urlopen(req, timeout=70) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        raw_text = res["choices"][0]["message"]["content"]
        
        print("\n--- Raw Response content received ---")
        print(raw_text)
        print("-------------------------------------")
        
        output_path = "/Users/deepak/antigravity/NiftyOptions/scratch_scripts/raw_nvidia_response.txt"
        with open(output_path, "w") as out_f:
            out_f.write(raw_text)
        print(f"\nSaved raw response content successfully to: {output_path}")
        
except urllib.error.HTTPError as e:
    print(f"HTTP Error {e.code}")
    try:
         print("Response body:", e.read().decode("utf-8", "replace"))
    except Exception:
         pass
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
