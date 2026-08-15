import time
import hmac
import hashlib
import base64
import json
import urllib.request

def generate_token(api_key: str, exp_seconds: int = 300) -> str:
    try:
        api_key_id, secret = api_key.split(".")
    except ValueError:
        raise ValueError("Invalid API key format")
        
    header = {
        "alg": "HS256",
        "sign_type": "SIGN"
    }
    
    timestamp = int(time.time() * 1000)
    payload = {
        "api_key": api_key_id,
        "exp": int(time.time()) + exp_seconds,
        "timestamp": timestamp
    }
    
    def b64url_encode(data: dict) -> str:
        json_str = json.dumps(data, separators=(',', ':')).encode('utf-8')
        # Base64url encoding strips padding '='
        return base64.urlsafe_b64encode(json_str).replace(b'=', b'').decode('utf-8')
        
    segments = [
        b64url_encode(header),
        b64url_encode(payload)
    ]
    
    signing_input = ".".join(segments).encode('utf-8')
    key = secret.encode('utf-8')
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    
    signature_b64 = base64.urlsafe_b64encode(signature).replace(b'=', b'').decode('utf-8')
    segments.append(signature_b64)
    
    return ".".join(segments)

def test_chat():
    api_key = "9b04029839064760920d16615bd3b4ae.cys0urAM8ZOUjPa8"
    
    print("Generating JWT authentication token...")
    try:
        token = generate_token(api_key)
    except Exception as token_err:
        print(f"Token generation failed: {token_err}")
        return
        
    # Zhipu AI chat completion endpoint
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    
    prompt = "Summarize the following market news in 1 sentence: Nifty closed 150 points up led by gains in IT and banking sector."
    print(f"Sending prompt: {prompt}")
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    body = {
        "model": "glm-5.2",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers=headers,
        method="POST"
    )
    
    try:
        print("Connecting to Zhipu AI API...")
        with urllib.request.urlopen(req) as response:
            resp_data = response.read().decode('utf-8')
            res = json.loads(resp_data)
            print("\n--- Response ---")
            print(res["choices"][0]["message"]["content"])
            print("----------------")
    except Exception as e:
        print(f"Error during API call: {e}")
        if hasattr(e, "read"):
            print("Server response:", e.read().decode('utf-8', errors='ignore'))

if __name__ == "__main__":
    test_chat()
