import pandas as pd
from pathlib import Path

file_path = Path("data_agent/fundamentals/screener_data/TCS.xlsx")
if file_path.exists():
    df = pd.read_excel(file_path, sheet_name="Data Sheet")
    print(df.iloc[38:53].to_string(index=False))
