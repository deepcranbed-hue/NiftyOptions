import re

with open("backend/quant/complacency.py", "r") as f:
    content = f.read()

replacement = """
    if c.put_oi_chg_pct_atm == 0.0:
        warnings.append("put_oi_chg_pct_atm is 0.0; field may be unpopulated or referencing wrong column")
"""
new_replacement = """
    provenance = "FULL"
    if c.put_oi_chg_pct_atm == 0.0:
        warnings.append("put_oi_chg_pct_atm is 0.0; field may be unpopulated or referencing wrong column")
        provenance = "PARTIAL"
"""

content = content.replace(replacement.strip(), new_replacement.strip())
content = content.replace('"warnings": warnings', '"warnings": warnings,\n        "provenance": provenance')

with open("backend/quant/complacency.py", "w") as f:
    f.write(content)
