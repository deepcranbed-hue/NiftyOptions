"""
Test script for breeze-connect (ICICI Direct API)
"""
try:
    from breeze_connect import BreezeConnect
    print("Successfully imported BreezeConnect!")
except ImportError:
    print("Error: breeze-connect is not installed in the current environment.")
    print("To install, run: pip install breeze-connect")
    import sys
    sys.exit(1)

def test_breeze():
    print("Initializing BreezeConnect...")
    # Initialize with dummy API key
    try:
        breeze = BreezeConnect(api_key="your_api_key")
        print("Initialization successful!")
        print(f"BreezeConnect object: {breeze}")
        
        # Test if methods exist
        methods = [m for m in dir(breeze) if not m.startswith('_')]
        print("\nAvailable public methods:")
        for m in methods[:10]: # Print first 10 methods
            print(f"- {m}")
        if len(methods) > 10:
            print(f"... and {len(methods) - 10} more.")
            
        print("\nNote: To fetch actual stock data, you need to generate a session:")
        print("breeze.generate_session(api_secret='your_secret', session_token='your_token')")
        print("Then you can use breeze.get_historical_data(...)")
        
    except Exception as e:
        print(f"Failed during initialization/testing: {e}")

if __name__ == "__main__":
    test_breeze()
