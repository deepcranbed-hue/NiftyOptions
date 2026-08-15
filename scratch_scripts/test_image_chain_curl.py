import requests
import json

payload = {
  "articles": [],
  "chain": {
    "strikes": [
        23400, 23450, 23500, 23550, 23600, 23650, 23700, 23750, 23800, 23850,
        23900, 23950, 24000, 24050, 24100, 24150, 24200, 24250, 24300, 24350,
        24400, 24450, 24500, 24550, 24600
    ],
    "call_ltp": [
        701.60, 653.65, 601.45, 556.00, 503.65, 455.40, 409.75, 364.30, 317.70, 276.50,
        234.40, 196.00, 160.95, 129.15, 102.45, 78.10, 58.70, 43.55, 31.15, 22.55,
        16.40, 12.25, 9.00, 6.65, 5.15
    ],
    "put_ltp": [
        3.30, 4.15, 4.90, 6.10, 7.50, 9.85, 12.95, 16.75, 22.10, 29.45,
        38.15, 49.50, 64.50, 82.80, 105.40, 131.20, 161.15, 196.70, 234.50, 275.85,
        320.40, 365.90, 413.00, 460.05, 510.00
    ],
    "spot": 24100.0,
    "days": 5.0,
    "r": 0.0655
  },
  "prev_regime": None
}

r = requests.post("http://127.0.0.1:8000/api/run-pipeline", json=payload)
print("Status:", r.status_code)
text = r.text
print("Has NaN?", "NaN" in text)
print("Has Infinity?", "Infinity" in text)

try:
    json.loads(text)
    print("Valid JSON.")
except Exception as e:
    print("Invalid JSON:", e)
