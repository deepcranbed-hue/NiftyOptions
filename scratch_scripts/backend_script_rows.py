import re

with open("backend/main.py", "r") as f:
    content = f.read()

# Modify load_nse_csv to use oi_in_lakh=True
content = content.replace(
    "chain = load_nse_csv(temp_path, spot=spot, days=days)",
    "chain = load_nse_csv(temp_path, spot=spot, days=days, oi_in_lakh=True)"
)

# Build csv_rows
injection_build_rows = """
        chain["max_pain"] = compute_max_pain(chain)
        
        # Build OptionRow structure for the frontend
        csv_rows = []
        for i, k in enumerate(chain["strikes"]):
            csv_rows.append({
                "strike": k,
                "call_ltp": chain["call_ltp"][i],
                "put_ltp": chain["put_ltp"][i],
                "call_oi": chain["call_oi"][i],
                "put_oi": chain["put_oi"][i],
                "call_oichg": chain["call_oi_chg_pct"][i],
                "put_oichg": chain["put_oi_chg_pct"][i],
                "iv": chain["call_iv"][i] if chain["call_iv"][i] is not None else 0.0
            })
        chain["rows"] = csv_rows
"""

content = content.replace("        chain[\"max_pain\"] = compute_max_pain(chain)", injection_build_rows)

with open("backend/main.py", "w") as f:
    f.write(content)
