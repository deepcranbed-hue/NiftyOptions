import pandas as pd
from pathlib import Path

file_path = Path("data_agent/fundamentals/screener_data/TCS.xlsx")
if file_path.exists():
    xls = pd.ExcelFile(file_path)
    print("Sheets:", xls.sheet_names)
    df = pd.read_excel(file_path, sheet_name="Data Sheet")
    print("\nFirst 20 rows of 'Data Sheet':")
    print(df.head(20).to_string(index=False))
else:
    print("File not found:", file_path)
