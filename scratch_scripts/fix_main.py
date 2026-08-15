import re

with open("backend/main.py", "r") as f:
    content = f.read()

injection = """
        # Clean up
        shutil.rmtree(temp_dir)
        
        # Calculate max pain
        def compute_max_pain(chain_dict):
            min_pain = float('inf')
            max_pain_strike = 0
            S = chain_dict["strikes"]
            C_OI = chain_dict["call_oi"]
            P_OI = chain_dict["put_oi"]
            for target_k in S:
                pain = 0
                for i, k in enumerate(S):
                    if k < target_k:
                        pain += (target_k - k) * C_OI[i]
                    if k > target_k:
                        pain += (k - target_k) * P_OI[i]
                if pain < min_pain:
                    min_pain = pain
                    max_pain_strike = target_k
            return max_pain_strike
        
        chain["max_pain"] = compute_max_pain(chain)
"""

content = content.replace("        # Clean up\n        shutil.rmtree(temp_dir)", injection)
content = content.replace("        return res", "        res['chain_meta'] = chain\n        return res")

with open("backend/main.py", "w") as f:
    f.write(content)
