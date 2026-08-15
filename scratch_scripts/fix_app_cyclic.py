import re

with open("src/App.tsx", "r") as f:
    content = f.read()

content = content.replace(
    "const payload = {",
    "const cleanOptConfig = (optConfig && optConfig._reactName) ? {} : (optConfig || {});\n      const payload = {"
)
content = content.replace("...(optConfig || {})", "...cleanOptConfig")

with open("src/App.tsx", "w") as f:
    f.write(content)
