import requests

payload = {
    "chain": {
        "spot": 24050,
        "days": 3,
        "strikes": [24000, 24100],
        "call_ltp": [160.95, 102.45],
        "put_ltp": [64.50, 105.40],
        "r": 0.0655
    }
}
r = requests.post("http://127.0.0.1:8000/api/run-pipeline", json=payload)
print(r.status_code)
text = r.text
print(text[:200])
import json
try:
    json.loads(text)
    print("Valid JSON!")
except Exception as e:
    print("Invalid JSON:", e)
