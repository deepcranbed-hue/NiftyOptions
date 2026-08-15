import re

with open("src/components/StrategySuggesterPanel.tsx", "r") as f:
    content = f.read()

props_destructure_injection = "onRunPipeline, uploadFile, setUploadFile, uploadSpot, setUploadSpot, uploadDays, setUploadDays, onUploadPipeline"

content = content.replace("setOptAllowBadRnd, onRunPipeline", "setOptAllowBadRnd, " + props_destructure_injection)

with open("src/components/StrategySuggesterPanel.tsx", "w") as f:
    f.write(content)
