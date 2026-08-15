import re

with open("backend/quant/nse_csv_loader.py", "r") as f:
    content = f.read()

# Fix the scaling issue for OI change
content = content.replace(
    "c_oichg.append(coc or 0.0)        # NSE gives change in CONTRACTS",
    "c_oichg.append((coc or 0.0) * scale)        # NSE gives change in CONTRACTS"
)
content = content.replace(
    "p_oichg.append(poc or 0.0)        #   you'd need prev OI",
    "p_oichg.append((poc or 0.0) * scale)        #   you'd need prev OI"
)

with open("backend/quant/nse_csv_loader.py", "w") as f:
    f.write(content)
