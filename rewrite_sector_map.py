import re

content = open("backend/quant/sector_map.py").read()

# I will write a script to rewrite NIFTY50 to a 3-tuple format and merge Banks -> Financials, Pharma -> Healthcare.
# We also need to add HCLTECH to make it 50 items.
