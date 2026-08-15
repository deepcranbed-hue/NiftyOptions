import pandas as pd
from pathlib import Path

def print_section_rows(file_path):
    print(f"--- {file_path.name} ---")
    df = pd.read_excel(file_path, sheet_name="Data Sheet")
    for idx, row in df.iterrows():
        val = str(row.iloc[0]).strip()
        if pd.notna(val) and val != 'nan':
            print(val)

print_section_rows(Path("data_agent/fundamentals/screener_data/TCS.xlsx"))
print_section_rows(Path("data_agent/fundamentals/screener_data/HDFCBANK.xlsx"))
