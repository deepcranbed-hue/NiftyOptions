import datetime
from breeze_connect import BreezeConnect

def get_today_chain():
    # Your credentials
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    session_token = "56225492"
    
    print("Connecting to Breeze API...")
    breeze = BreezeConnect(api_key=api_key)
    breeze.generate_session(api_secret=api_secret, session_token=session_token)
    
    target_expiry = "2026-07-07T06:00:00.000Z" 
    
    print(f"Fetching Option Chain for NIFTY (Expiry: {target_expiry})...")
    
    try:
        response = breeze.get_option_chain_quotes(
            stock_code="NIFTY",
            exchange_code="NFO",
            product_type="options",
            expiry_date=target_expiry,
            right="others" 
        )
        
        if response.get("Success"):
            data = response["Success"]
            if len(data) > 0:
                first_item = data[0]
                print("\nAvailable columns (keys) for a single strike:")
                for key, value in first_item.items():
                    print(f" - {key}: {value}")
        else:
            print(f"Failed to fetch data: {response}")
            
    except Exception as e:
        print(f"API Error: {e}")

if __name__ == "__main__":
    get_today_chain()
