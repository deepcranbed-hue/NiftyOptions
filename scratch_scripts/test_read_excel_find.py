import pandas as pd
from pathlib import Path

file_path = Path("data_agent/fundamentals/screener_data/TCS.xlsx")
if file_path.exists():
    df = pd.read_excel(file_path, sheet_name="Data Sheet")
    for idx, row in df.iterrows():
        val = str(row.iloc[0]).upper()
        if "QUARTER" in val or "BALANCE" in val or "CASH" in val:
            print(f"Row {idx}: {val}")
