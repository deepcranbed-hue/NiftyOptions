import os
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")
print(f"API Key present: {bool(api_key)}")
if api_key:
    print(f"API Key preview: {api_key[:5]}...{api_key[-5:]}")

url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}
payload = {
    "model": "gemini-2.5-flash",
    "messages": [
        {"role": "user", "content": "Hello, respond with 'Success' if you read this."}
    ]
}

print(f"Posting to: {url}")
data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers=headers, method="POST")

try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print("HTTP Status Code:", resp.status)
        print("Response body:")
        print(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print("HTTP Error Code:", e.code)
    print("Response body:")
    print(e.read().decode("utf-8"))
except Exception as e:
    print("Generic Error:", e)
