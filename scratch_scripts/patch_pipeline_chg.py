import re

with open("backend/quant/pipeline.py", "r") as f:
    content = f.read()

# Fix mean_put_oi_change_pct to support both live chains (put_oichg) and CSV chains (put_oi_chg_pct)
replacement = """
def mean_put_oi_change_pct(chain: dict, spot: float, band_pts: int = 200) -> float:
    strikes = chain.get("strikes", [])
    put_oichg = chain.get("put_oichg") or chain.get("put_oi_chg_pct", [])
"""
content = re.sub(
    r'def mean_put_oi_change_pct\(chain: dict, spot: float, band_pts: int = 200\) -> float:\n    strikes = chain.get\("strikes", \[\]\)\n    put_oichg = chain.get\("put_oichg", \[\]\)',
    replacement.strip(),
    content
)

with open("backend/quant/pipeline.py", "w") as f:
    f.write(content)
