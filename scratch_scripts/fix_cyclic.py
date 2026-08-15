import re

with open("src/components/StrategySuggesterPanel.tsx", "r") as f:
    content = f.read()

content = content.replace("onClick={onUploadPipeline}", "onClick={() => onUploadPipeline && onUploadPipeline()}")

with open("src/components/StrategySuggesterPanel.tsx", "w") as f:
    f.write(content)
