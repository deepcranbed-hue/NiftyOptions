content = open("entity_extract.py").read()

# I will add "sbi" back to SBIN in the python script.
content = content.replace('"SBIN": ["state bank", "state bank of india"],', '"SBIN": ["state bank", "state bank of india", "sbi"],')
content = content.replace('"BAJAJ-AUTO": ["bajaj auto"],', '"BAJAJ-AUTO": ["bajaj auto"],\n    "BAJAJFINSV": ["bajaj finserv"],\n    "BAJFINANCE": ["bajaj finance"],')

with open("entity_extract.py", "w") as f:
    f.write(content)
