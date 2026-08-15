import re

with open("backend/main.py", "r") as f:
    content = f.read()

content = content.replace(
    "chain[\"rows\"] = csv_rows",
    "chain[\"rows\"] = csv_rows\n        print('CSV ROWS LENGTH:', len(csv_rows))\n        print('CHAIN KEYS:', chain.keys())"
)

with open("backend/main.py", "w") as f:
    f.write(content)
