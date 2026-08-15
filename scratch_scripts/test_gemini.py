import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

async def test():
    if not api_key:
        print("No API key.")
        return
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    payload = {
        "contents": [{"parts": [{"text": "Hello, are you working?"}]}]
    }
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, params={"key": api_key}, json=payload, timeout=20)
            print("Status Code:", r.status_code)
            print("Response:", r.text)
        except Exception as e:
            print("Exception:", str(e))

asyncio.run(test())
