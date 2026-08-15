import pandas as pd
from pathlib import Path

file_path = Path("data_agent/fundamentals/screener_data/TCS.xlsx")
if file_path.exists():
    df = pd.read_excel(file_path, sheet_name="Quarters")
    print("\nFirst 20 rows of 'Quarters':")
    print(df.head(20).to_string(index=False))
else:
    print("File not found:", file_path)
