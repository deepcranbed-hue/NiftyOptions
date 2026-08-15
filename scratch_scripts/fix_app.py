import re

with open("src/App.tsx", "r") as f:
    content = f.read()

props_injection = """
                uploadFile={uploadFile}
                setUploadFile={setUploadFile}
                uploadSpot={uploadSpot}
                setUploadSpot={setUploadSpot}
                uploadDays={uploadDays}
                setUploadDays={setUploadDays}
                onUploadPipeline={onUploadPipeline}
"""

if "uploadFile={uploadFile}" not in content:
    content = content.replace(
        "onRunPipeline={runQuantPipeline}",
        "onRunPipeline={runQuantPipeline}\n" + props_injection
    )

with open("src/App.tsx", "w") as f:
    f.write(content)
