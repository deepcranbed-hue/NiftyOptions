content = open("backend/quant/sector_tagging.py").read()
content = content.replace('dt = datetime.fromisoformat(ts) if isinstance(ts, str) else (ts or now)', 'ts_clean = ts.replace("Z", "+00:00") if isinstance(ts, str) else ts\n        dt = datetime.fromisoformat(ts_clean) if isinstance(ts_clean, str) else (ts_clean or now)')
with open("backend/quant/sector_tagging.py", "w") as f:
    f.write(content)
