import sys
import json
import os

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_agent", "breeze_env", "lib", "python3.9", "site-packages"))
from breeze_connect import BreezeConnect

def test_connection():
    session_token = sys.argv[1] if len(sys.argv) > 1 else "56449309"
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"

    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        res = breeze.get_customer_details(api_session=session_token)
        print("RESULT:", json.dumps(res))
    except Exception as e:
        print("ERROR:", str(e))

if __name__ == "__main__":
    test_connection()
