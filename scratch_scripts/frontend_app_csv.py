import re

with open("src/App.tsx", "r") as f:
    content = f.read()

# 1. Add csvChainRows state
state_injection = """
  const [csvChainRows, setCsvChainRows] = useState<OptionRow[] | null>(null);
"""
content = content.replace("const [uploadDays, setUploadDays] = useState<number>(0);", "const [uploadDays, setUploadDays] = useState<number>(0);\n" + state_injection)

# 2. Update onUploadPipeline to set it
content = content.replace(
    "setPipelineRes(data);",
    "setPipelineRes(data);\n      if (data.chain_meta && data.chain_meta.rows) {\n        setCsvChainRows(data.chain_meta.rows);\n      }"
)

# 3. Update useMemo
content = content.replace(
    "const chainRows = parseChain(rawChain);",
    "const chainRows = csvChainRows || parseChain(rawChain);"
)

# 4. Optional: clear csvChainRows when 'Update Option Chain' (runQuantPipeline) is clicked to flip back to live data?
content = content.replace(
    "const strikes = analytics.chainRows.map",
    "setCsvChainRows(null);\n      const strikes = analytics.chainRows.map"
)

with open("src/App.tsx", "w") as f:
    f.write(content)
