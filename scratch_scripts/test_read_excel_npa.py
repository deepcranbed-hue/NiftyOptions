import pandas as pd
from pathlib import Path

df = pd.read_excel("data_agent/fundamentals/screener_data/HDFCBANK.xlsx", sheet_name="Data Sheet")
for idx, row in df.iterrows():
    val = str(row.iloc[0]).strip().upper()
    if 'NPA' in val:
        print(f"Row {idx}: {val}")
