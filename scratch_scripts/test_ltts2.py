import pandas as pd
df = pd.read_excel('data_agent/fundamentals/screener_data/LTTS.xlsx', sheet_name='Data Sheet')

def find_row_index(df, col, text):
    for idx, val in df[col].items():
        if pd.notna(val) and str(val).strip().upper().startswith(text.upper()):
            return idx
    return None

pl_idx = find_row_index(df, df.columns[0], 'PROFIT & LOSS')
q_idx = find_row_index(df, df.columns[0], 'QUARTERS')
bs_idx = find_row_index(df, df.columns[0], 'BALANCE SHEET')
derived_idx = find_row_index(df, df.columns[0], 'DERIVED')

from datetime import datetime
def process_section(df, start_idx, end_idx, date_idx):
    dates = []
    for col in df.columns[1:]:
        d = df.at[date_idx, col]
        if pd.notna(d):
            if isinstance(d, datetime):
                dates.append((col, d.date()))
            elif isinstance(d, str):
                try:
                    dates.append((col, datetime.strptime(d.strip()[:10], '%Y-%m-%d').date()))
                except:
                    pass
    row_map = {}
    for idx in range(start_idx, end_idx):
        key = str(df.at[idx, df.columns[0]]).strip().upper()
        if key and key != 'NAN':
            row_map[key] = idx
    return dates, row_map

ann_dates, pl_map = process_section(df, pl_idx + 2, q_idx if q_idx else bs_idx, pl_idx + 1)
derived_dates, derived_map = process_section(df, derived_idx + 1, len(df), pl_idx + 1)

print("ann_dates cols:", [col for col, d in ann_dates])
print("derived_dates cols:", [col for col, d in derived_dates])

for col, rdate in ann_dates:
    val = df.at[derived_map.get('ADJUSTED EQUITY SHARES IN CR', -1), col]
    print(f"col={col}, rdate={rdate}, val={val}")
