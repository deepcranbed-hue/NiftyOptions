import pandas as pd
from pathlib import Path

def print_all_rows(filename):
    print(f"\n--- {filename} ---")
    df = pd.read_excel(f"data_agent/fundamentals/screener_data/{filename}", sheet_name="Data Sheet")
    for idx, row in df.iterrows():
        val = str(row.iloc[0]).strip()
        if pd.notna(val) and val != 'nan':
            print(val)

print_all_rows("TCS.xlsx")
print_all_rows("HDFCBANK.xlsx")
