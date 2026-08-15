import sys
import os
import json
from datetime import datetime, timezone, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "breeze_env", "lib", "python3.9", "site-packages"))

from data_agent.fetching.orchestrator import build_plan, run
from data_agent.fetching.broker import BreezeBroker

def main():
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    session_token = "56616741"

    print("Connecting to Breeze...")
    broker = BreezeBroker(session_token=session_token, api_key=api_key, api_secret=api_secret)
    
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    
    day_atm = 24700
    strikes = [day_atm + (i * 50) for i in range(-10, 11)]

    plan = build_plan(
        underlying="NIFTY",
        include_cash=False,
        include_fo=True,
        option_expiries=[datetime(2026, 8, 11)],
        option_strikes=strikes,
        future_expiries=[],
        today=now_ist.date()
    )
    
    # Filter targets for just the missing dates (Aug 5 and Aug 6)
    # Actually `run` automatically fills gap using watermark, so we just run it and it fills up to now!
    
    print(f"Built plan with {len(plan)} targets. Running sync...")
    
    db_path = "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"
    
    report = run(broker, plan, db=db_path, timeframe="1m", now_ist=now_ist)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
