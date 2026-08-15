try:
    from breeze_connect import BreezeConnect
except ImportError:
    import sys
    print("Error: breeze-connect is not installed.")
    sys.exit(1)

def test_live():
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "s28*2~69700KUN944d63l#AN72Z66m38"
    session_token = "56191246"
    
    print("Initializing BreezeConnect...")
    try:
        breeze = BreezeConnect(api_key=api_key)
        
        print("Generating session...")
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        print("Session generated successfully!\n")
        
        print("Fetching Customer Details (to verify connection)...")
        customer_details = breeze.get_customer_details(api_session=session_token)
        print(f"Response: {customer_details}")
        
    except Exception as e:
        print(f"\nAPI Error: {e}")

if __name__ == "__main__":
    test_live()
