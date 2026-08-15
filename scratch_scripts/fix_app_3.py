import re

with open("src/App.tsx", "r") as f:
    content = f.read()

# Replace the top banner UI variables
content = content.replace("₹{analytics.spot.toLocaleString('en-IN')}", "₹{(pipelineRes?.chain_meta?.spot || analytics.spot).toLocaleString('en-IN')}")
content = content.replace("₹{analytics.maxPain}", "₹{pipelineRes?.chain_meta?.max_pain || analytics.maxPain}")
content = content.replace("{analytics.pcr.toFixed(2)}", "{(pipelineRes?.chain_meta?.pcr || analytics.pcr).toFixed(2)}")
content = content.replace("{(analytics.atmMeta.iv * 100).toFixed(1)}%", "{((pipelineRes?.chain_meta?.atm_iv || analytics.atmMeta.iv) * 100).toFixed(1)}%")

# Fix any conditional colors for PCR that rely on analytics.pcr
content = content.replace("analytics.pcr >=", "(pipelineRes?.chain_meta?.pcr || analytics.pcr) >=")
content = content.replace("analytics.pcr <=", "(pipelineRes?.chain_meta?.pcr || analytics.pcr) <=")

with open("src/App.tsx", "w") as f:
    f.write(content)
