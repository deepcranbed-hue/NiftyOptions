import pandas as pd
from pathlib import Path

file_path = Path("data_agent/fundamentals/screener_data/TCS.xlsx")
if file_path.exists():
    df = pd.read_excel(file_path, sheet_name="Quarters")
    df = df.dropna(how='all', axis=1) # Drop entirely empty columns
    print(df.head(20).to_string())
