import re

with open("src/App.tsx", "r") as f:
    content = f.read()

# Add a console log inside analytics useMemo to trace which chain is used
content = content.replace(
    "const chainRows = csvChainRows || parseChain(rawChain);",
    "const chainRows = csvChainRows || parseChain(rawChain);\n      console.log('Analytics Re-run. USING CSV?', !!csvChainRows, 'Rows:', chainRows.length);"
)

with open("src/App.tsx", "w") as f:
    f.write(content)
