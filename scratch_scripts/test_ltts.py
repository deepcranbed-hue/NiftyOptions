import pandas as pd
df = pd.read_excel('data_agent/fundamentals/screener_data/LTTS.xlsx', sheet_name='Data Sheet')
def find_row_index(df, col, text):
    for idx, val in df[col].items():
        if pd.notna(val) and str(val).strip().upper().startswith(text.upper()):
            return idx
    return None

pl_idx = find_row_index(df, df.columns[0], 'PROFIT & LOSS')
derived_idx = find_row_index(df, df.columns[0], 'DERIVED')
print(f"pl_idx={pl_idx}, derived_idx={derived_idx}")

def process_section(df, start_idx, end_idx, date_idx):
    row_map = {}
    for idx in range(start_idx, end_idx):
        key = str(df.at[idx, df.columns[0]]).strip().upper()
        if key and key != 'NAN':
            row_map[key] = idx
    return row_map

derived_map = process_section(df, derived_idx + 1, len(df), pl_idx + 1)
print(f"derived_map={derived_map}")
