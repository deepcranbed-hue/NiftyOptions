from breeze_connect import BreezeConnect
import json

def debug():
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    session_token = "56207238"
    
    breeze = BreezeConnect(api_key=api_key)
    breeze.generate_session(api_secret=api_secret, session_token=session_token)
    
    # After generation, the security master is downloaded and parsed.
    # Let's inspect the NDX scrip master dictionary.
    # index 2 corresponds to NDX
    ndx_dict = breeze.stock_script_dict_list[2]
    print(f"Total keys in NDX dictionary: {len(ndx_dict)}")
    
    # Print first 20 keys and values in NDX dictionary
    count = 0
    for k, v in ndx_dict.items():
        print(f"KEY: {k} -> VALUE: {v}")
        count += 1
        if count >= 30:
            break

if __name__ == "__main__":
    debug()
