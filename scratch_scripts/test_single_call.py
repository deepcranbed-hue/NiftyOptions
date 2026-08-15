import sys
import os
import time

# Ensure we can import modules from the parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from newsindex.NewsAgent.agents.llm import LLMClient

def main():
    client = LLMClient()
    start_time = time.time()
    print(f"Initiating single API call...")
    print(f"Provider: {client.cfg.provider}")
    print(f"Model   : {client.cfg.model}")
    print(f"URL     : {client.cfg.base_url}")
    print("-" * 50)
    
    try:
        # Measure call duration
        turn = client.chat(
            system="You are a financial analyst helper. Answer extremely briefly.",
            messages=[{"role": "user", "content": "What is the current primary sentiment direction of global steel markets? Answer in one short sentence."}],
            tools=[]
        )
        duration = time.time() - start_time
        print(f"STATUS   : SUCCESS")
        print(f"RESPONSE : {turn.text.strip()}")
        print(f"DURATION : {duration:.2f} seconds")
    except Exception as e:
        duration = time.time() - start_time
        print(f"STATUS   : FAILED")
        print(f"ERROR    : {str(e)}")
        print(f"DURATION : {duration:.2f} seconds")

if __name__ == "__main__":
    main()
