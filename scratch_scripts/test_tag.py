import sys
import os
import asyncio
import json

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.quant.llm_tag import tag_batch, _fallback

async def main():
    articles = [
        {
            "title": "Yes Bank Q1 results 2026: Net profit jumps 33.7% YoY, operating profit up 25.5%",
            "description": "Financials via RSS"
        },
        {
            "title": "Yes Bank Q1 Results: Net profit surges 34% YoY to Rs 1,071 crore; NII advances 18%",
            "description": "Financials via RSS"
        }
    ]
    
    # 1. Run full tag_batch
    print("--- Running tag_batch ---")
    res_batch = await tag_batch(articles)
    print(json.dumps(res_batch, indent=2))
    
    # 2. Run _fallback directly
    print("\n--- Running _fallback ---")
    for a in articles:
        print(a["title"], "=>", _fallback(a))

if __name__ == "__main__":
    asyncio.run(main())
